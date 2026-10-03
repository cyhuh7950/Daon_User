from __future__ import annotations

from contextlib import contextmanager

import pytest
import daon_user_api.provider_connection_admin as provider_connection_admin_module

from daon_user_api.provider_connection_admin import (
    ProviderAdminAudit,
    ProviderConnectionAdminContext,
    ProviderConnectionAdminError,
    ProviderConnectionCreateCommand,
    ProviderConnectionUpdateCommand,
    PostgresProviderAdminMutationRepository,
    PostgresProviderConnectionService,
    normalized_connection_fingerprint_payload,
    validate_connection_policy,
)
from daon_user_api.provider_credentials import ProviderCredentialCipher
from daon_user_api.provider_catalog import DiscoveredModel


class Cursor:
    def __init__(self, row=None, rows=None):
        self._row = row
        self._rows = [] if rows is None else rows

    def fetchone(self):
        return self._row

    def fetchall(self):
        return self._rows


class SharedDatabase:
    def __init__(self) -> None:
        self.idempotency = {}
        self.outbox = []
        self.locks = []
        self.system_connection = None
        self.models = []
        self.connection_deleted = False


class Connection:
    def __init__(self, database: SharedDatabase) -> None:
        self.database = database

    def execute(self, sql, params=()):
        normalized = " ".join(sql.split())
        if "pg_advisory_xact_lock" in normalized:
            self.database.locks.append(params[0])
            return Cursor()
        if normalized.startswith("SELECT request_fingerprint,result FROM system_provider_admin_idempotency"):
            return Cursor(self.database.idempotency.get(tuple(params)))
        if normalized.startswith("INSERT INTO system_provider_admin_idempotency"):
            key = tuple(params[:4])
            result = getattr(params[5], "obj", params[5])
            self.database.idempotency[key] = (params[4], result)
            return Cursor()
        if normalized.startswith("INSERT INTO system_provider_audit_outbox"):
            payload = getattr(params[-1], "obj", params[-1])
            self.database.outbox.append({"params": params, "payload": payload})
            return Cursor()
        if normalized.startswith("DELETE FROM system_provider_connections"):
            self.database.connection_deleted = True
            self.database.system_connection = None
            return Cursor((params[0],))
        if normalized.startswith("UPDATE system_provider_connections SET encrypted_credential=NULL"):
            assert "credential_version=0" in normalized
            record = self.database.system_connection
            if record is None or record["connection_id"] != params[-2] or record["version"] != params[-1]:
                return Cursor(None)
            record.update({
                "encrypted_credential": None,
                "credential_nonce": None,
                "encryption_key_version": None,
                "credential_schema_version": None,
                "credential_version": 0,
                "verification_status": "unverified",
                "verified_at": None,
                "version": record["version"] + 1,
            })
            return Cursor((record["connection_id"], record["version"]))
        if normalized.startswith("SELECT c.connection_id,c.provider_code,c.display_name,c.base_url,c.enabled"):
            record = self.database.system_connection
            if record is None or (params and record["connection_id"] != params[0]):
                return Cursor(rows=[])
            return Cursor(rows=[(
                record["connection_id"], record["provider_code"], record["display_name"], record["base_url"], record["enabled"],
                record["encrypted_credential"], record["credential_version"], record["verification_status"],
                record["verified_at"], record["version"], record["catalog_status"], record["catalog_version"],
                record["access_mode"], record["credential_requirement"], record["short_code"], record["adapter_type"],
                record.get("provider_name", record["provider_code"]),
            )])
        if normalized.startswith("SELECT model_id,reported_capabilities,effective_capabilities"):
            return Cursor(rows=list(self.database.models))
        if normalized.startswith("SELECT model_id FROM system_provider_allowed_models"):
            return Cursor(rows=[(item[0],) for item in self.database.models])
        raise AssertionError(f"unexpected SQL: {normalized}")


class Store:
    def __init__(self, database: SharedDatabase) -> None:
        self.database = database

    @contextmanager
    def _transaction(self, context):
        yield Connection(self.database)


