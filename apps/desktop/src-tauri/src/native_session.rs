use serde::{Deserialize, Serialize};
use sha2::{Digest, Sha256};
use std::{
    fmt,
    future::Future,
    pin::Pin,
    sync::{
        atomic::{AtomicBool, AtomicU64, AtomicUsize, Ordering},
        Arc,
    },
};
use time::{format_description::well_known::Rfc3339, OffsetDateTime};
use zeroize::{Zeroize, Zeroizing};

#[cfg(windows)]
use std::ptr::null_mut;

#[cfg(windows)]
use windows_sys::Win32::Foundation::{GetLastError, ERROR_NOT_FOUND};
#[cfg(windows)]
use windows_sys::Win32::Security::Credentials::{
    CredDeleteW, CredFree, CredReadW, CredWriteW, CREDENTIALW, CRED_PERSIST_LOCAL_MACHINE,
    CRED_TYPE_GENERIC,
};

pub const NATIVE_SESSION_CREDENTIAL_TARGET: &str = "DaonUser/NativeSession/v1";
pub const LOCAL_STORAGE_CREDENTIAL_TARGET: &str = "DaonUser/LocalStorage/v1";
pub const PUBLIC_GATEWAY: &str = "https://daon-user.sinsan.kr";

const NATIVE_LOGIN_PATH: &str = "/api/v1/auth/native/login";
const NATIVE_REFRESH_PATH: &str = "/api/v1/session/refresh";
const NATIVE_AUTHORIZATION_PATH: &str = "/api/v1/session";
const NATIVE_LOGIN_ENDPOINT: &str = "https://daon-user.sinsan.kr/api/v1/auth/native/login";
const NATIVE_REFRESH_ENDPOINT: &str = "https://daon-user.sinsan.kr/api/v1/session/refresh";
const NATIVE_AUTHORIZATION_ENDPOINT: &str = "https://daon-user.sinsan.kr/api/v1/session";
const MAX_CREDENTIAL_BYTES: usize = 512;
const MAX_PERSISTED_BYTES: usize = 2560;
const MAX_SAFE_ID_BYTES: usize = 256;
pub const CONNECT_TIMEOUT_SECONDS: u64 = 5;
pub const REQUEST_TIMEOUT_SECONDS: u64 = 20;
pub const MAX_RESPONSE_BYTES: usize = 128 * 1024;
pub(crate) const MAX_WORKSPACE_PDF_BYTES: usize = 25 * 1024 * 1024;

fn wipe_text(value: &mut String) {
    value.zeroize();
}

pub(crate) fn workspace_idempotency_key(
    scope: &str,
    payload: &[u8],
) -> Result<Zeroizing<String>, &'static str> {
    let mut digest = Sha256::new();
    digest.update(scope.as_bytes());
    digest.update([0]);
    digest.update(payload);
    Ok(Zeroizing::new(format!("native-{:x}", digest.finalize())))
}

#[cfg(feature = "contract-test")]
thread_local! {
    static PARTIAL_SECRET_DROP_AUDIT: std::cell::Cell<usize> = const { std::cell::Cell::new(0) };
}

#[cfg(feature = "contract-test")]
pub fn reset_partial_secret_drop_audit_for_contract() {
    PARTIAL_SECRET_DROP_AUDIT.with(|audit| audit.set(0));
}

#[cfg(feature = "contract-test")]
pub fn partial_secret_drop_count_for_contract() -> usize {
    PARTIAL_SECRET_DROP_AUDIT.with(std::cell::Cell::get)
}

#[derive(Default, Deserialize, Serialize)]
#[serde(transparent)]
struct SecretString(String);

impl SecretString {
    fn take(&mut self) -> String {
        std::mem::take(&mut self.0)
    }
}

impl Drop for SecretString {
    fn drop(&mut self) {
        self.0.zeroize();
        #[cfg(feature = "contract-test")]
        PARTIAL_SECRET_DROP_AUDIT.with(|audit| audit.set(audit.get() + 1));
    }
}

fn valid_safe_text(value: &str) -> bool {
    !value.is_empty() && value.len() <= MAX_SAFE_ID_BYTES && !value.contains('\0')
}

fn valid_rfc3339(value: &str) -> bool {
    OffsetDateTime::parse(value, &Rfc3339)
        .map(|parsed| parsed.offset().is_utc())
        .unwrap_or(false)
}

#[derive(Clone, Deserialize, PartialEq, Eq)]
pub struct NativeSessionProjection {
    user_id: String,
    tenant_id: String,
    workspace_id: String,
    session_id: String,
    device_id: String,
    expires_at: String,
}

impl NativeSessionProjection {
    pub(crate) fn workspace_id(&self) -> &str {
        &self.workspace_id
    }

    pub fn new(
        user_id: String,
        tenant_id: String,
        workspace_id: String,
        session_id: String,
        device_id: String,
        expires_at: String,
    ) -> Result<Self, &'static str> {
        if [
            user_id.as_str(),
            tenant_id.as_str(),
            workspace_id.as_str(),
            session_id.as_str(),
            device_id.as_str(),
            expires_at.as_str(),
        ]
        .iter()
        .any(|value| !valid_safe_text(value))
            || !valid_rfc3339(&expires_at)
        {
            return Err("AUTHENTICATION_REQUIRED");
        }
        Ok(Self {
            user_id,
            tenant_id,
            workspace_id,
            session_id,
            device_id,
            expires_at,
        })
    }
}

impl Serialize for NativeSessionProjection {
    fn serialize<S>(&self, serializer: S) -> Result<S::Ok, S::Error>
    where
        S: serde::Serializer,
    {
        #[derive(Serialize)]
        struct Projection<'a> {
            user_id: &'a str,
            tenant_id: &'a str,
            workspace_id: &'a str,
            session_id: &'a str,
            device_id: &'a str,
            expires_at: &'a str,
        }
        Projection {
            user_id: &self.user_id,
            tenant_id: &self.tenant_id,
            workspace_id: &self.workspace_id,
            session_id: &self.session_id,
            device_id: &self.device_id,
            expires_at: &self.expires_at,
        }
        .serialize(serializer)
    }
}

impl fmt::Debug for NativeSessionProjection {
    fn fmt(&self, formatter: &mut fmt::Formatter<'_>) -> fmt::Result {
        formatter
            .debug_struct("NativeSessionProjection")
            .field("user_id", &self.user_id)
            .field("tenant_id", &self.tenant_id)
            .field("workspace_id", &self.workspace_id)
            .field("session_id", &self.session_id)
            .field("device_id", &self.device_id)
            .field("expires_at", &self.expires_at)
            .finish()
    }
}

#[derive(PartialEq, Eq)]
struct SecretBytes(Vec<u8>);

impl SecretBytes {
    fn new(mut value: String) -> Result<Self, &'static str> {
        let bytes = std::mem::take(&mut value).into_bytes();
        if !(40..=MAX_CREDENTIAL_BYTES).contains(&bytes.len()) || bytes.contains(&0) {
            let mut rejected = bytes;
            rejected.zeroize();
            return Err("AUTHENTICATION_REQUIRED");
        }
        Ok(Self(bytes))
    }

    fn to_text(&self) -> Result<String, &'static str> {
        String::from_utf8(self.0.clone()).map_err(|_| "AUTHENTICATION_REQUIRED")
    }
}

impl Drop for SecretBytes {
    fn drop(&mut self) {
        self.0.zeroize();
    }
}

impl fmt::Debug for SecretBytes {
    fn fmt(&self, formatter: &mut fmt::Formatter<'_>) -> fmt::Result {
        formatter.write_str("SecretBytes([redacted])")
    }
}

#[derive(PartialEq, Eq)]
pub struct NativeSessionCredentials {
    access: SecretBytes,
    refresh: SecretBytes,
    projection: NativeSessionProjection,
}

impl NativeSessionCredentials {
    pub fn new(
        access_credential: String,
        refresh_credential: String,
        projection: NativeSessionProjection,
    ) -> Result<Self, &'static str> {
        Ok(Self {
            access: SecretBytes::new(access_credential)?,
            refresh: SecretBytes::new(refresh_credential)?,
            projection,
        })
    }

    pub fn projection(&self) -> &NativeSessionProjection {
        &self.projection
    }

    pub fn from_persisted_bytes(bytes: &[u8]) -> Result<Self, &'static str> {
        let mut wire: PersistedNativeSession =
            serde_json::from_slice(bytes).map_err(|_| "AUTHENTICATION_REQUIRED")?;
        let result = if wire.version != 1 {
            Err("AUTHENTICATION_REQUIRED")
        } else {
            Self::new(
                wire.access_credential.take(),
                wire.refresh_credential.take(),
                wire.projection.clone(),
            )
        };
        result
    }

    fn persisted_bytes(&self) -> Result<Vec<u8>, &'static str> {
        let wire = PersistedNativeSession {
            version: 1,
            access_credential: SecretString(self.access.to_text()?),
            refresh_credential: SecretString(self.refresh.to_text()?),
            projection: self.projection.clone(),
        };
        serde_json::to_vec(&wire).map_err(|_| "AUTHENTICATION_REQUIRED")
    }
}

