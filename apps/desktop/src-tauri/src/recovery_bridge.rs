use crate::local_service::LocalServiceManager;
use serde::{Deserialize, Serialize};
use sha2::{Digest, Sha256};
use std::collections::{HashSet, VecDeque};
use std::fmt;
use std::future::Future;
use std::pin::Pin;
use std::sync::atomic::{AtomicBool, AtomicU64, Ordering};
use std::sync::{Arc, Mutex};
use std::time::{SystemTime, UNIX_EPOCH};
use time::{OffsetDateTime, format_description::well_known::Rfc3339};
use zeroize::{Zeroize, Zeroizing};

const RESPONSE_MAX_BYTES: usize = 1_048_576;
const CLOUD_BODY_MAX_BYTES: usize = 256 * 1024;
static FALLBACK_TRACE_COUNTER: AtomicU64 = AtomicU64::new(1);
#[cfg(feature = "contract-test")]
static CLOUD_SECRET_DROP_COUNTER: AtomicU64 = AtomicU64::new(0);
#[cfg(feature = "contract-test")]
static CLOUD_WIRE_DROP_COUNTER: AtomicU64 = AtomicU64::new(0);
#[cfg(feature = "contract-test")]
static CLOUD_WIRE_OWNER_ALIASES_SOURCE: AtomicBool = AtomicBool::new(false);

fn trace_id_with(fill: impl FnOnce(&mut [u8; 16]) -> Result<(), ()>) -> String {
    let mut bytes = [0_u8; 16];
    if fill(&mut bytes).is_err() {
        let counter = FALLBACK_TRACE_COUNTER.fetch_add(1, Ordering::Relaxed);
        let timestamp = SystemTime::now()
            .duration_since(UNIX_EPOCH)
            .map(|duration| duration.as_nanos())
            .unwrap_or_default();
        let digest =
            Sha256::digest(format!("{}:{timestamp}:{counter}", std::process::id()).as_bytes());
        bytes.copy_from_slice(&digest[..16]);
    }
    let trace = bytes.iter().map(|byte| format!("{byte:02x}")).collect();
    bytes.fill(0);
    trace
}

#[derive(Clone, Debug, Deserialize, Serialize)]
#[serde(deny_unknown_fields)]
pub struct RecoveryScanRequest {
    pub workspace_id: String,
    pub target_id: String,
    pub snapshot_checksum: String,
    pub metadata_checksum: String,
    pub actual_checksum: String,
    pub journal_present: bool,
}

#[derive(Clone, Debug, Deserialize, Serialize)]
#[serde(deny_unknown_fields)]
pub struct RecoveryRepairRequest {
    pub workspace_id: String,
    pub expected_version: u64,
}

#[derive(Clone, Debug, Deserialize, Serialize, PartialEq, Eq)]
#[serde(deny_unknown_fields)]
pub struct LocalRecoveryJob {
    pub job_id: String,
    pub version: u64,
    pub state: String,
    pub target_id: String,
    pub journal_present: bool,
    pub recorded_at: String,
    pub previous_version: Option<u64>,
    pub integrity: String,
}

#[derive(Deserialize)]
#[serde(deny_unknown_fields)]
struct RecoveryEnvelope {
    data: LocalRecoveryJob,
}

pub struct RecoveryHttpResponse {
    pub status: u16,
    pub content_type: Option<String>,
    pub content_length: Option<usize>,
    pub body: Vec<u8>,
}

pub trait LocalRecoveryTransport {
    fn is_ready(&self) -> bool;
    fn execute(
        &self,
        scope: &str,
        capability: &str,
        method: &str,
        path: &str,
        body: &[u8],
    ) -> Result<RecoveryHttpResponse, &'static str>;
}

pub struct CloudSecret(Zeroizing<String>);

impl CloudSecret {
    pub fn new(value: impl Into<String>) -> Self {
        Self(Zeroizing::new(value.into()))
    }

    fn expose(&self) -> &str {
        self.0.as_str()
    }

    #[cfg(feature = "contract-test")]
    #[doc(hidden)]
    pub fn reset_drop_audit_for_contract() {
        CLOUD_SECRET_DROP_COUNTER.store(0, Ordering::SeqCst);
    }

    #[cfg(feature = "contract-test")]
    #[doc(hidden)]
    pub fn drop_audit_for_contract() -> u64 {
        CLOUD_SECRET_DROP_COUNTER.load(Ordering::SeqCst)
    }
}

impl Drop for CloudSecret {
    fn drop(&mut self) {
        self.0.zeroize();
        #[cfg(feature = "contract-test")]
        CLOUD_SECRET_DROP_COUNTER.fetch_add(1, Ordering::SeqCst);
    }
}

impl fmt::Debug for CloudSecret {
    fn fmt(&self, formatter: &mut fmt::Formatter<'_>) -> fmt::Result {
        formatter.write_str("CloudSecret([redacted])")
    }
}

pub struct CloudRecoveryRequest {
    pub method: &'static str,
    pub path: String,
    pub query: Option<String>,
    pub body: Zeroizing<Vec<u8>>,
    pub idempotency_key: Option<CloudSecret>,
    pub if_match: Option<CloudSecret>,
}

impl CloudRecoveryRequest {
    fn retry_copy_for_get(&self) -> Option<Self> {
        (self.method == "GET").then(|| Self {
            method: self.method,
            path: self.path.clone(),
            query: self.query.clone(),
            body: Zeroizing::new(Vec::new()),
            idempotency_key: None,
            if_match: None,
        })
    }
}

impl fmt::Debug for CloudRecoveryRequest {
    fn fmt(&self, formatter: &mut fmt::Formatter<'_>) -> fmt::Result {
        formatter
            .debug_struct("CloudRecoveryRequest")
            .field("method", &self.method)
            .field("path", &"[redacted]")
            .field("query", &self.query.as_ref().map(|_| "[redacted]"))
            .field("body", &"[redacted]")
            .field(
                "idempotency_key",
                &self.idempotency_key.as_ref().map(|_| "[redacted]"),
            )
            .field("if_match", &self.if_match.as_ref().map(|_| "[redacted]"))
            .finish()
    }
}

impl Drop for CloudRecoveryRequest {
    fn drop(&mut self) {
        self.path.zeroize();
        if let Some(query) = &mut self.query {
            query.zeroize();
        }
        self.body.zeroize();
    }
}

struct CloudWireBuffer {
    bytes: Zeroizing<Vec<u8>>,
}

impl CloudWireBuffer {
    fn take_from(request: &mut CloudRecoveryRequest) -> Option<Self> {
        (!request.body.is_empty()).then(|| Self {
            bytes: Zeroizing::new(std::mem::take(&mut *request.body)),
        })
    }
}

impl AsRef<[u8]> for CloudWireBuffer {
    fn as_ref(&self) -> &[u8] {
        self.bytes.as_slice()
    }
}

impl Drop for CloudWireBuffer {
    fn drop(&mut self) {
        self.bytes.zeroize();
        #[cfg(feature = "contract-test")]
        CLOUD_WIRE_DROP_COUNTER.fetch_add(1, Ordering::SeqCst);
    }
}

pub struct CloudRecoveryResponse {
    pub status: u16,
    pub content_type: Option<String>,
    pub content_length: Option<usize>,
    pub etag: Option<String>,
    pub body: Vec<u8>,
}

struct ReflectionCanary {
    byte_len: usize,
    digest: [u8; 32],
}

impl ReflectionCanary {
    fn new(value: &[u8]) -> Self {
        Self {
            byte_len: value.len(),
            digest: Sha256::digest(value).into(),
        }
    }

    fn matches_within(&self, value: &str) -> bool {
        self.byte_len > 0
            && value.as_bytes().windows(self.byte_len).any(|candidate| {
                let digest: [u8; 32] = Sha256::digest(candidate).into();
                digest == self.digest
            })
    }
}

pub(crate) struct CloudResponseCanaries {
    values: [ReflectionCanary; 3],
}

impl CloudResponseCanaries {
    pub(crate) fn for_access(access: &[u8]) -> Self {
        let mut bearer = Zeroizing::new(b"Bearer ".to_vec());
        bearer.extend_from_slice(access);
        Self {
            values: [
                ReflectionCanary::new(access),
                ReflectionCanary::new(&bearer),
                ReflectionCanary::new(crate::native_session::PUBLIC_GATEWAY.as_bytes()),
            ],
        }
    }

    fn reflects(&self, value: &str) -> bool {
        self.values
            .iter()
            .any(|canary| canary.matches_within(value))
    }
}

pub(crate) struct CloudRecoveryExchange {
    pub(crate) response: CloudRecoveryResponse,
    pub(crate) canaries: CloudResponseCanaries,
}

impl Drop for CloudRecoveryResponse {
    fn drop(&mut self) {
        if let Some(value) = &mut self.content_type {
            value.zeroize();
        }
        if let Some(value) = &mut self.etag {
            value.zeroize();
        }
        self.body.zeroize();
    }
}

#[cfg(feature = "contract-test")]
pub trait CloudRecoveryTransport: Send + Sync {
    fn execute<'a>(
        &'a self,
        access: &'a [u8],
        request: CloudRecoveryRequest,
    ) -> Pin<Box<dyn Future<Output = Result<CloudRecoveryResponse, &'static str>> + Send + 'a>>;
}

