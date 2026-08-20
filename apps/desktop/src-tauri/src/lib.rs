pub mod local_service;
pub mod native_session;
pub mod offline_studio_bridge;
pub mod offline_sync_bridge;
pub mod recovery_bridge;
#[cfg(windows)]
pub mod screen_preferences;
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
#[cfg(feature = "contract-test")]
use zeroize::Zeroize;
use workspace_bridge::{
    notebook_context, notebook_create, notebook_get, notebook_list,
    workspace_ask_question, workspace_citation_content, workspace_create_report,
    workspace_apply_license, workspace_get_license,
    workspace_list_sources, workspace_list_studio_outputs, workspace_processing_status,
    workspace_upload_pdf,
};
#[cfg(windows)]
use screen_preferences::{screen_preferences_get, screen_preferences_reset, screen_preferences_save};

#[cfg(feature = "contract-test")]
struct ContractTestRuntimeBootstrap {
    runtime: NativeSessionRuntime,
    login_id: Option<String>,
    password: Option<String>,
}

#[cfg(feature = "contract-test")]
fn take_contract_test_env(name: &str) -> Option<String> {
    let value = std::env::var(name).ok();
    // SAFETY: contract-test bootstrap runs synchronously before the app starts worker threads.
    unsafe { std::env::remove_var(name) };
    value
}

#[cfg(feature = "contract-test")]
fn contract_test_runtime_from_env() -> Result<Option<ContractTestRuntimeBootstrap>, &'static str> {
    let mut gateway = take_contract_test_env("DAON_CONTRACT_TEST_GATEWAY");
    let mut credential_target = take_contract_test_env("DAON_CONTRACT_TEST_CREDENTIAL_TARGET");
    let login_id = take_contract_test_env("DAON_CONTRACT_TEST_LOGIN_ID");
    let password = take_contract_test_env("DAON_CONTRACT_TEST_PASSWORD");
    if gateway.is_none() && credential_target.is_none() {
        let config_path = std::env::temp_dir().join("daon-phase-e-native-contract.conf");
        if let Ok(config) = std::fs::read_to_string(config_path) {
            let mut lines = config.lines();
            gateway = lines.next().map(str::to_owned);
            credential_target = lines.next().map(str::to_owned);
            if lines.next().is_some() {
                return Err("CONTRACT_TEST_RUNTIME_INVALID");
            }
        }
    }
    let result = match (gateway.as_deref(), credential_target.as_deref()) {
        (None, None) => Ok(None),
        (Some(gateway), Some(credential_target)) => {
            let runtime = NativeSessionRuntime::for_loopback_contract_test(gateway, credential_target)
                .map_err(|_| "CONTRACT_TEST_RUNTIME_INVALID")?;
            let (login_id, password) = match (login_id, password) {
                (Some(login_id), Some(password)) => (Some(login_id), Some(password)),
                (None, None) => (None, None),
                (mut login_id, mut password) => {
                    if let Some(value) = &mut login_id { value.zeroize(); }
                    if let Some(value) = &mut password { value.zeroize(); }
                    (None, None)
                }
            };
            Ok(Some(ContractTestRuntimeBootstrap { runtime, login_id, password }))
        }
        _ => Err("CONTRACT_TEST_RUNTIME_INVALID"),
    };
    if let Some(value) = &mut gateway { value.zeroize(); }
    if let Some(value) = &mut credential_target { value.zeroize(); }
    result
}

#[cfg(feature = "contract-test")]
fn failed_contract_test_runtime() -> NativeSessionRuntime {
    let target = format!("DaonUser/NativeSession/contract-failed/{}", std::process::id());
    NativeSessionRuntime::for_loopback_contract_test("http://127.0.0.1:9", &target)
        .expect("CONTRACT_TEST_BOOTSTRAP_FAILED")
}

fn native_session_runtime() -> NativeSessionRuntime {
    #[cfg(feature = "contract-test")]
    {
        let mut bootstrap = match contract_test_runtime_from_env() {
            Ok(Some(bootstrap)) => bootstrap,
            Ok(None) => return NativeSessionRuntime::new(),
            Err(_) => return failed_contract_test_runtime(),
        };
        if let (Some(mut login_id), Some(mut password)) =
            (bootstrap.login_id.take(), bootstrap.password.take())
        {
            let result = tauri::async_runtime::block_on(
                bootstrap
                    .runtime
                    .login(std::mem::take(&mut login_id), std::mem::take(&mut password)),
            );
            login_id.zeroize();
            password.zeroize();
            if result.is_err() {
                let _safe_code = "CONTRACT_TEST_BOOTSTRAP_FAILED";
            }
        }
        return bootstrap.runtime;
    }
    #[cfg(not(feature = "contract-test"))]
    return NativeSessionRuntime::new();
}

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

#[cfg(feature = "webview-smoke")]
pub fn run() {
    tauri::Builder::default()
        .run(tauri::generate_context!())
        .expect("Daon WebView smoke를 시작할 수 없습니다");
}

#[cfg(not(feature = "webview-smoke"))]
#[cfg_attr(mobile, tauri::mobile_entry_point)]
pub fn run() {
    let session_runtime = native_session_runtime();
    tauri::Builder::default()
        .setup(move |app| {
            let manager = LocalServiceManager::new();
            manager.start();
            app.manage(manager);
            app.manage(session_runtime);
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
            screen_preferences_get,
            screen_preferences_save,
            screen_preferences_reset,
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
            notebook_list,
            notebook_create,
            notebook_get,
            notebook_context,
            workspace_list_sources,
            workspace_upload_pdf,
            workspace_processing_status,
            workspace_ask_question,
            workspace_citation_content,
            workspace_create_report,
            workspace_list_studio_outputs,
            workspace_get_license,
            workspace_apply_license
        ])
        .on_window_event(|window, event| {
            if matches!(event, tauri::WindowEvent::Destroyed) {
                window.state::<LocalServiceManager>().shutdown();
            }
        })
        .run(tauri::generate_context!())
        .expect("Daon 사용자 프로그램을 시작할 수 없습니다");
}
