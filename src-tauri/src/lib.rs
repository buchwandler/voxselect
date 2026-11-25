mod backend;

use backend::BackendState;
use serde::Deserialize;
use serde_json::{json, Value};
use std::time::{Duration, SystemTime, UNIX_EPOCH};
use tauri::menu::{Menu, MenuItem};
use tauri::tray::TrayIconBuilder;
use tauri::{AppHandle, Emitter, Manager, State};
use tauri_plugin_autostart::{MacosLauncher, ManagerExt as AutostartExt};
use tauri_plugin_clipboard_manager::ClipboardExt;
use tauri_plugin_global_shortcut::{GlobalShortcutExt, ShortcutState};

#[derive(Deserialize)]
struct SpeechSettings {
    engine: Option<String>,
    target: Option<String>,
    voice: Option<String>,
    language: String,
    speaker: Option<String>,
    offline: bool,
    max_chars: usize,
}

#[tauri::command]
async fn backend_start(app: AppHandle, state: State<'_, BackendState>) -> Result<(), String> {
    state.start(app).await
}

#[tauri::command]
async fn backend_restart(app: AppHandle, state: State<'_, BackendState>) -> Result<(), String> {
    state.restart(app).await
}

#[tauri::command]
async fn backend_shutdown(state: State<'_, BackendState>) -> Result<(), String> {
    state.shutdown().await;
    Ok(())
}

#[tauri::command]
async fn readio_configure(
    state: State<'_, BackendState>,
    settings: SpeechSettings,
) -> Result<Value, String> {
    state
        .request(
            "configure",
            json!({"settings": {
                "engine": settings.engine,
                "target": settings.target,
                "voice": settings.voice,
                "language": settings.language,
                "speaker": settings.speaker,
                "offline": settings.offline,
                "max_chars": settings.max_chars,
            }}),
        )
        .await
}

#[tauri::command]
async fn readio_capabilities(state: State<'_, BackendState>) -> Result<Value, String> {
    state.request("capabilities", json!({})).await
}

#[tauri::command]
async fn readio_synthesis_capabilities(state: State<'_, BackendState>) -> Result<Value, String> {
    state.request("synthesis_capabilities", json!({})).await
}

#[tauri::command]
async fn readio_engines(state: State<'_, BackendState>) -> Result<Value, String> {
    state.request("engines", json!({})).await
}

#[tauri::command]
async fn readio_targets(
    state: State<'_, BackendState>,
    engine: String,
    language: Option<String>,
    offline: bool,
    refresh: bool,
) -> Result<Value, String> {
    state
        .request(
            "targets",
            json!({"engine": engine, "language": language, "offline": offline, "refresh": refresh}),
        )
        .await
}

#[tauri::command]
async fn readio_voices(
    state: State<'_, BackendState>,
    engine: String,
    target: String,
    language: Option<String>,
    offline: bool,
    refresh: bool,
) -> Result<Value, String> {
    state
        .request(
            "voices",
            json!({"engine": engine, "target": target, "language": language, "offline": offline, "refresh": refresh}),
        )
        .await
}

#[tauri::command]
async fn readio_diagnostics(state: State<'_, BackendState>) -> Result<Value, String> {
    state.request("diagnostics", json!({})).await
}

#[tauri::command]
async fn readio_speak(state: State<'_, BackendState>, text: String) -> Result<Value, String> {
    state.request("speak", json!({"text": text})).await
}

#[tauri::command]
async fn readio_stop(state: State<'_, BackendState>) -> Result<Value, String> {
    state.request("stop", json!({})).await
}

#[tauri::command]
async fn read_clipboard(app: AppHandle) -> Result<String, String> {
    clipboard_read(app).await
}

#[tauri::command]
async fn capture_selection(
    app: AppHandle,
    state: State<'_, BackendState>,
    delay_ms: u64,
) -> Result<Option<String>, String> {
    capture_selection_text(app, state.inner(), delay_ms).await
}

#[tauri::command]
fn configure_shortcuts(
    app: AppHandle,
    read_hotkey: String,
    stop_hotkey: String,
    copy_delay: u64,
) -> Result<(), String> {
    let shortcuts = app.global_shortcut();
    shortcuts
        .unregister_all()
        .map_err(|error| error.to_string())?;
    if !read_hotkey.trim().is_empty() {
        shortcuts
            .on_shortcut(read_hotkey.as_str(), move |app, _, event| {
                if event.state != ShortcutState::Pressed {
                    return;
                }
                let app = app.clone();
                tauri::async_runtime::spawn(async move {
                    let state = app.state::<BackendState>();
                    match capture_selection_text(app.clone(), state.inner(), copy_delay).await {
                        Ok(Some(text)) => {
                            let _ = app.emit("selection-captured", text);
                        }
                        Ok(None) => {
                            let _ = app.emit("selection-capture-empty", ());
                        }
                        Err(error) => {
                            let _ = app.emit("backend-error", error);
                        }
                    }
                });
            })
            .map_err(|error| error.to_string())?;
    }
    if !stop_hotkey.trim().is_empty() {
        shortcuts
            .on_shortcut(stop_hotkey.as_str(), move |app, _, event| {
                if event.state == ShortcutState::Pressed {
                    let _ = app.emit("shortcut-stop", ());
                }
            })
            .map_err(|error| error.to_string())?;
    }
    Ok(())
}

