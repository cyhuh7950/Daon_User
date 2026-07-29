from __future__ import annotations

import json
import re
from dataclasses import dataclass
from typing import Final


PROTOCOL_VERSION: Final = "1.1"
MAX_BOOTSTRAP_BYTES: Final = 4096
MAX_INSTANCE_ID_LENGTH: Final = 128
_INSTANCE_ID = re.compile(r"^[A-Za-z0-9_-]{1,128}$")
_ROOT_SECRET = re.compile(r"^[0-9a-f]{64}$")


class BootstrapError(ValueError):
    """Raised when the parent bootstrap envelope is not safe to accept."""


@dataclass(frozen=True, slots=True)
class Bootstrap:
    protocol_version: str
    app_instance_id: str
    root_secret: str
    storage_root_key: str
    storage_root: str
    parent_process_id: int


def _unique_object(pairs: list[tuple[str, object]]) -> dict[str, object]:
    result: dict[str, object] = {}
    for key, value in pairs:
        if key in result:
            raise BootstrapError("invalid bootstrap fields")
        result[key] = value
    return result


def parse_bootstrap(payload: bytes | bytearray) -> Bootstrap:
    if not payload or len(payload) > MAX_BOOTSTRAP_BYTES:
        raise BootstrapError("invalid bootstrap size")
    try:
        value = json.loads(payload, object_pairs_hook=_unique_object)
    except (UnicodeDecodeError, json.JSONDecodeError) as error:
        raise BootstrapError("invalid bootstrap format") from error
    if not isinstance(value, dict) or set(value) != {
        "protocol_version",
        "app_instance_id",
        "root_secret",
        "storage_root_key",
        "storage_root",
        "parent_process_id",
    }:
        raise BootstrapError("invalid bootstrap fields")
    protocol_version = value["protocol_version"]
    app_instance_id = value["app_instance_id"]
    root_secret = value["root_secret"]
    storage_root_key = value["storage_root_key"]
    storage_root = value["storage_root"]
    parent_process_id = value["parent_process_id"]
    if protocol_version != PROTOCOL_VERSION:
        raise BootstrapError("unsupported protocol")
    if not isinstance(app_instance_id, str) or not _INSTANCE_ID.fullmatch(app_instance_id):
        raise BootstrapError("invalid app instance")
    if not isinstance(root_secret, str) or not _ROOT_SECRET.fullmatch(root_secret):
        raise BootstrapError("invalid root secret")
    if not isinstance(storage_root_key, str) or not _ROOT_SECRET.fullmatch(storage_root_key):
        raise BootstrapError("invalid storage root key")
    if not isinstance(storage_root, str) or not 1 <= len(storage_root) <= 1024:
        raise BootstrapError("invalid storage root")
    if (
        not isinstance(parent_process_id, int)
        or isinstance(parent_process_id, bool)
        or not 1 <= parent_process_id <= 0xFFFF_FFFF
    ):
        raise BootstrapError("invalid parent process")
    return Bootstrap(
        protocol_version,
        app_instance_id,
        root_secret,
        storage_root_key,
        storage_root,
        parent_process_id,
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
