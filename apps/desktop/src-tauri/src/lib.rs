pub mod local_service;
pub mod native_session;
pub mod offline_studio_bridge;
pub mod offline_sync_bridge;
pub mod recovery_bridge;
#[cfg(windows)]
pub mod windows_credential;
pub mod workspace_bridge;

use local_service::{LocalServiceManager, LocalServiceState};
use native_session::{
    NativeRecoveryAuthorizationStatus, NativeSessionError, NativeSessionRuntime,
    NativeSessionStatus,
};
use offline_studio_bridge::{
    offline_studio_append_edit, offline_studio_confirm_settings, offline_studio_generate_draft,
    offline_studio_get_draft, offline_studio_import_raw_source, offline_studio_list_models,
    offline_studio_list_raw_sources, offline_studio_prepare_context, offline_studio_queue_sync,
};
use offline_sync_bridge::{
    OfflineSyncRuntime, offline_knowledge_list, offline_knowledge_provision,
    offline_knowledge_refresh, offline_sync_approve, offline_sync_preview,
    offline_sync_resolve, offline_sync_status, offline_sync_transfer,
};
use recovery_bridge::{
    NativeRecoveryRuntime, recovery_cloud_cancel_restore, recovery_cloud_create_backup,
    recovery_cloud_execute_restore, recovery_cloud_get_backup, recovery_cloud_get_restore,
    recovery_cloud_list_backups, recovery_cloud_preview_restore, recovery_local_get_job,
    recovery_local_repair_job, recovery_local_start_scan,
};
use tauri::Manager;
use workspace_bridge::{
    workspace_ask_question, workspace_citation_content, workspace_create_report,
    workspace_list_sources, workspace_list_studio_outputs, workspace_processing_status,
    workspace_upload_pdf,
};

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
async fn native_logout(
    runtime: tauri::State<'_, NativeSessionRuntime>,
) -> Result<NativeSessionStatus, NativeSessionError> {
    runtime.logout().await
}

#[tauri::command]
async fn native_session_status(
    runtime: tauri::State<'_, NativeSessionRuntime>,
) -> Result<NativeSessionStatus, NativeSessionError> {
    runtime.status().await
}

#[tauri::command]
async fn native_recovery_authorization_status(
    runtime: tauri::State<'_, NativeSessionRuntime>,
) -> Result<NativeRecoveryAuthorizationStatus, NativeSessionError> {
    runtime.recovery_authorization_status().await
}

#[cfg_attr(mobile, tauri::mobile_entry_point)]
pub fn run() {
    tauri::Builder::default()
        .setup(|app| {
            let manager = LocalServiceManager::new();
            manager.start();
            app.manage(manager);
            app.manage(NativeSessionRuntime::new());
            app.manage(NativeRecoveryRuntime::new());
            app.manage(OfflineSyncRuntime::new());
            Ok(())
        })
        .invoke_handler(tauri::generate_handler![
            local_service_status,
            local_service_retry,
            native_login,
            native_logout,
            native_session_status,
            native_recovery_authorization_status,
            offline_studio_list_models,
            offline_studio_list_raw_sources,
            offline_studio_import_raw_source,
            offline_studio_prepare_context,
            offline_studio_confirm_settings,
            offline_studio_generate_draft,
            offline_studio_get_draft,
            offline_studio_append_edit,
            offline_studio_queue_sync,
            offline_knowledge_list,
            offline_knowledge_provision,
            offline_knowledge_refresh,
            offline_sync_preview,
            offline_sync_status,
            offline_sync_approve,
            offline_sync_transfer,
            offline_sync_resolve,
            recovery_cloud_create_backup,
            recovery_cloud_list_backups,
            recovery_cloud_get_backup,
            recovery_cloud_preview_restore,
            recovery_cloud_get_restore,
            recovery_cloud_execute_restore,
            recovery_cloud_cancel_restore,
            recovery_local_start_scan,
            recovery_local_get_job,
            recovery_local_repair_job,
            workspace_list_sources,
            workspace_upload_pdf,
            workspace_processing_status,
            workspace_ask_question,
            workspace_citation_content,
            workspace_create_report,
            workspace_list_studio_outputs
        ])
        .on_window_event(|window, event| {
            if matches!(event, tauri::WindowEvent::Destroyed) {
                window.state::<LocalServiceManager>().shutdown();
            }
        })
        .run(tauri::generate_context!())
        .expect("Daon 사용자 프로그램을 시작할 수 없습니다");
}