#[cfg(not(feature = "contract-test"))]
pub(crate) trait CloudRecoveryTransport: Send + Sync {
    fn execute<'a>(
        &'a self,
        access: &'a [u8],
        request: CloudRecoveryRequest,
    ) -> Pin<Box<dyn Future<Output = Result<CloudRecoveryResponse, &'static str>> + Send + 'a>>;
}

pub struct NativeCloudRecoveryClient {
    client: reqwest::Client,
    gateway: String,
}

impl NativeCloudRecoveryClient {
    pub fn fixed() -> Result<Self, &'static str> {
        Self::for_gateway(crate::native_session::PUBLIC_GATEWAY)
    }

    #[cfg(feature = "contract-test")]
    #[doc(hidden)]
    pub fn reset_wire_drop_audit_for_contract() {
        CLOUD_WIRE_DROP_COUNTER.store(0, Ordering::SeqCst);
        CLOUD_WIRE_OWNER_ALIASES_SOURCE.store(false, Ordering::SeqCst);
    }

    #[cfg(feature = "contract-test")]
    #[doc(hidden)]
    pub fn wire_drop_audit_for_contract() -> u64 {
        CLOUD_WIRE_DROP_COUNTER.load(Ordering::SeqCst)
    }

    #[cfg(feature = "contract-test")]
    #[doc(hidden)]
    pub fn wire_body_owns_zeroizing_source_for_contract() -> bool {
        CLOUD_WIRE_OWNER_ALIASES_SOURCE.load(Ordering::SeqCst)
    }

    fn for_gateway(gateway: &str) -> Result<Self, &'static str> {
        if gateway != crate::native_session::PUBLIC_GATEWAY {
            return Err("CLOUD_RECOVERY_REQUEST_FAILED");
        }
        let client = reqwest::Client::builder()
            .https_only(true)
            .redirect(reqwest::redirect::Policy::none())
            .connect_timeout(std::time::Duration::from_secs(
                crate::native_session::CONNECT_TIMEOUT_SECONDS,
            ))
            .timeout(std::time::Duration::from_secs(
                crate::native_session::REQUEST_TIMEOUT_SECONDS,
            ))
            .build()
            .map_err(|_| "CLOUD_RECOVERY_REQUEST_FAILED")?;
        Ok(Self {
            client,
            gateway: gateway.to_owned(),
        })
    }

    #[cfg(feature = "contract-test")]
    #[doc(hidden)]
    pub fn for_contract_test(
        gateway: &str,
        timeout: std::time::Duration,
    ) -> Result<Self, &'static str> {
        if !gateway.starts_with("http://127.0.0.1:") || gateway.contains(['\r', '\n', '?', '#']) {
            return Err("CLOUD_RECOVERY_REQUEST_FAILED");
        }
        let client = reqwest::Client::builder()
            .redirect(reqwest::redirect::Policy::none())
            .connect_timeout(timeout)
            .timeout(timeout)
            .build()
            .map_err(|_| "CLOUD_RECOVERY_REQUEST_FAILED")?;
        Ok(Self {
            client,
            gateway: gateway.to_owned(),
        })
    }
}

impl fmt::Debug for NativeCloudRecoveryClient {
    fn fmt(&self, formatter: &mut fmt::Formatter<'_>) -> fmt::Result {
        formatter.write_str("NativeCloudRecoveryClient([redacted])")
    }
}

impl CloudRecoveryTransport for NativeCloudRecoveryClient {
    fn execute<'a>(
        &'a self,
        access: &'a [u8],
        mut request: CloudRecoveryRequest,
    ) -> Pin<Box<dyn Future<Output = Result<CloudRecoveryResponse, &'static str>> + Send + 'a>>
    {
        Box::pin(async move {
            // Move the app-owned allocation into a guard before any fallible wire setup so
            // normal completion, timeout, early error, and future cancellation share Drop.
            let wire_body = CloudWireBuffer::take_from(&mut request);
            if request.path.contains("//")
                || request.path.contains('?')
                || request.query.as_deref().is_some_and(|query| {
                    !valid_cloud_value(query, 320) || query.contains(['#', '?'])
                })
            {
                return Err("CLOUD_RECOVERY_REQUEST_FAILED");
            }
            let mut endpoint = format!("{}{}", self.gateway, request.path);
            if let Some(query) = &request.query {
                endpoint.push('?');
                endpoint.push_str(query);
            }
            let method = reqwest::Method::from_bytes(request.method.as_bytes())
                .map_err(|_| "CLOUD_RECOVERY_REQUEST_FAILED")?;
            let mut authorization = Zeroizing::new(b"Bearer ".to_vec());
            authorization.extend_from_slice(access);
            let mut authorization_header = reqwest::header::HeaderValue::from_bytes(&authorization)
                .map_err(|_| "AUTHENTICATION_REQUIRED")?;
            authorization_header.set_sensitive(true);
            let mut builder = self
                .client
                .request(method, endpoint)
                .header(reqwest::header::AUTHORIZATION, authorization_header)
                .header(reqwest::header::ACCEPT, "application/json");
            if let Some(wire) = wire_body {
                let source_pointer = wire.bytes.as_ptr();
                let request_body = reqwest::Body::from(bytes::Bytes::from_owner(wire));
                #[cfg(feature = "contract-test")]
                CLOUD_WIRE_OWNER_ALIASES_SOURCE.store(
                    request_body
                        .as_bytes()
                        .is_some_and(|bytes| bytes.as_ptr() == source_pointer),
                    Ordering::SeqCst,
                );
                builder = builder
                    .header(reqwest::header::CONTENT_TYPE, "application/json")
                    // Bytes retains CloudWireBuffer as its owner. All reqwest/hyper Bytes clones
                    // share this owner, which zeroizes the allocation when the last clone drops.
                    .body(request_body);
            }
            if let Some(value) = &request.idempotency_key {
                builder = builder.header("Idempotency-Key", value.expose());
            }
            if let Some(value) = &request.if_match {
                builder = builder.header(reqwest::header::IF_MATCH, value.expose());
            }
            let sent = builder.send().await;
            let mut response = sent.map_err(|_| "CLOUD_RECOVERY_REQUEST_FAILED")?;
            let status = response.status().as_u16();
            if status == 401 {
                return Err("AUTHENTICATION_REQUIRED");
            }
            if response.status().is_redirection()
                || response.headers().contains_key(reqwest::header::SET_COOKIE)
                || response
                    .headers()
                    .contains_key(reqwest::header::TRANSFER_ENCODING)
            {
                return Err("CLOUD_RECOVERY_RESPONSE_REJECTED");
            }
            let content_type = response
                .headers()
                .get(reqwest::header::CONTENT_TYPE)
                .and_then(|value| value.to_str().ok())
                .map(str::to_owned);
            let content_length = response
                .headers()
                .get(reqwest::header::CONTENT_LENGTH)
                .and_then(|value| value.to_str().ok())
                .and_then(|value| value.parse::<usize>().ok())
                .filter(|length| *length <= RESPONSE_MAX_BYTES)
                .ok_or("CLOUD_RECOVERY_RESPONSE_REJECTED")?;
            let etag = response
                .headers()
                .get(reqwest::header::ETAG)
                .and_then(|value| value.to_str().ok())
                .map(str::to_owned);
            let mut body = Zeroizing::new(Vec::with_capacity(content_length.min(64 * 1024)));
            while let Some(chunk) = response
                .chunk()
                .await
                .map_err(|_| "CLOUD_RECOVERY_RESPONSE_REJECTED")?
            {
                if body.len().saturating_add(chunk.len()) > RESPONSE_MAX_BYTES {
                    return Err("CLOUD_RECOVERY_RESPONSE_REJECTED");
                }
                body.extend_from_slice(&chunk);
            }
            if body.len() != content_length {
                return Err("CLOUD_RECOVERY_RESPONSE_REJECTED");
            }
            let body = std::mem::take(&mut *body);
            Ok(CloudRecoveryResponse {
                status,
                content_type,
                content_length: Some(content_length),
                etag,
                body,
            })
        })
    }
}

#[derive(Clone, Copy, PartialEq, Eq)]
enum CloudOperation {
    CreateBackup,
    ListBackups,
    GetBackup,
    PreviewRestore,
    GetRestore,
    ExecuteRestore,
    CancelRestore,
}

impl CloudOperation {
    fn is_get(self) -> bool {
        matches!(self, Self::ListBackups | Self::GetBackup | Self::GetRestore)
    }
}

#[derive(Clone, Deserialize, Serialize)]
#[serde(deny_unknown_fields)]
struct BackupProjection {
    backup_id: String,
    tenant_id: String,
    workspace_id: String,
    state: String,
    version: u64,
    trigger: String,
    created_at: String,
    verified_at: Option<String>,
    schema_revision: String,
    retention_watermark: String,
    manifest_digest: String,
    object_count: u64,
    transitions: Vec<String>,
}

#[derive(Clone, Deserialize, Serialize)]
#[serde(deny_unknown_fields)]
struct RestoreDestinationProjection {
    tenant_id: String,
    workspace_id: String,
    database_id: String,
    bucket_id: String,
}

