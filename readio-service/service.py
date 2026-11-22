#!/usr/bin/env python3
"""Persistent JSONL bridge from VoxSelect to Readio's public Python API."""

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
from typing import Any

from readio.api import CancellationToken, Readio, ReadioError, SpeakRequest, describe_error

try:
    from pynput.keyboard import Controller, Key
except Exception:  # frozen builds can still report a clean runtime error
    Controller = None  # type: ignore[assignment]
    Key = None  # type: ignore[assignment]


class Bridge:
    def __init__(self) -> None:
        self.settings: dict[str, Any] = {
            "engine": "kitten",
            "target": None,
            "voice": None,
            "language": "en-US",
            "speaker": None,
            "offline": True,
            "max_chars": 8000,
        }
        self.readio = Readio()
        self._generation = 0
        self._lock = threading.Lock()
        self._cancellation: CancellationToken | None = None
        self._jobs: queue.Queue[tuple[int, str]] = queue.Queue(maxsize=1)
        self._shutdown = threading.Event()
        self._worker = threading.Thread(target=self._speech_worker, daemon=True)
        self._worker.start()

    def generation(self) -> int:
        with self._lock:
            return self._generation

    def invalidate(self) -> int:
        with self._lock:
            self._generation += 1
            generation = self._generation
            cancellation = self._cancellation
            self._cancellation = None
        if cancellation is not None:
            cancellation.cancel()
        while True:
            try:
                self._jobs.get_nowait()
                self._jobs.task_done()
            except queue.Empty:
                break
        return generation
    def configure(self, settings: Mapping[str, Any]) -> dict[str, Any]:
        allowed = {"engine", "target", "voice", "language", "speaker", "offline", "max_chars"}
        for key in allowed:
            if key in settings:
                self.settings[key] = settings[key]
        return dict(self.settings)

    def queue_speech(self, text: str) -> int:
        text = text.strip()
        if not text:
            raise ValueError("text is empty")
        max_chars = int(self.settings.get("max_chars") or 8000)
        if len(text) > max_chars:
            raise ValueError(f"text length {len(text)} exceeds configured limit {max_chars}")
        generation = self.invalidate()
        self._jobs.put_nowait((generation, text))
        return generation

    def stop(self) -> int:
        return self.invalidate()

    def shutdown(self) -> None:
        self.invalidate()
        self._shutdown.set()

    def _speech_worker(self) -> None:
        while not self._shutdown.is_set():
            try:
                generation, text = self._jobs.get(timeout=0.2)
            except queue.Empty:
                continue
            cancellation: CancellationToken | None = None
            try:
                if generation != self.generation():
                    continue
                request = SpeakRequest(
                    text=text,
                    engine=_none(self.settings.get("engine")),
                    target=_none(self.settings.get("target")),
                    voice=_none(self.settings.get("voice")),
                    language=_none(self.settings.get("language")) or "en-US",
                    speaker=_speaker(self.settings.get("speaker")),
                    offline=bool(self.settings.get("offline", True)),
                )
                cancellation = CancellationToken()
                with self._lock:
                    if generation != self._generation:
                        continue
                    self._cancellation = cancellation
                result = self.readio.synthesize(request, cancellation=cancellation)
                if generation != self.generation():
                    emit({"event": "speech_canceled", "generation": generation})
                    continue
                wav = base64.b64encode(result.audio.to_wav_bytes()).decode("ascii")
                emit(
                    {
                        "event": "audio",
                        "generation": generation,
                        "wav_base64": wav,
                        "profile_id": result.profile_id,
                        "warnings": list(result.warnings),
                        "elapsed_seconds": result.elapsed_seconds,
                        "sample_rate": result.audio.sample_rate,
                        "channels": result.audio.channels,
                    }
                )
            except Exception as exc:
                if generation == self.generation():
                    payload = {
                        "event": "speech_error",
                        "generation": generation,
                        "message": str(exc),
                        "type": type(exc).__name__,
                    }
                    if isinstance(exc, ReadioError):
                        payload["error"] = jsonable(describe_error(exc))
                    emit(payload)
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


def jsonable(value: Any) -> Any:
    if value is None or isinstance(value, (str, int, float, bool)):
        return value
    if isinstance(value, Path):
        return str(value)
    if dataclasses.is_dataclass(value):
        return jsonable(dataclasses.asdict(value))
    if isinstance(value, Mapping):
        return {str(k): jsonable(v) for k, v in value.items()}
    if isinstance(value, (list, tuple, set)):
        return [jsonable(v) for v in value]
    to_dict = getattr(value, "to_dict", None)
    if callable(to_dict):
        try:
            return jsonable(to_dict())
        except Exception:
            pass
    return str(value)


def emit(payload: Mapping[str, Any]) -> None:
    sys.stdout.write(json.dumps(jsonable(payload), ensure_ascii=False) + "\n")
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
    command = str(message.get("command") or "")
    if command == "ping":
        return True, {"service": "voxselect-readio-service", "protocol": 1}
    if command == "configure":
        settings = message.get("settings")
        if not isinstance(settings, Mapping):
            raise ValueError("configure requires a settings object")
        return True, bridge.configure(settings)
    if command == "copy_selection":
        simulate_copy()
        return True, {"copied": True}
    if command == "speak":
        generation = bridge.queue_speech(str(message.get("text") or ""))
        return True, {"accepted": True, "generation": generation}
    if command == "stop":
        return True, {"generation": bridge.stop()}
    if command == "engines":
        engines = bridge.readio.list_engines()
        selected = _none(bridge.settings.get("engine"))
        targets: Any = []
        if selected:
            try:
                targets = bridge.readio.list_targets(
                    str(selected),
                    language=_none(bridge.settings.get("language")),
                    offline=bool(bridge.settings.get("offline", True)),
                )
            except Exception as exc:
                targets = {"error": str(exc)}
                if isinstance(exc, ReadioError):
                    targets["error_info"] = jsonable(describe_error(exc))
        return True, {"engines": engines, "targets_for_selected_engine": targets}
        bridge.shutdown()
        return True, {"shutdown": True}
    raise ValueError(f"unknown command: {command}")


def main() -> int:
    bridge = Bridge()
    emit({"event": "ready", "protocol": 1})
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
            error_payload = {"id": request_id, "ok": False, "error": str(exc), "type": type(exc).__name__}
            if isinstance(exc, ReadioError):
                error_payload["error_info"] = jsonable(describe_error(exc))
            emit(error_payload)
            if os.environ.get("VOXSELECT_DEBUG"):
                traceback.print_exc(file=sys.stderr)
    bridge.shutdown()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
