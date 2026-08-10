use daon_user_desktop_lib::local_service::{AppCredentials, LocalServiceManager};
use daon_user_desktop_lib::native_session::{
    NativeIdentityTransportPort, NativeSessionCredentials, NativeSessionError,
    NativeSessionProjection, NativeSessionRuntime, NativeSessionVaultPort,
};
use daon_user_desktop_lib::recovery_bridge::{
    CloudCancelRestoreCommandInput, CloudCreateBackupCommandInput, CloudExecuteRestoreCommandInput,
    CloudGetBackupCommandInput, CloudGetRestoreCommandInput, CloudListBackupsCommandInput,
    CloudPreviewRestoreCommandInput, CloudRecoveryPort, CloudRecoveryRequest,
    CloudRecoveryResponse, CloudRecoveryTransport, CloudSecret, LocalGetJobCommandInput,
    LocalRecoveryJob, LocalRecoveryPort, LocalRecoveryTransport, LocalRepairJobCommandInput,
    LocalStartScanCommandInput, NativeCloudRecoveryClient, NativeRecoveryRuntime,
    RecoveryHttpResponse, RecoveryRepairRequest, RecoveryScanRequest,
    recovery_local_get_job_for_contract, recovery_local_repair_job_for_contract,
    recovery_local_start_scan_for_contract,
};
use std::future::Future;
use std::io::{Read, Write};
use std::net::TcpListener;
use std::pin::Pin;
use std::sync::{
    Arc, Mutex,
    atomic::{AtomicUsize, Ordering},
    mpsc,
};
use std::thread;
use std::time::{Duration, Instant};
use zeroize::Zeroizing;

const WORKSPACE: &str = "55555555-5555-4555-8555-555555555555";
const JOB_ID: &str = "fixture-recovery-0123456789abcdef01234567";
const CLOUD_ACCESS: &str = "access-cloud-recovery-credential-0123456789abcdef";

fn cloud_credentials() -> NativeSessionCredentials {
    NativeSessionCredentials::new(
        CLOUD_ACCESS.to_owned(),
        "refresh-cloud-recovery-credential-0123456789abcdef".to_owned(),
        NativeSessionProjection::new(
            "user-cloud".to_owned(),
            "tenant-cloud".to_owned(),
            "workspace-cloud".to_owned(),
            "session-cloud".to_owned(),
            "device-cloud".to_owned(),
            "2026-08-10T23:59:59Z".to_owned(),
        )
        .expect("projection"),
    )
    .expect("credentials")
}

struct CloudVault;
impl NativeSessionVaultPort for CloudVault {
    fn read(&self) -> Result<Option<NativeSessionCredentials>, NativeSessionError> {
        Ok(Some(cloud_credentials()))
    }
    fn write(&self, _credentials: &NativeSessionCredentials) -> Result<(), NativeSessionError> {
        Ok(())
    }
    fn revoke(&self) -> Result<(), NativeSessionError> {
        Ok(())
    }
}

struct EmptyCloudVault;
impl NativeSessionVaultPort for EmptyCloudVault {
    fn read(&self) -> Result<Option<NativeSessionCredentials>, NativeSessionError> {
        Ok(None)
    }
    fn write(&self, _credentials: &NativeSessionCredentials) -> Result<(), NativeSessionError> {
        Ok(())
    }
    fn revoke(&self) -> Result<(), NativeSessionError> {
        Ok(())
    }
}

struct CloudIdentity;
impl NativeIdentityTransportPort for CloudIdentity {
    fn login<'a>(
        &'a self,
        _login_id: String,
        _password: String,
    ) -> Pin<
        Box<dyn Future<Output = Result<NativeSessionCredentials, NativeSessionError>> + Send + 'a>,
    > {
        Box::pin(async { Ok(cloud_credentials()) })
    }
    fn refresh<'a>(
        &'a self,
        _credentials: &'a NativeSessionCredentials,
    ) -> Pin<
        Box<dyn Future<Output = Result<NativeSessionCredentials, NativeSessionError>> + Send + 'a>,
    > {
        Box::pin(async { Ok(cloud_credentials()) })
    }
}

struct RefreshCountingIdentity(Arc<AtomicUsize>);
impl NativeIdentityTransportPort for RefreshCountingIdentity {
    fn login<'a>(
        &'a self,
        _login_id: String,
        _password: String,
    ) -> Pin<
        Box<dyn Future<Output = Result<NativeSessionCredentials, NativeSessionError>> + Send + 'a>,
    > {
        Box::pin(async { Ok(cloud_credentials()) })
    }
    fn refresh<'a>(
        &'a self,
        _credentials: &'a NativeSessionCredentials,
    ) -> Pin<
        Box<dyn Future<Output = Result<NativeSessionCredentials, NativeSessionError>> + Send + 'a>,
    > {
        self.0.fetch_add(1, Ordering::SeqCst);
        Box::pin(async { Ok(cloud_credentials()) })
    }
}

struct FakeCloudTransport {
    calls: Mutex<Vec<(String, String, String)>>,
    failures: Mutex<Vec<&'static str>>,
}

impl CloudRecoveryTransport for FakeCloudTransport {
    fn execute<'a>(
        &'a self,
        access: &'a [u8],
        request: CloudRecoveryRequest,
    ) -> Pin<Box<dyn Future<Output = Result<CloudRecoveryResponse, &'static str>> + Send + 'a>>
    {
        let access = String::from_utf8(access.to_vec()).expect("access");
        self.calls.lock().expect("calls").push((
            access,
            request.method.to_owned(),
            request.path.clone(),
        ));
        let failure = self.failures.lock().expect("failures").pop();
        let (status, data, etag) = if request.path == "/api/v1/backups" && request.method == "GET" {
            (
                200,
                serde_json::json!([backup_view()]),
                "\"projection-0123456789abcdef01234567\"".to_owned(),
            )
        } else if request.path.contains("restore") {
            (
                if request.path.ends_with("restore-previews") {
                    201
                } else {
                    200
                },
                restore_view(),
                "\"restore:restore-cloud:2\"".to_owned(),
            )
        } else {
            (
                if request.method == "POST" { 201 } else { 200 },
                backup_view(),
                "\"backup:backup-0123456789abcdef01234567:2\"".to_owned(),
            )
        };
        Box::pin(async move {
            if let Some(code) = failure {
                return Err(code);
            }
            let body = cloud_envelope(data);
            Ok(CloudRecoveryResponse {
                status,
                content_type: Some("application/json".to_owned()),
                content_length: Some(body.len()),
                etag: Some(etag),
                body,
            })
        })
    }
}

struct FixedCloudTransport(Vec<u8>);
impl CloudRecoveryTransport for FixedCloudTransport {
    fn execute<'a>(
        &'a self,
        _access: &'a [u8],
        _request: CloudRecoveryRequest,
    ) -> Pin<Box<dyn Future<Output = Result<CloudRecoveryResponse, &'static str>> + Send + 'a>>
    {
        let body = self.0.clone();
        Box::pin(async move {
            Ok(CloudRecoveryResponse {
                status: 200,
                content_type: Some("application/json".to_owned()),
                content_length: Some(body.len()),
                etag: None,
                body,
            })
        })
    }
}

struct ResponseCloudTransport {
    status: u16,
    body: Vec<u8>,
    etag: Option<String>,
    calls: Mutex<usize>,
}

impl CloudRecoveryTransport for ResponseCloudTransport {
    fn execute<'a>(
        &'a self,
        _access: &'a [u8],
        _request: CloudRecoveryRequest,
    ) -> Pin<Box<dyn Future<Output = Result<CloudRecoveryResponse, &'static str>> + Send + 'a>>
    {
        *self.calls.lock().expect("calls") += 1;
        let status = self.status;
        let body = self.body.clone();
        let etag = self.etag.clone();
        Box::pin(async move {
            Ok(CloudRecoveryResponse {
                status,
                content_type: Some("application/json".to_owned()),
                content_length: Some(body.len()),
                etag,
                body,
            })
        })
    }
}

fn backup_view() -> serde_json::Value {
    serde_json::json!({
        "backup_id":"backup-0123456789abcdef01234567",
        "tenant_id":"tenant-cloud",
        "workspace_id":"workspace-cloud",
        "state":"ready",
        "version":2,
        "trigger":"manual",
        "created_at":"2026-08-10T12:00:00+00:00",
        "verified_at":"2026-08-10T12:00:00+00:00",
        "schema_revision":"0006",
        "retention_watermark":"fixture-watermark",
        "manifest_digest":"aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa",
        "object_count":1,
        "transitions":["queued","capturing","verifying","ready"]
    })
}

fn restore_view() -> serde_json::Value {
    serde_json::json!({
        "request_id":"restore-cloud",
        "backup_id":"backup-0123456789abcdef01234567",
        "tenant_id":"tenant-cloud",
        "workspace_id":"workspace-cloud",
        "state":"preview_ready",
        "version":2,
        "preview":{
            "version":1,
            "included_object_ids":["fixture-object-cloud"],
            "excluded_object_ids":[],
            "exclusion_reasons":[],
            "destination":{
                "tenant_id":"fixture-tenant-cloud",
                "workspace_id":"fixture-workspace-cloud",
                "database_id":"fixture-database-cloud",
                "bucket_id":"fixture-bucket-cloud"
            },
            "created_at":"2026-08-10T12:00:00+00:00"
        },
        "transitions":["requested","preview_ready"],
        "verification_digest":null
    })
}

fn cloud_envelope(data: serde_json::Value) -> Vec<u8> {
    serde_json::to_vec(&serde_json::json!({
        "data":data,
        "meta":{"trace_id":"trace-cloud"}
    }))
    .expect("cloud envelope")
}

fn safe_error_envelope(code: &str, retryable: bool) -> Vec<u8> {
    serde_json::to_vec(&serde_json::json!({
        "error":{
            "code":code,
            "message":"request denied",
            "stage":"request",
            "impact":"request_not_completed",
            "retryable":retryable,
            "user_action":"check access",
            "trace_id":"trace-cloud",
            "details":{}
        }
    }))
    .expect("safe error")
}

fn cloud_request(
    method: &'static str,
    path: &str,
    body: serde_json::Value,
    idempotency: Option<&str>,
    if_match: Option<&str>,
    query: Option<&str>,
) -> CloudRecoveryRequest {
    CloudRecoveryRequest {
        method,
        path: path.to_owned(),
        query: query.map(str::to_owned),
        body: Zeroizing::new(if body.is_null() {
            Vec::new()
        } else {
            serde_json::to_vec(&body).expect("body")
        }),
        idempotency_key: idempotency.map(CloudSecret::new),
        if_match: if_match.map(CloudSecret::new),
    }
}

fn command_input<T: serde::de::DeserializeOwned>(value: serde_json::Value) -> T {
    serde_json::from_value(value).expect("command input")
}

fn backup_create_body() -> serde_json::Value {
    serde_json::json!({
        "workspace_id":"workspace-cloud",
        "trigger":"manual",
        "schema_revision":"schema-v1",
        "retention_watermark":"watermark-v1",
        "objects":[{
            "object_id":"fixture-object-cloud",
            "checksum_sha256":"aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa",
            "byte_size":128
        }]
    })
}

fn backup_create_command(idempotency_key: &str) -> CloudCreateBackupCommandInput {
    command_input(serde_json::json!({
        "workspace_id":"workspace-cloud",
        "trigger":"manual",
        "schema_revision":"schema-v1",
        "retention_watermark":"watermark-v1",
        "objects":[{
            "object_id":"fixture-object-cloud",
            "checksum_sha256":"aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa",
            "byte_size":128
        }],
        "idempotency_key":idempotency_key
    }))
}

