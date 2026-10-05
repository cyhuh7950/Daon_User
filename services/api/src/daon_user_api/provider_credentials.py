from __future__ import annotations

import hashlib
import hmac
import json
import os
import re
from dataclasses import dataclass, field
from datetime import datetime

from cryptography.exceptions import InvalidTag
from cryptography.hazmat.primitives.ciphers.aead import AESGCM


_IDENTIFIER_PATTERN = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:-]{0,255}$")
_PROVIDER_PATTERN = re.compile(r"^[A-Z][A-Z0-9_]{0,63}$")
_NONCE_BYTES = 12
_SYSTEM_SCHEMA_VERSION = 1
PERSONAL_CREDENTIAL_SCHEMA_VERSION = 2
_SCHEMA_VERSION = _SYSTEM_SCHEMA_VERSION
_IDEMPOTENCY_FINGERPRINT_DOMAIN = b"daon-user/provider-credential/idempotency/v1"


class ProviderCredentialError(RuntimeError):
    """Safe credential failure that never includes secret material."""


@dataclass(frozen=True, slots=True)
class EncryptedCredential:
    ciphertext: bytes = field(repr=False)
    nonce: bytes = field(repr=False)
    encryption_key_version: int
    credential_version: int
    schema_version: int


@dataclass(frozen=True, slots=True)
class ProviderConnection:
    connection_id: str
    provider_code: str
    display_name: str
    base_url: str
    credential: EncryptedCredential | None = field(repr=False)
    enabled: bool
    version: int
    verification_status: str = "unverified"
    verified_at: datetime | None = None


class ProviderCredentialCipher:
    """AES-256-GCM credential envelope bound to connection metadata."""

    def __init__(self, master_key: bytes, *, encryption_key_version: int) -> None:
        if not isinstance(master_key, bytes) or len(master_key) < 32:
            raise ProviderCredentialError("PROVIDER_CREDENTIAL_KEY_INVALID")
        if encryption_key_version < 1:
            raise ProviderCredentialError("PROVIDER_CREDENTIAL_KEY_INVALID")
        self._key = hashlib.sha256(master_key).digest()
        self._idempotency_fingerprint_key = hmac.new(
            master_key,
            _IDEMPOTENCY_FINGERPRINT_DOMAIN,
            hashlib.sha256,
        ).digest()
        self._encryption_key_version = encryption_key_version

    def idempotency_fingerprint(self, connection_id: str, plaintext: bytes) -> str:
        if not _IDENTIFIER_PATTERN.fullmatch(connection_id):
            raise ProviderCredentialError("PROVIDER_CONNECTION_ID_INVALID")
        if not isinstance(plaintext, bytes) or not plaintext:
            raise ProviderCredentialError("PROVIDER_CREDENTIAL_INVALID")
        context = json.dumps(
            {
                "connection_id": connection_id,
                "domain": _IDEMPOTENCY_FINGERPRINT_DOMAIN.decode("ascii"),
                "schema_version": _SCHEMA_VERSION,
            },
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")
        return hmac.new(
            self._idempotency_fingerprint_key,
            context + len(plaintext).to_bytes(8, "big") + plaintext,
            hashlib.sha256,
        ).hexdigest()

    def encrypt(
        self,
        connection_id: str,
        provider_code: str,
        version: int,
        plaintext: bytes,
        *,
        tenant_id: str | None = None,
        user_id: str | None = None,
    ) -> EncryptedCredential:
        scope = self._scope(tenant_id, user_id)
        schema_version = PERSONAL_CREDENTIAL_SCHEMA_VERSION if scope is not None else _SYSTEM_SCHEMA_VERSION
        aad = self._aad(connection_id, provider_code, version, schema_version, scope)
        if not isinstance(plaintext, bytes) or not plaintext:
            raise ProviderCredentialError("PROVIDER_CREDENTIAL_INVALID")
        nonce = os.urandom(_NONCE_BYTES)
        ciphertext = AESGCM(self._key).encrypt(nonce, plaintext, aad)
        return EncryptedCredential(
            ciphertext=ciphertext,
            nonce=nonce,
            encryption_key_version=self._encryption_key_version,
            credential_version=version,
            schema_version=schema_version,
        )

    def decrypt(
        self,
        connection_id: str,
        provider_code: str,
        version: int,
        sealed: EncryptedCredential,
        *,
        tenant_id: str | None = None,
        user_id: str | None = None,
    ) -> bytes:
        scope = self._scope(tenant_id, user_id)
        if sealed.encryption_key_version != self._encryption_key_version:
            raise ProviderCredentialError("PROVIDER_CREDENTIAL_KEY_UNAVAILABLE")
        if sealed.credential_version != version:
            raise ProviderCredentialError("CREDENTIAL_DECRYPTION_FAILED")
        expected_schema_version = PERSONAL_CREDENTIAL_SCHEMA_VERSION if scope is not None else _SYSTEM_SCHEMA_VERSION
        if sealed.schema_version != expected_schema_version:
            raise ProviderCredentialError("CREDENTIAL_DECRYPTION_FAILED")
        aad = self._aad(connection_id, provider_code, version, expected_schema_version, scope)
        try:
            return AESGCM(self._key).decrypt(sealed.nonce, sealed.ciphertext, aad)
        except (InvalidTag, ValueError):
            raise ProviderCredentialError("CREDENTIAL_DECRYPTION_FAILED") from None

    @staticmethod
    def _aad(
        connection_id: str,
        provider_code: str,
        version: int,
        schema_version: int,
        scope: tuple[str, str] | None,
    ) -> bytes:
        if not _IDENTIFIER_PATTERN.fullmatch(connection_id):
            raise ProviderCredentialError("PROVIDER_CONNECTION_ID_INVALID")
        if not _PROVIDER_PATTERN.fullmatch(provider_code):
            raise ProviderCredentialError("PROVIDER_CODE_INVALID")
        if version < 1:
            raise ProviderCredentialError("PROVIDER_CREDENTIAL_VERSION_INVALID")
        values: dict[str, object] = {
            "connection_id": connection_id,
            "credential_version": version,
            "provider_code": provider_code,
            "schema_version": schema_version,
        }
        if scope is not None:
            values["tenant_id"], values["user_id"] = scope
        return json.dumps(
            values,
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")

    @staticmethod
    def _scope(tenant_id: str | None, user_id: str | None) -> tuple[str, str] | None:
        if tenant_id is None and user_id is None:
            return None
        if tenant_id is None or user_id is None:
            raise ProviderCredentialError("PROVIDER_CREDENTIAL_SCOPE_REQUIRED")
        if (
            not isinstance(tenant_id, str)
            or not isinstance(user_id, str)
            or not _IDENTIFIER_PATTERN.fullmatch(tenant_id)
            or not _IDENTIFIER_PATTERN.fullmatch(user_id)
        ):
            raise ProviderCredentialError("PROVIDER_CREDENTIAL_SCOPE_INVALID")
        return tenant_id, user_id
