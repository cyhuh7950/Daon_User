from __future__ import annotations

import base64
import hashlib
import hmac
import json
import os
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from daon_user_local_service.app import COMMAND_REGISTRY, create_app
from daon_user_local_service.local_storage import LocalEncryptedStore, LocalStorageError
from daon_user_local_service.security import issue_request_token


ROOT_SECRET = "cd" * 32
INSTANCE = "34" * 16
PORT = 48124
NOW = 2_000_000_000
WORKSPACE = "33333333-3333-4333-8333-333333333333"
OTHER_WORKSPACE = "44444444-4444-4444-8444-444444444444"


@pytest.mark.parametrize(("method", "path", "capability", "command", "limit"), [
    ("POST", "/local/v1/studio/knowledge-copies", "knowledge.write", "studio_knowledge_copy_import", 16 * 1024 * 1024),
    ("POST", "/local/v1/studio/knowledge-copies/{id}/refresh", "knowledge.write", "studio_knowledge_copy_refresh", 32 * 1024),
    ("GET", "/local/v1/studio/sync-operations/{id}", "sync.read", "studio_sync_state_read", 0),
    ("POST", "/local/v1/studio/sync-operations/{id}/states", "sync.write", "studio_sync_state_append", 64 * 1024),
])
def test_native_only_local_contract_is_exact(
    method: str, path: str, capability: str, command: str, limit: int
) -> None:
    contract = COMMAND_REGISTRY[path]
    assert (contract.method, contract.capability, contract.command) == (
        method, capability, command,
    )
    assert contract.max_body_bytes == limit


def _headers(
    command: str, capability: str, nonce: int, workspace_id: str = WORKSPACE
) -> dict[str, str]:
    token = issue_request_token(
        root_secret=ROOT_SECRET, app_instance_id=INSTANCE, capability=capability,
        command=command, issued_at=NOW, nonce=f"{nonce:064x}",
    )
    proof = hmac.new(
        bytes.fromhex(ROOT_SECRET), f"{token}|{workspace_id}".encode("ascii"), hashlib.sha256
    ).hexdigest()
    return {
        "authorization": f"Bearer {token}",
        "x-daon-workspace-id": workspace_id,
        "x-daon-workspace-proof": proof,
    }


def _canonical_package() -> bytes:
    return json.dumps(
        {"knowledge": [{"citation_id": "citation-1", "text": "grounded"}], "schema_version": 1},
        sort_keys=True, separators=(",", ":"), ensure_ascii=False,
    ).encode()


def _import_body(workspace_id: str = WORKSPACE) -> dict[str, object]:
    content = _canonical_package()
    manifest = {
        "workspace_id": workspace_id,
        "copy_id": "copy-1",
        "package_id": "package-1",
        "producer_product": "daon3",
        "producer_version": "3.0.0",
        "knowledge_registration_id": "registration-1",
        "output_version_id": "output-version-1",
        "authority": "approved",
        "registration_state": "registered",
        "review_state": "approved",
        "effective_at": "2026-08-13T03:33:20Z",
        "expires_at": "2033-05-18T03:33:20Z",
        "schema_version": 1,
        "content_digest_sha256": hashlib.sha256(content).hexdigest(),
    }
    manifest_digest = hashlib.sha256(json.dumps(
        manifest, sort_keys=True, separators=(",", ":"), ensure_ascii=False,
    ).encode()).hexdigest()
    return {
        **manifest,
        "manifest_digest_sha256": manifest_digest,
        "canonical_package_base64": base64.b64encode(content).decode(),
        "idempotency_key": "knowledge-copy-import-1",
    }


def test_knowledge_and_sync_commands_require_token_bound_workspace_proof(tmp_path: Path) -> None:
    store = LocalEncryptedStore.open(tmp_path, os.urandom(32))
    client = TestClient(create_app(
        root_secret=ROOT_SECRET, app_instance_id=INSTANCE, listener_port=PORT,
        storage=store, clock=lambda: NOW,
    ), base_url=f"http://127.0.0.1:{PORT}")

    missing = client.post(
        "/local/v1/studio/knowledge-copies",
        headers={"authorization": _headers(
            "studio_knowledge_copy_import", "knowledge.write", 70
        )["authorization"]},
        json=_import_body(),
    )
    forged = client.post(
        "/local/v1/studio/knowledge-copies",
        headers={**_headers("studio_knowledge_copy_import", "knowledge.write", 71),
                 "x-daon-workspace-proof": "0" * 64},
        json=_import_body(),
    )

    assert missing.status_code == forged.status_code == 403
    assert store.list_object_ids(WORKSPACE, "artifact") == []
    assert store.list_canonical_envelopes(WORKSPACE, "artifact") == ()
    store.close()