impl fmt::Debug for NativeSessionCredentials {
    fn fmt(&self, formatter: &mut fmt::Formatter<'_>) -> fmt::Result {
        formatter
            .debug_struct("NativeSessionCredentials")
            .field("access", &"[redacted]")
            .field("refresh", &"[redacted]")
            .field("projection", &self.projection)
            .finish()
    }
}

#[derive(Deserialize, Serialize)]
#[serde(deny_unknown_fields)]
struct PersistedNativeSession {
    version: u8,
    access_credential: SecretString,
    refresh_credential: SecretString,
    projection: NativeSessionProjection,
}

pub struct NativeSessionVault {
    target: String,
}

impl NativeSessionVault {
    pub fn for_app() -> Self {
        Self {
            target: NATIVE_SESSION_CREDENTIAL_TARGET.to_owned(),
        }
    }

    pub fn new(target: String) -> Result<Self, &'static str> {
        if !target.starts_with("DaonUser/NativeSession/") || target.contains('\0') {
            return Err("AUTHENTICATION_REQUIRED");
        }
        Ok(Self { target })
    }

    pub fn target(&self) -> &str {
        &self.target
    }

    #[cfg(windows)]
    pub fn write(&self, credentials: &NativeSessionCredentials) -> Result<(), &'static str> {
        let mut blob = credentials.persisted_bytes()?;
        if blob.is_empty() || blob.len() > MAX_PERSISTED_BYTES {
            blob.zeroize();
            return Err("AUTHENTICATION_REQUIRED");
        }
        let mut target = wide(&self.target);
        let mut username = wide("Daon Native Session");
        let credential = CREDENTIALW {
            Type: CRED_TYPE_GENERIC,
            TargetName: target.as_mut_ptr(),
            CredentialBlobSize: blob.len() as u32,
            CredentialBlob: blob.as_mut_ptr(),
            Persist: CRED_PERSIST_LOCAL_MACHINE,
            UserName: username.as_mut_ptr(),
            ..CREDENTIALW::default()
        };
        // SAFETY: The UTF-16 and blob buffers stay live and mutable for the Win32 call.
        let written = unsafe { CredWriteW(&credential, 0) } != 0;
        blob.zeroize();
        written.then_some(()).ok_or("AUTHENTICATION_REQUIRED")
    }

    #[cfg(not(windows))]
    pub fn write(&self, _credentials: &NativeSessionCredentials) -> Result<(), &'static str> {
        Err("AUTHENTICATION_REQUIRED")
    }

    #[cfg(windows)]
    pub fn read(&self) -> Result<Option<NativeSessionCredentials>, &'static str> {
        let target = wide(&self.target);
        let mut raw: *mut CREDENTIALW = null_mut();
        // SAFETY: Target is nul-terminated and raw is an out pointer owned by CredFree.
        if unsafe { CredReadW(target.as_ptr(), CRED_TYPE_GENERIC, 0, &mut raw) } == 0 {
            // SAFETY: GetLastError immediately follows the failed Win32 call.
            return if unsafe { GetLastError() } == ERROR_NOT_FOUND {
                Ok(None)
            } else {
                Err("AUTHENTICATION_REQUIRED")
            };
        }
        if raw.is_null() {
            return Err("AUTHENTICATION_REQUIRED");
        }
        // SAFETY: CredReadW returned a valid CREDENTIALW until CredFree is called.
        let credential = unsafe { &*raw };
        let mut blob = if credential.CredentialBlob.is_null()
            || credential.CredentialBlobSize == 0
            || credential.CredentialBlobSize as usize > MAX_PERSISTED_BYTES
        {
            Vec::new()
        } else {
            // SAFETY: CredentialBlobSize was bounded while the CredReadW allocation is live.
            unsafe {
                std::slice::from_raw_parts(
                    credential.CredentialBlob,
                    credential.CredentialBlobSize as usize,
                )
            }
            .to_vec()
        };
        if !credential.CredentialBlob.is_null() && credential.CredentialBlobSize > 0 {
            // SAFETY: CredentialBlob belongs to the live CredReadW allocation and is wiped before CredFree.
            unsafe {
                std::slice::from_raw_parts_mut(
                    credential.CredentialBlob,
                    credential.CredentialBlobSize as usize,
                )
            }
            .zeroize();
        }
        // SAFETY: raw is exactly the allocation returned by CredReadW and freed once.
        unsafe { CredFree(raw.cast()) };
        let result = NativeSessionCredentials::from_persisted_bytes(&blob).map(Some);
        blob.zeroize();
        if result.is_err() {
            self.revoke().map_err(|_| "AUTHENTICATION_REQUIRED")?;
        }
        result
    }

    #[cfg(not(windows))]
    pub fn read(&self) -> Result<Option<NativeSessionCredentials>, &'static str> {
        Err("AUTHENTICATION_REQUIRED")
    }

    #[cfg(windows)]
    pub fn revoke(&self) -> Result<(), &'static str> {
        let target = wide(&self.target);
        // SAFETY: Target is a live nul-terminated UTF-16 buffer.
        if unsafe { CredDeleteW(target.as_ptr(), CRED_TYPE_GENERIC, 0) } != 0 {
            return Ok(());
        }
        // SAFETY: GetLastError immediately follows the failed Win32 call.
        if unsafe { GetLastError() } == ERROR_NOT_FOUND {
            Ok(())
        } else {
            Err("AUTHENTICATION_REQUIRED")
        }
    }

    #[cfg(not(windows))]
    pub fn revoke(&self) -> Result<(), &'static str> {
        Err("AUTHENTICATION_REQUIRED")
    }
}

impl fmt::Debug for NativeSessionVault {
    fn fmt(&self, formatter: &mut fmt::Formatter<'_>) -> fmt::Result {
        formatter.write_str("NativeSessionVault([redacted])")
    }
}

impl NativeSessionVaultPort for NativeSessionVault {
    fn read(&self) -> Result<Option<NativeSessionCredentials>, NativeSessionError> {
        NativeSessionVault::read(self).map_err(|_| NativeSessionError::authentication_required())
    }

    fn write(&self, credentials: &NativeSessionCredentials) -> Result<(), NativeSessionError> {
        NativeSessionVault::write(self, credentials)
            .map_err(|_| NativeSessionError::authentication_required())
    }

    fn revoke(&self) -> Result<(), NativeSessionError> {
        NativeSessionVault::revoke(self).map_err(|_| NativeSessionError::authentication_required())
    }
}

#[cfg(windows)]
fn wide(value: &str) -> Vec<u16> {
    value.encode_utf16().chain(std::iter::once(0)).collect()
}

#[derive(Clone, Serialize)]
pub struct NativeSessionError {
    code: &'static str,
}

impl NativeSessionError {
    fn authentication_required() -> Self {
        Self {
            code: "AUTHENTICATION_REQUIRED",
        }
    }

    pub fn code(&self) -> &'static str {
        self.code
    }

    pub fn authentication_required_for_contract() -> Self {
        Self::authentication_required()
    }
}

impl fmt::Debug for NativeSessionError {
    fn fmt(&self, formatter: &mut fmt::Formatter<'_>) -> fmt::Result {
        formatter
            .debug_struct("NativeSessionError")
            .field("code", &self.code)
            .finish()
    }
}

pub struct NativeHttpResponse {
    status: u16,
    content_type: Option<String>,
    content_length: Option<usize>,
    has_set_cookie: bool,
    body: Vec<u8>,
    drop_audit: Option<Arc<AtomicUsize>>,
}

impl NativeHttpResponse {
    pub fn from_parts(
        status: u16,
        content_type: Option<String>,
        content_length: Option<usize>,
        has_set_cookie: bool,
        body: Vec<u8>,
    ) -> Self {
        Self {
            status,
            content_type,
            content_length,
            has_set_cookie,
            body,
            drop_audit: None,
        }
    }

