use daon_user_desktop_lib::native_session::{
    LOCAL_STORAGE_CREDENTIAL_TARGET, NATIVE_SESSION_CREDENTIAL_TARGET, NativeHttpResponse,
    NativeIdentityClient, NativeIdentityTransportPort, NativeSessionCredentials,
    NativeSessionProjection, NativeSessionRuntime, NativeSessionStatus, NativeSessionVault,
    NativeSessionVaultPort, PUBLIC_GATEWAY, partial_secret_drop_count_for_contract,
    reset_partial_secret_drop_audit_for_contract,
};
use std::thread;
use std::{
    future::Future,
    io::{Read, Write},
    net::{TcpListener, TcpStream},
    pin::Pin,
    sync::{
        Arc, Mutex,
        atomic::{AtomicBool, AtomicUsize, Ordering},
    },
};

#[cfg(windows)]
use windows_sys::Win32::Security::Credentials::{
    CRED_PERSIST_LOCAL_MACHINE, CRED_TYPE_GENERIC, CREDENTIALW, CredWriteW,
};

fn projection() -> NativeSessionProjection {
    NativeSessionProjection::new(
        "usr-contract".to_owned(),
        "ten-contract".to_owned(),
        "wsp-contract".to_owned(),
        "ses-contract".to_owned(),
        "dev-contract".to_owned(),
        "2026-08-10T12:00:00+00:00".to_owned(),
    )
    .expect("safe projection")
}

#[test]
fn product_runtime_exposes_only_a_test_constructor_for_injected_ports() {
    let _ = NativeSessionRuntime::for_contract_test;
}

fn credentials() -> NativeSessionCredentials {
    NativeSessionCredentials::new(
        "access-contract-secret-0000000000000000000000".to_owned(),
        "refresh-contract-secret-000000000000000000000".to_owned(),
        projection(),
    )
    .expect("native credentials")
}

fn replacement_credentials() -> NativeSessionCredentials {
    NativeSessionCredentials::new(
        "access-replacement-secret-0000000000000000000000".to_owned(),
        "refresh-replacement-secret-000000000000000000000".to_owned(),
        NativeSessionProjection::new(
            "usr-contract".to_owned(),
            "ten-contract".to_owned(),
            "wsp-contract".to_owned(),
            "ses-replacement".to_owned(),
            "dev-contract".to_owned(),
            "2026-08-10T12:05:00+00:00".to_owned(),
        )
        .expect("replacement projection"),
    )
    .expect("replacement credentials")
}

fn read_http_request(stream: &mut TcpStream) -> Vec<u8> {
    stream
        .set_read_timeout(Some(std::time::Duration::from_secs(2)))
        .expect("read timeout");
    let mut request = Vec::new();
    let mut buffer = [0_u8; 4096];
    let mut expected = None;
    loop {
        match stream.read(&mut buffer) {
            Ok(0) => break,
            Ok(read) => {
                request.extend_from_slice(&buffer[..read]);
                if expected.is_none() {
                    if let Some(header_end) =
                        request.windows(4).position(|part| part == b"\r\n\r\n")
                    {
                        let headers = String::from_utf8_lossy(&request[..header_end]);
                        let length = headers
                            .lines()
                            .find_map(|line| {
                                line.strip_prefix("content-length: ")
                                    .or_else(|| line.strip_prefix("Content-Length: "))
                                    .and_then(|value| value.parse::<usize>().ok())
                            })
                            .unwrap_or(0);
                        expected = Some(header_end + 4 + length);
                    }
                }
                if expected.is_some_and(|length| request.len() >= length) {
                    break;
                }
            }
            Err(error)
                if error.kind() == std::io::ErrorKind::WouldBlock
                    || error.kind() == std::io::ErrorKind::TimedOut =>
            {
                break;
            }
            Err(error) => panic!("request read: {error}"),
        }
    }
    request
}

