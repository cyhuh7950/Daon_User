from __future__ import annotations

import hashlib
import hmac
import re
import secrets
import threading
from dataclasses import dataclass
from typing import Final


TOKEN_VERSION: Final = "lt1"
DEFAULT_TOKEN_TTL_SECONDS: Final = 60
MAX_TOKEN_TTL_SECONDS: Final = 300
MAX_TOKEN_BYTES: Final = 2048
_IDENTIFIER = re.compile(r"^[A-Za-z0-9._-]{1,128}$")
_HEX_256 = re.compile(r"^[0-9a-f]{64}$")


class TokenError(ValueError):
    def __init__(self) -> None:
        super().__init__("LOCAL_AUTH_REQUIRED")


@dataclass(frozen=True, slots=True)
class TokenClaims:
    app_instance_id: str
    capability: str
    command: str
    issued_at: int
    expires_at: int
    nonce: str


class NonceReplayCache:
    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._expires_by_nonce: dict[str, int] = {}

    def consume(self, nonce: str, expires_at: int, now: int) -> bool:
        with self._lock:
            self._expires_by_nonce = {
                known: expiry
                for known, expiry in self._expires_by_nonce.items()
                if expiry > now
            }
            if nonce in self._expires_by_nonce:
                return False
            self._expires_by_nonce[nonce] = expires_at
            return True


def _secret_bytes(root_secret: str) -> bytes:
    if not isinstance(root_secret, str) or not _HEX_256.fullmatch(root_secret):
        raise TokenError()
    return bytes.fromhex(root_secret)


def _valid_identifier(value: str) -> bool:
    return isinstance(value, str) and _IDENTIFIER.fullmatch(value) is not None


def issue_request_token(
    *,
    root_secret: str,
    app_instance_id: str,
    capability: str,
    command: str,
    issued_at: int,
    ttl_seconds: int = DEFAULT_TOKEN_TTL_SECONDS,
    nonce: str | None = None,
) -> str:
    if (
        not _valid_identifier(app_instance_id)
        or not _valid_identifier(capability)
        or not _valid_identifier(command)
        or not isinstance(issued_at, int)
        or isinstance(issued_at, bool)
        or issued_at < 0
        or not isinstance(ttl_seconds, int)
        or isinstance(ttl_seconds, bool)
        or not 1 <= ttl_seconds <= MAX_TOKEN_TTL_SECONDS
    ):
        raise TokenError()
    actual_nonce = secrets.token_hex(32) if nonce is None else nonce
    if not isinstance(actual_nonce, str) or not _HEX_256.fullmatch(actual_nonce):
        raise TokenError()
    fields = (
        TOKEN_VERSION,
        str(issued_at),
        str(issued_at + ttl_seconds),
        app_instance_id,
        capability,
        command,
        actual_nonce,
    )
    unsigned = "|".join(fields)
    signature = hmac.new(
        _secret_bytes(root_secret),
        unsigned.encode("ascii"),
        hashlib.sha256,
    ).hexdigest()
    return f"{unsigned}|{signature}"


def verify_request_token(
    *,
    token: str,
    root_secret: str,
    expected_instance_id: str,
    expected_capability: str | None,
    expected_command: str | None,
    now: int,
    replay_cache: NonceReplayCache,
    consume: bool = True,
) -> TokenClaims:
    try:
        if not isinstance(token, str) or not 1 <= len(token) <= MAX_TOKEN_BYTES:
            raise TokenError()
        parts = token.split("|")
        if len(parts) != 8:
            raise TokenError()
        version, issued_text, expires_text, instance, capability, command, nonce, supplied = parts
        if version != TOKEN_VERSION or not _HEX_256.fullmatch(supplied):
            raise TokenError()
        unsigned = "|".join(parts[:-1])
        expected_signature = hmac.new(
            _secret_bytes(root_secret),
            unsigned.encode("ascii"),
            hashlib.sha256,
        ).hexdigest()
        if not hmac.compare_digest(supplied, expected_signature):
            raise TokenError()
        issued_at = int(issued_text)
        expires_at = int(expires_text)
        if str(issued_at) != issued_text or str(expires_at) != expires_text:
            raise TokenError()
        if (
            not _valid_identifier(instance)
            or not _valid_identifier(capability)
            or not _valid_identifier(command)
            or not _HEX_256.fullmatch(nonce)
            or issued_at > now
            or expires_at <= now
            or not 1 <= expires_at - issued_at <= MAX_TOKEN_TTL_SECONDS
            or not hmac.compare_digest(instance, expected_instance_id)
            or (
                expected_capability is not None
                and not hmac.compare_digest(capability, expected_capability)
            )
            or (
                expected_command is not None
                and not hmac.compare_digest(command, expected_command)
            )
        ):
            raise TokenError()
        claims = TokenClaims(instance, capability, command, issued_at, expires_at, nonce)
        if consume and not replay_cache.consume(nonce, expires_at, now):
            raise TokenError()
        return claims
    except (UnicodeError, ValueError, OverflowError, TokenError) as error:
        if isinstance(error, TokenError):
            raise
        raise TokenError() from None
