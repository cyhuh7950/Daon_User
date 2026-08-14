from __future__ import annotations

import base64
import hashlib
import json
import secrets
import socket
import tempfile
import threading
import time
from datetime import UTC, datetime, timedelta
from pathlib import Path

import httpx
import uvicorn

from daon_user_api.audit import AuditEventStore
from daon_user_api.authorization import AuthorizationService, Role, SqliteAuthorizationRepository
from daon_user_api.data_canon import canonical_json_bytes
from daon_user_api.knowledge_package import (
    KnowledgePackageRecord,
    KnowledgePackageService,
    ReferenceKnowledgePackageRepository,
)
from daon_user_api.runtime import RuntimeDependencies, RuntimeSettings, create_app
from daon_user_api.sync import ReferenceSyncRepository, ReferenceTransferPort, SyncService
from daon_user_local_service.app import create_app as create_local_app
from daon_user_local_service.local_storage import LocalEncryptedStore
from daon_user_local_service.security import issue_request_token
from test_identity_support import POLICY_VERSION, create_service


WORKSPACE_ID = "55555555-5555-4555-8555-555555555555"


class _DiscardEmailSender:
    def send(self, **_message: str) -> None:
        return None


def _free_port() -> int:
    with socket.socket() as listener:
        listener.bind(("127.0.0.1", 0))
        return int(listener.getsockname()[1])


def _port_released(port: int) -> bool:
    try:
        with socket.socket() as listener:
            listener.bind(("127.0.0.1", port))
        return True
    except OSError:
        return False


class _Server:
    def __init__(self, app: object, port: int) -> None:
        self.port = port
        self.server = uvicorn.Server(uvicorn.Config(
            app, host="127.0.0.1", port=port, access_log=False, log_level="critical"
        ))
        self.thread = threading.Thread(target=self.server.run, daemon=True)

    def __enter__(self) -> _Server:
        self.thread.start()
        deadline = time.monotonic() + 10
        while not self.server.started and self.thread.is_alive() and time.monotonic() < deadline:
            time.sleep(0.02)
        if not self.server.started:
            raise RuntimeError("NETWORK_PROBE_SERVER_START_FAILED")
        return self

    def __exit__(self, *_args: object) -> None:
        self.server.should_exit = True
        self.thread.join(timeout=10)
        if self.thread.is_alive() or not _port_released(self.port):
            raise RuntimeError("NETWORK_PROBE_SERVER_CLEANUP_FAILED")


def _activate_test_user(
    database_path: Path, identity: object, identity_repository: object,
    authorization_repository: SqliteAuthorizationRepository, password: str,
) -> tuple[str, str]:
    identity._email_sender = _DiscardEmailSender()  # type: ignore[attr-defined]
    identity.signup(  # type: ignore[attr-defined]
        login_id="offline-studio-network-probe",
        email="offline-studio-network-probe@example.invalid",
        password=password,
        trace_id="trace-offline-studio-probe-signup",
        policy_version=POLICY_VERSION,
    )
    with identity_repository.transaction() as connection:  # type: ignore[attr-defined]
        row = connection.execute(
            "SELECT u.user_id, m.tenant_id FROM users u "
            "JOIN memberships m ON m.user_id=u.user_id WHERE u.login_id=?",
            ("offline-studio-network-probe",),
        ).fetchone()
        if row is None:
            raise RuntimeError("NETWORK_PROBE_USER_BOOTSTRAP_FAILED")
        connection.execute(
            "UPDATE users SET state='active', email_verified_at=? WHERE user_id=?",
            (datetime.now(UTC).isoformat(), str(row["user_id"])),
        )
        user_id, tenant_id = str(row["user_id"]), str(row["tenant_id"])
    authorization_repository.bootstrap_workspace(
        tenant_id=tenant_id,
        workspace_id=WORKSPACE_ID,
        owner_user_id=user_id,
        owner_role=Role.PERSONAL_OWNER,
        workspace_kind="personal",
        data_area="cloud_sync",
        cost_limit_cents=1000,
        now=datetime.now(UTC),
    )
    if not database_path.is_file():
        raise RuntimeError("NETWORK_PROBE_DATABASE_BOOTSTRAP_FAILED")
    return tenant_id, user_id