fn serve_once(
    response: Vec<u8>,
    delay: std::time::Duration,
) -> (
    String,
    std::sync::mpsc::Receiver<Vec<u8>>,
    thread::JoinHandle<()>,
) {
    let listener = TcpListener::bind("127.0.0.1:0").expect("local server");
    let address = listener.local_addr().expect("server address");
    let (sent, received) = std::sync::mpsc::channel();
    let handle = thread::spawn(move || {
        let (mut stream, _) = listener.accept().expect("accept request");
        sent.send(read_http_request(&mut stream))
            .expect("request capture");
        if !delay.is_zero() {
            thread::sleep(delay);
        }
        let _ = stream.write_all(&response);
    });
    (format!("http://{address}"), received, handle)
}

fn run_local_login(
    client: &NativeIdentityClient,
) -> Result<NativeSessionCredentials, daon_user_desktop_lib::native_session::NativeSessionError> {
    tokio::runtime::Builder::new_current_thread()
        .enable_io()
        .enable_time()
        .build()
        .expect("test runtime")
        .block_on(client.login(
            "login-contract".to_owned(),
            "password-contract-secret".to_owned(),
        ))
}

#[test]
fn actual_reqwest_redirect_never_hits_destination_or_forwards_login_secret() {
    for status in [307, 308] {
        let destination = TcpListener::bind("127.0.0.1:0").expect("redirect destination");
        destination
            .set_nonblocking(true)
            .expect("nonblocking destination");
        let destination_url = format!("http://{}", destination.local_addr().expect("address"));
        let response = format!(
            "HTTP/1.1 {status} Redirect\r\nLocation: {destination_url}/stolen\r\nContent-Length: 0\r\nConnection: close\r\n\r\n"
        ).into_bytes();
        let (gateway, request, server) = serve_once(response, std::time::Duration::ZERO);
        let client =
            NativeIdentityClient::for_contract_test(&gateway, std::time::Duration::from_secs(2))
                .expect("test client");
        assert_eq!(
            run_local_login(&client)
                .expect_err("redirect denied")
                .code(),
            "AUTHENTICATION_REQUIRED"
        );
        let source_request = request.recv().expect("source request");
        assert!(
            source_request
                .windows(b"password-contract-secret".len())
                .any(|part| part == b"password-contract-secret")
        );
        server.join().expect("source server");
        assert!(
            matches!(destination.accept(), Err(error) if error.kind() == std::io::ErrorKind::WouldBlock)
        );
    }
}

#[test]
fn actual_reqwest_timeout_chunk_overflow_malformed_length_and_truncation_fail_closed() {
    let cases = vec![
        (
            b"HTTP/1.1 200 OK\r\nContent-Type: application/json\r\nContent-Length: 0\r\nConnection: close\r\n\r\n".to_vec(),
            std::time::Duration::from_millis(150),
            std::time::Duration::from_millis(40),
        ),
        ({
            let mut response = b"HTTP/1.1 200 OK\r\nContent-Type: application/json\r\nTransfer-Encoding: chunked\r\nConnection: close\r\n\r\n20001\r\n".to_vec();
            response.extend(vec![b'x'; 128 * 1024 + 1]);
            response.extend_from_slice(b"\r\n0\r\n\r\n");
            response
        }, std::time::Duration::ZERO, std::time::Duration::from_secs(2)),
        (
            b"HTTP/1.1 200 OK\r\nContent-Type: application/json\r\nContent-Length: nope\r\nConnection: close\r\n\r\n{}".to_vec(),
            std::time::Duration::ZERO,
            std::time::Duration::from_secs(2),
        ),
        (
            b"HTTP/1.1 200 OK\r\nContent-Type: application/json\r\nContent-Length: 100\r\nConnection: close\r\n\r\nshort".to_vec(),
            std::time::Duration::ZERO,
            std::time::Duration::from_secs(2),
        ),
    ];
    for (response, delay, timeout) in cases {
        let (gateway, _request, server) = serve_once(response, delay);
        let client =
            NativeIdentityClient::for_contract_test(&gateway, timeout).expect("test client");
        assert_eq!(
            run_local_login(&client)
                .expect_err("transport fails closed")
                .code(),
            "AUTHENTICATION_REQUIRED"
        );
        server.join().expect("server");
    }
}