fn preview_body(step_up: &str) -> serde_json::Value {
    serde_json::json!({
        "destination":{
            "tenant_id":"fixture-tenant-cloud",
            "workspace_id":"fixture-workspace-cloud",
            "database_id":"fixture-database-cloud",
            "bucket_id":"fixture-bucket-cloud"
        },
        "step_up_authorization_id":step_up
    })
}

fn execute_body(step_up: &str) -> serde_json::Value {
    serde_json::json!({"preview_version":1,"step_up_authorization_id":step_up})
}

#[derive(Debug)]
struct Call {
    scope: String,
    capability: String,
    method: String,
    path: String,
    body: Vec<u8>,
}

struct FakeTransport {
    ready: bool,
    calls: Mutex<Vec<Call>>,
    responses: Mutex<Vec<Result<RecoveryHttpResponse, &'static str>>>,
}

impl FakeTransport {
    fn ready(responses: Vec<Result<RecoveryHttpResponse, &'static str>>) -> Self {
        Self {
            ready: true,
            calls: Mutex::new(Vec::new()),
            responses: Mutex::new(responses),
        }
    }

    fn unavailable() -> Self {
        Self {
            ready: false,
            calls: Mutex::new(Vec::new()),
            responses: Mutex::new(Vec::new()),
        }
    }
}

impl LocalRecoveryTransport for FakeTransport {
    fn is_ready(&self) -> bool {
        self.ready
    }

    fn execute(
        &self,
        scope: &str,
        capability: &str,
        method: &str,
        path: &str,
        body: &[u8],
    ) -> Result<RecoveryHttpResponse, &'static str> {
        self.calls.lock().expect("calls").push(Call {
            scope: scope.to_owned(),
            capability: capability.to_owned(),
            method: method.to_owned(),
            path: path.to_owned(),
            body: body.to_vec(),
        });
        self.responses.lock().expect("responses").remove(0)
    }
}

fn job_json(extra: &str) -> Vec<u8> {
    format!(
        r#"{{"data":{{"job_id":"{JOB_ID}","version":4,"state":"repairable","target_id":"fixture-damaged-object","journal_present":true,"recorded_at":"2026-07-31T05:00:00Z","previous_version":3,"integrity":"pending"{extra}}}}}"#
    ).into_bytes()
}

fn custom_job_json(
    job_id: &str,
    version: u64,
    state: &str,
    target_id: &str,
    recorded_at: &str,
    previous_version: &str,
) -> Vec<u8> {
    format!(
        r#"{{"data":{{"job_id":"{job_id}","version":{version},"state":"{state}","target_id":"{target_id}","journal_present":true,"recorded_at":"{recorded_at}","previous_version":{previous_version},"integrity":"pending"}}}}"#
    ).into_bytes()
}

fn response(body: Vec<u8>) -> RecoveryHttpResponse {
    RecoveryHttpResponse {
        status: 200,
        content_type: Some("application/json".to_owned()),
        content_length: Some(body.len()),
        body,
    }
}

fn scan_request() -> RecoveryScanRequest {
    RecoveryScanRequest {
        workspace_id: WORKSPACE.to_owned(),
        target_id: "fixture-damaged-object".to_owned(),
        snapshot_checksum: "a".repeat(64),
        metadata_checksum: "a".repeat(64),
        actual_checksum: "b".repeat(64),
        journal_present: true,
    }
}

#[test]
fn unavailable_service_fails_before_any_network_call_and_never_falls_back() {
    let transport = FakeTransport::unavailable();
    let error = LocalRecoveryPort::new(&transport)
        .scan(scan_request())
        .expect_err("unavailable must fail");
    assert_eq!(error.code(), "LOCAL_SERVICE_UNAVAILABLE");
    assert!(transport.calls.lock().expect("calls").is_empty());
}

#[test]
fn scan_get_and_repair_use_only_approved_method_path_and_scope_pairs() {
    let transport = FakeTransport::ready(vec![
        Ok(response(job_json(""))),
        Ok(response(job_json(""))),
        Ok(response(job_json(""))),
    ]);
    let port = LocalRecoveryPort::new(&transport);
    port.scan(scan_request()).expect("scan");
    port.get_job(JOB_ID).expect("get");
    port.repair(
        JOB_ID,
        RecoveryRepairRequest {
            workspace_id: WORKSPACE.to_owned(),
            expected_version: 4,
        },
    )
    .expect("repair");

    let calls = transport.calls.lock().expect("calls");
    assert_eq!(calls.len(), 3);
    assert_eq!(
        (
            &calls[0].scope,
            &calls[0].capability,
            &calls[0].method,
            &calls[0].path
        ),
        (
            &"recovery.write".to_owned(),
            &"recovery.scan".to_owned(),
            &"POST".to_owned(),
            &"/local/v1/recovery/scans".to_owned()
        )
    );
    assert_eq!(
        (
            &calls[1].scope,
            &calls[1].capability,
            &calls[1].method,
            &calls[1].path
        ),
        (
            &"recovery.read".to_owned(),
            &"recovery.job.read".to_owned(),
            &"GET".to_owned(),
            &format!("/local/v1/recovery/jobs/{JOB_ID}")
        )
    );
    assert!(calls[1].body.is_empty());
    assert_eq!(
        (
            &calls[2].scope,
            &calls[2].capability,
            &calls[2].method,
            &calls[2].path
        ),
        (
            &"recovery.write".to_owned(),
            &"recovery.repair".to_owned(),
            &"POST".to_owned(),
            &format!("/local/v1/recovery/jobs/{JOB_ID}/repair")
        )
    );
}

#[test]
fn invalid_ids_inputs_and_untrusted_responses_fail_closed() {
    let transport = FakeTransport::ready(vec![Ok(response(job_json("")))]);
    let port = LocalRecoveryPort::new(&transport);
    for invalid in ["", "../escape", "job?query", &"x".repeat(257)] {
        assert_eq!(
            port.get_job(invalid).expect_err("invalid id").code(),
            "LOCAL_RECOVERY_INPUT_INVALID"
        );
    }
    assert!(transport.calls.lock().expect("calls").is_empty());

    let cases = [
        RecoveryHttpResponse {
            status: 200,
            content_type: Some("text/plain".to_owned()),
            content_length: Some(2),
            body: b"{}".to_vec(),
        },
        RecoveryHttpResponse {
            status: 200,
            content_type: Some("application/json".to_owned()),
            content_length: Some(9),
            body: b"{}".to_vec(),
        },
        response(b"not-json".to_vec()),
        response(job_json(",\"unknown\":true")),
        RecoveryHttpResponse {
            status: 200,
            content_type: Some("application/json".to_owned()),
            content_length: Some(1_048_577),
            body: vec![b'x'; 1_048_577],
        },
    ];
    for untrusted in cases {
        let transport = FakeTransport::ready(vec![Ok(untrusted)]);
        let error = LocalRecoveryPort::new(&transport)
            .get_job(JOB_ID)
            .expect_err("untrusted response");
        assert_eq!(error.code(), "LOCAL_RECOVERY_RESPONSE_REJECTED");
    }
}

#[test]
fn safe_dto_debug_and_error_never_expose_native_context() {
    let transport = FakeTransport::ready(vec![Ok(response(job_json("")))]);
    let job: LocalRecoveryJob = LocalRecoveryPort::new(&transport)
        .get_job(JOB_ID)
        .expect("job");
    let serialized = serde_json::to_string(&job).expect("serialize");
    let debug = format!("{job:?}");
    for secret in [
        "127.0.0.1:48123",
        "root-secret",
        "Bearer",
        "quarantine/path",
    ] {
        assert!(!serialized.contains(secret));
        assert!(!debug.contains(secret));
    }
}

#[test]
fn canonical_failed_job_and_version_chain_are_accepted() {
    let body = custom_job_json(
        JOB_ID,
        1,
        "failed",
        "fixture-damaged-object",
        "2026-07-31T05:00:00Z",
        "null",
    );
    let transport = FakeTransport::ready(vec![Ok(response(body))]);
    let job = LocalRecoveryPort::new(&transport)
        .get_job(JOB_ID)
        .expect("canonical failed job");
    assert_eq!(job.state, "failed");
    assert_eq!(job.previous_version, None);
}

#[test]
fn python_fractional_utc_timestamp_is_accepted_and_noncanonical_forms_fail_closed() {
    let accepted = ["2026-08-10T13:15:17Z", "2026-08-10T13:15:17.123456Z"];
    for recorded_at in accepted {
        let transport = FakeTransport::ready(vec![Ok(response(custom_job_json(
            JOB_ID,
            1,
            "failed",
            "fixture-damaged-object",
            recorded_at,
            "null",
        )))]);
        LocalRecoveryPort::new(&transport)
            .get_job(JOB_ID)
            .expect("Python-compatible UTC timestamp");
    }

    for recorded_at in [
        "2026-08-10T13:15:17+00:00",
        "2026-02-30T13:15:17Z",
        "2026-08-10T25:15:17Z",
        "2026-08-10T13:15:17.123456Ztrailing",
        &format!("2026-08-10T13:15:17.{}Z", "1".repeat(80)),
    ] {
        let transport = FakeTransport::ready(vec![Ok(response(custom_job_json(
            JOB_ID,
            1,
            "failed",
            "fixture-damaged-object",
            recorded_at,
            "null",
        )))]);
        assert_eq!(
            LocalRecoveryPort::new(&transport)
                .get_job(JOB_ID)
                .expect_err("noncanonical timestamp")
                .code(),
            "LOCAL_RECOVERY_RESPONSE_REJECTED"
        );
    }
}

#[test]
fn python_fractional_timestamp_fixture_passes_full_manager_loopback_path() {
    let body = custom_job_json(
        JOB_ID,
        4,
        "repairable",
        "fixture-damaged-object",
        "2026-08-10T13:15:17.123456Z",
        "3",
    );
    let listener = TcpListener::bind("127.0.0.1:0").expect("listener");
    let port_number = listener.local_addr().expect("address").port();
    let server = thread::spawn(move || {
        let (mut stream, _) = listener.accept().expect("accept");
        let _ = read_request(&mut stream);
        stream
            .write_all(&http_response("200 OK", &body))
            .expect("Python fixture response");
    });
    let manager =
        LocalServiceManager::with_contract_recovery_endpoint(port_number, Duration::from_secs(1));
    let job = LocalRecoveryPort::new(&manager)
        .get_job(JOB_ID)
        .expect("full Python fractional timestamp path");
    assert_eq!(job.recorded_at, "2026-08-10T13:15:17.123456Z");
    server.join().expect("server");
}

