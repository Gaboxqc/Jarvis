// Kai desktop shell — REQ-1, REQ-29, REQ-32.
//
// Deliberately thin. The window hosts the same React UI that runs in a browser,
// talking to the same FastAPI the CLI drives, so the desktop build cannot
// acquire capabilities the other two front ends lack.
//
// The shell owns three things the web page cannot do for itself: a tray icon, a
// global hotkey that reaches the assistant without changing the focused window,
// and the lifetime of the bundled backend process.

#![cfg_attr(not(debug_assertions), windows_subsystem = "windows")]

use std::path::PathBuf;
use std::sync::Mutex;

use tauri::{
    menu::{Menu, MenuItem},
    tray::TrayIconBuilder,
    Manager, RunEvent, WindowEvent,
};
use tauri_plugin_global_shortcut::{
    Code, GlobalShortcutExt, Modifiers, Shortcut, ShortcutState,
};
use tauri_plugin_shell::process::{CommandChild, CommandEvent};
use tauri_plugin_shell::ShellExt;

/// Ctrl+Alt+K. Chosen to avoid colliding with anything Windows or common apps use.
///
/// A function rather than a `const`: `Shortcut::new` is not a const fn.
fn hotkey() -> Shortcut {
    Shortcut::new(Some(Modifiers::CONTROL | Modifiers::ALT), Code::KeyK)
}

/// The spawned backend, kept so it can be shut down with the app.
#[derive(Default)]
struct Backend(Mutex<Option<CommandChild>>);

/// Where the backend writes its API token.
///
/// Mirrors `data_dir()` in backend/app/settings.py. Duplicated rather than
/// asked for, because the one process that could answer the question is the one
/// this is trying to authenticate against.
fn token_path() -> PathBuf {
    if let Ok(override_dir) = std::env::var("KAI_DATA_DIR") {
        return PathBuf::from(override_dir).join("api-token");
    }
    let base = std::env::var("LOCALAPPDATA").unwrap_or_default();
    PathBuf::from(base).join("Kai").join("api-token")
}

/// Hand the webview the token it needs to talk to the backend.
///
/// The API refuses unauthenticated calls, because loopback and CORS are not
/// access control -- a form on any web page can POST to 127.0.0.1 with no
/// preflight, and CORS only hides the reply. See backend/app/security.py.
///
/// Retried rather than read once. The webview loads from a bundled directory
/// and is ready almost immediately; the backend is a PyInstaller bundle that
/// takes seconds to unpack before it writes the file. Asking once would mean a
/// UI that came up first never authenticated at all.
///
/// `async` plus `spawn_blocking` because of where the waiting happens: a
/// synchronous Tauri command runs on the main thread, so the retry loop would
/// freeze the window for as long as it ran -- during startup, which is exactly
/// when a frozen window reads as a broken app.
#[tauri::command]
async fn api_token() -> Result<String, String> {
    tauri::async_runtime::spawn_blocking(|| {
        let path = token_path();
        for _ in 0..100 {
            if let Ok(contents) = std::fs::read_to_string(&path) {
                let token = contents.trim();
                if !token.is_empty() {
                    return Ok(token.to_string());
                }
            }
            std::thread::sleep(std::time::Duration::from_millis(100));
        }
        Err(format!("no API token at {} after 10s", path.display()))
    })
    .await
    .map_err(|error| format!("could not read the API token: {error}"))?
}