#[test]
fn request_wire_and_response_buffer_drop_guards_run_on_cancel_and_early_failure() {
    let cancellation_audit = Arc::new(AtomicUsize::new(0));
    let (gateway, request, server) = serve_once(
        b"HTTP/1.1 200 OK\r\nContent-Length: 0\r\nConnection: close\r\n\r\n".to_vec(),
        std::time::Duration::from_millis(120),
    );
    let client = Arc::new(
        NativeIdentityClient::for_contract_test_with_drop_audit(
            &gateway,
            std::time::Duration::from_secs(2),
            cancellation_audit.clone(),
        )
        .expect("audited client"),
    );
    tokio::runtime::Builder::new_current_thread()
        .enable_io()
        .enable_time()
        .build()
        .expect("test runtime")
        .block_on(async {
            let task = {
                let client = client.clone();
                tokio::spawn(async move {
                    client
                        .login(
                            "login-contract".to_owned(),
                            "password-contract-secret".to_owned(),
                        )
                        .await
                })
            };
            for _ in 0..4 {
                tokio::task::yield_now().await;
            }
            request
                .recv_timeout(std::time::Duration::from_secs(2))
                .expect("request reached waiting server");
            task.abort();
            let _ = task.await;
        });
    server.join().expect("cancellation server");
    assert!(cancellation_audit.load(Ordering::Acquire) >= 1);

    let refresh_cancellation_audit = Arc::new(AtomicUsize::new(0));
    let (gateway, request, server) = serve_once(
        b"HTTP/1.1 200 OK\r\nContent-Length: 0\r\nConnection: close\r\n\r\n".to_vec(),
        std::time::Duration::from_millis(120),
    );
    let client = Arc::new(
        NativeIdentityClient::for_contract_test_with_drop_audit(
            &gateway,
            std::time::Duration::from_secs(2),
            refresh_cancellation_audit.clone(),
        )
        .expect("audited refresh client"),
    );
    let current = Arc::new(credentials());
    tokio::runtime::Builder::new_current_thread()
        .enable_io()
        .enable_time()
        .build()
        .expect("test runtime")
        .block_on(async {
            let task = {
                let client = client.clone();
                let current = current.clone();
                tokio::spawn(async move { client.refresh_once(&current).await })
            };
            for _ in 0..4 {
                tokio::task::yield_now().await;
            }
            request
                .recv_timeout(std::time::Duration::from_secs(2))
                .expect("refresh reached waiting server");
            task.abort();
            let _ = task.await;
        });
    server.join().expect("refresh cancellation server");
    assert!(refresh_cancellation_audit.load(Ordering::Acquire) >= 1);

    let buffer_audit = Arc::new(AtomicUsize::new(0));
    let (gateway, _request, server) = serve_once(
        b"HTTP/1.1 200 OK\r\nContent-Type: application/json\r\nContent-Length: 100\r\nConnection: close\r\n\r\nshort".to_vec(),
        std::time::Duration::ZERO,
    );
    let client = NativeIdentityClient::for_contract_test_with_drop_audit(
        &gateway,
        std::time::Duration::from_secs(2),
        buffer_audit.clone(),
    )
    .expect("audited client");
    assert!(run_local_login(&client).is_err());
    server.join().expect("truncated server");
    assert!(buffer_audit.load(Ordering::Acquire) >= 2);

    let wire_audit = Arc::new(AtomicUsize::new(0));
    let body = serde_json::json!({
        "data": {
            "user_id": "usr-contract", "tenant_id": "ten-contract", "workspace_id": "wsp-contract",
            "session_id": "ses-contract", "device_id": "dev-contract", "client_kind": "web",
            "delivery": "native_https_opaque_bearer", "access_credential": "access-contract-secret-0000000000000000000000",
            "refresh_credential": "refresh-contract-secret-000000000000000000000", "expires_at": "2026-08-10T12:00:00+00:00"
        }
    }).to_string();
    let response = format!(
        "HTTP/1.1 200 OK\r\nContent-Type: application/json\r\nContent-Length: {}\r\nConnection: close\r\n\r\n{body}",
        body.len()
    ).into_bytes();
    let (gateway, _request, server) = serve_once(response, std::time::Duration::ZERO);
    let client = NativeIdentityClient::for_contract_test_with_drop_audit(
        &gateway,
        std::time::Duration::from_secs(2),
        wire_audit.clone(),
    )
    .expect("audited client");
    assert!(run_local_login(&client).is_err());
    server.join().expect("wire server");
    assert!(wire_audit.load(Ordering::Acquire) >= 4);
}

