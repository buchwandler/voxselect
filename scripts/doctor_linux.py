#!/usr/bin/env python3
"""Check Linux WebKitGTK and GStreamer prerequisites for VoxSelect playback."""

from __future__ import annotations
import argparse
import ctypes.util
import os
import shutil
import subprocess
from collections.abc import Sequence

REQUIRED_PLUGINS = ("wavparse", "audioconvert", "audioresample", "autoaudiosink")


def run(command: Sequence[str]) -> subprocess.CompletedProcess[str]:
    return subprocess.run(command, capture_output=True, text=True, check=False)


def check_command(label: str, command: Sequence[str]) -> bool:
    result = run(command)
    if result.returncode == 0:
        detail = result.stdout.strip().splitlines()
        print(f"PASS {label}: {detail[0] if detail else 'available'}")
        return True
    detail = (result.stderr or result.stdout).strip().splitlines()
    print(f"FAIL {label}: {detail[0] if detail else f'exit {result.returncode}'}")
    return False


def check_webkit_runtime() -> bool:
    pkg_config = shutil.which("pkg-config")
    if pkg_config is not None:
        result = run((pkg_config, "--modversion", "webkit2gtk-4.1"))
        if result.returncode == 0:
            print(f"PASS WebKitGTK 4.1: {result.stdout.strip()}")
            return True

    runtime = ctypes.util.find_library("webkit2gtk-4.1")
    if runtime:
        print(f"PASS WebKitGTK 4.1 runtime: {runtime}")
        return True

    print("FAIL WebKitGTK 4.1 runtime: not found by pkg-config or the dynamic loader")
    return False


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--tone-test",
        action="store_true",
        help="play a short GStreamer test tone through the default audio sink",
    )
    args = parser.parse_args()

    print(
        "Session: "
        f"XDG_SESSION_TYPE={os.environ.get('XDG_SESSION_TYPE', 'unknown')} "
        f"WAYLAND_DISPLAY={os.environ.get('WAYLAND_DISPLAY', 'unset')} "
        f"DISPLAY={os.environ.get('DISPLAY', 'unset')}"
    )

    failures = 0
    if not check_webkit_runtime():
        failures += 1

    gst_inspect = shutil.which("gst-inspect-1.0")
    if gst_inspect is None:
        print("FAIL GStreamer plugins: gst-inspect-1.0 not found")
        failures += 1
    else:
        for plugin in REQUIRED_PLUGINS:
            if not check_command(f"GStreamer {plugin}", (gst_inspect, plugin)):
                failures += 1

    if args.tone_test:
        gst_launch = shutil.which("gst-launch-1.0")
        if gst_launch is None:
            print("FAIL GStreamer tone test: gst-launch-1.0 not found")
            failures += 1
        elif not check_command(
            "GStreamer tone test",
            (
                gst_launch,
                "-q",
                "audiotestsrc",
                "wave=sine",
                "freq=440",
                "num-buffers=10",
                "!",
                "audioconvert",
                "!",
                "audioresample",
                "!",
                "autoaudiosink",
            ),
        ):
            failures += 1

    print("A passing check does not establish that a person heard audible output.")
    return 1 if failures else 0


if __name__ == "__main__":
    raise SystemExit(main())
