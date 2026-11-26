#!/usr/bin/env python3
from __future__ import annotations

import argparse
import base64
import io
import json
import subprocess
import time
import wave
from pathlib import Path
from typing import Any

if __package__:
    from .smoke_sidecar import (
        JsonlReader,
        PROTOCOL,
        SERVICE,
        default_binary,
        verify_manifest,
    )
else:
    from smoke_sidecar import (
        JsonlReader,
        PROTOCOL,
        SERVICE,
        default_binary,
        verify_manifest,
    )


def request(
    process: subprocess.Popen[str],
    reader: JsonlReader,
    notifications: list[dict[str, Any]],
    sequence: int,
    command: str,
    params: dict[str, Any],
    timeout: float,
) -> dict[str, Any]:
    request_id = f"real-engine-{sequence}"
    message = {
        "id": request_id,
        "protocol": PROTOCOL,
        "command": command,
        "params": params,
    }
    if process.stdin is None:
        raise RuntimeError("sidecar stdin is unavailable")
    process.stdin.write(json.dumps(message, separators=(",", ":")) + "\n")
    process.stdin.flush()
    deadline = time.monotonic() + timeout
    while True:
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            raise TimeoutError(f"sidecar command {command} timed out")
        record = reader.read(remaining)
        if record.get("id") == request_id:
            if record.get("protocol") != PROTOCOL or record.get("ok") is not True:
                error = record.get("error", {})
                raise RuntimeError(f"sidecar command {command} failed: {error}")
            result = record.get("result")
            if not isinstance(result, dict):
                raise RuntimeError(
                    f"sidecar command {command} returned a non-object result"
                )
            return result
        if isinstance(record.get("event"), str):
            notifications.append(record)
            continue
        raise RuntimeError(f"sidecar returned an unexpected record: {record}")


def wait_for_audio(
    reader: JsonlReader,
    notifications: list[dict[str, Any]],
    generation: int,
    timeout: float,
) -> dict[str, Any]:
    deadline = time.monotonic() + timeout
    while True:
        for index, event in enumerate(notifications):
            if event.get("generation") != generation:
                continue
            notifications.pop(index)
            if event.get("event") == "speech_error":
                raise RuntimeError(
                    f"real-engine synthesis failed: {event.get('message')}"
                )
            if event.get("event") == "audio":
                return event
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            raise TimeoutError("real-engine synthesis did not produce audio")
        event = reader.read(remaining)
        if event.get("generation") != generation:
            notifications.append(event)
            continue
        if event.get("event") == "speech_error":
            raise RuntimeError(f"real-engine synthesis failed: {event.get('message')}")
        if event.get("event") == "audio":
            return event
        notifications.append(event)


def validate_wav(data: bytes) -> dict[str, int]:
    with wave.open(io.BytesIO(data), "rb") as wav_file:
        channels = wav_file.getnchannels()
        sample_rate = wav_file.getframerate()
        frame_count = wav_file.getnframes()
        sample_width = wav_file.getsampwidth()
        if wav_file.getcomptype() != "NONE":
            raise RuntimeError("synthesized WAV is not uncompressed PCM")
        if channels <= 0 or sample_rate <= 0 or frame_count <= 0:
            raise RuntimeError("synthesized WAV has invalid audio metadata")
        if sample_width not in (1, 2, 3, 4):
            raise RuntimeError(f"unsupported PCM sample width: {sample_width}")
        frames = wav_file.readframes(frame_count)
    expected_size = frame_count * channels * sample_width
    if len(frames) != expected_size:
        raise RuntimeError("synthesized WAV frame data is truncated")
    if sample_width == 1:
        non_silent = any(sample != 128 for sample in frames)
    else:
        non_silent = any(frames)
    if not non_silent:
        raise RuntimeError("synthesized WAV is silent")
    return {
        "channels": channels,
        "frames": frame_count,
        "sample_rate": sample_rate,
        "sample_width": sample_width,
    }


