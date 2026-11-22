# VoxSelect MVP

VoxSelect is a small cross-platform tray utility for reading selected text aloud
locally. Highlight text, press a global hotkey, and VoxSelect sends the text to
Readio running in a bundled Python sidecar. No cloud TTS API is required.

**License:** Apache-2.0

## What is in this MVP

- Tauri 2 desktop application for Windows, macOS, and Linux
- tray icon and settings window
- global Read Selection and Stop shortcuts
- Read Clipboard tray fallback
- voice/engine/target/language fields
- offline mode
- start-at-login setting
- persistent Python sidecar process
- current Readio source snapshot vendored for reproducible builds
- PyInstaller sidecar build script
- Tauri installer/package build script
- latest-request-wins semantics: stale synthesis output is discarded
- immediate stop of already-playing audio

## Important MVP limitation

`Readio.synthesize()` is synchronous. Its `CancellationToken` is cooperative at
workflow stage boundaries and does not interrupt VoiceRender inference already in
progress. Stop immediately stops browser playback, cancels the active workflow token,
and invalidates the generation. If inference is active, it runs to a stage boundary
or completion, and VoxSelect discards the stale result. Queueing and playback remain
application-owned.

## Prerequisites for developers

The developer/build machine needs:

- Python 3.10+
- Node.js 20+
- Rust stable / Cargo
- normal Tauri OS build prerequisites

End users of a built installer do **not** need Python, Node, or Rust.

## First run

```bash
npm install
python scripts/build_sidecar.py --engine-extra kitten
npm run tauri:dev
```

Or:

```bash
python scripts/dev.py --engine-extra kitten
```

The sidecar script creates its own `.venv-sidecar`, installs the vendored Readio
snapshot plus the requested engine extra, freezes the bridge with PyInstaller,
and copies it to the target-triple filename Tauri expects.

To build without an engine extra:

```bash
python scripts/build_sidecar.py --engine-extra none
```

You can substitute another Readio extra such as `kokoro`, `pocket`, `piper`, or
`supertonic` if its dependencies and license fit your release.

## Configure a voice

Open VoxSelect and set either:

1. a fully-qualified Readio/VoiceRender voice selector, **or**
2. Engine + Target/model, optionally with a voice name.

Press **Inspect engines** to see the engine metadata available in the frozen
sidecar. Press **Test voice** before using the global shortcut.

The default hotkeys are:

- Read selected text: `CommandOrControl+Alt+Space`
- Stop: `CommandOrControl+Alt+Escape`

## Build an installer/package

```bash
npm install
python scripts/build_release.py --engine-extra kitten
```

Tauri places platform bundles under `src-tauri/target/release/bundle/`.

Build on each target operating system rather than expecting one OS to create all
three native packages. macOS public distribution additionally needs Apple code
signing/notarization; Windows production releases should also be code-signed.

## Linux and Wayland

The MVP selection method synthesizes Copy with `pynput`. It is expected to work
best on Windows, macOS (with Accessibility permission when required), and X11.
Wayland intentionally restricts global input injection, so selection capture can
fail depending on compositor/session. Use **Read clipboard** as the MVP fallback.
A production Linux implementation should use portal/accessibility mechanisms
where available.

## Source layout

```text
src/                     Vite settings UI and desktop-side orchestration
src-tauri/               Tauri/Rust shell and tray
readio-service/           Python JSONL sidecar
scripts/                  sidecar/dev/release build helpers
vendor/readio/            supplied Readio source snapshot
docs/ARCHITECTURE.md      component and API-boundary notes
LICENSE                   Apache License 2.0 for VoxSelect
NOTICE                    project notice
THIRD_PARTY.md            dependency licensing reminder
```

## Release checklist after the MVP

- replace Copy simulation with native accessibility selection retrieval
- consider engine-supported inference interruption or session reuse while retaining latest-generation safety
- make engine/model/voice discovery fully interactive instead of free-text first
- model download/install UI through Readio's public management API
- streaming/chunked speech for long selections
- persistent structured config instead of localStorage
- signed auto-updates
- SBOM and automated third-party license report
- Windows/macOS/Linux CI smoke tests and real TTS E2E tests
