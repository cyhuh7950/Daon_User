pub mod local_service;
pub mod native_session;
#[cfg(windows)]
pub mod windows_credential;

use local_service::{LocalServiceManager, LocalServiceState};
use native_session::{NativeSessionError, NativeSessionRuntime, NativeSessionStatus};
use tauri::Manager;

#[tauri::command]
fn local_service_status(manager: tauri::State<'_, LocalServiceManager>) -> LocalServiceState {
    manager.status()
}

#[tauri::command]
fn local_service_retry(manager: tauri::State<'_, LocalServiceManager>) -> LocalServiceState {
    manager.retry()
}

#[tauri::command]
async fn native_login(
    login_id: String,
    password: String,
    runtime: tauri::State<'_, NativeSessionRuntime>,
) -> Result<NativeSessionStatus, NativeSessionError> {
    runtime.login(login_id, password).await
}

#[tauri::command]
fn native_logout(
    runtime: tauri::State<'_, NativeSessionRuntime>,
) -> Result<NativeSessionStatus, NativeSessionError> {
    runtime.logout()
}

#[tauri::command]
fn native_session_status(
    runtime: tauri::State<'_, NativeSessionRuntime>,
) -> Result<NativeSessionStatus, NativeSessionError> {
    runtime.status()
}

#[cfg_attr(mobile, tauri::mobile_entry_point)]
pub fn run() {
    tauri::Builder::default()
        .setup(|app| {
            let manager = LocalServiceManager::new();
            manager.start();
            app.manage(manager);
            app.manage(NativeSessionRuntime::new());
            Ok(())
        })
        .invoke_handler(tauri::generate_handler![
            local_service_status,
            local_service_retry,
            native_login,
            native_logout,
            native_session_status
        ])
        .on_window_event(|window, event| {
            if matches!(event, tauri::WindowEvent::Destroyed) {
                window.state::<LocalServiceManager>().shutdown();
            }
        })
        .run(tauri::generate_context!())
        .expect("Daon 사용자 프로그램을 시작할 수 없습니다");
}
