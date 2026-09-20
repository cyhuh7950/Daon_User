from __future__ import annotations

import hashlib
import os
from dataclasses import replace

import pytest

from daon_user_api.provider_credentials import (
    EncryptedCredential,
    ProviderConnection,
    ProviderCredentialCipher,
    ProviderCredentialError,
)


@pytest.fixture
def cipher() -> ProviderCredentialCipher:
    return ProviderCredentialCipher(os.urandom(32), encryption_key_version=7)


def test_cipher_round_trips_credential_without_exposing_plaintext(
    cipher: ProviderCredentialCipher,
) -> None:
    plaintext = b"credential-test-value"

    sealed = cipher.encrypt("ollama-lan", "OLLAMA", 1, plaintext)

    assert cipher.decrypt("ollama-lan", "OLLAMA", 1, sealed) == plaintext
    assert plaintext not in sealed.ciphertext
    assert plaintext.decode() not in repr(sealed)
    assert sealed.encryption_key_version == 7
    assert sealed.credential_version == 1


def test_cipher_rejects_cross_connection_replay(
    cipher: ProviderCredentialCipher,
) -> None:
    sealed = cipher.encrypt("ollama-lan", "OLLAMA", 1, b"credential-test-value")

    with pytest.raises(
        ProviderCredentialError,
        match="^CREDENTIAL_DECRYPTION_FAILED$",
    ):
        cipher.decrypt("ollama-public", "OLLAMA", 1, sealed)


def test_cipher_rejects_cross_provider_replay(
    cipher: ProviderCredentialCipher,
) -> None:
    sealed = cipher.encrypt("gateway-primary", "EOUL_GATEWAY", 1, b"client-key")

    with pytest.raises(
        ProviderCredentialError,
        match="^CREDENTIAL_DECRYPTION_FAILED$",
    ):
        cipher.decrypt("gateway-primary", "OMNIROUTE", 1, sealed)


def test_cipher_rejects_cross_version_replay(
    cipher: ProviderCredentialCipher,
) -> None:
    sealed = cipher.encrypt("openrouter-primary", "OPENROUTER", 3, b"api-key")

    with pytest.raises(
        ProviderCredentialError,
        match="^CREDENTIAL_DECRYPTION_FAILED$",
    ):
        cipher.decrypt("openrouter-primary", "OPENROUTER", 4, sealed)


def test_cipher_rejects_cross_schema_replay(
    cipher: ProviderCredentialCipher,
) -> None:
    sealed = cipher.encrypt("openrouter-primary", "OPENROUTER", 3, b"api-key")

    with pytest.raises(
        ProviderCredentialError,
        match="^CREDENTIAL_DECRYPTION_FAILED$",
    ):
        cipher.decrypt(
            "openrouter-primary",
            "OPENROUTER",
            3,
            replace(sealed, schema_version=sealed.schema_version + 1),
        )


@pytest.mark.parametrize("field", ["ciphertext", "nonce"])
def test_cipher_rejects_tampering(
    cipher: ProviderCredentialCipher,
    field: str,
) -> None:
    sealed = cipher.encrypt("openrouter-primary", "OPENROUTER", 1, b"api-key")
    changed = bytearray(getattr(sealed, field))
    changed[0] ^= 1
    tampered = EncryptedCredential(
        ciphertext=bytes(changed) if field == "ciphertext" else sealed.ciphertext,
        nonce=bytes(changed) if field == "nonce" else sealed.nonce,
        encryption_key_version=sealed.encryption_key_version,
        credential_version=sealed.credential_version,
        schema_version=sealed.schema_version,
    )

    with pytest.raises(
        ProviderCredentialError,
        match="^CREDENTIAL_DECRYPTION_FAILED$",
    ):
        cipher.decrypt("openrouter-primary", "OPENROUTER", 1, tampered)


def test_cipher_rejects_wrong_key_version_without_trying_fallback(
    cipher: ProviderCredentialCipher,
) -> None:
    sealed = cipher.encrypt("openrouter-primary", "OPENROUTER", 1, b"api-key")
    wrong_version = EncryptedCredential(
        ciphertext=sealed.ciphertext,
        nonce=sealed.nonce,
        encryption_key_version=sealed.encryption_key_version + 1,
        credential_version=sealed.credential_version,
        schema_version=sealed.schema_version,
    )

    with pytest.raises(
        ProviderCredentialError,
        match="^PROVIDER_CREDENTIAL_KEY_UNAVAILABLE$",
    ):
        cipher.decrypt("openrouter-primary", "OPENROUTER", 1, wrong_version)


def test_cipher_requires_at_least_256_bits_of_master_key() -> None:
    with pytest.raises(
        ProviderCredentialError,
        match="^PROVIDER_CREDENTIAL_KEY_INVALID$",
    ):
        ProviderCredentialCipher(b"short", encryption_key_version=1)


def test_idempotency_fingerprint_is_keyed_and_connection_bound(
    cipher: ProviderCredentialCipher,
) -> None:
    plaintext = b"credential-test-value"

    first = cipher.idempotency_fingerprint("ollama-lan", plaintext)
    retry = cipher.idempotency_fingerprint("ollama-lan", plaintext)
    changed_secret = cipher.idempotency_fingerprint("ollama-lan", b"credential-test-value-2")
    changed_connection = cipher.idempotency_fingerprint("ollama-public", plaintext)

    assert first == retry
    assert first != changed_secret
    assert first != changed_connection
    assert first != hashlib.sha256(plaintext).hexdigest()
    assert plaintext.decode() not in first


def test_provider_connection_supports_named_nullable_credential() -> None:
    connection = ProviderConnection(
        connection_id="ollama-lan",
        provider_code="OLLAMA",
        display_name="LAN Ollama",
        base_url="http://192.168.220.180:11434",
        credential=None,
        enabled=True,
        version=1,
    )

    assert connection.connection_id == "ollama-lan"
    assert connection.credential is None