def context() -> ProviderConnectionAdminContext:
    return ProviderConnectionAdminContext(
        tenant_id="tenant-001", actor_id="system-admin", trace_id="trace-001",
        policy_version="policy-v1",
    )


def audit() -> ProviderAdminAudit:
    return ProviderAdminAudit(
        action="provider_connection.updated", target_type="provider_connection",
        target_id="ollama-lan", version=2,
    )


def test_connection_policy_requires_unique_two_letter_code_and_keyless_public() -> None:
    assert validate_connection_policy("OPENROUTER", "public", "required", "OR", "OPENROUTER") == "public"
    assert validate_connection_policy("OPENROUTER", "public", "required", "OT", "OPENROUTER") == "public"
    with pytest.raises(ProviderConnectionAdminError, match="^PROVIDER_SHORT_CODE_INVALID$"):
        validate_connection_policy("CUSTOM", "personal", "required", "OR", "openai_compatible")
    with pytest.raises(ProviderConnectionAdminError, match="^PROVIDER_SHORT_CODE_INVALID$"):
        validate_connection_policy("CUSTOM", "personal", "required", "A", "openai_compatible")
    with pytest.raises(ProviderConnectionAdminError, match="^PROVIDER_ACCESS_MODE_INVALID$"):
        validate_connection_policy("OLLAMA", "personal", "none", "OL", "OLLAMA")
    with pytest.raises(ProviderConnectionAdminError, match="^PROVIDER_ACCESS_MODE_INVALID$"):
        validate_connection_policy("OLLAMA", "personal", "required", "OL", "OLLAMA")
    with pytest.raises(ProviderConnectionAdminError, match="^PROVIDER_ADAPTER_UNSUPPORTED$"):
        validate_connection_policy("GEMINI", "public", "required", "GE", "GEMINI")


def test_persistent_replay_survives_repository_restart_without_second_mutation() -> None:
    database = SharedDatabase()
    calls = []
    payload = {"connection_id": "ollama-lan", "expected_version": 1, "credential_action": "replace"}

    first = PostgresProviderAdminMutationRepository(Store(database))
    result, replayed = first.run(
        context(), operation="provider_connection.update", idempotency_key="provider-update-0001",
        fingerprint_payload=payload, mutation=lambda _connection: calls.append("mutated") or {"version": 2},
        audit=audit(),
    )
    restarted = PostgresProviderAdminMutationRepository(Store(database))
    replay, was_replayed = restarted.run(
        context(), operation="provider_connection.update", idempotency_key="provider-update-0001",
        fingerprint_payload=payload, mutation=lambda _connection: calls.append("mutated-again") or {"version": 3},
        audit=audit(),
    )

    assert (result, replayed) == ({"version": 2}, False)
    assert (replay, was_replayed) == ({"version": 2}, True)
    assert calls == ["mutated"]
    assert len(database.locks) == 2
    assert len(database.outbox) == 1


def test_same_idempotency_key_with_different_fingerprint_fails_closed() -> None:
    database = SharedDatabase()
    repository = PostgresProviderAdminMutationRepository(Store(database))
    repository.run(
        context(), operation="provider_connection.update", idempotency_key="provider-update-0002",
        fingerprint_payload={"connection_id": "ollama-lan", "expected_version": 1, "credential_action": "preserve"},
        mutation=lambda _connection: {"version": 2}, audit=audit(),
    )

    with pytest.raises(ProviderConnectionAdminError, match="^IDEMPOTENCY_KEY_REUSED$"):
        PostgresProviderAdminMutationRepository(Store(database)).run(
            context(), operation="provider_connection.update", idempotency_key="provider-update-0002",
            fingerprint_payload={"connection_id": "ollama-lan", "expected_version": 1, "credential_action": "replace"},
            mutation=lambda _connection: {"version": 3}, audit=audit(),
        )


