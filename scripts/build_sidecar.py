#!/usr/bin/env python3
from __future__ import annotations

import argparse
import ast
import hashlib
import json
import os
import re
import shutil
import subprocess
import sys
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
VENV = ROOT / ".venv-sidecar"
VENDOR = ROOT / "vendor" / "readio"
SERVICE = ROOT / "readio-service" / "service.py"
BINARY_DIR = ROOT / "src-tauri" / "binaries"


def run(*args: str, cwd: Path | None = None) -> None:
    print("+", " ".join(args), flush=True)
    subprocess.run(args, cwd=cwd or ROOT, check=True)


def capture(*args: str, cwd: Path | None = None) -> str:
    print("+", " ".join(args), flush=True)
    result = subprocess.run(
        args, cwd=cwd or ROOT, check=True, text=True, capture_output=True
    )
    return result.stdout


def venv_python() -> Path:
    if os.name == "nt":
        return VENV / "Scripts" / "python.exe"
    return VENV / "bin" / "python"


def rust_target() -> str:
    result = subprocess.run(
        ["rustc", "-vV"], check=True, text=True, capture_output=True
    )
    for line in result.stdout.splitlines():
        if line.startswith("host: "):
            return line.split(":", 1)[1].strip()
    raise RuntimeError("could not determine Rust host target from rustc -vV")


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def sha256_tree(directory: Path) -> str:
    digest = hashlib.sha256()
    for path in sorted(directory.rglob("*")):
        if not path.is_file() or "__pycache__" in path.parts or path.suffix == ".pyc":
            continue
        digest.update(path.relative_to(directory).as_posix().encode("utf-8"))
        digest.update(b"\0")
        with path.open("rb") as stream:
            for chunk in iter(lambda: stream.read(1024 * 1024), b""):
                digest.update(chunk)
    return digest.hexdigest()


def canonical_name(name: str) -> str:
    return re.sub(r"[-_.]+", "-", name).lower()


def render_constraints(freeze_output: str) -> str:
    entries: list[tuple[str, str]] = []
    for raw_line in freeze_output.splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or line.startswith("-e "):
            continue
        name = re.split(r"\s*@\s*|===|==|~=|!=|<=|>=|<|>|;", line, maxsplit=1)[0]
        if canonical_name(name) == "readio":
            continue
        entries.append((canonical_name(name), line))
    return "".join(f"{line}\n" for _, line in sorted(entries))


def service_constants() -> dict[str, Any]:
    tree = ast.parse(SERVICE.read_text(encoding="utf-8"))
    constants: dict[str, Any] = {}
    for node in tree.body:
        if isinstance(node, ast.Assign) and isinstance(node.value, ast.Constant):
            for target in node.targets:
                if isinstance(target, ast.Name) and target.id in {
                    "PROTOCOL",
                    "SERVICE_NAME",
                    "SERVICE_VERSION",
                }:
                    constants[target.id] = node.value.value
    expected = {"PROTOCOL", "SERVICE_NAME", "SERVICE_VERSION"}
    if constants.keys() < expected:
        raise RuntimeError("sidecar service metadata constants are incomplete")
    return constants


def dependencies(py: Path) -> dict[str, str]:
    installed = json.loads(capture(str(py), "-m", "pip", "list", "--format=json"))
    return {item["name"]: item["version"] for item in installed}


