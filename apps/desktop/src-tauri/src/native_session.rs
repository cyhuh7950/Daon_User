use serde::{Deserialize, Serialize};
use std::fmt;

#[cfg(windows)]
use std::ptr::null_mut;

#[cfg(windows)]
use windows_sys::Win32::Foundation::{ERROR_NOT_FOUND, GetLastError};
#[cfg(windows)]
use windows_sys::Win32::Security::Credentials::{
    CRED_PERSIST_LOCAL_MACHINE, CRED_TYPE_GENERIC, CREDENTIALW, CredDeleteW, CredFree, CredReadW,
    CredWriteW,
};

pub const NATIVE_SESSION_CREDENTIAL_TARGET: &str = "DaonUser/NativeSession/v1";
pub const LOCAL_STORAGE_CREDENTIAL_TARGET: &str = "DaonUser/LocalStorage/v1";
pub const PUBLIC_GATEWAY: &str = "https://daon-user.sinsan.kr";

const NATIVE_LOGIN_PATH: &str = "/api/v1/auth/native/login";
const NATIVE_REFRESH_PATH: &str = "/api/v1/session/refresh";
const NATIVE_LOGIN_ENDPOINT: &str = "https://daon-user.sinsan.kr/api/v1/auth/native/login";
const NATIVE_REFRESH_ENDPOINT: &str = "https://daon-user.sinsan.kr/api/v1/session/refresh";
const MAX_CREDENTIAL_BYTES: usize = 512;
const MAX_PERSISTED_BYTES: usize = 2560;
const MAX_SAFE_ID_BYTES: usize = 256;

fn wipe_text(value: &mut String) {
    let mut bytes = std::mem::take(value).into_bytes();
    bytes.fill(0);
}

fn valid_safe_text(value: &str) -> bool {
    !value.is_empty() && value.len() <= MAX_SAFE_ID_BYTES && !value.contains('\0')
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
            rejected.fill(0);
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
        self.0.fill(0);
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
                std::mem::take(&mut wire.access_credential),
                std::mem::take(&mut wire.refresh_credential),
                wire.projection,
            )
        };
        wipe_text(&mut wire.access_credential);
        wipe_text(&mut wire.refresh_credential);
        result
    }

    fn persisted_bytes(&self) -> Result<Vec<u8>, &'static str> {
        let mut wire = PersistedNativeSession {
            version: 1,
            access_credential: self.access.to_text()?,
            refresh_credential: self.refresh.to_text()?,
            projection: self.projection.clone(),
        };
        let result = serde_json::to_vec(&wire).map_err(|_| "AUTHENTICATION_REQUIRED");
        wipe_text(&mut wire.access_credential);
        wipe_text(&mut wire.refresh_credential);
        result
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
    access_credential: String,
    refresh_credential: String,
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
            blob.fill(0);
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
        blob.fill(0);
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
        // SAFETY: raw is exactly the allocation returned by CredReadW and freed once.
        unsafe { CredFree(raw.cast()) };
        let result = NativeSessionCredentials::from_persisted_bytes(&blob).map(Some);
        blob.fill(0);
        if result.is_err() {
            let _ = self.revoke();
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
}

impl fmt::Debug for NativeSessionError {
    fn fmt(&self, formatter: &mut fmt::Formatter<'_>) -> fmt::Result {
        formatter
            .debug_struct("NativeSessionError")
            .field("code", &self.code)
            .finish()
    }
}

#[derive(Clone, Serialize)]
pub struct NativeSessionStatus {
    authenticated: bool,
    session: Option<NativeSessionProjection>,
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
            .build()
            .map_err(|_| NativeSessionError::authentication_required())?;
        Ok(Self { client })
    }

    pub fn endpoint(&self, path: &str) -> Result<&'static str, NativeSessionError> {
        match path {
            NATIVE_LOGIN_PATH => Ok(NATIVE_LOGIN_ENDPOINT),
            NATIVE_REFRESH_PATH => Ok(NATIVE_REFRESH_ENDPOINT),
            _ => Err(NativeSessionError::authentication_required()),
        }
    }

    pub async fn login(
        &self,
        login_id: String,
        password: String,
    ) -> Result<NativeSessionCredentials, NativeSessionError> {
        let mut request = NativeLoginRequest { login_id, password };
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

    async fn credentials_from_response(
        &self,
        response: Result<reqwest::Response, reqwest::Error>,
    ) -> Result<NativeSessionCredentials, NativeSessionError> {
        let response = response.map_err(|_| NativeSessionError::authentication_required())?;
        if !response.status().is_success()
            || response.headers().contains_key(reqwest::header::SET_COOKIE)
        {
            return Err(NativeSessionError::authentication_required());
        }
        let mut payload = response
            .bytes()
            .await
            .map_err(|_| NativeSessionError::authentication_required())?
            .to_vec();
        let parsed = serde_json::from_slice::<NativeSessionEnvelope>(&payload);
        payload.fill(0);
        let wire = parsed.map_err(|_| NativeSessionError::authentication_required())?;
        wire.data.into_credentials()
    }
}

