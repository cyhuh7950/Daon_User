use daon_user_desktop_lib::offline_sync_bridge::{
    canonical_utc_timestamp, knowledge_path, sync_path, topological_batches, CopyState,
    OfflineSyncError, ReconnectGate, SyncItem, SyncItemKind, SyncState,
};

#[test]
fn cloud_utc_timestamp_is_validated_and_canonicalized_for_local_manifest() {
    assert_eq!(
        canonical_utc_timestamp("2026-08-14T06:00:00+00:00").unwrap(),
        "2026-08-14T06:00:00Z"
    );
    assert_eq!(
        canonical_utc_timestamp("2026-08-14T06:00:00.123Z").unwrap(),
        "2026-08-14T06:00:00.123Z"
    );
    assert!(canonical_utc_timestamp("2026-08-14T15:00:00+09:00").is_err());
    assert!(canonical_utc_timestamp("not-a-timestamp").is_err());
}

#[test]
fn cloud_paths_are_exact_and_do_not_add_the_plan_typo_alias() {
    assert_eq!(
        knowledge_path("list", "workspace-1", None).unwrap(),
        "/api/v1/workspaces/workspace-1/knowledge-packages"
    );
    assert_eq!(
        knowledge_path("provision", "workspace-1", Some("package-1")).unwrap(),
        "/api/v1/workspaces/workspace-1/knowledge-packages/package-1/offline-copies"
    );
    assert_eq!(
        sync_path("preview", "workspace-1", None, None).unwrap(),
        "/api/v1/workspaces/workspace-1/sync-operations"
    );
    assert_eq!(
        sync_path("status", "operation-1", None, None).unwrap(),
        "/api/v1/sync-operations/operation-1"
    );
    assert_eq!(
        sync_path("approve", "operation-1", None, None).unwrap(),
        "/api/v1/sync-operations/operation-1/approve"
    );
    assert_eq!(
        sync_path("transfer", "operation-1", None, None).unwrap(),
        "/api/v1/sync-operations/operation-1/transfer-batches"
    );
    assert_eq!(
        sync_path("resolve", "operation-1", Some("conflict-1"), None).unwrap(),
        "/api/v1/sync-operations/operation-1/conflicts/conflict-1/resolution"
    );
    assert!(sync_path("approval", "operation-1", None, None).is_err());
}

#[test]
fn reconnect_only_exposes_preview_and_never_transfers_without_explicit_resume() {
    let mut gate = ReconnectGate::restore("operation-1", 4).unwrap();
    let projection = gate.on_connectivity_changed(true);
    assert_eq!(projection.state, SyncState::AwaitingApproval);
    assert_eq!(projection.awaiting_approval, 4);
    assert_eq!(gate.transfer_calls(), 0);
    assert_eq!(
        gate.resume_transfer(false).unwrap_err(),
        OfflineSyncError::ApprovalRequired
    );
    assert_eq!(gate.transfer_calls(), 0);
}

#[test]
fn expired_or_revoked_approval_and_changed_scope_fail_closed() {
    let mut gate = ReconnectGate::restore("operation-1", 2).unwrap();
    assert_eq!(
        gate.approve("scope-v1", 9, 10).unwrap_err(),
        OfflineSyncError::StepUpExpired
    );
    gate.revoke_step_up();
    assert_eq!(
        gate.approve("scope-v1", 12, 10).unwrap_err(),
        OfflineSyncError::StepUpRevoked
    );
    gate.reset_step_up();
    gate.approve("scope-v1", 12, 10).unwrap();
    assert_eq!(
        gate.confirm_scope("scope-v2").unwrap_err(),
        OfflineSyncError::ScopeChanged
    );
    assert_eq!(
        gate.resume_transfer(true).unwrap_err(),
        OfflineSyncError::ScopeChanged
    );
    assert_eq!(gate.transfer_calls(), 0);
}

#[test]
fn source_items_are_batched_before_outputs_and_missing_dependencies_are_rejected() {
    let source = SyncItem::new("source-1", SyncItemKind::Source, vec![]).unwrap();
    let output = SyncItem::new("output-1", SyncItemKind::Output, vec!["source-1".into()]).unwrap();
    let batches = topological_batches(&[output.clone(), source]).unwrap();
    assert_eq!(batches[0][0].item_id, "source-1");
    assert_eq!(batches[1][0].item_id, "output-1");
    let missing = SyncItem::new("output-2", SyncItemKind::Output, vec!["missing".into()]).unwrap();
    assert_eq!(
        topological_batches(&[missing]).unwrap_err(),
        OfflineSyncError::MissingDependency
    );
}

#[test]
fn conflict_is_never_automatically_resolved_and_copy_states_are_closed() {
    let mut gate = ReconnectGate::restore("operation-1", 1).unwrap();
    gate.mark_conflict("conflict-1").unwrap();
    assert_eq!(gate.projection().state, SyncState::Conflict);
    assert_eq!(
        gate.resume_transfer(true).unwrap_err(),
        OfflineSyncError::ConflictRequiresResolution
    );
    assert_eq!(gate.transfer_calls(), 0);
    assert!(matches!(
        CopyState::parse("approved"),
        Ok(CopyState::Approved)
    ));
    assert!(CopyState::parse("unknown").is_err());
}

#[test]
fn knowledge_and_sync_local_calls_are_workspace_bound() {
    let source = std::fs::read_to_string("src/offline_sync_bridge.rs").unwrap();
    assert!(source.contains("execute_workspace_studio_request"));
    assert!(!source.contains(".execute_studio_request(capability, command"));
    assert!(source.contains("offline_knowledge_refresh"));
    assert!(source.contains("current_workspace"));
}