#[derive(Clone, Deserialize, Serialize)]
#[serde(deny_unknown_fields)]
struct RestorePreviewProjection {
    version: u64,
    included_object_ids: Vec<String>,
    excluded_object_ids: Vec<String>,
    exclusion_reasons: Vec<(String, String)>,
    destination: RestoreDestinationProjection,
    created_at: String,
}

#[derive(Clone, Deserialize, Serialize)]
#[serde(deny_unknown_fields)]
struct RestoreProjection {
    request_id: String,
    backup_id: String,
    tenant_id: String,
    workspace_id: String,
    state: String,
    version: u64,
    preview: RestorePreviewProjection,
    transitions: Vec<String>,
    verification_digest: Option<String>,
}

#[derive(Clone, Serialize)]
#[serde(untagged)]
enum CloudProjectionData {
    Backup(BackupProjection),
    Backups(Vec<BackupProjection>),
    Restore(RestoreProjection),
}

#[derive(Clone, Serialize)]
pub struct CloudRecoveryProjection {
    data: CloudProjectionData,
    etag: Option<String>,
}

impl fmt::Debug for CloudRecoveryProjection {
    fn fmt(&self, formatter: &mut fmt::Formatter<'_>) -> fmt::Result {
        let kind = match &self.data {
            CloudProjectionData::Backup(_) => "backup",
            CloudProjectionData::Backups(_) => "backup-list",
            CloudProjectionData::Restore(_) => "restore",
        };
        formatter
            .debug_struct("CloudRecoveryProjection")
            .field("kind", &kind)
            .field("has_etag", &self.etag.is_some())
            .finish()
    }
}

const CONSUMPTION_CACHE_LIMIT: usize = 128;

#[derive(Default)]
struct ConsumptionCache {
    digests: HashSet<[u8; 32]>,
    order: VecDeque<[u8; 32]>,
}

impl ConsumptionCache {
    fn consume(&mut self, value: &str) -> bool {
        let digest: [u8; 32] = Sha256::digest(value.as_bytes()).into();
        self.consume_digest(digest)
    }

    fn consume_digest(&mut self, digest: [u8; 32]) -> bool {
        if self.digests.contains(&digest) {
            return false;
        }
        if self.order.len() == CONSUMPTION_CACHE_LIMIT {
            if let Some(expired) = self.order.pop_front() {
                self.digests.remove(&expired);
            }
        }
        self.order.push_back(digest);
        self.digests.insert(digest)
    }

    #[cfg(feature = "contract-test")]
    fn contains_raw_probe(&self, probe: &str) -> bool {
        self.digests
            .iter()
            .any(|stored| stored.as_slice() == probe.as_bytes())
    }
}

pub struct NativeRecoveryRuntime {
    transport: Arc<dyn CloudRecoveryTransport>,
    used_idempotency: Arc<Mutex<ConsumptionCache>>,
    used_step_up: Arc<Mutex<ConsumptionCache>>,
}

impl NativeRecoveryRuntime {
    pub fn new() -> Self {
        Self {
            transport: Arc::new(
                NativeCloudRecoveryClient::fixed()
                    .expect("fixed Native recovery public gateway must build"),
            ),
            used_idempotency: Arc::new(Mutex::new(ConsumptionCache::default())),
            used_step_up: Arc::new(Mutex::new(ConsumptionCache::default())),
        }
    }

    #[cfg(feature = "contract-test")]
    #[doc(hidden)]
    pub fn for_contract_test(transport: Arc<dyn CloudRecoveryTransport>) -> Self {
        Self {
            transport,
            used_idempotency: Arc::new(Mutex::new(ConsumptionCache::default())),
            used_step_up: Arc::new(Mutex::new(ConsumptionCache::default())),
        }
    }

    fn port<'a>(
        &'a self,
        session: &'a crate::native_session::NativeSessionRuntime,
    ) -> CloudRecoveryPort<'a> {
        CloudRecoveryPort::with_consumption_caches(
            session,
            self.transport.as_ref(),
            Arc::clone(&self.used_idempotency),
            Arc::clone(&self.used_step_up),
        )
    }

    pub async fn cloud_create_backup(
        &self,
        session: &crate::native_session::NativeSessionRuntime,
        input: CloudCreateBackupCommandInput,
    ) -> Result<CloudRecoveryProjection, LocalRecoveryError> {
        let body = encode_cloud_body(&BackupInput {
            workspace_id: input.workspace_id,
            trigger: input.trigger,
            schema_revision: input.schema_revision,
            retention_watermark: input.retention_watermark,
            objects: input.objects,
        })?;
        self.port(session)
            .execute(CloudRecoveryRequest {
                method: "POST",
                path: "/api/v1/backups".to_owned(),
                query: None,
                body,
                idempotency_key: Some(input.idempotency_key.into_cloud_secret()),
                if_match: None,
            })
            .await
    }

    pub async fn cloud_list_backups(
        &self,
        session: &crate::native_session::NativeSessionRuntime,
        input: CloudListBackupsCommandInput,
    ) -> Result<CloudRecoveryProjection, LocalRecoveryError> {
        self.port(session)
            .execute(CloudRecoveryRequest {
                method: "GET",
                path: "/api/v1/backups".to_owned(),
                query: Some(format!("workspace_id={}", input.workspace_id)),
                body: Zeroizing::new(Vec::new()),
                idempotency_key: None,
                if_match: None,
            })
            .await
    }

    pub async fn cloud_get_backup(
        &self,
        session: &crate::native_session::NativeSessionRuntime,
        input: CloudGetBackupCommandInput,
    ) -> Result<CloudRecoveryProjection, LocalRecoveryError> {
        self.port(session)
            .execute(CloudRecoveryRequest {
                method: "GET",
                path: format!("/api/v1/backups/{}", input.backup_id),
                query: None,
                body: Zeroizing::new(Vec::new()),
                idempotency_key: None,
                if_match: None,
            })
            .await
    }

    pub async fn cloud_preview_restore(
        &self,
        session: &crate::native_session::NativeSessionRuntime,
        input: CloudPreviewRestoreCommandInput,
    ) -> Result<CloudRecoveryProjection, LocalRecoveryError> {
        let body = encode_cloud_body(&PreviewInput {
            destination: input.destination,
            step_up_authorization_id: input.step_up_authorization_id,
        })?;
        self.port(session)
            .execute(CloudRecoveryRequest {
                method: "POST",
                path: format!("/api/v1/backups/{}/restore-previews", input.backup_id),
                query: None,
                body,
                idempotency_key: Some(input.idempotency_key.into_cloud_secret()),
                if_match: None,
            })
            .await
    }

    pub async fn cloud_get_restore(
        &self,
        session: &crate::native_session::NativeSessionRuntime,
        input: CloudGetRestoreCommandInput,
    ) -> Result<CloudRecoveryProjection, LocalRecoveryError> {
        self.port(session)
            .execute(CloudRecoveryRequest {
                method: "GET",
                path: format!("/api/v1/restore-requests/{}", input.restore_request_id),
                query: None,
                body: Zeroizing::new(Vec::new()),
                idempotency_key: None,
                if_match: None,
            })
            .await
    }

    pub async fn cloud_execute_restore(
        &self,
        session: &crate::native_session::NativeSessionRuntime,
        input: CloudExecuteRestoreCommandInput,
    ) -> Result<CloudRecoveryProjection, LocalRecoveryError> {
        let body = encode_cloud_body(&ExecuteInput {
            preview_version: input.preview_version,
            step_up_authorization_id: input.step_up_authorization_id,
        })?;
        self.port(session)
            .execute(CloudRecoveryRequest {
                method: "POST",
                path: format!(
                    "/api/v1/restore-requests/{}/execute",
                    input.restore_request_id
                ),
                query: None,
                body,
                idempotency_key: Some(input.idempotency_key.into_cloud_secret()),
                if_match: Some(input.if_match.into_cloud_secret()),
            })
            .await
    }

    pub async fn cloud_cancel_restore(
        &self,
        session: &crate::native_session::NativeSessionRuntime,
        input: CloudCancelRestoreCommandInput,
    ) -> Result<CloudRecoveryProjection, LocalRecoveryError> {
        self.port(session)
            .execute(CloudRecoveryRequest {
                method: "POST",
                path: format!(
                    "/api/v1/restore-requests/{}/cancel",
                    input.restore_request_id
                ),
                query: None,
                body: Zeroizing::new(Vec::new()),
                idempotency_key: Some(input.idempotency_key.into_cloud_secret()),
                if_match: Some(input.if_match.into_cloud_secret()),
            })
            .await
    }
}

fn encode_cloud_body(value: &impl Serialize) -> Result<Zeroizing<Vec<u8>>, LocalRecoveryError> {
    serde_json::to_vec(value)
        .map(Zeroizing::new)
        .map_err(|_| LocalRecoveryError::new("CLOUD_RECOVERY_INPUT_INVALID", false))
}

pub struct CloudRecoveryPort<'a> {
    session: &'a crate::native_session::NativeSessionRuntime,
    transport: &'a dyn CloudRecoveryTransport,
    used_idempotency: Arc<Mutex<ConsumptionCache>>,
    used_step_up: Arc<Mutex<ConsumptionCache>>,
}

