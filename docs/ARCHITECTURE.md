# VoxSelect architecture

## Runtime boundary

```text
Tauri webview (src/main.js)
  -> narrow Tauri commands and events
Rust shell (src-tauri/src/lib.rs, backend.rs)
  -> owns lifecycle, request routing, clipboard, shortcuts, and autostart
Frozen Python sidecar (readio-service/service.py)
  -> Readio 0.6.8 public API
Readio -> VoiceRender -> local engine/model -> WAV bytes
```

The webview owns presentation and browser audio playback. It does not spawn the sidecar or call broad shell, clipboard, or global-shortcut plugin APIs. Rust's `BackendState` starts the configured target-triple sidecar, waits for readiness, routes JSONL requests, correlates responses, reports events to the UI, and shuts the process down with the application. Tauri commands expose specific operations such as `readio_engines`, `readio_targets`, `readio_voices`, `readio_speak`, `capture_selection`, and `read_clipboard`.

The Rust backend stores logs in the application data directory. `voxselect.log` rotates to `voxselect.log.1` when it exceeds the configured size. Logs summarize event type, generation, and encoded audio size. They do not contain selected text or WAV payloads.

## Sidecar protocol

The sidecar uses protocol version 2 over newline-delimited JSON on stdin and stdout. Startup begins with a `ready` event containing the service name, protocol, and service version. Rust validates the handshake, applies a startup timeout, and verifies the service with `ping` before the UI can use it.

Requests carry an ID, protocol, command, and parameters. Responses carry the matching ID, protocol, success state, and result or normalized error. Audio and error events carry a generation. Rust rejects malformed records, incompatible protocol messages, oversized frames, and pending requests when the child exits. The frontend ignores stale generations.

Supported service operations include configuration, capabilities, synthesis capabilities, engine/target/voice catalogs, diagnostics, speech, stop, selection-copy simulation, and shutdown. Expected Readio errors cross the boundary as structured error information rather than Python tracebacks.

## Readio boundary and synthesis

The bridge imports `CancellationToken`, `Readio`, `SpeakRequest`, and error/serialization helpers from Readio's public API. It does not import VoiceRender or other Readio internals directly. Readio owns speech planning, voice resolution, model/runtime integration, and WAV construction. VoxSelect owns text-length policy, scheduling, IPC, state, and browser playback.

Each speech request receives an immutable settings snapshot and a generation. A newer request or Stop invalidates earlier work. Readio cancellation is cooperative and does not interrupt a VoiceRender inference already in progress. Such a result is discarded and never played. Stop halts current webview playback immediately.

Engine, target, and voice controls come from Readio's runtime catalogs. Saved identifiers are reconciled against those catalogs; the UI does not report synthesis readiness when the engine or selection is unavailable.

## Selection, clipboard, and shortcuts

Rust registers global shortcuts and owns clipboard reads and writes. For selection capture, Rust saves the current plain-text clipboard value, writes a temporary sentinel, asks the sidecar to send the operating-system Copy shortcut, reads the resulting plain text, and restores the prior plain-text value. Non-text clipboard formats are not preserved. Apps that block synthetic Copy, have no selection, or do not expose plain text may fail. Read clipboard is the fallback for text already copied.

macOS may require Accessibility permission for synthetic input. Wayland compositors commonly restrict global input injection. VoxSelect does not yet use desktop portals or native accessibility APIs to retrieve selections. Autostart and shortcut ownership remain in Rust.

## Diagnostics and security

The diagnostics view exposes Readio capabilities, selected catalog identifiers, platform, and service/runtime metadata. Backend logs do not persist user text or audio. Tauri uses a restrictive content security policy and narrow webview permissions. The webview does not need a local HTTP service or direct child-process access.

## Packaging and reproducibility

`scripts/build_sidecar.py` builds a target-triple PyInstaller executable from the vendored Readio snapshot. The `kitten` extra explicitly collects KittenSynth, its G2P runtime, and package metadata so the frozen engine is discoverable. The builder generates a target-specific version-pinned dependency constraints file and a JSON manifest containing dependency versions, Python version, Readio and service source hashes, target triple, and hashes for both the frozen binary and constraints file.

`scripts/smoke_sidecar.py` verifies those hashes and exercises the exact target binary's handshake, ping, capabilities, catalogs, and shutdown. The release builder runs this smoke test before packaging and stages the manifest and constraints alongside release downloads.

## Deferred transport work

The current transport returns complete WAV data as a JSONL event and the webview plays it. Local HTTP endpoints, audio streaming, chunked playback, and native Rust playback are deferred. Revisit them only if measured latency, memory use, or user-facing behavior demonstrates a need.
