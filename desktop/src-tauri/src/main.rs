// MIB Trader desktop host.
//
// Responsibilities:
//   1. Spawn the packaged Python backend as a sidecar — unless 8811 is
//      already serving (dev already ran run_server.py). Never two engines.
//   2. Watchdog only the child WE spawned.
//   3. System tray + hide-on-X. Quit only from tray "Exit MiB Trader".
//   4. Point the backend at the OS data folder.
//   5. Kill the sidecar on real quit. Do not kill a backend we did not start.

use std::net::{SocketAddr, TcpStream};
use std::sync::{Arc, Mutex};
use std::time::Duration;

use tauri::{
    menu::{Menu, MenuItem},
    tray::TrayIconBuilder,
    Manager, WindowEvent,
};
use tauri_plugin_opener::OpenerExt;
use tauri_plugin_shell::{process::CommandChild, process::CommandEvent, ShellExt};

struct BackendHandle {
    child: Arc<Mutex<Option<CommandChild>>>,
    spawned_by_us: Arc<Mutex<bool>>,
}

const RESTART_BACKOFF_SECS: u64 = 3;
const ENGINE_PORT: u16 = 8811;

fn engine_listening() -> bool {
    let addr = SocketAddr::from(([127, 0, 0, 1], ENGINE_PORT));
    TcpStream::connect_timeout(&addr, Duration::from_millis(250)).is_ok()
}

fn spawn_backend(app: &tauri::AppHandle) {
    if engine_listening() {
        log::info!(
            "[backend] already listening on 127.0.0.1:{} — not starting a second engine",
            ENGINE_PORT
        );
        let state = app.state::<BackendHandle>();
        *state.spawned_by_us.lock().unwrap() = false;
        return;
    }

    let data_dir = match app.path().app_data_dir() {
        Ok(dir) => dir,
        Err(err) => {
            log::error!("[backend] no app data dir: {err}");
            return;
        }
    };
    std::fs::create_dir_all(&data_dir).ok();

    let db_path = data_dir.join("market_data.db");

    let shell = app.shell();
    let command = match shell.sidecar("mib-backend") {
        Ok(cmd) => cmd
            .env("MARKET_DB_PATH", db_path.to_string_lossy().to_string())
            .env(
                "CORS_ORIGINS",
                "tauri://localhost,https://tauri.localhost,http://tauri.localhost,http://localhost:1420,http://localhost:3000",
            )
            .env("MIB_PORT", ENGINE_PORT.to_string()),
        Err(err) => {
            log::warn!(
                "[backend] sidecar not packaged ({err}). Start backend with: python run_server.py"
            );
            let state = app.state::<BackendHandle>();
            *state.spawned_by_us.lock().unwrap() = false;
            return;
        }
    };

    let (mut rx, child) = match command.spawn() {
        Ok(pair) => pair,
        Err(err) => {
            log::warn!(
                "[backend] could not spawn sidecar ({err}). Start backend with: python run_server.py"
            );
            let state = app.state::<BackendHandle>();
            *state.spawned_by_us.lock().unwrap() = false;
            return;
        }
    };

    let handle_state = app.state::<BackendHandle>();
    *handle_state.child.lock().unwrap() = Some(child);
    *handle_state.spawned_by_us.lock().unwrap() = true;

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
                    let state = app_handle.state::<BackendHandle>();
                    *state.child.lock().unwrap() = None;
                    *state.spawned_by_us.lock().unwrap() = false;

                    tokio::time::sleep(Duration::from_secs(RESTART_BACKOFF_SECS)).await;
                    spawn_backend(&app_handle);
                    break;
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
    let ours = *state.spawned_by_us.lock().unwrap();
    if !ours {
        log::info!("[backend] not killing — this host did not start the engine");
        return;
    }
    if let Some(child) = state.child.lock().unwrap().take() {
        let _ = child.kill();
    }
    *state.spawned_by_us.lock().unwrap() = false;
}

fn toggle_window(app: &tauri::AppHandle) {
    if let Some(w) = app.get_webview_window("main") {
        if w.is_visible().unwrap_or(true) {
            let _ = w.hide();
        } else {
            let _ = w.show();
            let _ = w.set_focus();
        }
    }
}

fn show_window(app: &tauri::AppHandle) {
    if let Some(w) = app.get_webview_window("main") {
        let _ = w.show();
        let _ = w.set_focus();
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
        .manage(BackendHandle {
            child: Arc::new(Mutex::new(None)),
            spawned_by_us: Arc::new(Mutex::new(false)),
        })
        .setup(|app| {
            let handle = app.handle().clone();
            spawn_backend(&handle);

            let open_item = MenuItem::with_id(app, "open", "Open MiB Trader", true, None::<&str>)?;
            let hide_item = MenuItem::with_id(app, "hide", "Show/Hide", true, None::<&str>)?;
            let engine_item =
                MenuItem::with_id(app, "engine_status", "Engine Status", true, None::<&str>)?;
            let mexc_item = MenuItem::with_id(
                app,
                "mexc_status",
                "MEXC Connection Status",
                true,
                None::<&str>,
            )?;
            let restart_item = MenuItem::with_id(
                app,
                "restart_backend",
                "Restart Trading Engine",
                true,
                None::<&str>,
            )?;
            let data_folder_item =
                MenuItem::with_id(app, "open_data_folder", "Show Data Folder", true, None::<&str>)?;
            let quit_item = MenuItem::with_id(app, "quit", "Exit MiB Trader", true, None::<&str>)?;
            let tray_menu = Menu::with_items(
                app,
                &[
                    &open_item,
                    &hide_item,
                    &engine_item,
                    &mexc_item,
                    &restart_item,
                    &data_folder_item,
                    &quit_item,
                ],
            )?;

            let mut tray = TrayIconBuilder::new()
                .tooltip("MIB Trader")
                .menu(&tray_menu)
                .show_menu_on_left_click(true)
                .on_menu_event(|app, event| match event.id.as_ref() {
                    "open" => show_window(app),
                    "hide" => toggle_window(app),
                    "engine_status" | "mexc_status" => {
                        show_window(app);
                    }
                    "restart_backend" => {
                        log::info!("Manual backend restart requested from tray");
                        kill_backend(app);
                        let handle = app.clone();
                        tauri::async_runtime::spawn(async move {
                            tokio::time::sleep(Duration::from_millis(500)).await;
                            spawn_backend(&handle);
                        });
                    }
                    "open_data_folder" => {
                        if let Ok(dir) = app.path().app_data_dir() {
                            let _ = app
                                .opener()
                                .open_path(dir.to_string_lossy().to_string(), None::<&str>);
                        }
                    }
                    "quit" => {
                        kill_backend(app);
                        app.exit(0);
                    }
                    _ => {}
                });

            if let Some(icon) = app.default_window_icon().cloned() {
                tray = tray.icon(icon);
            }

            let _tray = tray.build(app)?;

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
