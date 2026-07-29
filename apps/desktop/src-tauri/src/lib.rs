pub mod local_service;
#[cfg(windows)]
pub mod windows_credential;

use local_service::{LocalServiceManager, LocalServiceState};
use tauri::Manager;

#[tauri::command]
fn local_service_status(manager: tauri::State<'_, LocalServiceManager>) -> LocalServiceState {
    manager.status()
}

#[tauri::command]
fn local_service_retry(manager: tauri::State<'_, LocalServiceManager>) -> LocalServiceState {
    manager.retry()
}

#[cfg_attr(mobile, tauri::mobile_entry_point)]
pub fn run() {
    tauri::Builder::default()
        .setup(|app| {
            let manager = LocalServiceManager::new();
            manager.start();
            app.manage(manager);
            Ok(())
        })
        .invoke_handler(tauri::generate_handler![
            local_service_status,
            local_service_retry
        ])
        .on_window_event(|window, event| {
            if matches!(event, tauri::WindowEvent::Destroyed) {
                window.state::<LocalServiceManager>().shutdown();
            }
        })
        .run(tauri::generate_context!())
        .expect("Daon 사용자 프로그램을 시작할 수 없습니다");
}