def test_import_refresh_and_sync_state_are_encrypted_append_only(tmp_path: Path) -> None:
    store = LocalEncryptedStore.open(tmp_path, os.urandom(32))
    client = TestClient(create_app(
        root_secret=ROOT_SECRET, app_instance_id=INSTANCE, listener_port=PORT,
        storage=store, clock=lambda: NOW,
    ), base_url=f"http://127.0.0.1:{PORT}")

    imported = client.post(
        "/local/v1/studio/knowledge-copies",
        headers=_headers("studio_knowledge_copy_import", "knowledge.write", 1),
        json=_import_body(),
    )
    assert imported.status_code == 200, imported.json()
    assert imported.json()["data"]["state"] == "approved"
    assert _canonical_package() not in b"".join(path.read_bytes() for path in tmp_path.rglob("*") if path.is_file())

    refreshed = client.post(
        "/local/v1/studio/knowledge-copies/copy-1/refresh",
        headers=_headers("studio_knowledge_copy_refresh", "knowledge.write", 2),
        json={"workspace_id": WORKSPACE, "state": "revoked", "recorded_at": "2033-05-18T03:33:21Z"},
    )
    assert refreshed.status_code == 200
    assert refreshed.json()["data"]["state"] == "revoked"
    assert len(store.list_canonical_envelopes(WORKSPACE, "artifact", "ScopeSnapshot")) == 2

    appended = client.post(
        "/local/v1/studio/sync-operations/sync-1/states",
        headers=_headers("studio_sync_state_append", "sync.write", 3),
        json={
            "workspace_id": WORKSPACE, "version": 1, "approval_state": "awaiting_approval",
            "manifest_digest": "a" * 64, "batch_cursor": None, "conflict_id": None,
            "queued_at": "2033-05-18T03:33:22Z", "previous_version": None,
        },
    )
    assert appended.status_code == 200
    read = client.get(
        "/local/v1/studio/sync-operations/sync-1",
        headers=_headers("studio_sync_state_read", "sync.read", 4),
        params=None,
    )
    assert read.status_code == 200
    assert read.json()["data"]["approval_state"] == "awaiting_approval"


@pytest.mark.parametrize("field", ("registration_state", "effective_at"))
def test_import_rejects_missing_knowledge_registration_lineage(
    tmp_path: Path, field: str
) -> None:
    store = LocalEncryptedStore.open(tmp_path, os.urandom(32))
    client = TestClient(create_app(
        root_secret=ROOT_SECRET, app_instance_id=INSTANCE, listener_port=PORT,
        storage=store, clock=lambda: NOW,
    ), base_url=f"http://127.0.0.1:{PORT}")
    body = _import_body()
    body.pop(field)
    denied = client.post(
        "/local/v1/studio/knowledge-copies",
        headers=_headers("studio_knowledge_copy_import", "knowledge.write", 80),
        json=body,
    )

    assert denied.status_code == 400
    assert denied.json()["error_code"] == "LOCAL_INPUT_INVALID"
    assert store.list_canonical_envelopes(WORKSPACE, "artifact") == ()
    store.close()


def test_digest_capability_query_replay_and_cross_workspace_are_write_zero(tmp_path: Path) -> None:
    store = LocalEncryptedStore.open(tmp_path, os.urandom(32))
    client = TestClient(create_app(
        root_secret=ROOT_SECRET, app_instance_id=INSTANCE, listener_port=PORT,
        storage=store, clock=lambda: NOW,
    ), base_url=f"http://127.0.0.1:{PORT}")
    invalid = _import_body()
    invalid["content_digest_sha256"] = "0" * 64
    response = client.post(
        "/local/v1/studio/knowledge-copies",
        headers=_headers("studio_knowledge_copy_import", "knowledge.write", 10), json=invalid,
    )
    assert response.status_code == 400
    assert store.list_object_ids(WORKSPACE, "artifact") == []
    assert store.list_canonical_envelopes(WORKSPACE, "artifact") == ()

    for nonce, headers, suffix in [
        (11, {**_headers("studio_knowledge_copy_import", "knowledge.write", 11), "origin": "null"}, ""),
        (12, {**_headers("studio_knowledge_copy_import", "knowledge.write", 12), "x-forwarded-for": "1"}, ""),
        (13, _headers("studio_knowledge_copy_import", "studio.write", 13), ""),
        (14, _headers("studio_knowledge_copy_import", "knowledge.write", 14), "?x=1"),
    ]:
        denied = client.post(f"/local/v1/studio/knowledge-copies{suffix}", headers=headers, json=_import_body())
        assert denied.status_code >= 400, nonce
    assert store.list_object_ids(WORKSPACE, "artifact") == []

    imported = client.post(
        "/local/v1/studio/knowledge-copies",
        headers=_headers("studio_knowledge_copy_import", "knowledge.write", 20), json=_import_body(),
    )
    assert imported.status_code == 200, imported.json()
    cross = client.post(
        "/local/v1/studio/knowledge-copies/copy-1/refresh",
        headers=_headers("studio_knowledge_copy_refresh", "knowledge.write", 21),
        json={"workspace_id": OTHER_WORKSPACE, "state": "revoked", "recorded_at": "2033-05-18T03:33:21Z"},
    )
    assert cross.status_code == 404
    assert len(store.list_canonical_envelopes(WORKSPACE, "artifact", "ScopeSnapshot")) == 1


