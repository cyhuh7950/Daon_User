use daon_user_desktop_lib::local_service::{
    parse_ready_envelope, AppCredentials, LocalServiceState, READY_MAX_BYTES,
};
#[cfg(windows)]
use daon_user_desktop_lib::windows_credential::WindowsCredentialStore;

#[cfg(windows)]
struct CredentialCleanup<'a> {
    store: &'a WindowsCredentialStore,
    armed: bool,
}

#[cfg(windows)]
impl<'a> CredentialCleanup<'a> {
    fn new(store: &'a WindowsCredentialStore) -> Self {
        Self { store, armed: true }
    }

    fn disarm(&mut self) {
        self.armed = false;
    }
}

#[cfg(windows)]
impl Drop for CredentialCleanup<'_> {
    fn drop(&mut self) {
        if self.armed {
            let _ = self.store.revoke();
        }
    }
}

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
    assert!(!format!("{first:?}").contains(first_bootstrap["storage_root"].as_str().unwrap()));
    assert!(format!("{first:?}").contains("storage_root: \"[redacted]\""));
}

#[test]
fn ready_envelope_requires_exact_protocol_instance_and_loopback_port() {
    let credentials = AppCredentials::generate().expect("credentials");
    let valid = format!(
        r#"{{"event":"ready","protocol_version":"1.1","app_instance_id":"{}","port":48123}}"#,
        credentials.app_instance_id()
    );
    let ready = parse_ready_envelope(valid.as_bytes(), &credentials).expect("valid ready");
    assert_eq!(ready.port(), 48123);

    let wrong_instance = valid.replace(credentials.app_instance_id(), "other-instance");
    assert!(parse_ready_envelope(wrong_instance.as_bytes(), &credentials).is_err());
    let legacy_sidecar =
        valid.replace(r#""protocol_version":"1.1""#, r#""protocol_version":"1.0""#);
    assert!(parse_ready_envelope(legacy_sidecar.as_bytes(), &credentials).is_err());
    let missing_protocol = valid.replace(r#""protocol_version":"1.1","#, "");
    assert!(parse_ready_envelope(missing_protocol.as_bytes(), &credentials).is_err());
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

#[test]
fn recovery_token_allowlist_adds_only_the_three_approved_pairs() {
    let credentials = AppCredentials::generate().expect("credentials");
    let allowed = [
        ("recovery.write", "recovery.scan"),
        ("recovery.read", "recovery.job.read"),
        ("recovery.write", "recovery.repair"),
    ];
    let mut tokens = Vec::new();
    for (scope, capability) in allowed {
        let token = credentials
            .issue_request_token(scope, capability, 2_000_000_000)
            .expect("approved recovery token");
        let fields: Vec<_> = token.split('|').collect();
        assert_eq!(fields[4], scope);
        assert_eq!(fields[5], capability);
        tokens.push(token);
    }
    assert_eq!(tokens.len(), 3);
    assert_ne!(tokens[0], tokens[1]);
    assert_ne!(tokens[0], tokens[2]);
    assert_ne!(tokens[1], tokens[2]);

    for (scope, capability) in [
        ("recovery.read", "recovery.scan"),
        ("recovery.write", "recovery.job.read"),
        ("recovery.read", "recovery.repair"),
        ("runtime.read", "recovery.scan"),
        ("recovery.write", "storage.file.put"),
    ] {
        assert_eq!(
            credentials.issue_request_token(scope, capability, 2_000_000_000),
            Err("LOCAL_COMMAND_NOT_ALLOWED".to_owned())
        );
    }
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
    let mut cleanup = CredentialCleanup::new(&store);
    let first = store.load_or_create(false).expect("create credential");
    assert_eq!(first.expose_for_bootstrap().len(), 64);
    let second = store.load_or_create(true).expect("reuse credential");
    assert_eq!(first.expose_for_bootstrap(), second.expose_for_bootstrap());
    assert!(!format!("{first:?}").contains(&first.expose_for_bootstrap()));
    store.revoke().expect("revoke credential");
    assert_eq!(store.read().expect("read after revoke"), None);
    cleanup.disarm();
}
