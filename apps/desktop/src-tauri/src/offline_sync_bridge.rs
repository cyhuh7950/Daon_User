use crate::local_service::LocalServiceManager;
use crate::native_session::{NativeSessionRuntime, PUBLIC_GATEWAY};
use serde::{Deserialize, Serialize};
use serde_json::Value;
use sha2::{Digest, Sha256};
use std::collections::{HashMap, HashSet};
use std::future::Future;
use std::pin::Pin;
use std::sync::{Arc, Mutex};
use zeroize::{Zeroize, Zeroizing};

const JSON_RESPONSE_MAX: usize = 2 * 1024 * 1024;
const KNOWLEDGE_CONTENT_MAX: usize = 12 * 1024 * 1024;

#[derive(Clone, Copy, Debug, Deserialize, PartialEq, Eq, Serialize)]
#[serde(rename_all = "snake_case")]
pub enum CopyState {
    Available,
    Approved,
    Revoked,
    Expired,
}

impl CopyState {
    pub fn parse(value: &str) -> Result<Self, OfflineSyncError> {
        match value {
            "available" => Ok(Self::Available),
            "approved" => Ok(Self::Approved),
            "revoked" => Ok(Self::Revoked),
            "expired" => Ok(Self::Expired),
            _ => Err(OfflineSyncError::ResponseRejected),
        }
    }
}

#[derive(Clone, Copy, Debug, Deserialize, PartialEq, Eq, Serialize)]
#[serde(rename_all = "snake_case")]
pub enum SyncState {
    Draft,
    AwaitingApproval,
    Approved,
    Transferring,
    Conflict,
    ReindexRequested,
}

#[derive(Clone, Copy, Debug, PartialEq, Eq)]
pub enum OfflineSyncError {
    InputInvalid,
    ApprovalRequired,
    StepUpExpired,
    StepUpRevoked,
    ScopeChanged,
    MissingDependency,
    DependencyCycle,
    ConflictRequiresResolution,
    ResponseRejected,
}

impl OfflineSyncError {
    pub fn code(self) -> &'static str {
        match self {
            Self::InputInvalid => "OFFLINE_SYNC_INPUT_INVALID",
            Self::ApprovalRequired => "OFFLINE_SYNC_APPROVAL_REQUIRED",
            Self::StepUpExpired => "OFFLINE_SYNC_STEP_UP_EXPIRED",
            Self::StepUpRevoked => "OFFLINE_SYNC_STEP_UP_REVOKED",
            Self::ScopeChanged => "OFFLINE_SYNC_SCOPE_CHANGED",
            Self::MissingDependency => "OFFLINE_SYNC_DEPENDENCY_REQUIRED",
            Self::DependencyCycle => "OFFLINE_SYNC_DEPENDENCY_CYCLE",
            Self::ConflictRequiresResolution => "OFFLINE_SYNC_CONFLICT_REQUIRES_RESOLUTION",
            Self::ResponseRejected => "OFFLINE_SYNC_RESPONSE_REJECTED",
        }
    }
}

#[derive(Clone, Debug, PartialEq, Eq, Serialize)]
pub struct ReconnectProjection {
    pub operation_id: String,
    pub state: SyncState,
    pub awaiting_approval: usize,
    pub conflict_id: Option<String>,
}

#[derive(Clone, Debug)]
pub struct ReconnectGate {
    projection: ReconnectProjection,
    approved_scope: Option<String>,
    scope_changed: bool,
    step_up_revoked: bool,
    transfer_calls: usize,
}

impl ReconnectGate {
    pub fn restore(operation_id: &str, pending: usize) -> Result<Self, OfflineSyncError> {
        if !safe_id(operation_id) || pending == 0 {
            return Err(OfflineSyncError::InputInvalid);
        }
        Ok(Self {
            projection: ReconnectProjection {
                operation_id: operation_id.to_owned(),
                state: SyncState::AwaitingApproval,
                awaiting_approval: pending,
                conflict_id: None,
            },
            approved_scope: None,
            scope_changed: false,
            step_up_revoked: false,
            transfer_calls: 0,
        })
    }

    pub fn on_connectivity_changed(&self, online: bool) -> ReconnectProjection {
        let mut projection = self.projection.clone();
        if !online && projection.state != SyncState::Conflict {
            projection.state = SyncState::Draft;
        }
        projection
    }

    pub fn approve(
        &mut self,
        scope_digest: &str,
        expires_at: u64,
        now: u64,
    ) -> Result<ReconnectProjection, OfflineSyncError> {
        if self.step_up_revoked {
            return Err(OfflineSyncError::StepUpRevoked);
        }
        if expires_at <= now {
            return Err(OfflineSyncError::StepUpExpired);
        }
        if !safe_id(scope_digest) {
            return Err(OfflineSyncError::InputInvalid);
        }
        self.approved_scope = Some(scope_digest.to_owned());
        self.scope_changed = false;
        self.projection.state = SyncState::Approved;
        Ok(self.projection.clone())
    }

    pub fn revoke_step_up(&mut self) {
        self.step_up_revoked = true;
    }

    pub fn reset_step_up(&mut self) {
        self.step_up_revoked = false;
    }

    pub fn confirm_scope(&mut self, current: &str) -> Result<(), OfflineSyncError> {
        if self.approved_scope.as_deref() != Some(current) {
            self.scope_changed = true;
            self.projection.state = SyncState::AwaitingApproval;
            return Err(OfflineSyncError::ScopeChanged);
        }
        Ok(())
    }

    pub fn resume_transfer(
        &mut self,
        explicit_resume: bool,
    ) -> Result<ReconnectProjection, OfflineSyncError> {
        if self.projection.state == SyncState::Conflict {
            return Err(OfflineSyncError::ConflictRequiresResolution);
        }
        if self.scope_changed {
            return Err(OfflineSyncError::ScopeChanged);
        }
        if !explicit_resume || self.projection.state != SyncState::Approved {
            return Err(OfflineSyncError::ApprovalRequired);
        }
        self.transfer_calls += 1;
        self.projection.state = SyncState::Transferring;
        Ok(self.projection.clone())
    }

