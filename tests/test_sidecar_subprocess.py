from __future__ import annotations

import json
import os
import queue
import subprocess
import sys
import threading
from pathlib import Path
from typing import Any, Callable


ROOT = Path(__file__).resolve().parents[1]
SERVICE = ROOT / "readio-service" / "service.py"
FAKE_READIO = '''
from dataclasses import dataclass
from types import SimpleNamespace


class CancellationToken:
    def __init__(self):
        self.cancelled = False

    def cancel(self):
        self.cancelled = True

    def raise_if_cancelled(self):
        if self.cancelled:
            raise RuntimeError("cancelled")


class ReadioError(Exception):
    pass


@dataclass
class SpeakRequest:
    text: str
    engine: str | None = None
    target: str | None = None
    voice: str | None = None
    language: str | None = None
    speaker: str | int | None = None
    offline: bool = False


class Readio:
    def synthesize(self, request, *, cancellation):
        cancellation.raise_if_cancelled()
        audio = SimpleNamespace(
            to_wav_bytes=lambda: b"RIFFfake-WAVE",
            sample_rate=24000,
            channels=1,
        )
        return SimpleNamespace(
            audio=audio,
            profile_id="fake-profile",
            warnings=(),
            elapsed_seconds=0.01,
        )


def describe_error(error):
    return {"type": type(error).__name__, "message": str(error)}



def serialize_public(value):
    return value
'''


def _write_fake_readio(directory: Path) -> None:
    package = directory / "readio"
    package.mkdir()
    (package / "__init__.py").write_text("", encoding="utf-8")
    (package / "api.py").write_text(FAKE_READIO, encoding="utf-8")


def test_jsonl_sidecar_subprocess_with_fake_readio(tmp_path: Path) -> None:
    _write_fake_readio(tmp_path)
    env = os.environ.copy()
    existing_pythonpath = env.get("PYTHONPATH")
    env["PYTHONPATH"] = os.pathsep.join(
        value for value in (str(tmp_path), existing_pythonpath) if value
    )
    process = subprocess.Popen(
        [sys.executable, "-u", str(SERVICE)],
        cwd=ROOT,
        env=env,
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        encoding="utf-8",
        bufsize=1,
    )
    assert process.stdin is not None
    assert process.stdout is not None

    output: queue.Queue[str | None] = queue.Queue()
    unmatched: list[dict[str, Any]] = []

    def read_stdout() -> None:
        assert process.stdout is not None
        for line in process.stdout:
            output.put(line)
        output.put(None)

    threading.Thread(target=read_stdout, daemon=True).start()

    def receive(predicate: Callable[[dict[str, Any]], bool]) -> dict[str, Any]:
        for index, message in enumerate(unmatched):
            if predicate(message):
                return unmatched.pop(index)
        while True:
            try:
                line = output.get(timeout=10)
            except queue.Empty as error:
                raise AssertionError("timed out waiting for sidecar output") from error
            if line is None:
                stderr = process.stderr.read() if process.stderr is not None else ""
                raise AssertionError(f"sidecar exited before expected output: {stderr}")
            message = json.loads(line)
            if predicate(message):
                return message
            unmatched.append(message)

    def send(message: dict[str, Any]) -> None:
        process.stdin.write(json.dumps(message) + "\n")
        process.stdin.flush()

    try:
        ready = receive(lambda message: message.get("event") == "ready")
        assert ready == {
            "event": "ready",
            "protocol": 2,
            "service": "voxselect-readio-service",
            "service_version": "0.2.0",
        }

        send({"id": "mismatch-1", "protocol": 1, "command": "ping", "params": {}})
        mismatch = receive(lambda message: message.get("id") == "mismatch-1")
        assert mismatch["protocol"] == 2
        assert mismatch["ok"] is False
        assert mismatch["error"]["schema"] == "readio.error-info.v1"

        send({"id": "ping-1", "protocol": 2, "command": "ping", "params": {}})
        ping = receive(lambda message: message.get("id") == "ping-1")
        assert ping["ok"] is True
        assert ping["protocol"] == 2
        assert ping["result"]["protocol"] == 2

        send({"id": "configure-1", "protocol": 2, "command": "configure", "params": {"settings": {"engine": "fake"}}})
        configured = receive(lambda message: message.get("id") == "configure-1")
        assert configured["ok"] is True
        assert configured["result"]["engine"] == "fake"

        send({"id": "speak-1", "protocol": 2, "command": "speak", "params": {"text": "hello"}})
        speech = receive(lambda message: message.get("id") == "speak-1")
        assert speech["ok"] is True
        generation = speech["result"]["generation"]
        audio = receive(
            lambda message: message.get("event") == "audio"
            and message.get("generation") == generation
        )
        assert audio["wav_base64"]
        assert audio["sample_rate"] == 24000
        assert audio["channels"] == 1

        send({"id": "shutdown-1", "protocol": 2, "command": "shutdown", "params": {}})
        shutdown = receive(lambda message: message.get("id") == "shutdown-1")
        assert shutdown["ok"] is True
        assert shutdown["result"] == {"shutdown": True}
        assert process.wait(timeout=10) == 0
    finally:
        if process.poll() is None:
            process.terminate()
            process.wait(timeout=5)
        if process.stderr is not None:
            process.stderr.close()
        if process.stdin is not None:
            process.stdin.close()
        if process.stdout is not None:
            process.stdout.close()
