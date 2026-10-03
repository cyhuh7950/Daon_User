from __future__ import annotations

from contextlib import contextmanager

import pytest

import daon_user_api.user_provider_credentials as module
from daon_user_api.provider_connection_adapters import AdapterError
from daon_user_api.provider_credentials import ProviderCredentialCipher
from daon_user_api.user_provider_credentials import resolve_credential_candidates


def test_system_credential_is_preferred_when_available() -> None:
    result = resolve_credential_candidates(bytes(range(8)), bytes(range(8, 16)))

    assert result.credential == bytes(range(8))
    assert result.source == "system"


def test_user_credential_is_used_only_when_system_credential_is_unavailable() -> None:
    result = resolve_credential_candidates(None, bytes(range(8, 16)), access_mode="personal")

    assert result.credential == bytes(range(8, 16))
    assert result.source == "user"


def test_no_credential_is_reported_without_exposing_secret_material() -> None:
    result = resolve_credential_candidates(None, None)

    assert result.credential is None
    assert result.source == "none"
    assert "key" not in repr(result).lower()


def test_personal_connection_never_uses_system_credential() -> None:
    result = resolve_credential_candidates(bytes(range(8)), None, access_mode="personal")

    assert result.credential is None
    assert result.source == "none"


def test_public_connection_never_uses_personal_credential() -> None:
    result = resolve_credential_candidates(None, bytes(range(8)), access_mode="public")

    assert result.credential is None
    assert result.source == "none"


def test_failed_personal_probe_preserves_existing_credential(monkeypatch) -> None:
    calls = []

    class Cursor:
        def __init__(self, row=None):
            self.row = row

        def fetchone(self):
            return self.row

    class Connection:
        def execute(self, sql, _params=()):
            calls.append(sql)
            if sql.startswith("SELECT provider_code"):
                return Cursor(("CUSTOM", "https://models.example.com/v1", "personal", "required", True))
            if sql.startswith("SELECT credential_version"):
                return Cursor((2,))
            return Cursor()

    class Store:
        @contextmanager
        def _transaction(self, _context):
            yield Connection()

    class FailingAdapter:
        def verify(self, _profile, _credential):
            raise AdapterError("PROVIDER_AUTHENTICATION_FAILED", 401)

    class Registry:
        def adapter(self, _code):
            return FailingAdapter()

    monkeypatch.setattr(module, "AdapterRegistry", Registry, raising=False)
    service = module.PostgresUserProviderCredentialService(
        Store(), ProviderCredentialCipher(bytes(range(32)), encryption_key_version=1),
    )
    with pytest.raises(module.UserProviderCredentialError, match="^PROVIDER_AUTHENTICATION_FAILED$"):
        service.replace_credential(
            tenant_id="tenant-1", user_id="user-1", connection_id="custom-1",
            credential="".join(chr(n) for n in range(97, 101)), expected_version=2,
        )

    assert not any(sql.startswith("INSERT INTO user_provider_credentials") for sql in calls)
