from __future__ import annotations

from contextlib import contextmanager

import pytest

import daon_user_api.user_provider_credentials as module
from daon_user_api.provider_connection_adapters import AdapterError, AdapterRegistry, TransportResponse
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

        def fetchall(self):
            return (("manual-a",),)

    class Connection:
        def execute(self, sql, _params=()):
            calls.append(sql)
            if sql.startswith("SELECT provider_code"):
                return Cursor(("CUSTOM", "https://models.example.com/v1", "personal", "required", True, "openai_compatible"))
            if sql.startswith("SELECT credential_version"):
                return Cursor((2,))
            return Cursor()

    class Store:
        @contextmanager
        def _transaction(self, _context):
            yield Connection()

    class FailingAdapter:
        def verify_models(self, _profile, _credential, _model_ids):
            raise AdapterError("PROVIDER_AUTHENTICATION_FAILED", 401)

    class Registry:
        def adapter(self, _code, _adapter_type=""):
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


@pytest.mark.parametrize("adapter_type", ["openai_compatible", "anthropic_compatible"])
def test_custom_personal_key_probes_only_db_allowed_models_before_encrypt(monkeypatch, adapter_type) -> None:
    calls = []
    secret = "fixture-personal-secret"

    class Cursor:
        def __init__(self, row=None, rows=()):
            self.row, self.rows = row, rows

        def fetchone(self):
            return self.row

        def fetchall(self):
            return self.rows

    class Connection:
        def execute(self, sql, params=()):
            calls.append((sql, params))
            if sql.startswith("SELECT provider_code"):
                return Cursor(("CUSTOM", "https://models.example.com/v1", "personal", "required", True, adapter_type))
            if sql.startswith("SELECT credential_version"):
                return Cursor((2,))
            if sql.startswith("SELECT model_id FROM system_provider_allowed_models"):
                return Cursor(rows=(("manual-a",), ("manual-b",)))
            return Cursor()

    class Store:
        @contextmanager
        def _transaction(self, _context):
            yield Connection()

    class Adapter:
        def verify_models(self, _profile, credential, model_ids):
            calls.append(("probe", credential, tuple(model_ids)))
            if adapter_type == "anthropic_compatible":
                raise AdapterError("PROVIDER_PROBE_RESPONSE_INVALID", 503)
            return ()

    class Registry:
        def adapter(self, provider_code, selected_type=""):
            assert (provider_code, selected_type) == ("CUSTOM", adapter_type)
            return Adapter()

    monkeypatch.setattr(module, "AdapterRegistry", Registry)
    service = module.PostgresUserProviderCredentialService(
        Store(), ProviderCredentialCipher(bytes(range(32)), encryption_key_version=1),
    )
    if adapter_type == "anthropic_compatible":
        with pytest.raises(module.UserProviderCredentialError, match="^PROVIDER_PROBE_RESPONSE_INVALID$"):
            service.replace_credential(
                tenant_id="tenant-1", user_id="user-1", connection_id="custom-1",
                credential=secret, expected_version=2,
            )
        assert not any(item[0].startswith("INSERT INTO user_provider_credentials") for item in calls if len(item) == 2)
    else:
        result = service.replace_credential(
            tenant_id="tenant-1", user_id="user-1", connection_id="custom-1",
            credential=secret, expected_version=2,
        )
        assert result.verification_status == "verified"
        assert any(item[0].startswith("INSERT INTO user_provider_credentials") for item in calls if len(item) == 2)
    assert ("probe", secret, ("manual-a", "manual-b")) in calls


def test_migrated_custom_personal_key_replacement_uses_openai_probe(monkeypatch) -> None:
    calls = []

    class Cursor:
        def __init__(self, row=None, rows=()):
            self.row, self.rows = row, rows

        def fetchone(self):
            return self.row

        def fetchall(self):
            return self.rows

    class Connection:
        def execute(self, sql, params=()):
            calls.append((sql, params))
            if sql.startswith("SELECT provider_code"):
                return Cursor(("CUSTOM", "https://models.example.com/v1", "personal", "required", True, "CUSTOM"))
            if sql.startswith("SELECT credential_version"):
                return Cursor((1,))
            if sql.startswith("SELECT model_id FROM system_provider_allowed_models"):
                return Cursor(rows=(("legacy-model",),))
            return Cursor()

    class Store:
        @contextmanager
        def _transaction(self, _context):
            yield Connection()

    class Transport:
        def request(self, method, url, headers, body, timeout_seconds, *, follow_redirects):
            calls.append((method, url, body["model"], follow_redirects))
            return TransportResponse(200, {"choices": [{"message": {"content": "ready"}}]})

    transport = Transport()
    monkeypatch.setattr(module, "AdapterRegistry", lambda: AdapterRegistry(transport))
    service = module.PostgresUserProviderCredentialService(
        Store(), ProviderCredentialCipher(bytes(range(32)), encryption_key_version=1),
    )

    result = service.replace_credential(
        tenant_id="tenant-1", user_id="user-1", connection_id="custom-1",
        credential="fixture-personal-secret", expected_version=1,
    )

    assert result.verification_status == "verified" and result.credential_version == 2
    assert ("POST", "https://models.example.com/v1/chat/completions", "legacy-model", False) in calls
    assert any(item[0].startswith("INSERT INTO user_provider_credentials") for item in calls if len(item) == 2)
