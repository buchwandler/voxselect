#!/usr/bin/env python3
from __future__ import annotations

import argparse
import hashlib
import json
import platform
import shutil
import subprocess
import sys
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
TAURI_DIR = ROOT / "src-tauri"
SIDECAR_BINARIES = TAURI_DIR / "binaries"
CONFIG_PATH = TAURI_DIR / "tauri.conf.json"
RELEASE_DIR = ROOT / "release"
TARGET_RELEASE = TAURI_DIR / "target" / "release"
LEGAL_FILES = ("LICENSE", "NOTICE", "THIRD_PARTY.md")


def run(*args: str, cwd: Path | None = None) -> None:
    print("+", " ".join(args), flush=True)
    subprocess.run(args, cwd=cwd or ROOT, check=True)


def rust_target() -> str:
    result = subprocess.run(
        ["rustc", "-vV"], check=True, text=True, capture_output=True
    )
    for line in result.stdout.splitlines():
        if line.startswith("host: "):
            return line.split(":", 1)[1].strip()
    raise RuntimeError("could not determine Rust host target from rustc -vV")


def platform_name() -> str:
    value = platform.system().lower()
    if value == "windows":
        return "windows"
    if value == "darwin":
        return "macos"
    if value == "linux":
        return "linux"
    raise RuntimeError(f"unsupported release platform: {platform.system()}")


def arch_name(target: str) -> str:
    machine = target.split("-", 1)[0]
    return {
        "x86_64": "x86_64",
        "aarch64": "arm64",
        "i686": "x86",
        "i586": "x86",
        "armv7": "armv7",
    }.get(machine, machine)


def default_bundles(system: str) -> list[str]:
    if system == "windows":
        return ["nsis", "msi"]
    if system == "macos":
        return ["app", "dmg"]
    if system == "linux":
        return ["deb", "appimage"]
    raise RuntimeError(f"unsupported release platform: {system}")


def load_config() -> dict:
    return json.loads(CONFIG_PATH.read_text(encoding="utf-8"))


def clean_release_dir() -> None:
    if RELEASE_DIR.exists():
        shutil.rmtree(RELEASE_DIR)


def copy_legal_files(destination: Path) -> None:
    for name in LEGAL_FILES:
        source = ROOT / name
        if source.exists():
            shutil.copy2(source, destination / name)


def write_portable_readme(destination: Path, system: str) -> None:
    lines = [
        "VoxSelect portable release",
        "==========================",
        "",
        "This package is built for end users. Python, Node.js and Rust are not required.",
        "",
    ]
    if system == "windows":
        lines += [
            "Run VoxSelect.exe.",
            "Keep voxselect-readio-service.exe in the same folder as VoxSelect.exe.",
            "If WebView2 is missing on an older Windows installation, use the installer build instead.",
        ]
    elif system == "macos":
        lines += [
            "Open VoxSelect.app.",
            "For public distribution the app should be code-signed and notarized.",
        ]
    else:
        lines += [
            "Run the VoxSelect AppImage file and keep this folder together.",
            "If needed, mark the AppImage executable before launching it.",
        ]
    lines += [
        "",
        "VoxSelect uses a bundled local Readio sidecar for speech synthesis.",
        "See LICENSE, NOTICE and THIRD_PARTY.md for licensing information.",
        "",
    ]
    (destination / "README-PORTABLE.txt").write_text(
        "\n".join(lines), encoding="utf-8", newline="\n"
    )


def portable_zip(source_dir: Path, output_zip: Path, system: str) -> None:
    output_zip.parent.mkdir(parents=True, exist_ok=True)
    if system == "macos" and shutil.which("ditto"):
        run(
            "ditto",
            "-c",
            "-k",
            "--sequesterRsrc",
            "--keepParent",
            str(source_dir),
            str(output_zip),
        )
        return

    with zipfile.ZipFile(output_zip, "w", compression=zipfile.ZIP_DEFLATED) as zf:
        for path in sorted(source_dir.rglob("*")):
            rel = path.relative_to(source_dir.parent)
            if path.is_dir():
                continue
            zf.write(path, rel.as_posix())


