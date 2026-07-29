use daon_user_desktop_lib::local_service::{
    AppCredentials, LocalServiceState, READY_MAX_BYTES, parse_ready_envelope,
};
#[cfg(windows)]
use daon_user_desktop_lib::windows_credential::WindowsCredentialStore;

#[test]
fn each_app_launch_generates_distinct_credentials() {
    let first = AppCredentials::generate().expect("first credentials");
    let second = AppCredentials::generate().expect("second credentials");
    assert_ne!(first.app_instance_id(), second.app_instance_id());
    let first_bootstrap: serde_json::Value =
        serde_json::from_str(&first.bootstrap_json()).expect("first bootstrap");
    let second_bootstrap: serde_json::Value =
        serde_json::from_str(&second.bootstrap_json()).expect("second bootstrap");
    assert_ne!(
        first_bootstrap["root_secret"],
        second_bootstrap["root_secret"]
    );
    assert_ne!(
        first_bootstrap["storage_root_key"],
        second_bootstrap["storage_root_key"]
    );
    assert_eq!(first_bootstrap["parent_process_id"], std::process::id());
    assert!(first.bootstrap_json().len() <= 4096);
    assert!(!format!("{first:?}").contains(first_bootstrap["root_secret"].as_str().unwrap()));
    assert!(!format!("{first:?}").contains(first_bootstrap["storage_root_key"].as_str().unwrap()));
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
    assert_eq!(
        json,
        r#"{"state":"ready","retryable":false,"error_code":null}"#
    );
    assert!(!json.contains("port"));
    assert!(!json.contains("token"));
}

#[cfg(windows)]
#[test]
fn windows_credential_manager_round_trip_and_revoke() {
    let target = format!(
        "DaonUser/R1-M5-03-test/{}-{}",
        std::process::id(),
        std::time::SystemTime::now()
            .duration_since(std::time::UNIX_EPOCH)
            .expect("clock")
            .as_nanos()
    );
    let store = WindowsCredentialStore::new(target);
    let first = store.load_or_create(false).expect("create credential");
    assert_eq!(first.expose_for_bootstrap().len(), 64);
    let second = store.load_or_create(true).expect("reuse credential");
    assert_eq!(first.expose_for_bootstrap(), second.expose_for_bootstrap());
    assert!(!format!("{first:?}").contains(&first.expose_for_bootstrap()));
    store.revoke().expect("revoke credential");
    assert_eq!(store.read().expect("read after revoke"), None);
}
