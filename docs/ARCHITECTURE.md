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
   - imports only Readio's public API for speech and engine discovery
   - converts selected text into a `SpeakRequest`
   - calls `readio.api.speak(..., no_play=True)`
   - returns generated WAV bytes to the Tauri webview as base64

3. **Readio**
   - semantic plain-text planning
   - RenderProfile/voice resolution
   - VoiceRender preflight/rendering
   - AudioCompose output
   - public engine discovery

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
a generation counter. Readio synthesis is currently synchronous, so an active
model inference is not forcibly interrupted. If that inference completes after
Stop or a newer request, the stale result is discarded and never played.

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

The sidecar deliberately does not import `voicerender`, `utterplan`, or
`audiocompose` directly. The public Readio surface used by the MVP is:

```python
from readio.api import SpeakRequest, discover_engine_targets, list_engines, speak
```

The next useful Readio API improvement for VoxSelect is a public resident speech
session/job controller that owns model/session caching and explicit cancellation.
When that exists, the bridge can switch without changing the desktop UI or IPC
contract.