    pub fn mark_conflict(&mut self, conflict_id: &str) -> Result<(), OfflineSyncError> {
        if !safe_id(conflict_id) {
            return Err(OfflineSyncError::InputInvalid);
        }
        self.projection.state = SyncState::Conflict;
        self.projection.conflict_id = Some(conflict_id.to_owned());
        Ok(())
    }

    pub fn projection(&self) -> ReconnectProjection {
        self.projection.clone()
    }

    pub fn transfer_calls(&self) -> usize {
        self.transfer_calls
    }
}

#[derive(Clone, Copy, Debug, Deserialize, PartialEq, Eq, Serialize)]
#[serde(rename_all = "snake_case")]
pub enum SyncItemKind {
    Source,
    Output,
}

#[derive(Clone, Debug, Deserialize, PartialEq, Eq, Serialize)]
#[serde(deny_unknown_fields)]
pub struct SyncItem {
    pub item_id: String,
    pub item_kind: SyncItemKind,
    pub dependency_item_ids: Vec<String>,
}

impl SyncItem {
    pub fn new(
        item_id: &str,
        item_kind: SyncItemKind,
        dependency_item_ids: Vec<String>,
    ) -> Result<Self, OfflineSyncError> {
        if !safe_id(item_id)
            || dependency_item_ids
                .iter()
                .any(|dependency| !safe_id(dependency))
        {
            return Err(OfflineSyncError::InputInvalid);
        }
        Ok(Self {
            item_id: item_id.to_owned(),
            item_kind,
            dependency_item_ids,
        })
    }
}

pub fn topological_batches(items: &[SyncItem]) -> Result<Vec<Vec<SyncItem>>, OfflineSyncError> {
    let by_id: HashMap<&str, &SyncItem> = items
        .iter()
        .map(|item| (item.item_id.as_str(), item))
        .collect();
    if by_id.len() != items.len() {
        return Err(OfflineSyncError::InputInvalid);
    }
    for item in items {
        if item
            .dependency_item_ids
            .iter()
            .any(|dependency| !by_id.contains_key(dependency.as_str()))
        {
            return Err(OfflineSyncError::MissingDependency);
        }
        if item.item_kind == SyncItemKind::Source && !item.dependency_item_ids.is_empty() {
            return Err(OfflineSyncError::MissingDependency);
        }
    }
    let mut completed = HashSet::new();
    let mut remaining: Vec<SyncItem> = items.to_vec();
    let mut batches = Vec::new();
    while !remaining.is_empty() {
        let mut ready = Vec::new();
        remaining.retain(|item| {
            let eligible = item
                .dependency_item_ids
                .iter()
                .all(|dependency| completed.contains(dependency));
            if eligible {
                ready.push(item.clone());
            }
            !eligible
        });
        if ready.is_empty() {
            return Err(OfflineSyncError::DependencyCycle);
        }
        ready.sort_by_key(|item| (item.item_kind == SyncItemKind::Output, item.item_id.clone()));
        for item in &ready {
            completed.insert(item.item_id.clone());
        }
        batches.push(ready);
    }
    Ok(batches)
}

pub fn knowledge_path(
    operation: &str,
    identifier: &str,
    package_or_copy_id: Option<&str>,
) -> Result<String, OfflineSyncError> {
    if !safe_id(identifier) || package_or_copy_id.is_some_and(|value| !safe_id(value)) {
        return Err(OfflineSyncError::InputInvalid);
    }
    match operation {
        "list" if package_or_copy_id.is_none() => Ok(format!(
            "/api/v1/workspaces/{identifier}/knowledge-packages"
        )),
        "provision" => Ok(format!(
            "/api/v1/workspaces/{identifier}/knowledge-packages/{}/offline-copies",
            package_or_copy_id.ok_or(OfflineSyncError::InputInvalid)?
        )),
        "content" if package_or_copy_id.is_none() => Ok(format!(
            "/api/v1/offline-knowledge-copies/{identifier}/content"
        )),
        _ => Err(OfflineSyncError::InputInvalid),
    }
}

pub fn sync_path(
    operation: &str,
    identifier: &str,
    conflict_id: Option<&str>,
    _reserved: Option<&str>,
) -> Result<String, OfflineSyncError> {
    if !safe_id(identifier) || conflict_id.is_some_and(|value| !safe_id(value)) {
        return Err(OfflineSyncError::InputInvalid);
    }
    match operation {
        "preview" if conflict_id.is_none() => {
            Ok(format!("/api/v1/workspaces/{identifier}/sync-operations"))
        }
        "status" if conflict_id.is_none() => Ok(format!("/api/v1/sync-operations/{identifier}")),
        "approve" if conflict_id.is_none() => {
            Ok(format!("/api/v1/sync-operations/{identifier}/approve"))
        }
        "transfer" if conflict_id.is_none() => Ok(format!(
            "/api/v1/sync-operations/{identifier}/transfer-batches"
        )),
        "resolve" => Ok(format!(
            "/api/v1/sync-operations/{identifier}/conflicts/{}/resolution",
            conflict_id.ok_or(OfflineSyncError::InputInvalid)?
        )),
        _ => Err(OfflineSyncError::InputInvalid),
    }
}

async fn current_workspace(
    session: &NativeSessionRuntime,
) -> Result<String, OfflineSyncBridgeError> {
    let status = session
        .status()
        .await
        .map_err(|_| safe_bridge_error("AUTHENTICATION_REQUIRED"))?;
    let value = serde_json::to_value(status)
        .map_err(|_| safe_bridge_error("AUTHENTICATION_REQUIRED"))?;
    if value.get("authenticated").and_then(Value::as_bool) != Some(true) {
        return Err(safe_bridge_error("AUTHENTICATION_REQUIRED"));
    }
    value
        .get("session")
        .and_then(Value::as_object)
        .and_then(|session| session.get("workspace_id"))
        .and_then(Value::as_str)
        .filter(|workspace_id| safe_id(workspace_id))
        .map(str::to_owned)
        .ok_or_else(|| safe_bridge_error("AUTHENTICATION_REQUIRED"))
}
fn safe_id(value: &str) -> bool {
    !value.is_empty()
        && value.len() <= 256
        && value.bytes().enumerate().all(|(index, byte)| {
            byte.is_ascii_alphanumeric() || (index > 0 && matches!(byte, b'.' | b'_' | b':' | b'-'))
        })
}

