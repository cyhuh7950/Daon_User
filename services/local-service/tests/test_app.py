from __future__ import annotations

from collections.abc import Callable
from base64 import b64decode, b64encode
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
import re
from typing import cast

import pytest
from fastapi.testclient import TestClient

from daon_user_local_service.app import create_app
from daon_user_local_service.local_storage import LocalEncryptedStore
from daon_user_local_service.security import issue_request_token


ROOT_SECRET = "ab" * 32
INSTANCE = "12" * 16
PORT = 48123
NOW = 2_000_000_000
EXPECTED_MAX_HEADER_BYTES = 8192
OPAQUE_TRACE = re.compile(r"^[0-9a-f]{16}$")


def client(
    clock: Callable[[], int] = lambda: NOW,
    storage: LocalEncryptedStore | None = None,
) -> TestClient:
    return TestClient(
        create_app(
            root_secret=ROOT_SECRET,
            app_instance_id=INSTANCE,
            listener_port=PORT,
            storage=storage,
            clock=clock,
        ),
        base_url=f"http://127.0.0.1:{PORT}",
    )


def token(
    command: str,
    *,
    nonce: str,
    capability: str = "runtime.read",
    issued_at: int = NOW,
    ttl_seconds: int = 60,
) -> str:
    return issue_request_token(
        root_secret=ROOT_SECRET,
        app_instance_id=INSTANCE,
        capability=capability,
        command=command,
        issued_at=issued_at,
        ttl_seconds=ttl_seconds,
        nonce=nonce,
    )


def auth_headers(
    command: str,
    *,
    nonce: str = "34" * 32,
    capability: str = "runtime.read",
) -> dict[str, str]:
    return {
        "authorization": f"Bearer {token(command, nonce=nonce, capability=capability)}"
    }


def assert_safe_error(response: object, expected_code: str) -> None:
    payload = response.json()  # type: ignore[attr-defined]
    assert payload["error_code"] == expected_code
    assert OPAQUE_TRACE.fullmatch(payload["trace_id"])
    serialized = response.text  # type: ignore[attr-defined]
    assert ROOT_SECRET not in serialized
    assert INSTANCE not in serialized
    assert str(PORT) not in serialized


def test_fixed_read_only_commands_require_command_bound_single_use_tokens() -> None:
    test_client = client()
    status_headers = auth_headers("runtime.status.read")
    response = test_client.get("/v1/status", headers=status_headers)
    assert response.status_code == 200
    assert response.json() == {"status": "ready", "protocol_version": "1.0"}
    assert test_client.get("/v1/status", headers=status_headers).status_code == 401

    capabilities = test_client.get(
        "/v1/capabilities",
        headers=auth_headers("runtime.capabilities.read", nonce="56" * 32),
    )
    assert capabilities.status_code == 200
    assert capabilities.json() == {
        "catalog_version": "1.0",
        "capabilities": [
            {
                "capability": "runtime.read",
                "commands": ["runtime.capabilities.read", "runtime.status.read"],
            },
            {
                "capability": "storage.read",
                "commands": [
                    "storage.file.get",
                    "storage.status.read",
                    "storage.vector.search",
                ],
            },
            {
                "capability": "storage.write",
                "commands": ["storage.file.put", "storage.lock", "storage.vector.put"],
            },
        ],
    }


