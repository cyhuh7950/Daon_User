use crate::local_service::LocalServiceManager;
use crate::native_session::NativeSessionRuntime;
use crate::offline_sync_bridge::OfflineSyncRuntime;
use serde::{Deserialize, Serialize};
use serde_json::Value;

#[derive(Clone, Debug, Deserialize, Serialize)]
#[serde(deny_unknown_fields)]
pub struct PrepareContextRequest {
    pub workspace_id: String,
    pub mode: String,
    pub daon_knowledge_ids: Vec<String>,
    pub raw_source_version_ids: Vec<String>,
    pub idempotency_key: String,
}

#[derive(Clone, Debug, Deserialize, Serialize)]
#[serde(deny_unknown_fields)]
pub struct ListModelsRequest {
    pub workspace_id: String,
}

#[derive(Clone, Debug, Deserialize, Serialize)]
#[serde(deny_unknown_fields)]
pub struct ListRawSourcesRequest {
    pub workspace_id: String,
}

#[derive(Clone, Debug, Deserialize, Serialize)]
#[serde(deny_unknown_fields)]
pub struct ImportRawSourceRequest {
    pub workspace_id: String,
    pub filename: String,
    pub content_type: String,
    pub content_base64: String,
    pub content_digest_sha256: String,
    pub idempotency_key: String,
}

#[derive(Clone, Debug, Deserialize, Serialize)]
#[serde(deny_unknown_fields)]
pub struct GetDraftRequest {
    pub workspace_id: String,
    pub draft_id: String,
}

#[derive(Clone, Debug, Deserialize, Serialize)]
#[serde(deny_unknown_fields)]
pub struct ConfirmSettingsRequest {
    pub workspace_id: String,
    pub title: String,
    pub purpose: String,
    pub temperature: f64,
    pub max_output_tokens: u32,
    pub context_snapshot_id: String,
    pub model_deployment_id: String,
    pub idempotency_key: String,
}

#[derive(Clone, Debug, Deserialize, Serialize)]
#[serde(deny_unknown_fields)]
pub struct GenerateDraftRequest {
    pub workspace_id: String,
    pub request_id: String,
    pub idempotency_key: String,
}

#[derive(Clone, Debug, Deserialize, Serialize)]
#[serde(deny_unknown_fields)]
pub struct EditSectionRequest {
    pub title: String,
    pub body: String,
    pub unverified: bool,
}

#[derive(Clone, Debug, Deserialize, Serialize)]
#[serde(deny_unknown_fields)]
pub struct AppendEditRequest {
    pub workspace_id: String,
    pub draft_id: String,
    pub previous_version_id: String,
    pub sections: Vec<EditSectionRequest>,
    pub idempotency_key: String,
}

#[derive(Clone, Debug, Deserialize, Serialize)]
#[serde(deny_unknown_fields)]
pub struct QueueSyncRequest {
    pub workspace_id: String,
    pub draft_id: String,
    pub output_version_id: String,
    pub source_dependency_ids: Vec<String>,
    pub idempotency_key: String,
}

#[derive(Serialize)]
struct AppendEditWire<'a> {
    workspace_id: &'a str,
    previous_version_id: &'a str,
    sections: &'a [EditSectionRequest],
    idempotency_key: &'a str,
}

#[derive(Serialize)]
struct QueueSyncWire<'a> {
    workspace_id: &'a str,
    output_version_id: &'a str,
    source_dependency_ids: &'a [String],
    idempotency_key: &'a str,
}

#[derive(Serialize)]
struct ConfirmSettingsWire<'a> {
    workspace_id: &'a str,
    title: &'a str,
    purpose: &'a str,
    temperature: f64,
    max_output_tokens: u32,
    context_snapshot_id: &'a str,
    model_deployment_id: &'a str,
    selection_actor_id: &'a str,
    idempotency_key: &'a str,
}

#[derive(Debug, Serialize)]
pub struct OfflineStudioBridgeError {
    pub error_code: &'static str,
}

#[derive(Deserialize)]
#[serde(deny_unknown_fields)]
struct StudioEnvelope {
    data: Value,
}