    fn with_drop_audit(mut self, drop_audit: Option<Arc<AtomicUsize>>) -> Self {
        self.drop_audit = drop_audit;
        self
    }

    pub fn into_credentials(mut self) -> Result<NativeSessionCredentials, NativeSessionError> {
        let content_type = self
            .content_type
            .as_deref()
            .unwrap_or("")
            .to_ascii_lowercase();
        let valid_content_type = content_type.split(';').next() == Some("application/json");
        let valid_status = (200..300).contains(&self.status);
        if !valid_status
            || (300..400).contains(&self.status)
            || self.has_set_cookie
            || !valid_content_type
            || self
                .content_length
                .is_some_and(|length| length > MAX_RESPONSE_BYTES)
            || self.body.len() > MAX_RESPONSE_BYTES
        {
            return Err(NativeSessionError::authentication_required());
        }
        let parsed = serde_json::from_slice::<NativeSessionEnvelope>(&self.body)
            .map_err(|_| NativeSessionError::authentication_required());
        self.body.zeroize();
        let mut wire = parsed?.data;
        wire.drop_audit = self.drop_audit.clone();
        wire.into_credentials()
    }
}

impl Drop for NativeHttpResponse {
    fn drop(&mut self) {
        self.body.zeroize();
        if let Some(audit) = &self.drop_audit {
            audit.fetch_add(1, Ordering::AcqRel);
        }
    }
}

pub(crate) enum NativeWorkspaceOperation {
    ListSources {
        workspace_id: String,
    },
    UploadPdf {
        workspace_id: String,
        filename: String,
        bytes: Zeroizing<Vec<u8>>,
        idempotency_key: Zeroizing<String>,
    },
    ProcessingStatus {
        workspace_id: String,
        processing_run_id: String,
    },
    AskQuestion {
        workspace_id: String,
        body: Zeroizing<Vec<u8>>,
        idempotency_key: Zeroizing<String>,
    },
    CitationContent {
        workspace_id: String,
        citation_id: String,
    },
    CreateReport {
        workspace_id: String,
        body: Zeroizing<Vec<u8>>,
        idempotency_key: Zeroizing<String>,
    },
    ListStudioOutputs {
        workspace_id: String,
    },
}

impl NativeWorkspaceOperation {
    pub(crate) fn workspace_id(&self) -> &str {
        match self {
            Self::ListSources { workspace_id }
            | Self::UploadPdf { workspace_id, .. }
            | Self::ProcessingStatus { workspace_id, .. }
            | Self::AskQuestion { workspace_id, .. }
            | Self::CitationContent { workspace_id, .. }
            | Self::CreateReport { workspace_id, .. }
            | Self::ListStudioOutputs { workspace_id } => workspace_id,
        }
    }

    fn method_path(&self) -> (&'static str, String) {
        match self {
            Self::ListSources { workspace_id } => {
                ("GET", format!("/api/v1/workspaces/{workspace_id}/sources"))
            }
            Self::UploadPdf { workspace_id, .. } => {
                ("POST", format!("/api/v1/workspaces/{workspace_id}/sources"))
            }
            Self::ProcessingStatus {
                workspace_id,
                processing_run_id,
            } => (
                "GET",
                format!("/api/v1/workspaces/{workspace_id}/processing-runs/{processing_run_id}"),
            ),
            Self::AskQuestion { workspace_id, .. } => (
                "POST",
                format!("/api/v1/workspaces/{workspace_id}/questions"),
            ),
            Self::CitationContent {
                workspace_id,
                citation_id,
            } => (
                "GET",
                format!("/api/v1/workspaces/{workspace_id}/citations/{citation_id}/content"),
            ),
            Self::CreateReport { workspace_id, .. } => (
                "POST",
                format!("/api/v1/workspaces/{workspace_id}/studio/reports"),
            ),
            Self::ListStudioOutputs { workspace_id } => (
                "GET",
                format!("/api/v1/workspaces/{workspace_id}/studio/outputs"),
            ),
        }
    }

    fn expected_status(&self, status: u16) -> bool {
        match self {
            Self::UploadPdf { .. } => status == 202,
            Self::CreateReport { .. } => status == 200 || status == 201,
            _ => status == 200,
        }
    }

    fn expects_pdf(&self) -> bool {
        matches!(self, Self::CitationContent { .. })
    }
}

impl fmt::Debug for NativeWorkspaceOperation {
    fn fmt(&self, formatter: &mut fmt::Formatter<'_>) -> fmt::Result {
        formatter.write_str(match self {
            Self::ListSources { .. } => "NativeWorkspaceOperation::ListSources",
            Self::UploadPdf { .. } => "NativeWorkspaceOperation::UploadPdf([redacted])",
            Self::ProcessingStatus { .. } => "NativeWorkspaceOperation::ProcessingStatus",
            Self::AskQuestion { .. } => "NativeWorkspaceOperation::AskQuestion([redacted])",
            Self::CitationContent { .. } => "NativeWorkspaceOperation::CitationContent",
            Self::CreateReport { .. } => "NativeWorkspaceOperation::CreateReport([redacted])",
            Self::ListStudioOutputs { .. } => "NativeWorkspaceOperation::ListStudioOutputs",
        })
    }
}

pub(crate) struct NativeWorkspaceResponse {
    pub(crate) status: u16,
    pub(crate) content_type: Option<String>,
    pub(crate) citation_page: Option<u32>,
    pub(crate) body: Zeroizing<Vec<u8>>,
}

pub(crate) trait NativeWorkspaceTransport: Send + Sync {
    fn execute<'a>(
        &'a self,
        access: &'a [u8],
        operation: NativeWorkspaceOperation,
    ) -> Pin<Box<dyn Future<Output = Result<NativeWorkspaceResponse, &'static str>> + Send + 'a>>;
}

struct WorkspaceWireBuffer(Zeroizing<Vec<u8>>);

impl AsRef<[u8]> for WorkspaceWireBuffer {
    fn as_ref(&self) -> &[u8] {
        self.0.as_slice()
    }
}

impl Drop for WorkspaceWireBuffer {
    fn drop(&mut self) {
        self.0.zeroize();
    }
}

struct WorkspaceAuthorizationBuffer(Zeroizing<Vec<u8>>);

impl AsRef<[u8]> for WorkspaceAuthorizationBuffer {
    fn as_ref(&self) -> &[u8] {
        self.0.as_slice()
    }
}

impl Drop for WorkspaceAuthorizationBuffer {
    fn drop(&mut self) {
        self.0.zeroize();
    }
}

pub(crate) struct NativeWorkspaceClient {
    client: reqwest::Client,
    gateway: String,
}

impl NativeWorkspaceClient {
    fn status_error(status: u16) -> &'static str {
        match status {
            400 | 415 => "INVALID_REQUEST",
            401 => "AUTHENTICATION_REQUIRED",
            403 => "FORBIDDEN",
            404 | 503 => "RESOURCE_UNAVAILABLE",
            409 => "CONFLICT",
            413 => "REQUEST_TOO_LARGE",
            504 => "WORKSPACE_REQUEST_FAILED",
            _ => "WORKSPACE_RESPONSE_REJECTED",
        }
    }

    fn fixed() -> Result<Self, &'static str> {
        let client = reqwest::Client::builder()
            .https_only(true)
            .redirect(reqwest::redirect::Policy::none())
            .connect_timeout(std::time::Duration::from_secs(CONNECT_TIMEOUT_SECONDS))
            .timeout(std::time::Duration::from_secs(REQUEST_TIMEOUT_SECONDS))
            .build()
            .map_err(|_| "WORKSPACE_REQUEST_FAILED")?;
        Ok(Self {
            client,
            gateway: PUBLIC_GATEWAY.to_owned(),
        })
    }

    #[cfg(feature = "contract-test")]
    pub(crate) fn for_contract_test(
        gateway: &str,
        timeout: std::time::Duration,
    ) -> Result<Self, &'static str> {
        if !gateway.starts_with("http://127.0.0.1:") || gateway.contains(['\r', '\n', '?', '#']) {
            return Err("WORKSPACE_REQUEST_FAILED");
        }
        let client = reqwest::Client::builder()
            .redirect(reqwest::redirect::Policy::none())
            .connect_timeout(timeout)
            .timeout(timeout)
            .build()
            .map_err(|_| "WORKSPACE_REQUEST_FAILED")?;
        Ok(Self {
            client,
            gateway: gateway.to_owned(),
        })
    }
}

