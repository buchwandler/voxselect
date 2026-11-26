#!/usr/bin/env python3
"""Persistent, versioned JSONL bridge from VoxSelect to Readio's public API."""

from __future__ import annotations

import base64
import dataclasses
import json
import os
import queue
import sys
import threading
import traceback
from collections.abc import Mapping
from pathlib import Path
from types import MappingProxyType
from typing import Any

import numpy as np

from readio.api import (
    CancellationToken,
    Readio,
    ReadioError,
    SpeakRequest,
    describe_error,
    serialize_public,
)

try:
    from pynput.keyboard import Controller, Key
except Exception:  # frozen builds can still report a clean runtime error
    Controller = None  # type: ignore[assignment]
    Key = None  # type: ignore[assignment]


PROTOCOL = 2
SERVICE_NAME = "voxselect-readio-service"
SERVICE_VERSION = "0.2.0"
_OUTPUT_LOCK = threading.Lock()


class Bridge:
    def __init__(self) -> None:
        self.settings: dict[str, Any] = {
            "engine": "kitten",
            "target": "KittenML/kitten-tts-mini-0.8",
            "voice": "Jasper",
            "language": "en-US",
            "speaker": None,
            "offline": False,
            "max_chars": 8000,
        }
        self.readio = Readio()
        self._generation = 0
        self._lock = threading.Lock()
        self._cancellation: CancellationToken | None = None
        self._pending_generations: set[int] = set()
        self._jobs: queue.Queue[tuple[int, str, Mapping[str, Any]]] = queue.Queue(
            maxsize=1
        )
        self._shutdown = threading.Event()
        self._worker = threading.Thread(target=self._speech_worker, daemon=True)
        self._worker.start()

    def generation(self) -> int:
        with self._lock:
            return self._generation

    def settings_snapshot(self) -> dict[str, Any]:
        with self._lock:
            return dict(self.settings)

    def diagnostics(self) -> dict[str, Any]:
        settings = self.settings_snapshot()
        return {
            "schema": "voxselect.diagnostics.v1",
            "result": {
                "service": {
                    "name": SERVICE_NAME,
                    "version": SERVICE_VERSION,
                    "protocol": PROTOCOL,
                },
                "platform": sys.platform,
                "readio": serialize_public(self.readio.capabilities()),
                "selection": {
                    key: _none(settings.get(key))
                    for key in ("engine", "target", "voice")
                },
            },
        }

    def _discard_queued_locked(self) -> None:
        while True:
            try:
                self._jobs.get_nowait()
                self._jobs.task_done()
            except queue.Empty:
                return

    def _cancel_pending_locked(self) -> tuple[int, ...]:
        generations = tuple(sorted(self._pending_generations))
        self._pending_generations.clear()
        self._discard_queued_locked()
        return generations

    @staticmethod
    def _emit_cancellations(generations: tuple[int, ...]) -> None:
        for generation in generations:
            emit({"event": "speech_canceled", "generation": generation})

    def _finish_generation(self, generation: int, payload: Mapping[str, Any]) -> bool:
        with self._lock:
            if generation not in self._pending_generations:
                return False
            self._pending_generations.remove(generation)
        emit(payload)
        return True

    def invalidate(self) -> int:
        with self._lock:
            self._generation += 1
            generation = self._generation
            cancellation = self._cancellation
            self._cancellation = None
            canceled_generations = self._cancel_pending_locked()
        if cancellation is not None:
            cancellation.cancel()
        self._emit_cancellations(canceled_generations)
        return generation

    def configure(self, settings: Mapping[str, Any]) -> dict[str, Any]:
        allowed = {
            "engine",
            "target",
            "voice",
            "language",
            "speaker",
            "offline",
            "max_chars",
        }
        with self._lock:
            for key in allowed:
                if key in settings:
                    self.settings[key] = settings[key]
            return dict(self.settings)

    def queue_speech(self, text: str) -> int:
        text = text.strip()
        if not text:
            raise ValueError("text is empty")
        with self._lock:
            settings = MappingProxyType(dict(self.settings))
            max_chars = int(settings.get("max_chars") or 8000)
            if len(text) > max_chars:
                raise ValueError(
                    f"text length {len(text)} exceeds configured limit {max_chars}"
                )
            self._generation += 1
            generation = self._generation
            cancellation = self._cancellation
            self._cancellation = None
            canceled_generations = self._cancel_pending_locked()
            if cancellation is not None:
                cancellation.cancel()
            self._emit_cancellations(canceled_generations)
            self._pending_generations.add(generation)
            self._jobs.put_nowait((generation, text, settings))
        return generation

    def stop(self) -> int:
        return self.invalidate()

    def shutdown(self) -> None:
        self.invalidate()
        self._shutdown.set()

    def _speech_worker(self) -> None:
        while not self._shutdown.is_set():
            try:
                job = self._jobs.get(timeout=0.2)
            except queue.Empty:
                continue
            cancellation: CancellationToken | None = None
            job_generation: int | None = None
            try:
                job_generation, text, settings = job
                cancellation = CancellationToken()
                with self._lock:
                    if job_generation != self._generation:
                        continue
                    self._cancellation = cancellation
                request = SpeakRequest(
                    text=text,
                    engine=_none(settings.get("engine")),
                    target=_none(settings.get("target")),
                    voice=_none(settings.get("voice")),
                    language=_none(settings.get("language")) or "en-US",
                    speaker=_speaker(settings.get("speaker")),
                    offline=bool(settings.get("offline", False)),
                )

                def on_progress(event: Any) -> None:
                    if job_generation != self.generation():
                        return
                    stage = _progress_stage(event)
                    payload: dict[str, Any] = {
                        "event": "progress",
                        "generation": job_generation,
                        "stage": stage,
                        "message": safe_progress_message(event),
                    }
                    for field in ("current", "total"):
                        value = getattr(event, field, None)
                        if isinstance(value, int) and not isinstance(value, bool):
                            payload[field] = value
                    emit(payload)

                result = self.readio.synthesize(
                    request,
                    on_progress=on_progress,
                    cancellation=cancellation,
                )
                metadata = _audio_metadata(result.audio)
                wav_bytes = result.audio.to_wav_bytes()
                wav = base64.b64encode(wav_bytes).decode("ascii")
                self._finish_generation(
                    job_generation,
                    {
                        "event": "audio",
                        "generation": job_generation,
                        "wav_base64": wav,
                        "wav_bytes": len(wav_bytes),
                        "frames": metadata["frames"],
                        "duration_seconds": metadata["duration_seconds"],
                        "peak": metadata["peak"],
                        "profile_id": result.profile_id,
                        "warnings": list(result.warnings),
                        "elapsed_seconds": result.elapsed_seconds,
                        "sample_rate": result.audio.sample_rate,
                        "channels": result.audio.channels,
                    },
                )
            except Exception as exc:
                if job_generation is not None:
                    self._finish_generation(
                        job_generation,
                        {
                            "event": "speech_error",
                            "generation": job_generation,
                            "message": str(exc),
                            "type": type(exc).__name__,
                            "error": _error_info(exc),
                        },
                    )
            finally:
                if cancellation is not None:
                    with self._lock:
                        if self._cancellation is cancellation:
                            self._cancellation = None
                self._jobs.task_done()


