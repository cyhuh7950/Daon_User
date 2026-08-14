from __future__ import annotations

import hashlib
import hmac
from base64 import b64encode
from typing import Any, cast

import pytest
from fastapi.testclient import TestClient

from daon_user_local_service.app import COMMAND_REGISTRY, create_app
from daon_user_local_service.security import issue_request_token
from daon_user_local_service.provider_draft import ProviderModelDescriptor
from daon_user_local_service.local_storage import LocalEncryptedStore
from daon_user_local_service.raw_source import RawSourceService


ROOT_SECRET = "ab" * 32
INSTANCE = "12" * 16
PORT = 48123
NOW = 2_000_000_000


@pytest.mark.parametrize(("method", "path", "command"), [
    ("GET", "/local/v1/studio/models", "studio_models_list"),
    ("POST", "/local/v1/studio/knowledge-contexts", "studio_context_prepare"),
    ("POST", "/local/v1/studio/settings/confirm", "studio_settings_confirm"),
    ("POST", "/local/v1/studio/drafts/generate", "studio_draft_generate"),
    ("GET", "/local/v1/studio/drafts/{id}", "studio_draft_get"),
    ("POST", "/local/v1/studio/drafts/{id}/versions", "studio_draft_append_version"),
    ("POST", "/local/v1/studio/drafts/{id}/sync-queue", "studio_sync_queue"),
])
def test_command_registry_is_exact(method: str, path: str, command: str) -> None:
    contract = COMMAND_REGISTRY[path]
    assert (contract.method, contract.command) == (method, command)
    assert contract.max_body_bytes <= 8 * 1024 * 1024


def test_raw_source_command_registry_is_exact_and_bounded() -> None:
    read = COMMAND_REGISTRY["/local/v1/studio/raw-sources:GET"]
    write = COMMAND_REGISTRY["/local/v1/studio/raw-sources:POST"]
    assert (read.method, read.capability, read.command) == (
        "GET", "studio.read", "studio_raw_sources_list",
    )
    assert (write.method, write.capability, write.command) == (
        "POST", "studio.write", "studio_raw_source_import",
    )
    assert 34 * 1024 * 1024 < write.max_body_bytes <= 36 * 1024 * 1024


def _headers(command: str, capability: str, nonce: int) -> dict[str, str]:
    token = issue_request_token(
        root_secret=ROOT_SECRET, app_instance_id=INSTANCE, capability=capability,
        command=command, issued_at=NOW, nonce=f"{nonce:064x}",
    )
    return {"authorization": f"Bearer {token}"}


def _workspace_headers(
    command: str, capability: str, nonce: int, workspace_id: str
) -> dict[str, str]:
    headers = _headers(command, capability, nonce)
    token = headers["authorization"].removeprefix("Bearer ")
    proof = hmac.new(
        bytes.fromhex(ROOT_SECRET), f"{token}|{workspace_id}".encode("ascii"), hashlib.sha256
    ).hexdigest()
    return headers | {
        "x-daon-workspace-id": workspace_id,
        "x-daon-workspace-proof": proof,
    }


def test_studio_workspace_header_requires_token_bound_proof() -> None:
    app = create_app(
        root_secret=ROOT_SECRET, app_instance_id=INSTANCE, listener_port=PORT,
        clock=lambda: NOW,
    )
    test_client = TestClient(app, base_url=f"http://127.0.0.1:{PORT}")
    missing = test_client.get(
        "/local/v1/studio/models",
        headers=_headers("studio_models_list", "studio.read", 90),
    )
    forged = test_client.get(
        "/local/v1/studio/models",
        headers=_headers("studio_models_list", "studio.read", 91) | {
            "x-daon-workspace-id": "33333333-3333-4333-8333-333333333333",
            "x-daon-workspace-proof": "0" * 64,
        },
    )
    bound = test_client.get(
        "/local/v1/studio/models",
        headers=_workspace_headers(
            "studio_models_list", "studio.read", 92,
            "33333333-3333-4333-8333-333333333333",
        ),
    )
    assert (missing.status_code, forged.status_code) == (403, 403)
    assert bound.status_code == 503
    assert bound.json()["error_code"] == "LOCAL_STUDIO_UNAVAILABLE"


