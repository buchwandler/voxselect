#!/usr/bin/env python3
from __future__ import annotations

import argparse
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Build sidecar and launch VoxSelect in Tauri dev mode"
    )
    parser.add_argument("--engine-extra", default="kitten")
    args = parser.parse_args()
    subprocess.run(
        [
            sys.executable,
            str(ROOT / "scripts" / "build_sidecar.py"),
            "--engine-extra",
            args.engine_extra,
        ],
        cwd=ROOT,
        check=True,
    )
    subprocess.run(["npm", "run", "tauri:dev"], cwd=ROOT, check=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