pub fn canonical_utc_timestamp(value: &str) -> Result<String, OfflineSyncError> {
    let body = value
        .strip_suffix('Z')
        .or_else(|| value.strip_suffix("+00:00"))
        .ok_or(OfflineSyncError::InputInvalid)?;
    let bytes = body.as_bytes();
    if bytes.len() < 19
        || bytes[4] != b'-'
        || bytes[7] != b'-'
        || bytes[10] != b'T'
        || bytes[13] != b':'
        || bytes[16] != b':'
        || !bytes[..19]
            .iter()
            .enumerate()
            .all(|(index, byte)| matches!(index, 4 | 7 | 10 | 13 | 16) || byte.is_ascii_digit())
        || (bytes.len() > 19
            && (bytes[19] != b'.'
                || bytes.len() == 20
                || !bytes[20..].iter().all(u8::is_ascii_digit)))
    {
        return Err(OfflineSyncError::InputInvalid);
    }
    Ok(format!("{body}Z"))
}

pub struct OfflineCloudRequest {
    pub method: &'static str,
    pub path: String,
    pub body: Zeroizing<Vec<u8>>,
    pub idempotency_key: Option<Zeroizing<String>>,
    pub if_match: Option<Zeroizing<String>>,
    pub workspace_id: Option<String>,
    pub expects_binary: bool,
}

pub struct OfflineCloudResponse {
    pub status: u16,
    pub content_type: Option<String>,
    pub body: Zeroizing<Vec<u8>>,
}

pub trait OfflineCloudTransport: Send + Sync {
    fn execute<'a>(
        &'a self,
        access: &'a [u8],
        request: OfflineCloudRequest,
    ) -> Pin<Box<dyn Future<Output = Result<OfflineCloudResponse, &'static str>> + Send + 'a>>;
}

struct NativeOfflineCloudClient {
    client: reqwest::Client,
    gateway: String,
}

impl NativeOfflineCloudClient {
    fn fixed() -> Result<Self, &'static str> {
        let client = reqwest::Client::builder()
            .https_only(true)
            .redirect(reqwest::redirect::Policy::none())
            .connect_timeout(std::time::Duration::from_secs(5))
            .timeout(std::time::Duration::from_secs(30))
            .build()
            .map_err(|_| "OFFLINE_SYNC_REQUEST_FAILED")?;
        Ok(Self {
            client,
            gateway: PUBLIC_GATEWAY.to_owned(),
        })
    }

    #[cfg(feature = "contract-test")]
    fn for_contract_test(gateway: &str) -> Result<Self, &'static str> {
        if !gateway.starts_with("http://127.0.0.1:") || gateway.contains(['?', '#', '\r', '\n']) {
            return Err("OFFLINE_SYNC_REQUEST_FAILED");
        }
        let client = reqwest::Client::builder()
            .redirect(reqwest::redirect::Policy::none())
            .timeout(std::time::Duration::from_secs(5))
            .build()
            .map_err(|_| "OFFLINE_SYNC_REQUEST_FAILED")?;
        Ok(Self {
            client,
            gateway: gateway.to_owned(),
        })
    }
}

impl OfflineCloudTransport for NativeOfflineCloudClient {
    fn execute<'a>(
        &'a self,
        access: &'a [u8],
        mut operation: OfflineCloudRequest,
    ) -> Pin<Box<dyn Future<Output = Result<OfflineCloudResponse, &'static str>> + Send + 'a>> {
        Box::pin(async move {
            if operation.path.contains("//") || operation.path.contains(['?', '#', '\r', '\n']) {
                return Err("OFFLINE_SYNC_REQUEST_FAILED");
            }
            let endpoint = format!("{}{}", self.gateway, operation.path);
            let method = reqwest::Method::from_bytes(operation.method.as_bytes())
                .map_err(|_| "OFFLINE_SYNC_REQUEST_FAILED")?;
            let access_text = std::str::from_utf8(access).map_err(|_| "AUTHENTICATION_REQUIRED")?;
            let mut request = self
                .client
                .request(method, endpoint)
                .bearer_auth(access_text)
                .header(
                    reqwest::header::ACCEPT,
                    if operation.expects_binary {
                        "application/octet-stream"
                    } else {
                        "application/json"
                    },
                );
            if let Some(value) = &operation.idempotency_key {
                request = request.header("Idempotency-Key", value.as_str());
            }
            if let Some(value) = &operation.if_match {
                request = request.header(reqwest::header::IF_MATCH, value.as_str());
            }
            if let Some(value) = &operation.workspace_id {
                request = request.header("X-Daon-Workspace-Id", value);
            }
            if !operation.body.is_empty() {
                request = request
                    .header(reqwest::header::CONTENT_TYPE, "application/json")
                    .body(std::mem::take(&mut *operation.body));
            }
            let mut response = request
                .send()
                .await
                .map_err(|_| "OFFLINE_SYNC_REQUEST_FAILED")?;
            let status = response.status().as_u16();
            let content_type = response
                .headers()
                .get(reqwest::header::CONTENT_TYPE)
                .and_then(|value| value.to_str().ok())
                .map(str::to_owned);
            let content_length = response
                .headers()
                .get(reqwest::header::CONTENT_LENGTH)
                .and_then(|value| value.to_str().ok())
                .and_then(|value| value.parse::<usize>().ok());
            let maximum = if operation.expects_binary {
                KNOWLEDGE_CONTENT_MAX
            } else {
                JSON_RESPONSE_MAX
            };
            let expected = if operation.expects_binary {
                "application/octet-stream"
            } else {
                "application/json"
            };
            if !matches!(status, 200 | 201)
                || response.headers().contains_key(reqwest::header::SET_COOKIE)
                || content_type.as_deref().unwrap_or("").split(';').next() != Some(expected)
                || content_length.is_some_and(|length| length > maximum)
            {
                return Err(match status {
                    401 => "AUTHENTICATION_REQUIRED",
                    403 => "OFFLINE_SYNC_FORBIDDEN",
                    404 => "OFFLINE_SYNC_NOT_FOUND",
                    409 => "OFFLINE_SYNC_CONFLICT",
                    _ => "OFFLINE_SYNC_RESPONSE_REJECTED",
                });
            }
            let mut body = Zeroizing::new(Vec::new());
            while let Some(chunk) = response
                .chunk()
                .await
                .map_err(|_| "OFFLINE_SYNC_RESPONSE_REJECTED")?
            {
                if body.len().saturating_add(chunk.len()) > maximum {
                    return Err("OFFLINE_SYNC_RESPONSE_REJECTED");
                }
                body.extend_from_slice(&chunk);
            }
            if body
                .windows(access.len())
                .any(|candidate| candidate == access)
                || body
                    .windows(PUBLIC_GATEWAY.len())
                    .any(|candidate| candidate == PUBLIC_GATEWAY.as_bytes())
            {
                return Err("OFFLINE_SYNC_RESPONSE_REJECTED");
            }
            Ok(OfflineCloudResponse {
                status,
                content_type,
                body,
            })
        })
    }
}