impl<'a> CloudRecoveryPort<'a> {
    pub fn new(
        session: &'a crate::native_session::NativeSessionRuntime,
        transport: &'a dyn CloudRecoveryTransport,
    ) -> Self {
        Self {
            session,
            transport,
            used_idempotency: Arc::new(Mutex::new(ConsumptionCache::default())),
            used_step_up: Arc::new(Mutex::new(ConsumptionCache::default())),
        }
    }

    fn with_consumption_caches(
        session: &'a crate::native_session::NativeSessionRuntime,
        transport: &'a dyn CloudRecoveryTransport,
        used_idempotency: Arc<Mutex<ConsumptionCache>>,
        used_step_up: Arc<Mutex<ConsumptionCache>>,
    ) -> Self {
        Self {
            session,
            transport,
            used_idempotency,
            used_step_up,
        }
    }

    #[cfg(feature = "contract-test")]
    #[doc(hidden)]
    pub fn consumption_cache_state_for_contract(
        &self,
        idempotency_probe: &str,
        step_up_probe: &str,
    ) -> (usize, usize, bool) {
        let idempotency = self.used_idempotency.lock().expect("idempotency cache");
        let step_up = self.used_step_up.lock().expect("step-up cache");
        (
            idempotency.digests.len(),
            step_up.digests.len(),
            idempotency.contains_raw_probe(idempotency_probe)
                || step_up.contains_raw_probe(step_up_probe),
        )
    }

    pub async fn execute(
        &self,
        request: CloudRecoveryRequest,
    ) -> Result<CloudRecoveryProjection, LocalRecoveryError> {
        let context = validate_cloud_request(&request, &self.used_idempotency, &self.used_step_up)?;
        let operation = context.operation;
        let retry_request = request.retry_copy_for_get();
        let first = self
            .session
            .execute_cloud_once(self.transport, request)
            .await;
        let response = match first {
            Err("AUTHENTICATION_REQUIRED") if operation.is_get() => {
                self.session
                    .refresh_once()
                    .await
                    .map_err(|_| LocalRecoveryError::new("AUTHENTICATION_REQUIRED", false))?;
                self.session
                    .execute_cloud_once(
                        self.transport,
                        retry_request.expect("GET request has a retry copy"),
                    )
                    .await
            }
            Err("AUTHENTICATION_REQUIRED") => {
                self.session
                    .refresh_once()
                    .await
                    .map_err(|_| LocalRecoveryError::new("AUTHENTICATION_REQUIRED", false))?;
                Err("AUTHENTICATION_REQUIRED")
            }
            result => result,
        }
        .map_err(|code| match code {
            "AUTHENTICATION_REQUIRED" => LocalRecoveryError::new("AUTHENTICATION_REQUIRED", false),
            "CLOUD_RECOVERY_RESPONSE_REJECTED" => LocalRecoveryError::new(code, false),
            _ => LocalRecoveryError::new("CLOUD_RECOVERY_REQUEST_FAILED", operation.is_get()),
        })?;
        validate_cloud_response(&context, response)
    }
}

struct CloudRequestContext {
    operation: CloudOperation,
    workspace_id: Option<String>,
    resource_id: Option<String>,
    destination: Option<RestoreDestinationInput>,
}

fn classify_cloud_operation(request: &CloudRecoveryRequest) -> Option<CloudOperation> {
    match (request.method, request.path.as_str()) {
        ("POST", "/api/v1/backups") => Some(CloudOperation::CreateBackup),
        ("GET", "/api/v1/backups") => Some(CloudOperation::ListBackups),
        ("GET", path) if cloud_id_path(path, "/api/v1/backups/", "") => {
            Some(CloudOperation::GetBackup)
        }
        ("POST", path) if cloud_id_path(path, "/api/v1/backups/", "/restore-previews") => {
            Some(CloudOperation::PreviewRestore)
        }
        ("GET", path) if cloud_id_path(path, "/api/v1/restore-requests/", "") => {
            Some(CloudOperation::GetRestore)
        }
        ("POST", path) if cloud_id_path(path, "/api/v1/restore-requests/", "/execute") => {
            Some(CloudOperation::ExecuteRestore)
        }
        ("POST", path) if cloud_id_path(path, "/api/v1/restore-requests/", "/cancel") => {
            Some(CloudOperation::CancelRestore)
        }
        _ => None,
    }
}

fn valid_cloud_value(value: &str, max: usize) -> bool {
    !value.is_empty()
        && value.len() <= max
        && !value.bytes().any(|byte| matches!(byte, b'\r' | b'\n' | 0))
}

fn cloud_id_path(path: &str, prefix: &str, suffix: &str) -> bool {
    path.strip_prefix(prefix)
        .and_then(|rest| rest.strip_suffix(suffix))
        .is_some_and(valid_cloud_id)
}

fn cloud_path_id(path: &str, prefix: &str, suffix: &str) -> Option<String> {
    path.strip_prefix(prefix)
        .and_then(|rest| rest.strip_suffix(suffix))
        .filter(|id| valid_cloud_id(id))
        .map(str::to_owned)
}

fn valid_cloud_id(id: &str) -> bool {
    valid_cloud_value(id, 256)
        && id.bytes().enumerate().all(|(i, b)| {
            b.is_ascii_alphanumeric() || (i > 0 && matches!(b, b'.' | b'_' | b':' | b'-'))
        })
}

#[derive(Deserialize, Serialize)]
#[serde(transparent)]
struct SensitiveInput(String);

impl SensitiveInput {
    fn into_cloud_secret(mut self) -> CloudSecret {
        CloudSecret::new(std::mem::take(&mut self.0))
    }
}

impl Drop for SensitiveInput {
    fn drop(&mut self) {
        self.0.zeroize();
        #[cfg(feature = "contract-test")]
        CLOUD_SECRET_DROP_COUNTER.fetch_add(1, Ordering::SeqCst);
    }
}

#[derive(Deserialize, Serialize)]
#[serde(deny_unknown_fields)]
struct BackupObjectInput {
    object_id: String,
    checksum_sha256: String,
    #[serde(rename = "byte_size")]
    _byte_size: u64,
}

#[derive(Deserialize, Serialize)]
#[serde(deny_unknown_fields)]
struct BackupInput {
    workspace_id: String,
    trigger: String,
    schema_revision: String,
    retention_watermark: String,
    objects: Vec<BackupObjectInput>,
}

#[derive(Clone, Deserialize, Serialize, PartialEq, Eq)]
#[serde(deny_unknown_fields)]
struct RestoreDestinationInput {
    tenant_id: String,
    workspace_id: String,
    database_id: String,
    bucket_id: String,
}

#[derive(Deserialize, Serialize)]
#[serde(deny_unknown_fields)]
struct PreviewInput {
    destination: RestoreDestinationInput,
    step_up_authorization_id: SensitiveInput,
}

#[derive(Deserialize, Serialize)]
#[serde(deny_unknown_fields)]
struct ExecuteInput {
    preview_version: u64,
    step_up_authorization_id: SensitiveInput,
}

#[derive(Deserialize)]
#[serde(deny_unknown_fields)]
pub struct CloudCreateBackupCommandInput {
    workspace_id: String,
    trigger: String,
    schema_revision: String,
    retention_watermark: String,
    objects: Vec<BackupObjectInput>,
    idempotency_key: SensitiveInput,
}

#[derive(Deserialize)]
#[serde(deny_unknown_fields)]
pub struct CloudListBackupsCommandInput {
    workspace_id: String,
}

#[derive(Deserialize)]
#[serde(deny_unknown_fields)]
pub struct CloudGetBackupCommandInput {
    backup_id: String,
}

#[derive(Deserialize)]
#[serde(deny_unknown_fields)]
pub struct CloudPreviewRestoreCommandInput {
    backup_id: String,
    destination: RestoreDestinationInput,
    step_up_authorization_id: SensitiveInput,
    idempotency_key: SensitiveInput,
}

#[derive(Deserialize)]
#[serde(deny_unknown_fields)]
pub struct CloudGetRestoreCommandInput {
    restore_request_id: String,
}

#[derive(Deserialize)]
#[serde(deny_unknown_fields)]
pub struct CloudExecuteRestoreCommandInput {
    restore_request_id: String,
    preview_version: u64,
    step_up_authorization_id: SensitiveInput,
    idempotency_key: SensitiveInput,
    if_match: SensitiveInput,
}

#[derive(Deserialize)]
#[serde(deny_unknown_fields)]
pub struct CloudCancelRestoreCommandInput {
    restore_request_id: String,
    idempotency_key: SensitiveInput,
    if_match: SensitiveInput,
}

#[derive(Deserialize)]
#[serde(deny_unknown_fields)]
pub struct LocalStartScanCommandInput {
    workspace_id: String,
    target_id: String,
    snapshot_checksum: String,
    metadata_checksum: String,
    actual_checksum: String,
    journal_present: bool,
}

#[derive(Deserialize)]
#[serde(deny_unknown_fields)]
pub struct LocalGetJobCommandInput {
    job_id: String,
}

#[derive(Deserialize)]
#[serde(deny_unknown_fields)]
pub struct LocalRepairJobCommandInput {
    job_id: String,
    workspace_id: String,
    expected_version: u64,
}

