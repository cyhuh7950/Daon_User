from __future__ import annotations

from contextlib import contextmanager

import pytest

import daon_user_api.user_provider_credentials as module
from daon_user_api.provider_connection_adapters import AdapterError, AdapterRegistry, TransportResponse
from daon_user_api.provider_credentials import ProviderCredentialCipher, ProviderCredentialError
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


def test_legacy_personal_v1_resolves_as_missing_without_rebinding() -> None:
    cipher = ProviderCredentialCipher(bytes(range(32)), encryption_key_version=1)
    legacy = cipher.encrypt("route-1", "OPENROUTER", 1, b"legacy-personal-key")
    seen_contexts = []

    class Cursor:
        def __init__(self, row):
            self.row = row

        def fetchone(self):
            return self.row

    class Connection:
        def execute(self, sql, params=()):
            if sql.startswith("SELECT provider_code"):
                return Cursor(("OPENROUTER", None, None, None, None, 0, "personal", "required"))
            if sql.startswith("SELECT encrypted_credential"):
                assert params == ("tenant-1", "user-1", "route-1")
                return Cursor((
                    legacy.ciphertext, legacy.nonce, legacy.encryption_key_version,
                    legacy.schema_version, legacy.credential_version, "verified",
                ))
            raise AssertionError(f"unexpected query: {sql}")

    class Store:
        @contextmanager
        def _transaction(self, context):
            seen_contexts.append(context)
            yield Connection()

    service = module.PostgresUserProviderCredentialService(Store(), cipher)
    result = service.resolve_credential(
        tenant_id="tenant-1", user_id="user-1", connection_id="route-1",
    )

    assert result.source == "none" and result.credential is None
    assert len(seen_contexts) == 1
    assert seen_contexts[0].tenant_id == "tenant-1"
    assert seen_contexts[0].actor_id == "user-1"
    assert seen_contexts[0].workspace_id == "user:user-1"


def test_legacy_personal_v1_is_reported_as_needing_reentry() -> None:
    class Cursor:
        def fetchall(self):
            return (("route-1", "OPENROUTER", 3, "verified", 1),)

    class Connection:
        def execute(self, sql, params=()):
            assert "credential_schema_version" in sql
            assert params == ("tenant-1", "user-1")
            return Cursor()

    class Store:
        @contextmanager
        def _transaction(self, _context):
            yield Connection()

    service = module.PostgresUserProviderCredentialService(
        Store(), ProviderCredentialCipher(bytes(range(32)), encryption_key_version=1),
    )
    credentials = service.list_credentials(tenant_id="tenant-1", user_id="user-1")

    assert len(credentials) == 1
    assert credentials[0].configured is False
    assert credentials[0].verification_status == "unverified"
    assert credentials[0].credential_version == 3


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


def test_pending_custom_without_allowed_models_cannot_verify_personal_key(monkeypatch) -> None:
    calls = []

    class Cursor:
        def __init__(self, row=None, rows=()):
            self.row, self.rows = row, rows

        def fetchone(self):
            return self.row

        def fetchall(self):
            return self.rows

    class Connection:
        def execute(self, sql, _params=()):
            calls.append(sql)
            if sql.startswith("SELECT provider_code"):
                return Cursor(("CUSTOM", "https://models.example/v1", "personal", "required", True, "openai_compatible"))
            if sql.startswith("SELECT credential_version"):
                return Cursor(None)
            if sql.startswith("SELECT model_id FROM system_provider_allowed_models"):
                return Cursor(rows=())
            return Cursor()

    class Store:
        @contextmanager
        def _transaction(self, _context):
            yield Connection()

    class Transport:
        def request(self, *_args, **_kwargs):
            raise AssertionError("pending connection must not call Provider")

    monkeypatch.setattr(module, "AdapterRegistry", lambda: AdapterRegistry(Transport()))
    service = module.PostgresUserProviderCredentialService(
        Store(), ProviderCredentialCipher(bytes(range(32)), encryption_key_version=1),
    )

    with pytest.raises(module.UserProviderCredentialError, match="^PROVIDER_MODEL_IDS_INVALID$"):
        service.replace_credential(
            tenant_id="tenant-1", user_id="user-1", connection_id="custom-1",
            credential="fixture-personal-secret", expected_version=0,
        )

    assert not any(sql.startswith("INSERT INTO user_provider_credentials") for sql in calls)


