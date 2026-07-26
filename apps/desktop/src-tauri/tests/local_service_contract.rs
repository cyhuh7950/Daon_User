use daon_user_desktop_lib::local_service::{
    parse_ready_envelope, AppCredentials, LocalServiceState, READY_MAX_BYTES,
};

#[test]
fn each_app_launch_generates_distinct_credentials() {
    let first = AppCredentials::generate().expect("first credentials");
    let second = AppCredentials::generate().expect("second credentials");
    assert_ne!(first.app_instance_id(), second.app_instance_id());
    assert_ne!(first.token(), second.token());
    assert!(first.bootstrap_json().len() <= 4096);
    assert!(!format!("{first:?}").contains(first.token()));
}

#[test]
fn ready_envelope_requires_exact_protocol_instance_and_loopback_port() {
    let credentials = AppCredentials::generate().expect("credentials");
    let valid = format!(
        r#"{{"event":"ready","protocol_version":"1.0","app_instance_id":"{}","port":48123}}"#,
        credentials.app_instance_id()
    );
    let ready = parse_ready_envelope(valid.as_bytes(), &credentials).expect("valid ready");
    assert_eq!(ready.port(), 48123);

    let wrong_instance = valid.replace(credentials.app_instance_id(), "other-instance");
    assert!(parse_ready_envelope(wrong_instance.as_bytes(), &credentials).is_err());
    assert!(parse_ready_envelope(&vec![b'x'; READY_MAX_BYTES + 1], &credentials).is_err());
    assert!(parse_ready_envelope(b"unexpected stdout", &credentials).is_err());
}

#[test]
fn public_status_never_contains_port_or_secret() {
    let status = LocalServiceState::ready();
    let json = serde_json::to_string(&status).expect("serialize status");
    assert_eq!(json, r#"{"state":"ready","retryable":false,"error_code":null}"#);
    assert!(!json.contains("port"));
    assert!(!json.contains("token"));
}