fn safe_id(value: &str) -> bool {
    !value.is_empty()
        && value.len() <= 256
        && value.bytes().enumerate().all(|(index, byte)| {
            byte.is_ascii_alphanumeric() || (index > 0 && matches!(byte, b'.' | b'_' | b':' | b'-'))
        })
}

fn safe_raw_source_request(request: &ImportRawSourceRequest) -> bool {
    safe_id(&request.workspace_id)
        && !request.filename.is_empty()
        && request.filename.len() <= 255
        && request.filename.trim() == request.filename
        && !request.filename.contains(['/', '\\', '\0'])
        && matches!(
            request.content_type.as_str(),
            "application/pdf" | "text/plain" | "text/markdown"
        )
        && !request.content_base64.is_empty()
        && request.content_base64.len() <= 35 * 1024 * 1024
        && request.content_base64.bytes().all(|value| {
            value.is_ascii_alphanumeric() || matches!(value, b'+' | b'/' | b'=')
        })
        && request.content_digest_sha256.len() == 64
        && request.content_digest_sha256.bytes().all(|value| value.is_ascii_hexdigit())
        && safe_id(&request.idempotency_key)
}

fn error(code: &'static str) -> OfflineStudioBridgeError {
    let error_code = match code {
        "LOCAL_COMMAND_NOT_ALLOWED"
        | "AUTHENTICATION_REQUIRED"
        | "LOCAL_WORKSPACE_FORBIDDEN"
        | "LOCAL_SERVICE_UNAVAILABLE"
        | "LOCAL_SERVICE_REQUEST_TIMEOUT"
        | "LOCAL_STUDIO_RESPONSE_REJECTED"
        | "LOCAL_STUDIO_INPUT_INVALID" => code,
        _ => "LOCAL_STUDIO_REQUEST_FAILED",
    };
    OfflineStudioBridgeError { error_code }
}

fn response_is_safe(value: &Value) -> bool {
    match value {
        Value::Object(fields) => fields.iter().all(|(key, value)| {
            !matches!(
                key.to_ascii_lowercase().as_str(),
                "path" | "endpoint" | "port" | "token" | "secret" | "root_secret"
            ) && response_is_safe(value)
        }),
        Value::Array(values) => values.iter().all(response_is_safe),
        Value::String(text) => {
            let lower = text.to_ascii_lowercase();
            !lower.contains("localhost")
                && !lower.contains("127.0.0.1")
                && !lower.contains("http://")
                && !lower.contains("https://")
        }
        _ => true,
    }
}

fn request_value<T: Serialize>(
    manager: &LocalServiceManager,
    workspace_id: &str,
    capability: &'static str,
    command: &'static str,
    method: &'static str,
    path: &str,
    request: Option<&T>,
) -> Result<Value, OfflineStudioBridgeError> {
    let body = match request {
        Some(request) => {
            serde_json::to_vec(request).map_err(|_| error("LOCAL_STUDIO_INPUT_INVALID"))?
        }
        None => Vec::new(),
    };
    let response = manager
        .execute_workspace_studio_request(
            workspace_id,
            capability,
            command,
            method,
            path,
            &body,
        )
        .map_err(error)?;
    if response.status != 200
        || response.content_type.as_deref() != Some("application/json")
        || response.content_length != Some(response.body.len())
        || response.body.len() > 2 * 1024 * 1024
    {
        return Err(error("LOCAL_STUDIO_RESPONSE_REJECTED"));
    }
    let envelope: StudioEnvelope = serde_json::from_slice(&response.body)
        .map_err(|_| error("LOCAL_STUDIO_RESPONSE_REJECTED"))?;
    if !response_is_safe(&envelope.data) {
        return Err(error("LOCAL_STUDIO_RESPONSE_REJECTED"));
    }
    Ok(envelope.data)
}

