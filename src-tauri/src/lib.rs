use tauri::{Emitter, Manager};
use tauri::menu::{Menu, MenuItem};
use tauri::tray::TrayIconBuilder;
use tauri::WindowEvent;
use tauri_plugin_autostart::MacosLauncher;

pub fn run() {
    tauri::Builder::default()
        .plugin(tauri_plugin_shell::init())
        .plugin(tauri_plugin_clipboard_manager::init())
        .plugin(tauri_plugin_global_shortcut::Builder::new().build())
        .plugin(tauri_plugin_autostart::init(
            MacosLauncher::LaunchAgent,
            Some(vec![]),
        ))
        .setup(|app| {
            let open = MenuItem::with_id(app, "open", "Open VoxSelect", true, None::<&str>)?;
            let read_selection = MenuItem::with_id(
                app,
                "read_selection",
                "Read selected text",
                true,
                None::<&str>,
            )?;
            let read_clipboard = MenuItem::with_id(
                app,
                "read_clipboard",
                "Read clipboard",
                true,
                None::<&str>,
            )?;
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
                    if let WindowEvent::CloseRequested { api, .. } = event {
                        api.prevent_close();
                        let _ = close_window.hide();
                    }
                });
            }
            Ok(())
        })
        .run(tauri::generate_context!())
        .expect("error while running VoxSelect");
}