def test_credential_plaintext_is_absent_from_fingerprint_result_and_audit_outbox() -> None:
    secret = "raw-secret-must-never-persist"
    cipher = ProviderCredentialCipher(b"k" * 32, encryption_key_version=1)
    payload = normalized_connection_fingerprint_payload(
        connection_id="ollama-lan", provider_code="OLLAMA", display_name="LAN Ollama",
        base_url="http://ollama.internal:11434", enabled=True, expected_version=1,
        logical_model_ids=("qwen3",),
        credential_fingerprint=cipher.idempotency_fingerprint("ollama-lan", secret.encode()),
    )
    assert payload["credential_action"] == "replace"
    assert secret not in repr(payload)
    assert "credential" not in payload

    database = SharedDatabase()
    result = {"connection_id": "ollama-lan", "configured": True, "credential_version": 2}
    PostgresProviderAdminMutationRepository(Store(database)).run(
        context(), operation="provider_connection.update", idempotency_key="provider-update-0003",
        fingerprint_payload=payload, mutation=lambda _connection: result, audit=audit(),
    )
    persisted = repr(database.idempotency) + repr(database.outbox)
    assert secret not in persisted
    assert "raw-secret" not in persisted
    assert database.outbox[0]["payload"] == {
        "version": 2, "reason_code": "SYSTEM_PROVIDER_CONFIGURATION_CHANGED"
    }


def test_provider_connection_result_may_return_endpoint_but_not_credentials() -> None:
    repository = PostgresProviderAdminMutationRepository(Store(SharedDatabase()))

    repository._validate_safe_mapping({
        "connection_id": "media-bridge", "base_url": "http://127.0.0.1:8642/v1",
    })

    with pytest.raises(ProviderConnectionAdminError, match="^PROVIDER_ADMIN_RESULT_UNSAFE$"):
        repository._validate_safe_mapping({"connection_id": "media-bridge", "api_key": "secret"})


def test_connection_projection_separates_catalog_from_allowed_models() -> None:
    database = SharedDatabase()
    database.system_connection = {
        "connection_id": "ollama-lan", "provider_code": "OLLAMA", "display_name": "LAN Ollama",
        "base_url": "http://ollama.internal:11434", "enabled": True,
        "encrypted_credential": None, "credential_version": 0,
        "verification_status": "verified", "verified_at": None, "version": 2,
        "catalog_status": "ready", "catalog_version": 3,
        "access_mode": "public", "credential_requirement": "none", "short_code": "OL", "adapter_type": "OLLAMA",
    }
    database.models = [("qwen3", ["text_generation"], ["text_generation"], False, "ready", 3)]
    service = PostgresProviderConnectionService(
        Store(database), ProviderCredentialCipher(bytes(range(32)), encryption_key_version=1),
    )

    item = next(item for item in service.list_connections(context()) if item["connection_id"] == "ollama-lan")

    assert (item["access_mode"], item["credential_requirement"], item["short_code"]) == ("public", "none", "OL")
    assert item["allowed_model_ids"] == ["qwen3"]
    assert item["provider_name"] == "OLLAMA"
    assert [model["model_id"] for model in item["models"]] == ["qwen3"]