#[test]
fn actual_runtime_context_is_rejected_from_every_raw_response_position() {
    let listener = TcpListener::bind("127.0.0.1:0").expect("listener");
    let port_number = listener.local_addr().expect("address").port();
    let (manager, canaries) = LocalServiceManager::with_contract_recovery_endpoint_and_canaries(
        port_number,
        Duration::from_secs(1),
    );
    let static_canaries = vec![
        port_number.to_string(),
        canaries.root_secret().to_owned(),
        canaries.storage_root().to_owned(),
        canaries.quarantine_path().to_owned(),
    ];
    let request_count = (static_canaries.len() + 1) * 3;
    let server = thread::spawn(move || {
        for index in 0..request_count {
            let (mut stream, _) = listener.accept().expect("accept");
            let request = read_request(&mut stream);
            let token = request
                .lines()
                .find_map(|line| line.strip_prefix("Authorization: Bearer "))
                .expect("actual request token");
            let canary = if index / 3 == static_canaries.len() {
                token
            } else {
                &static_canaries[index / 3]
            };
            let injected = match index % 3 {
                0 => format!("{canary}-suffix"),
                1 => format!("prefix-{canary}"),
                _ => format!("prefix-{canary}-suffix"),
            };
            let body = job_json("");
            let mut response = format!(
                "HTTP/1.1 200 OK\r\nContent-Type: application/json\r\nX-Untrusted: {injected}\r\nContent-Length: {}\r\nConnection: close\r\n\r\n",
                body.len()
            )
            .into_bytes();
            response.extend_from_slice(&body);
            stream.write_all(&response).expect("injected response");
        }
    });
    let recovery = LocalRecoveryPort::new(&manager);
    let results: Vec<_> = (0..request_count)
        .map(|_| recovery.get_job(JOB_ID))
        .collect();
    server.join().expect("server");
    assert!(results.iter().all(|result| {
        result
            .as_ref()
            .is_err_and(|error| error.code() == "LOCAL_RECOVERY_RESPONSE_REJECTED")
    }));
}

#[test]
fn decoded_json_runtime_context_variants_are_rejected() {
    fn unicode_escape_ascii(value: &str) -> String {
        value.bytes().map(|byte| format!("\\u{byte:04x}")).collect()
    }

    let listener = TcpListener::bind("127.0.0.1:0").expect("listener");
    let port_number = listener.local_addr().expect("address").port();
    let (manager, canaries) = LocalServiceManager::with_contract_recovery_endpoint_and_canaries(
        port_number,
        Duration::from_secs(1),
    );
    let injected_targets = vec![
        format!("fixture-{}", unicode_escape_ascii(canaries.root_secret())),
        format!("fixture-{}", canaries.root_secret().to_ascii_uppercase()),
        format!("fixture-{}", unicode_escape_ascii(&port_number.to_string())),
    ];
    let request_count = injected_targets.len();
    let server = thread::spawn(move || {
        for target_id in injected_targets {
            let (mut stream, _) = listener.accept().expect("accept");
            let _ = read_request(&mut stream);
            let body = custom_job_json(
                JOB_ID,
                4,
                "repairable",
                &target_id,
                "2026-08-10T13:15:17.123456Z",
                "3",
            );
            stream
                .write_all(&http_response("200 OK", &body))
                .expect("encoded context response");
        }
    });
    let recovery = LocalRecoveryPort::new(&manager);
    let results: Vec<_> = (0..request_count)
        .map(|_| recovery.get_job(JOB_ID))
        .collect();
    server.join().expect("server");
    assert!(results.iter().all(|result| {
        result
            .as_ref()
            .is_err_and(|error| error.code() == "LOCAL_RECOVERY_RESPONSE_REJECTED")
    }));
}

#[test]
fn job_id_requires_exact_python_prefix_and_24_lower_hex_suffix() {
    for invalid in [
        "fixture-recovery-0123456789abcdef0123456",
        "fixture-recovery-0123456789abcdef012345678",
        "fixture-recovery-0123456789ABCDEF01234567",
        "fixture-recovery-0123456789abcdef01234567suffix",
    ] {
        let transport = FakeTransport::ready(vec![Ok(response(custom_job_json(
            invalid,
            1,
            "failed",
            "fixture-damaged-object",
            "2026-08-10T13:15:17.123456Z",
            "null",
        )))]);
        assert_eq!(
            LocalRecoveryPort::new(&transport)
                .get_job(JOB_ID)
                .expect_err("noncanonical job id")
                .code(),
            "LOCAL_RECOVERY_RESPONSE_REJECTED"
        );
    }
}

#[test]
fn malformed_timestamp_version_chain_and_native_canaries_are_rejected() {
    let credentials = AppCredentials::generate().expect("dynamic credentials");
    let bootstrap: serde_json::Value =
        serde_json::from_str(&credentials.bootstrap_json()).expect("bootstrap");
    let listener = TcpListener::bind("127.0.0.1:0").expect("dynamic port");
    let dynamic_port = listener.local_addr().expect("dynamic address").port();
    drop(listener);
    let dynamic_token = credentials
        .issue_request_token("recovery.read", "recovery.job.read", 2_000_000_000)
        .expect("dynamic token");
    let storage_root = bootstrap["storage_root"].as_str().expect("storage root");
    let dynamic_canaries = vec![
        dynamic_port.to_string(),
        format!("127.0.0.1:{dynamic_port}"),
        bootstrap["root_secret"]
            .as_str()
            .expect("root secret")
            .to_owned(),
        dynamic_token,
        storage_root.to_owned(),
        format!("{storage_root}\\quarantine\\fixture-damaged-object"),
    ];
    let mut bodies = vec![
        custom_job_json(
            JOB_ID,
            4,
            "repairable",
            "fixture-ok",
            "not-a-timestamp",
            "3",
        ),
        custom_job_json(
            JOB_ID,
            1,
            "repairable",
            "fixture-ok",
            "2026-07-31T05:00:00Z",
            "0",
        ),
        custom_job_json(
            JOB_ID,
            4,
            "repairable",
            "fixture-ok",
            "2026-07-31T05:00:00Z",
            "2",
        ),
    ];
    for canary in dynamic_canaries {
        for field in ["job_id", "target_id", "state", "recorded_at", "integrity"] {
            let mut value = serde_json::json!({
                "data": {
                    "job_id": JOB_ID,
                    "version": 1,
                    "state": "failed",
                    "target_id": "fixture-ok",
                    "journal_present": true,
                    "recorded_at": "2026-07-31T05:00:00Z",
                    "previous_version": null,
                    "integrity": "pending"
                }
            });
            value["data"][field] = serde_json::Value::String(canary.clone());
            bodies.push(serde_json::to_vec(&value).expect("canary response"));
        }
    }
    for body in bodies {
        let transport = FakeTransport::ready(vec![Ok(response(body))]);
        assert_eq!(
            LocalRecoveryPort::new(&transport)
                .get_job(JOB_ID)
                .expect_err("must reject")
                .code(),
            "LOCAL_RECOVERY_RESPONSE_REJECTED"
        );
    }
}

#[test]
fn parser_rejection_is_non_retryable_and_safe_error_has_opaque_trace() {
    let transport = FakeTransport::ready(vec![Err("LOCAL_RECOVERY_RESPONSE_REJECTED")]);
    let error = LocalRecoveryPort::new(&transport)
        .get_job(JOB_ID)
        .expect_err("parser rejection");
    let value = serde_json::to_value(&error).expect("safe error json");
    assert_eq!(value["code"], "LOCAL_RECOVERY_RESPONSE_REJECTED");
    assert_eq!(value["retryable"], false);
    let trace = value["trace_id"].as_str().expect("trace id");
    assert_eq!(trace.len(), 32);
    assert!(
        trace
            .bytes()
            .all(|byte| byte.is_ascii_hexdigit() && !byte.is_ascii_uppercase())
    );
    let debug = format!("{error:?}");
    for secret in [
        "48123",
        "127.0.0.1",
        "root-secret",
        "storage-root",
        "quarantine",
    ] {
        assert!(!debug.contains(secret));
    }
}

#[test]
fn connectivity_failure_remains_retryable_and_distinct_from_parser_rejection() {
    let transport = FakeTransport::ready(vec![Err("LOCAL_RECOVERY_CONNECT_FAILED")]);
    let error = LocalRecoveryPort::new(&transport)
        .get_job(JOB_ID)
        .expect_err("connect failure");
    let value = serde_json::to_value(error).expect("safe error json");
    assert_eq!(value["code"], "LOCAL_RECOVERY_REQUEST_FAILED");
    assert_eq!(value["retryable"], true);
}

fn read_request(stream: &mut std::net::TcpStream) -> String {
    stream
        .set_read_timeout(Some(Duration::from_secs(1)))
        .expect("read timeout");
    let mut raw = Vec::new();
    let mut chunk = [0_u8; 4096];
    loop {
        let count = stream.read(&mut chunk).expect("request read");
        raw.extend_from_slice(&chunk[..count]);
        let Some(separator) = raw.windows(4).position(|window| window == b"\r\n\r\n") else {
            continue;
        };
        let headers = String::from_utf8_lossy(&raw[..separator]);
        let content_length = headers
            .lines()
            .find_map(|line| {
                line.to_ascii_lowercase()
                    .strip_prefix("content-length:")
                    .map(str::trim)
                    .and_then(|value| value.parse::<usize>().ok())
            })
            .unwrap_or(0);
        if raw.len() >= separator + 4 + content_length {
            return String::from_utf8(raw).expect("utf8 request");
        }
    }
}

fn http_response(status: &str, body: &[u8]) -> Vec<u8> {
    let mut response = format!(
        "HTTP/1.1 {status}\r\nContent-Type: application/json\r\nContent-Length: {}\r\nConnection: close\r\n\r\n",
        body.len()
    ).into_bytes();
    response.extend_from_slice(body);
    response
}

#[test]
fn manager_real_loopback_path_binds_method_path_command_and_unique_tokens() {
    let listener = TcpListener::bind("127.0.0.1:0").expect("listener");
    let port_number = listener.local_addr().expect("address").port();
    let (sender, receiver) = mpsc::channel();
    let server = thread::spawn(move || {
        for _ in 0..2 {
            let (mut stream, _) = listener.accept().expect("accept");
            let request = read_request(&mut stream);
            sender.send(request).expect("capture");
            stream
                .write_all(&http_response("200 OK", &job_json("")))
                .expect("response");
        }
    });
    let manager =
        LocalServiceManager::with_contract_recovery_endpoint(port_number, Duration::from_secs(1));
    let recovery = LocalRecoveryPort::new(&manager);
    recovery
        .scan(scan_request())
        .expect("scan through manager TCP parser");
    recovery
        .get_job(JOB_ID)
        .expect("get through manager TCP parser");
    let requests = [
        receiver.recv().expect("scan request"),
        receiver.recv().expect("get request"),
    ];
    assert!(requests[0].starts_with("POST /local/v1/recovery/scans HTTP/1.1\r\n"));
    assert!(requests[1].starts_with(&format!(
        "GET /local/v1/recovery/jobs/{JOB_ID} HTTP/1.1\r\n"
    )));
    let tokens: Vec<_> = requests
        .iter()
        .map(|request| {
            request
                .lines()
                .find_map(|line| line.strip_prefix("Authorization: Bearer "))
                .expect("authorization")
        })
        .collect();
    assert_ne!(tokens[0], tokens[1], "request token replay must not occur");
    let scan_fields: Vec<_> = tokens[0].split('|').collect();
    let get_fields: Vec<_> = tokens[1].split('|').collect();
    assert_eq!(
        (&scan_fields[4], &scan_fields[5]),
        (&"recovery.write", &"recovery.scan")
    );
    assert_eq!(
        (&get_fields[4], &get_fields[5]),
        (&"recovery.read", &"recovery.job.read")
    );
    server.join().expect("server");
}