def run_real_engine(
    binary: Path,
    *,
    engine_id: str,
    text: str,
    output: Path,
    timeout: float,
) -> dict[str, Any]:
    manifest = verify_manifest(binary, Path(f"{binary}.manifest.json"))
    if manifest.get("service", {}).get("name") != SERVICE:
        raise RuntimeError("sidecar version manifest names a different service")
    process = subprocess.Popen(
        [str(binary)],
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        text=True,
        encoding="utf-8",
        bufsize=1,
    )
    if process.stdout is None:
        raise RuntimeError("sidecar stdout is unavailable")
    reader = JsonlReader(process.stdout)
    notifications: list[dict[str, Any]] = []
    try:
        ready = reader.read(timeout)
        if (
            ready.get("event") != "ready"
            or ready.get("protocol") != PROTOCOL
            or ready.get("service") != SERVICE
            or ready.get("service_version")
            != manifest.get("service", {}).get("version")
        ):
            raise RuntimeError(f"incompatible sidecar ready record: {ready}")
        request(process, reader, notifications, 1, "capabilities", {}, timeout)
        catalog = request(process, reader, notifications, 2, "engines", {}, timeout)
        engines = catalog.get("engines")
        if not isinstance(engines, list):
            raise RuntimeError("engine catalog did not contain an engines list")
        engine = next((item for item in engines if item.get("id") == engine_id), None)
        if engine is None or engine.get("runnable") is not True:
            raise RuntimeError(f"configured real engine is not runnable: {engine_id}")

        targets = request(
            process,
            reader,
            notifications,
            3,
            "targets",
            {"engine": engine_id, "language": "en-US", "offline": False},
            timeout,
        )
        target_items = targets.get("result", {}).get("items", [])
        target = next(
            (item for item in target_items if item.get("id") == "nano-0.8-int8"),
            None,
        )
        if target is None:
            target = next(
                (
                    item
                    for item in target_items
                    if item.get("runtime_available") and not item.get("reference_only")
                ),
                None,
            )
        if target is None:
            raise RuntimeError(
                f"no runnable target is available for engine {engine_id}"
            )
        target_id = target.get("id")
        if not isinstance(target_id, str) or not target_id:
            raise RuntimeError("selected target has no identifier")

        voices = request(
            process,
            reader,
            notifications,
            4,
            "voices",
            {
                "engine": engine_id,
                "target": target_id,
                "language": "en-US",
                "offline": False,
            },
            timeout,
        )
        voice_items = voices.get("result", {}).get("items", [])
        if not isinstance(voice_items, list) or not voice_items:
            raise RuntimeError(f"no voice is available for target {target_id}")
        default_voice = target.get("default_voice")
        voice = next(
            (item for item in voice_items if item.get("id") == default_voice),
            voice_items[0],
        )
        voice_id = voice.get("id")
        if not isinstance(voice_id, str) or not voice_id:
            raise RuntimeError("selected voice has no identifier")

        request(
            process,
            reader,
            notifications,
            5,
            "configure",
            {
                "settings": {
                    "engine": engine_id,
                    "target": target_id,
                    "voice": voice_id,
                    "language": "en-US",
                    "offline": False,
                }
            },
            timeout,
        )
        accepted = request(
            process,
            reader,
            notifications,
            6,
            "speak",
            {"text": text},
            timeout,
        )
        generation = accepted.get("generation")
        if accepted.get("accepted") is not True or not isinstance(generation, int):
            raise RuntimeError(
                f"sidecar did not accept the synthesis request: {accepted}"
            )
        audio_event = wait_for_audio(reader, notifications, generation, timeout)
        encoded_audio = audio_event.get("wav_base64")
        if not isinstance(encoded_audio, str):
            raise RuntimeError("audio event did not contain WAV data")
        wav_bytes = base64.b64decode(encoded_audio, validate=True)
        audio_info = validate_wav(wav_bytes)
        output.parent.mkdir(parents=True, exist_ok=True)
        output.write_bytes(wav_bytes)
        shutdown = request(process, reader, notifications, 7, "shutdown", {}, timeout)
        if shutdown.get("shutdown") is not True:
            raise RuntimeError(f"invalid sidecar shutdown response: {shutdown}")
        if process.wait(timeout=timeout) != 0:
            raise RuntimeError(f"sidecar exited with status {process.returncode}")
        return {
            "engine": engine_id,
            "target": target_id,
            "voice": voice_id,
            "audio": audio_info,
            "output": str(output),
        }
    finally:
        if process.poll() is None:
            process.kill()
            process.wait()


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Synthesize and validate non-silent WAV audio with a real engine"
    )
    parser.add_argument("--binary", type=Path, default=default_binary())
    parser.add_argument("--engine", default="kitten")
    parser.add_argument("--text", default="VoxSelect real engine integration test.")
    parser.add_argument(
        "--output", type=Path, default=Path("build/real-engine-smoke.wav")
    )
    parser.add_argument("--timeout", type=float, default=900.0)
    args = parser.parse_args()
    result = run_real_engine(
        args.binary.resolve(),
        engine_id=args.engine,
        text=args.text,
        output=args.output.resolve(),
        timeout=args.timeout,
    )
    print(
        "PASS: generated valid non-silent WAV with "
        f"{result['engine']} ({result['target']}, {result['voice']}), "
        f"{result['audio']['sample_rate']} Hz, "
        f"{result['audio']['channels']} channel(s), {result['audio']['frames']} frames"
    )
    print(f"WAV: {result['output']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
