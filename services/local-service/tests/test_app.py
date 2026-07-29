from __future__ import annotations

from collections.abc import Callable
import re

import pytest
from fastapi.testclient import TestClient

from daon_user_local_service.app import create_app
from daon_user_local_service.security import issue_request_token


ROOT_SECRET = "ab" * 32
INSTANCE = "12" * 16
PORT = 48123
NOW = 2_000_000_000
EXPECTED_MAX_HEADER_BYTES = 8192
OPAQUE_TRACE = re.compile(r"^[0-9a-f]{16}$")


def client(clock: Callable[[], int] = lambda: NOW) -> TestClient:
    return TestClient(
        create_app(
            root_secret=ROOT_SECRET,
            app_instance_id=INSTANCE,
            listener_port=PORT,
            clock=clock,
        ),
        base_url=f"http://127.0.0.1:{PORT}",
    )


def token(command: str, *, nonce: str, issued_at: int = NOW, ttl_seconds: int = 60) -> str:
    return issue_request_token(
        root_secret=ROOT_SECRET,
        app_instance_id=INSTANCE,
        capability="runtime.read",
        command=command,
        issued_at=issued_at,
        ttl_seconds=ttl_seconds,
        nonce=nonce,
    )


def auth_headers(command: str, *, nonce: str = "34" * 32) -> dict[str, str]:
    return {"authorization": f"Bearer {token(command, nonce=nonce)}"}


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
            }
        ],
    }


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
