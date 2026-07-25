from fastapi.testclient import TestClient
import pytest

from daon_user_local_service import app as app_module
from daon_user_local_service.app import create_app


TOKEN = "token-" + ("x" * 43)
INSTANCE = "instance-123"
EXPECTED_MAX_HEADER_BYTES = 8192
EXPECTED_MAX_REQUEST_BODY_BYTES = 0


def client() -> TestClient:
    return TestClient(create_app(token=TOKEN, app_instance_id=INSTANCE))


def auth_headers(token: str = TOKEN, instance: str = INSTANCE) -> dict[str, str]:
    return {
        "authorization": f"Bearer {token}",
        "x-daon-app-instance": instance,
        "host": "127.0.0.1:48123",
    }


def test_status_requires_matching_token_and_instance() -> None:
    test_client = client()
    assert test_client.get("/v1/status", headers={"host": "127.0.0.1:48123"}).status_code == 401
    assert test_client.get("/v1/status", headers=auth_headers(token="wrong")).status_code == 401
    assert test_client.get("/v1/status", headers=auth_headers(instance="other")).status_code == 401


def test_status_returns_only_safe_operational_metadata() -> None:
    response = client().get("/v1/status", headers=auth_headers())
    assert response.status_code == 200
    assert response.json() == {
        "status": "ready",
        "protocol_version": "1.0",
        "app_instance_id": INSTANCE,
    }
    assert TOKEN not in response.text


def test_unapproved_method_path_host_and_command_fail_closed() -> None:
    test_client = client()
    assert test_client.post("/v1/status", headers=auth_headers()).status_code == 405
    assert test_client.get("/v1/unknown", headers=auth_headers()).status_code == 404
    assert (
        test_client.post("/v1/commands/arbitrary", headers=auth_headers()).status_code == 404
    )
    bad_host = auth_headers()
    bad_host["host"] = "192.168.1.10:48123"
    assert test_client.get("/v1/status", headers=bad_host).status_code == 400


def test_error_responses_do_not_echo_secrets() -> None:
    response = client().get("/v1/status", headers=auth_headers(token="attacker-secret"))
    assert response.status_code == 401
    assert "attacker-secret" not in response.text
    assert TOKEN not in response.text


def test_authentication_always_compares_token_and_instance(
    monkeypatch,
) -> None:
    calls: list[tuple[str, str]] = []
    real_compare = app_module.hmac.compare_digest

    def observe(left: str, right: str) -> bool:
        calls.append((left, right))
        return real_compare(left, right)

    monkeypatch.setattr(app_module.hmac, "compare_digest", observe)
    response = client().get("/v1/status", headers=auth_headers(token="wrong"))
    assert response.status_code == 401
    assert calls == [("wrong", TOKEN), (INSTANCE, INSTANCE)]


@pytest.mark.parametrize(
    ("headers", "expected_status", "expected_code"),
    [
        ({"content-length": "1"}, 413, "REQUEST_BODY_NOT_ALLOWED"),
        ({"content-length": "-1"}, 400, "INVALID_CONTENT_LENGTH"),
        ({"content-length": "not-a-number"}, 400, "INVALID_CONTENT_LENGTH"),
        ({"transfer-encoding": "chunked"}, 400, "TRANSFER_ENCODING_NOT_ALLOWED"),
    ],
)
def test_status_rejects_body_headers_before_reading_body(
    headers: dict[str, str],
    expected_status: int,
    expected_code: str,
) -> None:
    response = client().get("/v1/status", headers=auth_headers() | headers)
    assert response.status_code == expected_status
    assert response.json() == {"error_code": expected_code}


def test_status_allows_exact_zero_body_contract() -> None:
    response = client().get(
        "/v1/status",
        headers=auth_headers() | {"content-length": str(EXPECTED_MAX_REQUEST_BODY_BYTES)},
    )
    assert response.status_code == 200


def test_status_rejects_headers_over_declared_limit() -> None:
    oversized = "x" * EXPECTED_MAX_HEADER_BYTES
    response = client().get(
        "/v1/status",
        headers=auth_headers() | {"x-oversized": oversized},
    )
    assert response.status_code == 431
    assert response.json() == {"error_code": "REQUEST_HEADERS_TOO_LARGE"}