#[test]
fn actual_tcp_parser_preserves_forged_truncated_and_oversize_rejections() {
    let valid_body = job_json("");
    let cases = [
        b"GARBAGE 200 OK\r\nContent-Length: 0\r\n\r\n".to_vec(),
        b"HTTP/1.1 200 OK\r\nContent-Type: application/json\r\nContent-Length: 9\r\n\r\n{}"
            .to_vec(),
        {
            let mut oversized_header = b"HTTP/1.1 200 OK\r\nX-Oversized: ".to_vec();
            oversized_header.extend(vec![b'x'; 8192]);
            oversized_header.extend_from_slice(b"\r\nContent-Length: 0\r\n\r\n");
            oversized_header
        },
        http_response("200 OK", &vec![b'x'; 1_048_577]),
        http_response("500 Forged", &valid_body),
    ];
    for raw_response in cases {
        let listener = TcpListener::bind("127.0.0.1:0").expect("listener");
        let port_number = listener.local_addr().expect("address").port();
        let server = thread::spawn(move || {
            let (mut stream, _) = listener.accept().expect("accept");
            let _ = read_request(&mut stream);
            stream.write_all(&raw_response).expect("response");
        });
        let manager = LocalServiceManager::with_contract_recovery_endpoint(
            port_number,
            Duration::from_secs(1),
        );
        let error = LocalRecoveryPort::new(&manager)
            .get_job(JOB_ID)
            .expect_err("reject parser input");
        let safe = serde_json::to_value(error).expect("safe error");
        assert_eq!(safe["code"], "LOCAL_RECOVERY_RESPONSE_REJECTED");
        assert_eq!(safe["retryable"], false);
        server.join().expect("server");
    }
}

#[test]
fn slow_drip_obeys_overall_deadline_without_blocking_status_or_shutdown() {
    let listener = TcpListener::bind("127.0.0.1:0").expect("listener");
    let port_number = listener.local_addr().expect("address").port();
    let request_timeout = Duration::from_millis(300);
    let (entered_sender, entered_receiver) = mpsc::sync_channel(1);
    let server = thread::spawn(move || {
        let (mut stream, _) = listener.accept().expect("accept");
        let _ = read_request(&mut stream);
        entered_sender.send(()).expect("TCP I/O barrier");
        for byte in http_response("200 OK", &job_json("")).into_iter().take(32) {
            if stream.write_all(&[byte]).is_err() {
                break;
            }
            thread::sleep(Duration::from_millis(20));
        }
    });
    let manager =
        LocalServiceManager::with_contract_recovery_endpoint(port_number, request_timeout);
    let request_manager = manager.clone();
    let started = Instant::now();
    let request = thread::spawn(move || LocalRecoveryPort::new(&request_manager).get_job(JOB_ID));
    entered_receiver
        .recv_timeout(request_timeout)
        .expect("request entered actual TCP I/O");
    let status_started = Instant::now();
    assert_eq!(manager.status().state(), "ready");
    assert!(status_started.elapsed() < request_timeout);
    let shutdown_started = Instant::now();
    manager.shutdown();
    assert!(shutdown_started.elapsed() < request_timeout);
    let error = request
        .join()
        .expect("request thread")
        .expect_err("deadline");
    assert_eq!(
        serde_json::to_value(error).expect("safe error")["code"],
        "LOCAL_RECOVERY_REQUEST_FAILED"
    );
    assert!(started.elapsed() < request_timeout * 3);
    server.join().expect("server");
}

#[test]
fn cloud_port_allows_only_seven_contracts_and_keeps_vault_access_internal() {
    tauri::async_runtime::block_on(async {
        let runtime =
            NativeSessionRuntime::for_contract_test(Arc::new(CloudVault), Arc::new(CloudIdentity));
        let transport = FakeCloudTransport {
            calls: Mutex::new(Vec::new()),
            failures: Mutex::new(Vec::new()),
        };
        let port = CloudRecoveryPort::new(&runtime, &transport);
        let requests = vec![
            cloud_request(
                "POST",
                "/api/v1/backups",
                backup_create_body(),
                Some("idempotency-create"),
                None,
                None,
            ),
            cloud_request(
                "GET",
                "/api/v1/backups",
                serde_json::Value::Null,
                None,
                None,
                Some("workspace_id=workspace-cloud"),
            ),
            cloud_request(
                "GET",
                "/api/v1/backups/backup-0123456789abcdef01234567",
                serde_json::Value::Null,
                None,
                None,
                None,
            ),
            cloud_request(
                "POST",
                "/api/v1/backups/backup-0123456789abcdef01234567/restore-previews",
                preview_body("step-preview"),
                Some("idempotency-preview"),
                None,
                None,
            ),
            cloud_request(
                "GET",
                "/api/v1/restore-requests/restore-cloud",
                serde_json::Value::Null,
                None,
                None,
                None,
            ),
            cloud_request(
                "POST",
                "/api/v1/restore-requests/restore-cloud/execute",
                execute_body("step-execute"),
                Some("idempotency-execute"),
                Some("\"restore:restore-cloud:1\""),
                None,
            ),
            cloud_request(
                "POST",
                "/api/v1/restore-requests/restore-cloud/cancel",
                serde_json::Value::Null,
                Some("idempotency-cancel"),
                Some("\"restore:restore-cloud:2\""),
                None,
            ),
        ];
        for request in requests {
            let projection = port
                .execute(request)
                .await
                .expect("approved cloud contract");
            let serialized = serde_json::to_string(&projection).expect("projection");
            assert!(!serialized.contains(CLOUD_ACCESS));
            assert!(!serialized.contains("daon-user.sinsan.kr"));
        }
        let calls = transport.calls.lock().expect("calls");
        assert_eq!(calls.len(), 7);
        assert!(calls.iter().all(|(access, _, _)| access == CLOUD_ACCESS));
    });
}

#[test]
fn cloud_port_rejects_invalid_headers_reuse_and_never_retries_writes() {
    tauri::async_runtime::block_on(async {
        let runtime =
            NativeSessionRuntime::for_contract_test(Arc::new(CloudVault), Arc::new(CloudIdentity));
        let transport = FakeCloudTransport {
            calls: Mutex::new(Vec::new()),
            failures: Mutex::new(vec!["AUTHENTICATION_REQUIRED"]),
        };
        let port = CloudRecoveryPort::new(&runtime, &transport);
        let write = cloud_request(
            "POST",
            "/api/v1/backups",
            backup_create_body(),
            Some("idempotency-no-retry"),
            None,
            None,
        );
        assert_eq!(
            port.execute(write)
                .await
                .expect_err("write auth failure")
                .code(),
            "AUTHENTICATION_REQUIRED"
        );
        assert_eq!(transport.calls.lock().expect("calls").len(), 1);

        for invalid in [
            cloud_request(
                "DELETE",
                "/api/v1/backups/backup-cloud",
                serde_json::Value::Null,
                None,
                None,
                None,
            ),
            cloud_request(
                "POST",
                "/api/v1/restore-requests/restore-cloud/execute",
                execute_body("step"),
                Some("idem"),
                None,
                None,
            ),
            cloud_request(
                "GET",
                "http://127.0.0.1:8000/api/v1/backups",
                serde_json::Value::Null,
                None,
                None,
                None,
            ),
        ] {
            assert_eq!(
                port.execute(invalid)
                    .await
                    .expect_err("invalid cloud request")
                    .code(),
                "CLOUD_RECOVERY_INPUT_INVALID"
            );
        }
    });
}

#[test]
fn cloud_port_rejects_unapproved_list_query_fields() {
    tauri::async_runtime::block_on(async {
        let runtime =
            NativeSessionRuntime::for_contract_test(Arc::new(CloudVault), Arc::new(CloudIdentity));
        let transport = FakeCloudTransport {
            calls: Mutex::new(Vec::new()),
            failures: Mutex::new(Vec::new()),
        };
        let port = CloudRecoveryPort::new(&runtime, &transport);
        let invalid = cloud_request(
            "GET",
            "/api/v1/backups",
            serde_json::Value::Null,
            None,
            None,
            Some("workspace_id=workspace-cloud&redirect=http://127.0.0.1"),
        );

        assert_eq!(
            port.execute(invalid)
                .await
                .expect_err("unapproved query field must be rejected")
                .code(),
            "CLOUD_RECOVERY_INPUT_INVALID"
        );
        assert!(transport.calls.lock().expect("calls").is_empty());
    });
}

#[test]
fn cloud_port_rejects_unknown_or_incomplete_write_bodies_before_transport() {
    tauri::async_runtime::block_on(async {
        let runtime =
            NativeSessionRuntime::for_contract_test(Arc::new(CloudVault), Arc::new(CloudIdentity));
        let transport = FakeCloudTransport {
            calls: Mutex::new(Vec::new()),
            failures: Mutex::new(Vec::new()),
        };
        let port = CloudRecoveryPort::new(&runtime, &transport);
        let invalid = cloud_request(
            "POST",
            "/api/v1/backups",
            serde_json::json!({"workspace_id":"workspace-cloud","gateway_url":"http://127.0.0.1"}),
            Some("idem-invalid-body"),
            None,
            None,
        );

        assert_eq!(
            port.execute(invalid)
                .await
                .expect_err("unknown and incomplete body rejected")
                .code(),
            "CLOUD_RECOVERY_INPUT_INVALID"
        );
        assert!(transport.calls.lock().expect("calls").is_empty());
    });
}

#[test]
fn cloud_port_refreshes_and_retries_a_get_exactly_once() {
    tauri::async_runtime::block_on(async {
        let refreshes = Arc::new(AtomicUsize::new(0));
        let runtime = NativeSessionRuntime::for_contract_test(
            Arc::new(CloudVault),
            Arc::new(RefreshCountingIdentity(Arc::clone(&refreshes))),
        );
        let transport = FakeCloudTransport {
            calls: Mutex::new(Vec::new()),
            failures: Mutex::new(vec!["AUTHENTICATION_REQUIRED"]),
        };
        let port = CloudRecoveryPort::new(&runtime, &transport);

        port.execute(cloud_request(
            "GET",
            "/api/v1/backups/backup-0123456789abcdef01234567",
            serde_json::Value::Null,
            None,
            None,
            None,
        ))
        .await
        .expect("GET succeeds after one credential rotation");

        assert_eq!(refreshes.load(Ordering::SeqCst), 1);
        assert_eq!(transport.calls.lock().expect("calls").len(), 2);
    });
}

#[test]
fn cloud_port_rejects_unknown_envelope_and_sensitive_projection_fields() {
    tauri::async_runtime::block_on(async {
        for body in [
            br#"{"data":{"id":"backup-cloud"},"meta":{"trace_id":"trace-cloud"},"unknown":true}"#
                .to_vec(),
            br#"{"data":{"access_token":"must-not-cross"},"meta":{"trace_id":"trace-cloud"}}"#
                .to_vec(),
            br#"{"data":{"id":"backup-cloud"}}"#.to_vec(),
        ] {
            let runtime = NativeSessionRuntime::for_contract_test(
                Arc::new(CloudVault),
                Arc::new(CloudIdentity),
            );
            let transport = FixedCloudTransport(body);
            let port = CloudRecoveryPort::new(&runtime, &transport);
            assert_eq!(
                port.execute(cloud_request(
                    "GET",
                    "/api/v1/backups/backup-cloud",
                    serde_json::Value::Null,
                    None,
                    None,
                    None,
                ))
                .await
                .expect_err("unsafe response rejected")
                .code(),
                "CLOUD_RECOVERY_RESPONSE_REJECTED"
            );
        }
    });
}