def test_custom_empty_allowlist_is_rejected_before_a_noop_verifier_can_mark_key_verified(monkeypatch) -> None:
    calls = []

    class Cursor:
        def __init__(self, row=None, rows=()):
            self.row, self.rows = row, rows

        def fetchone(self):
            return self.row

        def fetchall(self):
            return self.rows

    class Connection:
        def execute(self, sql, _params=()):
            calls.append(sql)
            if sql.startswith("SELECT provider_code"):
                return Cursor(("CUSTOM", "https://models.example/v1", "personal", "required", True, "openai_compatible"))
            if sql.startswith("SELECT credential_version"):
                return Cursor(None)
            if sql.startswith("SELECT model_id FROM system_provider_allowed_models"):
                return Cursor(rows=())
            return Cursor()

    class Store:
        @contextmanager
        def _transaction(self, _context):
            yield Connection()

    class NoopVerifier:
        def verify_models(self, _profile, _credential, model_ids):
            calls.append(("verify_models", tuple(model_ids)))
            return ()

    class Registry:
        def adapter(self, _provider_code, _adapter_type=""):
            return NoopVerifier()

    monkeypatch.setattr(module, "AdapterRegistry", Registry)
    service = module.PostgresUserProviderCredentialService(
        Store(), ProviderCredentialCipher(bytes(range(32)), encryption_key_version=1),
    )

    with pytest.raises(module.UserProviderCredentialError, match="^PROVIDER_MODEL_IDS_INVALID$"):
        service.replace_credential(
            tenant_id="tenant-1", user_id="user-1", connection_id="custom-1",
            credential="fixture-personal-secret", expected_version=0,
        )

    assert ("verify_models", ()) not in calls
    assert not any(sql.startswith("INSERT INTO user_provider_credentials") for sql in calls)


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
    insert = next(
        item for item in calls
        if isinstance(item, tuple) and item[0].startswith("INSERT INTO user_provider_credentials")
    )
    params = insert[1]
    sealed = module._sealed((params[4], params[5], params[6], params[7], params[8]))
    cipher = ProviderCredentialCipher(bytes(range(32)), encryption_key_version=1)
    assert sealed.schema_version == 2
    assert cipher.decrypt(
        "custom-1", "CUSTOM", 2, sealed, tenant_id="tenant-1", user_id="user-1",
    ) == b"fixture-personal-secret"
    with pytest.raises(ProviderCredentialError, match="^CREDENTIAL_DECRYPTION_FAILED$"):
        cipher.decrypt(
            "custom-1", "CUSTOM", 2, sealed, tenant_id="tenant-2", user_id="user-1",
        )


@pytest.mark.parametrize("second_response", [{"output_text": "ready"}, {}])
def test_omniroute_personal_key_probes_every_allowed_model_before_encrypt(monkeypatch, second_response) -> None:
    calls = []

    class Cursor:
        def __init__(self, row=None, rows=()):
            self.row, self.rows = row, rows

        def fetchone(self):
            return self.row

        def fetchall(self):
            return self.rows

    class Connection:
        def execute(self, sql, _params=()):
            calls.append(sql)
            if sql.startswith("SELECT provider_code"):
                return Cursor(("OMNIROUTE", "https://omniroute.example/v1", "personal", "required", True, "OMNIROUTE"))
            if sql.startswith("SELECT credential_version"):
                return Cursor((0,))
            if sql.startswith("SELECT model_id FROM system_provider_allowed_models"):
                return Cursor(rows=(("cc/claude-sonnet",), ("combo-writing",)))
            return Cursor()

    class Store:
        @contextmanager
        def _transaction(self, _context):
            yield Connection()

    class Transport:
        def request(self, method, url, headers, body, timeout_seconds, *, follow_redirects):
            calls.append((method, url, body["model"], follow_redirects))
            return TransportResponse(200, second_response if body["model"] == "combo-writing" else {"output_text": "ready"})

    monkeypatch.setattr(module, "AdapterRegistry", lambda **kwargs: AdapterRegistry(Transport(), **kwargs))
    service = module.PostgresUserProviderCredentialService(
        Store(), ProviderCredentialCipher(bytes(range(32)), encryption_key_version=1),
    )

    if second_response:
        result = service.replace_credential(
            tenant_id="tenant-1", user_id="user-1", connection_id="route-1",
            credential="fixture-personal-secret", expected_version=0,
        )
        assert result.verification_status == "verified"
        assert any(isinstance(item, str) and item.startswith("INSERT INTO user_provider_credentials") for item in calls)
    else:
        with pytest.raises(module.UserProviderCredentialError, match="^PROVIDER_PROBE_RESPONSE_INVALID$"):
            service.replace_credential(
                tenant_id="tenant-1", user_id="user-1", connection_id="route-1",
                credential="fixture-personal-secret", expected_version=0,
            )
        assert not any(isinstance(item, str) and item.startswith("INSERT INTO user_provider_credentials") for item in calls)
    assert ("POST", "https://omniroute.example/v1/responses", "cc/claude-sonnet", False) in calls
    assert ("POST", "https://omniroute.example/v1/responses", "combo-writing", False) in calls