impl NativeWorkspaceTransport for NativeWorkspaceClient {
    fn execute<'a>(
        &'a self,
        access: &'a [u8],
        mut operation: NativeWorkspaceOperation,
    ) -> Pin<Box<dyn Future<Output = Result<NativeWorkspaceResponse, &'static str>> + Send + 'a>>
    {
        Box::pin(async move {
            let (method, path) = operation.method_path();
            if path.contains("//") || path.contains(['?', '#', '\r', '\n']) {
                return Err("WORKSPACE_REQUEST_FAILED");
            }
            let endpoint = format!("{}{}", self.gateway, path);
            let method = reqwest::Method::from_bytes(method.as_bytes())
                .map_err(|_| "WORKSPACE_REQUEST_FAILED")?;
            let mut authorization = Zeroizing::new(b"Bearer ".to_vec());
            authorization.extend_from_slice(access);
            let authorization_owner =
                WorkspaceAuthorizationBuffer(Zeroizing::new(std::mem::take(&mut *authorization)));
            let mut authorization_header = reqwest::header::HeaderValue::from_maybe_shared(
                bytes::Bytes::from_owner(authorization_owner),
            )
            .map_err(|_| "AUTHENTICATION_REQUIRED")?;
            authorization_header.set_sensitive(true);
            let mut request = self
                .client
                .request(method, endpoint)
                .header(reqwest::header::AUTHORIZATION, authorization_header)
                .header(
                    reqwest::header::ACCEPT,
                    if operation.expects_pdf() {
                        "application/pdf"
                    } else {
                        "application/json"
                    },
                );
            match &mut operation {
                NativeWorkspaceOperation::UploadPdf {
                    filename,
                    bytes,
                    idempotency_key,
                    ..
                } => {
                    let wire = WorkspaceWireBuffer(Zeroizing::new(std::mem::take(&mut **bytes)));
                    request = request
                        .header(reqwest::header::CONTENT_TYPE, "application/pdf")
                        .header("X-Source-Filename", filename.as_str())
                        .header("Idempotency-Key", idempotency_key.as_str())
                        .body(reqwest::Body::from(bytes::Bytes::from_owner(wire)));
                }
                NativeWorkspaceOperation::AskQuestion {
                    body,
                    idempotency_key,
                    ..
                }
                | NativeWorkspaceOperation::CreateReport {
                    body,
                    idempotency_key,
                    ..
                } => {
                    let wire = WorkspaceWireBuffer(Zeroizing::new(std::mem::take(&mut **body)));
                    request = request
                        .header(reqwest::header::CONTENT_TYPE, "application/json")
                        .header("Idempotency-Key", idempotency_key.as_str())
                        .body(reqwest::Body::from(bytes::Bytes::from_owner(wire)));
                }
                _ => {}
            }
            let mut response = request
                .send()
                .await
                .map_err(|_| "WORKSPACE_REQUEST_FAILED")?;
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
            let citation_page = response
                .headers()
                .get("X-Citation-Page")
                .and_then(|value| value.to_str().ok())
                .and_then(|value| value.parse::<u32>().ok());
            let maximum = if operation.expects_pdf() {
                MAX_WORKSPACE_PDF_BYTES
            } else {
                MAX_RESPONSE_BYTES
            };
            let expected_type = if operation.expects_pdf() {
                "application/pdf"
            } else {
                "application/json"
            };
            if !operation.expected_status(status)
                || response.headers().contains_key(reqwest::header::SET_COOKIE)
                || content_type
                    .as_deref()
                    .unwrap_or("")
                    .to_ascii_lowercase()
                    .split(';')
                    .next()
                    != Some(expected_type)
                || content_length.is_some_and(|length| length > maximum)
            {
                return Err(Self::status_error(status));
            }
            let mut body = Zeroizing::new(Vec::new());
            while let Some(chunk) = response
                .chunk()
                .await
                .map_err(|_| "WORKSPACE_RESPONSE_REJECTED")?
            {
                if body.len().saturating_add(chunk.len()) > maximum {
                    return Err("WORKSPACE_RESPONSE_REJECTED");
                }
                body.extend_from_slice(&chunk);
            }
            if operation.expects_pdf() && (body.len() < 5 || &body[..5] != b"%PDF-") {
                return Err("WORKSPACE_RESPONSE_REJECTED");
            }
            if !operation.expects_pdf() {
                let mut bearer = Zeroizing::new(b"Bearer ".to_vec());
                bearer.extend_from_slice(access);
                let reflects = |needle: &[u8]| {
                    !needle.is_empty()
                        && body
                            .windows(needle.len())
                            .any(|candidate| candidate == needle)
                };
                if reflects(access) || reflects(&bearer) || reflects(PUBLIC_GATEWAY.as_bytes()) {
                    return Err("WORKSPACE_RESPONSE_REJECTED");
                }
            }
            Ok(NativeWorkspaceResponse {
                status,
                content_type,
                citation_page,
                body,
            })
        })
    }
}

#[cfg(feature = "contract-test")]
pub trait NativeSessionVaultPort: Send + Sync {
    fn read(&self) -> Result<Option<NativeSessionCredentials>, NativeSessionError>;
    fn write(&self, credentials: &NativeSessionCredentials) -> Result<(), NativeSessionError>;
    fn revoke(&self) -> Result<(), NativeSessionError>;
}

#[cfg(not(feature = "contract-test"))]
pub(crate) trait NativeSessionVaultPort: Send + Sync {
    fn read(&self) -> Result<Option<NativeSessionCredentials>, NativeSessionError>;
    fn write(&self, credentials: &NativeSessionCredentials) -> Result<(), NativeSessionError>;
    fn revoke(&self) -> Result<(), NativeSessionError>;
}

#[cfg(feature = "contract-test")]
pub trait NativeIdentityTransportPort: Send + Sync {
    fn login<'a>(
        &'a self,
        login_id: String,
        password: String,
    ) -> Pin<
        Box<dyn Future<Output = Result<NativeSessionCredentials, NativeSessionError>> + Send + 'a>,
    >;

    fn refresh<'a>(
        &'a self,
        credentials: &'a NativeSessionCredentials,
    ) -> Pin<
        Box<dyn Future<Output = Result<NativeSessionCredentials, NativeSessionError>> + Send + 'a>,
    >;

    fn recovery_authorization<'a>(
        &'a self,
        _credentials: &'a NativeSessionCredentials,
    ) -> Pin<
        Box<
            dyn Future<Output = Result<NativeRecoveryAuthorizationProjection, NativeSessionError>>
                + Send
                + 'a,
        >,
    > {
        Box::pin(async { Err(NativeSessionError::authentication_required()) })
    }
}

#[cfg(not(feature = "contract-test"))]
pub(crate) trait NativeIdentityTransportPort: Send + Sync {
    fn login<'a>(
        &'a self,
        login_id: String,
        password: String,
    ) -> Pin<
        Box<dyn Future<Output = Result<NativeSessionCredentials, NativeSessionError>> + Send + 'a>,
    >;

    fn refresh<'a>(
        &'a self,
        credentials: &'a NativeSessionCredentials,
    ) -> Pin<
        Box<dyn Future<Output = Result<NativeSessionCredentials, NativeSessionError>> + Send + 'a>,
    >;

    fn recovery_authorization<'a>(
        &'a self,
        _credentials: &'a NativeSessionCredentials,
    ) -> Pin<
        Box<
            dyn Future<Output = Result<NativeRecoveryAuthorizationProjection, NativeSessionError>>
                + Send
                + 'a,
        >,
    > {
        Box::pin(async { Err(NativeSessionError::authentication_required()) })
    }
}

#[derive(Clone, Serialize)]
pub struct NativeSessionStatus {
    authenticated: bool,
    session: Option<NativeSessionProjection>,
}

const RECOVERY_OPERATIONS: [&str; 7] = [
    "cloud_backup_create",
    "cloud_backup_get",
    "cloud_backup_list",
    "cloud_restore_cancel",
    "cloud_restore_execute",
    "cloud_restore_get",
    "cloud_restore_preview",
];

#[derive(Clone, Debug, Serialize)]
pub struct NativeRecoveryAuthorizationStatus {
    recovery_operations: Vec<String>,
}

impl NativeRecoveryAuthorizationStatus {
    fn new(recovery_operations: Vec<String>) -> Self {
        Self {
            recovery_operations,
        }
    }

    pub fn recovery_operations(&self) -> &[String] {
        &self.recovery_operations
    }
}

#[derive(Clone)]
pub struct NativeRecoveryAuthorizationProjection {
    session: NativeSessionProjection,
    client_kind: String,
    delivery: String,
    recovery_operations: Vec<String>,
}