def test_token_bound_workspace_mismatch_is_domain_write_zero() -> None:
    class CountingStudio:
        calls = 0

        def prepare_context(self, **_kwargs: object) -> object:
            self.calls += 1
            raise AssertionError("cross-workspace request reached domain")

    studio = CountingStudio()
    header_workspace = "33333333-3333-4333-8333-333333333333"
    body_workspace = "44444444-4444-4444-8444-444444444444"
    client = TestClient(create_app(
        root_secret=ROOT_SECRET, app_instance_id=INSTANCE, listener_port=PORT,
        offline_studio=cast(Any, studio), clock=lambda: NOW,
    ), base_url=f"http://127.0.0.1:{PORT}")
    response = client.post(
        "/local/v1/studio/knowledge-contexts",
        headers=_workspace_headers(
            "studio_context_prepare", "studio.write", 93, header_workspace,
        ),
        json={
            "workspace_id": body_workspace, "mode": "raw_only",
            "daon_knowledge_ids": [], "raw_source_version_ids": ["source-v1"],
            "idempotency_key": "context-cross-workspace",
        },
    )
    assert response.status_code == 403
    assert response.json()["error_code"] == "LOCAL_WORKSPACE_REQUIRED"
    assert studio.calls == 0


def test_unconfigured_studio_and_extra_keys_fail_closed() -> None:
    app = create_app(
        root_secret=ROOT_SECRET, app_instance_id=INSTANCE, listener_port=PORT,
        clock=lambda: NOW,
    )
    client = TestClient(app, base_url=f"http://127.0.0.1:{PORT}")
    unavailable = client.get(
        "/local/v1/studio/models",
        headers=_workspace_headers(
            "studio_models_list", "studio.read", 1,
            "33333333-3333-4333-8333-333333333333",
        ),
    )
    assert unavailable.status_code == 503
    assert unavailable.json()["error_code"] == "LOCAL_STUDIO_UNAVAILABLE"
    invalid = client.post(
        "/local/v1/studio/knowledge-contexts",
        headers=_workspace_headers(
            "studio_context_prepare", "studio.write", 2,
            "33333333-3333-4333-8333-333333333333",
        ),
        json={
            "workspace_id": "33333333-3333-4333-8333-333333333333",
            "mode": "raw_only", "daon_knowledge_ids": [],
            "raw_source_version_ids": ["source-v1"], "idempotency_key": "context-1",
            "unexpected": True,
        },
    )
    assert invalid.status_code == 400
    assert invalid.json()["error_code"] == "LOCAL_INPUT_INVALID"


def test_models_projection_exposes_only_common_provider_identity() -> None:
    class Studio:
        def list_models(self, **_kwargs: object) -> tuple[ProviderModelDescriptor, ...]:
            return (ProviderModelDescriptor(
                "OLLAMA", "server_internal", "provider-ollama", "deployment-qwen",
                "qwen3:8b", "sha256:" + "a" * 64, "b" * 64, 3,
            ),)

    client = TestClient(create_app(
        root_secret=ROOT_SECRET, app_instance_id=INSTANCE, listener_port=PORT,
        offline_studio=cast(Any, Studio()), clock=lambda: NOW,
    ), base_url=f"http://127.0.0.1:{PORT}")

    response = client.get(
        "/local/v1/studio/models",
        headers=_workspace_headers(
            "studio_models_list", "studio.read", 20,
            "33333333-3333-4333-8333-333333333333",
        ),
    )

    assert response.status_code == 200
    assert response.json()["data"] == [{
        "deployment_id": "deployment-qwen",
        "provider_code": "OLLAMA",
        "provider_kind": "server_internal",
        "label": "qwen3:8b",
        "version": "sha256:" + "a" * 64,
        "readiness": "ready",
    }]


