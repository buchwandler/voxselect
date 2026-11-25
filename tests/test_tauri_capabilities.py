from __future__ import annotations

import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
FRONTEND = (ROOT / "src" / "main.js").read_text(encoding="utf-8")
INDEX = (ROOT / "src" / "index.html").read_text(encoding="utf-8")
TAURI_CONFIG = json.loads(
    (ROOT / "src-tauri" / "tauri.conf.json").read_text(encoding="utf-8")
)
BACKEND = "\n".join(
    (ROOT / "src-tauri" / "src" / filename).read_text(encoding="utf-8")
    for filename in ("lib.rs", "backend.rs")
)
CAPABILITIES = json.loads(
    (ROOT / "src-tauri" / "capabilities" / "default.json").read_text(encoding="utf-8")
)
PERMISSIONS = set(CAPABILITIES["permissions"])


def test_frontend_delegates_backend_operations_to_rust() -> None:
    assert 'invoke("backend_start")' in FRONTEND
    assert 'invoke("readio_speak"' in FRONTEND
    assert 'invoke("capture_selection"' in FRONTEND
    assert 'invoke("read_clipboard")' in FRONTEND
    assert 'invoke("configure_shortcuts"' in FRONTEND
    assert 'invoke("set_autostart"' in FRONTEND
    assert 'listen("readio-event"' in FRONTEND
    assert "Command.sidecar" not in FRONTEND
    assert "@tauri-apps/plugin-" not in FRONTEND

    assert ".sidecar(SIDECAR)" in BACKEND
    assert "capture_selection_text" in BACKEND
    assert "configure_shortcuts" in BACKEND
    assert "autostart_status" in BACKEND
    assert "BackendState" in BACKEND


def test_frontend_capabilities_exclude_native_plugin_access() -> None:
    assert PERMISSIONS == {"core:default"}


def test_tauri_csp_is_local_and_disallows_script_injection() -> None:
    csp = TAURI_CONFIG["app"]["security"]["csp"]
    directives = {
        parts[0]: parts[1:]
        for directive in csp.split(";")
        if (parts := directive.strip().split())
    }
    assert directives["default-src"] == ["'self'"]
    assert directives["script-src"] == ["'self'"]
    assert directives["object-src"] == ["'none'"]
    assert "ipc:" in directives["connect-src"]
    assert "http://ipc.localhost" in directives["connect-src"]
    assert "http://127.0.0.1:1420" in directives["connect-src"]
    assert "blob:" in directives["media-src"]
    assert "'unsafe-eval'" not in csp


def test_diagnostics_and_capture_failures_are_visible_and_distinct() -> None:
    assert 'id="refreshDiagnostics"' in INDEX
    assert 'id="diagnosticsInfo"' in INDEX
    assert 'invoke("readio_diagnostics")' in FRONTEND
    assert "No selectable text captured." in FRONTEND
    assert "Clipboard contains no readable text." in FRONTEND
    assert "SPEECH_STATES.ENGINE_UNAVAILABLE" in FRONTEND