#[tauri::command]
fn autostart_status(app: AppHandle) -> Result<bool, String> {
    app.autolaunch()
        .is_enabled()
        .map_err(|error| error.to_string())
}

#[tauri::command]
fn set_autostart(app: AppHandle, enabled: bool) -> Result<(), String> {
    if enabled {
        app.autolaunch().enable().map_err(|error| error.to_string())
    } else {
        app.autolaunch()
            .disable()
            .map_err(|error| error.to_string())
    }
}

async fn clipboard_read(app: AppHandle) -> Result<String, String> {
    tauri::async_runtime::spawn_blocking(move || {
        app.clipboard()
            .read_text()
            .map_err(|error| error.to_string())
    })
    .await
    .map_err(|error| error.to_string())?
}

async fn clipboard_write(app: AppHandle, text: String) -> Result<(), String> {
    tauri::async_runtime::spawn_blocking(move || {
        app.clipboard()
            .write_text(text)
            .map_err(|error| error.to_string())
    })
    .await
    .map_err(|error| error.to_string())?
}

async fn capture_selection_text(
    app: AppHandle,
    backend: &BackendState,
    delay_ms: u64,
) -> Result<Option<String>, String> {
    let previous = clipboard_read(app.clone()).await.unwrap_or_default();
    let stamp = SystemTime::now()
        .duration_since(UNIX_EPOCH)
        .map(|duration| duration.as_nanos())
        .unwrap_or_default();
    let sentinel = format!("__VOXSELECT_{stamp}__");
    clipboard_write(app.clone(), sentinel.clone()).await?;
    if let Err(error) = backend.request("copy_selection", json!({})).await {
        let restore = clipboard_write(app, previous).await;
        return match restore {
            Ok(()) => Err(error),
            Err(restore_error) => Err(format!(
                "{error}; clipboard restore failed: {restore_error}"
            )),
        };
    }
    tokio::time::sleep(Duration::from_millis(delay_ms.min(3000))).await;
    let selected = clipboard_read(app.clone()).await;
    let restore = clipboard_write(app, previous).await;
    let selected = selected?;
    restore?;
    if selected.trim().is_empty() || selected == sentinel {
        Ok(None)
    } else {
        Ok(Some(selected))
    }
}

pub fn run() {
    let app = tauri::Builder::default()
        .plugin(tauri_plugin_shell::init())
        .plugin(tauri_plugin_clipboard_manager::init())
        .plugin(tauri_plugin_global_shortcut::Builder::new().build())
        .plugin(tauri_plugin_autostart::init(
            MacosLauncher::LaunchAgent,
            Some(vec![]),
        ))
        .manage(BackendState::default())
        .invoke_handler(tauri::generate_handler![
            backend_start,
            backend_restart,
            backend_shutdown,
            readio_configure,
            readio_capabilities,
            readio_synthesis_capabilities,
            readio_engines,
            readio_targets,
            readio_voices,
            readio_diagnostics,
            readio_speak,
            readio_stop,
            read_clipboard,
            capture_selection,
            configure_shortcuts,
            autostart_status,
            set_autostart,
        ])
        .setup(|app| {
            let open = MenuItem::with_id(app, "open", "Open VoxSelect", true, None::<&str>)?;
            let read_selection = MenuItem::with_id(
                app,
                "read_selection",
                "Read selected text",
                true,
                None::<&str>,
            )?;
            let read_clipboard =
                MenuItem::with_id(app, "read_clipboard", "Read clipboard", true, None::<&str>)?;
            let stop = MenuItem::with_id(app, "stop", "Stop speaking", true, None::<&str>)?;
            let quit = MenuItem::with_id(app, "quit", "Quit", true, None::<&str>)?;
            let menu = Menu::with_items(
                app,
                &[&open, &read_selection, &read_clipboard, &stop, &quit],
            )?;
            let mut tray = TrayIconBuilder::new()
                .menu(&menu)
                .tooltip("VoxSelect")
                .on_menu_event(|app, event| match event.id().as_ref() {
                    "open" => {
                        if let Some(window) = app.get_webview_window("main") {
                            let _ = window.show();
                            let _ = window.set_focus();
                        }
                        let _ = app.emit("tray-open", ());
                    }
                    "read_selection" => {
                        let _ = app.emit("tray-read-selection", ());
                    }
                    "read_clipboard" => {
                        let _ = app.emit("tray-read-clipboard", ());
                    }
                    "stop" => {
                        let _ = app.emit("tray-stop", ());
                    }
                    "quit" => app.exit(0),
                    _ => {}
                });
            if let Some(icon) = app.default_window_icon() {
                tray = tray.icon(icon.clone());
            }
            tray.build(app)?;

            if let Some(window) = app.get_webview_window("main") {
                let close_window = window.clone();
                window.on_window_event(move |event| {
                    if let tauri::WindowEvent::CloseRequested { api, .. } = event {
                        api.prevent_close();
                        let _ = close_window.hide();
                    }
                });
            }
            Ok(())
        })
        .build(tauri::generate_context!())
        .expect("failed to build VoxSelect");

    app.run(|app, event| {
        if matches!(event, tauri::RunEvent::Exit) {
            app.state::<BackendState>().stop();
        }
    });
}
