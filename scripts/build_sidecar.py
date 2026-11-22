#!/usr/bin/env python3
from __future__ import annotations

import argparse
import os
import shutil
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
VENV = ROOT / ".venv-sidecar"
VENDOR = ROOT / "vendor" / "readio"
SERVICE = ROOT / "readio-service" / "service.py"
BINARY_DIR = ROOT / "src-tauri" / "binaries"


def run(*args: str, cwd: Path | None = None) -> None:
    print("+", " ".join(args))
    subprocess.run(args, cwd=cwd or ROOT, check=True)


def venv_python() -> Path:
    if os.name == "nt":
        return VENV / "Scripts" / "python.exe"
    return VENV / "bin" / "python"


def rust_target() -> str:
    result = subprocess.run(["rustc", "-vV"], check=True, text=True, capture_output=True)
    for line in result.stdout.splitlines():
        if line.startswith("host: "):
            return line.split(":", 1)[1].strip()
    raise RuntimeError("could not determine Rust host target from rustc -vV")


def main() -> int:
    parser = argparse.ArgumentParser(description="Build VoxSelect's frozen Readio sidecar")
    parser.add_argument(
        "--engine-extra",
        default="kitten",
        help="Readio optional extra to install (default: kitten). Use 'none' for no engine extra.",
    )
    parser.add_argument("--recreate-venv", action="store_true")
    args = parser.parse_args()

    if args.recreate_venv and VENV.exists():
        shutil.rmtree(VENV)
    if not VENV.exists():
        run(sys.executable, "-m", "venv", str(VENV))

    py = str(venv_python())
    run(py, "-m", "pip", "install", "--upgrade", "pip", "wheel", "setuptools")
    readio_requirement = str(VENDOR)
    if args.engine_extra.lower() != "none":
        readio_requirement = f"{VENDOR}[{args.engine_extra}]"
    run(py, "-m", "pip", "install", readio_requirement, "pynput>=1.7,<2", "pyinstaller>=6,<7")

    work = ROOT / "build" / "sidecar"
    dist = ROOT / "build" / "sidecar-dist"
    spec = ROOT / "build" / "sidecar-spec"
    for path in (work, dist, spec):
        path.mkdir(parents=True, exist_ok=True)

    cmd = [
        py,
        "-m",
        "PyInstaller",
        "--noconfirm",
        "--clean",
        "--onefile",
        "--name",
        "voxselect-readio-service",
        "--distpath",
        str(dist),
        "--workpath",
        str(work),
        "--specpath",
        str(spec),
        "--collect-all",
        "readio",
        "--collect-all",
        "voicerender",
        "--collect-all",
        "utterplan",
        "--collect-all",
        "audiocompose",
        "--collect-all",
        "audioexport",
        "--collect-all",
        "ssmdconvert",
        str(SERVICE),
    ]
    run(*cmd)

    source = dist / ("voxselect-readio-service.exe" if os.name == "nt" else "voxselect-readio-service")
    target_triple = rust_target()
    BINARY_DIR.mkdir(parents=True, exist_ok=True)
    destination = BINARY_DIR / f"voxselect-readio-service-{target_triple}{'.exe' if os.name == 'nt' else ''}"
    shutil.copy2(source, destination)
    if os.name != "nt":
        destination.chmod(0o755)
    print(f"Sidecar ready: {destination}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