struct RuntimeVault {
    state: Mutex<u8>,
    revoke_failures_remaining: Mutex<u8>,
    revokes: AtomicUsize,
}

impl RuntimeVault {
    fn new(revoke_failures_remaining: u8) -> Self {
        Self {
            state: Mutex::new(0),
            revoke_failures_remaining: Mutex::new(revoke_failures_remaining),
            revokes: AtomicUsize::new(0),
        }
    }
}

impl NativeSessionVaultPort for RuntimeVault {
    fn read(
        &self,
    ) -> Result<
        Option<NativeSessionCredentials>,
        daon_user_desktop_lib::native_session::NativeSessionError,
    > {
        Ok(match *self.state.lock().expect("state") {
            0 => Some(credentials()),
            1 => Some(replacement_credentials()),
            _ => None,
        })
    }

    fn write(
        &self,
        _credentials: &NativeSessionCredentials,
    ) -> Result<(), daon_user_desktop_lib::native_session::NativeSessionError> {
        *self.state.lock().expect("state") = 1;
        Ok(())
    }

    fn revoke(&self) -> Result<(), daon_user_desktop_lib::native_session::NativeSessionError> {
        self.revokes.fetch_add(1, Ordering::AcqRel);
        let mut remaining = self
            .revoke_failures_remaining
            .lock()
            .expect("revoke failures");
        if *remaining > 0 {
            *remaining -= 1;
            return Err(daon_user_desktop_lib::native_session::NativeSessionError::authentication_required_for_contract());
        }
        *self.state.lock().expect("state") = 2;
        Ok(())
    }
}

struct RuntimeTransport {
    calls: AtomicUsize,
    login_calls: AtomicUsize,
    block_first: AtomicBool,
    entered: tokio::sync::Notify,
    release: tokio::sync::Notify,
}

impl RuntimeTransport {
    fn new(block_first: bool) -> Self {
        Self {
            calls: AtomicUsize::new(0),
            login_calls: AtomicUsize::new(0),
            block_first: AtomicBool::new(block_first),
            entered: tokio::sync::Notify::new(),
            release: tokio::sync::Notify::new(),
        }
    }
}

impl NativeIdentityTransportPort for RuntimeTransport {
    fn login<'a>(
        &'a self,
        _login_id: String,
        _password: String,
    ) -> Pin<
        Box<
            dyn Future<
                    Output = Result<
                        NativeSessionCredentials,
                        daon_user_desktop_lib::native_session::NativeSessionError,
                    >,
                > + Send
                + 'a,
        >,
    > {
        Box::pin(async move {
            self.login_calls.fetch_add(1, Ordering::AcqRel);
            Ok(replacement_credentials())
        })
    }

    fn refresh<'a>(
        &'a self,
        _credentials: &'a NativeSessionCredentials,
    ) -> Pin<
        Box<
            dyn Future<
                    Output = Result<
                        NativeSessionCredentials,
                        daon_user_desktop_lib::native_session::NativeSessionError,
                    >,
                > + Send
                + 'a,
        >,
    > {
        Box::pin(async move {
            self.calls.fetch_add(1, Ordering::AcqRel);
            if self.block_first.swap(false, Ordering::AcqRel) {
                self.entered.notify_one();
                self.release.notified().await;
            }
            Ok(replacement_credentials())
        })
    }
}