#[derive(Clone, Debug, Deserialize, Serialize)]
#[serde(deny_unknown_fields)]
pub struct KnowledgeListRequest {
    pub workspace_id: String,
}

#[derive(Clone, Debug, Deserialize, Serialize)]
#[serde(deny_unknown_fields)]
pub struct KnowledgeProvisionRequest {
    pub workspace_id: String,
    pub package_id: String,
    pub device_id: String,
    pub step_up_authorization_id: String,
    pub idempotency_key: String,
}

#[derive(Clone, Debug, Deserialize, Serialize)]
#[serde(deny_unknown_fields)]
pub struct KnowledgeRefreshRequest {
    pub workspace_id: String,
    pub copy_id: String,
    pub state: CopyState,
    pub recorded_at: String,
}

#[derive(Clone, Debug, Deserialize, Serialize)]
#[serde(deny_unknown_fields)]
pub struct SyncPreviewRequest {
    pub workspace_id: String,
    pub target_area: String,
    pub items: Vec<Value>,
    pub if_match: String,
    pub idempotency_key: String,
}

#[derive(Clone, Debug, Deserialize, Serialize)]
#[serde(deny_unknown_fields)]
pub struct SyncStatusRequest {
    pub operation_id: String,
}

#[derive(Clone, Debug, Deserialize, Serialize)]
#[serde(deny_unknown_fields)]
pub struct SyncApproveRequest {
    pub operation_id: String,
    pub approved_item_ids: Vec<String>,
    pub step_up_authorization_id: String,
    pub if_match: String,
    pub idempotency_key: String,
}

#[derive(Clone, Debug, Deserialize, Serialize)]
#[serde(deny_unknown_fields)]
pub struct SyncTransferRequest {
    pub operation_id: String,
    pub cursor: Option<String>,
    pub items: Vec<Value>,
    pub if_match: String,
    pub idempotency_key: String,
    pub resume: bool,
}

#[derive(Clone, Debug, Deserialize, Serialize)]
#[serde(deny_unknown_fields)]
pub struct SyncResolveRequest {
    pub operation_id: String,
    pub conflict_id: String,
    pub choice: String,
    pub content_base64: Option<String>,
    pub if_match: String,
    pub idempotency_key: String,
}

#[derive(Clone, Debug, Deserialize, Serialize)]
struct PackageProjection {
    package_id: String,
    producer: String,
    producer_version: String,
    knowledge_registration_id: String,
    output_version_id: String,
    authority: String,
    registration_state: String,
    review_state: String,
    digest_sha256: String,
    byte_size: usize,
    content_type: String,
    effective_at: String,
    expires_at: String,
}

#[derive(Deserialize)]
struct PackageListData {
    items: Vec<PackageProjection>,
}
#[derive(Deserialize)]
struct PackageListEnvelope {
    data: PackageListData,
}

#[derive(Deserialize)]
struct CopyGrant {
    copy_id: String,
    package_id: String,
    state: String,
    digest_sha256: String,
    expires_at: String,
}
#[derive(Deserialize)]
struct CopyGrantEnvelope {
    data: CopyGrant,
}

#[derive(Debug, Serialize)]
pub struct OfflineSyncBridgeError {
    pub error_code: &'static str,
}

pub struct OfflineSyncRuntime {
    transport: Arc<dyn OfflineCloudTransport>,
    packages: Mutex<HashMap<(String, String), PackageProjection>>,
}

impl OfflineSyncRuntime {
    pub fn new() -> Self {
        Self {
            transport: Arc::new(NativeOfflineCloudClient::fixed().expect("fixed public gateway")),
            packages: Mutex::new(HashMap::new()),
        }
    }

    #[cfg(feature = "contract-test")]
    pub fn for_contract_test(transport: Arc<dyn OfflineCloudTransport>) -> Self {
        Self {
            transport,
            packages: Mutex::new(HashMap::new()),
        }
    }

    async fn cloud(
        &self,
        session: &NativeSessionRuntime,
        request: OfflineCloudRequest,
    ) -> Result<OfflineCloudResponse, OfflineSyncBridgeError> {
        session
            .execute_offline_sync_once(self.transport.as_ref(), request)
            .await
            .map_err(safe_bridge_error)
    }