def test_same_idempotency_key_replays_same_credential_and_rejects_different_credential() -> None:
    cipher = ProviderCredentialCipher(b"m" * 32, encryption_key_version=1)

    def payload(secret: str) -> dict[str, object]:
        return normalized_connection_fingerprint_payload(
            connection_id="ollama-lan", provider_code="OLLAMA", display_name="LAN Ollama",
            base_url="http://ollama.internal:11434", enabled=True, expected_version=0,
            logical_model_ids=("qwen3",),
            credential_fingerprint=cipher.idempotency_fingerprint("ollama-lan", secret.encode()),
        )

    database = SharedDatabase()
    first = PostgresProviderAdminMutationRepository(Store(database))
    result, replayed = first.run(
        context(), operation="provider_connection.create", idempotency_key="provider-create-0001",
        fingerprint_payload=payload("first-secret"), mutation=lambda _connection: {"version": 1},
        audit=ProviderAdminAudit("provider_connection.created", "provider_connection", "ollama-lan", 1),
    )
    retry, was_replayed = PostgresProviderAdminMutationRepository(Store(database)).run(
        context(), operation="provider_connection.create", idempotency_key="provider-create-0001",
        fingerprint_payload=payload("first-secret"), mutation=lambda _connection: {"version": 2},
        audit=ProviderAdminAudit("provider_connection.created", "provider_connection", "ollama-lan", 1),
    )

    assert (result, replayed) == ({"version": 1}, False)
    assert (retry, was_replayed) == ({"version": 1}, True)
    with pytest.raises(ProviderConnectionAdminError, match="^IDEMPOTENCY_KEY_REUSED$"):
        PostgresProviderAdminMutationRepository(Store(database)).run(
            context(), operation="provider_connection.create", idempotency_key="provider-create-0001",
            fingerprint_payload=payload("different-secret"), mutation=lambda _connection: {"version": 3},
            audit=ProviderAdminAudit("provider_connection.created", "provider_connection", "ollama-lan", 1),
        )

    persisted = repr(database.idempotency) + repr(database.outbox)
    assert "first-secret" not in persisted
    assert "different-secret" not in persisted


class CapturingMutationRepository:
    def __init__(self) -> None:
        self.payloads: list[dict[str, object]] = []

    def run(self, _context, **kwargs):
        self.payloads.append(dict(kwargs["fingerprint_payload"]))
        return {"version": 1}, False


def test_connection_service_fingerprints_replaced_credentials_but_omits_preserved_credential() -> None:
    cipher = ProviderCredentialCipher(b"s" * 32, encryption_key_version=1)
    service = PostgresProviderConnectionService(Store(SharedDatabase()), cipher)
    capture = CapturingMutationRepository()
    service._mutations = capture

    first_command = ProviderConnectionCreateCommand(
        connection_id="ollama-lan", provider_code="OLLAMA", display_name="LAN Ollama",
        base_url="http://ollama.internal:11434", credential="first-secret",
        logical_model_ids=("qwen3",), enabled=True, expected_version=0,
        access_mode="public", credential_requirement="none", short_code="OL", adapter_type="OLLAMA",
    )
    changed_command = ProviderConnectionCreateCommand(
        connection_id="ollama-lan", provider_code="OLLAMA", display_name="LAN Ollama",
        base_url="http://ollama.internal:11434", credential="different-secret",
        logical_model_ids=("qwen3",), enabled=True, expected_version=0,
        access_mode="public", credential_requirement="none", short_code="OL", adapter_type="OLLAMA",
    )
    preserved_command = ProviderConnectionUpdateCommand(
        display_name="LAN Ollama", base_url="http://ollama.internal:11434", credential=None,
        logical_model_ids=("qwen3",), enabled=True, expected_version=1,
        access_mode="public", credential_requirement="none", short_code="OL", adapter_type="OLLAMA",
    )
    replaced_command = ProviderConnectionUpdateCommand(
        display_name="LAN Ollama", base_url="http://ollama.internal:11434", credential="update-secret",
        logical_model_ids=("qwen3",), enabled=True, expected_version=1,
        access_mode="public", credential_requirement="none", short_code="OL", adapter_type="OLLAMA",
    )
    assert "first-secret" not in repr(first_command)
    assert "different-secret" not in repr(changed_command)
    assert "update-secret" not in repr(replaced_command)

    service.create_connection(
        context(), first_command, "provider-create-0002",
    )
    service.create_connection(
        context(), changed_command, "provider-create-0002",
    )
    service.update_connection(
        context(), "ollama-lan", preserved_command, "provider-update-0004",
    )
    service.update_connection(
        context(), "ollama-lan", replaced_command, "provider-update-0005",
    )

    first, changed, preserved, replaced = capture.payloads
    assert first["credential_action"] == "replace"
    assert first["credential_fingerprint"] != changed["credential_fingerprint"]
    assert "first-secret" not in repr(first)
    assert "different-secret" not in repr(changed)
    assert preserved["credential_action"] == "preserve"
    assert "credential_fingerprint" not in preserved
    assert replaced["credential_action"] == "replace"
    assert "credential_fingerprint" in replaced
    assert "update-secret" not in repr(replaced)