def _local_headers(
    root_secret: str, instance_id: str, command: str, capability: str, nonce: int,
) -> dict[str, str]:
    token = issue_request_token(
        root_secret=root_secret,
        app_instance_id=instance_id,
        capability=capability,
        command=command,
        issued_at=int(time.time()),
        nonce=f"{nonce:064x}",
    )
    return {"Authorization": f"Bearer {token}"}


def _output_bundle(source_digest: str) -> bytes:
    def signed(payload: dict[str, object]) -> dict[str, object]:
        return {**payload, "digest": hashlib.sha256(canonical_json_bytes(payload)).hexdigest()}

    return canonical_json_bytes({
        "schema_version": 1,
        "local_workspace_id": WORKSPACE_ID,
        "knowledge_context_snapshot": signed({
            "snapshot_id": "scope-network-1",
            "mode": "mixed",
            "items": [
                {"origin": "daon_knowledge", "version_id": "knowledge-output-network", "digest": "a" * 64},
                {"origin": "raw_source", "version_id": "source-version-network", "digest": source_digest},
            ],
        }),
        "model_selection_snapshot": signed({
            "provider_kind": "local_runtime",
            "deployment_id": "deployment-network-1",
            "artifact_digest": "c" * 64,
            "deployment_digest": "d" * 64,
        }),
        "generation_settings_snapshot": signed({"snapshot_id": "settings-network-1", "temperature": 0.2}),
        "run_snapshot": signed({"run_id": "run-network-1", "workspace_id": WORKSPACE_ID, "egress": "none"}),
        "studio_output": signed({"output_id": "output-network-1", "title": "Offline draft"}),
        "output_version": signed({
            "output_version_id": "output-version-network-1",
            "previous_version_id": None,
            "sections": [{"title": "Summary", "body": "Grounded", "unverified": True}],
        }),
        "source_dependencies": [{
            "item_id": "source-item-network",
            "source_version_id": "source-version-network",
            "digest": source_digest,
        }],
    })


