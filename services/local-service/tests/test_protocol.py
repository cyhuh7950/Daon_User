import json

import pytest

from daon_user_local_service.protocol import (
    MAX_BOOTSTRAP_BYTES,
    PROTOCOL_VERSION,
    BootstrapError,
    parse_bootstrap,
    ready_envelope,
)


def valid_bootstrap() -> bytes:
    return json.dumps(
        {
            "protocol_version": PROTOCOL_VERSION,
            "app_instance_id": "instance-123",
            "token": "token-" + ("x" * 43),
        }
    ).encode()


def test_parse_bootstrap_accepts_exact_contract() -> None:
    parsed = parse_bootstrap(valid_bootstrap())
    assert parsed.app_instance_id == "instance-123"
    assert parsed.token.startswith("token-")


@pytest.mark.parametrize(
    "payload",
    [
        b"{}",
        b"not-json",
        json.dumps(
            {"protocol_version": "unsupported", "app_instance_id": "i", "token": "x" * 32}
        ).encode(),
        b"x" * (MAX_BOOTSTRAP_BYTES + 1),
    ],
)
def test_parse_bootstrap_fails_closed(payload: bytes) -> None:
    with pytest.raises(BootstrapError):
        parse_bootstrap(payload)


def test_ready_envelope_never_contains_token() -> None:
    envelope = ready_envelope(port=48123, app_instance_id="instance-123")
    assert envelope == {
        "event": "ready",
        "protocol_version": PROTOCOL_VERSION,
        "app_instance_id": "instance-123",
        "port": 48123,
    }
    assert "token" not in envelope


@pytest.mark.parametrize("port", [0, 65536])
def test_ready_envelope_rejects_invalid_port(port: int) -> None:
    with pytest.raises(ValueError):
        ready_envelope(port=port, app_instance_id="instance-123")