#[test]
fn cloud_port_accepts_runtime_list_etag_and_rejects_nested_unknown_before_projection() {
    tauri::async_runtime::block_on(async {
        let runtime =
            NativeSessionRuntime::for_contract_test(Arc::new(CloudVault), Arc::new(CloudIdentity));
        let list_transport = ResponseCloudTransport {
            status: 200,
            body: cloud_envelope(serde_json::json!([backup_view()])),
            etag: Some("\"projection-0123456789abcdef01234567\"".to_owned()),
            calls: Mutex::new(0),
        };
        CloudRecoveryPort::new(&runtime, &list_transport)
            .execute(cloud_request(
                "GET",
                "/api/v1/backups",
                serde_json::Value::Null,
                None,
                None,
                Some("workspace_id=workspace-cloud"),
            ))
            .await
            .expect("runtime list projection and ETag");

        let mut unsafe_backup = backup_view();
        unsafe_backup.as_object_mut().expect("backup").insert(
            "nested".to_owned(),
            serde_json::json!({"Password":"must-not-project"}),
        );
        let unsafe_transport = ResponseCloudTransport {
            status: 200,
            body: cloud_envelope(unsafe_backup),
            etag: Some("\"backup:backup-0123456789abcdef01234567:2\"".to_owned()),
            calls: Mutex::new(0),
        };
        assert_eq!(
            CloudRecoveryPort::new(&runtime, &unsafe_transport)
                .execute(cloud_request(
                    "GET",
                    "/api/v1/backups/backup-0123456789abcdef01234567",
                    serde_json::Value::Null,
                    None,
                    None,
                    None,
                ))
                .await
                .expect_err("nested unknown rejected before projection")
                .code(),
            "CLOUD_RECOVERY_RESPONSE_REJECTED"
        );
    });
}

#[test]
fn cloud_port_requires_restore_if_match_for_execute() {
    tauri::async_runtime::block_on(async {
        let runtime =
            NativeSessionRuntime::for_contract_test(Arc::new(CloudVault), Arc::new(CloudIdentity));
        let transport = FakeCloudTransport {
            calls: Mutex::new(Vec::new()),
            failures: Mutex::new(Vec::new()),
        };
        let port = CloudRecoveryPort::new(&runtime, &transport);
        assert_eq!(
            port.execute(cloud_request(
                "POST",
                "/api/v1/restore-requests/restore-cloud/execute",
                execute_body("step-if-match"),
                Some("idem-if-match"),
                Some("\"backup:backup-cloud:2\""),
                None,
            ))
            .await
            .expect_err("backup ETag cannot authorize restore execute")
            .code(),
            "CLOUD_RECOVERY_INPUT_INVALID"
        );
        assert!(transport.calls.lock().expect("calls").is_empty());
    });
}

#[test]
fn cloud_port_treats_403_as_safe_denial_without_refresh() {
    tauri::async_runtime::block_on(async {
        let refreshes = Arc::new(AtomicUsize::new(0));
        let runtime = NativeSessionRuntime::for_contract_test(
            Arc::new(CloudVault),
            Arc::new(RefreshCountingIdentity(Arc::clone(&refreshes))),
        );
        let transport = ResponseCloudTransport {
            status: 403,
            body: safe_error_envelope("FORBIDDEN", false),
            etag: None,
            calls: Mutex::new(0),
        };
        let error = CloudRecoveryPort::new(&runtime, &transport)
            .execute(cloud_request(
                "GET",
                "/api/v1/backups/backup-cloud",
                serde_json::Value::Null,
                None,
                None,
                None,
            ))
            .await
            .expect_err("403 safe denial");
        assert_eq!(error.code(), "FORBIDDEN");
        assert_eq!(refreshes.load(Ordering::SeqCst), 0);
        assert_eq!(*transport.calls.lock().expect("calls"), 1);
    });
}

#[test]
fn cloud_port_refreshes_write_401_once_without_replaying_write() {
    tauri::async_runtime::block_on(async {
        let refreshes = Arc::new(AtomicUsize::new(0));
        let runtime = NativeSessionRuntime::for_contract_test(
            Arc::new(CloudVault),
            Arc::new(RefreshCountingIdentity(Arc::clone(&refreshes))),
        );
        let transport = FakeCloudTransport {
            calls: Mutex::new(Vec::new()),
            failures: Mutex::new(vec!["AUTHENTICATION_REQUIRED"]),
        };
        let error = CloudRecoveryPort::new(&runtime, &transport)
            .execute(cloud_request(
                "POST",
                "/api/v1/backups",
                backup_create_body(),
                Some("idempotency-write-401"),
                None,
                None,
            ))
            .await
            .expect_err("write remains uncommitted after 401");
        assert_eq!(error.code(), "AUTHENTICATION_REQUIRED");
        assert_eq!(refreshes.load(Ordering::SeqCst), 1);
        assert_eq!(transport.calls.lock().expect("calls").len(), 1);
    });
}

#[test]
fn cloud_port_write_transport_failure_is_non_retryable_and_consumed_secrets_are_not_raw() {
    tauri::async_runtime::block_on(async {
        let runtime =
            NativeSessionRuntime::for_contract_test(Arc::new(CloudVault), Arc::new(CloudIdentity));
        let failing = FakeCloudTransport {
            calls: Mutex::new(Vec::new()),
            failures: Mutex::new(vec!["CLOUD_RECOVERY_CONNECT_FAILED"]),
        };
        let port = CloudRecoveryPort::new(&runtime, &failing);
        let error = port
            .execute(cloud_request(
                "POST",
                "/api/v1/backups",
                backup_create_body(),
                Some("idempotency-sensitive-connect"),
                None,
                None,
            ))
            .await
            .expect_err("write transport failure");
        let error_json = serde_json::to_value(error).expect("safe error");
        assert_eq!(error_json["retryable"], false);

        let successful = FakeCloudTransport {
            calls: Mutex::new(Vec::new()),
            failures: Mutex::new(Vec::new()),
        };
        let port = CloudRecoveryPort::new(&runtime, &successful);
        port.execute(cloud_request(
            "POST",
            "/api/v1/backups/backup-0123456789abcdef01234567/restore-previews",
            preview_body("step-sensitive-cache"),
            Some("idempotency-sensitive-cache"),
            None,
            None,
        ))
        .await
        .expect("preview accepted");
        let (idempotency_count, step_up_count, raw_present) = port
            .consumption_cache_state_for_contract(
                "idempotency-sensitive-cache",
                "step-sensitive-cache",
            );
        assert_eq!((idempotency_count, step_up_count), (1, 1));
        assert!(!raw_present, "consumption cache retained a raw secret");
    });
}

#[test]
fn cloud_consumption_cache_is_bounded_and_secrets_drop_on_success_failure_and_cancel() {
    tauri::async_runtime::block_on(async {
        CloudSecret::reset_drop_audit_for_contract();
        let runtime =
            NativeSessionRuntime::for_contract_test(Arc::new(CloudVault), Arc::new(CloudIdentity));
        let transport = FakeCloudTransport {
            calls: Mutex::new(Vec::new()),
            failures: Mutex::new(Vec::new()),
        };
        let port = CloudRecoveryPort::new(&runtime, &transport);
        for index in 0..128 {
            port.execute(cloud_request(
                "POST",
                "/api/v1/backups",
                backup_create_body(),
                Some(&format!("bounded-key-{index:04}")),
                None,
                None,
            ))
            .await
            .expect("within bounded cache");
        }
        port.execute(cloud_request(
            "POST",
            "/api/v1/backups",
            backup_create_body(),
            Some("bounded-key-overflow"),
            None,
            None,
        ))
        .await
        .expect("bounded cache recycles oldest entry");
        let invalid = cloud_request(
            "POST",
            "/api/v1/backups",
            serde_json::json!({"unknown":"secret"}),
            Some("invalid-secret"),
            None,
            None,
        );
        assert!(port.execute(invalid).await.is_err());
        assert_eq!(transport.calls.lock().expect("calls").len(), 129);

        let transport_failure = FakeCloudTransport {
            calls: Mutex::new(Vec::new()),
            failures: Mutex::new(vec!["CLOUD_RECOVERY_CONNECT_FAILED"]),
        };
        assert!(
            CloudRecoveryPort::new(&runtime, &transport_failure)
                .execute(cloud_request(
                    "POST",
                    "/api/v1/backups",
                    backup_create_body(),
                    Some("transport-secret"),
                    None,
                    None,
                ))
                .await
                .is_err()
        );
        let cancel = FakeCloudTransport {
            calls: Mutex::new(Vec::new()),
            failures: Mutex::new(Vec::new()),
        };
        CloudRecoveryPort::new(&runtime, &cancel)
            .execute(cloud_request(
                "POST",
                "/api/v1/restore-requests/restore-cloud/cancel",
                serde_json::Value::Null,
                Some("idempotency-cancel-secret"),
                Some("\"restore:restore-cloud:2\""),
                None,
            ))
            .await
            .expect("cancel response");
        assert!(CloudSecret::drop_audit_for_contract() >= 133);
    });
}

#[test]
fn actual_cloud_transport_sends_only_approved_headers_and_fixed_path() {
    let listener = TcpListener::bind("127.0.0.1:0").expect("listener");
    let gateway = format!("http://{}", listener.local_addr().expect("address"));
    let server = thread::spawn(move || {
        let (mut stream, _) = listener.accept().expect("accept");
        let request = read_request(&mut stream);
        stream
            .write_all(&http_response(
                "200 OK",
                br#"{"data":[{"id":"backup-cloud","state":"ready"}],"meta":{"trace_id":"trace-cloud"}}"#,
            ))
            .expect("response");
        request
    });
    let client = NativeCloudRecoveryClient::for_contract_test(&gateway, Duration::from_secs(2))
        .expect("contract client");
    let response = tauri::async_runtime::block_on(client.execute(
        CLOUD_ACCESS.as_bytes(),
        cloud_request(
            "GET",
            "/api/v1/backups",
            serde_json::Value::Null,
            None,
            None,
            Some("workspace_id=workspace-cloud"),
        ),
    ))
    .expect("cloud response");
    assert_eq!(response.status, 200);

    let request = server.join().expect("server");
    let lower = request.to_ascii_lowercase();
    assert!(request.starts_with("GET /api/v1/backups?workspace_id=workspace-cloud HTTP/1.1\r\n"));
    assert!(lower.contains(&format!("authorization: bearer {}", CLOUD_ACCESS)));
    assert!(lower.contains("accept: application/json"));
    assert!(!lower.contains("cookie:"));
    assert!(!lower.contains("localhost"));
}

#[test]
fn actual_cloud_transport_rejects_redirect_cookie_and_chunked_responses() {
    for response in [
        b"HTTP/1.1 307 Temporary Redirect\r\nLocation: http://127.0.0.1:9/stolen\r\nContent-Length: 0\r\nConnection: close\r\n\r\n".to_vec(),
        b"HTTP/1.1 200 OK\r\nContent-Type: application/json\r\nSet-Cookie: secret=value\r\nContent-Length: 11\r\nConnection: close\r\n\r\n{\"data\":{}}".to_vec(),
        b"HTTP/1.1 200 OK\r\nContent-Type: application/json\r\nTransfer-Encoding: chunked\r\nConnection: close\r\n\r\nb\r\n{\"data\":{}}\r\n0\r\n\r\n".to_vec(),
    ] {
        let listener = TcpListener::bind("127.0.0.1:0").expect("listener");
        let gateway = format!("http://{}", listener.local_addr().expect("address"));
        let server = thread::spawn(move || {
            let (mut stream, _) = listener.accept().expect("accept");
            let _ = read_request(&mut stream);
            stream.write_all(&response).expect("response");
        });
        let client =
            NativeCloudRecoveryClient::for_contract_test(&gateway, Duration::from_secs(2))
                .expect("contract client");
        let result = tauri::async_runtime::block_on(client.execute(
            CLOUD_ACCESS.as_bytes(),
            cloud_request(
                "GET",
                "/api/v1/backups/backup-cloud",
                serde_json::Value::Null,
                None,
                None,
                None,
            ),
        ));
        let error = match result {
            Err(error) => error,
            Ok(_) => panic!("unsafe response accepted"),
        };
        assert_eq!(error, "CLOUD_RECOVERY_RESPONSE_REJECTED");
        server.join().expect("server");
    }
}