@pytest.mark.parametrize("legacy_allowed", [
    ("m1", "m2", "m3", "m4", "m5"),
    ("auto", "m1", "m2", "m3", "m4"),
])
def test_omniroute_personal_key_probes_every_legacy_allowed_model(monkeypatch, legacy_allowed) -> None:
    calls = []

    class Cursor:
        def __init__(self, row=None, rows=()):
            self.row, self.rows = row, rows

        def fetchone(self):
            return self.row

        def fetchall(self):
            return self.rows

    class Connection:
        def execute(self, sql, _params=()):
            calls.append(sql)
            if sql.startswith("SELECT provider_code"):
                return Cursor(("OMNIROUTE", "https://omniroute.example/v1", "personal", "required", True, "OMNIROUTE"))
            if sql.startswith("SELECT credential_version"):
                return Cursor((1,))
            if sql.startswith("SELECT model_id FROM system_provider_allowed_models"):
                return Cursor(rows=tuple((model_id,) for model_id in legacy_allowed))
            return Cursor()

    class Store:
        @contextmanager
        def _transaction(self, _context):
            yield Connection()

    class Transport:
        def request(self, method, url, headers, body, timeout_seconds, *, follow_redirects):
            calls.append((method, url, body))
            return TransportResponse(200, {"output_text": "ready"})

    monkeypatch.setattr(module, "AdapterRegistry", lambda **kwargs: AdapterRegistry(Transport(), **kwargs))
    service = module.PostgresUserProviderCredentialService(
        Store(), ProviderCredentialCipher(bytes(range(32)), encryption_key_version=1),
    )

    saved = service.replace_credential(
        tenant_id="tenant-1", user_id="user-1", connection_id="route-1",
        credential="fixture-personal-secret", expected_version=1,
    )

    assert saved.verification_status == "verified"
    assert [item[2]["model"] for item in calls if isinstance(item, tuple)] == list(legacy_allowed)
    assert any(isinstance(item, str) and item.startswith("INSERT INTO user_provider_credentials") for item in calls)


def test_omniroute_personal_key_without_allowed_models_omits_model(monkeypatch) -> None:
    calls = []

    class Cursor:
        def __init__(self, row=None, rows=()):
            self.row, self.rows = row, rows

        def fetchone(self):
            return self.row

        def fetchall(self):
            return self.rows

    class Connection:
        def execute(self, sql, _params=()):
            if sql.startswith("SELECT provider_code"):
                return Cursor(("OMNIROUTE", "https://omniroute.example/v1", "personal", "required", True, "OMNIROUTE"))
            if sql.startswith("SELECT credential_version"):
                return Cursor((0,))
            if sql.startswith("SELECT model_id FROM system_provider_allowed_models"):
                return Cursor(rows=())
            return Cursor()

    class Store:
        @contextmanager
        def _transaction(self, _context):
            yield Connection()

    class Transport:
        def request(self, method, url, headers, body, timeout_seconds, *, follow_redirects):
            calls.append((method, url, body))
            return TransportResponse(200, {"output_text": "ready"})

    monkeypatch.setattr(module, "AdapterRegistry", lambda **kwargs: AdapterRegistry(Transport(), **kwargs))
    service = module.PostgresUserProviderCredentialService(
        Store(), ProviderCredentialCipher(bytes(range(32)), encryption_key_version=1),
    )

    saved = service.replace_credential(
        tenant_id="tenant-1", user_id="user-1", connection_id="route-1",
        credential="fixture-personal-secret", expected_version=0,
    )

    assert saved.verification_status == "verified"
    assert len(calls) == 1
    assert calls[0][0:2] == ("POST", "https://omniroute.example/v1/responses")
    assert "model" not in calls[0][2]