fn valid_cloud_write_body(request: &CloudRecoveryRequest) -> bool {
    if request.path.ends_with("/cancel") {
        return request.body.is_empty();
    }
    if request.path == "/api/v1/backups" {
        let Ok(input) = serde_json::from_slice::<BackupInput>(&request.body) else {
            return false;
        };
        return valid_cloud_id(&input.workspace_id)
            && matches!(input.trigger.as_str(), "automatic" | "manual")
            && valid_cloud_id(&input.schema_revision)
            && valid_cloud_id(&input.retention_watermark)
            && !input.objects.is_empty()
            && input.objects.len() <= 10_000
            && input.objects.iter().all(|item| {
                valid_cloud_id(&item.object_id) && validate_digest(&item.checksum_sha256)
            });
    }
    if request.path.ends_with("/restore-previews") {
        let Ok(input) = serde_json::from_slice::<PreviewInput>(&request.body) else {
            return false;
        };
        let destination = input.destination;
        return [
            &destination.tenant_id,
            &destination.workspace_id,
            &destination.database_id,
            &destination.bucket_id,
        ]
        .into_iter()
        .all(|id| id.starts_with("fixture-") && valid_cloud_id(id))
            && valid_cloud_value(&input.step_up_authorization_id.0, 512);
    }
    if request.path.ends_with("/execute") {
        let Ok(input) = serde_json::from_slice::<ExecuteInput>(&request.body) else {
            return false;
        };
        return input.preview_version > 0
            && valid_cloud_value(&input.step_up_authorization_id.0, 512);
    }
    false
}

fn cloud_step_up_digest(request: &CloudRecoveryRequest) -> Option<[u8; 32]> {
    let step = if request.path.ends_with("/restore-previews") {
        serde_json::from_slice::<PreviewInput>(&request.body)
            .ok()?
            .step_up_authorization_id
    } else if request.path.ends_with("/execute") {
        serde_json::from_slice::<ExecuteInput>(&request.body)
            .ok()?
            .step_up_authorization_id
    } else {
        return None;
    };
    Some(Sha256::digest(step.0.as_bytes()).into())
}

fn validate_cloud_request(
    request: &CloudRecoveryRequest,
    used_idempotency: &Mutex<ConsumptionCache>,
    used_step_up: &Mutex<ConsumptionCache>,
) -> Result<CloudRequestContext, LocalRecoveryError> {
    if request.body.len() > CLOUD_BODY_MAX_BYTES
        || request.path.contains('?')
        || request.path.contains("://")
    {
        return Err(LocalRecoveryError::new(
            "CLOUD_RECOVERY_INPUT_INVALID",
            false,
        ));
    }
    let operation = classify_cloud_operation(request)
        .ok_or_else(|| LocalRecoveryError::new("CLOUD_RECOVERY_INPUT_INVALID", false))?;
    let allowed = match operation {
        CloudOperation::CreateBackup => request.query.is_none() && !request.body.is_empty(),
        CloudOperation::ListBackups => {
            request.body.is_empty()
                && request
                    .query
                    .as_deref()
                    .is_some_and(|q| q.strip_prefix("workspace_id=").is_some_and(valid_cloud_id))
        }
        CloudOperation::GetBackup | CloudOperation::GetRestore => {
            request.query.is_none() && request.body.is_empty()
        }
        CloudOperation::PreviewRestore => request.query.is_none() && !request.body.is_empty(),
        CloudOperation::ExecuteRestore => {
            request.query.is_none()
                && !request.body.is_empty()
                && request
                    .if_match
                    .as_ref()
                    .map(CloudSecret::expose)
                    .is_some_and(|v| valid_restore_if_match(v, &request.path))
        }
        CloudOperation::CancelRestore => {
            request.query.is_none()
                && request.body.is_empty()
                && request
                    .if_match
                    .as_ref()
                    .map(CloudSecret::expose)
                    .is_some_and(|v| valid_restore_if_match(v, &request.path))
        }
    };
    if !allowed {
        return Err(LocalRecoveryError::new(
            "CLOUD_RECOVERY_INPUT_INVALID",
            false,
        ));
    }
    if request.method == "POST" && !valid_cloud_write_body(request) {
        return Err(LocalRecoveryError::new(
            "CLOUD_RECOVERY_INPUT_INVALID",
            false,
        ));
    }
    if request.method == "POST" {
        let key = request
            .idempotency_key
            .as_ref()
            .map(CloudSecret::expose)
            .filter(|v| valid_idempotency_key(v))
            .ok_or_else(|| LocalRecoveryError::new("CLOUD_RECOVERY_INPUT_INVALID", false))?;
        if !used_idempotency
            .lock()
            .map_err(|_| LocalRecoveryError::new("CLOUD_RECOVERY_INPUT_INVALID", false))?
            .consume(key)
        {
            return Err(LocalRecoveryError::new(
                "CLOUD_RECOVERY_INPUT_INVALID",
                false,
            ));
        }
    } else if request.idempotency_key.is_some() || request.if_match.is_some() {
        return Err(LocalRecoveryError::new(
            "CLOUD_RECOVERY_INPUT_INVALID",
            false,
        ));
    }
    if request.path.ends_with("/restore-previews") || request.path.ends_with("/execute") {
        let digest = cloud_step_up_digest(request)
            .ok_or_else(|| LocalRecoveryError::new("CLOUD_RECOVERY_INPUT_INVALID", false))?;
        if !used_step_up
            .lock()
            .map_err(|_| LocalRecoveryError::new("CLOUD_RECOVERY_INPUT_INVALID", false))?
            .consume_digest(digest)
        {
            return Err(LocalRecoveryError::new(
                "CLOUD_RECOVERY_INPUT_INVALID",
                false,
            ));
        }
    }
    let (workspace_id, resource_id, destination) = match operation {
        CloudOperation::CreateBackup => {
            let input: BackupInput = serde_json::from_slice(&request.body)
                .map_err(|_| LocalRecoveryError::new("CLOUD_RECOVERY_INPUT_INVALID", false))?;
            (Some(input.workspace_id), None, None)
        }
        CloudOperation::ListBackups => (
            request
                .query
                .as_deref()
                .and_then(|value| value.strip_prefix("workspace_id="))
                .map(str::to_owned),
            None,
            None,
        ),
        CloudOperation::GetBackup => (
            None,
            cloud_path_id(&request.path, "/api/v1/backups/", ""),
            None,
        ),
        CloudOperation::PreviewRestore => {
            let input: PreviewInput = serde_json::from_slice(&request.body)
                .map_err(|_| LocalRecoveryError::new("CLOUD_RECOVERY_INPUT_INVALID", false))?;
            (
                None,
                cloud_path_id(&request.path, "/api/v1/backups/", "/restore-previews"),
                Some(input.destination),
            )
        }
        CloudOperation::GetRestore => (
            None,
            cloud_path_id(&request.path, "/api/v1/restore-requests/", ""),
            None,
        ),
        CloudOperation::ExecuteRestore => (
            None,
            cloud_path_id(&request.path, "/api/v1/restore-requests/", "/execute"),
            None,
        ),
        CloudOperation::CancelRestore => (
            None,
            cloud_path_id(&request.path, "/api/v1/restore-requests/", "/cancel"),
            None,
        ),
    };
    Ok(CloudRequestContext {
        operation,
        workspace_id,
        resource_id,
        destination,
    })
}

fn valid_idempotency_key(value: &str) -> bool {
    (16..=128).contains(&value.len())
        && value.bytes().enumerate().all(|(index, byte)| {
            byte.is_ascii_alphanumeric() || (index > 0 && matches!(byte, b'.' | b'_' | b':' | b'-'))
        })
}

fn valid_restore_if_match(value: &str, path: &str) -> bool {
    let request_id = path
        .strip_prefix("/api/v1/restore-requests/")
        .and_then(|rest| rest.split('/').next())
        .filter(|id| valid_cloud_id(id));
    let Some(inner) = value.strip_prefix('"').and_then(|v| v.strip_suffix('"')) else {
        return false;
    };
    let mut parts = inner.split(':');
    matches!(parts.next(), Some("restore"))
        && parts.next() == request_id
        && parts
            .next()
            .and_then(|version| version.parse::<u64>().ok())
            .is_some_and(|version| version > 0)
        && parts.next().is_none()
}