#[test]
fn actual_cloud_transport_rejects_length_framing_and_deadline_failures() {
    for response in [
        b"HTTP/1.1 200 OK\r\nContent-Type: application/json\r\nConnection: close\r\n\r\n{}".to_vec(),
        b"HTTP/1.1 200 OK\r\nContent-Type: application/json\r\nContent-Length: nope\r\nConnection: close\r\n\r\n{}".to_vec(),
        b"HTTP/1.1 200 OK\r\nContent-Type: application/json\r\nContent-Length: 1048577\r\nConnection: close\r\n\r\n".to_vec(),
        b"HTTP/1.1 200 OK\r\nContent-Type: application/json\r\nContent-Length: 100\r\nConnection: close\r\n\r\n{}".to_vec(),
    ] {
        let listener = TcpListener::bind("127.0.0.1:0").expect("listener");
        let gateway = format!("http://{}", listener.local_addr().expect("address"));
        let server = thread::spawn(move || {
            let (mut stream, _) = listener.accept().expect("accept");
            let _ = read_request(&mut stream);
            stream.write_all(&response).expect("response");
        });
        let client = NativeCloudRecoveryClient::for_contract_test(&gateway, Duration::from_secs(2))
            .expect("client");
        let result = tauri::async_runtime::block_on(client.execute(
            CLOUD_ACCESS.as_bytes(),
            cloud_request("GET", "/api/v1/backups/backup-cloud", serde_json::Value::Null, None, None, None),
        ));
        assert!(result.is_err());
        server.join().expect("server");
    }

    let listener = TcpListener::bind("127.0.0.1:0").expect("listener");
    let gateway = format!("http://{}", listener.local_addr().expect("address"));
    let server = thread::spawn(move || {
        let (mut stream, _) = listener.accept().expect("accept");
        let _ = read_request(&mut stream);
        for byte in
            b"HTTP/1.1 200 OK\r\nContent-Type: application/json\r\nContent-Length: 2\r\n\r\n{}"
        {
            if stream.write_all(&[*byte]).is_err() {
                break;
            }
            thread::sleep(Duration::from_millis(20));
        }
    });
    let client = NativeCloudRecoveryClient::for_contract_test(&gateway, Duration::from_millis(50))
        .expect("client");
    let result = tauri::async_runtime::block_on(client.execute(
        CLOUD_ACCESS.as_bytes(),
        cloud_request(
            "GET",
            "/api/v1/backups/backup-cloud",
            serde_json::Value::Null,
            None,
            None,
            None,
        ),
    ));
    assert_eq!(
        match result {
            Err(error) => error,
            Ok(_) => panic!("deadline response accepted"),
        },
        "CLOUD_RECOVERY_REQUEST_FAILED"
    );
    server.join().expect("server");
}

#[test]
fn actual_cloud_transport_never_follows_307_or_308() {
    for status in ["307 Temporary Redirect", "308 Permanent Redirect"] {
        let destination = TcpListener::bind("127.0.0.1:0").expect("destination");
        destination.set_nonblocking(true).expect("nonblocking");
        let destination_url = format!(
            "http://{}/stolen",
            destination.local_addr().expect("address")
        );
        let source = TcpListener::bind("127.0.0.1:0").expect("source");
        let gateway = format!("http://{}", source.local_addr().expect("address"));
        let response = format!(
            "HTTP/1.1 {status}\r\nLocation: {destination_url}\r\nContent-Length: 0\r\nConnection: close\r\n\r\n"
        );
        let server = thread::spawn(move || {
            let (mut stream, _) = source.accept().expect("accept");
            let _ = read_request(&mut stream);
            stream.write_all(response.as_bytes()).expect("response");
        });
        let client = NativeCloudRecoveryClient::for_contract_test(&gateway, Duration::from_secs(2))
            .expect("client");
        let result = tauri::async_runtime::block_on(client.execute(
            CLOUD_ACCESS.as_bytes(),
            cloud_request(
                "GET",
                "/api/v1/backups/backup-cloud",
                serde_json::Value::Null,
                None,
                None,
                None,
            ),
        ));
        assert_eq!(
            match result {
                Err(error) => error,
                Ok(_) => panic!("redirect accepted"),
            },
            "CLOUD_RECOVERY_RESPONSE_REJECTED"
        );
        server.join().expect("server");
        assert!(
            destination.accept().is_err(),
            "redirect destination received a request"
        );
    }
}

#[test]
fn r02_rejects_access_bearer_and_gateway_reflection_in_every_safe_string_layer() {
    tauri::async_runtime::block_on(async {
        let runtime =
            NativeSessionRuntime::for_contract_test(Arc::new(CloudVault), Arc::new(CloudIdentity));
        let mut reflected_backup = backup_view();
        reflected_backup["workspace_id"] =
            serde_json::Value::String(format!("workspace-{CLOUD_ACCESS}"));
        let cases = [
            (
                cloud_request(
                    "GET",
                    "/api/v1/backups/backup-0123456789abcdef01234567",
                    serde_json::Value::Null,
                    None,
                    None,
                    None,
                ),
                cloud_envelope(reflected_backup),
                Some("\"backup:backup-0123456789abcdef01234567:2\"".to_owned()),
            ),
            (
                cloud_request(
                    "GET",
                    "/api/v1/restore-requests/restore-cloud",
                    serde_json::Value::Null,
                    None,
                    None,
                    None,
                ),
                {
                    let mut restore = restore_view();
                    restore["preview"]["exclusion_reasons"] = serde_json::json!([
                        ["fixture-object-cloud", format!("prefix-Bearer {CLOUD_ACCESS}-suffix")]
                    ]);
                    cloud_envelope(restore)
                },
                Some("\"restore:restore-cloud:2\"".to_owned()),
            ),
            (
                cloud_request(
                    "GET",
                    "/api/v1/backups/backup-0123456789abcdef01234567",
                    serde_json::Value::Null,
                    None,
                    None,
                    None,
                ),
                serde_json::to_vec(&serde_json::json!({
                    "data": backup_view(),
                    "meta":{"trace_id":format!("trace-{}-suffix", daon_user_desktop_lib::native_session::PUBLIC_GATEWAY)}
                })).expect("gateway envelope"),
                Some("\"backup:backup-0123456789abcdef01234567:2\"".to_owned()),
            ),
        ];
        for (request, body, etag) in cases {
            let transport = ResponseCloudTransport {
                status: 200,
                body,
                etag,
                calls: Mutex::new(0),
            };
            assert_eq!(
                CloudRecoveryPort::new(&runtime, &transport)
                    .execute(request)
                    .await
                    .expect_err("reflected native context rejected")
                    .code(),
                "CLOUD_RECOVERY_RESPONSE_REJECTED"
            );
        }
    });
}

#[test]
fn r02_binds_response_resource_workspace_destination_and_etag_to_request() {
    tauri::async_runtime::block_on(async {
        let runtime =
            NativeSessionRuntime::for_contract_test(Arc::new(CloudVault), Arc::new(CloudIdentity));
        let cases = [
            (
                cloud_request(
                    "GET",
                    "/api/v1/backups/backup-requested",
                    serde_json::Value::Null,
                    None,
                    None,
                    None,
                ),
                200,
                cloud_envelope(backup_view()),
                "\"backup:backup-0123456789abcdef01234567:2\"",
            ),
            (
                cloud_request(
                    "GET",
                    "/api/v1/backups",
                    serde_json::Value::Null,
                    None,
                    None,
                    Some("workspace_id=workspace-requested"),
                ),
                200,
                cloud_envelope(serde_json::json!([backup_view()])),
                "\"projection-0123456789abcdef01234567\"",
            ),
            (
                cloud_request(
                    "POST",
                    "/api/v1/backups",
                    {
                        let mut body = backup_create_body();
                        body["workspace_id"] = serde_json::json!("workspace-requested");
                        body
                    },
                    Some("idempotency-create-0001"),
                    None,
                    None,
                ),
                201,
                cloud_envelope(backup_view()),
                "\"backup:backup-0123456789abcdef01234567:2\"",
            ),
            (
                cloud_request(
                    "POST",
                    "/api/v1/backups/backup-requested/restore-previews",
                    preview_body("step-binding-preview"),
                    Some("idempotency-preview-0001"),
                    None,
                    None,
                ),
                201,
                cloud_envelope(restore_view()),
                "\"restore:restore-cloud:2\"",
            ),
            (
                cloud_request(
                    "POST",
                    "/api/v1/backups/backup-0123456789abcdef01234567/restore-previews",
                    {
                        let mut body = preview_body("step-binding-destination");
                        body["destination"]["bucket_id"] =
                            serde_json::json!("fixture-requested-bucket");
                        body
                    },
                    Some("idempotency-preview-destination-0001"),
                    None,
                    None,
                ),
                201,
                cloud_envelope(restore_view()),
                "\"restore:restore-cloud:2\"",
            ),
            (
                cloud_request(
                    "GET",
                    "/api/v1/restore-requests/restore-requested",
                    serde_json::Value::Null,
                    None,
                    None,
                    None,
                ),
                200,
                cloud_envelope(restore_view()),
                "\"restore:restore-cloud:2\"",
            ),
            (
                cloud_request(
                    "POST",
                    "/api/v1/restore-requests/restore-requested/execute",
                    execute_body("step-binding-execute"),
                    Some("idempotency-execute-binding-0001"),
                    Some("\"restore:restore-requested:2\""),
                    None,
                ),
                200,
                cloud_envelope(restore_view()),
                "\"restore:restore-cloud:2\"",
            ),
            (
                cloud_request(
                    "POST",
                    "/api/v1/restore-requests/restore-requested/cancel",
                    serde_json::Value::Null,
                    Some("idempotency-cancel-binding-0001"),
                    Some("\"restore:restore-requested:2\""),
                    None,
                ),
                200,
                cloud_envelope(restore_view()),
                "\"restore:restore-cloud:2\"",
            ),
        ];
        for (request, status, body, etag) in cases {
            let transport = ResponseCloudTransport {
                status,
                body,
                etag: Some(etag.to_owned()),
                calls: Mutex::new(0),
            };
            assert_eq!(
                CloudRecoveryPort::new(&runtime, &transport)
                    .execute(request)
                    .await
                    .expect_err("cross-resource response rejected")
                    .code(),
                "CLOUD_RECOVERY_RESPONSE_REJECTED"
            );
        }
    });
}