def test_delete_connection_removes_only_credential_and_replays_without_second_mutation() -> None:
    database = SharedDatabase()
    database.system_connection = {
        "connection_id": "ollama-lan", "provider_code": "OLLAMA", "display_name": "LAN Ollama",
        "base_url": "http://ollama.internal:11434", "enabled": True,
        "encrypted_credential": b"sealed", "credential_nonce": b"nonce",
        "encryption_key_version": 1, "credential_schema_version": 1,
        "credential_version": 4, "verification_status": "verified",
        "verified_at": None, "version": 7, "catalog_status": "ready", "catalog_version": 3,
        "access_mode": "public", "credential_requirement": "none", "short_code": "OL", "adapter_type": "OLLAMA",
    }
    database.models = [("qwen3", ["text_generation"], ["text_generation"], False, "ready", 3)]
    service = PostgresProviderConnectionService(
        Store(database), ProviderCredentialCipher(b"d" * 32, encryption_key_version=1),
    )

    result, replayed = service.delete_credential(
        context(), "ollama-lan", 7, "provider-credential-delete-0001",
    )
    retry, was_replayed = service.delete_credential(
        context(), "ollama-lan", 7, "provider-credential-delete-0001",
    )

    assert replayed is False
    assert was_replayed is True
    assert retry == result
    assert database.connection_deleted is False
    assert database.system_connection["base_url"] == "http://ollama.internal:11434"
    assert database.models == [("qwen3", ["text_generation"], ["text_generation"], False, "ready", 3)]
    assert result["configured"] is False
    assert result["credential_version"] == 0
    assert result["verification_status"] == "unverified"
    assert result["version"] == 8
    assert result["catalog_version"] == 3
    assert len(database.outbox) == 1


def test_connection_delete_reports_references_without_deleting_any_data() -> None:
    database = SharedDatabase()
    database.system_connection = {
        "connection_id": "ollama-lan", "provider_code": "OLLAMA", "display_name": "LAN Ollama",
        "base_url": "http://ollama.internal:11434", "enabled": True,
        "encrypted_credential": None, "credential_version": 0,
        "verification_status": "verified", "verified_at": None, "version": 7,
        "catalog_status": "ready", "catalog_version": 3,
        "access_mode": "public", "credential_requirement": "none", "short_code": "OL", "adapter_type": "OLLAMA",
    }

    class ReferenceConnection(Connection):
        def execute(self, sql, params=()):
            if sql.startswith("SELECT version FROM system_provider_connections"):
                return Cursor((7,))
            if sql.startswith("SELECT count(*) FROM user_provider_credentials"):
                return Cursor((2,))
            if sql.startswith("SELECT count(*) FROM workspace_model_defaults"):
                return Cursor((1,))
            return super().execute(sql, params)

    class ReferenceStore(Store):
        @contextmanager
        def _transaction(self, _context):
            yield ReferenceConnection(database)

    service = PostgresProviderConnectionService(
        ReferenceStore(database), ProviderCredentialCipher(bytes(range(32)), encryption_key_version=1),
    )
    with pytest.raises(ProviderConnectionAdminError, match="^PROVIDER_CONNECTION_REFERENCED$") as caught:
        service.delete_connection(context(), "ollama-lan", 7, "delete-0001")

    assert caught.value.references == {"user_credentials": 2, "workspace_defaults": 1}
    assert database.connection_deleted is False