fn validate_cloud_response(
    context: &CloudRequestContext,
    exchange: CloudRecoveryExchange,
) -> Result<CloudRecoveryProjection, LocalRecoveryError> {
    let operation = context.operation;
    let CloudRecoveryExchange {
        mut response,
        canaries,
    } = exchange;
    let content_type_ok = response.content_type.as_deref().is_some_and(|v| {
        v.eq_ignore_ascii_case("application/json")
            || v.to_ascii_lowercase().starts_with("application/json;")
    }) && response
        .content_type
        .as_deref()
        .is_none_or(|value| !canaries.reflects(value));
    if !content_type_ok
        || response.body.len() > CLOUD_BODY_MAX_BYTES
        || response.content_length != Some(response.body.len())
    {
        response.body.zeroize();
        return Err(LocalRecoveryError::new(
            "CLOUD_RECOVERY_RESPONSE_REJECTED",
            false,
        ));
    }
    if !(200..300).contains(&response.status) {
        #[derive(Deserialize)]
        #[serde(deny_unknown_fields)]
        struct SafeErrorEnvelope {
            error: SafeError,
        }
        #[derive(Deserialize)]
        #[serde(deny_unknown_fields)]
        struct SafeError {
            code: String,
            message: String,
            stage: String,
            impact: String,
            retryable: bool,
            user_action: String,
            trace_id: String,
            details: std::collections::BTreeMap<String, serde_json::Value>,
        }
        let parsed: SafeErrorEnvelope = serde_json::from_slice(&response.body)
            .map_err(|_| LocalRecoveryError::new("CLOUD_RECOVERY_RESPONSE_REJECTED", false))?;
        response.body.zeroize();
        let error = parsed.error;
        let safe_code = match error.code.as_str() {
            "FORBIDDEN" => "FORBIDDEN",
            "CURRENT_ACCESS_DENIED" => "CURRENT_ACCESS_DENIED",
            "STEP_UP_REQUIRED" => "STEP_UP_REQUIRED",
            "INVALID_REQUEST" => "INVALID_REQUEST",
            "RESOURCE_UNAVAILABLE" => "RESOURCE_UNAVAILABLE",
            "CONFLICT" => "CONFLICT",
            "NOT_FOUND" => "NOT_FOUND",
            _ => {
                return Err(LocalRecoveryError::new(
                    "CLOUD_RECOVERY_RESPONSE_REJECTED",
                    false,
                ));
            }
        };
        if !safe_cloud_string(&canaries, &error.message, 4096)
            || !safe_cloud_string(&canaries, &error.stage, 128)
            || !safe_cloud_string(&canaries, &error.impact, 256)
            || !safe_cloud_string(&canaries, &error.user_action, 4096)
            || !safe_cloud_string(&canaries, &error.trace_id, 256)
            || !error.details.is_empty()
        {
            return Err(LocalRecoveryError::new(
                "CLOUD_RECOVERY_RESPONSE_REJECTED",
                false,
            ));
        }
        return Err(LocalRecoveryError::with_trace(
            safe_code,
            error.trace_id,
            operation.is_get() && error.retryable,
        ));
    }
    let expected_status = match operation {
        CloudOperation::CreateBackup | CloudOperation::PreviewRestore => 201,
        _ => 200,
    };
    if response.status != expected_status {
        return Err(LocalRecoveryError::new(
            "CLOUD_RECOVERY_RESPONSE_REJECTED",
            false,
        ));
    }
    #[derive(Deserialize)]
    #[serde(deny_unknown_fields)]
    struct Envelope<T> {
        data: T,
        meta: CloudResponseMeta,
    }
    #[derive(Deserialize)]
    #[serde(deny_unknown_fields)]
    struct CloudResponseMeta {
        trace_id: String,
    }
    let parse_rejected = || LocalRecoveryError::new("CLOUD_RECOVERY_RESPONSE_REJECTED", false);
    let (data, resource_id, version) = match operation {
        CloudOperation::ListBackups => {
            let parsed: Envelope<Vec<BackupProjection>> =
                serde_json::from_slice(&response.body).map_err(|_| parse_rejected())?;
            if !safe_cloud_string(&canaries, &parsed.meta.trace_id, 256)
                || !parsed
                    .data
                    .iter()
                    .all(|value| valid_backup_projection(value, &canaries))
                || parsed.data.iter().any(|value| {
                    context.workspace_id.as_deref() != Some(value.workspace_id.as_str())
                })
            {
                return Err(parse_rejected());
            }
            (CloudProjectionData::Backups(parsed.data), None, None)
        }
        CloudOperation::CreateBackup | CloudOperation::GetBackup => {
            let parsed: Envelope<BackupProjection> =
                serde_json::from_slice(&response.body).map_err(|_| parse_rejected())?;
            if !safe_cloud_string(&canaries, &parsed.meta.trace_id, 256)
                || !valid_backup_projection(&parsed.data, &canaries)
                || (operation == CloudOperation::CreateBackup
                    && context.workspace_id.as_deref() != Some(parsed.data.workspace_id.as_str()))
                || (operation == CloudOperation::GetBackup
                    && context.resource_id.as_deref() != Some(parsed.data.backup_id.as_str()))
            {
                return Err(parse_rejected());
            }
            let id = parsed.data.backup_id.clone();
            let version = parsed.data.version;
            (
                CloudProjectionData::Backup(parsed.data),
                Some(("backup", id)),
                Some(version),
            )
        }
        _ => {
            let parsed: Envelope<RestoreProjection> =
                serde_json::from_slice(&response.body).map_err(|_| parse_rejected())?;
            if !safe_cloud_string(&canaries, &parsed.meta.trace_id, 256)
                || !valid_restore_projection(&parsed.data, &canaries)
                || (operation == CloudOperation::PreviewRestore
                    && (context.resource_id.as_deref() != Some(parsed.data.backup_id.as_str())
                        || context.destination.as_ref().is_none_or(|expected| {
                            !same_destination(expected, &parsed.data.preview.destination)
                        })))
                || (matches!(
                    operation,
                    CloudOperation::GetRestore
                        | CloudOperation::ExecuteRestore
                        | CloudOperation::CancelRestore
                ) && context.resource_id.as_deref() != Some(parsed.data.request_id.as_str()))
            {
                return Err(parse_rejected());
            }
            let id = parsed.data.request_id.clone();
            let version = parsed.data.version;
            (
                CloudProjectionData::Restore(parsed.data),
                Some(("restore", id)),
                Some(version),
            )
        }
    };
    response.body.zeroize();
    if response
        .etag
        .as_deref()
        .is_some_and(|value| canaries.reflects(value))
        || !valid_cloud_etag(
            operation,
            response.etag.as_deref(),
            resource_id.as_ref(),
            version,
        )
    {
        return Err(LocalRecoveryError::new(
            "CLOUD_RECOVERY_RESPONSE_REJECTED",
            false,
        ));
    }
    Ok(CloudRecoveryProjection {
        data,
        etag: response.etag.take(),
    })
}

fn safe_cloud_string(canaries: &CloudResponseCanaries, value: &str, max: usize) -> bool {
    valid_cloud_value(value, max) && !canaries.reflects(value)
}

fn valid_backup_projection(value: &BackupProjection, canaries: &CloudResponseCanaries) -> bool {
    safe_cloud_string(canaries, &value.backup_id, 256)
        && valid_cloud_id(&value.backup_id)
        && safe_cloud_string(canaries, &value.tenant_id, 256)
        && valid_cloud_id(&value.tenant_id)
        && safe_cloud_string(canaries, &value.workspace_id, 256)
        && valid_cloud_id(&value.workspace_id)
        && matches!(
            value.state.as_str(),
            "queued" | "capturing" | "verifying" | "ready" | "failed" | "expired"
        )
        && !canaries.reflects(&value.state)
        && value.version > 0
        && matches!(value.trigger.as_str(), "automatic" | "manual")
        && !canaries.reflects(&value.trigger)
        && !canaries.reflects(&value.created_at)
        && OffsetDateTime::parse(&value.created_at, &Rfc3339).is_ok()
        && value
            .verified_at
            .as_deref()
            .is_none_or(|v| !canaries.reflects(v) && OffsetDateTime::parse(v, &Rfc3339).is_ok())
        && safe_cloud_string(canaries, &value.schema_revision, 256)
        && valid_cloud_id(&value.schema_revision)
        && safe_cloud_string(canaries, &value.retention_watermark, 256)
        && valid_cloud_id(&value.retention_watermark)
        && !canaries.reflects(&value.manifest_digest)
        && validate_digest(&value.manifest_digest)
        && value.transitions.len() <= 32
        && value.transitions.iter().all(|state| {
            matches!(
                state.as_str(),
                "queued" | "capturing" | "verifying" | "ready" | "failed" | "expired"
            )
        })
        && value
            .transitions
            .iter()
            .all(|state| !canaries.reflects(state))
}

fn valid_restore_projection(value: &RestoreProjection, canaries: &CloudResponseCanaries) -> bool {
    let destination = &value.preview.destination;
    valid_cloud_id(&value.request_id)
        && valid_cloud_id(&value.backup_id)
        && valid_cloud_id(&value.tenant_id)
        && valid_cloud_id(&value.workspace_id)
        && matches!(
            value.state.as_str(),
            "requested"
                | "preview_ready"
                | "authorized"
                | "restoring"
                | "completed"
                | "failed"
                | "cancelled"
        )
        && !canaries.reflects(&value.state)
        && value.version > 0
        && value.preview.version > 0
        && value
            .preview
            .included_object_ids
            .iter()
            .all(|id| valid_cloud_id(id) && !canaries.reflects(id))
        && value
            .preview
            .excluded_object_ids
            .iter()
            .all(|id| valid_cloud_id(id) && !canaries.reflects(id))
        && value.preview.exclusion_reasons.iter().all(|(id, reason)| {
            valid_cloud_id(id)
                && !canaries.reflects(id)
                && safe_cloud_string(canaries, reason, 4096)
        })
        && [
            &value.request_id,
            &value.backup_id,
            &value.tenant_id,
            &value.workspace_id,
        ]
        .into_iter()
        .all(|item| !canaries.reflects(item))
        && valid_cloud_id(&destination.tenant_id)
        && valid_cloud_id(&destination.workspace_id)
        && valid_cloud_id(&destination.database_id)
        && valid_cloud_id(&destination.bucket_id)
        && [
            &destination.tenant_id,
            &destination.workspace_id,
            &destination.database_id,
            &destination.bucket_id,
        ]
        .into_iter()
        .all(|id| id.starts_with("fixture-") && !canaries.reflects(id))
        && !canaries.reflects(&value.preview.created_at)
        && OffsetDateTime::parse(&value.preview.created_at, &Rfc3339).is_ok()
        && value.transitions.len() <= 32
        && value.transitions.iter().all(|state| {
            matches!(
                state.as_str(),
                "requested"
                    | "preview_ready"
                    | "authorized"
                    | "restoring"
                    | "completed"
                    | "failed"
                    | "cancelled"
            )
        })
        && value
            .transitions
            .iter()
            .all(|state| !canaries.reflects(state))
        && value
            .verification_digest
            .as_deref()
            .is_none_or(|digest| validate_digest(digest) && !canaries.reflects(digest))
}

