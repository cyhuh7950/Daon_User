use daon_user_desktop_lib::local_service::{AppCredentials, LocalServiceManager};
use daon_user_desktop_lib::recovery_bridge::{
    LocalRecoveryJob, LocalRecoveryPort, LocalRecoveryTransport, RecoveryHttpResponse,
    RecoveryRepairRequest, RecoveryScanRequest,
};
use std::io::{Read, Write};
use std::net::TcpListener;
use std::sync::{mpsc, Mutex};
use std::thread;
use std::time::{Duration, Instant};

const WORKSPACE: &str = "55555555-5555-4555-8555-555555555555";
const JOB_ID: &str = "fixture-recovery-0123456789abcdef01234567";

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
    assert!(trace
        .bytes()
        .all(|byte| byte.is_ascii_hexdigit() && !byte.is_ascii_uppercase()));
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