#[test]
fn r02_enforces_expired_manifest_fixture_destination_and_exact_success_status() {
    tauri::async_runtime::block_on(async {
        let runtime =
            NativeSessionRuntime::for_contract_test(Arc::new(CloudVault), Arc::new(CloudIdentity));
        let mut expired = backup_view();
        expired["state"] = serde_json::json!("expired");
        expired["transitions"] =
            serde_json::json!(["queued", "capturing", "verifying", "ready", "expired"]);
        let accepted = ResponseCloudTransport {
            status: 200,
            body: cloud_envelope(expired),
            etag: Some("\"backup:backup-0123456789abcdef01234567:2\"".to_owned()),
            calls: Mutex::new(0),
        };
        CloudRecoveryPort::new(&runtime, &accepted)
            .execute(cloud_request(
                "GET",
                "/api/v1/backups/backup-0123456789abcdef01234567",
                serde_json::Value::Null,
                None,
                None,
                None,
            ))
            .await
            .expect("expired is an actual backup state");

        let mut null_manifest = backup_view();
        null_manifest["manifest_digest"] = serde_json::Value::Null;
        let mut unsafe_destination = restore_view();
        unsafe_destination["preview"]["destination"]["bucket_id"] =
            serde_json::json!("production-bucket");
        let rejected = [
            (
                cloud_request(
                    "GET",
                    "/api/v1/backups/backup-0123456789abcdef01234567",
                    serde_json::Value::Null,
                    None,
                    None,
                    None,
                ),
                200,
                cloud_envelope(null_manifest),
                "\"backup:backup-0123456789abcdef01234567:2\"",
            ),
            (
                cloud_request(
                    "GET",
                    "/api/v1/restore-requests/restore-cloud",
                    serde_json::Value::Null,
                    None,
                    None,
                    None,
                ),
                200,
                cloud_envelope(unsafe_destination),
                "\"restore:restore-cloud:2\"",
            ),
            (
                cloud_request(
                    "POST",
                    "/api/v1/backups",
                    backup_create_body(),
                    Some("idempotency-status-0001"),
                    None,
                    None,
                ),
                200,
                cloud_envelope(backup_view()),
                "\"backup:backup-0123456789abcdef01234567:2\"",
            ),
            (
                cloud_request(
                    "GET",
                    "/api/v1/backups/backup-0123456789abcdef01234567",
                    serde_json::Value::Null,
                    None,
                    None,
                    None,
                ),
                201,
                cloud_envelope(backup_view()),
                "\"backup:backup-0123456789abcdef01234567:2\"",
            ),
        ];
        for (request, status, body, etag) in rejected {
            let transport = ResponseCloudTransport {
                status,
                body,
                etag: Some(etag.to_owned()),
                calls: Mutex::new(0),
            };
            assert_eq!(
                CloudRecoveryPort::new(&runtime, &transport)
                    .execute(request)
                    .await
                    .expect_err("invalid runtime DTO/status rejected")
                    .code(),
                "CLOUD_RECOVERY_RESPONSE_REJECTED"
            );
        }
    });
}

#[test]
fn r02_idempotency_uses_server_intersection_and_bounded_cache_recycles() {
    tauri::async_runtime::block_on(async {
        let runtime =
            NativeSessionRuntime::for_contract_test(Arc::new(CloudVault), Arc::new(CloudIdentity));
        for invalid in ["short", "invalid key space", &"x".repeat(129)] {
            let transport = FakeCloudTransport {
                calls: Mutex::new(Vec::new()),
                failures: Mutex::new(Vec::new()),
            };
            assert_eq!(
                CloudRecoveryPort::new(&runtime, &transport)
                    .execute(cloud_request(
                        "POST",
                        "/api/v1/backups",
                        backup_create_body(),
                        Some(invalid),
                        None,
                        None
                    ))
                    .await
                    .expect_err("invalid idempotency key")
                    .code(),
                "CLOUD_RECOVERY_INPUT_INVALID"
            );
            assert!(transport.calls.lock().expect("calls").is_empty());
        }
        let transport = FakeCloudTransport {
            calls: Mutex::new(Vec::new()),
            failures: Mutex::new(Vec::new()),
        };
        let port = CloudRecoveryPort::new(&runtime, &transport);
        for index in 0..129 {
            port.execute(cloud_request(
                "POST",
                "/api/v1/backups",
                backup_create_body(),
                Some(&format!("idempotency-{index:04}")),
                None,
                None,
            ))
            .await
            .expect("bounded cache recycles oldest generation");
        }
        let (idempotency_count, _, raw_present) =
            port.consumption_cache_state_for_contract("idempotency-0128", "unused");
        assert!(idempotency_count <= 128);
        assert!(!raw_present);
        assert_eq!(transport.calls.lock().expect("calls").len(), 129);
    });
}

#[test]
fn r02_actual_write_abort_drops_the_app_owned_zeroizing_wire_buffer() {
    NativeCloudRecoveryClient::reset_wire_drop_audit_for_contract();
    let listener = TcpListener::bind("127.0.0.1:0").expect("listener");
    let gateway = format!("http://{}", listener.local_addr().expect("address"));
    let (entered_sender, entered_receiver) = mpsc::sync_channel(1);
    let server = thread::spawn(move || {
        let (mut stream, _) = listener.accept().expect("accept");
        let _ = read_request(&mut stream);
        entered_sender.send(()).expect("wire entered");
        thread::sleep(Duration::from_millis(250));
    });
    tauri::async_runtime::block_on(async move {
        let client = NativeCloudRecoveryClient::for_contract_test(&gateway, Duration::from_secs(2))
            .expect("client");
        let task = tauri::async_runtime::spawn(async move {
            client
                .execute(
                    CLOUD_ACCESS.as_bytes(),
                    cloud_request(
                        "POST",
                        "/api/v1/backups/backup-0123456789abcdef01234567/restore-previews",
                        preview_body("step-up-wire-abort-sensitive"),
                        Some("idempotency-wire-abort-0001"),
                        None,
                        None,
                    ),
                )
                .await
        });
        entered_receiver
            .recv_timeout(Duration::from_secs(1))
            .expect("request entered actual wire");
        task.abort();
        let _ = task.await;
    });
    server.join().expect("server");
    assert!(
        NativeCloudRecoveryClient::wire_drop_audit_for_contract() >= 1,
        "aborted write had no app-owned zeroizing wire-buffer drop"
    );
    assert!(
        NativeCloudRecoveryClient::wire_body_owns_zeroizing_source_for_contract(),
        "actual reqwest body used a separate non-zeroizing Daon allocation"
    );
}

#[test]
fn direct_implementation_rejects_exact_and_unicode_escaped_reflection() {
    tauri::async_runtime::block_on(async {
        let runtime =
            NativeSessionRuntime::for_contract_test(Arc::new(CloudVault), Arc::new(CloudIdentity));
        let control = ResponseCloudTransport {
            status: 403,
            body: serde_json::to_vec(&serde_json::json!({
                "error": {
                    "code": "FORBIDDEN",
                    "message": "request denied",
                    "stage": "request",
                    "impact": "request_not_completed",
                    "retryable": false,
                    "user_action": "check access",
                    "trace_id": "trace-safe-control",
                    "details": {}
                }
            }))
            .expect("non-reflecting control envelope"),
            etag: None,
            calls: Mutex::new(0),
        };
        assert_eq!(
            CloudRecoveryPort::new(&runtime, &control)
                .execute(cloud_request(
                    "GET",
                    "/api/v1/backups/backup-0123456789abcdef01234567",
                    serde_json::Value::Null,
                    None,
                    None,
                    None,
                ))
                .await
                .expect_err("non-reflecting control remains a safe denial")
                .code(),
            "FORBIDDEN"
        );
        let escaped_access = CLOUD_ACCESS.replacen('a', r"\u0061", 1);
        let bodies = [
            serde_json::to_vec(&serde_json::json!({
                "error": {
                    "code": "FORBIDDEN",
                    "message": "request denied",
                    "stage": "request",
                    "impact": "request_not_completed",
                    "retryable": false,
                    "user_action": "check access",
                    "trace_id": CLOUD_ACCESS,
                    "details": {}
                }
            }))
            .expect("exact reflection envelope"),
            format!(
                r#"{{"error":{{"code":"FORBIDDEN","message":"request denied","stage":"request","impact":"request_not_completed","retryable":false,"user_action":"check access","trace_id":"{escaped_access}","details":{{}}}}}}"#
            )
            .into_bytes(),
        ];
        for body in bodies {
            let transport = ResponseCloudTransport {
                status: 403,
                body,
                etag: None,
                calls: Mutex::new(0),
            };
            assert_eq!(
                CloudRecoveryPort::new(&runtime, &transport)
                    .execute(cloud_request(
                        "GET",
                        "/api/v1/backups/backup-0123456789abcdef01234567",
                        serde_json::Value::Null,
                        None,
                        None,
                        None,
                    ))
                    .await
                    .expect_err("reflected trace rejected")
                    .code(),
                "CLOUD_RECOVERY_RESPONSE_REJECTED"
            );
        }
    });
}

#[test]
fn r02_actual_write_normal_timeout_and_early_error_drop_the_app_owned_wire_buffer() {
    NativeCloudRecoveryClient::reset_wire_drop_audit_for_contract();

    let normal_listener = TcpListener::bind("127.0.0.1:0").expect("normal listener");
    let normal_gateway = format!(
        "http://{}",
        normal_listener.local_addr().expect("normal address")
    );
    let normal_server = thread::spawn(move || {
        let (mut stream, _) = normal_listener.accept().expect("normal accept");
        let _ = read_request(&mut stream);
        use std::io::Write;
        stream
            .write_all(
                b"HTTP/1.1 201 Created\r\nContent-Type: application/json\r\nContent-Length: 2\r\nConnection: close\r\n\r\n{}",
            )
            .expect("normal response");
    });
    tauri::async_runtime::block_on(async {
        NativeCloudRecoveryClient::for_contract_test(&normal_gateway, Duration::from_secs(2))
            .expect("normal client")
            .execute(
                CLOUD_ACCESS.as_bytes(),
                cloud_request(
                    "POST",
                    "/api/v1/backups",
                    backup_create_body(),
                    Some("idempotency-wire-normal-0001"),
                    None,
                    None,
                ),
            )
            .await
            .expect("normal response");
    });
    normal_server.join().expect("normal server");

    let timeout_listener = TcpListener::bind("127.0.0.1:0").expect("timeout listener");
    let timeout_gateway = format!(
        "http://{}",
        timeout_listener.local_addr().expect("timeout address")
    );
    let timeout_server = thread::spawn(move || {
        let (mut stream, _) = timeout_listener.accept().expect("timeout accept");
        let _ = read_request(&mut stream);
        thread::sleep(Duration::from_millis(150));
    });
    tauri::async_runtime::block_on(async {
        let result = NativeCloudRecoveryClient::for_contract_test(
            &timeout_gateway,
            Duration::from_millis(50),
        )
        .expect("timeout client")
        .execute(
            CLOUD_ACCESS.as_bytes(),
            cloud_request(
                "POST",
                "/api/v1/backups",
                backup_create_body(),
                Some("idempotency-wire-timeout-0001"),
                None,
                None,
            ),
        )
        .await;
        assert_eq!(result.err(), Some("CLOUD_RECOVERY_REQUEST_FAILED"));
    });
    timeout_server.join().expect("timeout server");

    tauri::async_runtime::block_on(async {
        let result = NativeCloudRecoveryClient::for_contract_test(
            "http://127.0.0.1:9",
            Duration::from_millis(50),
        )
        .expect("early client")
        .execute(
            CLOUD_ACCESS.as_bytes(),
            cloud_request(
                "POST",
                "/api//v1/backups",
                backup_create_body(),
                Some("idempotency-wire-early-0001"),
                None,
                None,
            ),
        )
        .await;
        assert_eq!(result.err(), Some("CLOUD_RECOVERY_REQUEST_FAILED"));
    });

    assert!(
        NativeCloudRecoveryClient::wire_drop_audit_for_contract() >= 3,
        "normal, timeout, and early error must each drop the app-owned wire guard"
    );
}