def test_authenticated_storage_file_vector_restart_and_lock_contract(tmp_path: Path) -> None:
    storage = LocalEncryptedStore.open(tmp_path, bytes.fromhex("cd" * 32))
    test_client = client(storage=storage)
    workspace = "11111111-1111-4111-8111-111111111111"
    payload = b"LOCAL-API-ENCRYPTED-CANARY"

    put = test_client.post(
        "/v1/storage/file/put",
        headers=auth_headers(
            "storage.file.put", capability="storage.write", nonce="10" * 32
        ),
        json={
            "workspace_id": workspace,
            "area": "source",
            "content_base64": b64encode(payload).decode("ascii"),
        },
    )
    assert put.status_code == 200
    object_id = put.json()["object_id"]

    get = test_client.post(
        "/v1/storage/file/get",
        headers=auth_headers("storage.file.get", capability="storage.read", nonce="11" * 32),
        json={"workspace_id": workspace, "area": "source", "object_id": object_id},
    )
    assert get.status_code == 200
    assert b64decode(get.json()["content_base64"]) == payload

    vector = {
        "workspace_id": workspace,
        "area": "source",
        "item_id": "chunk-a",
        "embedding": [1.0, 0.0, 0.0],
        "model_digest": "a" * 64,
        "artifact_digest": "b" * 64,
        "embedding_version": "embedding-v1",
        "source_version": "source-v1",
        "object_version": "object-v1",
    }
    assert test_client.post(
        "/v1/storage/vector/put",
        headers=auth_headers(
            "storage.vector.put", capability="storage.write", nonce="12" * 32
        ),
        json=vector,
    ).status_code == 200
    search = test_client.post(
        "/v1/storage/vector/search",
        headers=auth_headers(
            "storage.vector.search", capability="storage.read", nonce="13" * 32
        ),
        json={
            "workspace_id": workspace,
            "area": "source",
            "embedding": [1.0, 0.0, 0.0],
            "limit": 1,
        },
    )
    assert search.json() == {"item_ids": ["chunk-a"]}

    locked = test_client.post(
        "/v1/storage/lock",
        headers=auth_headers("storage.lock", capability="storage.write", nonce="14" * 32),
    )
    assert locked.json() == {"state": "locked"}
    status = test_client.get(
        "/v1/storage/status",
        headers=auth_headers(
            "storage.status.read", capability="storage.read", nonce="15" * 32
        ),
    )
    assert status.json() == {"state": "locked"}
    denied = test_client.post(
        "/v1/storage/file/get",
        headers=auth_headers("storage.file.get", capability="storage.read", nonce="16" * 32),
        json={"workspace_id": workspace, "area": "source", "object_id": object_id},
    )
    assert denied.status_code == 423
    assert denied.json()["error_code"] == "LOCAL_KEY_UNAVAILABLE"


def test_parallel_storage_requests_and_lock_race_fail_closed(tmp_path: Path) -> None:
    storage = LocalEncryptedStore.open(tmp_path, bytes.fromhex("ef" * 32))
    test_client = client(storage=storage)
    workspace = "11111111-1111-4111-8111-111111111111"
    put = test_client.post(
        "/v1/storage/file/put",
        headers=auth_headers(
            "storage.file.put", capability="storage.write", nonce="20" * 32
        ),
        json={
            "workspace_id": workspace,
            "area": "source",
            "content_base64": b64encode(b"parallel-lock-canary").decode("ascii"),
        },
    )
    object_id = put.json()["object_id"]
    vector = {
        "workspace_id": workspace,
        "area": "source",
        "item_id": "parallel-seed",
        "embedding": [1.0, 0.0, 0.0],
        "model_digest": "a" * 64,
        "artifact_digest": "b" * 64,
        "embedding_version": "embedding-v1",
        "source_version": "source-v1",
        "object_version": "object-v1",
    }
    assert test_client.post(
        "/v1/storage/vector/put",
        headers=auth_headers(
            "storage.vector.put", capability="storage.write", nonce="21" * 32
        ),
        json=vector,
    ).status_code == 200

    requests = [
        (
            "/v1/storage/file/get",
            "storage.file.get",
            "storage.read",
            {"workspace_id": workspace, "area": "source", "object_id": object_id},
        ),
        (
            "/v1/storage/vector/search",
            "storage.vector.search",
            "storage.read",
            {
                "workspace_id": workspace,
                "area": "source",
                "embedding": [1.0, 0.0, 0.0],
                "limit": 1,
            },
        ),
        (
            "/v1/storage/file/put",
            "storage.file.put",
            "storage.write",
            {
                "workspace_id": workspace,
                "area": "source",
                "content_base64": b64encode(b"race-write").decode("ascii"),
            },
        ),
    ]

    def request(index: int) -> int:
        path, command, capability, body = requests[index]
        return cast(int, test_client.post(
            path,
            headers=auth_headers(
                command, capability=capability, nonce=f"{80 + index:02x}" * 32
            ),
            json=body,
        ).status_code)

    def lock() -> int:
        return cast(int, test_client.post(
            "/v1/storage/lock",
            headers=auth_headers(
                "storage.lock", capability="storage.write", nonce="60" * 32
            ),
        ).status_code)

    with ThreadPoolExecutor(max_workers=4) as pool:
        futures = [pool.submit(request, index) for index in range(len(requests))]
        lock_future = pool.submit(lock)
        statuses = [future.result() for future in futures]
        lock_status = lock_future.result()

    assert lock_status == 200
    assert all(status in {200, 423} for status in statuses)
    for index, (path, command, capability, body) in enumerate(requests):
        after_lock = test_client.post(
            path,
            headers=auth_headers(
                command, capability=capability, nonce=f"{112 + index:02x}" * 32
            ),
            json=body,
        )
        assert after_lock.status_code == 423
        assert after_lock.json()["error_code"] == "LOCAL_KEY_UNAVAILABLE"


