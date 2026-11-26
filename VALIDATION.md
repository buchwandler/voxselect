# Validation status

Validation below describes the current working tree. Automated checks establish software and artifact behavior. They do not establish that a person heard speech from a physical audio device.

## Automated checks passed

- `python -m pytest -q`: 31 passed, including service progress, cancellation, invalid-audio, and protocol tests.
- `node --test tests/*.test.js`: 18 passed, including playback-tone WAV validation.
- `npm run build`: passed.
- `cargo test --manifest-path src-tauri/Cargo.toml`: 11 Rust tests passed.
- `env -u CI node_modules/.bin/tauri build --no-bundle --debug` passed using the locked Tauri CLI 2.12.6 schema, including `bundle.linux.appimage.bundleMediaFramework`. No package bundle was produced by this check.
- `python scripts/build_sidecar.py --engine-extra kitten` rebuilt the Linux frozen sidecar with dynamic language modules collected and updated its hash manifest.
- `python scripts/smoke_sidecar.py`: passed the frozen sidecar ready handshake, ping, capabilities, Kitten engine catalog, and shutdown.
- Both GitHub Actions workflow files parsed as YAML. The regular build workflow gates artifact jobs on Python, frontend, and Rust tests. Its Windows, macOS, and Linux matrix runs the exact-target frozen-sidecar smoke before packaging.

The build and smoke checks above cover this Linux target only. The cross-platform jobs have not been executed in this workspace. Real Kitten synthesis passed locally, but physical-device audibility and interactive application playback remain unverified.

## Not validated

- Audible output from physical Windows, macOS, or Linux devices. A valid non-silent WAV is not evidence that the audio device played it or that speech was intelligible.
- Actual selection capture, hotkey behavior, clipboard restoration, accessibility permissions, or app shutdown on physical devices.
- Wayland behavior across compositors.
- macOS signing/notarization or Windows production signing.
- Cross-platform frozen builds in this environment.
- Ruff linting, because `ruff` is unavailable in the project environment.

The real-engine workflow is isolated from pull requests and runs on a schedule or manual dispatch. `scripts/smoke_real_engine.py` fails if the configured engine or target is unavailable, synthesis errors, the WAV is invalid, or its PCM samples are silent. It never treats skipped synthesis as success. The check still does not prove physical playback or audibility.

## Linux multimedia validation

- The locked npm dependency and installed local schema are Tauri CLI 2.12.6. `env -u CI node_modules/.bin/tauri build --no-bundle --debug` passed with `bundle.linux.appimage.bundleMediaFramework: true`; no bundle was created.
- `python scripts/doctor_linux.py` found WebKitGTK 4.1 version 2.54.2, but `gst-inspect-1.0` is absent. The doctor exited 1 because the required GStreamer plugins could not be checked.
- `python scripts/doctor_linux.py --tone-test` also found `gst-launch-1.0` absent, so no GStreamer tone was played.
- The real-engine smoke generated a valid non-silent 24 kHz mono WAV with 89,800 frames at `build/real-engine-smoke.wav`. `paplay build/real-engine-smoke.wav` exited 0, but no person confirmed audible output. The interactive application's playback-tone and Test voice paths were not exercised in this session. No physical-device or WebView result is inferred.
- The first frozen Kitten synthesis attempt exposed missing dynamic `numeralform` and `abbr2words` modules. The PyInstaller build now collects those packages, and the real-engine smoke passes. The required repeatable WebView failure after a system-player check has not been demonstrated. Playback ownership remains in the WebView; Rust-native playback is not justified by current evidence.

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
