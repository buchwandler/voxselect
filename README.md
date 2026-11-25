# VoxSelect

VoxSelect is a cross-platform Tauri tray application for reading selected or copied text aloud with local speech synthesis. It uses a bundled Python sidecar with Readio 0.6.8. No cloud TTS API is required. On first use, the selected engine may download model weights to the local user cache when Offline only is disabled.

## Runtime behavior

The webview owns presentation only. It calls narrow Tauri commands and listens for events. Rust owns the sidecar process, JSONL request routing, clipboard access, global shortcuts, autostart, and shutdown. The sidecar uses Readio's public synthesis, capability, diagnostic, and engine/target/voice catalog APIs.

Engine, target, and voice are selected from runtime catalogs. The UI does not report speech readiness until Readio reports a runnable engine and valid selections. Playback remains in the webview. Stop halts current playback immediately and invalidates the current generation. Readio cancellation is cooperative, so an active VoiceRender inference may continue until a stage boundary or completion; VoxSelect discards stale results.

## Developer setup

Requirements:

- Python 3.11 or newer
- Node.js 22
- Rust stable and Cargo
- Tauri prerequisites for the current operating system

Install frontend dependencies and build the target sidecar:

```bash
npm ci
python scripts/build_sidecar.py --engine-extra kitten
python scripts/smoke_sidecar.py
npm run tauri:dev
```

Alternatively, `python scripts/dev.py --engine-extra kitten` builds the sidecar and starts Tauri development mode. The sidecar build creates `.venv-sidecar`, installs the vendored Readio snapshot and selected engine extra, freezes the service for the current Rust target, and writes a target-specific constraints file and version manifest under `src-tauri/binaries/`. Existing generated constraints are reused on later builds. Pass `--constraints PATH` to use a supplied constraints artifact or `--recreate-venv` to recreate the build environment.

The exact-target smoke verifies the manifest's binary and constraints hashes, protocol-v2 ready handshake, ping, capabilities, catalogs, and shutdown. It reports unavailable runners or optional engine catalog queries as skips. The scheduled/manual real-engine workflow is stricter and fails if the engine cannot synthesize a valid non-silent WAV.

## Speech settings and diagnostics

Use the engine, target, and voice selectors to choose values exposed by the running Readio sidecar. Leave Offline only disabled for the first synthesis if model weights are not cached. Use Test voice before relying on the global Read Selection shortcut.

Refresh backend diagnostics to inspect Readio capabilities, service/runtime versions, platform, and selected engine identifiers. Rust writes bounded logs to the application data directory as `voxselect.log`, rotating an oversized file to `voxselect.log.1`. Logs omit selected text and WAV payloads.

## Selection and platform limitations

Selection capture uses the sidecar to send a synthetic Copy keystroke. Rust temporarily replaces the clipboard contents, reads the resulting plain text, and restores the previous plain-text value. Non-text clipboard formats are not preserved. Apps that block synthetic Copy, have no selection, or do not expose plain text may fail. Read clipboard is the fallback for text already copied.

On macOS, synthetic keyboard input may require Accessibility permission. Global shortcuts depend on operating-system permissions. Wayland compositors may block input injection, so selection capture and hotkeys vary by compositor. VoxSelect does not yet use desktop portals or native accessibility APIs for retrieving selections. Screen-reader, high-contrast, and formal platform accessibility audits remain outstanding.

## Tests and validation

```bash
python -m pytest -q
node --test tests/*.test.js
npm run build
cargo test --manifest-path src-tauri/Cargo.toml
```

The release workflow runs these checks before artifact builds. Its Windows, macOS, and Linux build matrix freezes and smoke-tests each platform's exact target sidecar. See [VALIDATION.md](VALIDATION.md) for current evidence and the physical-device checklist.

## Release builds

Build native packages on the matching operating system:

```bash
python scripts/build_release.py --engine-extra kitten --clean
```

Outputs are staged under `release/`, including installers/packages, a portable ZIP, checksums, the sidecar dependency constraints and version manifest, and `release-manifest.json`. End users do not need Python, Node.js, Rust, or a source checkout. See [docs/RELEASING.md](docs/RELEASING.md).

## Architecture

See [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md) for process ownership, protocol, privacy, and cancellation details. HTTP audio endpoints and streaming remain deferred until measurements show they are needed.