fn same_destination(
    expected: &RestoreDestinationInput,
    actual: &RestoreDestinationProjection,
) -> bool {
    expected.tenant_id == actual.tenant_id
        && expected.workspace_id == actual.workspace_id
        && expected.database_id == actual.database_id
        && expected.bucket_id == actual.bucket_id
}

fn valid_cloud_etag(
    operation: CloudOperation,
    value: Option<&str>,
    resource: Option<&(&str, String)>,
    version: Option<u64>,
) -> bool {
    let Some(value) = value else { return false };
    if operation == CloudOperation::ListBackups {
        return value
            .strip_prefix("\"projection-")
            .and_then(|v| v.strip_suffix('"'))
            .is_some_and(|digest| {
                digest.len() == 24
                    && digest
                        .bytes()
                        .all(|b| b.is_ascii_hexdigit() && !b.is_ascii_uppercase())
            });
    }
    resource
        .zip(version)
        .is_some_and(|((kind, id), version)| value == format!("\"{kind}:{id}:{version}\""))
}

impl LocalRecoveryTransport for LocalServiceManager {
    fn is_ready(&self) -> bool {
        self.status().state() == "ready"
    }

    fn execute(
        &self,
        scope: &str,
        capability: &str,
        method: &str,
        path: &str,
        body: &[u8],
    ) -> Result<RecoveryHttpResponse, &'static str> {
        let response = self.execute_recovery_request(scope, capability, method, path, body)?;
        Ok(RecoveryHttpResponse {
            status: response.status,
            content_type: response.content_type,
            content_length: response.content_length,
            body: response.body,
        })
    }
}

#[derive(Clone, Serialize)]
pub struct LocalRecoveryError {
    code: &'static str,
    trace_id: String,
    retryable: bool,
}

impl LocalRecoveryError {
    fn new(code: &'static str, retryable: bool) -> Self {
        let trace_id = trace_id_with(|random| getrandom::fill(random).map_err(|_| ()));
        Self {
            code,
            trace_id,
            retryable,
        }
    }

    fn with_trace(code: &'static str, trace_id: String, retryable: bool) -> Self {
        Self {
            code,
            trace_id,
            retryable,
        }
    }

    pub fn code(&self) -> &'static str {
        self.code
    }
}

impl fmt::Debug for LocalRecoveryError {
    fn fmt(&self, formatter: &mut fmt::Formatter<'_>) -> fmt::Result {
        formatter
            .debug_struct("LocalRecoveryError")
            .field("code", &self.code)
            .field("trace_id", &self.trace_id)
            .field("retryable", &self.retryable)
            .finish()
    }
}

pub struct LocalRecoveryPort<'a> {
    transport: &'a dyn LocalRecoveryTransport,
}

impl<'a> LocalRecoveryPort<'a> {
    pub fn new(transport: &'a dyn LocalRecoveryTransport) -> Self {
        Self { transport }
    }

    pub fn scan(
        &self,
        request: RecoveryScanRequest,
    ) -> Result<LocalRecoveryJob, LocalRecoveryError> {
        validate_scan(&request)?;
        let body = serde_json::to_vec(&request)
            .map_err(|_| LocalRecoveryError::new("LOCAL_RECOVERY_INPUT_INVALID", false))?;
        if body.len() > 4096 {
            return Err(LocalRecoveryError::new(
                "LOCAL_RECOVERY_INPUT_INVALID",
                false,
            ));
        }
        self.call(
            "recovery.write",
            "recovery.scan",
            "POST",
            "/local/v1/recovery/scans",
            &body,
        )
    }

    pub fn get_job(&self, job_id: &str) -> Result<LocalRecoveryJob, LocalRecoveryError> {
        validate_id(job_id, 256)?;
        self.call(
            "recovery.read",
            "recovery.job.read",
            "GET",
            &format!("/local/v1/recovery/jobs/{job_id}"),
            &[],
        )
    }

    pub fn repair(
        &self,
        job_id: &str,
        request: RecoveryRepairRequest,
    ) -> Result<LocalRecoveryJob, LocalRecoveryError> {
        validate_id(job_id, 256)?;
        validate_id(&request.workspace_id, 64)?;
        if request.expected_version == 0 {
            return Err(LocalRecoveryError::new(
                "LOCAL_RECOVERY_INPUT_INVALID",
                false,
            ));
        }
        let body = serde_json::to_vec(&request)
            .map_err(|_| LocalRecoveryError::new("LOCAL_RECOVERY_INPUT_INVALID", false))?;
        if body.len() > 1024 {
            return Err(LocalRecoveryError::new(
                "LOCAL_RECOVERY_INPUT_INVALID",
                false,
            ));
        }
        self.call(
            "recovery.write",
            "recovery.repair",
            "POST",
            &format!("/local/v1/recovery/jobs/{job_id}/repair"),
            &body,
        )
    }

    fn call(
        &self,
        scope: &str,
        capability: &str,
        method: &str,
        path: &str,
        body: &[u8],
    ) -> Result<LocalRecoveryJob, LocalRecoveryError> {
        if !self.transport.is_ready() {
            return Err(LocalRecoveryError::new("LOCAL_SERVICE_UNAVAILABLE", true));
        }
        let response = self
            .transport
            .execute(scope, capability, method, path, body)
            .map_err(|code| match code {
                "LOCAL_SERVICE_UNAVAILABLE" => LocalRecoveryError::new(code, true),
                "LOCAL_COMMAND_NOT_ALLOWED" => LocalRecoveryError::new(code, false),
                "LOCAL_RECOVERY_RESPONSE_REJECTED" => LocalRecoveryError::new(code, false),
                _ => LocalRecoveryError::new("LOCAL_RECOVERY_REQUEST_FAILED", true),
            })?;
        validate_response(response)
    }
}

#[tauri::command]
pub async fn recovery_cloud_create_backup(
    input: CloudCreateBackupCommandInput,
    recovery: tauri::State<'_, NativeRecoveryRuntime>,
    session: tauri::State<'_, crate::native_session::NativeSessionRuntime>,
) -> Result<CloudRecoveryProjection, LocalRecoveryError> {
    recovery.cloud_create_backup(&session, input).await
}

#[tauri::command]
pub async fn recovery_cloud_list_backups(
    input: CloudListBackupsCommandInput,
    recovery: tauri::State<'_, NativeRecoveryRuntime>,
    session: tauri::State<'_, crate::native_session::NativeSessionRuntime>,
) -> Result<CloudRecoveryProjection, LocalRecoveryError> {
    recovery.cloud_list_backups(&session, input).await
}

#[tauri::command]
pub async fn recovery_cloud_get_backup(
    input: CloudGetBackupCommandInput,
    recovery: tauri::State<'_, NativeRecoveryRuntime>,
    session: tauri::State<'_, crate::native_session::NativeSessionRuntime>,
) -> Result<CloudRecoveryProjection, LocalRecoveryError> {
    recovery.cloud_get_backup(&session, input).await
}

#[tauri::command]
pub async fn recovery_cloud_preview_restore(
    input: CloudPreviewRestoreCommandInput,
    recovery: tauri::State<'_, NativeRecoveryRuntime>,
    session: tauri::State<'_, crate::native_session::NativeSessionRuntime>,
) -> Result<CloudRecoveryProjection, LocalRecoveryError> {
    recovery.cloud_preview_restore(&session, input).await
}

#[tauri::command]
pub async fn recovery_cloud_get_restore(
    input: CloudGetRestoreCommandInput,
    recovery: tauri::State<'_, NativeRecoveryRuntime>,
    session: tauri::State<'_, crate::native_session::NativeSessionRuntime>,
) -> Result<CloudRecoveryProjection, LocalRecoveryError> {
    recovery.cloud_get_restore(&session, input).await
}

#[tauri::command]
pub async fn recovery_cloud_execute_restore(
    input: CloudExecuteRestoreCommandInput,
    recovery: tauri::State<'_, NativeRecoveryRuntime>,
    session: tauri::State<'_, crate::native_session::NativeSessionRuntime>,
) -> Result<CloudRecoveryProjection, LocalRecoveryError> {
    recovery.cloud_execute_restore(&session, input).await
}

