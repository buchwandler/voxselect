# Third-party components

VoxSelect itself is licensed under Apache-2.0.

The MVP depends on Tauri and its plugins, Readio and its specialist packages,
PyInstaller, pynput, and whichever optional VoiceRender engine you select at
build time. Those projects retain their own licenses.

A vendored Readio source snapshot is included under `vendor/readio/`; its own
`LICENSE` file is preserved. The default sidecar build command requests Readio's
`kitten` optional extra, but VoxSelect does not bundle a voice model in this ZIP.
Before publishing binaries, generate and review a complete SBOM/license report
for the exact dependency versions and model assets included in that release.

Do not assume the Apache-2.0 license of VoxSelect changes the license of an
optional TTS engine, model, runtime, or downloaded voice asset.
