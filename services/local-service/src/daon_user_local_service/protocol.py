from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Final

PROTOCOL_VERSION: Final = "1.0"
MAX_BOOTSTRAP_BYTES: Final = 4096
MIN_TOKEN_LENGTH: Final = 32
MAX_INSTANCE_ID_LENGTH: Final = 128


class BootstrapError(ValueError):
    """Raised when the parent bootstrap envelope is not safe to accept."""


@dataclass(frozen=True, slots=True)
class Bootstrap:
    protocol_version: str
    app_instance_id: str
    token: str


def parse_bootstrap(payload: bytes) -> Bootstrap:
    if not payload or len(payload) > MAX_BOOTSTRAP_BYTES:
        raise BootstrapError("invalid bootstrap size")
    try:
        value = json.loads(payload)
    except (UnicodeDecodeError, json.JSONDecodeError) as error:
        raise BootstrapError("invalid bootstrap format") from error
    if not isinstance(value, dict) or set(value) != {
        "protocol_version",
        "app_instance_id",
        "token",
    }:
        raise BootstrapError("invalid bootstrap fields")
    protocol_version = value["protocol_version"]
    app_instance_id = value["app_instance_id"]
    token = value["token"]
    if protocol_version != PROTOCOL_VERSION:
        raise BootstrapError("unsupported protocol")
    if not isinstance(app_instance_id, str) or not 1 <= len(app_instance_id) <= MAX_INSTANCE_ID_LENGTH:
        raise BootstrapError("invalid app instance")
    if not isinstance(token, str) or len(token) < MIN_TOKEN_LENGTH:
        raise BootstrapError("invalid token")
    return Bootstrap(
        protocol_version=protocol_version,
        app_instance_id=app_instance_id,
        token=token,
    )


def ready_envelope(*, port: int, app_instance_id: str) -> dict[str, str | int]:
    if not 1 <= port <= 65535:
        raise ValueError("invalid port")
    return {
        "event": "ready",
        "protocol_version": PROTOCOL_VERSION,
        "app_instance_id": app_instance_id,
        "port": port,
    }