    pub(crate) async fn refresh_provider_settings(
        &self,
        session: &NativeSessionRuntime,
        manager: &LocalServiceManager,
        workspace_id: &str,
    ) -> Result<Value, OfflineSyncBridgeError> {
        if !safe_id(workspace_id) {
            return Err(safe_bridge_error("OFFLINE_SYNC_INPUT_INVALID"));
        }
        let profiles_response = self
            .cloud(
                session,
                cloud_request(
                    "GET",
                    format!("/api/v1/model-profiles?workspace_id={workspace_id}"),
                    Value::Null,
                    None,
                    None,
                    None,
                    false,
                )?,
            )
            .await?;
        let deployments_response = self
            .cloud(
                session,
                cloud_request(
                    "GET",
                    format!("/api/v1/model-deployments?workspace_id={workspace_id}"),
                    Value::Null,
                    None,
                    None,
                    None,
                    false,
                )?,
            )
            .await?;
        let profiles_value: Value = serde_json::from_slice(&profiles_response.body)
            .map_err(|_| safe_bridge_error("OFFLINE_SYNC_RESPONSE_REJECTED"))?;
        let deployments_value: Value = serde_json::from_slice(&deployments_response.body)
            .map_err(|_| safe_bridge_error("OFFLINE_SYNC_RESPONSE_REJECTED"))?;
        let raw_profiles = profiles_value
            .get("data")
            .and_then(Value::as_array)
            .ok_or_else(|| safe_bridge_error("OFFLINE_SYNC_RESPONSE_REJECTED"))?;
        let raw_deployments = deployments_value
            .get("data")
            .and_then(Value::as_array)
            .ok_or_else(|| safe_bridge_error("OFFLINE_SYNC_RESPONSE_REJECTED"))?;
        let profiles: Vec<Value> = raw_profiles
            .iter()
            .filter(|item| {
                item.get("provider_code").and_then(Value::as_str) == Some("OLLAMA")
                    && item.get("provider_kind").and_then(Value::as_str)
                        == Some("server_internal")
            })
            .map(|item| {
                serde_json::json!({
                    "profile_id": item.get("profile_id"),
                    "provider_code": item.get("provider_code"),
                    "provider_kind": item.get("provider_kind"),
                    "base_url": item.get("base_url"),
                    "active": item.get("active"),
                    "version": item.get("version"),
                })
            })
            .collect();
        let deployments: Vec<Value> = raw_deployments
            .iter()
            .filter(|item| item.get("provider_code").and_then(Value::as_str) == Some("OLLAMA"))
            .map(|item| {
                serde_json::json!({
                    "deployment_id": item.get("deployment_id"),
                    "profile_id": item.get("profile_id"),
                    "provider_code": item.get("provider_code"),
                    "model_id": item.get("model_id"),
                    "roles": item.get("roles"),
                    "active": item.get("active"),
                    "selected": item.get("selected"),
                    "version": item.get("version"),
                })
            })
            .collect();
        let policy_material = serde_json::to_vec(&serde_json::json!({
            "workspace_id": workspace_id,
            "profiles": profiles,
            "deployments": deployments,
        }))
        .map_err(|_| safe_bridge_error("OFFLINE_SYNC_RESPONSE_REJECTED"))?;
        let policy_version = format!("provider-settings-{:x}", Sha256::digest(&policy_material));
        local_json(
            manager,
            workspace_id,
            "studio.write",
            "studio_provider_settings_import",
            "POST",
            "/local/v1/studio/provider-settings",
            &serde_json::json!({
                "workspace_id": workspace_id,
                "profiles": profiles,
                "deployments": deployments,
                "policy_version": policy_version,
            }),
        )
    }
    async fn list_knowledge(
        &self,
        session: &NativeSessionRuntime,
        input: &KnowledgeListRequest,
    ) -> Result<Value, OfflineSyncBridgeError> {
        let path = knowledge_path("list", &input.workspace_id, None).map_err(core_error)?;
        let response = self
            .cloud(
                session,
                cloud_request("GET", path, Value::Null, None, None, None, false)?,
            )
            .await?;
        let envelope: PackageListEnvelope = serde_json::from_slice(&response.body)
            .map_err(|_| safe_bridge_error("OFFLINE_SYNC_RESPONSE_REJECTED"))?;
        let mut cache = self
            .packages
            .lock()
            .map_err(|_| safe_bridge_error("OFFLINE_SYNC_REQUEST_FAILED"))?;
        cache.retain(|(workspace, _), _| workspace != &input.workspace_id);
        for package in &envelope.data.items {
            if !safe_id(&package.package_id)
                || !digest(&package.digest_sha256)
                || package.byte_size > KNOWLEDGE_CONTENT_MAX
                || package.authority != "approved"
                || package.registration_state != "registered"
                || package.review_state != "approved"
            {
                return Err(safe_bridge_error("OFFLINE_SYNC_RESPONSE_REJECTED"));
            }
            cache.insert(
                (input.workspace_id.clone(), package.package_id.clone()),
                package.clone(),
            );
        }
        serde_json::to_value(&envelope.data.items)
            .map_err(|_| safe_bridge_error("OFFLINE_SYNC_RESPONSE_REJECTED"))
    }

