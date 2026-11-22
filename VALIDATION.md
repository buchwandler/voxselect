# Validation performed

The MVP source was checked in the generation environment for:

- strict reconstruction of the supplied Readio Codecrate snapshot
- Python syntax compilation of the sidecar and build scripts
- JavaScript syntax parsing with Node.js
- JSON parsing of package, Tauri, and capability configuration
- sidecar dependency-boundary check: no direct VoiceRender, sounddevice, or private `readio.speech` imports
- Readio package metadata declaring `Apache-2.0`

Not validated here:

- Cargo/Tauri compilation (the generation environment does not contain Rust/Cargo)
- PyInstaller freezing with real VoiceRender engine dependencies (requires package installation)
- real TTS model execution
- code signing/notarization
- Wayland selection capture across compositors

Run `python scripts/dev.py --engine-extra kitten` on a configured developer machine as the first integration test.