/// Open the folder the backend writes its log to.
///
/// A command of its own rather than `shell:allow-open` in the capability file.
/// That permission would let the webview ask the shell to open anything at all,
/// which is exactly the kind of ability capabilities/default.json says it does
/// not want to hand out -- "the assistant's capabilities live behind the Action
/// Gate in the backend, and anything granted here would sit outside it". This
/// opens one directory, computed here, and takes no argument that could point it
/// somewhere else.
/// Explorer directly, rather than the shell plugin's `open`, which is deprecated
/// in favour of another plugin. One button is not worth a dependency, and this
/// spawns one named program with a path this function computed -- nothing the
/// caller supplies reaches it.
#[tauri::command]
async fn open_log_folder() -> Result<String, String> {
    let directory = token_path()
        .parent()
        .ok_or("no data directory")?
        .join("logs");
    if !directory.is_dir() {
        return Err(format!("no logs at {}", directory.display()));
    }
    // Not `.status()`: explorer.exe returns 1 on success as often as not, so
    // waiting on the exit code would report every successful open as a failure.
    std::process::Command::new("explorer.exe")
        .arg(&directory)
        .spawn()
        .map_err(|error| format!("could not open {}: {error}", directory.display()))?;
    Ok(directory.to_string_lossy().to_string())
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

/// Start the bundled backend.
///
/// In development there is usually one already running from a terminal, and a
/// second copy would just lose the port race and exit; the sidecar is only
/// spawned in release builds, where it is the only copy there is.
fn start_backend(app: &tauri::AppHandle) {
    if cfg!(debug_assertions) {
        println!("dev build: expecting a backend on 127.0.0.1:8756");
        return;
    }

    // The backend ships as a resource directory, not an externalBin: PyInstaller
    // produces an executable that needs its _internal folder as a sibling, and
    // externalBin carries only one file.
    let executable = match app.path().resolve(
        "resources/kai-backend/kai-backend.exe",
        tauri::path::BaseDirectory::Resource,
    ) {
        Ok(path) if path.exists() => path,
        Ok(path) => {
            eprintln!("backend missing at {}", path.display());
            return;
        }
        Err(error) => {
            eprintln!("could not locate the backend: {error}");
            return;
        }
    };

    // Arms the backend's own watchdog. stop_backend below covers every exit this
    // process can see; it cannot cover the ones where it never runs -- a crash,
    // a kill, a policy stopping the binary. The sidecar then outlives the app,
    // keeps the port, and holds its DLLs open so the next installer cannot
    // overwrite them. With this set it notices the closed stdin and exits by
    // itself.
    match app
        .shell()
        .command(executable)
        .env("KAI_PARENT_WATCH", "1")
        .spawn()
    {
        Ok((mut rx, child)) => {
            app.state::<Backend>().0.lock().unwrap().replace(child);
            // Drain the pipes. Left unread they fill and block the child.
            tauri::async_runtime::spawn(async move {
                while let Some(event) = rx.recv().await {
                    if let CommandEvent::Stderr(line) = event {
                        eprintln!("[backend] {}", String::from_utf8_lossy(&line));
                    }
                }
            });
        }
        Err(error) => eprintln!("backend failed to start: {error}"),
    }
}

/// Stop the backend we started.
///
/// Without this the sidecar outlives the window and keeps the port, so the next
/// launch finds 8756 taken and refuses to start — by a process the user cannot
/// see and would have no reason to look for.
fn stop_backend(app: &tauri::AppHandle) {
    if let Some(child) = app.state::<Backend>().0.lock().unwrap().take() {
        let _ = child.kill();
    }
}

fn main() {
    tauri::Builder::default()
        .manage(Backend::default())
        .invoke_handler(tauri::generate_handler![api_token, open_log_folder])
        .plugin(tauri_plugin_shell::init())
        // A reminder that only appears inside a visible window is a reminder
        // you miss by having the window hidden -- which is the normal state for
        // an assistant that lives in the tray.
        .plugin(tauri_plugin_notification::init())
        // Checking is a deliberate act, not something that happens on launch.
        // The plugin only exposes the capability; nothing here reaches out to
        // GitHub until the user presses the button in Settings.
        .plugin(tauri_plugin_updater::Builder::new().build())
        // Start with Windows, so an assistant that lives in the tray is
        // actually there after a reboot rather than waiting to be remembered.
        // Registered but not enabled: the plugin only writes the run key when
        // the user asks for it in Settings.
        .plugin(tauri_plugin_autostart::init(
            tauri_plugin_autostart::MacosLauncher::LaunchAgent,
            None,
        ))
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
            start_backend(app.handle());

            let show = MenuItem::with_id(app, "show", "Show Kai", true, None::<&str>)?;
            let quit = MenuItem::with_id(app, "quit", "Quit", true, None::<&str>)?;
            let menu = Menu::with_items(app, &[&show, &quit])?;

            TrayIconBuilder::new()
                .icon(app.default_window_icon().unwrap().clone())
                .tooltip("Kai")
                .menu(&menu)
                .on_menu_event(|app, event| match event.id.as_ref() {
                    "show" => toggle(app),
                    "quit" => {
                        stop_backend(app);
                        app.exit(0);
                    }
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
        .build(tauri::generate_context!())
        .expect("error while building Kai")
        .run(|app, event| {
            // Covers every exit path, including ones that never reach the tray
            // menu — a logout, a taskbar close, a crash in the webview.
            if let RunEvent::ExitRequested { .. } | RunEvent::Exit = event {
                stop_backend(app);
            }
        });
}
