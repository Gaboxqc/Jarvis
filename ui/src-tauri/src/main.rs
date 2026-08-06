// Kai desktop shell — REQ-1, REQ-32.
//
// Deliberately thin. The window hosts the same React UI that runs in a browser,
// which talks to the same FastAPI the CLI drives, so the desktop build cannot
// acquire capabilities the other two front ends lack.
//
// The shell owns exactly two things the web page cannot do for itself: a tray
// icon, and a global hotkey that reaches the assistant without changing the
// focused window (REQ-1).

#![cfg_attr(not(debug_assertions), windows_subsystem = "windows")]

use tauri::{
    menu::{Menu, MenuItem},
    tray::TrayIconBuilder,
    Manager, WindowEvent,
};
use tauri_plugin_global_shortcut::{
    Code, GlobalShortcutExt, Modifiers, Shortcut, ShortcutState,
};

/// Ctrl+Alt+K. Chosen to avoid colliding with anything Windows or common apps use.
///
/// A function rather than a `const`: `Shortcut::new` is not a const fn.
fn hotkey() -> Shortcut {
    Shortcut::new(Some(Modifiers::CONTROL | Modifiers::ALT), Code::KeyK)
}

fn toggle(app: &tauri::AppHandle) {
    if let Some(window) = app.get_webview_window("main") {
        // Toggle rather than always-show: the same key that summons it should
        // dismiss it, or the hotkey becomes one-way.
        let visible = window.is_visible().unwrap_or(false);
        let focused = window.is_focused().unwrap_or(false);
        if visible && focused {
            let _ = window.hide();
        } else {
            let _ = window.show();
            let _ = window.unminimize();
            let _ = window.set_focus();
        }
    }
}

fn main() {
    tauri::Builder::default()
        .plugin(
            tauri_plugin_global_shortcut::Builder::new()
                .with_handler(|app, shortcut, event| {
                    if shortcut == &hotkey() && event.state() == ShortcutState::Pressed {
                        toggle(app);
                    }
                })
                .build(),
        )
        .setup(|app| {
            app.global_shortcut().register(hotkey())?;

            let show = MenuItem::with_id(app, "show", "Show Kai", true, None::<&str>)?;
            let quit = MenuItem::with_id(app, "quit", "Quit", true, None::<&str>)?;
            let menu = Menu::with_items(app, &[&show, &quit])?;

            TrayIconBuilder::new()
                .icon(app.default_window_icon().unwrap().clone())
                .tooltip("Kai")
                .menu(&menu)
                .on_menu_event(|app, event| match event.id.as_ref() {
                    "show" => toggle(app),
                    "quit" => app.exit(0),
                    _ => {}
                })
                .build(app)?;

            Ok(())
        })
        .on_window_event(|window, event| {
            // Closing the window hides it instead of quitting: an assistant that
            // disappears the first time you hit the X is not always-available.
            if let WindowEvent::CloseRequested { api, .. } = event {
                api.prevent_close();
                let _ = window.hide();
            }
        })
        .run(tauri::generate_context!())
        .expect("error while running Kai");
}