#[test]
fn product_runtime_coalesces_same_generation_refresh_and_allows_a_later_generation() {
    tokio::runtime::Builder::new_current_thread()
        .enable_io()
        .enable_time()
        .build()
        .expect("test runtime")
        .block_on(async {
            let vault = Arc::new(RuntimeVault::new(0));
            let transport = Arc::new(RuntimeTransport::new(true));
            let runtime = Arc::new(NativeSessionRuntime::for_contract_test(
                vault,
                transport.clone(),
            ));
            let first = {
                let runtime = runtime.clone();
                tokio::spawn(async move { runtime.refresh_once().await })
            };
            transport.entered.notified().await;
            let followers: Vec<_> = (0..4)
                .map(|_| {
                    let runtime = runtime.clone();
                    tokio::spawn(async move { runtime.refresh_once().await })
                })
                .collect();
            for _ in 0..4 {
                tokio::task::yield_now().await;
            }
            transport.release.notify_one();

            let expected =
                serde_json::to_string(&first.await.expect("first join").expect("first refresh"))
                    .expect("first status");
            for follower in followers {
                let status = follower
                    .await
                    .expect("follower join")
                    .expect("coalesced refresh");
                assert_eq!(serde_json::to_string(&status).expect("status"), expected);
            }
            assert_eq!(transport.calls.load(Ordering::Acquire), 1);

            assert!(
                runtime
                    .refresh_once()
                    .await
                    .expect("new generation refresh")
                    .is_authenticated()
            );
            assert_eq!(transport.calls.load(Ordering::Acquire), 2);
        });
}

#[test]
fn product_logout_revoke_failure_stays_fail_closed_until_retry_removes_target() {
    tokio::runtime::Builder::new_current_thread()
        .enable_io()
        .enable_time()
        .build()
        .expect("test runtime")
        .block_on(async {
            let vault = Arc::new(RuntimeVault::new(1));
            let runtime = NativeSessionRuntime::for_contract_test(
                vault.clone(),
                Arc::new(RuntimeTransport::new(false)),
            );
            assert_eq!(
                runtime
                    .logout()
                    .await
                    .expect_err("first revoke fails")
                    .code(),
                "AUTHENTICATION_REQUIRED"
            );
            assert_eq!(
                runtime
                    .status()
                    .await
                    .expect_err("pending revoke hides session")
                    .code(),
                "AUTHENTICATION_REQUIRED"
            );
            assert!(
                !runtime
                    .logout()
                    .await
                    .expect("revoke retry")
                    .is_authenticated()
            );
            assert!(
                !runtime
                    .status()
                    .await
                    .expect("target absent")
                    .is_authenticated()
            );
            assert_eq!(*vault.state.lock().expect("state"), 2);
        });
}

#[test]
fn product_login_clears_pending_revoke_before_persisting_a_new_session() {
    tokio::runtime::Builder::new_current_thread()
        .enable_io()
        .enable_time()
        .build()
        .expect("test runtime")
        .block_on(async {
            let vault = Arc::new(RuntimeVault::new(1));
            let transport = Arc::new(RuntimeTransport::new(false));
            let runtime = NativeSessionRuntime::for_contract_test(vault.clone(), transport.clone());
            assert_eq!(
                runtime
                    .logout()
                    .await
                    .expect_err("first revoke fails")
                    .code(),
                "AUTHENTICATION_REQUIRED"
            );
            assert!(
                runtime
                    .login(
                        "login-contract".to_owned(),
                        "password-contract-secret".to_owned()
                    )
                    .await
                    .expect("login retries pending revoke")
                    .is_authenticated()
            );
            assert!(
                runtime
                    .status()
                    .await
                    .expect("new session remains visible")
                    .is_authenticated()
            );
            assert_eq!(vault.revokes.load(Ordering::Acquire), 2);
            assert_eq!(transport.login_calls.load(Ordering::Acquire), 1);
        });
}

