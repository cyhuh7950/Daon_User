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
            "root_secret": "ab" * 32,
            "storage_root_key": "cd" * 32,
            "storage_root": "C:\\Daon\\local-storage",
            "parent_process_id": 12345,
        }
    ).encode()


def test_parse_bootstrap_accepts_exact_contract() -> None:
    parsed = parse_bootstrap(valid_bootstrap())
    assert parsed.app_instance_id == "instance-123"
    assert parsed.root_secret == "ab" * 32
    assert parsed.storage_root_key == "cd" * 32
    assert parsed.storage_root == "C:\\Daon\\local-storage"
    assert parsed.parent_process_id == 12345


@pytest.mark.parametrize(
    "payload",
    [
        b"{}",
        b"not-json",
        json.dumps(
            {"protocol_version": "unsupported", "app_instance_id": "i", "root_secret": "ab" * 32, "parent_process_id": 1}
        ).encode(),
        json.dumps(
            {"protocol_version": PROTOCOL_VERSION, "app_instance_id": "", "root_secret": "ab" * 32, "parent_process_id": 1}
        ).encode(),
        json.dumps(
            {"protocol_version": PROTOCOL_VERSION, "app_instance_id": "i", "root_secret": "short", "parent_process_id": 1}
        ).encode(),
        json.dumps({"protocol_version": PROTOCOL_VERSION, "app_instance_id": "i", "root_secret": "ab" * 32, "parent_process_id": 0}).encode(),
        b'{"protocol_version":"1.0","protocol_version":"1.0","app_instance_id":"i","root_secret":"' + (b"ab" * 32) + b'","parent_process_id":1}',
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