def _none(value: Any) -> Any | None:
    if value is None:
        return None
    if isinstance(value, str) and not value.strip():
        return None
    return value


def _speaker(value: Any) -> str | int | None:
    value = _none(value)
    if value is None:
        return None
    if isinstance(value, int):
        return value
    text = str(value)
    try:
        return int(text)
    except ValueError:
        return text


def _identifier(params: Mapping[str, Any], name: str) -> str:
    value = params.get(name)
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{name} is required")
    return value.strip()


def _optional_string(params: Mapping[str, Any], name: str) -> str | None:
    value = params.get(name)
    if value is None:
        return None
    if not isinstance(value, str):
        raise ValueError(f"{name} must be a string or null")
    return value.strip() or None


def _boolean(params: Mapping[str, Any], name: str, *, default: bool) -> bool:
    value = params.get(name, default)
    if not isinstance(value, bool):
        raise ValueError(f"{name} must be a boolean")
    return value


def jsonable(value: Any) -> Any:
    if value is None or isinstance(value, (str, int, float, bool)):
        return value
    if isinstance(value, Path):
        return str(value)
    if dataclasses.is_dataclass(value):
        return jsonable(dataclasses.asdict(value))
    if isinstance(value, Mapping):
        return {str(key): jsonable(item) for key, item in value.items()}
    if isinstance(value, (list, tuple, set)):
        return [jsonable(item) for item in value]
    to_dict = getattr(value, "to_dict", None)
    if callable(to_dict):
        try:
            return jsonable(to_dict())
        except Exception:
            pass
    return str(value)