#[test]
fn product_login_waits_for_an_inflight_refresh_transition() {
    tokio::runtime::Builder::new_current_thread()
        .enable_io()
        .enable_time()
        .build()
        .expect("test runtime")
        .block_on(async {
            let vault = Arc::new(RuntimeVault::new(0));
            let transport = Arc::new(RuntimeTransport::new(true));
            let runtime = Arc::new(NativeSessionRuntime::for_contract_test(
                vault,
                transport.clone(),
            ));
            let refresh = {
                let runtime = runtime.clone();
                tokio::spawn(async move { runtime.refresh_once().await })
            };
            transport.entered.notified().await;
            let login = {
                let runtime = runtime.clone();
                tokio::spawn(async move {
                    runtime
                        .login(
                            "login-contract".to_owned(),
                            "password-contract-secret".to_owned(),
                        )
                        .await
                })
            };
            for _ in 0..32 {
                tokio::task::yield_now().await;
            }
            assert_eq!(
                transport.login_calls.load(Ordering::Acquire),
                0,
                "login transport must not start before the refresh transition releases"
            );
            transport.release.notify_one();
            assert!(
                refresh
                    .await
                    .expect("refresh join")
                    .expect("refresh")
                    .is_authenticated()
            );
            assert!(
                login
                    .await
                    .expect("login join")
                    .expect("login")
                    .is_authenticated()
            );
            assert_eq!(transport.calls.load(Ordering::Acquire), 1);
            assert_eq!(transport.login_calls.load(Ordering::Acquire), 1);
        });
}

#[test]
fn native_session_target_is_separate_from_local_storage_root_key() {
    assert_eq!(
        NATIVE_SESSION_CREDENTIAL_TARGET,
        "DaonUser/NativeSession/v1"
    );
    assert_eq!(LOCAL_STORAGE_CREDENTIAL_TARGET, "DaonUser/LocalStorage/v1");
    assert_ne!(
        NATIVE_SESSION_CREDENTIAL_TARGET,
        LOCAL_STORAGE_CREDENTIAL_TARGET
    );
    assert_eq!(
        NativeSessionVault::for_app().target(),
        NATIVE_SESSION_CREDENTIAL_TARGET
    );
}

#[test]
fn credential_debug_and_safe_status_never_expose_opaque_credentials_or_gateway() {
    let credentials = credentials();
    let debug = format!("{credentials:?}");
    assert!(!debug.contains("access-contract-secret-0000000000000000000000"));
    assert!(!debug.contains("refresh-contract-secret-000000000000000000000"));
    assert!(!debug.contains(PUBLIC_GATEWAY));

    let safe = NativeSessionStatus::authenticated(credentials.projection());
    let json = serde_json::to_string(&safe).expect("safe DTO JSON");
    assert!(!json.contains("access-contract-secret-0000000000000000000000"));
    assert!(!json.contains("refresh-contract-secret-000000000000000000000"));
    assert!(!json.contains("credential"));
    assert!(!json.contains(PUBLIC_GATEWAY));
}

#[test]
fn corrupted_native_session_blob_fails_closed() {
    assert_eq!(
        NativeSessionCredentials::from_persisted_bytes(b"not-a-native-session"),
        Err("AUTHENTICATION_REQUIRED")
    );
}

#[test]
fn identity_client_accepts_only_fixed_https_gateway_and_two_identity_paths() {
    let client = NativeIdentityClient::for_gateway(PUBLIC_GATEWAY).expect("fixed public gateway");
    assert!(client.endpoint("/api/v1/auth/native/login").is_ok());
    assert!(client.endpoint("/api/v1/session/refresh").is_ok());
    for rejected_gateway in [
        "http://daon-user.sinsan.kr",
        "https://localhost:3330",
        "https://127.0.0.1:3330",
        "https://api:8000",
        "https://daon-user.sinsan.kr:3330",
    ] {
        assert_eq!(
            NativeIdentityClient::for_gateway(rejected_gateway)
                .expect_err("unapproved gateway")
                .code(),
            "AUTHENTICATION_REQUIRED"
        );
    }
    assert_eq!(
        client
            .endpoint("/api/v1/backups")
            .expect_err("unapproved path")
            .code(),
        "AUTHENTICATION_REQUIRED"
    );
}

#[test]
fn native_response_rejects_redirect_cookie_oversize_and_non_json_before_credential_use() {
    for response in [
        NativeHttpResponse::from_parts(
            307,
            Some("application/json".to_owned()),
            None,
            false,
            vec![],
        ),
        NativeHttpResponse::from_parts(
            308,
            Some("application/json".to_owned()),
            None,
            false,
            vec![],
        ),
        NativeHttpResponse::from_parts(
            200,
            Some("application/json".to_owned()),
            None,
            true,
            vec![],
        ),
        NativeHttpResponse::from_parts(200, Some("text/html".to_owned()), None, false, vec![]),
        NativeHttpResponse::from_parts(
            200,
            Some("application/json".to_owned()),
            Some(131_073),
            false,
            vec![],
        ),
        NativeHttpResponse::from_parts(
            200,
            Some("application/json".to_owned()),
            None,
            false,
            vec![0; 131_073],
        ),
    ] {
        assert_eq!(
            response
                .into_credentials()
                .expect_err("must fail closed")
                .code(),
            "AUTHENTICATION_REQUIRED"
        );
    }
}

