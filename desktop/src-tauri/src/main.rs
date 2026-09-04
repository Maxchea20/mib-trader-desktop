// MIB Trader desktop host.
//
// Responsibilities (per the desktop-packaging plan):
//   1. Spawn the packaged Python backend as a sidecar process.
//   2. Watchdog it: if it crashes, respawn automatically (with a short
//      backoff so a fast-crash-loop doesn't spin the CPU).
//   3. System tray + "minimize to tray" instead of quitting on window
//      close, so an accidental click doesn't kill an open live position's
//      local journal/UI (the exchange-side SL/TP brackets keep working
//      regardless, but you still want the app itself to stay running).
//   4. Point the backend at a real OS data folder (not a temp extraction
//      dir) so the SQLite journal survives restarts.
//   5. Explicitly kill the sidecar child when the app actually quits —
//      an orphaned backend process left running in the background is a
//      classic way to end up with two auto-traders fighting over the
//      same account.
//
// NOT yet wired here: pulling MEXC_API_KEY/MEXC_API_SECRET from a
// Settings screen and writing them to <app_data_dir>/.env before spawn.
// For now, set them as real OS environment variables before launching
// the app, or drop a `.env` file into the app data folder shown in the
// tray menu ("Show Data Folder") — run_server.py already reads it from
// there if present.

use std::sync::{Arc, Mutex};
use std::time::Duration;

use tauri::{
    menu::{Menu, MenuItem},
    tray::TrayIconBuilder,
    Manager, WindowEvent,
};
use tauri_plugin_opener::OpenerExt;
use tauri_plugin_shell::{process::CommandChild, process::CommandEvent, ShellExt};

struct BackendHandle(Arc<Mutex<Option<CommandChild>>>);

const RESTART_BACKOFF_SECS: u64 = 3;

fn spawn_backend(app: &tauri::AppHandle) {
    let data_dir = app
        .path()
        .app_data_dir()
        .expect("could not resolve app data dir");
    std::fs::create_dir_all(&data_dir).ok();

    let db_path = data_dir.join("market_data.db");

    let shell = app.shell();
    let command = shell
        .sidecar("mib-backend")
        .expect("mib-backend sidecar not found — did you run build_sidecar and place the binary in src-tauri/binaries/?")
        .env("MARKET_DB_PATH", db_path.to_string_lossy().to_string())
        .env("CORS_ORIGINS", "tauri://localhost,http://localhost:1420")
        .env("MIB_PORT", "8811");

    let (mut rx, child) = command.spawn().expect("failed to spawn backend sidecar");

    let handle_state = app.state::<BackendHandle>();
    *handle_state.0.lock().unwrap() = Some(child);

    let app_handle = app.clone();
    tauri::async_runtime::spawn(async move {
        while let Some(event) = rx.recv().await {
            match event {
                CommandEvent::Stdout(line) => {
                    log::info!("[backend] {}", String::from_utf8_lossy(&line));
                }
                CommandEvent::Stderr(line) => {
                    log::warn!("[backend] {}", String::from_utf8_lossy(&line));
                }
                CommandEvent::Terminated(payload) => {
                    log::error!(
                        "[backend] exited (code={:?}) — restarting in {}s",
                        payload.code,
                        RESTART_BACKOFF_SECS
                    );
                    // Clear the dead handle so we don't try to kill a
                    // process that's already gone on shutdown.
                    let state = app_handle.state::<BackendHandle>();
                    *state.0.lock().unwrap() = None;

                    tokio::time::sleep(Duration::from_secs(RESTART_BACKOFF_SECS)).await;
                    spawn_backend(&app_handle);
                    break; // this task's job is done; the respawn starts a new one
                }
                CommandEvent::Error(err) => {
                    log::error!("[backend] sidecar error: {}", err);
                }
                _ => {}
            }
        }
    });
}

fn kill_backend(app: &tauri::AppHandle) {
    let state = app.state::<BackendHandle>();
    if let Some(child) = state.0.lock().unwrap().take() {
        let _ = child.kill();
    }
}

fn main() {
    tauri::Builder::default()
        .plugin(tauri_plugin_shell::init())
        .plugin(tauri_plugin_opener::init())
        .plugin(tauri_plugin_autostart::init(
            tauri_plugin_autostart::MacosLauncher::LaunchAgent,
            None,
        ))
        .manage(BackendHandle(Arc::new(Mutex::new(None))))
        .setup(|app| {
            let handle = app.handle().clone();
            spawn_backend(&handle);

            // --- System tray -------------------------------------------------
            let show_item = MenuItem::with_id(app, "show", "Show MIB Trader", true, None::<&str>)?;
            let restart_item =
                MenuItem::with_id(app, "restart_backend", "Restart Trading Engine", true, None::<&str>)?;
            let data_folder_item =
                MenuItem::with_id(app, "open_data_folder", "Show Data Folder", true, None::<&str>)?;
            let quit_item = MenuItem::with_id(app, "quit", "Quit", true, None::<&str>)?;
            let tray_menu = Menu::with_items(
                app,
                &[&show_item, &restart_item, &data_folder_item, &quit_item],
            )?;

            let _tray = TrayIconBuilder::new()
                .menu(&tray_menu)
                .show_menu_on_left_click(true)
                .on_menu_event(|app, event| match event.id.as_ref() {
                    "show" => {
                        if let Some(w) = app.get_webview_window("main") {
                            let _ = w.show();
                            let _ = w.set_focus();
                        }
                    }
                    "restart_backend" => {
                        log::info!("Manual backend restart requested from tray");
                        kill_backend(app);
                        let handle = app.clone();
                        // give the OS a moment to release the port before respawn
                        tauri::async_runtime::spawn(async move {
                            tokio::time::sleep(Duration::from_millis(500)).await;
                            spawn_backend(&handle);
                        });
                    }
                    "open_data_folder" => {
                        if let Ok(dir) = app.path().app_data_dir() {
                            let _ = app.opener().open_path(dir.to_string_lossy().to_string(), None::<&str>);
                        }
                    }
                    "quit" => {
                        kill_backend(app);
                        app.exit(0);
                    }
                    _ => {}
                })
                .build(app)?;

            // Minimize to tray instead of closing the process, so a stray
            // click on the window's X button doesn't kill an unattended
            // trading engine.
            if let Some(window) = app.get_webview_window("main") {
                let window_clone = window.clone();
                window.on_window_event(move |event| {
                    if let WindowEvent::CloseRequested { api, .. } = event {
                        api.prevent_close();
                        let _ = window_clone.hide();
                    }
                });
            }

            Ok(())
        })
        .run(tauri::generate_context!())
        .expect("error while running MIB Trader desktop app");
}