def test_exact_replay_converges_but_changed_fingerprint_and_nonce_replay_write_zero(tmp_path: Path) -> None:
    store = LocalEncryptedStore.open(tmp_path, os.urandom(32))
    client = TestClient(create_app(
        root_secret=ROOT_SECRET, app_instance_id=INSTANCE, listener_port=PORT,
        storage=store, clock=lambda: NOW,
    ), base_url=f"http://127.0.0.1:{PORT}")
    body = _import_body()
    first = client.post(
        "/local/v1/studio/knowledge-copies",
        headers=_headers("studio_knowledge_copy_import", "knowledge.write", 30), json=body,
    )
    replay = client.post(
        "/local/v1/studio/knowledge-copies",
        headers=_headers("studio_knowledge_copy_import", "knowledge.write", 31), json=body,
    )
    assert first.status_code == replay.status_code == 200
    assert first.json()["data"]["object_id"] == replay.json()["data"]["object_id"]
    assert len(store.list_object_ids(WORKSPACE, "artifact")) == 1

    changed = dict(body)
    content = b'{"knowledge":[],"schema_version":1}'
    changed["canonical_package_base64"] = base64.b64encode(content).decode()
    changed["content_digest_sha256"] = hashlib.sha256(content).hexdigest()
    manifest = {key: value for key, value in changed.items() if key not in {
        "canonical_package_base64", "manifest_digest_sha256", "idempotency_key",
    }}
    changed["manifest_digest_sha256"] = hashlib.sha256(json.dumps(
        manifest, sort_keys=True, separators=(",", ":"), ensure_ascii=False,
    ).encode()).hexdigest()
    conflict = client.post(
        "/local/v1/studio/knowledge-copies",
        headers=_headers("studio_knowledge_copy_import", "knowledge.write", 32), json=changed,
    )
    assert conflict.status_code == 409
    assert len(store.list_object_ids(WORKSPACE, "artifact")) == 1

    replay_headers = _headers("studio_knowledge_copy_import", "knowledge.write", 33)
    denied_first = client.post("/local/v1/studio/knowledge-copies", headers=replay_headers, json=body)
    denied_second = client.post("/local/v1/studio/knowledge-copies", headers=replay_headers, json=body)
    assert denied_first.status_code == 200
    assert denied_second.status_code == 401
    assert len(store.list_object_ids(WORKSPACE, "artifact")) == 1


def test_malformed_canonical_oversize_and_cross_workspace_queue_write_zero(tmp_path: Path) -> None:
    store = LocalEncryptedStore.open(tmp_path, os.urandom(32))
    client = TestClient(create_app(
        root_secret=ROOT_SECRET, app_instance_id=INSTANCE, listener_port=PORT,
        storage=store, clock=lambda: NOW,
    ), base_url=f"http://127.0.0.1:{PORT}")
    malformed = _import_body()
    raw = b'{"schema_version":1, "knowledge":[]}'
    malformed["canonical_package_base64"] = base64.b64encode(raw).decode()
    malformed["content_digest_sha256"] = hashlib.sha256(raw).hexdigest()
    manifest = {key: value for key, value in malformed.items() if key not in {
        "canonical_package_base64", "manifest_digest_sha256", "idempotency_key",
    }}
    malformed["manifest_digest_sha256"] = hashlib.sha256(json.dumps(
        manifest, sort_keys=True, separators=(",", ":"), ensure_ascii=False,
    ).encode()).hexdigest()
    rejected = client.post(
        "/local/v1/studio/knowledge-copies",
        headers=_headers("studio_knowledge_copy_import", "knowledge.write", 40), json=malformed,
    )
    assert rejected.status_code == 400
    assert store.list_object_ids(WORKSPACE, "artifact") == []
    with pytest.raises(LocalStorageError, match="LOCAL_KNOWLEDGE_COPY_INVALID"):
        store.import_knowledge_copy(
            manifest=manifest, manifest_digest_sha256=str(malformed["manifest_digest_sha256"]),
            canonical_package=b"x" * (12 * 1024 * 1024 + 1), idempotency_key="oversize-copy-key-1",
        )
    assert store.list_object_ids(WORKSPACE, "artifact") == []

    state = {
        "workspace_id": WORKSPACE, "version": 1, "approval_state": "approved",
        "manifest_digest": "b" * 64, "batch_cursor": None, "conflict_id": None,
        "queued_at": "2033-05-18T03:33:22Z", "previous_version": None,
    }
    assert client.post(
        "/local/v1/studio/sync-operations/sync-cross/states",
        headers=_headers("studio_sync_state_append", "sync.write", 41), json=state,
    ).status_code == 200
    cross = {**state, "workspace_id": OTHER_WORKSPACE, "version": 2, "previous_version": 1}
    denied = client.post(
        "/local/v1/studio/sync-operations/sync-cross/states",
        headers=_headers("studio_sync_state_append", "sync.write", 42), json=cross,
    )
    assert denied.status_code == 404
    assert store.get_sync_queue_state(WORKSPACE, "sync-cross").version == 1
    with pytest.raises(LocalStorageError, match="LOCAL_SYNC_QUEUE_NOT_FOUND"):
        store.get_sync_queue_state(OTHER_WORKSPACE, "sync-cross")