impl fmt::Debug for NativeIdentityClient {
    fn fmt(&self, formatter: &mut fmt::Formatter<'_>) -> fmt::Result {
        formatter.write_str("NativeIdentityClient([redacted])")
    }
}

#[derive(Serialize)]
struct NativeLoginRequest {
    login_id: String,
    password: String,
}

impl NativeLoginRequest {
    fn wipe(&mut self) {
        wipe_text(&mut self.password);
    }
}

#[derive(Serialize)]
struct NativeRefreshRequest {
    refresh_credential: String,
}

impl NativeRefreshRequest {
    fn wipe(&mut self) {
        wipe_text(&mut self.refresh_credential);
    }
}

#[derive(Deserialize)]
struct NativeSessionEnvelope {
    data: NativeSessionWire,
}

#[derive(Deserialize)]
struct NativeSessionWire {
    user_id: String,
    tenant_id: String,
    workspace_id: String,
    session_id: String,
    device_id: String,
    client_kind: String,
    delivery: String,
    access_credential: String,
    refresh_credential: String,
    expires_at: String,
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
                    std::mem::take(&mut self.access_credential),
                    std::mem::take(&mut self.refresh_credential),
                    projection,
                )
                .map_err(|_| NativeSessionError::authentication_required())
            };
        wipe_text(&mut self.access_credential);
        wipe_text(&mut self.refresh_credential);
        result
    }
}

pub struct NativeSessionRuntime {
    client: NativeIdentityClient,
    vault: NativeSessionVault,
}

impl NativeSessionRuntime {
    pub fn new() -> Self {
        Self {
            client: NativeIdentityClient::fixed().expect("fixed Native public gateway must build"),
            vault: NativeSessionVault::for_app(),
        }
    }

    pub async fn login(
        &self,
        login_id: String,
        password: String,
    ) -> Result<NativeSessionStatus, NativeSessionError> {
        let credentials = match self.client.login(login_id, password).await {
            Ok(value) => value,
            Err(error) => return self.fail_closed(error),
        };
        if self.vault.write(&credentials).is_err() {
            return self.fail_closed(NativeSessionError::authentication_required());
        }
        Ok(NativeSessionStatus::authenticated(credentials.projection()))
    }

    pub async fn refresh_once(&self) -> Result<NativeSessionStatus, NativeSessionError> {
        let current = self
            .vault
            .read()
            .map_err(|_| NativeSessionError::authentication_required())?
            .ok_or_else(NativeSessionError::authentication_required)?;
        let replacement = match self.client.refresh_once(&current).await {
            Ok(value) => value,
            Err(error) => return self.fail_closed(error),
        };
        if self.vault.write(&replacement).is_err() {
            return self.fail_closed(NativeSessionError::authentication_required());
        }
        Ok(NativeSessionStatus::authenticated(replacement.projection()))
    }

    pub fn logout(&self) -> Result<NativeSessionStatus, NativeSessionError> {
        self.vault
            .revoke()
            .map_err(|_| NativeSessionError::authentication_required())?;
        Ok(NativeSessionStatus::unauthenticated())
    }

    pub fn status(&self) -> Result<NativeSessionStatus, NativeSessionError> {
        match self
            .vault
            .read()
            .map_err(|_| NativeSessionError::authentication_required())?
        {
            Some(credentials) => Ok(NativeSessionStatus::authenticated(credentials.projection())),
            None => Ok(NativeSessionStatus::unauthenticated()),
        }
    }

    fn fail_closed<T>(&self, error: NativeSessionError) -> Result<T, NativeSessionError> {
        let _ = self.vault.revoke();
        Err(error)
    }
}