#[test]
fn native_recovery_runtime_maps_exact_cloud_commands_and_keeps_consumption_history() {
    tauri::async_runtime::block_on(async {
        let session =
            NativeSessionRuntime::for_contract_test(Arc::new(CloudVault), Arc::new(CloudIdentity));
        let transport = Arc::new(FakeCloudTransport {
            calls: Mutex::new(Vec::new()),
            failures: Mutex::new(Vec::new()),
        });
        let runtime = NativeRecoveryRuntime::for_contract_test(transport.clone());

        runtime
            .cloud_create_backup(&session, backup_create_command("command-create-0001"))
            .await
            .expect("create");
        runtime
            .cloud_list_backups(
                &session,
                command_input::<CloudListBackupsCommandInput>(serde_json::json!({
                    "workspace_id":"workspace-cloud"
                })),
            )
            .await
            .expect("list");
        runtime
            .cloud_get_backup(
                &session,
                command_input::<CloudGetBackupCommandInput>(serde_json::json!({
                    "backup_id":"backup-0123456789abcdef01234567"
                })),
            )
            .await
            .expect("get backup");
        runtime
            .cloud_preview_restore(
                &session,
                command_input::<CloudPreviewRestoreCommandInput>(serde_json::json!({
                    "backup_id":"backup-0123456789abcdef01234567",
                    "destination":{
                        "tenant_id":"fixture-tenant-cloud",
                        "workspace_id":"fixture-workspace-cloud",
                        "database_id":"fixture-database-cloud",
                        "bucket_id":"fixture-bucket-cloud"
                    },
                    "step_up_authorization_id":"command-step-preview",
                    "idempotency_key":"command-preview-0001"
                })),
            )
            .await
            .expect("preview");
        runtime
            .cloud_get_restore(
                &session,
                command_input::<CloudGetRestoreCommandInput>(serde_json::json!({
                    "restore_request_id":"restore-cloud"
                })),
            )
            .await
            .expect("get restore");
        runtime
            .cloud_execute_restore(
                &session,
                command_input::<CloudExecuteRestoreCommandInput>(serde_json::json!({
                    "restore_request_id":"restore-cloud",
                    "preview_version":1,
                    "step_up_authorization_id":"command-step-execute",
                    "idempotency_key":"command-execute-0001",
                    "if_match":"\"restore:restore-cloud:1\""
                })),
            )
            .await
            .expect("execute");
        runtime
            .cloud_cancel_restore(
                &session,
                command_input::<CloudCancelRestoreCommandInput>(serde_json::json!({
                    "restore_request_id":"restore-cloud",
                    "idempotency_key":"command-cancel-0001",
                    "if_match":"\"restore:restore-cloud:2\""
                })),
            )
            .await
            .expect("cancel");

        let calls = transport.calls.lock().expect("calls");
        assert_eq!(calls.len(), 7);
        assert_eq!(
            calls
                .iter()
                .map(|(_, method, path)| (method.as_str(), path.as_str()))
                .collect::<Vec<_>>(),
            vec![
                ("POST", "/api/v1/backups"),
                ("GET", "/api/v1/backups"),
                ("GET", "/api/v1/backups/backup-0123456789abcdef01234567"),
                (
                    "POST",
                    "/api/v1/backups/backup-0123456789abcdef01234567/restore-previews",
                ),
                ("GET", "/api/v1/restore-requests/restore-cloud"),
                ("POST", "/api/v1/restore-requests/restore-cloud/execute"),
                ("POST", "/api/v1/restore-requests/restore-cloud/cancel"),
            ]
        );
        drop(calls);

        let error = runtime
            .cloud_create_backup(&session, backup_create_command("command-create-0001"))
            .await
            .expect_err("idempotency history must survive command calls");
        assert_eq!(error.code(), "CLOUD_RECOVERY_INPUT_INVALID");
        let error = runtime
            .cloud_execute_restore(
                &session,
                command_input::<CloudExecuteRestoreCommandInput>(serde_json::json!({
                    "restore_request_id":"restore-cloud",
                    "preview_version":1,
                    "step_up_authorization_id":"command-step-preview",
                    "idempotency_key":"command-execute-0002",
                    "if_match":"\"restore:restore-cloud:2\""
                })),
            )
            .await
            .expect_err("step-up history must survive command calls");
        assert_eq!(error.code(), "CLOUD_RECOVERY_INPUT_INVALID");
        assert_eq!(transport.calls.lock().expect("calls").len(), 7);
    });
}

#[test]
fn native_recovery_runtime_rejects_unknown_fields_and_missing_session_before_transport() {
    let unknown = serde_json::from_value::<CloudListBackupsCommandInput>(serde_json::json!({
        "workspace_id":"workspace-cloud",
        "method":"DELETE"
    }));
    assert!(unknown.is_err());

    tauri::async_runtime::block_on(async {
        let session = NativeSessionRuntime::for_contract_test(
            Arc::new(EmptyCloudVault),
            Arc::new(CloudIdentity),
        );
        let transport = Arc::new(FakeCloudTransport {
            calls: Mutex::new(Vec::new()),
            failures: Mutex::new(Vec::new()),
        });
        let runtime = NativeRecoveryRuntime::for_contract_test(transport.clone());
        let error = runtime
            .cloud_list_backups(
                &session,
                command_input::<CloudListBackupsCommandInput>(serde_json::json!({
                    "workspace_id":"workspace-cloud"
                })),
            )
            .await
            .expect_err("missing session");
        assert_eq!(error.code(), "AUTHENTICATION_REQUIRED");
        assert!(transport.calls.lock().expect("calls").is_empty());
    });
}

struct BlockingLocalTransport {
    entered: mpsc::Sender<()>,
    release: mpsc::Receiver<()>,
}

impl LocalRecoveryTransport for BlockingLocalTransport {
    fn is_ready(&self) -> bool {
        true
    }

    fn execute(
        &self,
        _scope: &str,
        _capability: &str,
        _method: &str,
        _path: &str,
        _body: &[u8],
    ) -> Result<RecoveryHttpResponse, &'static str> {
        self.entered.send(()).expect("entered");
        self.release.recv().expect("release");
        Ok(response(job_json("")))
    }
}

struct UnavailableCountingTransport(Arc<AtomicUsize>);

impl LocalRecoveryTransport for UnavailableCountingTransport {
    fn is_ready(&self) -> bool {
        false
    }

    fn execute(
        &self,
        _scope: &str,
        _capability: &str,
        _method: &str,
        _path: &str,
        _body: &[u8],
    ) -> Result<RecoveryHttpResponse, &'static str> {
        self.0.fetch_add(1, Ordering::SeqCst);
        Err("must not execute")
    }
}

struct PanicLocalTransport;

impl LocalRecoveryTransport for PanicLocalTransport {
    fn is_ready(&self) -> bool {
        true
    }

    fn execute(
        &self,
        _scope: &str,
        _capability: &str,
        _method: &str,
        _path: &str,
        _body: &[u8],
    ) -> Result<RecoveryHttpResponse, &'static str> {
        panic!()
    }
}

#[test]
fn local_command_blocking_io_does_not_block_an_independent_async_operation() {
    tauri::async_runtime::block_on(async {
        let (entered_sender, entered_receiver) = mpsc::channel();
        let (release_sender, release_receiver) = mpsc::channel();
        let task = tauri::async_runtime::spawn(recovery_local_get_job_for_contract(
            BlockingLocalTransport {
                entered: entered_sender,
                release: release_receiver,
            },
            command_input::<LocalGetJobCommandInput>(serde_json::json!({"job_id":JOB_ID})),
        ));
        entered_receiver
            .recv_timeout(Duration::from_secs(1))
            .expect("blocking request entered");

        let (progress_sender, progress_receiver) = mpsc::channel();
        tauri::async_runtime::spawn(async move {
            progress_sender.send(()).expect("independent progress");
        });
        progress_receiver
            .recv_timeout(Duration::from_millis(250))
            .expect("independent async operation must progress while Local I/O is delayed");

        release_sender.send(()).expect("release blocking request");
        task.await.expect("task join").expect("local result");
    });
}

#[test]
fn local_command_helpers_keep_exact_three_method_path_and_input_mappings() {
    let listener = TcpListener::bind("127.0.0.1:0").expect("listener");
    let port_number = listener.local_addr().expect("address").port();
    let (sender, receiver) = mpsc::channel();
    let server = thread::spawn(move || {
        for _ in 0..3 {
            let (mut stream, _) = listener.accept().expect("accept");
            let request = read_request(&mut stream);
            sender.send(request).expect("capture");
            stream
                .write_all(&http_response("200 OK", &job_json("")))
                .expect("response");
        }
    });
    let manager =
        LocalServiceManager::with_contract_recovery_endpoint(port_number, Duration::from_secs(1));

    tauri::async_runtime::block_on(async {
        recovery_local_start_scan_for_contract(
            manager.clone(),
            command_input::<LocalStartScanCommandInput>(serde_json::json!({
                "workspace_id":WORKSPACE,
                "target_id":"fixture-damaged-object",
                "snapshot_checksum":"a".repeat(64),
                "metadata_checksum":"a".repeat(64),
                "actual_checksum":"b".repeat(64),
                "journal_present":true
            })),
        )
        .await
        .expect("scan command");
        recovery_local_get_job_for_contract(
            manager.clone(),
            command_input::<LocalGetJobCommandInput>(serde_json::json!({"job_id":JOB_ID})),
        )
        .await
        .expect("get command");
        recovery_local_repair_job_for_contract(
            manager,
            command_input::<LocalRepairJobCommandInput>(serde_json::json!({
                "job_id":JOB_ID,
                "workspace_id":WORKSPACE,
                "expected_version":4
            })),
        )
        .await
        .expect("repair command");
    });

    let requests: Vec<_> = (0..3)
        .map(|_| {
            receiver
                .recv_timeout(Duration::from_secs(1))
                .expect("request")
        })
        .collect();
    server.join().expect("server");
    assert!(requests[0].starts_with("POST /local/v1/recovery/scans HTTP/1.1"));
    assert!(requests[0].contains(&format!(r#""workspace_id":"{WORKSPACE}""#)));
    assert!(requests[1].starts_with(&format!("GET /local/v1/recovery/jobs/{JOB_ID} HTTP/1.1")));
    assert!(requests[2].starts_with(&format!(
        "POST /local/v1/recovery/jobs/{JOB_ID}/repair HTTP/1.1"
    )));
    assert!(requests[2].contains(r#""expected_version":4"#));
}

#[test]
fn local_command_unavailable_skips_transport_and_join_failure_is_safe() {
    tauri::async_runtime::block_on(async {
        let calls = Arc::new(AtomicUsize::new(0));
        let error = recovery_local_start_scan_for_contract(
            UnavailableCountingTransport(Arc::clone(&calls)),
            command_input::<LocalStartScanCommandInput>(serde_json::json!({
                "workspace_id":WORKSPACE,
                "target_id":"fixture-damaged-object",
                "snapshot_checksum":"a".repeat(64),
                "metadata_checksum":"a".repeat(64),
                "actual_checksum":"b".repeat(64),
                "journal_present":true
            })),
        )
        .await
        .expect_err("unavailable");
        assert_eq!(error.code(), "LOCAL_SERVICE_UNAVAILABLE");
        assert_eq!(calls.load(Ordering::SeqCst), 0);

        let join_error = recovery_local_get_job_for_contract(
            PanicLocalTransport,
            command_input::<LocalGetJobCommandInput>(serde_json::json!({"job_id":JOB_ID})),
        )
        .await
        .expect_err("join failure");
        let safe = serde_json::to_value(join_error).expect("safe join error");
        assert_eq!(safe["code"], "LOCAL_RECOVERY_REQUEST_FAILED");
        assert_eq!(safe["retryable"], true);
        assert_eq!(safe["trace_id"].as_str().expect("trace").len(), 32);
    });
}