impl NativeRecoveryAuthorizationProjection {
    fn matches(&self, expected: &NativeSessionProjection) -> bool {
        self.client_kind == "native"
            && self.delivery == "native_https_opaque_bearer"
            && self.session == *expected
    }

    pub fn recovery_operations(&self) -> &[String] {
        &self.recovery_operations
    }

    #[cfg(feature = "contract-test")]
    #[doc(hidden)]
    pub fn for_contract_test(
        session: NativeSessionProjection,
        client_kind: String,
        delivery: String,
        recovery_operations: Vec<String>,
    ) -> Self {
        Self {
            session,
            client_kind,
            delivery,
            recovery_operations,
        }
    }
}

impl NativeSessionStatus {
    pub fn authenticated(projection: &NativeSessionProjection) -> Self {
        Self {
            authenticated: true,
            session: Some(projection.clone()),
        }
    }

    pub fn unauthenticated() -> Self {
        Self {
            authenticated: false,
            session: None,
        }
    }

    pub fn is_authenticated(&self) -> bool {
        self.authenticated
    }
}

impl fmt::Debug for NativeSessionStatus {
    fn fmt(&self, formatter: &mut fmt::Formatter<'_>) -> fmt::Result {
        formatter
            .debug_struct("NativeSessionStatus")
            .field("authenticated", &self.authenticated)
            .field("session", &self.session)
            .finish()
    }
}

pub struct NativeIdentityClient {
    client: reqwest::Client,
    login_endpoint: String,
    refresh_endpoint: String,
    authorization_endpoint: String,
    drop_audit: Option<Arc<AtomicUsize>>,
}

impl NativeIdentityClient {
    pub fn fixed() -> Result<Self, NativeSessionError> {
        Self::for_gateway(PUBLIC_GATEWAY)
    }

    pub fn for_gateway(gateway: &str) -> Result<Self, NativeSessionError> {
        if gateway != PUBLIC_GATEWAY {
            return Err(NativeSessionError::authentication_required());
        }
        let client = reqwest::Client::builder()
            .https_only(true)
            .redirect(reqwest::redirect::Policy::none())
            .connect_timeout(std::time::Duration::from_secs(CONNECT_TIMEOUT_SECONDS))
            .timeout(std::time::Duration::from_secs(REQUEST_TIMEOUT_SECONDS))
            .build()
            .map_err(|_| NativeSessionError::authentication_required())?;
        Ok(Self {
            client,
            login_endpoint: NATIVE_LOGIN_ENDPOINT.to_owned(),
            refresh_endpoint: NATIVE_REFRESH_ENDPOINT.to_owned(),
            authorization_endpoint: NATIVE_AUTHORIZATION_ENDPOINT.to_owned(),
            drop_audit: None,
        })
    }

    #[cfg(feature = "contract-test")]
    #[doc(hidden)]
    pub fn for_contract_test(
        gateway: &str,
        timeout: std::time::Duration,
    ) -> Result<Self, NativeSessionError> {
        if !gateway.starts_with("http://127.0.0.1:")
            || gateway.contains('\r')
            || gateway.contains('\n')
        {
            return Err(NativeSessionError::authentication_required());
        }
        let client = reqwest::Client::builder()
            .redirect(reqwest::redirect::Policy::none())
            .connect_timeout(timeout)
            .timeout(timeout)
            .build()
            .map_err(|_| NativeSessionError::authentication_required())?;
        Ok(Self {
            client,
            login_endpoint: format!("{gateway}{NATIVE_LOGIN_PATH}"),
            refresh_endpoint: format!("{gateway}{NATIVE_REFRESH_PATH}"),
            authorization_endpoint: format!("{gateway}{NATIVE_AUTHORIZATION_PATH}"),
            drop_audit: None,
        })
    }

    #[cfg(feature = "contract-test")]
    #[doc(hidden)]
    pub fn for_contract_test_with_drop_audit(
        gateway: &str,
        timeout: std::time::Duration,
        drop_audit: Arc<AtomicUsize>,
    ) -> Result<Self, NativeSessionError> {
        let mut client = Self::for_contract_test(gateway, timeout)?;
        client.drop_audit = Some(drop_audit);
        Ok(client)
    }

    pub fn endpoint(&self, path: &str) -> Result<&str, NativeSessionError> {
        match path {
            NATIVE_LOGIN_PATH => Ok(&self.login_endpoint),
            NATIVE_REFRESH_PATH => Ok(&self.refresh_endpoint),
            NATIVE_AUTHORIZATION_PATH => Ok(&self.authorization_endpoint),
            _ => Err(NativeSessionError::authentication_required()),
        }
    }

    pub async fn login(
        &self,
        login_id: String,
        password: String,
    ) -> Result<NativeSessionCredentials, NativeSessionError> {
        let mut request = NativeLoginRequest {
            login_id,
            password,
            drop_audit: self.drop_audit.clone(),
        };
        if !valid_safe_text(&request.login_id)
            || request.password.is_empty()
            || request.password.len() > 256
        {
            request.wipe();
            return Err(NativeSessionError::authentication_required());
        }
        let response = self
            .client
            .post(self.endpoint(NATIVE_LOGIN_PATH)?)
            .json(&request)
            .send()
            .await;
        request.wipe();
        self.credentials_from_response(response).await
    }

    pub async fn refresh_once(
        &self,
        credentials: &NativeSessionCredentials,
    ) -> Result<NativeSessionCredentials, NativeSessionError> {
        let mut request = NativeRefreshRequest {
            refresh_credential: credentials
                .refresh
                .to_text()
                .map_err(|_| NativeSessionError::authentication_required())?,
            drop_audit: self.drop_audit.clone(),
        };
        let response = self
            .client
            .post(self.endpoint(NATIVE_REFRESH_PATH)?)
            .json(&request)
            .send()
            .await;
        request.wipe();
        self.credentials_from_response(response).await
    }

    pub async fn fetch_recovery_authorization(
        &self,
        credentials: &NativeSessionCredentials,
    ) -> Result<NativeRecoveryAuthorizationProjection, NativeSessionError> {
        let access = std::str::from_utf8(&credentials.access.0)
            .map_err(|_| NativeSessionError::authentication_required())?;
        let response = self
            .client
            .get(self.endpoint(NATIVE_AUTHORIZATION_PATH)?)
            .bearer_auth(access)
            .send()
            .await;
        let projection = self.authorization_from_response(response).await?;
        if !projection.matches(credentials.projection()) {
            return Err(NativeSessionError::authentication_required());
        }
        Ok(projection)
    }

    async fn authorization_from_response(
        &self,
        response: Result<reqwest::Response, reqwest::Error>,
    ) -> Result<NativeRecoveryAuthorizationProjection, NativeSessionError> {
        let mut response = response.map_err(|_| NativeSessionError::authentication_required())?;
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
        if status != 200
            || response.headers().contains_key(reqwest::header::SET_COOKIE)
            || content_type
                .as_deref()
                .unwrap_or("")
                .to_ascii_lowercase()
                .split(';')
                .next()
                != Some("application/json")
            || content_length.is_some_and(|length| length > MAX_RESPONSE_BYTES)
        {
            return Err(NativeSessionError::authentication_required());
        }
        let mut payload = Zeroizing::new(Vec::new());
        while let Some(chunk) = response
            .chunk()
            .await
            .map_err(|_| NativeSessionError::authentication_required())?
        {
            if payload.len().saturating_add(chunk.len()) > MAX_RESPONSE_BYTES {
                return Err(NativeSessionError::authentication_required());
            }
            payload.extend_from_slice(&chunk);
        }
        let envelope: NativeRecoveryAuthorizationEnvelope = serde_json::from_slice(&payload)
            .map_err(|_| NativeSessionError::authentication_required())?;
        envelope.into_projection()
    }

    async fn credentials_from_response(
        &self,
        response: Result<reqwest::Response, reqwest::Error>,
    ) -> Result<NativeSessionCredentials, NativeSessionError> {
        let mut response = response.map_err(|_| NativeSessionError::authentication_required())?;
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
        let has_set_cookie = response.headers().contains_key(reqwest::header::SET_COOKIE);
        if content_length.is_some_and(|length| length > MAX_RESPONSE_BYTES) {
            return Err(NativeSessionError::authentication_required());
        }
        let mut payload = SecretResponseBuffer::new(self.drop_audit.clone());
        while let Some(chunk) = response
            .chunk()
            .await
            .map_err(|_| NativeSessionError::authentication_required())?
        {
            if payload.bytes.len().saturating_add(chunk.len()) > MAX_RESPONSE_BYTES {
                return Err(NativeSessionError::authentication_required());
            }
            payload.bytes.extend_from_slice(&chunk);
        }
        NativeHttpResponse::from_parts(
            status,
            content_type,
            content_length,
            has_set_cookie,
            std::mem::take(&mut payload.bytes),
        )
        .with_drop_audit(self.drop_audit.clone())
        .into_credentials()
    }
}