    async fn provision_knowledge(
        &self,
        session: &NativeSessionRuntime,
        manager: &LocalServiceManager,
        input: &KnowledgeProvisionRequest,
    ) -> Result<Value, OfflineSyncBridgeError> {
        valid_idempotency(&input.idempotency_key)?;
        let package = self
            .packages
            .lock()
            .map_err(|_| safe_bridge_error("OFFLINE_SYNC_REQUEST_FAILED"))?
            .get(&(input.workspace_id.clone(), input.package_id.clone()))
            .cloned()
            .ok_or_else(|| safe_bridge_error("OFFLINE_KNOWLEDGE_LIST_REQUIRED"))?;
        let path = knowledge_path("provision", &input.workspace_id, Some(&input.package_id))
            .map_err(core_error)?;
        let body = serde_json::json!({
            "device_id": input.device_id,
            "step_up_authorization_id": input.step_up_authorization_id,
        });
        let grant_response = self
            .cloud(
                session,
                cloud_request(
                    "POST",
                    path,
                    body,
                    Some(&input.idempotency_key),
                    None,
                    None,
                    false,
                )?,
            )
            .await?;
        let grant: CopyGrantEnvelope = serde_json::from_slice(&grant_response.body)
            .map_err(|_| safe_bridge_error("OFFLINE_SYNC_RESPONSE_REJECTED"))?;
        if grant.data.package_id != package.package_id
            || grant.data.digest_sha256 != package.digest_sha256
            || grant.data.state != "approved"
            || grant.data.expires_at != package.expires_at
            || !safe_id(&grant.data.copy_id)
        {
            return Err(safe_bridge_error("OFFLINE_SYNC_RESPONSE_REJECTED"));
        }
        let canonical_expires_at = canonical_utc_timestamp(&package.expires_at)
            .map_err(|_| safe_bridge_error("OFFLINE_SYNC_RESPONSE_REJECTED"))?;
        let canonical_effective_at = canonical_utc_timestamp(&package.effective_at)
            .map_err(|_| safe_bridge_error("OFFLINE_SYNC_RESPONSE_REJECTED"))?;
        if canonical_effective_at >= canonical_expires_at {
            return Err(safe_bridge_error("OFFLINE_SYNC_RESPONSE_REJECTED"));
        }
        let content_path =
            knowledge_path("content", &grant.data.copy_id, None).map_err(core_error)?;
        let content = self
            .cloud(
                session,
                cloud_request(
                    "GET",
                    content_path,
                    Value::Null,
                    None,
                    None,
                    Some(&input.workspace_id),
                    true,
                )?,
            )
            .await?;
        if content.body.len() != package.byte_size
            || format!("{:x}", Sha256::digest(content.body.as_slice())) != package.digest_sha256
        {
            return Err(safe_bridge_error("OFFLINE_SYNC_RESPONSE_REJECTED"));
        }
        let manifest = serde_json::json!({
            "workspace_id": input.workspace_id,
            "copy_id": grant.data.copy_id,
            "package_id": package.package_id,
            "producer_product": package.producer,
            "producer_version": package.producer_version,
            "knowledge_registration_id": package.knowledge_registration_id,
            "output_version_id": package.output_version_id,
            "authority": package.authority,
            "registration_state": package.registration_state,
            "review_state": package.review_state,
            "effective_at": canonical_effective_at.clone(),
            "expires_at": canonical_expires_at.clone(),
            "schema_version": 1,
            "content_digest_sha256": package.digest_sha256,
        });
        let manifest_bytes = serde_json::to_vec(&manifest)
            .map_err(|_| safe_bridge_error("OFFLINE_SYNC_REQUEST_FAILED"))?;
        let local_body = serde_json::json!({
            "workspace_id": input.workspace_id,
            "copy_id": grant.data.copy_id,
            "package_id": package.package_id,
            "producer_product": package.producer,
            "producer_version": package.producer_version,
            "knowledge_registration_id": package.knowledge_registration_id,
            "output_version_id": package.output_version_id,
            "authority": package.authority,
            "registration_state": package.registration_state,
            "review_state": package.review_state,
            "effective_at": canonical_effective_at,
            "expires_at": canonical_expires_at,
            "schema_version": 1,
            "content_digest_sha256": package.digest_sha256,
            "manifest_digest_sha256": format!("{:x}", Sha256::digest(&manifest_bytes)),
            "canonical_package_base64": encode_base64(content.body.as_slice()),
            "idempotency_key": input.idempotency_key,
        });
        local_json(
            manager,
            &input.workspace_id,
            "knowledge.write",
            "studio_knowledge_copy_import",
            "POST",
            "/local/v1/studio/knowledge-copies",
            &local_body,
        )
    }
}

fn cloud_request(
    method: &'static str,
    path: String,
    body: Value,
    idempotency_key: Option<&str>,
    if_match: Option<&str>,
    workspace_id: Option<&str>,
    expects_binary: bool,
) -> Result<OfflineCloudRequest, OfflineSyncBridgeError> {
    if let Some(value) = idempotency_key {
        valid_idempotency(value)?;
    }
    if if_match
        .is_some_and(|value| value.is_empty() || value.len() > 256 || value.contains(['\r', '\n']))
    {
        return Err(safe_bridge_error("OFFLINE_SYNC_INPUT_INVALID"));
    }
    let body = if body.is_null() {
        Vec::new()
    } else {
        serde_json::to_vec(&body).map_err(|_| safe_bridge_error("OFFLINE_SYNC_INPUT_INVALID"))?
    };
    Ok(OfflineCloudRequest {
        method,
        path,
        body: Zeroizing::new(body),
        idempotency_key: idempotency_key.map(|value| Zeroizing::new(value.to_owned())),
        if_match: if_match.map(|value| Zeroizing::new(value.to_owned())),
        workspace_id: workspace_id.map(str::to_owned),
        expects_binary,
    })
}

fn valid_idempotency(value: &str) -> Result<(), OfflineSyncBridgeError> {
    if !(16..=128).contains(&value.len()) || value.contains(['\r', '\n']) {
        return Err(safe_bridge_error("OFFLINE_SYNC_INPUT_INVALID"));
    }
    Ok(())
}

fn digest(value: &str) -> bool {
    value.len() == 64
        && value
            .bytes()
            .all(|byte| byte.is_ascii_hexdigit() && !byte.is_ascii_uppercase())
}

fn safe_bridge_error(code: &'static str) -> OfflineSyncBridgeError {
    let error_code = match code {
        "AUTHENTICATION_REQUIRED"
        | "OFFLINE_SYNC_INPUT_INVALID"
        | "OFFLINE_SYNC_APPROVAL_REQUIRED"
        | "OFFLINE_SYNC_STEP_UP_EXPIRED"
        | "OFFLINE_SYNC_STEP_UP_REVOKED"
        | "OFFLINE_SYNC_SCOPE_CHANGED"
        | "OFFLINE_SYNC_DEPENDENCY_REQUIRED"
        | "OFFLINE_SYNC_CONFLICT"
        | "OFFLINE_SYNC_FORBIDDEN"
        | "OFFLINE_SYNC_NOT_FOUND"
        | "OFFLINE_SYNC_RESPONSE_REJECTED"
        | "OFFLINE_KNOWLEDGE_LIST_REQUIRED"
        | "LOCAL_SERVICE_UNAVAILABLE"
        | "LOCAL_COMMAND_NOT_ALLOWED" => code,
        _ => "OFFLINE_SYNC_REQUEST_FAILED",
    };
    OfflineSyncBridgeError { error_code }
}