def test_catalog_refresh_keeps_catalog_history_and_admin_allowed_models() -> None:
    class CapturingConnection:
        def __init__(self) -> None:
            self.calls = []

        def execute(self, sql, params=()):
            self.calls.append((" ".join(sql.split()), params))
            return Cursor()

    connection = CapturingConnection()
    model = DiscoveredModel(
        connection_id="upstage-primary", provider_code="UPSTAGE",
        model_id="information-extract", reported_capabilities=("text_generation",),
        routing_owner="provider", daon_fallback_allowed=True,
    )

    PostgresProviderConnectionService._replace_models(
        connection, context(), "upstage-primary", (model,), 8,
    )

    assert not any("DELETE FROM system_provider_models" in sql for sql, _ in connection.calls)
    assert not any("system_provider_allowed_models" in sql for sql, _ in connection.calls)
    upsert = connection.calls[0][0]
    assert "ON CONFLICT (connection_id,model_id) DO UPDATE" in upsert
    assert "WHEN system_provider_models.override_applied" in upsert
    assert "THEN system_provider_models.effective_capabilities" in upsert


def test_connection_prepare_probes_without_discovering_catalog(monkeypatch) -> None:
    calls = []

    class Adapter:
        def verify(self, _profile, _credential):
            calls.append("verify")

        def discover_models(self, _profile, _credential):
            calls.append("discover")
            return [DiscoveredModel(
                connection_id="ollama-lan", provider_code="OLLAMA", model_id="qwen3",
                reported_capabilities=("text_generation",), routing_owner="provider",
                daon_fallback_allowed=True,
            )]

    class Registry:
        def __init__(self, **_kwargs):
            pass

        def adapter(self, _provider_code):
            return Adapter()

    monkeypatch.setattr(provider_connection_admin_module, "AdapterRegistry", Registry)
    service = PostgresProviderConnectionService(
        Store(SharedDatabase()), ProviderCredentialCipher(b"p" * 32, encryption_key_version=1),
    )

    _profile, _sealed, models = service._prepare(
        connection_id="ollama-lan", provider_code="OLLAMA", display_name="LAN Ollama",
        base_url="http://ollama.internal:11434", credential=None, logical_model_ids=(),
        enabled=True, version=1, discover_models=False,
    )

    assert models == ()
    assert calls == ["verify"]


def test_connection_prepare_allows_pending_private_connection_without_calling_provider(monkeypatch) -> None:
    calls = []

    class Adapter:
        def verify(self, _profile, _credential):
            calls.append("verify")

        def discover_models(self, _profile, _credential):
            calls.append("discover")
            return ()

    class Registry:
        def __init__(self, **_kwargs):
            pass

        def adapter(self, _provider_code):
            return Adapter()

    monkeypatch.setattr(provider_connection_admin_module, "AdapterRegistry", Registry)
    service = PostgresProviderConnectionService(
        Store(SharedDatabase()), ProviderCredentialCipher(b"p" * 32, encryption_key_version=1),
    )

    _profile, _sealed, models = service._prepare(
        connection_id="omniroute", provider_code="OMNIROUTE", display_name="OmniRoute",
        base_url="http://localhost:20128/v1", credential=None, logical_model_ids=(),
        enabled=True, version=1, discover_models=False, verify_required=False,
    )

    assert models == ()
    assert calls == []


def test_allowed_models_reject_removing_workspace_default_before_mutation() -> None:
    calls = []

    class ReferenceConnection:
        def execute(self, sql, _params=()):
            calls.append(sql)
            if sql.startswith("SELECT model_id FROM system_provider_models"):
                return Cursor(rows=[("solar-pro4",)])
            if sql.startswith("SELECT count(*) FROM workspace_model_defaults"):
                return Cursor((1,))
            return Cursor()

    with pytest.raises(ProviderConnectionAdminError, match="^PROVIDER_MODEL_DEFAULT_REFERENCED$"):
        PostgresProviderConnectionService._set_allowed_models(
            ReferenceConnection(), context(), "upstage-primary", ("solar-pro4",),
        )

    assert not any(sql.startswith("DELETE FROM system_provider_allowed_models") for sql in calls)