async fn current_identity(
    runtime: &NativeSessionRuntime,
) -> Result<(String, String), OfflineStudioBridgeError> {
    let status = runtime.status().await.map_err(|_| error("AUTHENTICATION_REQUIRED"))?;
    let value = serde_json::to_value(status).map_err(|_| error("AUTHENTICATION_REQUIRED"))?;
    if value.get("authenticated").and_then(Value::as_bool) != Some(true) {
        return Err(error("AUTHENTICATION_REQUIRED"));
    }
    let session = value
        .get("session")
        .and_then(Value::as_object)
        .ok_or_else(|| error("AUTHENTICATION_REQUIRED"))?;
    let workspace_id = session
        .get("workspace_id")
        .and_then(Value::as_str)
        .filter(|value| safe_id(value))
        .ok_or_else(|| error("AUTHENTICATION_REQUIRED"))?
        .to_owned();
    let actor_id = session
        .get("user_id")
        .and_then(Value::as_str)
        .filter(|value| safe_id(value))
        .ok_or_else(|| error("AUTHENTICATION_REQUIRED"))?
        .to_owned();
    Ok((workspace_id, actor_id))
}

fn require_workspace(
    requested: &str,
    current: &str,
) -> Result<(), OfflineStudioBridgeError> {
    if !safe_id(requested) || requested != current {
        return Err(error("LOCAL_WORKSPACE_FORBIDDEN"));
    }
    Ok(())
}

#[tauri::command]
pub async fn offline_studio_list_models(
    manager: tauri::State<'_, LocalServiceManager>,
    runtime: tauri::State<'_, NativeSessionRuntime>,
    sync_runtime: tauri::State<'_, OfflineSyncRuntime>,
    request: ListModelsRequest,
) -> Result<Value, OfflineStudioBridgeError> {
    let (workspace_id, _) = current_identity(&runtime).await?;
    require_workspace(&request.workspace_id, &workspace_id)?;
    let _ = sync_runtime
        .refresh_provider_settings(&runtime, &manager, &workspace_id)
        .await;
    request_value::<Value>(
        &manager,
        &workspace_id,
        "studio.read",
        "studio_models_list",
        "GET",
        "/local/v1/studio/models",
        None,
    )
}

#[tauri::command]
pub async fn offline_studio_list_raw_sources(
    manager: tauri::State<'_, LocalServiceManager>,
    runtime: tauri::State<'_, NativeSessionRuntime>,
    request: ListRawSourcesRequest,
) -> Result<Value, OfflineStudioBridgeError> {
    let (workspace_id, _) = current_identity(&runtime).await?;
    require_workspace(&request.workspace_id, &workspace_id)?;
    request_value::<Value>(
        &manager,
        &workspace_id,
        "studio.read",
        "studio_raw_sources_list",
        "GET",
        "/local/v1/studio/raw-sources",
        None,
    )
}

#[tauri::command]
pub async fn offline_studio_import_raw_source(
    manager: tauri::State<'_, LocalServiceManager>,
    runtime: tauri::State<'_, NativeSessionRuntime>,
    request: ImportRawSourceRequest,
) -> Result<Value, OfflineStudioBridgeError> {
    let (workspace_id, _) = current_identity(&runtime).await?;
    require_workspace(&request.workspace_id, &workspace_id)?;
    if !safe_raw_source_request(&request) {
        return Err(error("LOCAL_STUDIO_INPUT_INVALID"));
    }
    request_value(
        &manager,
        &workspace_id,
        "studio.write",
        "studio_raw_source_import",
        "POST",
        "/local/v1/studio/raw-sources",
        Some(&request),
    )
}

#[tauri::command]
pub async fn offline_studio_prepare_context(
    manager: tauri::State<'_, LocalServiceManager>,
    runtime: tauri::State<'_, NativeSessionRuntime>,
    request: PrepareContextRequest,
) -> Result<Value, OfflineStudioBridgeError> {
    let (workspace_id, _) = current_identity(&runtime).await?;
    require_workspace(&request.workspace_id, &workspace_id)?;
    request_value(
        &manager,
        &workspace_id,
        "studio.write",
        "studio_context_prepare",
        "POST",
        "/local/v1/studio/knowledge-contexts",
        Some(&request),
    )
}