def _error_info(error: Exception) -> dict[str, Any]:
    if isinstance(error, ReadioError):
        value = jsonable(describe_error(error))
        result = dict(value) if isinstance(value, Mapping) else {}
    else:
        result = {}
    result.setdefault("type", type(error).__name__)
    result.setdefault("message", str(error))
    result.setdefault("code", None)
    result.setdefault("stage", None)
    result.setdefault("package", None)
    result.setdefault("selector", None)
    result.setdefault("details", {})
    result.setdefault("recoverable", False)
    result.setdefault("suggested_action", None)
    return {"schema": "readio.error-info.v1", "result": result}


_PROGRESS_MESSAGES = {
    "convert": "Preparing speech assets",
    "compile": "Preparing speech",
    "render_plan": "Planning synthesis",
    "render": "Synthesizing speech",
    "compose": "Composing audio",
    "export": "Preparing audio",
}


def _progress_stage(event: Any) -> str:
    stage = getattr(event, "stage", None)
    if not isinstance(stage, str):
        stage = getattr(stage, "value", None)
    return stage if stage in _PROGRESS_MESSAGES else "synthesis"


def safe_progress_message(event: Any) -> str:
    return _PROGRESS_MESSAGES.get(_progress_stage(event), "Preparing speech")


def _audio_metadata(audio: Any) -> dict[str, int | float]:
    samples = np.asarray(audio.audio, dtype=np.float32)
    frames = int(audio.frames)
    if frames <= 0:
        raise RuntimeError("synthesis returned an empty audio buffer")
    if not np.isfinite(samples).all():
        raise RuntimeError("synthesis returned non-finite audio samples")

    sample_rate = int(audio.sample_rate)
    if sample_rate <= 0:
        raise RuntimeError("synthesis returned an invalid sample rate")
    peak = float(np.max(np.abs(samples))) if samples.size else 0.0
    if peak < 1e-6:
        raise RuntimeError("synthesis returned silent audio")
    return {
        "frames": frames,
        "duration_seconds": frames / sample_rate,
        "peak": peak,
    }


def emit(payload: Mapping[str, Any]) -> None:
    message = {**payload, "protocol": PROTOCOL}
    with _OUTPUT_LOCK:
        sys.stdout.write(json.dumps(jsonable(message), ensure_ascii=False) + "\n")
        sys.stdout.flush()


def simulate_copy() -> None:
    if Controller is None or Key is None:
        raise RuntimeError("pynput keyboard support is unavailable")
    keyboard = Controller()
    modifier = Key.cmd if sys.platform == "darwin" else Key.ctrl
    with keyboard.pressed(modifier):
        keyboard.press("c")
        keyboard.release("c")


