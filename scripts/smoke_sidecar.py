#!/usr/bin/env python3
from __future__ import annotations

import argparse
import hashlib
import json
import os
import queue
import subprocess
import sys
import threading
from pathlib import Path
from typing import Any, TextIO

ROOT = Path(__file__).resolve().parents[1]
BINARY_DIR = ROOT / "src-tauri" / "binaries"
PROTOCOL = 2
SERVICE = "voxselect-readio-service"


class JsonlReader:
    def __init__(self, stream: TextIO) -> None:
        self.lines: queue.Queue[str | None] = queue.Queue()
        self.thread = threading.Thread(target=self._read, args=(stream,), daemon=True)
        self.thread.start()

    def _read(self, stream: TextIO) -> None:
        for line in stream:
            self.lines.put(line)
        self.lines.put(None)

    def read(self, timeout: float) -> dict[str, Any]:
        try:
            line = self.lines.get(timeout=timeout)
        except queue.Empty as error:
            raise TimeoutError("sidecar did not produce a JSONL response") from error
        if line is None:
            raise RuntimeError("sidecar closed stdout before completing the smoke test")
        value = json.loads(line)
        if not isinstance(value, dict):
            raise RuntimeError("sidecar JSONL records must be objects")
        return value


def rust_target() -> str:
    result = subprocess.run(
        ["rustc", "-vV"], check=True, text=True, capture_output=True
    )
    for line in result.stdout.splitlines():
        if line.startswith("host: "):
            return line.split(":", 1)[1].strip()
    raise RuntimeError("could not determine Rust host target from rustc -vV")


def default_binary() -> Path:
    target = rust_target()
    suffix = ".exe" if os.name == "nt" else ""
    return BINARY_DIR / f"{SERVICE}-{target}{suffix}"


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def verify_manifest(binary: Path, manifest_path: Path) -> dict[str, Any]:
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    if manifest.get("schema") != "voxselect.sidecar-build.v1":
        raise RuntimeError("sidecar version manifest schema is unsupported")
    if manifest.get("binary", {}).get("file") != binary.name:
        raise RuntimeError("sidecar version manifest names a different binary")
    if manifest.get("binary", {}).get("sha256") != sha256_file(binary):
        raise RuntimeError("sidecar binary does not match its version manifest")
    constraints = manifest.get("constraints", {})
    constraints_path = manifest_path.parent / constraints.get("file", "")
    if not constraints_path.is_file():
        raise RuntimeError("sidecar dependency constraints are missing")
    if constraints.get("sha256") != sha256_file(constraints_path):
        raise RuntimeError("sidecar dependency constraints do not match the manifest")
    return manifest


def request(
    process: subprocess.Popen[str],
    reader: JsonlReader,
    sequence: int,
    command: str,
    params: dict[str, Any],
    timeout: float,
) -> dict[str, Any]:
    request_id = f"smoke-{sequence}"
    message = {
        "id": request_id,
        "protocol": PROTOCOL,
        "command": command,
        "params": params,
    }
    assert process.stdin is not None
    process.stdin.write(json.dumps(message, separators=(",", ":")) + "\n")
    process.stdin.flush()
    response = reader.read(timeout)
    if response.get("protocol") != PROTOCOL or response.get("id") != request_id:
        raise RuntimeError(f"sidecar response did not match request {request_id}")
    if response.get("ok") is not True:
        error = response.get("error", {})
        detail = error.get("result", {}).get("message") or error.get("message") or error
        raise RuntimeError(f"sidecar {command} failed: {detail}")
    result = response.get("result")
    if not isinstance(result, dict):
        raise RuntimeError(f"sidecar {command} returned a non-object result")
    return result


