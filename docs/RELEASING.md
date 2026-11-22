# Releasing VoxSelect

VoxSelect release builds are native per operating system. Build Windows on Windows, macOS on macOS, and Linux on Linux.

## Local release build

Install the developer toolchain once on the build machine:

- Python 3.10+ (3.11 recommended)
- Node.js 20+
- Rust stable
- Tauri's platform prerequisites

Then run:

```bash
npm ci
python scripts/build_release.py --engine-extra kitten --clean
```

The script builds the PyInstaller Readio sidecar, runs the Tauri release build, and stages end-user downloads under `release/`.

Expected outputs:

```text
release/
├─ downloads/
│  ├─ platform installer/package files
│  ├─ VoxSelect-<version>-<platform>-<arch>-portable.zip
│  └─ VoxSelect-<version>-<platform>-<arch>-SHA256SUMS.txt
├─ portable/
│  └─ VoxSelect-<version>-<platform>-<arch>-portable/
└─ release-manifest.json
```

Platform defaults:

- Windows: NSIS setup EXE + MSI + portable ZIP
- macOS: app bundle + DMG + portable ZIP containing the `.app`
- Linux: DEB + AppImage + portable ZIP containing the AppImage

The portable package is intended for users who do not have Python, Node.js, Rust, or a VoxSelect source checkout.

## GitHub Actions

`.github/workflows/build.yml` builds all three platforms. Every workflow run exposes downloadable artifacts in GitHub Actions. Pushing a tag such as `v0.1.0` additionally creates a GitHub Release and attaches the installers, portable ZIPs, AppImages, and checksum files.

Before a public production release, add platform signing:

- Windows: Authenticode signing for the executable/installers.
- macOS: Developer ID signing and notarization.
- Linux: optionally sign repository packages/AppImages according to your distribution channel.

Unsigned builds are suitable for internal testing but may trigger operating-system trust warnings.

## First-run model

The packaged Python sidecar contains the Kitten engine runtime, not the model weights. The
default target is `KittenML/kitten-tts-mini-0.8` with `Jasper`. A fresh user should leave
**Offline only** disabled for the first synthesis so the model can be downloaded into the
user cache. Subsequent synthesis can run with offline mode enabled.