#[tauri::command]
pub async fn offline_studio_confirm_settings(
    manager: tauri::State<'_, LocalServiceManager>,
    runtime: tauri::State<'_, NativeSessionRuntime>,
    request: ConfirmSettingsRequest,
) -> Result<Value, OfflineStudioBridgeError> {
    let (workspace_id, actor_id) = current_identity(&runtime).await?;
    require_workspace(&request.workspace_id, &workspace_id)?;
    let wire = ConfirmSettingsWire {
        workspace_id: &request.workspace_id,
        title: &request.title,
        purpose: &request.purpose,
        temperature: request.temperature,
        max_output_tokens: request.max_output_tokens,
        context_snapshot_id: &request.context_snapshot_id,
        model_deployment_id: &request.model_deployment_id,
        selection_actor_id: &actor_id,
        idempotency_key: &request.idempotency_key,
    };
    request_value(
        &manager,
        &workspace_id,
        "studio.write",
        "studio_settings_confirm",
        "POST",
        "/local/v1/studio/settings/confirm",
        Some(&wire),
    )
}

#[tauri::command]
pub async fn offline_studio_generate_draft(
    manager: tauri::State<'_, LocalServiceManager>,
    runtime: tauri::State<'_, NativeSessionRuntime>,
    request: GenerateDraftRequest,
) -> Result<Value, OfflineStudioBridgeError> {
    let (workspace_id, _) = current_identity(&runtime).await?;
    require_workspace(&request.workspace_id, &workspace_id)?;
    request_value(
        &manager,
        &workspace_id,
        "studio.write",
        "studio_draft_generate",
        "POST",
        "/local/v1/studio/drafts/generate",
        Some(&request),
    )
}

#[tauri::command]
pub async fn offline_studio_get_draft(
    manager: tauri::State<'_, LocalServiceManager>,
    runtime: tauri::State<'_, NativeSessionRuntime>,
    request: GetDraftRequest,
) -> Result<Value, OfflineStudioBridgeError> {
    let (workspace_id, _) = current_identity(&runtime).await?;
    require_workspace(&request.workspace_id, &workspace_id)?;
    if !safe_id(&request.draft_id) {
        return Err(error("LOCAL_STUDIO_INPUT_INVALID"));
    }
    request_value::<Value>(
        &manager,
        &workspace_id,
        "studio.read",
        "studio_draft_get",
        "GET",
        &format!("/local/v1/studio/drafts/{}", request.draft_id),
        None,
    )
}

#[tauri::command]
pub async fn offline_studio_append_edit(
    manager: tauri::State<'_, LocalServiceManager>,
    runtime: tauri::State<'_, NativeSessionRuntime>,
    request: AppendEditRequest,
) -> Result<Value, OfflineStudioBridgeError> {
    let (workspace_id, _) = current_identity(&runtime).await?;
    require_workspace(&request.workspace_id, &workspace_id)?;
    if !safe_id(&request.draft_id) {
        return Err(error("LOCAL_STUDIO_INPUT_INVALID"));
    }
    let path = format!("/local/v1/studio/drafts/{}/versions", request.draft_id);
    let wire = AppendEditWire {
        workspace_id: &request.workspace_id,
        previous_version_id: &request.previous_version_id,
        sections: &request.sections,
        idempotency_key: &request.idempotency_key,
    };
    request_value(
        &manager,
        &workspace_id,
        "studio.write",
        "studio_draft_append_version",
        "POST",
        &path,
        Some(&wire),
    )
}

#[tauri::command]
pub async fn offline_studio_queue_sync(
    manager: tauri::State<'_, LocalServiceManager>,
    runtime: tauri::State<'_, NativeSessionRuntime>,
    request: QueueSyncRequest,
) -> Result<Value, OfflineStudioBridgeError> {
    let (workspace_id, _) = current_identity(&runtime).await?;
    require_workspace(&request.workspace_id, &workspace_id)?;
    if !safe_id(&request.draft_id) {
        return Err(error("LOCAL_STUDIO_INPUT_INVALID"));
    }
    let path = format!("/local/v1/studio/drafts/{}/sync-queue", request.draft_id);
    let wire = QueueSyncWire {
        workspace_id: &request.workspace_id,
        output_version_id: &request.output_version_id,
        source_dependency_ids: &request.source_dependency_ids,
        idempotency_key: &request.idempotency_key,
    };
    request_value(
        &manager,
        &workspace_id,
        "studio.write",
        "studio_sync_queue",
        "POST",
        &path,
        Some(&wire),
    )
}