#[tauri::command]
pub async fn recovery_cloud_cancel_restore(
    input: CloudCancelRestoreCommandInput,
    recovery: tauri::State<'_, NativeRecoveryRuntime>,
    session: tauri::State<'_, crate::native_session::NativeSessionRuntime>,
) -> Result<CloudRecoveryProjection, LocalRecoveryError> {
    recovery.cloud_cancel_restore(&session, input).await
}

#[tauri::command]
pub async fn recovery_local_start_scan(
    input: LocalStartScanCommandInput,
    manager: tauri::State<'_, LocalServiceManager>,
) -> Result<LocalRecoveryJob, LocalRecoveryError> {
    run_local_start_scan(manager.inner().clone(), input).await
}

#[tauri::command]
pub async fn recovery_local_get_job(
    input: LocalGetJobCommandInput,
    manager: tauri::State<'_, LocalServiceManager>,
) -> Result<LocalRecoveryJob, LocalRecoveryError> {
    run_local_get_job(manager.inner().clone(), input).await
}

#[tauri::command]
pub async fn recovery_local_repair_job(
    input: LocalRepairJobCommandInput,
    manager: tauri::State<'_, LocalServiceManager>,
) -> Result<LocalRecoveryJob, LocalRecoveryError> {
    run_local_repair_job(manager.inner().clone(), input).await
}

async fn run_local_start_scan<T>(
    transport: T,
    input: LocalStartScanCommandInput,
) -> Result<LocalRecoveryJob, LocalRecoveryError>
where
    T: LocalRecoveryTransport + Send + 'static,
{
    tauri::async_runtime::spawn_blocking(move || {
        LocalRecoveryPort::new(&transport).scan(RecoveryScanRequest {
            workspace_id: input.workspace_id,
            target_id: input.target_id,
            snapshot_checksum: input.snapshot_checksum,
            metadata_checksum: input.metadata_checksum,
            actual_checksum: input.actual_checksum,
            journal_present: input.journal_present,
        })
    })
    .await
    .map_err(|_| LocalRecoveryError::new("LOCAL_RECOVERY_REQUEST_FAILED", true))?
}

async fn run_local_get_job<T>(
    transport: T,
    input: LocalGetJobCommandInput,
) -> Result<LocalRecoveryJob, LocalRecoveryError>
where
    T: LocalRecoveryTransport + Send + 'static,
{
    tauri::async_runtime::spawn_blocking(move || {
        LocalRecoveryPort::new(&transport).get_job(&input.job_id)
    })
    .await
    .map_err(|_| LocalRecoveryError::new("LOCAL_RECOVERY_REQUEST_FAILED", true))?
}

async fn run_local_repair_job<T>(
    transport: T,
    input: LocalRepairJobCommandInput,
) -> Result<LocalRecoveryJob, LocalRecoveryError>
where
    T: LocalRecoveryTransport + Send + 'static,
{
    tauri::async_runtime::spawn_blocking(move || {
        LocalRecoveryPort::new(&transport).repair(
            &input.job_id,
            RecoveryRepairRequest {
                workspace_id: input.workspace_id,
                expected_version: input.expected_version,
            },
        )
    })
    .await
    .map_err(|_| LocalRecoveryError::new("LOCAL_RECOVERY_REQUEST_FAILED", true))?
}

#[cfg(feature = "contract-test")]
#[doc(hidden)]
pub async fn recovery_local_start_scan_for_contract<T>(
    transport: T,
    input: LocalStartScanCommandInput,
) -> Result<LocalRecoveryJob, LocalRecoveryError>
where
    T: LocalRecoveryTransport + Send + 'static,
{
    run_local_start_scan(transport, input).await
}

#[cfg(feature = "contract-test")]
#[doc(hidden)]
pub async fn recovery_local_get_job_for_contract<T>(
    transport: T,
    input: LocalGetJobCommandInput,
) -> Result<LocalRecoveryJob, LocalRecoveryError>
where
    T: LocalRecoveryTransport + Send + 'static,
{
    run_local_get_job(transport, input).await
}

#[cfg(feature = "contract-test")]
#[doc(hidden)]
pub async fn recovery_local_repair_job_for_contract<T>(
    transport: T,
    input: LocalRepairJobCommandInput,
) -> Result<LocalRecoveryJob, LocalRecoveryError>
where
    T: LocalRecoveryTransport + Send + 'static,
{
    run_local_repair_job(transport, input).await
}

fn validate_id(value: &str, max: usize) -> Result<(), LocalRecoveryError> {
    let valid = !value.is_empty()
        && value.len() <= max
        && value.bytes().enumerate().all(|(index, byte)| {
            byte.is_ascii_alphanumeric() || (index > 0 && matches!(byte, b'.' | b'_' | b':' | b'-'))
        });
    valid
        .then_some(())
        .ok_or_else(|| LocalRecoveryError::new("LOCAL_RECOVERY_INPUT_INVALID", false))
}

fn validate_digest(value: &str) -> bool {
    value.len() == 64
        && value
            .bytes()
            .all(|byte| byte.is_ascii_hexdigit() && !byte.is_ascii_uppercase())
}

fn validate_scan(request: &RecoveryScanRequest) -> Result<(), LocalRecoveryError> {
    validate_id(&request.workspace_id, 64)?;
    validate_id(&request.target_id, 256)?;
    if !request.target_id.starts_with("fixture-")
        || !validate_digest(&request.snapshot_checksum)
        || !validate_digest(&request.metadata_checksum)
        || !validate_digest(&request.actual_checksum)
    {
        return Err(LocalRecoveryError::new(
            "LOCAL_RECOVERY_INPUT_INVALID",
            false,
        ));
    }
    Ok(())
}

fn validate_response(
    response: RecoveryHttpResponse,
) -> Result<LocalRecoveryJob, LocalRecoveryError> {
    let content_type_ok = response.content_type.as_deref().is_some_and(|value| {
        value.eq_ignore_ascii_case("application/json")
            || value.to_ascii_lowercase().starts_with("application/json;")
    });
    if response.status != 200
        || !content_type_ok
        || response.body.len() > RESPONSE_MAX_BYTES
        || response.content_length != Some(response.body.len())
    {
        return Err(LocalRecoveryError::new(
            "LOCAL_RECOVERY_RESPONSE_REJECTED",
            false,
        ));
    }
    let envelope: RecoveryEnvelope = serde_json::from_slice(&response.body)
        .map_err(|_| LocalRecoveryError::new("LOCAL_RECOVERY_RESPONSE_REJECTED", false))?;
    validate_job(&envelope.data)?;
    Ok(envelope.data)
}

fn validate_job(job: &LocalRecoveryJob) -> Result<(), LocalRecoveryError> {
    if validate_id(&job.job_id, 256).is_err() || validate_id(&job.target_id, 256).is_err() {
        return Err(LocalRecoveryError::new(
            "LOCAL_RECOVERY_RESPONSE_REJECTED",
            false,
        ));
    }
    let canonical_timestamp = (20..=32).contains(&job.recorded_at.len())
        && job.recorded_at.ends_with('Z')
        && OffsetDateTime::parse(&job.recorded_at, &Rfc3339).is_ok();
    let canonical_version = match job.version {
        0 => false,
        1 => job.previous_version.is_none(),
        version => job.previous_version == Some(version - 1),
    };
    let lower_target_id = job.target_id.to_ascii_lowercase();
    let canonical_job_id = job
        .job_id
        .strip_prefix("fixture-recovery-")
        .is_some_and(|suffix| {
            suffix.len() == 24
                && suffix
                    .bytes()
                    .all(|byte| byte.is_ascii_hexdigit() && !byte.is_ascii_uppercase())
        });
    let safe_identifiers = canonical_job_id
        && job.target_id.starts_with("fixture-")
        && !lower_target_id.contains("quarantine");
    if !canonical_version
        || !canonical_timestamp
        || !safe_identifiers
        || !matches!(
            job.state.as_str(),
            "detected"
                | "quarantined"
                | "scanning"
                | "repairable"
                | "repairing"
                | "verified"
                | "manual_recovery_required"
                | "failed"
        )
        || !matches!(
            job.integrity.as_str(),
            "pending" | "verified" | "manual_required"
        )
    {
        return Err(LocalRecoveryError::new(
            "LOCAL_RECOVERY_RESPONSE_REJECTED",
            false,
        ));
    }
    Ok(())
}

#[cfg(test)]
mod tests {
    use super::trace_id_with;

    #[test]
    fn rng_failure_trace_fallback_is_fixed_format_and_collision_resistant() {
        let first = trace_id_with(|_| Err(()));
        let second = trace_id_with(|_| Err(()));
        assert_eq!(first.len(), 32);
        assert_eq!(second.len(), 32);
        assert!(
            first
                .bytes()
                .all(|byte| byte.is_ascii_hexdigit() && !byte.is_ascii_uppercase())
        );
        assert!(
            second
                .bytes()
                .all(|byte| byte.is_ascii_hexdigit() && !byte.is_ascii_uppercase())
        );
        assert_ne!(first, second);
        assert_ne!(first, "00000000000000000000000000000000");
        assert_ne!(second, "00000000000000000000000000000000");
    }
}
