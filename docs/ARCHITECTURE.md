# VoxSelect MVP architecture

## Components

1. **Tauri 2 desktop shell**
   - settings window
   - tray icon
   - global read/stop shortcuts
   - clipboard access
   - autostart
   - browser audio playback so Stop is immediate

2. **Frozen Python sidecar**
   - long-running JSONL process
   - holds a persistent Readio instance and uses its public synthesis and normalized catalog API
   - converts selected text into a `SpeakRequest`
   - calls `Readio.synthesize(..., cancellation=token)`
   - returns generated WAV bytes to the Tauri webview as base64
   - serializes expected Readio failures through `describe_error()` as stable `ErrorInfo` records

3. **Readio**
   - semantic plain-text planning
   - RenderProfile/voice resolution
   - VoiceRender preflight/rendering
   - AudioCompose output
   - normalized public engine, target, voice, and option catalogs

## IPC protocol

Messages are newline-delimited JSON on sidecar stdin/stdout.

Requests:

- `ping`
- `configure`
- `copy_selection`
- `speak`
- `stop`
- `engines`
- `shutdown`

Speech synthesis is performed on a worker thread. New speech or Stop increments
a generation counter and cancels the active token. Readio checks cancellation at
workflow stage boundaries only. This does not interrupt an active VoiceRender
inference. If inference completes after Stop or a newer request, the stale result
is discarded and never played.
## Selection capture

The global shortcut is registered by Tauri. When released, the UI writes a
sentinel to the clipboard, asks the Python sidecar to synthesize the operating
system's Copy chord through `pynput`, waits a configurable delay, then reads the
clipboard. The previous text clipboard value is restored afterward.

This is an MVP fallback rather than the final accessibility integration:

- Windows: replace with UI Automation selected-text retrieval.
- macOS: replace/fallback with Accessibility API retrieval; Copy simulation may
  require Accessibility permission.
- Linux X11: simulated Copy generally works.
- Linux Wayland: global input injection is compositor-dependent and may fail;
  keep Read Clipboard as a fallback and later integrate desktop portals where
  available.

## Readio API boundary

The sidecar deliberately avoids direct imports of `voicerender`, `utterplan`, and
`audiocompose`. It uses Readio's stable application API:

```python
from readio.api import CancellationToken, Readio, SpeakRequest, describe_error

service = Readio()
token = CancellationToken()
request = SpeakRequest(text="Selected text")
result = service.synthesize(request, cancellation=token)
wav_bytes = result.audio.to_wav_bytes()
engines = service.list_engines()
targets = service.list_targets(engines[0].id, language="en-US", offline=True)
```

The sidecar owns scheduling, text-length policy, transport, and browser playback.
Synthesis returns audio in memory; Readio does not create a sidecar WAV file or
play audio. `CancellationToken` provides cooperative stage-boundary cancellation,
not interruption of a VoiceRender inference already running. Expected Readio errors
are projected with `describe_error()` before they cross the JSONL boundary.