impl fmt::Debug for NativeIdentityClient {
    fn fmt(&self, formatter: &mut fmt::Formatter<'_>) -> fmt::Result {
        formatter.write_str("NativeIdentityClient([redacted])")
    }
}

impl NativeIdentityTransportPort for NativeIdentityClient {
    fn login<'a>(
        &'a self,
        login_id: String,
        password: String,
    ) -> Pin<
        Box<dyn Future<Output = Result<NativeSessionCredentials, NativeSessionError>> + Send + 'a>,
    > {
        Box::pin(NativeIdentityClient::login(self, login_id, password))
    }

    fn refresh<'a>(
        &'a self,
        credentials: &'a NativeSessionCredentials,
    ) -> Pin<
        Box<dyn Future<Output = Result<NativeSessionCredentials, NativeSessionError>> + Send + 'a>,
    > {
        Box::pin(NativeIdentityClient::refresh_once(self, credentials))
    }

    fn recovery_authorization<'a>(
        &'a self,
        credentials: &'a NativeSessionCredentials,
    ) -> Pin<
        Box<
            dyn Future<Output = Result<NativeRecoveryAuthorizationProjection, NativeSessionError>>
                + Send
                + 'a,
        >,
    > {
        Box::pin(NativeIdentityClient::fetch_recovery_authorization(
            self,
            credentials,
        ))
    }
}

#[derive(Serialize)]
struct NativeLoginRequest {
    login_id: String,
    password: String,
    #[serde(skip)]
    drop_audit: Option<Arc<AtomicUsize>>,
}

impl NativeLoginRequest {
    fn wipe(&mut self) {
        wipe_text(&mut self.password);
        if let Some(audit) = &self.drop_audit {
            audit.fetch_add(1, Ordering::AcqRel);
        }
    }
}

impl Drop for NativeLoginRequest {
    fn drop(&mut self) {
        self.wipe();
    }
}

#[derive(Serialize)]
struct NativeRefreshRequest {
    refresh_credential: String,
    #[serde(skip)]
    drop_audit: Option<Arc<AtomicUsize>>,
}

impl NativeRefreshRequest {
    fn wipe(&mut self) {
        wipe_text(&mut self.refresh_credential);
        if let Some(audit) = &self.drop_audit {
            audit.fetch_add(1, Ordering::AcqRel);
        }
    }
}

impl Drop for NativeRefreshRequest {
    fn drop(&mut self) {
        self.wipe();
    }
}

#[derive(Deserialize)]
#[serde(deny_unknown_fields)]
struct NativeSessionEnvelope {
    data: NativeSessionWire,
}

#[derive(Deserialize)]
#[serde(deny_unknown_fields)]
struct NativeRecoveryAuthorizationEnvelope {
    data: NativeRecoveryAuthorizationWire,
    meta: NativeRecoveryAuthorizationMeta,
}

impl NativeRecoveryAuthorizationEnvelope {
    fn into_projection(self) -> Result<NativeRecoveryAuthorizationProjection, NativeSessionError> {
        if !valid_safe_text(&self.meta.trace_id) {
            return Err(NativeSessionError::authentication_required());
        }
        self.data.into_projection()
    }
}

#[derive(Deserialize)]
#[serde(deny_unknown_fields)]
struct NativeRecoveryAuthorizationMeta {
    trace_id: String,
}

#[derive(Deserialize)]
#[serde(deny_unknown_fields)]
struct NativeRecoveryAuthorizationWire {
    user_id: String,
    tenant_id: String,
    workspace_id: String,
    session_id: String,
    device_id: String,
    client_kind: String,
    delivery: String,
    expires_at: String,
    recovery_operations: Vec<String>,
}

impl NativeRecoveryAuthorizationWire {
    fn into_projection(self) -> Result<NativeRecoveryAuthorizationProjection, NativeSessionError> {
        if !matches!(self.recovery_operations.len(), 0 | 7)
            || self
                .recovery_operations
                .windows(2)
                .any(|pair| pair[0] >= pair[1])
            || self.recovery_operations.iter().any(|operation| {
                RECOVERY_OPERATIONS
                    .binary_search(&operation.as_str())
                    .is_err()
            })
        {
            return Err(NativeSessionError::authentication_required());
        }
        let session = NativeSessionProjection::new(
            self.user_id,
            self.tenant_id,
            self.workspace_id,
            self.session_id,
            self.device_id,
            self.expires_at,
        )
        .map_err(|_| NativeSessionError::authentication_required())?;
        Ok(NativeRecoveryAuthorizationProjection {
            session,
            client_kind: self.client_kind,
            delivery: self.delivery,
            recovery_operations: self.recovery_operations,
        })
    }
}

#[derive(Deserialize)]
#[serde(deny_unknown_fields)]
struct NativeSessionWire {
    user_id: String,
    tenant_id: String,
    workspace_id: String,
    session_id: String,
    device_id: String,
    client_kind: String,
    delivery: String,
    access_credential: SecretString,
    refresh_credential: SecretString,
    expires_at: String,
    #[serde(skip)]
    drop_audit: Option<Arc<AtomicUsize>>,
}

impl NativeSessionWire {
    fn into_credentials(mut self) -> Result<NativeSessionCredentials, NativeSessionError> {
        let result =
            if self.client_kind != "native" || self.delivery != "native_https_opaque_bearer" {
                Err(NativeSessionError::authentication_required())
            } else {
                let projection = NativeSessionProjection::new(
                    std::mem::take(&mut self.user_id),
                    std::mem::take(&mut self.tenant_id),
                    std::mem::take(&mut self.workspace_id),
                    std::mem::take(&mut self.session_id),
                    std::mem::take(&mut self.device_id),
                    std::mem::take(&mut self.expires_at),
                )
                .map_err(|_| NativeSessionError::authentication_required())?;
                NativeSessionCredentials::new(
                    self.access_credential.take(),
                    self.refresh_credential.take(),
                    projection,
                )
                .map_err(|_| NativeSessionError::authentication_required())
            };
        result
    }
}

impl Drop for NativeSessionWire {
    fn drop(&mut self) {
        self.access_credential.0.zeroize();
        self.refresh_credential.0.zeroize();
        if let Some(audit) = &self.drop_audit {
            audit.fetch_add(1, Ordering::AcqRel);
        }
    }
}

struct SecretResponseBuffer {
    bytes: Zeroizing<Vec<u8>>,
    drop_audit: Option<Arc<AtomicUsize>>,
}

impl SecretResponseBuffer {
    fn new(drop_audit: Option<Arc<AtomicUsize>>) -> Self {
        Self {
            bytes: Zeroizing::new(Vec::new()),
            drop_audit,
        }
    }
}

impl Drop for SecretResponseBuffer {
    fn drop(&mut self) {
        self.bytes.zeroize();
        if let Some(audit) = &self.drop_audit {
            audit.fetch_add(1, Ordering::AcqRel);
        }
    }
}

pub struct NativeSessionRuntime {
    client: Arc<dyn NativeIdentityTransportPort>,
    vault: Arc<dyn NativeSessionVaultPort>,
    workspace_client: Arc<dyn NativeWorkspaceTransport>,
    transition_gate: tokio::sync::Mutex<()>,
    generation: AtomicU64,
    revoke_pending: AtomicBool,
}

impl NativeSessionRuntime {
    pub fn new() -> Self {
        Self {
            client: Arc::new(
                NativeIdentityClient::fixed().expect("fixed Native public gateway must build"),
            ),
            vault: Arc::new(NativeSessionVault::for_app()),
            workspace_client: Arc::new(
                NativeWorkspaceClient::fixed().expect("fixed Native Workspace gateway must build"),
            ),
            transition_gate: tokio::sync::Mutex::new(()),
            generation: AtomicU64::new(0),
            revoke_pending: AtomicBool::new(false),
        }
    }

