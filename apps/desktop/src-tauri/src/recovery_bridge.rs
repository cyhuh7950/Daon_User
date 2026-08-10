use crate::local_service::LocalServiceManager;
use serde::{Deserialize, Serialize};
use sha2::{Digest, Sha256};
use std::fmt;
use std::sync::atomic::{AtomicU64, Ordering};
use std::time::{SystemTime, UNIX_EPOCH};
use time::{format_description::well_known::Rfc3339, OffsetDateTime};

const RESPONSE_MAX_BYTES: usize = 1_048_576;
static FALLBACK_TRACE_COUNTER: AtomicU64 = AtomicU64::new(1);

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
        assert!(first
            .bytes()
            .all(|byte| byte.is_ascii_hexdigit() && !byte.is_ascii_uppercase()));
        assert!(second
            .bytes()
            .all(|byte| byte.is_ascii_hexdigit() && !byte.is_ascii_uppercase()));
        assert_ne!(first, second);
        assert_ne!(first, "00000000000000000000000000000000");
        assert_ne!(second, "00000000000000000000000000000000");
    }
}
