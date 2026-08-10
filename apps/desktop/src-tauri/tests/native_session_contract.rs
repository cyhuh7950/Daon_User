use daon_user_desktop_lib::native_session::{
    LOCAL_STORAGE_CREDENTIAL_TARGET, NATIVE_SESSION_CREDENTIAL_TARGET, NativeIdentityClient,
    NativeSessionCredentials, NativeSessionProjection, NativeSessionStatus, NativeSessionVault,
    PUBLIC_GATEWAY,
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

fn credentials() -> NativeSessionCredentials {
    NativeSessionCredentials::new(
        "access-contract-secret-0000000000000000000000".to_owned(),
        "refresh-contract-secret-000000000000000000000".to_owned(),
        projection(),
    )
    .expect("native credentials")
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