fn core_error(error: OfflineSyncError) -> OfflineSyncBridgeError {
    safe_bridge_error(error.code())
}

fn local_json(
    manager: &LocalServiceManager,
    workspace_id: &str,
    capability: &'static str,
    command: &'static str,
    method: &'static str,
    path: &str,
    body: &Value,
) -> Result<Value, OfflineSyncBridgeError> {
    let mut encoded = if body.is_null() {
        Vec::new()
    } else {
        serde_json::to_vec(body).map_err(|_| safe_bridge_error("OFFLINE_SYNC_INPUT_INVALID"))?
    };
    let response = manager
        .execute_workspace_studio_request(workspace_id, capability, command, method, path, &encoded)
        .map_err(safe_bridge_error);
    encoded.zeroize();
    let response = response?;
    if response.status != 200
        || response.content_type.as_deref() != Some("application/json")
        || response.content_length != Some(response.body.len())
        || response.body.len() > JSON_RESPONSE_MAX
    {
        return Err(safe_bridge_error("OFFLINE_SYNC_RESPONSE_REJECTED"));
    }
    let envelope: Value = serde_json::from_slice(&response.body)
        .map_err(|_| safe_bridge_error("OFFLINE_SYNC_RESPONSE_REJECTED"))?;
    envelope
        .get("data")
        .cloned()
        .ok_or_else(|| safe_bridge_error("OFFLINE_SYNC_RESPONSE_REJECTED"))
}

fn persist_sync_projection(
    manager: &LocalServiceManager,
    cloud: &Value,
) -> Result<(), OfflineSyncBridgeError> {
    let operation = cloud
        .get("operation")
        .or_else(|| cloud.get("data"))
        .and_then(Value::as_object)
        .ok_or_else(|| safe_bridge_error("OFFLINE_SYNC_RESPONSE_REJECTED"))?;
    let operation_id = operation
        .get("operation_id")
        .and_then(Value::as_str)
        .filter(|value| safe_id(value))
        .ok_or_else(|| safe_bridge_error("OFFLINE_SYNC_RESPONSE_REJECTED"))?;
    let workspace_id = operation
        .get("workspace_id")
        .and_then(Value::as_str)
        .filter(|value| safe_id(value))
        .ok_or_else(|| safe_bridge_error("OFFLINE_SYNC_RESPONSE_REJECTED"))?;
    let state = operation
        .get("state")
        .and_then(Value::as_str)
        .filter(|value| {
            matches!(
                *value,
                "draft"
                    | "awaiting_approval"
                    | "approved"
                    | "transferring"
                    | "conflict"
                    | "reindex_requested"
            )
        })
        .ok_or_else(|| safe_bridge_error("OFFLINE_SYNC_RESPONSE_REJECTED"))?;
    let version = operation
        .get("version")
        .and_then(Value::as_u64)
        .filter(|value| *value >= 1)
        .ok_or_else(|| safe_bridge_error("OFFLINE_SYNC_RESPONSE_REJECTED"))?;
    let manifest_digest = operation
        .get("manifest_digest")
        .and_then(Value::as_str)
        .filter(|value| digest(value))
        .ok_or_else(|| safe_bridge_error("OFFLINE_SYNC_RESPONSE_REJECTED"))?;
    let conflict_id = operation
        .get("conflicts")
        .and_then(Value::as_array)
        .and_then(|items| {
            items.iter().find_map(|item| {
                if item.get("state").and_then(Value::as_str) == Some("unresolved") {
                    item.get("conflict_id").and_then(Value::as_str)
                } else {
                    None
                }
            })
        });
    if conflict_id.is_some_and(|value| !safe_id(value)) {
        return Err(safe_bridge_error("OFFLINE_SYNC_RESPONSE_REJECTED"));
    }
    let queued_at = time::OffsetDateTime::now_utc()
        .format(&time::format_description::well_known::Rfc3339)
        .map_err(|_| safe_bridge_error("OFFLINE_SYNC_REQUEST_FAILED"))?;
    let path = format!("/local/v1/studio/sync-operations/{operation_id}/states");
    local_json(
        manager,
        workspace_id,
        "sync.write",
        "studio_sync_state_append",
        "POST",
        &path,
        &serde_json::json!({
            "workspace_id": workspace_id,
            "version": version,
            "approval_state": state,
            "manifest_digest": manifest_digest,
            "batch_cursor": Value::Null,
            "conflict_id": conflict_id,
            "queued_at": queued_at,
            "previous_version": if version == 1 { Value::Null } else { Value::from(version - 1) },
        }),
    )?;
    Ok(())
}

fn encode_base64(input: &[u8]) -> String {
    const TABLE: &[u8; 64] = b"ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789+/";
    let mut output = String::with_capacity(input.len().div_ceil(3) * 4);
    for chunk in input.chunks(3) {
        let value = (u32::from(chunk[0]) << 16)
            | (u32::from(*chunk.get(1).unwrap_or(&0)) << 8)
            | u32::from(*chunk.get(2).unwrap_or(&0));
        output.push(TABLE[((value >> 18) & 63) as usize] as char);
        output.push(TABLE[((value >> 12) & 63) as usize] as char);
        output.push(if chunk.len() > 1 {
            TABLE[((value >> 6) & 63) as usize] as char
        } else {
            '='
        });
        output.push(if chunk.len() > 2 {
            TABLE[(value & 63) as usize] as char
        } else {
            '='
        });
    }
    output
}

#[tauri::command]
pub async fn offline_knowledge_list(
    request: KnowledgeListRequest,
    runtime: tauri::State<'_, OfflineSyncRuntime>,
    session: tauri::State<'_, NativeSessionRuntime>,
) -> Result<Value, OfflineSyncBridgeError> {
    runtime.list_knowledge(&session, &request).await
}

