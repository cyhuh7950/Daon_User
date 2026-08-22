use daon_user_desktop_lib::local_service::AppCredentials;
use daon_user_desktop_lib::offline_studio_bridge::{
    AppendEditRequest, ConfirmSettingsRequest, GenerateDraftRequest, PrepareContextRequest,
    QueueSyncRequest, ImportRawSourceRequest,
};

#[test]
fn offline_studio_tokens_are_exact_and_each_nonce_is_fresh() {
    let credentials = AppCredentials::generate().expect("credentials");
    let allowed = [
        ("studio.read", "studio_models_list"),
        ("studio.read", "studio_raw_sources_list"),
        ("studio.write", "studio_raw_source_import"),
        ("studio.write", "studio_context_prepare"),
        ("studio.write", "studio_settings_confirm"),
        ("studio.write", "studio_draft_generate"),
        ("studio.read", "studio_draft_get"),
        ("studio.write", "studio_draft_append_version"),
        ("studio.write", "studio_sync_queue"),
    ];
    let tokens: Vec<_> = allowed
        .iter()
        .map(|(capability, command)| {
            credentials
                .issue_request_token(capability, command, 2_000_000_000)
                .expect("approved studio token")
        })
        .collect();
    assert_eq!(tokens.len(), 9);
    for (index, token) in tokens.iter().enumerate() {
        let fields: Vec<_> = token.split('|').collect();
        assert_eq!(fields[4], allowed[index].0);
        assert_eq!(fields[5], allowed[index].1);
        assert!(tokens.iter().skip(index + 1).all(|other| other != token));
    }
    for denied in [
        ("studio.read", "studio_context_prepare"),
        ("studio.write", "studio_draft_get"),
        ("runtime.read", "studio_models_list"),
    ] {
        assert_eq!(
            credentials.issue_request_token(denied.0, denied.1, 2_000_000_000),
            Err("LOCAL_COMMAND_NOT_ALLOWED".to_owned())
        );
    }
}

#[test]
fn offline_studio_command_dtos_deny_extra_keys() {
    let cases = [
        serde_json::from_str::<PrepareContextRequest>(r#"{"workspace_id":"w","mode":"mixed","daon_knowledge_ids":[],"raw_source_version_ids":[],"idempotency_key":"i","extra":true}"#).is_err(),
        serde_json::from_str::<ConfirmSettingsRequest>(r#"{"workspace_id":"w","title":"t","purpose":"p","temperature":0.1,"max_output_tokens":1,"context_snapshot_id":"c","model_deployment_id":"m","idempotency_key":"i","extra":true}"#).is_err(),
        serde_json::from_str::<GenerateDraftRequest>(r#"{"workspace_id":"w","request_id":"r","idempotency_key":"i","extra":true}"#).is_err(),
        serde_json::from_str::<AppendEditRequest>(r#"{"workspace_id":"w","draft_id":"d","previous_version_id":"v","sections":[],"idempotency_key":"i","extra":true}"#).is_err(),
        serde_json::from_str::<QueueSyncRequest>(r#"{"workspace_id":"w","draft_id":"d","output_version_id":"v","source_dependency_ids":[],"idempotency_key":"i","extra":true}"#).is_err(),
        serde_json::from_str::<ImportRawSourceRequest>(r#"{"workspace_id":"w","filename":"a.txt","content_type":"text/plain","content_base64":"YQ==","content_digest_sha256":"aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa","idempotency_key":"i","extra":true}"#).is_err(),
    ];
    assert!(cases.into_iter().all(|denied| denied));
}

#[test]
fn offline_studio_bridge_binds_every_command_to_native_session_workspace() {
    let bridge = include_str!("../src/offline_studio_bridge.rs");
    let manager = include_str!("../src/local_service.rs");
    assert!(bridge.contains("NativeSessionRuntime"));
    assert!(bridge.contains("execute_workspace_studio_request"));
    assert!(manager.contains("X-Daon-Workspace-Id"));
    assert!(manager.contains("X-Daon-Workspace-Proof"));
    assert!(manager.contains("hmac_sha256(&key, message.as_bytes())"));
    assert!(!bridge.contains("X-Daon-Workspace-Id"));
}