def find_one(paths: list[Path], description: str) -> Path:
    matches = [p for p in paths if p.exists()]
    if not matches:
        raise FileNotFoundError(f"could not find {description}")
    return matches[0]


def find_bundle_file(suffix: str) -> Path | None:
    bundle = TARGET_RELEASE / "bundle"
    if not bundle.exists():
        return None
    suffix_lower = suffix.lower()
    matches = sorted(
        p
        for p in bundle.rglob("*")
        if p.is_file() and p.name.lower().endswith(suffix_lower)
    )
    return matches[0] if matches else None


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def copy_download(source: Path, destination: Path) -> dict:
    destination.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(source, destination)
    digest = sha256_file(destination)
    return {
        "file": destination.name,
        "sha256": digest,
        "size": destination.stat().st_size,
    }


def stage_installers(
    *, system: str, version: str, arch: str, downloads: Path
) -> list[dict]:
    staged: list[dict] = []
    prefix = f"VoxSelect-{version}-{system}-{arch}"

    if system == "windows":
        nsis = find_bundle_file(".exe")
        msi = find_bundle_file(".msi")
        if nsis:
            staged.append(copy_download(nsis, downloads / f"{prefix}-setup.exe"))
        if msi:
            staged.append(copy_download(msi, downloads / f"{prefix}.msi"))
    elif system == "macos":
        dmg = find_bundle_file(".dmg")
        if dmg:
            staged.append(copy_download(dmg, downloads / f"{prefix}.dmg"))
    else:
        deb = find_bundle_file(".deb")
        appimage = find_bundle_file(".appimage")
        if deb:
            staged.append(copy_download(deb, downloads / f"{prefix}.deb"))
        if appimage:
            target = downloads / f"{prefix}.AppImage"
            staged.append(copy_download(appimage, target))
            target.chmod(target.stat().st_mode | 0o111)

    if not staged:
        raise FileNotFoundError(
            f"no installer/package artifacts found under {TARGET_RELEASE / 'bundle'}"
        )
    return staged


def stage_portable(
    *, system: str, version: str, arch: str, downloads: Path, portable_root: Path
) -> dict:
    folder_name = f"VoxSelect-{version}-{system}-{arch}-portable"
    destination = portable_root / folder_name
    if destination.exists():
        shutil.rmtree(destination)
    destination.mkdir(parents=True, exist_ok=True)

    if system == "windows":
        main = find_one(
            [TARGET_RELEASE / "voxselect.exe", TARGET_RELEASE / "VoxSelect.exe"],
            "Windows VoxSelect executable",
        )
        sidecar = find_one(
            [TARGET_RELEASE / "voxselect-readio-service.exe"],
            "Windows Readio sidecar",
        )
        shutil.copy2(main, destination / "VoxSelect.exe")
        shutil.copy2(sidecar, destination / "voxselect-readio-service.exe")
    elif system == "macos":
        app_candidates = sorted((TARGET_RELEASE / "bundle").rglob("*.app"))
        if not app_candidates:
            raise FileNotFoundError("could not find macOS VoxSelect.app bundle")
        shutil.copytree(
            app_candidates[0],
            destination / app_candidates[0].name,
            symlinks=True,
        )
    else:
        appimage = find_bundle_file(".appimage")
        if not appimage:
            raise FileNotFoundError("could not find Linux AppImage")
        target = destination / "VoxSelect.AppImage"
        shutil.copy2(appimage, target)
        target.chmod(target.stat().st_mode | 0o111)

    copy_legal_files(destination)
    write_portable_readme(destination, system)

    zip_path = downloads / f"{folder_name}.zip"
    portable_zip(destination, zip_path, system)
    digest = sha256_file(zip_path)
    return {
        "file": zip_path.name,
        "sha256": digest,
        "size": zip_path.stat().st_size,
    }


def write_checksums(downloads: Path, records: list[dict], platform_id: str) -> dict:
    path = downloads / f"VoxSelect-{platform_id}-SHA256SUMS.txt"
    body = "".join(f"{item['sha256']}  {item['file']}\n" for item in records)
    path.write_text(body, encoding="utf-8", newline="\n")
    return {
        "file": path.name,
        "sha256": sha256_file(path),
        "size": path.stat().st_size,
    }