#[test]
fn native_response_rejects_unknown_fields_and_invalid_expiry_format() {
    let mut unknown = serde_json::json!({
        "data": {
            "user_id": "usr-contract", "tenant_id": "ten-contract", "workspace_id": "wsp-contract",
            "session_id": "ses-contract", "device_id": "dev-contract", "client_kind": "native",
            "delivery": "native_https_opaque_bearer", "access_credential": "access-contract-secret-0000000000000000000000",
            "refresh_credential": "refresh-contract-secret-000000000000000000000", "expires_at": "not-a-time",
            "unexpected": "not allowed"
        }
    });
    let response = NativeHttpResponse::from_parts(
        200,
        Some("application/json".to_owned()),
        None,
        false,
        serde_json::to_vec(&unknown).expect("json"),
    );
    assert_eq!(
        response
            .into_credentials()
            .expect_err("unknown field denied")
            .code(),
        "AUTHENTICATION_REQUIRED"
    );
    unknown["data"]
        .as_object_mut()
        .expect("object")
        .remove("unexpected");
    let response = NativeHttpResponse::from_parts(
        200,
        Some("application/json".to_owned()),
        None,
        false,
        serde_json::to_vec(&unknown).expect("json"),
    );
    assert_eq!(
        response
            .into_credentials()
            .expect_err("invalid expiry denied")
            .code(),
        "AUTHENTICATION_REQUIRED"
    );
}

#[test]
fn partial_wire_deserialization_zeroizes_credentials_before_returning_an_error() {
    let bodies = [
        r#"{"data":{"user_id":"usr-contract","tenant_id":"ten-contract","workspace_id":"wsp-contract","session_id":"ses-contract","device_id":"dev-contract","client_kind":"native","delivery":"native_https_opaque_bearer","access_credential":"access-contract-secret-0000000000000000000000","refresh_credential":"refresh-contract-secret-000000000000000000000","unexpected":"denied","expires_at":"2026-08-10T12:00:00+00:00"}}"#,
        r#"{"data":{"user_id":"usr-contract","tenant_id":"ten-contract","workspace_id":"wsp-contract","session_id":"ses-contract","device_id":"dev-contract","client_kind":"native","delivery":"native_https_opaque_bearer","access_credential":"access-contract-secret-0000000000000000000000","refresh_credential":"refresh-contract-secret-000000000000000000000"}}"#,
        r#"{"data":{"user_id":"usr-contract","tenant_id":"ten-contract","workspace_id":"wsp-contract","session_id":"ses-contract","device_id":"dev-contract","client_kind":"native","delivery":"native_https_opaque_bearer","access_credential":"access-contract-secret-0000000000000000000000","refresh_credential":"refresh-contract-secret-000000000000000000000","expires_at":17}}"#,
    ];
    for body in bodies {
        reset_partial_secret_drop_audit_for_contract();
        let response = NativeHttpResponse::from_parts(
            200,
            Some("application/json".to_owned()),
            None,
            false,
            body.as_bytes().to_vec(),
        );
        assert_eq!(
            response
                .into_credentials()
                .expect_err("partial wire must fail closed")
                .code(),
            "AUTHENTICATION_REQUIRED"
        );
        assert_eq!(partial_secret_drop_count_for_contract(), 2);
    }
}