def smoke(binary: Path, manifest_path: Path, timeout: float) -> None:
    manifest = verify_manifest(binary, manifest_path)
    if manifest.get("service", {}).get("name") != SERVICE:
        raise RuntimeError("sidecar version manifest names a different service")
    if manifest.get("service", {}).get("protocol") != PROTOCOL:
        raise RuntimeError("sidecar version manifest uses an unsupported protocol")
    process = subprocess.Popen(
        [str(binary)],
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        text=True,
        encoding="utf-8",
        bufsize=1,
    )
    assert process.stdout is not None
    reader = JsonlReader(process.stdout)
    try:
        ready = reader.read(timeout)
        if (
            ready.get("event") != "ready"
            or ready.get("protocol") != PROTOCOL
            or ready.get("service") != SERVICE
        ):
            raise RuntimeError(f"incompatible sidecar ready record: {ready}")
        if ready.get("service_version") != manifest["service"]["version"]:
            raise RuntimeError("sidecar ready version differs from its manifest")

        ping = request(process, reader, 1, "ping", {}, timeout)
        if ping.get("service") != SERVICE or ping.get("protocol") != PROTOCOL:
            raise RuntimeError(f"invalid sidecar ping response: {ping}")

        capabilities = request(process, reader, 2, "capabilities", {}, timeout)
        if not isinstance(capabilities, dict):
            raise RuntimeError("capability response is invalid")
        catalog = request(process, reader, 3, "engines", {}, timeout)
        engines = catalog.get("engines")
        if not isinstance(engines, list):
            raise RuntimeError("engine catalog did not contain an engines list")

        runnable = [engine for engine in engines if engine.get("runnable")]
        if not runnable:
            print("SKIP: no runnable engine is installed; target and voice queries were skipped")
        else:
            engine = runnable[0]
            engine_id = engine.get("id")
            if not isinstance(engine_id, str) or not engine_id:
                raise RuntimeError("runnable engine has no identifier")
            targets = request(
                process,
                reader,
                4,
                "targets",
                {"engine": engine_id, "language": "en-US", "offline": True},
                timeout,
            )
            target_items = targets.get("result", {}).get("items", [])
            if not target_items:
                print(f"SKIP: runnable engine {engine_id} has no offline target to query")
            else:
                target_id = target_items[0].get("id")
                if not isinstance(target_id, str) or not target_id:
                    raise RuntimeError("target catalog item has no identifier")
                voices = request(
                    process,
                    reader,
                    5,
                    "voices",
                    {
                        "engine": engine_id,
                        "target": target_id,
                        "language": "en-US",
                        "offline": True,
                    },
                    timeout,
                )
                if not isinstance(voices.get("result", {}).get("items"), list):
                    raise RuntimeError("voice catalog did not contain an items list")

        shutdown_sequence = 6 if runnable else 4
        shutdown = request(
            process, reader, shutdown_sequence, "shutdown", {}, timeout
        )
        if shutdown.get("shutdown") is not True:
            raise RuntimeError(f"invalid sidecar shutdown response: {shutdown}")
        if process.wait(timeout=timeout) != 0:
            raise RuntimeError(f"sidecar exited with status {process.returncode}")
        print(
            f"PASS: {binary.name} handshake, ping, capabilities, engine catalog, and shutdown"
        )
    finally:
        if process.poll() is None:
            process.kill()
            process.wait()


def main() -> int:
    parser = argparse.ArgumentParser(description="Smoke-test the target-triple frozen sidecar")
    parser.add_argument("--binary", type=Path)
    parser.add_argument("--manifest", type=Path)
    parser.add_argument("--timeout", type=float, default=60.0)
    parser.add_argument(
        "--skip-missing",
        action="store_true",
        help="Report a clear skip instead of failing when rustc or the target binary is unavailable.",
    )
    args = parser.parse_args()
    try:
        binary = args.binary.resolve() if args.binary else default_binary()
    except (FileNotFoundError, subprocess.CalledProcessError, RuntimeError) as error:
        if args.skip_missing:
            print(f"SKIP: cannot determine the target sidecar path: {error}")
            return 0
        raise
    if not binary.is_file():
        if args.skip_missing:
            print(f"SKIP: target frozen sidecar is unavailable: {binary}")
            return 0
        raise FileNotFoundError(f"target frozen sidecar is unavailable: {binary}")
    manifest_path = args.manifest or Path(f"{binary}.manifest.json")
    smoke(binary, manifest_path, args.timeout)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