def stage_sidecar_metadata(
    *, system: str, target: str, engine_extra: str, downloads: Path
) -> list[dict]:
    suffix = ".exe" if system == "windows" else ""
    target_name = f"voxselect-readio-service-{target}"
    manifest = SIDECAR_BINARIES / f"{target_name}{suffix}.manifest.json"
    constraints = (
        SIDECAR_BINARIES / f"{target_name}-{engine_extra.lower()}.constraints.txt"
    )
    return [
        copy_download(manifest, downloads / manifest.name),
        copy_download(constraints, downloads / constraints.name),
    ]


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Build and stage VoxSelect installers plus a portable end-user package"
    )
    parser.add_argument("--engine-extra", default="kitten")
    parser.add_argument(
        "--bundles",
        default="",
        help="Comma-separated Tauri bundle targets. Defaults to native release targets.",
    )
    parser.add_argument("--clean", action="store_true", help="Remove release/ first")
    parser.add_argument("--recreate-venv", action="store_true")
    parser.add_argument(
        "--skip-sidecar", action="store_true", help="Reuse an already-built sidecar"
    )
    parser.add_argument(
        "--skip-build", action="store_true", help="Reuse existing Tauri build outputs"
    )
    args = parser.parse_args()

    system = platform_name()
    target = rust_target()
    arch = arch_name(target)
    config = load_config()
    version = str(config.get("version") or "0.0.0")
    bundle_targets = [
        value.strip()
        for value in (
            args.bundles.split(",") if args.bundles else default_bundles(system)
        )
        if value.strip()
    ]

    if args.clean:
        clean_release_dir()

    if not args.skip_sidecar:
        sidecar_cmd = [
            sys.executable,
            str(ROOT / "scripts" / "build_sidecar.py"),
            "--engine-extra",
            args.engine_extra,
        ]
        if args.recreate_venv:
            sidecar_cmd.append("--recreate-venv")
        run(*sidecar_cmd)

    run(sys.executable, str(ROOT / "scripts" / "smoke_sidecar.py"))

    if not args.skip_build:
        if not shutil.which("npm"):
            raise RuntimeError("npm is required on the build machine")
        if not (ROOT / "node_modules").exists():
            raise RuntimeError("node_modules is missing; run 'npm ci' before building")
        run(
            "npm",
            "run",
            "tauri:build",
            "--",
            "--bundles",
            ",".join(bundle_targets),
        )

    downloads = RELEASE_DIR / "downloads"
    portable_root = RELEASE_DIR / "portable"
    downloads.mkdir(parents=True, exist_ok=True)
    portable_root.mkdir(parents=True, exist_ok=True)

    records = stage_installers(
        system=system, version=version, arch=arch, downloads=downloads
    )
    records.append(
        stage_portable(
            system=system,
            version=version,
            arch=arch,
            downloads=downloads,
            portable_root=portable_root,
        )
    )
    sidecar_records = stage_sidecar_metadata(
        system=system,
        target=target,
        engine_extra=args.engine_extra,
        downloads=downloads,
    )
    records.extend(sidecar_records)
    checksum_record = write_checksums(downloads, records, f"{version}-{system}-{arch}")

    manifest = {
        "product": "VoxSelect",
        "version": version,
        "platform": system,
        "arch": arch,
        "rust_target": target,
        "bundles": bundle_targets,
        "downloads": [*records, checksum_record],
        "portable_folder": str(
            portable_root / f"VoxSelect-{version}-{system}-{arch}-portable"
        ),
        "sidecar": {
            "version_manifest": sidecar_records[0]["file"],
            "constraints": sidecar_records[1]["file"],
        },
    }
    manifest_path = RELEASE_DIR / "release-manifest.json"
    manifest_path.write_text(
        json.dumps(manifest, indent=2) + "\n", encoding="utf-8", newline="\n"
    )

    print("\nRelease artifacts:")
    for item in manifest["downloads"]:
        print(f"  {downloads / item['file']}")
    print(f"  {manifest['portable_folder']}/")
    print(f"  {manifest_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