def handle(bridge: Bridge, message: Mapping[str, Any]) -> tuple[bool, Any]:
    if message.get("protocol") != PROTOCOL:
        raise ValueError(
            f"unsupported request protocol {message.get('protocol')!r}; expected {PROTOCOL}"
        )
    command = str(message.get("command") or "")
    params = message.get("params") or {}
    if not isinstance(params, Mapping):
        raise ValueError("request params must be an object")

    if command == "ping":
        return True, {"service": SERVICE_NAME, "protocol": PROTOCOL}
    if command == "configure":
        settings = params.get("settings")
        if not isinstance(settings, Mapping):
            raise ValueError("configure requires a settings object")
        return True, bridge.configure(settings)
    if command == "copy_selection":
        simulate_copy()
        return True, {"copied": True}
    if command == "speak":
        generation = bridge.queue_speech(str(params.get("text") or ""))
        return True, {"accepted": True, "generation": generation}
    if command == "stop":
        return True, {"generation": bridge.stop()}
    if command == "capabilities":
        return True, serialize_public(bridge.readio.capabilities())
    if command == "synthesis_capabilities":
        return True, serialize_public(bridge.readio.synthesis_capabilities())
    if command == "diagnostics":
        return True, bridge.diagnostics()
    if command == "engines":
        settings = bridge.settings_snapshot()
        engines = bridge.readio.list_engines()
        selected = _none(settings.get("engine"))
        targets: Any = []
        if selected:
            try:
                targets = bridge.readio.list_targets(
                    str(selected),
                    language=_none(settings.get("language")),
                    offline=True,
                )
            except Exception as exc:
                targets = {"error": str(exc)}
                if isinstance(exc, ReadioError):
                    targets["error_info"] = serialize_public(describe_error(exc))
        return True, {
            "schema": "voxselect.engine-catalog.v1",
            "engines": serialize_public(engines),
            "selected_engine": selected,
            "targets_for_selected_engine": serialize_public(targets),
        }
    if command == "targets":
        engine = _identifier(params, "engine")
        targets = bridge.readio.list_targets(
            engine,
            language=_optional_string(params, "language"),
            offline=_boolean(params, "offline", default=True),
            refresh=_boolean(params, "refresh", default=False),
        )
        return True, {
            "schema": "voxselect.target-catalog.v1",
            "result": {"engine": engine, "items": serialize_public(targets)},
        }
    if command == "voices":
        engine = _identifier(params, "engine")
        target = _identifier(params, "target")
        voices = bridge.readio.list_voices(
            engine=engine,
            target=target,
            language=_optional_string(params, "language"),
            offline=_boolean(params, "offline", default=True),
            refresh=_boolean(params, "refresh", default=False),
        )
        return True, {
            "schema": "voxselect.voice-catalog.v1",
            "result": {
                "engine": engine,
                "target": target,
                "items": serialize_public(voices),
            },
        }
    if command == "shutdown":
        bridge.shutdown()
        return True, {"shutdown": True}
    raise ValueError(f"unknown command: {command}")


def main() -> int:
    bridge = Bridge()
    emit(
        {
            "event": "ready",
            "service": SERVICE_NAME,
            "service_version": SERVICE_VERSION,
        }
    )
    for raw in sys.stdin:
        raw = raw.strip()
        if not raw:
            continue
        request_id: Any = None
        try:
            message = json.loads(raw)
            if not isinstance(message, Mapping):
                raise ValueError("request must be a JSON object")
            request_id = message.get("id")
            ok, result = handle(bridge, message)
            emit({"id": request_id, "ok": ok, "result": result})
            if message.get("command") == "shutdown":
                return 0
        except Exception as exc:
            emit(
                {
                    "id": request_id,
                    "ok": False,
                    "error": _error_info(exc),
                }
            )
            if os.environ.get("VOXSELECT_DEBUG"):
                print(
                    f"sidecar request failed: {type(exc).__name__}",
                    file=sys.stderr,
                )
                traceback.print_tb(exc.__traceback__, file=sys.stderr, limit=20)
    bridge.shutdown()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