@pytest.mark.parametrize(
    "headers",
    [
        {"origin": "https://example.test"},
        {"sec-fetch-site": "same-origin"},
        {"sec-fetch-mode": "cors"},
        {"forwarded": "host=127.0.0.1"},
        {"x-forwarded-host": f"127.0.0.1:{PORT}"},
        {"x-forwarded-for": "127.0.0.1"},
        {"x-forwarded-proto": "http"},
    ],
)
def test_browser_and_proxy_headers_are_rejected_before_dispatch(headers: dict[str, str]) -> None:
    response = client().get(
        "/v1/status",
        headers=auth_headers("runtime.status.read") | headers,
    )
    assert response.status_code in {400, 403}
    assert response.json()["error_code"] in {
        "BROWSER_REQUEST_NOT_ALLOWED",
        "PROXY_HEADERS_NOT_ALLOWED",
    }
    assert OPAQUE_TRACE.fullmatch(response.json()["trace_id"])
    assert "access-control-allow-origin" not in response.headers


@pytest.mark.parametrize(
    "host",
    ["127.0.0.1", "localhost:48123", "127.0.0.1:48124", "192.168.1.10:48123"],
)
def test_host_must_match_exact_bound_loopback_port(host: str) -> None:
    response = client().get(
        "/v1/status",
        headers=auth_headers("runtime.status.read") | {"host": host},
    )
    assert response.status_code == 400
    assert_safe_error(response, "INVALID_LOOPBACK_HOST")


def test_wrong_command_token_unknown_path_query_encoding_and_method_fail_closed() -> None:
    test_client = client()
    unauthenticated_unknown = test_client.get("/v1/unknown")
    assert unauthenticated_unknown.status_code == 401
    assert_safe_error(unauthenticated_unknown, "LOCAL_AUTH_REQUIRED")
    assert (
        test_client.get(
            "/v1/status",
            headers=auth_headers("runtime.capabilities.read", nonce="01" * 32),
        ).status_code
        == 401
    )
    assert (
        test_client.get(
            "/v1/unknown",
            headers=auth_headers("runtime.status.read", nonce="02" * 32),
        ).status_code
        == 404
    )
    assert (
        test_client.get(
            "/v1/status?command=runtime.status.read",
            headers=auth_headers("runtime.status.read", nonce="03" * 32),
        ).status_code
        == 400
    )
    assert (
        test_client.get(
            "/v1/%73tatus",
            headers=auth_headers("runtime.status.read", nonce="04" * 32),
        ).status_code
        == 400
    )
    assert (
        test_client.post(
            "/v1/status",
            headers=auth_headers("runtime.status.read", nonce="05" * 32),
        ).status_code
        == 405
    )


@pytest.mark.parametrize(
    ("headers", "expected_status", "expected_code"),
    [
        ({"content-length": "1"}, 413, "REQUEST_BODY_NOT_ALLOWED"),
        ({"content-length": "-1"}, 400, "INVALID_CONTENT_LENGTH"),
        ({"content-length": "not-a-number"}, 400, "INVALID_CONTENT_LENGTH"),
        ({"transfer-encoding": "chunked"}, 400, "TRANSFER_ENCODING_NOT_ALLOWED"),
    ],
)
def test_body_framing_is_rejected_before_dispatch(
    headers: dict[str, str],
    expected_status: int,
    expected_code: str,
) -> None:
    response = client().get(
        "/v1/status",
        headers=auth_headers("runtime.status.read") | headers,
    )
    assert response.status_code == expected_status
    assert_safe_error(response, expected_code)


def test_oversized_headers_and_auth_failures_are_safe() -> None:
    test_client = client()
    oversized = test_client.get(
        "/v1/status",
        headers=auth_headers("runtime.status.read") | {"x-oversized": "x" * EXPECTED_MAX_HEADER_BYTES},
    )
    assert oversized.status_code == 431
    assert_safe_error(oversized, "REQUEST_HEADERS_TOO_LARGE")

    for authorization in ("", "Bearer invalid", f"Bearer {token('runtime.status.read', nonce='78' * 32)[:-1]}0"):
        response = test_client.get("/v1/status", headers={"authorization": authorization})
        assert response.status_code == 401
        assert_safe_error(response, "LOCAL_AUTH_REQUIRED")