def write_manifest(
    *,
    path: Path,
    target: str,
    engine_extra: str,
    python_version: str,
    package_versions: dict[str, str],
    constraints_path: Path,
    binary_path: Path,
) -> dict[str, Any]:
    constants = service_constants()
    package_versions = dict(
        sorted(package_versions.items(), key=lambda item: canonical_name(item[0]))
    )
    version_by_name = {
        canonical_name(name): version for name, version in package_versions.items()
    }
    manifest = {
        "schema": "voxselect.sidecar-build.v1",
        "service": {
            "name": constants["SERVICE_NAME"],
            "version": constants["SERVICE_VERSION"],
            "protocol": constants["PROTOCOL"],
        },
        "target_triple": target,
        "engine_extra": engine_extra,
        "python_version": python_version,
        "readio_version": version_by_name.get("readio"),
        "readio_source_sha256": sha256_tree(VENDOR),
        "service_source_sha256": sha256_file(SERVICE),
        "dependencies": package_versions,
        "constraints": {
            "file": constraints_path.name,
            "sha256": sha256_file(constraints_path),
        },
        "binary": {
            "file": binary_path.name,
            "sha256": sha256_file(binary_path),
        },
    }
    path.write_text(
        json.dumps(manifest, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
        newline="\n",
    )
    return manifest


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Build VoxSelect's frozen Readio sidecar"
    )
    parser.add_argument(
        "--engine-extra",
        default="kitten",
        help="Readio optional extra to install (default: kitten). Use 'none' for no engine extra.",
    )
    parser.add_argument(
        "--constraints",
        type=Path,
        help="Use an existing pip constraints file instead of the generated target lock.",
    )
    parser.add_argument("--recreate-venv", action="store_true")
    args = parser.parse_args()

    target = rust_target()
    extra = args.engine_extra.lower()
    target_name = f"voxselect-readio-service-{target}"
    constraints_path = BINARY_DIR / f"{target_name}-{extra}.constraints.txt"
    manifest_path = (
        BINARY_DIR / f"{target_name}{'.exe' if os.name == 'nt' else ''}.manifest.json"
    )
    if args.constraints is not None and not args.constraints.is_file():
        raise FileNotFoundError(f"constraints file does not exist: {args.constraints}")
    input_constraints = args.constraints or (
        constraints_path if constraints_path.is_file() else None
    )

    expected_marker = {
        "target_triple": target,
        "engine_extra": extra,
        "constraints_sha256": sha256_file(input_constraints)
        if input_constraints
        else None,
    }
    marker_path = VENV / ".voxselect-build.json"
    if VENV.exists():
        try:
            marker = json.loads(marker_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            marker = None
        if args.recreate_venv or marker != expected_marker:
            shutil.rmtree(VENV)
    if not VENV.exists():
        run(sys.executable, "-m", "venv", str(VENV))

    py = venv_python()
    if input_constraints:
        run(
            str(py),
            "-m",
            "pip",
            "install",
            "--upgrade",
            "--constraint",
            str(input_constraints),
            "pip",
            "wheel",
            "setuptools",
        )
    else:
        run(str(py), "-m", "pip", "install", "--upgrade", "pip", "wheel", "setuptools")

    readio_requirement = str(VENDOR)
    if extra != "none":
        readio_requirement = f"{VENDOR}[{extra}]"
    install = [str(py), "-m", "pip", "install"]
    if input_constraints:
        install.extend(["--constraint", str(input_constraints)])
    install.extend([readio_requirement, "pynput>=1.7,<2", "pyinstaller>=6,<7"])
    run(*install)

    work = ROOT / "build" / "sidecar"
    dist = ROOT / "build" / "sidecar-dist"
    spec = ROOT / "build" / "sidecar-spec"
    for path in (work, dist, spec):
        path.mkdir(parents=True, exist_ok=True)

    engine_collect_args: list[str] = []
    if extra == "kitten":
        engine_collect_args = [
            "--collect-all",
            "kittensynth",
            "--collect-all",
            "kitteng2p",
            "--copy-metadata",
            "kittensynth",
        ]

    cmd = [
        str(py),
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
        "--collect-all",
        "numeralform",
        "--collect-all",
        "abbr2words",
        *engine_collect_args,
        str(SERVICE),
    ]
    run(*cmd)

    source = dist / (
        "voxselect-readio-service.exe"
        if os.name == "nt"
        else "voxselect-readio-service"
    )
    BINARY_DIR.mkdir(parents=True, exist_ok=True)
    destination = BINARY_DIR / f"{target_name}{'.exe' if os.name == 'nt' else ''}"
    shutil.copy2(source, destination)
    if os.name != "nt":
        destination.chmod(0o755)

    freeze_output = capture(str(py), "-m", "pip", "freeze", "--all")
    constraints_path.write_text(
        render_constraints(freeze_output), encoding="utf-8", newline="\n"
    )
    package_versions = dependencies(py)
    python_version = capture(
        str(py), "-c", "import platform; print(platform.python_version())"
    ).strip()
    manifest = write_manifest(
        path=manifest_path,
        target=target,
        engine_extra=extra,
        python_version=python_version,
        package_versions=package_versions,
        constraints_path=constraints_path,
        binary_path=destination,
    )
    marker_path.write_text(
        json.dumps(
            {
                "target_triple": target,
                "engine_extra": extra,
                "constraints_sha256": manifest["constraints"]["sha256"],
            },
            sort_keys=True,
        )
        + "\n",
        encoding="utf-8",
        newline="\n",
    )
    print(f"Sidecar ready: {destination}")
    print(f"Dependency constraints: {constraints_path}")
    print(f"Version manifest: {manifest_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
