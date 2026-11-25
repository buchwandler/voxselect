# Validation status

Validation below describes the current working tree. Automated checks establish software and artifact behavior. They do not establish that a person heard speech from a physical audio device.

## Automated checks passed

- `python -m pytest -q`: 24 passed. This includes the Python service, protocol, capability, packaging-manifest, and WAV-validation tests.
- `node --test tests/*.test.js`: 9 passed.
- `npm run build`: passed.
- `cargo test --manifest-path src-tauri/Cargo.toml`: 10 Rust tests passed.
- A Linux x86_64 frozen sidecar was rebuilt using its generated dependency constraints. Its version manifest matched the exact binary and constraints hashes.
- `python scripts/smoke_sidecar.py`: passed the frozen sidecar ready handshake, ping, capabilities, runnable Kitten engine catalog, target and voice catalog queries, and shutdown.
- Both GitHub Actions workflow files parsed as YAML. The regular build workflow gates artifact jobs on Python, frontend, and Rust tests. Its Windows, macOS, and Linux matrix runs the exact-target frozen-sidecar smoke before packaging.
- A Linux Tauri build produced DEB and RPM packages in this workspace.

The build and smoke checks above cover this Linux target only. The cross-platform jobs have not been executed in this workspace. The real-engine workflow and script are present, but real model synthesis was not run locally.

## Not validated

- Audible output from physical Windows, macOS, or Linux devices. A valid non-silent WAV is not evidence that the audio device played it or that speech was intelligible.
- Real Kitten model execution, model download, or synthesis through the separately scheduled/manual workflow.
- Actual selection capture, hotkey behavior, clipboard restoration, accessibility permissions, or app shutdown on physical devices.
- Wayland behavior across compositors.
- macOS signing/notarization or Windows production signing.
- Cross-platform frozen builds in this environment.
- Ruff linting, because `ruff` is unavailable in the project environment.

The real-engine workflow is isolated from pull requests and runs on a schedule or manual dispatch. `scripts/smoke_real_engine.py` fails if the configured engine or target is unavailable, synthesis errors, the WAV is invalid, or its PCM samples are silent. It never treats skipped synthesis as success. The check still does not prove physical playback or audibility.

## Manual audible-speech checklist

Complete this checklist on a physical Windows, macOS, and Linux machine before claiming cross-platform audible output. Record the operating system/version, VoxSelect build, engine, target, voice, output device, and result for each machine.

1. Install and launch the platform's native package. Confirm the settings window opens without permission or backend errors.
2. Select a runnable engine, target, and voice from the catalogs. Confirm the UI reports ready only after the sidecar handshake, capability checks, and valid selection.
3. Leave Offline only disabled when model weights are not cached. Use Test voice with a short, known English sentence and wait for model download and synthesis to complete.
4. Confirm that the intended physical output device plays intelligible speech at a reasonable volume. Check that the UI reports success and that no playback error appears. A generated WAV or a successful `Audio.play()` call alone is not sufficient evidence.
5. Repeat with Read Selection and the configured global shortcut. Confirm the selected text is spoken and the prior plain-text clipboard value is restored. Note that non-text clipboard formats are not preserved.
6. Test Read Clipboard with copied text. Test empty clipboard and no-selection behavior, and confirm each has a distinct, actionable UI result.
7. During playback, press Stop and confirm playback stops promptly. If synthesis is still in progress, confirm any later stale result is discarded and never begins playback.
8. Quit VoxSelect and confirm the sidecar process exits. Inspect diagnostics/logs and confirm selected text and audio payloads are not recorded.
9. On Linux, record whether the test used X11 or Wayland and the compositor. Do not generalize one compositor's result to all Wayland environments.

## Reproduce the checks

```bash
python -m pytest -q
node --test tests/*.test.js
npm run build
cargo test --manifest-path src-tauri/Cargo.toml
python scripts/build_sidecar.py --engine-extra kitten
python scripts/smoke_sidecar.py
```

To run the strict real-engine WAV check, with model downloads and runtime available:

```bash
python scripts/smoke_real_engine.py \
  --engine kitten \
  --timeout 1200 \
  --output build/real-engine-smoke.wav
```

This command validates the exact target-triple frozen binary and a valid non-silent PCM WAV. It does not play the audio or establish human audibility.