    #[cfg(feature = "contract-test")]
    #[doc(hidden)]
    pub fn for_contract_test(
        vault: Arc<dyn NativeSessionVaultPort>,
        client: Arc<dyn NativeIdentityTransportPort>,
    ) -> Self {
        Self {
            client,
            vault,
            workspace_client: Arc::new(
                NativeWorkspaceClient::fixed().expect("fixed Native Workspace gateway must build"),
            ),
            transition_gate: tokio::sync::Mutex::new(()),
            generation: AtomicU64::new(0),
            revoke_pending: AtomicBool::new(false),
        }
    }

    pub async fn login(
        &self,
        login_id: String,
        password: String,
    ) -> Result<NativeSessionStatus, NativeSessionError> {
        let _guard = self.transition_gate.lock().await;
        if self.revoke_pending.load(Ordering::Acquire) {
            if self.vault_revoke().await.is_err() {
                return Err(NativeSessionError::authentication_required());
            }
            self.revoke_pending.store(false, Ordering::Release);
        }
        let credentials = match self.client.login(login_id, password).await {
            Ok(value) => value,
            Err(_) => return self.fail_closed().await,
        };
        if self.vault_write(&credentials).await.is_err() {
            return self.fail_closed().await;
        }
        self.revoke_pending.store(false, Ordering::Release);
        self.generation.fetch_add(1, Ordering::AcqRel);
        Ok(NativeSessionStatus::authenticated(credentials.projection()))
    }

    pub async fn refresh_once(&self) -> Result<NativeSessionStatus, NativeSessionError> {
        let arrival_generation = self.generation.load(Ordering::Acquire);
        let _guard = self.transition_gate.lock().await;
        if self.revoke_pending.load(Ordering::Acquire) && self.vault_revoke().await.is_err() {
            return Err(NativeSessionError::authentication_required());
        }
        self.revoke_pending.store(false, Ordering::Release);
        if self.generation.load(Ordering::Acquire) != arrival_generation {
            return match self.vault_read().await? {
                Some(credentials) => {
                    Ok(NativeSessionStatus::authenticated(credentials.projection()))
                }
                None => Err(NativeSessionError::authentication_required()),
            };
        }
        let current = match self.vault_read().await {
            Ok(Some(value)) => value,
            _ => return self.fail_closed().await,
        };
        let replacement = match self.client.refresh(&current).await {
            Ok(value) => value,
            Err(_) => return self.fail_closed().await,
        };
        if self.vault_write(&replacement).await.is_err() {
            return self.fail_closed().await;
        }
        self.generation.fetch_add(1, Ordering::AcqRel);
        Ok(NativeSessionStatus::authenticated(replacement.projection()))
    }

    pub async fn logout(&self) -> Result<NativeSessionStatus, NativeSessionError> {
        let _guard = self.transition_gate.lock().await;
        if self.vault_revoke().await.is_err() {
            self.revoke_pending.store(true, Ordering::Release);
            return Err(NativeSessionError::authentication_required());
        }
        self.revoke_pending.store(false, Ordering::Release);
        self.generation.fetch_add(1, Ordering::AcqRel);
        Ok(NativeSessionStatus::unauthenticated())
    }

    pub async fn status(&self) -> Result<NativeSessionStatus, NativeSessionError> {
        if self.revoke_pending.load(Ordering::Acquire) {
            return Err(NativeSessionError::authentication_required());
        }
        match self.vault_read().await? {
            Some(credentials) => Ok(NativeSessionStatus::authenticated(credentials.projection())),
            None => Ok(NativeSessionStatus::unauthenticated()),
        }
    }

    pub async fn recovery_authorization_status(
        &self,
    ) -> Result<NativeRecoveryAuthorizationStatus, NativeSessionError> {
        if self.revoke_pending.load(Ordering::Acquire) {
            return Err(NativeSessionError::authentication_required());
        }
        let credentials = self
            .vault_read()
            .await?
            .ok_or_else(NativeSessionError::authentication_required)?;
        let projection = self.client.recovery_authorization(&credentials).await?;
        if !projection.matches(credentials.projection()) {
            return Err(NativeSessionError::authentication_required());
        }
        Ok(NativeRecoveryAuthorizationStatus::new(
            projection.recovery_operations,
        ))
    }

    pub(crate) async fn execute_cloud_once(
        &self,
        transport: &dyn crate::recovery_bridge::CloudRecoveryTransport,
        request: crate::recovery_bridge::CloudRecoveryRequest,
    ) -> Result<crate::recovery_bridge::CloudRecoveryExchange, &'static str> {
        if self.revoke_pending.load(Ordering::Acquire) {
            return Err("AUTHENTICATION_REQUIRED");
        }
        let credentials = self
            .vault_read()
            .await
            .map_err(|_| "AUTHENTICATION_REQUIRED")?
            .ok_or("AUTHENTICATION_REQUIRED")?;
        let canaries =
            crate::recovery_bridge::CloudResponseCanaries::for_access(&credentials.access.0);
        let response = transport.execute(&credentials.access.0, request).await?;
        Ok(crate::recovery_bridge::CloudRecoveryExchange { response, canaries })
    }

    pub(crate) async fn execute_workspace_once(
        &self,
        operation: NativeWorkspaceOperation,
    ) -> Result<NativeWorkspaceResponse, &'static str> {
        if self.revoke_pending.load(Ordering::Acquire) {
            return Err("AUTHENTICATION_REQUIRED");
        }
        let credentials = self
            .vault_read()
            .await
            .map_err(|_| "AUTHENTICATION_REQUIRED")?
            .ok_or("AUTHENTICATION_REQUIRED")?;
        if credentials.projection().workspace_id() != operation.workspace_id() {
            return Err("AUTHENTICATION_REQUIRED");
        }
        let response = self
            .workspace_client
            .execute(&credentials.access.0, operation)
            .await?;
        let reflects = |needle: &[u8]| {
            !needle.is_empty()
                && response
                    .body
                    .windows(needle.len())
                    .any(|candidate| candidate == needle)
        };
        if reflects(&credentials.access.0) || reflects(&credentials.refresh.0) {
            return Err("WORKSPACE_RESPONSE_REJECTED");
        }
        Ok(response)
    }

    #[cfg(feature = "contract-test")]
    pub(crate) fn for_workspace_contract_test(
        vault: Arc<dyn NativeSessionVaultPort>,
        client: Arc<dyn NativeIdentityTransportPort>,
        workspace_client: Arc<dyn NativeWorkspaceTransport>,
    ) -> Self {
        Self {
            client,
            vault,
            workspace_client,
            transition_gate: tokio::sync::Mutex::new(()),
            generation: AtomicU64::new(0),
            revoke_pending: AtomicBool::new(false),
        }
    }

    async fn vault_read(&self) -> Result<Option<NativeSessionCredentials>, NativeSessionError> {
        let vault = Arc::clone(&self.vault);
        tauri::async_runtime::spawn_blocking(move || vault.read())
            .await
            .map_err(|_| NativeSessionError::authentication_required())?
            .map_err(|_| NativeSessionError::authentication_required())
    }

    async fn vault_write(
        &self,
        credentials: &NativeSessionCredentials,
    ) -> Result<(), NativeSessionError> {
        let persisted = credentials
            .persisted_bytes()
            .map_err(|_| NativeSessionError::authentication_required())?;
        let vault = Arc::clone(&self.vault);
        let result = tauri::async_runtime::spawn_blocking(move || {
            let mut persisted = persisted;
            let decoded = NativeSessionCredentials::from_persisted_bytes(&persisted)
                .map_err(|_| NativeSessionError::authentication_required());
            persisted.zeroize();
            let credentials = decoded?;
            vault
                .write(&credentials)
                .map_err(|_| NativeSessionError::authentication_required())
        })
        .await
        .map_err(|_| NativeSessionError::authentication_required())?;
        result
    }

    async fn vault_revoke(&self) -> Result<(), NativeSessionError> {
        let vault = Arc::clone(&self.vault);
        tauri::async_runtime::spawn_blocking(move || vault.revoke())
            .await
            .map_err(|_| NativeSessionError::authentication_required())?
            .map_err(|_| NativeSessionError::authentication_required())
    }

    async fn fail_closed<T>(&self) -> Result<T, NativeSessionError> {
        if self.vault_revoke().await.is_err() {
            self.revoke_pending.store(true, Ordering::Release);
        }
        Err(NativeSessionError::authentication_required())
    }
}

#[cfg(all(test, feature = "contract-test"))]
mod workspace_transport_tests {
    use super::*;
    use std::{
        io::{Read, Write},
        net::TcpListener,
        sync::mpsc,
        thread,
        time::Duration,
    };

    fn list_operation() -> NativeWorkspaceOperation {
        NativeWorkspaceOperation::ListSources {
            workspace_id: "workspace-1".into(),
        }
    }