def test_browser_proxy_query_wrong_capability_and_replay_are_write_zero() -> None:
    class CountingStudio:
        calls = 0

        def prepare_context(self, **_kwargs: object) -> object:
            self.calls += 1
            raise AssertionError("must not reach domain")

    studio = CountingStudio()
    client = TestClient(create_app(
        root_secret=ROOT_SECRET, app_instance_id=INSTANCE, listener_port=PORT,
        offline_studio=cast(Any, studio), clock=lambda: NOW,
    ), base_url=f"http://127.0.0.1:{PORT}")
    base = {
        "workspace_id": "33333333-3333-4333-8333-333333333333", "mode": "raw_only",
        "daon_knowledge_ids": [], "raw_source_version_ids": ["source-v1"],
        "idempotency_key": "context-1",
    }
    cases = [
        ({**_headers("studio_context_prepare", "studio.write", 3), "origin": "null"}, ""),
        ({**_headers("studio_context_prepare", "studio.write", 4), "x-forwarded-for": "1"}, ""),
        (_headers("studio_context_prepare", "studio.read", 5), ""),
        (_headers("studio_context_prepare", "studio.write", 6), "?extra=1"),
    ]
    for headers, suffix in cases:
        response = client.post(
            f"/local/v1/studio/knowledge-contexts{suffix}", headers=headers, json=base
        )
        assert response.status_code >= 400
    assert studio.calls == 0


def test_raw_source_import_and_list_are_workspace_bound(tmp_path) -> None:
    workspace_id = "33333333-3333-4333-8333-333333333333"
    other_workspace = "44444444-4444-4444-8444-444444444444"
    store = LocalEncryptedStore.open(tmp_path / "raw-http", bytes(range(32)))
    client = TestClient(create_app(
        root_secret=ROOT_SECRET,
        app_instance_id=INSTANCE,
        listener_port=PORT,
        raw_source_service=RawSourceService(store),
        clock=lambda: NOW,
    ), base_url=f"http://127.0.0.1:{PORT}")
    content = "로컬 원문 근거".encode()
    body = {
        "workspace_id": workspace_id,
        "filename": "local.txt",
        "content_type": "text/plain",
        "content_base64": b64encode(content).decode("ascii"),
        "content_digest_sha256": hashlib.sha256(content).hexdigest(),
        "idempotency_key": "raw-http-import-0001",
    }

    missing = client.post(
        "/local/v1/studio/raw-sources",
        headers=_headers("studio_raw_source_import", "studio.write", 100),
        json=body,
    )
    mismatch = client.post(
        "/local/v1/studio/raw-sources",
        headers=_workspace_headers(
            "studio_raw_source_import", "studio.write", 101, other_workspace,
        ),
        json=body,
    )
    created = client.post(
        "/local/v1/studio/raw-sources",
        headers=_workspace_headers(
            "studio_raw_source_import", "studio.write", 102, workspace_id,
        ),
        json=body,
    )
    listed = client.get(
        "/local/v1/studio/raw-sources",
        headers=_workspace_headers(
            "studio_raw_sources_list", "studio.read", 103, workspace_id,
        ),
    )
    other = client.get(
        "/local/v1/studio/raw-sources",
        headers=_workspace_headers(
            "studio_raw_sources_list", "studio.read", 104, other_workspace,
        ),
    )

    assert (missing.status_code, mismatch.status_code) == (403, 403)
    assert created.status_code == 200
    assert created.json()["data"]["filename"] == "local.txt"
    assert listed.status_code == 200
    assert listed.json()["data"] == [created.json()["data"]]
    assert other.status_code == 200
    assert other.json()["data"] == []
    assert len(store.list_object_ids(workspace_id, "source")) == 1
    assert store.list_object_ids(other_workspace, "source") == []
    store.close()
