# Releasing VoxSelect

Build native packages on their target operating system. The release workflow uses Windows, macOS, and Ubuntu runners rather than cross-compiling platform installers.

## Local release build

Requirements:

- Python 3.11 or newer
- Node.js 22
- Rust stable and Cargo
- Tauri prerequisites for the build operating system

Run:

```bash
npm ci
python scripts/build_release.py --engine-extra kitten --clean
```

The release builder creates the sidecar, runs its exact-target smoke test, builds Tauri artifacts, and stages end-user downloads under `release/`. `scripts/build_sidecar.py` creates `.venv-sidecar`, installs the vendored Readio snapshot and requested engine extra, and freezes the Python service for the current Rust target.

The sidecar build writes two target-specific files under `src-tauri/binaries/`:

- A version-pinned Python dependency constraints file, reusable on later builds or supplied with `--constraints PATH`.
- A JSON version manifest with target, Python and package versions, Readio/service source hashes, and hashes of the exact frozen binary and constraints file.

For the Kitten engine, PyInstaller explicitly collects KittenSynth, its G2P runtime, and package metadata so the engine remains discoverable in the frozen process. The release builder runs `scripts/smoke_sidecar.py` before packaging. The smoke verifies the exact target binary and its manifest, then checks protocol-v2 startup, ping, capabilities, catalogs, and shutdown. Missing target binaries fail the normal release build; use the smoke script's `--skip-missing` only for an intentional runner probe.

`--recreate-venv` rebuilds the sidecar environment. `--skip-sidecar` and `--skip-build` are useful only when the matching target artifacts and metadata already exist. The builder still smoke-tests the target sidecar and stages its metadata.

## Staged files

```text
release/
├─ downloads/
│  ├─ platform installer/package files
│  ├─ portable ZIP
│  ├─ sidecar constraints and version manifest
│  └─ SHA256SUMS file covering staged downloads
├─ portable/
│  └─ platform portable folder
└─ release-manifest.json
```

`release-manifest.json` identifies the platform, architecture, Rust target, package records, portable folder, and sidecar metadata. The GitHub Release publishing job attaches the sidecar constraints and manifest as assets as well as installers, portable archives, and checksums. End users do not need Python, Node.js, Rust, or a source checkout.

Platform defaults:

- Windows: NSIS setup EXE, MSI, and portable ZIP.
- macOS: app bundle, DMG, and portable ZIP containing the `.app`.
- Linux: DEB, RPM, AppImage, and portable ZIP containing the AppImage.

## GitHub Actions

`.github/workflows/build.yml` runs Python tests, frontend tests/build, and Rust tests before artifact jobs. The Windows, macOS, and Ubuntu matrix builds each platform's frozen sidecar and runs the exact-target smoke before producing installers. Pull requests and pushes to `main` build artifacts. A `v*` tag additionally publishes a GitHub Release.

`.github/workflows/real-engine.yml` is separate from pull-request and release-build jobs. It runs weekly or through manual dispatch on Ubuntu, builds the frozen sidecar with the Kitten extra, synthesizes speech, and validates that the WAV is valid and non-silent before uploading it. Missing runtime support, synthesis failures, invalid WAV data, and silence fail the job rather than becoming skips. This checks real model execution and audio data, not physical-device playback or human audibility.

## Release acceptance

Before claiming audible output, use the manual checklist in [VALIDATION.md](../VALIDATION.md) on physical Windows, macOS, and Linux systems. The real-engine workflow does not replace that checklist. Record device, engine, target, voice, and observed result. Keep Wayland compositor results scoped to the compositor tested.

Public macOS releases still need code signing and notarization. Windows production builds should be code-signed. Linux signing depends on the distribution channel. Unsigned builds may trigger platform trust warnings.