#[test]
fn partial_vault_deserialization_zeroizes_credentials_before_returning_an_error() {
    let bodies = [
        r#"{"version":1,"access_credential":"access-contract-secret-0000000000000000000000","refresh_credential":"refresh-contract-secret-000000000000000000000","projection":{"user_id":"usr-contract","tenant_id":"ten-contract","workspace_id":"wsp-contract","session_id":"ses-contract","device_id":"dev-contract","expires_at":"2026-08-10T12:00:00+00:00"},"unexpected":"denied"}"#,
        r#"{"version":1,"access_credential":"access-contract-secret-0000000000000000000000","refresh_credential":"refresh-contract-secret-000000000000000000000","projection":{"user_id":"usr-contract","tenant_id":"ten-contract","workspace_id":"wsp-contract","session_id":"ses-contract","device_id":"dev-contract","expires_at":17}}"#,
    ];
    for body in bodies {
        reset_partial_secret_drop_audit_for_contract();
        assert_eq!(
            NativeSessionCredentials::from_persisted_bytes(body.as_bytes()),
            Err("AUTHENTICATION_REQUIRED")
        );
        assert_eq!(partial_secret_drop_count_for_contract(), 2);
    }
}

#[test]
fn projection_rejects_invalid_rfc3339_calendar_time_offset_and_trailing_data() {
    for expires_at in [
        "2026-13-10T12:00:00+00:00",
        "2026-02-30T12:00:00+00:00",
        "2026-08-10T24:00:00+00:00",
        "2026-08-10T12:60:00+00:00",
        "2026-08-10T12:00:61+00:00",
        "2026-08-10T12:00:00+09:00",
        "2026-08-10T12:00:00+00:00 trailing",
    ] {
        assert_eq!(
            NativeSessionProjection::new(
                "usr-contract".to_owned(),
                "ten-contract".to_owned(),
                "wsp-contract".to_owned(),
                "ses-contract".to_owned(),
                "dev-contract".to_owned(),
                expires_at.to_owned(),
            ),
            Err("AUTHENTICATION_REQUIRED")
        );
    }
}

#[cfg(windows)]
#[test]
fn vault_round_trip_and_revoke_use_a_dedicated_native_target() {
    let target = format!(
        "DaonUser/NativeSession/contract-test/{}-{}",
        std::process::id(),
        std::time::SystemTime::now()
            .duration_since(std::time::UNIX_EPOCH)
            .expect("clock")
            .as_nanos()
    );
    let vault = NativeSessionVault::new(target.clone()).expect("test vault");
    vault.write(&credentials()).expect("write native session");
    let loaded = vault
        .read()
        .expect("read native session")
        .expect("stored session");
    assert_eq!(loaded.projection(), &projection());
    assert!(!format!("{vault:?}").contains(&target));
    vault.revoke().expect("revoke native session");
    assert_eq!(vault.read().expect("read after revoke"), None);
}

#[cfg(windows)]
#[test]
fn corrupt_windows_generic_credential_is_removed_before_a_status_can_be_returned() {
    let target = format!(
        "DaonUser/NativeSession/contract-corrupt/{}-{}",
        std::process::id(),
        std::time::SystemTime::now()
            .duration_since(std::time::UNIX_EPOCH)
            .expect("clock")
            .as_nanos()
    );
    let vault = NativeSessionVault::new(target.clone()).expect("test vault");
    let mut target_wide: Vec<u16> = target.encode_utf16().chain(std::iter::once(0)).collect();
    let mut username: Vec<u16> = "Daon Native Session"
        .encode_utf16()
        .chain(std::iter::once(0))
        .collect();
    let mut corrupt = b"{not-json".to_vec();
    let credential = CREDENTIALW {
        Type: CRED_TYPE_GENERIC,
        TargetName: target_wide.as_mut_ptr(),
        CredentialBlobSize: corrupt.len() as u32,
        CredentialBlob: corrupt.as_mut_ptr(),
        Persist: CRED_PERSIST_LOCAL_MACHINE,
        UserName: username.as_mut_ptr(),
        ..CREDENTIALW::default()
    };
    // SAFETY: all test buffers remain live for the single CredWriteW call.
    assert_ne!(unsafe { CredWriteW(&credential, 0) }, 0);
    corrupt.fill(0);
    assert_eq!(vault.read(), Err("AUTHENTICATION_REQUIRED"));
    assert_eq!(vault.read().expect("revoked target read"), None);
}