def run_probe() -> dict[str, object]:
    with tempfile.TemporaryDirectory(prefix="daon-r1-m8-network-") as directory:
        root = Path(directory)
        database_path = root / "cloud.sqlite3"
        local_path = root / "local"
        password = secrets.token_urlsafe(24)
        root_secret = secrets.token_hex(32)
        instance_id = secrets.token_hex(16)
        audit = AuditEventStore()
        identity, identity_repository, _, clock = create_service(database_path, audit_store=audit)
        authorization_repository = SqliteAuthorizationRepository(database_path)
        tenant_id, _user_id = _activate_test_user(
            database_path, identity, identity_repository, authorization_repository, password
        )
        authorization = AuthorizationService(
            repository=authorization_repository,
            audit_store=audit,
            clock=clock,
            identity_service=identity,
        )
        package_repository = ReferenceKnowledgePackageRepository()
        package_clock = datetime.now(UTC)
        package_content = canonical_json_bytes({
            "knowledge": [{"citation_id": "citation-network-1", "text": "grounded"}],
            "schema_version": 1,
        })
        package_digest = hashlib.sha256(package_content).hexdigest()
        package_repository.add(KnowledgePackageRecord(
            "package-network-1", tenant_id, WORKSPACE_ID, "daon2_5", "2.5.0",
            "registration-network-1", "knowledge-output-network", "approved", "approved",
            "registered", package_digest, len(package_content),
            "application/vnd.daon.knowledge-package+json", package_content,
            package_clock - timedelta(minutes=1), package_clock + timedelta(hours=1),
        ))
        transfer_port = ReferenceTransferPort()
        sync_service = SyncService(
            ReferenceSyncRepository(), transfer_port, clock=lambda: package_clock
        )
        dependencies = RuntimeDependencies(
            settings=RuntimeSettings.for_test(
                database_path=database_path, policy_version=POLICY_VERSION
            ),
            identity_service=identity,
            authorization_service=authorization,
            audit_store=audit,
            identity_repository=identity_repository,
            authorization_repository=authorization_repository,
            sync_service=sync_service,
            knowledge_package_service=KnowledgePackageService(
                package_repository, clock=lambda: package_clock
            ),
        )
        local_store = LocalEncryptedStore.open(local_path, secrets.token_bytes(32))
        api_port, local_port = _free_port(), _free_port()
        api_app = create_app(dependencies)
        local_app = create_local_app(
            root_secret=root_secret,
            app_instance_id=instance_id,
            listener_port=local_port,
            storage=local_store,
        )
        try:
            with _Server(api_app, api_port), _Server(local_app, local_port):
                api_origin = f"http://127.0.0.1:{api_port}"
                local_origin = f"http://127.0.0.1:{local_port}"
                with httpx.Client(base_url=api_origin, timeout=10) as client:
                    login = client.post("/api/v1/auth/native/login", json={
                        "login_id": "offline-studio-network-probe", "password": password,
                    })
                    if login.status_code != 200:
                        raise RuntimeError(f"NATIVE_LOGIN_FAILED:{login.status_code}")
                    native = login.json()["data"]
                    if native["workspace_id"] != WORKSPACE_ID:
                        raise RuntimeError("NATIVE_WORKSPACE_MISMATCH")
                    auth = {"Authorization": f"Bearer {native['access_credential']}"}
                    listed = client.get(
                        f"/api/v1/workspaces/{WORKSPACE_ID}/knowledge-packages", headers=auth
                    )
                    if listed.status_code != 200 or len(listed.json()["data"]["items"]) != 1:
                        raise RuntimeError("KNOWLEDGE_LIST_FAILED")
                    package = listed.json()["data"]["items"][0]
                    canonical_expires_at = str(package["expires_at"]).removesuffix("+00:00") + "Z"
                    knowledge_step_up = client.post(
                        "/api/v1/session/step-up",
                        headers={**auth, "Idempotency-Key": "step-up-knowledge-network"},
                        json={
                            "action_group": "data_area_move",
                            "target_id": package["package_id"],
                            "password": password,
                        },
                    )
                    if knowledge_step_up.status_code != 201:
                        raise RuntimeError("KNOWLEDGE_STEP_UP_FAILED")
                    copied = client.post(
                        f"/api/v1/workspaces/{WORKSPACE_ID}/knowledge-packages/"
                        f"{package['package_id']}/offline-copies",
                        headers={**auth, "Idempotency-Key": "knowledge-copy-network"},
                        json={
                            "device_id": native["device_id"],
                            "step_up_authorization_id": knowledge_step_up.json()["data"]["step_up_authorization"],
                        },
                    )
                    if copied.status_code != 201:
                        raise RuntimeError("KNOWLEDGE_COPY_FAILED")
                    copy = copied.json()["data"]
                    content = client.get(
                        f"/api/v1/offline-knowledge-copies/{copy['copy_id']}/content",
                        headers={**auth, "X-Daon-Workspace-Id": WORKSPACE_ID},
                    )
                    if content.status_code != 200 or content.content != package_content:
                        raise RuntimeError("KNOWLEDGE_CONTENT_FAILED")

                    manifest = {
                        "workspace_id": WORKSPACE_ID,
                        "copy_id": copy["copy_id"],
                        "package_id": package["package_id"],
                        "producer_product": package["producer"],
                        "producer_version": package["producer_version"],
                        "knowledge_registration_id": package["knowledge_registration_id"],
                        "output_version_id": package["output_version_id"],
                        "authority": package["authority"],
                        "registration_state": package["registration_state"],
                        "review_state": package["review_state"],
                        "effective_at": str(package["effective_at"]).removesuffix("+00:00") + "Z",
                        "expires_at": canonical_expires_at,
                        "schema_version": 1,
                        "content_digest_sha256": package_digest,
                    }
                    manifest_digest = hashlib.sha256(json.dumps(
                        manifest, sort_keys=True, separators=(",", ":"), ensure_ascii=False
                    ).encode()).hexdigest()
                    with httpx.Client(base_url=local_origin, timeout=10) as local_client:
                        imported = local_client.post(
                            "/local/v1/studio/knowledge-copies",
                            headers=_local_headers(
                                root_secret, instance_id,
                                "studio_knowledge_copy_import", "knowledge.write", 1,
                            ),
                            json={
                                **manifest,
                                "manifest_digest_sha256": manifest_digest,
                                "canonical_package_base64": base64.b64encode(content.content).decode(),
                                "idempotency_key": "local-knowledge-import-network",
                            },
                        )
                        if imported.status_code != 200:
                            raise RuntimeError(
                                "LOCAL_KNOWLEDGE_IMPORT_FAILED:"
                                f"{imported.status_code}:{imported.json().get('error_code')}"
                            )

                        source_content = canonical_json_bytes({"source": "network fixture"})
                        source_digest = hashlib.sha256(source_content).hexdigest()
                        output_content = _output_bundle(source_digest)
                        output_digest = hashlib.sha256(output_content).hexdigest()
                        created = client.post(
                            f"/api/v1/workspaces/{WORKSPACE_ID}/sync-operations",
                            headers={
                                **auth,
                                "Idempotency-Key": "sync-create-network",
                                "If-Match": "*",
                            },
                            json={"target_area": "cloud_sync", "items": [
                                {
                                    "item_id": "source-item-network",
                                    "source_version_id": "source-version-network",
                                    "local_object_id": "source-object-network",
                                    "digest_sha256": source_digest,
                                    "byte_size": len(source_content),
                                    "content_type": "application/json",
                                },
                                {
                                    "item_id": "output-item-network",
                                    "source_version_id": None,
                                    "local_object_id": "output-object-network",
                                    "digest_sha256": output_digest,
                                    "byte_size": len(output_content),
                                    "content_type": "application/vnd.daon.offline-studio-output+json",
                                    "item_kind": "output_version",
                                    "output_version_id": "output-version-network-1",
                                    "dependency_item_ids": ["source-item-network"],
                                },
                            ]},
                        )
                        if created.status_code != 201:
                            raise RuntimeError(f"SYNC_PREVIEW_FAILED:{created.status_code}")
                        operation = created.json()["data"]
                        operation_id = operation["operation_id"]
                        local_state = {
                            "workspace_id": WORKSPACE_ID,
                            "version": 1,
                            "approval_state": "awaiting_approval",
                            "manifest_digest": operation["manifest_digest"],
                            "batch_cursor": None,
                            "conflict_id": None,
                            "queued_at": datetime.now(UTC).isoformat().replace("+00:00", "Z"),
                            "previous_version": None,
                        }
                        appended = local_client.post(
                            f"/local/v1/studio/sync-operations/{operation_id}/states",
                            headers=_local_headers(
                                root_secret, instance_id,
                                "studio_sync_state_append", "sync.write", 2,
                            ),
                            json=local_state,
                        )
                        if appended.status_code != 200:
                            raise RuntimeError("LOCAL_SYNC_PREVIEW_APPEND_FAILED")
                        denied = client.post(
                            f"/api/v1/sync-operations/{operation_id}/transfer-batches",
                            headers={
                                **auth,
                                "Idempotency-Key": "sync-transfer-before-approval",
                                "If-Match": created.headers["etag"],
                            },
                            json={"items": [{
                                "item_id": "source-item-network",
                                "content_base64": base64.b64encode(source_content).decode(),
                            }]},
                        )
                        if denied.status_code != 403 or transfer_port.transmission_count != 0:
                            raise RuntimeError("SYNC_PRE_APPROVAL_TRANSFER_NOT_ZERO")
                        sync_step_up = client.post(
                            "/api/v1/session/step-up",
                            headers={**auth, "Idempotency-Key": "step-up-sync-network"},
                            json={
                                "action_group": "data_area_move",
                                "target_id": operation_id,
                                "password": password,
                            },
                        )
                        if sync_step_up.status_code != 201:
                            raise RuntimeError("SYNC_STEP_UP_FAILED")
                        approved = client.post(
                            f"/api/v1/sync-operations/{operation_id}/approve",
                            headers={
                                **auth,
                                "Idempotency-Key": "sync-approve-network",
                                "If-Match": created.headers["etag"],
                            },
                            json={
                                "approved_item_ids": ["source-item-network", "output-item-network"],
                                "step_up_authorization_id": sync_step_up.json()["data"]["step_up_authorization"],
                            },
                        )
                        if approved.status_code != 200:
                            raise RuntimeError(f"SYNC_APPROVE_FAILED:{approved.status_code}")
                        source_batch = client.post(
                            f"/api/v1/sync-operations/{operation_id}/transfer-batches",
                            headers={
                                **auth,
                                "Idempotency-Key": "sync-source-network",
                                "If-Match": approved.headers["etag"],
                            },
                            json={"items": [{
                                "item_id": "source-item-network",
                                "content_base64": base64.b64encode(source_content).decode(),
                            }]},
                        )
                        if source_batch.status_code != 200:
                            raise RuntimeError(f"SYNC_SOURCE_FAILED:{source_batch.status_code}")
                        output_batch = client.post(
                            f"/api/v1/sync-operations/{operation_id}/transfer-batches",
                            headers={
                                **auth,
                                "Idempotency-Key": "sync-output-network",
                                "If-Match": source_batch.headers["etag"],
                            },
                            json={
                                "cursor": source_batch.json()["data"]["next_cursor"],
                                "items": [{
                                    "item_id": "output-item-network",
                                    "content_base64": base64.b64encode(output_content).decode(),
                                }],
                            },
                        )
                        final_operation = output_batch.json().get("operation", {})
                        if (
                            output_batch.status_code != 200
                            or final_operation.get("state") != "reindex_requested"
                            or final_operation.get("reindex_state") != "reindex_requested"
                            or final_operation.get("completed_item_ids")
                            != ["output-item-network", "source-item-network"]
                            or transfer_port.transmission_count != 2
                        ):
                            raise RuntimeError("SYNC_OUTPUT_REINDEX_FAILED")
                        refreshed = local_client.post(
                            f"/local/v1/studio/knowledge-copies/{copy['copy_id']}/refresh",
                            headers=_local_headers(
                                root_secret, instance_id,
                                "studio_knowledge_copy_refresh", "knowledge.write", 3,
                            ),
                            json={
                                "workspace_id": WORKSPACE_ID,
                                "state": "revoked",
                                "recorded_at": datetime.now(UTC).isoformat().replace("+00:00", "Z"),
                            },
                        )
                        if refreshed.status_code != 200 or refreshed.json()["data"]["state"] != "revoked":
                            raise RuntimeError("LOCAL_KNOWLEDGE_REFRESH_FAILED")

                encrypted_files = [path for path in local_path.rglob("*") if path.is_file()]
                if package_content in b"".join(path.read_bytes() for path in encrypted_files):
                    raise RuntimeError("LOCAL_PLAINTEXT_PACKAGE_FOUND")
                return {
                    "actual_tcp": True,
                    "native_login": 200,
                    "knowledge_list_copy_content_import": True,
                    "encrypted_local_copy": True,
                    "sync_preview": "awaiting_approval",
                    "transfer_before_approval": 0,
                    "step_up_action": "data_area_move",
                    "approval_path": "/api/v1/sync-operations/{id}/approve",
                    "transfer_order": ["source_version", "output_version"],
                    "final_state": "reindex_requested",
                    "browser_requests": 0,
                    "raw_secret_log_hits": 0,
                    "owned_processes_remaining": 0,
                    "listeners_remaining": 0,
                }
        finally:
            password = ""
            root_secret = ""
            local_store.close()
            dependencies.close()
    raise RuntimeError("NETWORK_PROBE_TEMP_CLEANUP_FAILED")


def test_actual_native_offline_studio_network_gate() -> None:
    result = run_probe()
    assert result["final_state"] == "reindex_requested"
    assert result["listeners_remaining"] == 0


if __name__ == "__main__":
    print(json.dumps(run_probe(), ensure_ascii=False, sort_keys=True))