    fn block_on<T>(future: impl Future<Output = T>) -> T {
        tokio::runtime::Builder::new_current_thread()
            .enable_all()
            .build()
            .expect("runtime")
            .block_on(future)
    }

    fn server(response: Vec<u8>, delay: Duration) -> (String, mpsc::Receiver<Vec<u8>>) {
        let listener = TcpListener::bind("127.0.0.1:0").expect("listener");
        let address = format!("http://{}", listener.local_addr().expect("address"));
        let (tx, rx) = mpsc::channel();
        thread::spawn(move || {
            let (mut stream, _) = listener.accept().expect("accept");
            stream
                .set_read_timeout(Some(Duration::from_millis(200)))
                .expect("read timeout");
            let mut request = vec![0_u8; 16 * 1024];
            let read = stream.read(&mut request).unwrap_or(0);
            request.truncate(read);
            tx.send(request).ok();
            thread::sleep(delay);
            let _ = stream.write_all(&response);
        });
        (address, rx)
    }

    #[test]
    fn workspace_operation_method_and_path_are_fixed() {
        let cases = [
            (
                NativeWorkspaceOperation::ListSources {
                    workspace_id: "workspace-1".into(),
                },
                "GET",
                "/api/v1/workspaces/workspace-1/sources",
            ),
            (
                NativeWorkspaceOperation::UploadPdf {
                    workspace_id: "workspace-1".into(),
                    filename: "guide.pdf".into(),
                    bytes: Zeroizing::new(b"%PDF-".to_vec()),
                    idempotency_key: Zeroizing::new("native-0123456789abcdef".into()),
                },
                "POST",
                "/api/v1/workspaces/workspace-1/sources",
            ),
            (
                NativeWorkspaceOperation::ProcessingStatus {
                    workspace_id: "workspace-1".into(),
                    processing_run_id: "run-1".into(),
                },
                "GET",
                "/api/v1/workspaces/workspace-1/processing-runs/run-1",
            ),
            (
                NativeWorkspaceOperation::AskQuestion {
                    workspace_id: "workspace-1".into(),
                    body: Zeroizing::new(Vec::new()),
                    idempotency_key: Zeroizing::new("native-0123456789abcdef".into()),
                },
                "POST",
                "/api/v1/workspaces/workspace-1/questions",
            ),
            (
                NativeWorkspaceOperation::CitationContent {
                    workspace_id: "workspace-1".into(),
                    citation_id: "citation-1".into(),
                },
                "GET",
                "/api/v1/workspaces/workspace-1/citations/citation-1/content",
            ),
            (
                NativeWorkspaceOperation::CreateReport {
                    workspace_id: "workspace-1".into(),
                    body: Zeroizing::new(Vec::new()),
                    idempotency_key: Zeroizing::new("native-0123456789abcdef".into()),
                },
                "POST",
                "/api/v1/workspaces/workspace-1/studio/reports",
            ),
            (
                NativeWorkspaceOperation::ListStudioOutputs {
                    workspace_id: "workspace-1".into(),
                },
                "GET",
                "/api/v1/workspaces/workspace-1/studio/outputs",
            ),
        ];
        for (operation, method, path) in cases {
            assert_eq!(operation.method_path(), (method, path.into()));
        }
    }

    #[test]
    fn workspace_idempotency_key_is_stable_and_fingerprint_bound() {
        let first = workspace_idempotency_key("studio:workspace-1:report-000000000001", b"body")
            .expect("first key");
        let replay = workspace_idempotency_key("studio:workspace-1:report-000000000001", b"body")
            .expect("replay key");
        let changed = workspace_idempotency_key("studio:workspace-1:report-000000000002", b"body")
            .expect("changed key");
        assert_eq!(first.as_str(), replay.as_str());
        assert_ne!(first.as_str(), changed.as_str());
        assert!((16..=128).contains(&first.len()));
    }

    #[test]
    fn actual_workspace_transport_preserves_status_and_citation_page() {
        let response = b"HTTP/1.1 200 OK\r\nContent-Type: application/pdf\r\nX-Citation-Page: 7\r\nContent-Length: 5\r\n\r\n%PDF-".to_vec();
        let (gateway, request) = server(response, Duration::ZERO);
        let client = NativeWorkspaceClient::for_contract_test(&gateway, Duration::from_millis(100))
            .expect("client");
        let result = block_on(client.execute(
            b"a-credential-that-is-at-least-forty-bytes-long",
            NativeWorkspaceOperation::CitationContent {
                workspace_id: "workspace-1".into(),
                citation_id: "citation-1".into(),
            },
        ))
        .expect("citation response");
        assert_eq!(result.status, 200);
        assert_eq!(result.citation_page, Some(7));
        assert!(
            String::from_utf8_lossy(&request.recv().expect("request")).starts_with(
                "GET /api/v1/workspaces/workspace-1/citations/citation-1/content HTTP/1.1"
            )
        );
    }

    #[test]
    fn actual_workspace_write_preserves_status_body_and_idempotency_header() {
        let response = b"HTTP/1.1 201 Created\r\nContent-Type: application/json\r\nContent-Length: 2\r\n\r\n{}".to_vec();
        let (gateway, request) = server(response, Duration::ZERO);
        let client = NativeWorkspaceClient::for_contract_test(&gateway, Duration::from_millis(100))
            .expect("client");
        let result = block_on(client.execute(
            b"a-credential-that-is-at-least-forty-bytes-long",
            NativeWorkspaceOperation::CreateReport {
                workspace_id: "workspace-1".into(),
                body: Zeroizing::new(b"{}".to_vec()),
                idempotency_key: Zeroizing::new("native-0123456789abcdef".into()),
            },
        ))
        .expect("write response");
        assert_eq!(result.status, 201);
        let wire = String::from_utf8_lossy(&request.recv().expect("request")).to_ascii_lowercase();
        assert!(wire.starts_with("post /api/v1/workspaces/workspace-1/studio/reports http/1.1"));
        assert!(wire.contains("idempotency-key: native-0123456789abcdef"));
        assert!(wire.ends_with("\r\n\r\n{}"));
    }

    #[test]
    fn actual_workspace_transport_never_follows_redirect() {
        let destination = TcpListener::bind("127.0.0.1:0").expect("destination");
        destination.set_nonblocking(true).expect("nonblocking");
        let location = format!(
            "http://{}",
            destination.local_addr().expect("destination address")
        );
        let response = format!(
            "HTTP/1.1 307 Temporary Redirect\r\nLocation: {location}\r\nContent-Length: 0\r\n\r\n"
        )
        .into_bytes();
        let (gateway, request) = server(response, Duration::ZERO);
        let client = NativeWorkspaceClient::for_contract_test(&gateway, Duration::from_millis(100))
            .expect("client");
        assert_eq!(
            block_on(client.execute(
                b"a-credential-that-is-at-least-forty-bytes-long",
                list_operation()
            ))
            .err(),
            Some("WORKSPACE_RESPONSE_REJECTED")
        );
        let request_bytes = request
            .recv_timeout(Duration::from_secs(1))
            .expect("request");
        let wire = String::from_utf8_lossy(&request_bytes);
        assert!(wire.starts_with("GET /api/v1/workspaces/workspace-1/sources HTTP/1.1"));
        assert!(wire
            .to_ascii_lowercase()
            .contains("authorization: bearer a-credential-that-is-at-least-forty-bytes-long"));
        thread::sleep(Duration::from_millis(20));
        assert!(destination.accept().is_err());
    }

    #[test]
    fn actual_workspace_transport_rejects_oversize_truncation_and_timeout() {
        let fixtures = [
            (b"HTTP/1.1 200 OK\r\nContent-Type: application/json\r\nContent-Length: 131073\r\n\r\n".to_vec(), Duration::ZERO),
            (b"HTTP/1.1 200 OK\r\nContent-Type: application/json\r\nContent-Length: 20\r\n\r\n{}".to_vec(), Duration::ZERO),
            (b"HTTP/1.1 200 OK\r\nContent-Type: application/json\r\nContent-Length: 2\r\n\r\n{}".to_vec(), Duration::from_millis(100)),
        ];
        for (response, delay) in fixtures {
            let (gateway, _) = server(response, delay);
            let client =
                NativeWorkspaceClient::for_contract_test(&gateway, Duration::from_millis(30))
                    .expect("client");
            assert!(block_on(client.execute(
                b"a-credential-that-is-at-least-forty-bytes-long",
                list_operation()
            ))
            .is_err());
        }
    }
}