#[tauri::command]
pub async fn offline_knowledge_provision(
    request: KnowledgeProvisionRequest,
    runtime: tauri::State<'_, OfflineSyncRuntime>,
    session: tauri::State<'_, NativeSessionRuntime>,
    manager: tauri::State<'_, LocalServiceManager>,
) -> Result<Value, OfflineSyncBridgeError> {
    runtime
        .provision_knowledge(&session, &manager, &request)
        .await
}

#[tauri::command]
pub async fn offline_knowledge_refresh(
    request: KnowledgeRefreshRequest,
    manager: tauri::State<'_, LocalServiceManager>,
    session: tauri::State<'_, NativeSessionRuntime>,
) -> Result<Value, OfflineSyncBridgeError> {
    let workspace_id = current_workspace(&session).await?;
    if request.workspace_id != workspace_id {
        return Err(safe_bridge_error("OFFLINE_SYNC_FORBIDDEN"));
    }
    if !safe_id(&request.copy_id) {
        return Err(safe_bridge_error("OFFLINE_SYNC_INPUT_INVALID"));
    }
    let path = format!(
        "/local/v1/studio/knowledge-copies/{}/refresh",
        request.copy_id
    );
    local_json(
        &manager,
        &request.workspace_id,
        "knowledge.write",
        "studio_knowledge_copy_refresh",
        "POST",
        &path,
        &serde_json::json!({"workspace_id":request.workspace_id,"state":request.state,"recorded_at":request.recorded_at}),
    )
}

async fn sync_exchange(
    runtime: &OfflineSyncRuntime,
    session: &NativeSessionRuntime,
    method: &'static str,
    path: String,
    body: Value,
    idempotency: Option<&str>,
    if_match: Option<&str>,
) -> Result<Value, OfflineSyncBridgeError> {
    let response = runtime
        .cloud(
            session,
            cloud_request(method, path, body, idempotency, if_match, None, false)?,
        )
        .await?;
    serde_json::from_slice(&response.body)
        .map_err(|_| safe_bridge_error("OFFLINE_SYNC_RESPONSE_REJECTED"))
}

#[tauri::command]
pub async fn offline_sync_preview(
    request: SyncPreviewRequest,
    runtime: tauri::State<'_, OfflineSyncRuntime>,
    session: tauri::State<'_, NativeSessionRuntime>,
    manager: tauri::State<'_, LocalServiceManager>,
) -> Result<Value, OfflineSyncBridgeError> {
    let path = sync_path("preview", &request.workspace_id, None, None).map_err(core_error)?;
    let value = sync_exchange(
        &runtime,
        &session,
        "POST",
        path,
        serde_json::json!({"target_area":request.target_area,"items":request.items}),
        Some(&request.idempotency_key),
        Some(&request.if_match),
    )
    .await?;
    persist_sync_projection(&manager, &value)?;
    Ok(value)
}

#[tauri::command]
pub async fn offline_sync_status(
    request: SyncStatusRequest,
    runtime: tauri::State<'_, OfflineSyncRuntime>,
    session: tauri::State<'_, NativeSessionRuntime>,
    manager: tauri::State<'_, LocalServiceManager>,
) -> Result<Value, OfflineSyncBridgeError> {
    let path = sync_path("status", &request.operation_id, None, None).map_err(core_error)?;
    let value = sync_exchange(&runtime, &session, "GET", path, Value::Null, None, None).await?;
    persist_sync_projection(&manager, &value)?;
    Ok(value)
}

#[tauri::command]
pub async fn offline_sync_approve(
    request: SyncApproveRequest,
    runtime: tauri::State<'_, OfflineSyncRuntime>,
    session: tauri::State<'_, NativeSessionRuntime>,
    manager: tauri::State<'_, LocalServiceManager>,
) -> Result<Value, OfflineSyncBridgeError> {
    let path = sync_path("approve", &request.operation_id, None, None).map_err(core_error)?;
    let value = sync_exchange(&runtime, &session, "POST", path,
        serde_json::json!({"approved_item_ids":request.approved_item_ids,"step_up_authorization_id":request.step_up_authorization_id}),
        Some(&request.idempotency_key), Some(&request.if_match)).await?;
    persist_sync_projection(&manager, &value)?;
    Ok(value)
}

#[tauri::command]
pub async fn offline_sync_transfer(
    request: SyncTransferRequest,
    runtime: tauri::State<'_, OfflineSyncRuntime>,
    session: tauri::State<'_, NativeSessionRuntime>,
    manager: tauri::State<'_, LocalServiceManager>,
) -> Result<Value, OfflineSyncBridgeError> {
    if !request.resume {
        return Err(safe_bridge_error("OFFLINE_SYNC_APPROVAL_REQUIRED"));
    }
    let path = sync_path("transfer", &request.operation_id, None, None).map_err(core_error)?;
    let value = sync_exchange(
        &runtime,
        &session,
        "POST",
        path,
        serde_json::json!({"cursor":request.cursor,"items":request.items}),
        Some(&request.idempotency_key),
        Some(&request.if_match),
    )
    .await?;
    persist_sync_projection(&manager, &value)?;
    Ok(value)
}

#[tauri::command]
pub async fn offline_sync_resolve(
    request: SyncResolveRequest,
    runtime: tauri::State<'_, OfflineSyncRuntime>,
    session: tauri::State<'_, NativeSessionRuntime>,
    manager: tauri::State<'_, LocalServiceManager>,
) -> Result<Value, OfflineSyncBridgeError> {
    if !matches!(request.choice.as_str(), "keep_local" | "keep_cloud") {
        return Err(safe_bridge_error("OFFLINE_SYNC_INPUT_INVALID"));
    }
    let path = sync_path(
        "resolve",
        &request.operation_id,
        Some(&request.conflict_id),
        None,
    )
    .map_err(core_error)?;
    let value = sync_exchange(
        &runtime,
        &session,
        "POST",
        path,
        serde_json::json!({"choice":request.choice,"content_base64":request.content_base64}),
        Some(&request.idempotency_key),
        Some(&request.if_match),
    )
    .await?;
    persist_sync_projection(&manager, &value)?;
    Ok(value)
}
