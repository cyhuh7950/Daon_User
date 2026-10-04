from __future__ import annotations

from contextlib import contextmanager
from copy import deepcopy
from dataclasses import replace

import pytest
import daon_user_api.provider_connection_admin as provider_connection_admin_module

from daon_user_api.provider_connection_admin import (
    ProviderAdminAudit,
    ProviderConnectionAdminContext,
    ProviderConnectionAdminError,
    ProviderConnectionCreateCommand,
    ProviderConnectionUpdateCommand,
    ProviderCredentialReplaceCommand,
    PostgresProviderAdminMutationRepository,
    PostgresProviderConnectionService,
    normalized_connection_fingerprint_payload,
    validate_connection_policy,
)
from daon_user_api.provider_credentials import ProviderCredentialCipher
from daon_user_api.provider_catalog import DiscoveredModel
from daon_user_api.provider_connection_adapters import AdapterRegistry, TransportResponse


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
            catalog_status = max((item[4] for item in self.database.models), default=None)
            return Cursor(rows=[(
                record["connection_id"], record["provider_code"], record["display_name"], record["base_url"], record["enabled"],
                record["encrypted_credential"], record["credential_version"], record["verification_status"],
                record["verified_at"], record["version"], catalog_status, record["catalog_version"],
                record["access_mode"], record["credential_requirement"], record["short_code"], record["adapter_type"],
                record.get("provider_name", record["provider_code"]),
                record.get("auto_model_id"),
            )])
        if normalized.startswith("SELECT model_id,reported_capabilities,effective_capabilities"):
            return Cursor(rows=[(*item[:6], item[6] if len(item) > 6 else "upstream") for item in self.database.models])
        if normalized.startswith("SELECT model_id FROM system_provider_allowed_models"):
            return Cursor(rows=[(item[0],) for item in self.database.models])
        raise AssertionError(f"unexpected SQL: {normalized}")


class Store:
    def __init__(self, database: SharedDatabase) -> None:
        self.database = database

    @contextmanager
    def _transaction(self, context):
        yield Connection(self.database)


class CompatibleDatabase(SharedDatabase):
    def __init__(self) -> None:
        super().__init__()
        self.allowed_model_ids: list[str] = []
        self.user_credentials: list[dict[str, object]] = []
        self.default_model_ids: list[str] = []


class CompatibleConnection(Connection):
    def execute(self, sql, params=()):
        normalized = " ".join(sql.split())
        record = self.database.system_connection
        if normalized.startswith("SELECT 1 FROM system_provider_connections"):
            return Cursor((1,) if record and record["connection_id"] == params[0] else None)
        if normalized.startswith("SELECT connection_id FROM system_provider_connections WHERE short_code"):
            return Cursor((record["connection_id"],) if record and record["short_code"] == params[0]
                          and record["connection_id"] != params[1] else None)
        if normalized.startswith("SELECT connection_id,provider_code,display_name,base_url,encrypted_credential"):
            if record is None or record["connection_id"] != params[0]:
                return Cursor(None)
            return Cursor(tuple(record.get(key) for key in (
                "connection_id", "provider_code", "display_name", "base_url", "encrypted_credential",
                "credential_nonce", "encryption_key_version", "credential_schema_version", "credential_version",
                "enabled", "verification_status", "verified_at", "version", "access_mode",
                "credential_requirement", "short_code", "adapter_type", "provider_name", "auto_model_id",
            )))
        if normalized.startswith("INSERT INTO system_provider_connections"):
            self.database.system_connection = dict(zip((
                "connection_id", "provider_code", "display_name", "base_url", "encrypted_credential",
                "credential_nonce", "encryption_key_version", "credential_schema_version", "credential_version",
                "enabled", "verification_status", "verified_at", "updated_by", "trace_id", "policy_version",
                "access_mode", "credential_requirement", "short_code", "adapter_type", "provider_name", "auto_model_id",
            ), params))
            self.database.system_connection.update(version=1, catalog_status="stale", catalog_version=0)
            return Cursor()
        if normalized.startswith("UPDATE system_provider_connections SET display_name="):
            if record is None or record["connection_id"] != params[-2] or record["version"] != params[-1]:
                return Cursor(None)
            record.update(dict(zip((
                "display_name", "base_url", "encrypted_credential", "credential_nonce",
                "encryption_key_version", "credential_schema_version", "credential_version", "enabled",
                "verification_status", "verified_at", "access_mode", "credential_requirement", "short_code",
                "adapter_type", "provider_name", "auto_model_id", "updated_by", "trace_id", "policy_version",
            ), params[:-2])))
            record["version"] += 1
            return Cursor((record["version"],))
        if normalized.startswith("UPDATE system_provider_connections SET encrypted_credential=%s"):
            if record is None or record["connection_id"] != params[-2] or record["version"] != params[-1]:
                return Cursor(None)
            record.update(dict(zip((
                "encrypted_credential", "credential_nonce", "encryption_key_version",
                "credential_schema_version", "credential_version",
            ), params[:5])))
            record["verification_status"] = "verified"
            record["version"] += 1
            return Cursor((record["version"],))
        if normalized.startswith("UPDATE system_provider_connections SET version=version+1"):
            if record is None or record["connection_id"] != params[-2] or record["version"] != params[-1]:
                return Cursor(None)
            record["version"] += 1
            return Cursor((record["version"],))
        if normalized.startswith("UPDATE system_provider_connections SET verification_status='verified'"):
            if record is None or record["connection_id"] != params[-2] or record["version"] != params[-1]:
                return Cursor(None)
            record["verification_status"] = "verified"
            record["version"] += 1
            return Cursor((record["version"],))
        if normalized.startswith("INSERT INTO system_provider_models"):
            existing = next((item for item in self.database.models if item[0] == params[1]), None)
            catalog_origin = params[5]
            if (existing is not None and params[7]
                    and (existing[6] if len(existing) > 6 else "upstream") == "upstream"
                    and catalog_origin == "logical"):
                catalog_origin = "upstream"
            self.database.models = [item for item in self.database.models if item[0] != params[1]]
            self.database.models.append((params[1], params[2], params[3], False, "ready", params[4], catalog_origin))
            self.database.system_connection["catalog_status"] = "ready"
            self.database.system_connection["catalog_version"] = params[4]
            return Cursor()
        if normalized.startswith("UPDATE system_provider_models SET catalog_status='stale'"):
            self.database.models = [
                (item[0], item[1], item[2], item[3], item[4] if item[0] in params[1] else "stale", item[5], item[6] if len(item) > 6 else "upstream")
                for item in self.database.models
            ]
            return Cursor()
        if normalized.startswith("UPDATE system_provider_models SET reported_capabilities=%s"):
            self.database.models = [
                (item[0], params[0], params[1], False, "stale", params[2], item[6] if len(item) > 6 else "upstream") if item[0] in params[4] else item
                for item in self.database.models
            ]
            return Cursor()
        if normalized.startswith("SELECT model_id FROM system_provider_models WHERE connection_id=%s AND model_id=ANY"):
            return Cursor(rows=[(item[0],) for item in self.database.models if item[0] in params[1]])
        if normalized.startswith("SELECT model_id FROM system_provider_models WHERE connection_id=%s ORDER BY"):
            return Cursor(rows=[(item[0],) for item in self.database.models])
        if normalized.startswith("SELECT count(*) FROM workspace_model_defaults"):
            if "AND model_id=%s" in normalized:
                return Cursor((sum(item == params[1] for item in self.database.default_model_ids),))
            return Cursor((0,))
        if normalized.startswith("DELETE FROM system_provider_allowed_models"):
            self.database.allowed_model_ids = [item for item in self.database.allowed_model_ids if item in params[1]]
            return Cursor()
        if normalized.startswith("INSERT INTO system_provider_allowed_models"):
            if params[1] not in self.database.allowed_model_ids:
                self.database.allowed_model_ids.append(params[1])
            return Cursor()
        if normalized.startswith("SELECT model_id FROM system_provider_allowed_models"):
            return Cursor(rows=[(item,) for item in self.database.allowed_model_ids])
        if normalized.startswith("UPDATE user_provider_credentials SET verification_status='unverified'"):
            for item in self.database.user_credentials:
                if item["connection_id"] == params[0] and item["verification_status"] == "verified":
                    item["verification_status"] = "unverified"
            return Cursor()
        return super().execute(sql, params)


class CompatibleStore(Store):
    @contextmanager
    def _transaction(self, context):
        snapshot = deepcopy(self.database.__dict__)
        try:
            yield CompatibleConnection(self.database)
        except Exception:
            self.database.__dict__.clear()
            self.database.__dict__.update(snapshot)
            raise


class HealthConnection(CompatibleConnection):
    def execute(self, sql, params=()):
        normalized = " ".join(sql.split())
        if normalized.startswith("UPDATE system_provider_connections SET verification_status="):
            self.database.health_updates.append(normalized)
            self.database.system_connection["verification_status"] = (
                "verified" if "verification_status='verified'" in normalized else "failed"
            )
            return Cursor()
        return super().execute(sql, params)


class HealthStore(CompatibleStore):
    @contextmanager
    def _transaction(self, context):
        snapshot = deepcopy(self.database.__dict__)
        try:
            yield HealthConnection(self.database)
        except Exception:
            self.database.__dict__.clear()
            self.database.__dict__.update(snapshot)
            raise


class FixtureTransport:
    def __init__(self) -> None:
        self.requests = []
        self.responses = {}

    def request(self, method, url, headers, body, timeout_seconds, *, follow_redirects):
        self.requests.append((method, url, dict(headers), body, timeout_seconds, follow_redirects))
        response = self.responses.get((url, None if body is None else body.get("model")),
                                      self.responses.get(url))
        if isinstance(response, Exception):
            raise response
        if response is None:
            raise AssertionError("fixture response missing")
        return response


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


def compatible_service(monkeypatch, transport: FixtureTransport):
    monkeypatch.setattr(
        provider_connection_admin_module, "AdapterRegistry",
        lambda **kwargs: AdapterRegistry(transport, **kwargs),
    )
    database = CompatibleDatabase()
    service = PostgresProviderConnectionService(
        CompatibleStore(database), ProviderCredentialCipher(b"t" * 32, encryption_key_version=1),
    )
    return service, database


def durable_state(database: CompatibleDatabase):
    return deepcopy((database.system_connection, database.models, database.allowed_model_ids,
                     database.idempotency, [item["payload"] for item in database.outbox]))


def custom_command(*, adapter_type="openai_compatible", access_mode="public", credential="fixture-public-key",
                   test_credential=None, allowed_model_ids=("manual-model",)):
    return ProviderConnectionCreateCommand(
        connection_id="custom-1", provider_code="CUSTOM", display_name="Custom primary",
        base_url="https://models.example/v1", credential=credential,
        logical_model_ids=(), enabled=True, expected_version=0, access_mode=access_mode,
        credential_requirement="required", short_code="CU", adapter_type=adapter_type,
        allowed_model_ids=allowed_model_ids, provider_name="Example AI", test_credential=test_credential,
    )


def route_command(*, model_ids=("auto",)):
    return ProviderConnectionCreateCommand(
        connection_id="route-1", provider_code="OMNIROUTE", display_name="OmniRoute",
        base_url="https://omniroute.example/v1", credential="fixture-route-key",
        logical_model_ids=model_ids, enabled=True, expected_version=0, access_mode="public",
        credential_requirement="required", short_code="OM", adapter_type="OMNIROUTE",
        allowed_model_ids=model_ids, provider_name="OmniRoute",
    )


def test_update_omits_auto_model_id_preserves_existing_router(monkeypatch) -> None:
    transport = FixtureTransport()
    transport.responses["https://omniroute.example/v1/responses"] = TransportResponse(200, {"output_text": "ready"})
    service, database = compatible_service(monkeypatch, transport)
    created, _ = service.create_connection(context(), route_command(), "route-auto-create")
    assert created["auto_model_id"] == "auto"
    updated, _ = service.update_connection(context(), "route-1", ProviderConnectionUpdateCommand(
        display_name="OmniRoute renamed", base_url="https://omniroute.example/v1", credential=None,
        logical_model_ids=("auto",), enabled=True, expected_version=1,
        access_mode="public", credential_requirement="required", short_code="OM",
        adapter_type="OMNIROUTE", allowed_model_ids=("auto",), provider_name="OmniRoute",
    ), "route-auto-rename")
    assert updated["auto_model_id"] == "auto"
    assert database.system_connection["auto_model_id"] == "auto"


def test_named_custom_is_not_router_until_admin_marks_it(monkeypatch) -> None:
    transport = FixtureTransport()
    service, database = compatible_service(monkeypatch, transport)
    command = replace(
        custom_command(access_mode="personal", credential=None, allowed_model_ids=()),
        display_name="Media Bridge Server", provider_name="Media Bridge Server",
    )
    saved, _ = service.create_connection(context(), command, "media-bridge-named-create")
    assert saved["auto_model_id"] is None
    assert database.system_connection["auto_model_id"] is None
    assert saved["allowed_model_ids"] == []


def test_fixed_router_rejects_changed_auto_model_id_without_mutation(monkeypatch) -> None:
    transport = FixtureTransport()
    transport.responses["https://omniroute.example/v1/responses"] = TransportResponse(200, {"output_text": "ready"})
    service, database = compatible_service(monkeypatch, transport)
    service.create_connection(context(), route_command(), "route-fixed-auto-create")
    before = durable_state(database)
    with pytest.raises(ProviderConnectionAdminError, match="^PROVIDER_AUTO_MODEL_ID_INVALID$"):
        service.update_connection(context(), "route-1", ProviderConnectionUpdateCommand(
            display_name="OmniRoute", base_url="https://omniroute.example/v1", credential=None,
            logical_model_ids=("auto",), enabled=True, expected_version=1,
            access_mode="public", credential_requirement="required", short_code="OM",
            adapter_type="OMNIROUTE", allowed_model_ids=("auto",), provider_name="OmniRoute",
            auto_model_id="other-auto", auto_model_id_specified=True,
        ), "route-fixed-auto-tamper")
    assert durable_state(database) == before


def test_custom_auto_requires_openai_compatible_and_valid_id(monkeypatch) -> None:
    transport = FixtureTransport()
    service, database = compatible_service(monkeypatch, transport)
    for command in (
        replace(custom_command(adapter_type="anthropic_compatible"), auto_model_id="auto"),
        replace(custom_command(), auto_model_id="bad model"),
    ):
        with pytest.raises(ProviderConnectionAdminError, match="^PROVIDER_AUTO_MODEL_ID_INVALID$"):
            service.create_connection(context(), command, "custom-auto-invalid")
    assert database.system_connection is None


def test_custom_auto_probe_failure_preserves_key_allowlist_and_default(monkeypatch) -> None:
    transport = FixtureTransport()
    url = "https://models.example/v1/chat/completions"
    transport.responses[(url, "manual-model")] = TransportResponse(
        200, {"choices": [{"message": {"content": "ready"}}]},
    )
    service, database = compatible_service(monkeypatch, transport)
    service.create_connection(context(), custom_command(), "custom-auto-probe-seed")
    before = durable_state(database)
    transport.responses[(url, "auto")] = TransportResponse(200, {})
    with pytest.raises(ProviderConnectionAdminError, match="^PROVIDER_PROBE_RESPONSE_INVALID$"):
        service.update_connection(context(), "custom-1", ProviderConnectionUpdateCommand(
            display_name="Custom primary", base_url="https://models.example/v1", credential=None,
            logical_model_ids=(), enabled=True, expected_version=1,
            access_mode="public", credential_requirement="required", short_code="CU",
            adapter_type="openai_compatible", allowed_model_ids=("manual-model",),
            provider_name="Example AI", auto_model_id="auto", auto_model_id_specified=True,
        ), "custom-auto-probe-failed")
    assert durable_state(database) == before
    assert database.system_connection["auto_model_id"] is None


def test_custom_auto_success_adds_logical_catalog_without_granting_allowlist(monkeypatch) -> None:
    transport = FixtureTransport()
    url = "https://models.example/v1/chat/completions"
    transport.responses[(url, "manual-model")] = TransportResponse(
        200, {"choices": [{"message": {"content": "ready"}}]},
    )
    transport.responses[(url, "auto")] = TransportResponse(
        200, {"choices": [{"message": {"content": "ready"}}]},
    )
    service, _ = compatible_service(monkeypatch, transport)
    saved, _ = service.create_connection(
        context(), replace(custom_command(), auto_model_id="auto"), "custom-auto-created",
    )
    assert saved["auto_model_id"] == "auto"
    assert saved["allowed_model_ids"] == ["manual-model"]
    assert next(item for item in saved["models"] if item["model_id"] == "auto")["catalog_origin"] == "logical"
    assert [item[3]["model"] for item in transport.requests] == ["manual-model", "auto"]


def test_custom_auto_configuration_does_not_count_unselected_auto_as_fifth_allowed(monkeypatch) -> None:
    transport = FixtureTransport()
    url = "https://models.example/v1/chat/completions"
    for model_id in ("m1", "m2", "m3", "m4", "auto"):
        transport.responses[(url, model_id)] = TransportResponse(
            200, {"choices": [{"message": {"content": "ready"}}]},
        )
    service, _ = compatible_service(monkeypatch, transport)
    saved, _ = service.create_connection(context(), replace(
        custom_command(allowed_model_ids=("m1", "m2", "m3", "m4")), auto_model_id="auto",
    ), "custom-auto-four-allowed")
    assert saved["allowed_model_ids"] == ["m1", "m2", "m3", "m4"]
    assert [item[3]["model"] for item in transport.requests] == ["m1", "m2", "m3", "m4", "auto"]


def test_removing_router_flag_with_referenced_auto_rejects_without_mutation(monkeypatch) -> None:
    transport = FixtureTransport()
    transport.responses[("https://models.example/v1/chat/completions", "auto")] = TransportResponse(
        200, {"choices": [{"message": {"content": "ready"}}]},
    )
    service, database = compatible_service(monkeypatch, transport)
    service.create_connection(context(), replace(
        custom_command(allowed_model_ids=("auto",)), auto_model_id="auto",
    ), "custom-auto-default-seed")
    database.default_model_ids = ["auto"]
    before = durable_state(database)
    transport.requests.clear()
    with pytest.raises(ProviderConnectionAdminError, match="^PROVIDER_AUTO_MODEL_REFERENCED$"):
        service.update_connection(context(), "custom-1", ProviderConnectionUpdateCommand(
            display_name="Custom primary", base_url="https://models.example/v1", credential=None,
            logical_model_ids=(), enabled=True, expected_version=1,
            access_mode="public", credential_requirement="required", short_code="CU",
            adapter_type="openai_compatible", allowed_model_ids=("auto",),
            provider_name="Example AI", auto_model_id=None, auto_model_id_specified=True,
        ), "custom-auto-default-remove")
    assert durable_state(database) == before
    assert transport.requests == []


def test_openrouter_listed_auto_refresh_keeps_one_upstream_row(monkeypatch) -> None:
    transport = FixtureTransport()
    transport.responses["https://openrouter.ai/api/v1/models"] = TransportResponse(
        200, {"data": [{"id": "openrouter/auto", "architecture": {"output_modalities": ["text"]}}]},
    )
    service, _ = compatible_service(monkeypatch, transport)
    command = replace(
        route_command(model_ids=("openrouter/auto",)),
        connection_id="openrouter-1", provider_code="OPENROUTER", display_name="OpenRouter",
        base_url="https://openrouter.ai/api/v1", short_code="OR", adapter_type="OPENROUTER",
        provider_name="OpenRouter",
    )
    created, _ = service.create_connection(context(), command, "openrouter-auto-seed")
    created_auto_rows = [item for item in created["models"] if item["model_id"] == "openrouter/auto"]
    assert len(created_auto_rows) == 1
    assert created_auto_rows[0]["catalog_origin"] == "upstream"
    refreshed, _ = service.refresh_catalog(context(), "openrouter-1", 1, "openrouter-auto-refresh")
    auto_rows = [item for item in refreshed["models"] if item["model_id"] == "openrouter/auto"]
    assert len(auto_rows) == 1
    assert auto_rows[0]["catalog_origin"] == "upstream"
    assert refreshed["allowed_model_ids"] == ["openrouter/auto"]


def test_openrouter_auto_counts_toward_four_allowed_models(monkeypatch) -> None:
    transport = FixtureTransport()
    service, database = compatible_service(monkeypatch, transport)
    command = replace(
        route_command(model_ids=("openrouter/auto", "m1", "m2", "m3", "m4")),
        connection_id="openrouter-1", provider_code="OPENROUTER", display_name="OpenRouter",
        base_url="https://openrouter.ai/api/v1", short_code="OR", adapter_type="OPENROUTER",
        provider_name="OpenRouter",
    )
    with pytest.raises(ProviderConnectionAdminError, match="^PROVIDER_MODEL_IDS_INVALID$"):
        service.create_connection(context(), command, "openrouter-auto-fifth")
    assert database.system_connection is None
    assert transport.requests == []


@pytest.mark.parametrize("model_ids", [("auto",), ("cc/claude-sonnet",), ("combo-writing",)])
def test_omniroute_create_uses_selected_model_or_auto_for_probe_and_allowlist(monkeypatch, model_ids) -> None:
    transport = FixtureTransport()
    transport.responses["https://omniroute.example/v1/responses"] = TransportResponse(200, {"output_text": "ready"})
    service, database = compatible_service(monkeypatch, transport)

    saved, _ = service.create_connection(context(), route_command(model_ids=model_ids), "route-create-001")

    chosen = list(model_ids or ("auto",))
    assert saved["allowed_model_ids"] == chosen
    assert {item["model_id"] for item in saved["models"]} == set(chosen) | {"auto"}
    assert [(item[0], item[1], item[3]["model"]) for item in transport.requests] == [
        ("POST", "https://omniroute.example/v1/responses", chosen[0])
    ]
    assert "fixture-route-key" not in repr(saved) + repr(database.idempotency) + repr(database.outbox)


def test_omniroute_explicit_model_takes_precedence_over_previous_auto(monkeypatch) -> None:
    transport = FixtureTransport()
    transport.responses["https://omniroute.example/v1/responses"] = TransportResponse(200, {"output_text": "ready"})
    service, _ = compatible_service(monkeypatch, transport)

    saved, _ = service.create_connection(
        context(), route_command(model_ids=("auto", "cc/claude-sonnet")), "route-create-explicit-001",
    )

    assert saved["allowed_model_ids"] == ["auto", "cc/claude-sonnet"]
    assert [item["model_id"] for item in saved["models"]] == ["auto", "cc/claude-sonnet"]
    assert [(item[0], item[3]["model"]) for item in transport.requests] == [
        ("POST", "auto"), ("POST", "cc/claude-sonnet"),
    ]


def test_omniroute_auto_plus_four_explicit_counts_as_four_probes(monkeypatch) -> None:
    transport = FixtureTransport()
    transport.responses["https://omniroute.example/v1/responses"] = TransportResponse(200, {"output_text": "ready"})
    service, _ = compatible_service(monkeypatch, transport)

    with pytest.raises(ProviderConnectionAdminError, match="^PROVIDER_MODEL_IDS_INVALID$"):
        service.create_connection(
            context(), route_command(model_ids=("auto", "m1", "m2", "m3", "m4")),
            "route-auto-plus-four-create",
        )
    assert transport.requests == []


def test_omniroute_unlisted_auto_survives_refresh_and_keeps_allowlist(monkeypatch) -> None:
    transport = FixtureTransport()
    transport.responses["https://omniroute.example/v1/responses"] = TransportResponse(200, {"output_text": "ready"})
    transport.responses["https://omniroute.example/v1/models"] = TransportResponse(
        200, {"data": [{"id": "cc/claude-sonnet"}]},
    )
    service, database = compatible_service(monkeypatch, transport)
    service.create_connection(context(), route_command(model_ids=("auto",)), "route-auto-refresh-create")
    refreshed, _ = service.refresh_catalog(context(), "route-1", 1, "route-auto-refresh")
    auto = next(item for item in refreshed["models"] if item["model_id"] == "auto")
    assert auto["catalog_status"] == "ready"
    assert auto["catalog_origin"] == "logical"
    assert refreshed["allowed_model_ids"] == ["auto"]
    assert database.allowed_model_ids == ["auto"]


def test_omniroute_unselected_model_uses_provider_default_without_granting_auto(monkeypatch) -> None:
    transport = FixtureTransport()
    transport.responses["https://omniroute.example/v1/responses"] = TransportResponse(200, {"output_text": "ready"})
    service, _ = compatible_service(monkeypatch, transport)
    saved, _ = service.create_connection(context(), route_command(model_ids=()), "route-unselected-create")
    assert saved["allowed_model_ids"] == []
    assert saved["auto_model_id"] == "auto"
    assert next(item for item in saved["models"] if item["model_id"] == "auto")["catalog_origin"] == "logical"
    assert [item[3] for item in transport.requests] == [
        {"input": "Reply OK.", "max_output_tokens": 16, "stream": False}
    ]


def test_omniroute_refresh_reads_models_without_verifying_or_expanding_allowlist(monkeypatch) -> None:
    transport = FixtureTransport()
    transport.responses["https://omniroute.example/v1/responses"] = TransportResponse(200, {"output_text": "ready"})
    transport.responses["https://omniroute.example/v1/models"] = TransportResponse(
        200, {"data": [
            {"id": "cc/claude-sonnet"}, {"id": "combo-writing"},
            {"id": "openai/whisper-1"},
            {"id": "openai/whisper-1", "type": "audio", "subtype": "transcription"},
            {"id": "embedding-only", "type": "embedding"},
            {"id": "image-only", "type": "image"},
        ]},
    )
    service, database = compatible_service(monkeypatch, transport)
    service.create_connection(context(), route_command(), "route-create-002")
    old_key = database.system_connection["encrypted_credential"]
    transport.requests.clear()

    saved, _ = service.refresh_catalog(context(), "route-1", 1, "route-refresh-001")

    assert saved["allowed_model_ids"] == ["auto"]
    assert {item["model_id"] for item in saved["models"]} == {"auto", "cc/claude-sonnet", "combo-writing"}
    assert database.system_connection["encrypted_credential"] == old_key
    assert [(item[0], item[1], item[3]) for item in transport.requests] == [
        ("GET", "https://omniroute.example/v1/models", None)
    ]


@pytest.mark.parametrize(
    ("updated_base_url", "expected_origin"),
    [
        ("https://omniroute.example/v1", "upstream"),
        ("https://alternate-omniroute.example/v1", "logical"),
    ],
)
def test_omniroute_allowing_discovered_model_tracks_origin_for_endpoint(
    monkeypatch, updated_base_url: str, expected_origin: str,
) -> None:
    transport = FixtureTransport()
    transport.responses["https://omniroute.example/v1/responses"] = TransportResponse(200, {"output_text": "ready"})
    transport.responses["https://alternate-omniroute.example/v1/responses"] = TransportResponse(
        200, {"output_text": "ready"},
    )
    transport.responses["https://omniroute.example/v1/models"] = TransportResponse(
        200, {"data": [{"id": "cc/claude-sonnet"}]},
    )
    service, database = compatible_service(monkeypatch, transport)
    service.create_connection(context(), route_command(), "route-origin-create")
    refreshed, _ = service.refresh_catalog(context(), "route-1", 1, "route-origin-refresh")
    assert next(model for model in refreshed["models"] if model["model_id"] == "cc/claude-sonnet")["catalog_origin"] == "upstream"

    updated, _ = service.update_connection(context(), "route-1", ProviderConnectionUpdateCommand(
        display_name="OmniRoute", base_url=updated_base_url, credential=None,
        logical_model_ids=("auto", "cc/claude-sonnet"), enabled=True, expected_version=2,
        access_mode="public", credential_requirement="required", short_code="OM",
        adapter_type="OMNIROUTE", allowed_model_ids=("auto", "cc/claude-sonnet"),
        provider_name="OmniRoute",
    ), "route-origin-allow")

    assert updated["allowed_model_ids"] == ["auto", "cc/claude-sonnet"]
    assert next(model for model in updated["models"] if model["model_id"] == "cc/claude-sonnet")["catalog_origin"] == expected_origin
    assert next(model for model in updated["models"] if model["model_id"] == "auto")["catalog_origin"] == "logical"
    assert database.system_connection["encrypted_credential"] is not None


@pytest.mark.parametrize("allowed_model", ["auto", "combo-writing"])
def test_omniroute_refresh_stales_missing_unallowed_but_preserves_allowed(
    monkeypatch, allowed_model: str,
) -> None:
    transport = FixtureTransport()
    transport.responses["https://omniroute.example/v1/responses"] = TransportResponse(200, {"output_text": "ready"})
    transport.responses["https://omniroute.example/v1/models"] = TransportResponse(
        200, {"data": [{"id": "old-discovered"}]},
    )
    service, database = compatible_service(monkeypatch, transport)
    service.create_connection(context(), route_command(model_ids=(allowed_model,)), f"route-stale-create-{allowed_model}")
    service.refresh_catalog(context(), "route-1", 1, f"route-stale-refresh-1-{allowed_model}")
    transport.responses["https://omniroute.example/v1/models"] = TransportResponse(
        200, {"data": [{"id": "new-discovered"}]},
    )

    saved, _ = service.refresh_catalog(context(), "route-1", 2, f"route-stale-refresh-2-{allowed_model}")

    statuses = {item["model_id"]: item["catalog_status"] for item in saved["models"]}
    assert statuses == {allowed_model: "ready", "auto": "ready", "old-discovered": "stale", "new-discovered": "ready"}
    assert saved["allowed_model_ids"] == [allowed_model]
    assert database.allowed_model_ids == [allowed_model]


@pytest.mark.parametrize("specialty_type", ["embedding", "image", "audio"])
def test_omniroute_refresh_revokes_ready_capability_for_listed_specialty_but_keeps_absent_probe(
    monkeypatch, specialty_type: str,
) -> None:
    transport = FixtureTransport()
    transport.responses["https://omniroute.example/v1/responses"] = TransportResponse(200, {"output_text": "ready"})
    transport.responses["https://omniroute.example/v1/models"] = TransportResponse(
        200, {"data": [{"id": "listed-specialty", "type": specialty_type}]},
    )
    service, database = compatible_service(monkeypatch, transport)
    service.create_connection(
        context(), route_command(model_ids=("manual-absent", "listed-specialty")),
        f"route-specialty-create-{specialty_type}",
    )
    original_key = database.system_connection["encrypted_credential"]
    transport.requests.clear()

    saved, _ = service.refresh_catalog(context(), "route-1", 1, f"route-specialty-refresh-{specialty_type}")

    models = {item["model_id"]: item for item in saved["models"]}
    assert models["manual-absent"]["catalog_status"] == "ready"
    assert models["manual-absent"]["effective_capabilities"] == ["text_generation"]
    assert saved["catalog_status"] == "ready"
    assert models["listed-specialty"]["catalog_status"] == "stale"
    assert models["listed-specialty"]["reported_capabilities"] == []
    assert models["listed-specialty"]["effective_capabilities"] == []
    assert saved["allowed_model_ids"] == ["manual-absent", "listed-specialty"]
    assert database.system_connection["encrypted_credential"] == original_key
    assert [request[0] for request in transport.requests] == ["GET"]


def test_omniroute_key_replacement_probes_only_allowed_models(monkeypatch) -> None:
    transport = FixtureTransport()
    transport.responses["https://omniroute.example/v1/responses"] = TransportResponse(200, {"output_text": "ready"})
    transport.responses["https://omniroute.example/v1/models"] = TransportResponse(
        200, {"data": [{"id": "catalog-only"}]},
    )
    service, database = compatible_service(monkeypatch, transport)
    allowed = ("cc/claude-sonnet", "combo-writing")
    service.create_connection(context(), route_command(model_ids=allowed), "route-replace-create")
    service.refresh_catalog(context(), "route-1", 1, "route-replace-refresh")
    transport.requests.clear()

    saved, _ = service.replace_credential(
        context(), "route-1", ProviderCredentialReplaceCommand("fixture-new-route-key", 2),
        "route-replace-key",
    )

    assert saved["allowed_model_ids"] == list(allowed)
    assert database.system_connection["credential_version"] == 2
    assert [request[3]["model"] for request in transport.requests] == list(allowed)


def test_omniroute_untyped_catalog_candidate_stays_unallowed_until_successful_probe(monkeypatch) -> None:
    transport = FixtureTransport()
    responses_url = "https://omniroute.example/v1/responses"
    transport.responses[responses_url] = TransportResponse(200, {"output_text": "ready"})
    transport.responses["https://omniroute.example/v1/models"] = TransportResponse(
        200, {"data": [{"id": "untyped-candidate"}]},
    )
    service, database = compatible_service(monkeypatch, transport)
    service.create_connection(context(), route_command(), "route-untyped-create")
    refreshed, _ = service.refresh_catalog(context(), "route-1", 1, "route-untyped-refresh")
    assert {item["model_id"] for item in refreshed["models"]} == {"auto", "untyped-candidate"}
    assert refreshed["allowed_model_ids"] == ["auto"]

    command = ProviderConnectionUpdateCommand(
        display_name="OmniRoute", base_url="https://omniroute.example/v1", credential=None,
        logical_model_ids=("untyped-candidate",), enabled=True, expected_version=2,
        access_mode="public", credential_requirement="required", short_code="OM",
        adapter_type="OMNIROUTE", allowed_model_ids=("untyped-candidate",), provider_name="OmniRoute",
    )
    before = durable_state(database)
    transport.responses[(responses_url, "untyped-candidate")] = TransportResponse(200, {})
    with pytest.raises(ProviderConnectionAdminError, match="^PROVIDER_PROBE_RESPONSE_INVALID$"):
        service.update_connection(context(), "route-1", command, "route-untyped-invalid")
    assert durable_state(database) == before

    transport.responses[(responses_url, "untyped-candidate")] = TransportResponse(200, {"output_text": "ready"})
    saved, _ = service.update_connection(context(), "route-1", command, "route-untyped-valid")
    assert saved["allowed_model_ids"] == ["untyped-candidate"]
    assert database.system_connection["credential_version"] == 1


def test_omniroute_health_rechecks_each_allowed_model(monkeypatch) -> None:
    transport = FixtureTransport()
    transport.responses["https://omniroute.example/v1/responses"] = TransportResponse(200, {"output_text": "ready"})
    service, database = compatible_service(monkeypatch, transport)
    service.create_connection(
        context(), route_command(model_ids=("cc/claude-sonnet", "combo-writing")),
        "route-health-create",
    )
    service._store = HealthStore(database)
    database.health_updates = []
    transport.requests.clear()

    result = service.check_active_connection(context(), "route-1")

    assert result["status"] == "verified"
    assert [request[3]["model"] for request in transport.requests] == [
        "cc/claude-sonnet", "combo-writing",
    ]


@pytest.mark.parametrize("access_mode", ["public", "personal"])
def test_omniroute_create_rejects_fifth_model_before_network_or_mutation(monkeypatch, access_mode: str) -> None:
    transport = FixtureTransport()
    transport.responses["https://omniroute.example/v1/responses"] = TransportResponse(200, {"output_text": "ready"})
    service, database = compatible_service(monkeypatch, transport)
    command = route_command(model_ids=("m1", "m2", "m3", "m4", "m5"))
    if access_mode == "personal":
        command = replace(command, credential=None, access_mode="personal")

    with pytest.raises(ProviderConnectionAdminError, match="^PROVIDER_MODEL_IDS_INVALID$"):
        service.create_connection(context(), command, f"route-too-many-create-{access_mode}")

    assert transport.requests == []
    assert database.system_connection is None
    assert database.idempotency == {}


def test_omniroute_update_rejects_fifth_model_without_changing_connection(monkeypatch) -> None:
    transport = FixtureTransport()
    transport.responses["https://omniroute.example/v1/responses"] = TransportResponse(200, {"output_text": "ready"})
    service, database = compatible_service(monkeypatch, transport)
    service.create_connection(context(), route_command(), "route-too-many-update-create")
    before = durable_state(database)
    transport.requests.clear()

    with pytest.raises(ProviderConnectionAdminError, match="^PROVIDER_MODEL_IDS_INVALID$"):
        service.update_connection(context(), "route-1", ProviderConnectionUpdateCommand(
            display_name="OmniRoute", base_url="https://omniroute.example/v1", credential=None,
            logical_model_ids=("m1", "m2", "m3", "m4", "m5"), enabled=True, expected_version=1,
            access_mode="public", credential_requirement="required", short_code="OM",
            adapter_type="OMNIROUTE", allowed_model_ids=("m1", "m2", "m3", "m4", "m5"), provider_name="OmniRoute",
        ), "route-too-many-update")

    assert durable_state(database) == before
    assert transport.requests == []


@pytest.mark.parametrize("legacy_allowed", [
    ["m1", "m2", "m3", "m4", "m5"],
    ["auto", "m1", "m2", "m3", "m4"],
])
def test_omniroute_key_replacement_probes_every_legacy_allowed_model(monkeypatch, legacy_allowed) -> None:
    transport = FixtureTransport()
    transport.responses["https://omniroute.example/v1/responses"] = TransportResponse(200, {"output_text": "ready"})
    service, database = compatible_service(monkeypatch, transport)
    service.create_connection(context(), route_command(), "route-too-many-key-create")
    database.allowed_model_ids = legacy_allowed
    old_encrypted_key = database.system_connection["encrypted_credential"]
    transport.requests.clear()

    saved, replayed = service.replace_credential(
        context(), "route-1", ProviderCredentialReplaceCommand("fixture-new-route-key", 1),
        "route-too-many-key",
    )

    assert replayed is False
    assert saved["verification_status"] == "verified"
    assert saved["allowed_model_ids"] == sorted(legacy_allowed)
    assert database.system_connection["encrypted_credential"] != old_encrypted_key
    assert [request[3]["model"] for request in transport.requests] == sorted(legacy_allowed)


@pytest.mark.parametrize("legacy_allowed", [
    ["m1", "m2", "m3", "m4", "m5"],
    ["auto", "m1", "m2", "m3", "m4"],
])
def test_omniroute_health_probes_every_legacy_allowed_model(monkeypatch, legacy_allowed) -> None:
    transport = FixtureTransport()
    transport.responses["https://omniroute.example/v1/responses"] = TransportResponse(200, {"output_text": "ready"})
    service, database = compatible_service(monkeypatch, transport)
    service.create_connection(context(), route_command(), "route-too-many-health-create")
    database.allowed_model_ids = legacy_allowed
    service._store = HealthStore(database)
    database.health_updates = []
    transport.requests.clear()

    result = service.check_active_connection(context(), "route-1")

    assert result["status"] == "verified"
    assert database.system_connection["verification_status"] == "verified"
    assert [request[3]["model"] for request in transport.requests] == sorted(legacy_allowed)


@pytest.mark.parametrize("legacy_allowed", [
    ("m1", "m2", "m3", "m4", "m5"),
    ("auto", "m1", "m2", "m3", "m4"),
])
def test_omniroute_unchanged_legacy_allowlist_survives_admin_rename_and_disable(
    monkeypatch, legacy_allowed,
) -> None:
    transport = FixtureTransport()
    transport.responses["https://omniroute.example/v1/responses"] = TransportResponse(200, {"output_text": "ready"})
    service, database = compatible_service(monkeypatch, transport)
    service.create_connection(context(), route_command(), "route-legacy-admin-seed")
    database.allowed_model_ids = list(legacy_allowed)
    database.models = [
        (model_id, ["text_generation"], ["text_generation"], False, "ready", 1)
        for model_id in legacy_allowed
    ]
    before_models = deepcopy(database.models)
    before_key = database.system_connection["encrypted_credential"]
    transport.requests.clear()

    saved, replayed = service.update_connection(context(), "route-1", ProviderConnectionUpdateCommand(
        display_name="Renamed Route", base_url="https://omniroute.example/v1", credential=None,
        logical_model_ids=legacy_allowed, enabled=False, expected_version=1,
        access_mode="public", credential_requirement="required", short_code="OM",
        adapter_type="OMNIROUTE", allowed_model_ids=legacy_allowed, provider_name="OmniRoute",
    ), "route-legacy-admin-update")

    assert replayed is False
    assert saved["display_name"] == "Renamed Route"
    assert saved["enabled"] is False
    assert saved["allowed_model_ids"] == list(legacy_allowed)
    assert database.allowed_model_ids == list(legacy_allowed)
    assert [item for item in database.models if item[0] != "auto"] == [
        item for item in before_models if item[0] != "auto"
    ]
    assert {item[0] for item in database.models} == {item[0] for item in before_models} | {"auto"}
    assert database.system_connection["encrypted_credential"] == before_key
    assert transport.requests == []


def test_omniroute_legacy_allowlist_change_still_rejects_five_selected_models(monkeypatch) -> None:
    transport = FixtureTransport()
    transport.responses["https://omniroute.example/v1/responses"] = TransportResponse(200, {"output_text": "ready"})
    service, database = compatible_service(monkeypatch, transport)
    service.create_connection(context(), route_command(), "route-legacy-change-seed")
    database.allowed_model_ids = ["m1", "m2", "m3", "m4", "m5"]
    before = durable_state(database)
    transport.requests.clear()

    with pytest.raises(ProviderConnectionAdminError, match="^PROVIDER_MODEL_IDS_INVALID$"):
        service.update_connection(context(), "route-1", ProviderConnectionUpdateCommand(
            display_name="Changed", base_url="https://omniroute.example/v1", credential=None,
            logical_model_ids=("m1", "m2", "m3", "m4", "m6"), enabled=True, expected_version=1,
            access_mode="public", credential_requirement="required", short_code="OM",
            adapter_type="OMNIROUTE", allowed_model_ids=("m1", "m2", "m3", "m4", "m6"), provider_name="OmniRoute",
        ), "route-legacy-change-rejected")

    assert durable_state(database) == before
    assert transport.requests == []


def test_omniroute_explicit_legacy_model_change_removes_auto_only_after_probe(monkeypatch) -> None:
    transport = FixtureTransport()
    transport.responses["https://omniroute.example/v1/responses"] = TransportResponse(200, {"output_text": "ready"})
    service, database = compatible_service(monkeypatch, transport)
    service.create_connection(context(), route_command(), "route-legacy-explicit-seed")
    database.allowed_model_ids = ["auto", "m1", "m2", "m3", "m4"]
    database.models = [
        (model_id, ["text_generation"], ["text_generation"], False, "ready", 1)
        for model_id in database.allowed_model_ids
    ]
    transport.requests.clear()

    saved, _ = service.update_connection(context(), "route-1", ProviderConnectionUpdateCommand(
        display_name="OmniRoute", base_url="https://omniroute.example/v1", credential=None,
        logical_model_ids=("m1", "m2", "m3", "m4"), enabled=True, expected_version=1,
        access_mode="public", credential_requirement="required", short_code="OM",
        adapter_type="OMNIROUTE", allowed_model_ids=("m1", "m2", "m3", "m4"), provider_name="OmniRoute",
    ), "route-legacy-explicit-change")

    assert saved["allowed_model_ids"] == ["m1", "m2", "m3", "m4"]
    assert database.allowed_model_ids == ["m1", "m2", "m3", "m4"]
    assert [request[3]["model"] for request in transport.requests] == ["m1", "m2", "m3", "m4"]
    assert {item[0]: item[4] for item in database.models}["auto"] == "ready"


def test_omniroute_failed_refresh_preserves_connection_and_key(monkeypatch) -> None:
    transport = FixtureTransport()
    transport.responses["https://omniroute.example/v1/responses"] = TransportResponse(200, {"output_text": "ready"})
    service, database = compatible_service(monkeypatch, transport)
    service.create_connection(context(), route_command(), "route-create-003")
    before = durable_state(database)
    transport.requests.clear()
    transport.responses["https://omniroute.example/v1/models"] = TransportResponse(401, {})

    with pytest.raises(ProviderConnectionAdminError, match="^PROVIDER_AUTHENTICATION_FAILED$"):
        service.refresh_catalog(context(), "route-1", 1, "route-refresh-002")

    assert durable_state(database) == before
    assert [(item[0], item[1]) for item in transport.requests] == [
        ("GET", "https://omniroute.example/v1/models")
    ]


def test_omniroute_model_change_probes_selected_id_and_keeps_saved_key(monkeypatch) -> None:
    transport = FixtureTransport()
    transport.responses["https://omniroute.example/v1/responses"] = TransportResponse(200, {"output_text": "ready"})
    service, database = compatible_service(monkeypatch, transport)
    service.create_connection(context(), route_command(), "route-create-004")
    old_key = database.system_connection["encrypted_credential"]
    transport.requests.clear()

    saved, _ = service.update_connection(context(), "route-1", ProviderConnectionUpdateCommand(
        display_name="OmniRoute", base_url="https://omniroute.example/v1", credential=None,
        logical_model_ids=("cc/claude-sonnet",), enabled=True, expected_version=1,
        access_mode="public", credential_requirement="required", short_code="OM",
        adapter_type="OMNIROUTE", allowed_model_ids=("cc/claude-sonnet",), provider_name="OmniRoute",
    ), "route-update-001")

    assert saved["allowed_model_ids"] == ["cc/claude-sonnet"]
    assert database.system_connection["encrypted_credential"] == old_key
    assert [(item[0], item[1], item[3]["model"]) for item in transport.requests] == [
        ("POST", "https://omniroute.example/v1/responses", "cc/claude-sonnet")
    ]


def test_personal_omniroute_saves_pending_auto_without_admin_key_or_network(monkeypatch) -> None:
    transport = FixtureTransport()
    service, database = compatible_service(monkeypatch, transport)
    command = replace(route_command(), credential=None, access_mode="personal")

    saved, _ = service.create_connection(context(), command, "route-personal-001")

    assert saved["verification_status"] == "unverified"
    assert saved["allowed_model_ids"] == ["auto"]
    assert saved["configured"] is False
    assert database.system_connection["encrypted_credential"] is None
    assert transport.requests == []


def test_personal_omniroute_allowed_change_invalidates_user_keys_without_deleting_them(monkeypatch) -> None:
    transport = FixtureTransport()
    service, database = compatible_service(monkeypatch, transport)
    service.create_connection(
        context(), replace(route_command(), credential=None, access_mode="personal"),
        "route-personal-change-create",
    )
    database.user_credentials = [
        {"connection_id": "route-1", "verification_status": "verified", "encrypted_credential": b"sealed-a", "credential_version": 2},
        {"connection_id": "other-route", "verification_status": "verified", "encrypted_credential": b"sealed-b", "credential_version": 3},
    ]

    saved, _ = service.update_connection(context(), "route-1", ProviderConnectionUpdateCommand(
        display_name="OmniRoute", base_url="https://omniroute.example/v1", credential=None,
        logical_model_ids=("combo-writing",), enabled=True, expected_version=1,
        access_mode="personal", credential_requirement="required", short_code="OM",
        adapter_type="OMNIROUTE", allowed_model_ids=("combo-writing",), provider_name="OmniRoute",
    ), "route-personal-change-update")

    assert saved["allowed_model_ids"] == ["combo-writing"]
    assert database.user_credentials == [
        {"connection_id": "route-1", "verification_status": "unverified", "encrypted_credential": b"sealed-a", "credential_version": 2},
        {"connection_id": "other-route", "verification_status": "verified", "encrypted_credential": b"sealed-b", "credential_version": 3},
    ]
    assert transport.requests == []


def test_personal_omniroute_endpoint_change_invalidates_same_models_user_key(monkeypatch) -> None:
    transport = FixtureTransport()
    service, database = compatible_service(monkeypatch, transport)
    service.create_connection(
        context(), replace(route_command(), credential=None, access_mode="personal"),
        "route-personal-endpoint-create",
    )
    database.user_credentials = [{
        "connection_id": "route-1", "verification_status": "verified",
        "encrypted_credential": b"sealed-a", "credential_version": 2,
    }]

    saved, _ = service.update_connection(context(), "route-1", ProviderConnectionUpdateCommand(
        display_name="OmniRoute", base_url="https://new-omniroute.example/v1", credential=None,
        logical_model_ids=("auto",), enabled=True, expected_version=1,
        access_mode="personal", credential_requirement="required", short_code="OM",
        adapter_type="OMNIROUTE", allowed_model_ids=("auto",), provider_name="OmniRoute",
    ), "route-personal-endpoint-update")

    assert saved["base_url"] == "https://new-omniroute.example/v1"
    assert saved["allowed_model_ids"] == ["auto"]
    assert database.user_credentials == [{
        "connection_id": "route-1", "verification_status": "unverified",
        "encrypted_credential": b"sealed-a", "credential_version": 2,
    }]
    assert transport.requests == []


@pytest.mark.parametrize(
    ("adapter_type", "path", "payload"),
    [
        ("openai_compatible", "/chat/completions", {"choices": [{"message": {"content": "ready"}}]}),
        ("anthropic_compatible", "/messages", {"content": [{"type": "text", "text": "ready"}]}),
    ],
)
def test_custom_create_registers_verified_manual_model(
    monkeypatch, adapter_type, path, payload,
) -> None:
    transport = FixtureTransport()
    transport.responses["https://models.example/v1/models"] = TransportResponse(404, {})
    transport.responses[f"https://models.example/v1{path}"] = TransportResponse(200, payload)
    service, database = compatible_service(monkeypatch, transport)

    saved, replayed = service.create_connection(
        context(), custom_command(adapter_type=adapter_type), "custom-create-001",
    )

    assert replayed is False
    assert saved["adapter_type"] == adapter_type
    assert saved["allowed_model_ids"] == ["manual-model"]
    assert [item["model_id"] for item in saved["models"]] == ["manual-model"]
    assert saved["verification_status"] == "verified"
    assert database.system_connection["encrypted_credential"] is not None
    assert len(database.outbox) == 1
    assert [(item[0], item[1], item[3]["model"]) for item in transport.requests] == [
        ("POST", f"https://models.example/v1{path}", "manual-model"),
    ]
    assert "fixture-public-key" not in repr(database.idempotency) + repr(database.outbox) + repr(saved)


@pytest.mark.parametrize(
    ("adapter_type", "path", "payload"),
    [
        ("openai_compatible", "/chat/completions", {"choices": [{"message": {"content": "ready"}}]}),
        ("anthropic_compatible", "/messages", {"content": [{"type": "text", "text": "ready"}]}),
    ],
)
def test_custom_keyless_public_connection_verifies_without_auth_header(
    monkeypatch, adapter_type, path, payload,
) -> None:
    transport = FixtureTransport()
    transport.responses[f"https://models.example/v1{path}"] = TransportResponse(200, payload)
    service, database = compatible_service(monkeypatch, transport)
    command = replace(custom_command(adapter_type=adapter_type, credential=None), credential_requirement="none")

    saved, _ = service.create_connection(context(), command, f"custom-keyless-{adapter_type}")

    assert saved["credential_requirement"] == "none"
    assert saved["verification_status"] == "verified"
    assert database.system_connection["encrypted_credential"] is None
    assert len(transport.requests) == 1
    assert transport.requests[0][2].get("authorization") is None
    assert transport.requests[0][2].get("x-api-key") is None


def test_private_test_key_is_ephemeral(monkeypatch) -> None:
    transport = FixtureTransport()
    transport.responses["https://models.example/v1/messages"] = TransportResponse(
        200, {"content": [{"type": "text", "text": "ready"}]},
    )
    service, database = compatible_service(monkeypatch, transport)
    command = custom_command(adapter_type="anthropic_compatible", access_mode="personal",
                             credential=None, test_credential="fixture-one-time-key")

    saved, _replayed = service.create_connection(context(), command, "private-create-001")

    assert database.system_connection["encrypted_credential"] is None
    assert database.system_connection["credential_version"] == 0
    assert saved["allowed_model_ids"] == ["manual-model"]
    assert saved["verification_status"] == "unverified"
    assert transport.requests[0][2]["x-api-key"] == "fixture-one-time-key"
    assert "fixture-one-time-key" not in repr(command) + repr(saved) + repr(database.__dict__)


def test_personal_custom_auto_expansion_requires_existing_user_keys_to_be_reverified(monkeypatch) -> None:
    transport = FixtureTransport()
    transport.responses["https://models.example/v1/chat/completions"] = TransportResponse(
        200, {"choices": [{"message": {"content": "ready"}}]},
    )
    service, database = compatible_service(monkeypatch, transport)
    service.create_connection(
        context(), custom_command(access_mode="personal", credential=None,
                                  test_credential="fixture-one-time-key"),
        "custom-personal-auto-seed",
    )
    database.user_credentials = [
        {"connection_id": "custom-1", "verification_status": "verified", "encrypted_credential": b"sealed-a", "credential_version": 2},
        {"connection_id": "other-custom", "verification_status": "verified", "encrypted_credential": b"sealed-b", "credential_version": 3},
    ]

    saved, _ = service.update_connection(context(), "custom-1", ProviderConnectionUpdateCommand(
        display_name="Custom primary", base_url="https://models.example/v1", credential=None,
        logical_model_ids=(), enabled=True, expected_version=1,
        access_mode="personal", credential_requirement="required", short_code="CU",
        adapter_type="openai_compatible", allowed_model_ids=("manual-model", "auto"),
        provider_name="Example AI", test_credential="fixture-one-time-key",
        auto_model_id="auto", auto_model_id_specified=True,
    ), "custom-personal-auto-expand")

    assert saved["auto_model_id"] == "auto"
    assert saved["allowed_model_ids"] == ["manual-model", "auto"]
    assert database.user_credentials == [
        {"connection_id": "custom-1", "verification_status": "unverified", "encrypted_credential": b"sealed-a", "credential_version": 2},
        {"connection_id": "other-custom", "verification_status": "verified", "encrypted_credential": b"sealed-b", "credential_version": 3},
    ]


@pytest.mark.parametrize("adapter_type", ["openai_compatible", "anthropic_compatible"])
def test_personal_custom_without_key_or_models_registers_pending_without_provider_call(
    monkeypatch, adapter_type,
) -> None:
    transport = FixtureTransport()
    service, database = compatible_service(monkeypatch, transport)
    command = custom_command(
        adapter_type=adapter_type, access_mode="personal", credential=None,
        allowed_model_ids=(),
    )

    saved, replayed = service.create_connection(context(), command, "custom-pending-001")

    assert replayed is False
    assert saved["verification_status"] == "unverified"
    assert saved["allowed_model_ids"] == []
    assert saved["models"] == []
    assert database.system_connection["encrypted_credential"] is None
    assert database.system_connection["credential_version"] == 0
    assert transport.requests == []
    before = durable_state(database)
    replay, replayed = service.create_connection(context(), command, "custom-pending-001")
    assert replayed is True
    assert replay == saved
    assert durable_state(database) == before


@pytest.mark.parametrize(
    "overrides",
    [
        {"allowed_model_ids": ("manual-model",)},
        {"logical_model_ids": ("manual-model",)},
        {"access_mode": "public"},
    ],
)
def test_pending_exception_does_not_bypass_model_or_public_key_requirements(
    monkeypatch, overrides,
) -> None:
    transport = FixtureTransport()
    service, database = compatible_service(monkeypatch, transport)
    command = replace(custom_command(access_mode="personal", credential=None, allowed_model_ids=()), **overrides)

    with pytest.raises(ProviderConnectionAdminError, match="^PROVIDER_CREDENTIAL_REQUIRED$"):
        service.create_connection(context(), command, "custom-pending-rejected-001")

    assert database.system_connection is None
    assert database.allowed_model_ids == []
    assert transport.requests == []


@pytest.mark.parametrize("access_mode,model_ids", [("personal", ("manual-model",)), ("public", ())])
def test_pending_custom_cannot_become_usable_without_required_key_and_probe(
    monkeypatch, access_mode, model_ids,
) -> None:
    transport = FixtureTransport()
    service, database = compatible_service(monkeypatch, transport)
    service.create_connection(
        context(), custom_command(access_mode="personal", credential=None, allowed_model_ids=()),
        "custom-pending-transition-seed",
    )
    before = durable_state(database)
    update = ProviderConnectionUpdateCommand(
        display_name="Changed", base_url="https://models.example/v1", credential=None,
        logical_model_ids=model_ids, enabled=True, expected_version=1, access_mode=access_mode,
        credential_requirement="required", short_code="CU", adapter_type="openai_compatible",
        allowed_model_ids=model_ids, provider_name="Example AI",
    )

    with pytest.raises(ProviderConnectionAdminError, match="^PROVIDER_CREDENTIAL_REQUIRED$"):
        service.update_connection(context(), "custom-1", update, "custom-pending-transition-001")

    assert durable_state(database) == before
    assert transport.requests == []


@pytest.mark.parametrize("probe_ok", [False, True])
def test_pending_custom_model_addition_requires_ephemeral_probe_and_keeps_key_unstored(
    monkeypatch, probe_ok,
) -> None:
    transport = FixtureTransport()
    service, database = compatible_service(monkeypatch, transport)
    service.create_connection(
        context(), custom_command(access_mode="personal", credential=None, allowed_model_ids=()),
        "custom-pending-add-seed",
    )
    before = durable_state(database)
    url = "https://models.example/v1/chat/completions"
    transport.responses[url] = TransportResponse(
        200 if probe_ok else 401,
        {"choices": [{"message": {"content": "ready"}}]} if probe_ok else {"error": "fixture"},
    )
    update = ProviderConnectionUpdateCommand(
        display_name="Changed", base_url="https://models.example/v1", credential=None,
        logical_model_ids=("manual-model",), enabled=True, expected_version=1, access_mode="personal",
        credential_requirement="required", short_code="CU", adapter_type="openai_compatible",
        allowed_model_ids=("manual-model",), provider_name="Example AI",
        test_credential="fixture-ephemeral-key",
    )

    if probe_ok:
        saved, replayed = service.update_connection(context(), "custom-1", update, "custom-pending-add-001")
        assert replayed is False
        assert saved["allowed_model_ids"] == ["manual-model"]
        assert saved["verification_status"] == "unverified"
        assert database.system_connection["encrypted_credential"] is None
    else:
        with pytest.raises(ProviderConnectionAdminError, match="^PROVIDER_AUTHENTICATION_FAILED$"):
            service.update_connection(context(), "custom-1", update, "custom-pending-add-001")
        assert durable_state(database) == before
    assert [(item[1], item[3]["model"]) for item in transport.requests] == [(url, "manual-model")]
    assert "fixture-ephemeral-key" not in repr(database.__dict__)


def test_failed_update_preserves_connection(monkeypatch) -> None:
    transport = FixtureTransport()
    url = "https://models.example/v1/chat/completions"
    transport.responses[url] = TransportResponse(200, {"choices": [{"message": {"content": "ready"}}]})
    service, database = compatible_service(monkeypatch, transport)
    service.create_connection(context(), custom_command(), "custom-create-002")
    before = durable_state(database)
    transport.requests.clear()
    transport.responses[(url, "second-model")] = TransportResponse(401, {"error": "fixture-private-error"})
    update = ProviderConnectionUpdateCommand(
        display_name="Changed", base_url="https://models.example/v1", credential=None,
        logical_model_ids=(), enabled=True, expected_version=1, access_mode="public",
        credential_requirement="required", short_code="CU", adapter_type="openai_compatible",
        allowed_model_ids=("manual-model", "second-model"), provider_name="Example AI",
    )

    with pytest.raises(ProviderConnectionAdminError, match="^PROVIDER_AUTHENTICATION_FAILED$"):
        service.update_connection(context(), "custom-1", update, "custom-update-001")

    assert durable_state(database) == before
    assert [item[3]["model"] for item in transport.requests] == ["manual-model", "second-model"]


def test_manual_catalog_refresh_failure_preserves_allowlist(monkeypatch) -> None:
    transport = FixtureTransport()
    transport.responses["https://models.example/v1/messages"] = TransportResponse(
        200, {"content": [{"type": "text", "text": "ready"}]},
    )
    service, database = compatible_service(monkeypatch, transport)
    service.create_connection(context(), custom_command(adapter_type="anthropic_compatible"), "custom-create-003")
    before = durable_state(database)
    transport.requests.clear()
    transport.responses["https://models.example/v1/models"] = TransportResponse(405, {})

    with pytest.raises(ProviderConnectionAdminError):
        service.refresh_catalog(context(), "custom-1", 1, "custom-refresh-001")

    assert durable_state(database) == before
    assert [item[1] for item in transport.requests] == ["https://models.example/v1/models"]
    assert transport.requests[0][2]["anthropic-version"] == "2023-06-01"


@pytest.mark.parametrize(
    ("adapter_type", "path", "probe_payload"),
    [
        ("openai_compatible", "/chat/completions", {"choices": [{"message": {"content": "ready"}}]}),
        ("anthropic_compatible", "/messages", {"content": [{"type": "text", "text": "ready"}]}),
    ],
)
def test_custom_catalog_refresh_success_preserves_verified_allowed_manual_model(
    monkeypatch, adapter_type, path, probe_payload,
) -> None:
    transport = FixtureTransport()
    transport.responses[f"https://models.example/v1{path}"] = TransportResponse(200, probe_payload)
    service, database = compatible_service(monkeypatch, transport)
    service.create_connection(context(), custom_command(adapter_type=adapter_type), "custom-create-007")
    transport.requests.clear()
    transport.responses["https://models.example/v1/models"] = TransportResponse(
        200, {"data": [{"id": "listed-only-model"}], "has_more": adapter_type == "anthropic_compatible"},
    )

    saved, replayed = service.refresh_catalog(context(), "custom-1", 1, "custom-refresh-002")

    assert replayed is False
    assert saved["allowed_model_ids"] == ["manual-model"]
    assert {item["model_id"]: item["catalog_status"] for item in saved["models"]} == {
        "manual-model": "ready", "listed-only-model": "ready",
    }
    assert database.system_connection["verification_status"] == "verified"
    assert [item[1] for item in transport.requests] == ["https://models.example/v1/models"]


def test_existing_custom_adapter_type_immutable(monkeypatch) -> None:
    transport = FixtureTransport()
    transport.responses["https://models.example/v1/chat/completions"] = TransportResponse(
        200, {"choices": [{"message": {"content": "ready"}}]},
    )
    service, database = compatible_service(monkeypatch, transport)
    service.create_connection(context(), custom_command(), "custom-create-004")
    before = durable_state(database)
    transport.requests.clear()
    update = ProviderConnectionUpdateCommand(
        display_name="Changed", base_url="https://models.example/v1", credential=None,
        logical_model_ids=(), enabled=True, expected_version=1, access_mode="public",
        credential_requirement="required", short_code="CU", adapter_type="anthropic_compatible",
        allowed_model_ids=("manual-model",), provider_name="Example AI",
    )

    with pytest.raises(ProviderConnectionAdminError, match="^PROVIDER_ADAPTER_IMMUTABLE$"):
        service.update_connection(context(), "custom-1", update, "custom-update-002")

    assert durable_state(database) == before
    assert transport.requests == []


def test_new_custom_rejects_migration_legacy_adapter_type(monkeypatch) -> None:
    service, database = compatible_service(monkeypatch, FixtureTransport())

    with pytest.raises(ProviderConnectionAdminError, match="^PROVIDER_ADAPTER_UNSUPPORTED$"):
        service.create_connection(context(), custom_command(adapter_type="CUSTOM"), "legacy-create-001")

    assert database.system_connection is None


def test_migrated_custom_update_preserves_legacy_adapter_type(monkeypatch) -> None:
    transport = FixtureTransport()
    transport.responses["https://models.example/v1/chat/completions"] = TransportResponse(
        200, {"choices": [{"message": {"content": "ready"}}]},
    )
    service, database = compatible_service(monkeypatch, transport)
    service.create_connection(context(), custom_command(), "legacy-seed-001")
    database.system_connection["adapter_type"] = "CUSTOM"
    before_allowed = list(database.allowed_model_ids)
    before_credential = database.system_connection["encrypted_credential"]
    transport.requests.clear()

    saved, replayed = service.update_connection(context(), "custom-1", ProviderConnectionUpdateCommand(
        display_name="Legacy renamed", base_url="https://models.example/v1", credential=None,
        logical_model_ids=(), enabled=True, expected_version=1, access_mode="public",
        credential_requirement="required", short_code="CU", adapter_type="CUSTOM",
        allowed_model_ids=("manual-model",), provider_name="Example AI",
    ), "legacy-update-001")

    assert replayed is False
    assert saved["display_name"] == "Legacy renamed"
    assert saved["adapter_type"] == "CUSTOM"
    assert database.allowed_model_ids == before_allowed
    assert database.system_connection["encrypted_credential"] == before_credential
    assert transport.requests == []


def test_private_replay_uses_keyed_test_key_digest(monkeypatch) -> None:
    transport = FixtureTransport()
    transport.responses["https://models.example/v1/messages"] = TransportResponse(
        200, {"content": [{"type": "text", "text": "ready"}]},
    )
    service, database = compatible_service(monkeypatch, transport)
    first = custom_command(adapter_type="anthropic_compatible", access_mode="personal",
                           credential=None, test_credential="fixture-one-time-key")
    service.create_connection(context(), first, "private-create-002")
    before = durable_state(database)
    transport.requests.clear()

    replay, replayed = service.create_connection(context(), first, "private-create-002")
    assert replayed is True
    assert replay["allowed_model_ids"] == ["manual-model"]
    assert transport.requests == []
    with pytest.raises(ProviderConnectionAdminError, match="^IDEMPOTENCY_KEY_REUSED$"):
        service.create_connection(context(), replace(first, test_credential="fixture-different-key"),
                                  "private-create-002")
    assert durable_state(database) == before
    assert "fixture-one-time-key" not in repr(database.__dict__)
    assert "fixture-different-key" not in repr(database.__dict__)


def test_preview_reads_only_catalog_and_rejects_non_custom(monkeypatch) -> None:
    transport = FixtureTransport()
    transport.responses["https://models.example/v1/models"] = TransportResponse(
        200, {"data": [{"id": "listed-model"}]},
    )
    service, database = compatible_service(monkeypatch, transport)

    ids = service.preview_models(
        context(), connection_id="custom-1", provider_code="CUSTOM",
        adapter_type="anthropic_compatible", base_url="https://models.example/v1",
        credential="fixture-preview-key",
    )

    assert ids == ("listed-model",)
    assert database.system_connection is None and database.idempotency == {} and database.outbox == []
    assert [(item[0], item[1], item[2]["anthropic-version"]) for item in transport.requests] == [
        ("GET", "https://models.example/v1/models", "2023-06-01"),
    ]
    with pytest.raises(ProviderConnectionAdminError, match="^PROVIDER_ADAPTER_UNSUPPORTED$"):
        service.preview_models(context(), connection_id="custom-1", provider_code="UPSTAGE",
                               adapter_type="UPSTAGE", base_url="https://api.upstage.ai/v1",
                               credential="fixture-preview-key")
    assert len(transport.requests) == 1


def test_preview_keyless_custom_catalog_omits_authentication(monkeypatch) -> None:
    transport = FixtureTransport()
    transport.responses["https://models.example/v1/models"] = TransportResponse(
        200, {"data": [{"id": "local-model"}]},
    )
    service, database = compatible_service(monkeypatch, transport)

    ids = service.preview_models(
        context(), connection_id="custom-1", provider_code="CUSTOM",
        adapter_type="openai_compatible", base_url="https://models.example/v1",
        credential=None,
    )

    assert ids == ("local-model",)
    assert transport.requests[0][2] == {}
    assert database.system_connection is None


def test_preview_rejects_upstream_model_id_that_echoes_input_key(monkeypatch) -> None:
    transport = FixtureTransport()
    transport.responses["https://models.example/v1/models"] = TransportResponse(
        200, {"data": [{"id": "model-fixture-preview-key"}]},
    )
    service, database = compatible_service(monkeypatch, transport)

    with pytest.raises(ProviderConnectionAdminError, match="^PROVIDER_CATALOG_RESPONSE_INVALID$") as captured:
        service.preview_models(context(), connection_id="custom-1", provider_code="CUSTOM",
                               adapter_type="openai_compatible", base_url="https://models.example/v1",
                               credential="fixture-preview-key")

    assert "fixture-preview-key" not in repr(captured.value)
    assert database.system_connection is None


def test_custom_model_change_probes_then_updates_catalog_and_allowlist(monkeypatch) -> None:
    transport = FixtureTransport()
    url = "https://models.example/v1/chat/completions"
    transport.responses[url] = TransportResponse(200, {"choices": [{"message": {"content": "ready"}}]})
    service, database = compatible_service(monkeypatch, transport)
    service.create_connection(context(), custom_command(), "custom-create-005")
    transport.requests.clear()
    update = ProviderConnectionUpdateCommand(
        display_name="Changed", base_url="https://models.example/v1", credential=None,
        logical_model_ids=(), enabled=True, expected_version=1, access_mode="public",
        credential_requirement="required", short_code="CU", adapter_type="openai_compatible",
        allowed_model_ids=("manual-model", "second-model"), provider_name="Example AI",
    )

    saved, replayed = service.update_connection(context(), "custom-1", update, "custom-update-003")

    assert replayed is False
    assert saved["allowed_model_ids"] == ["manual-model", "second-model"]
    assert {item["model_id"] for item in saved["models"]} == {"manual-model", "second-model"}
    assert [item[3]["model"] for item in transport.requests] == ["manual-model", "second-model"]
    assert database.system_connection["credential_version"] == 1


def test_custom_public_key_replacement_probes_allowed_model_before_encrypting(monkeypatch) -> None:
    transport = FixtureTransport()
    url = "https://models.example/v1/messages"
    transport.responses[url] = TransportResponse(200, {"content": [{"type": "text", "text": "ready"}]})
    service, database = compatible_service(monkeypatch, transport)
    service.create_connection(context(), custom_command(adapter_type="anthropic_compatible"), "custom-create-006")
    old_ciphertext = database.system_connection["encrypted_credential"]
    transport.requests.clear()

    saved, replayed = service.replace_credential(
        context(), "custom-1", ProviderCredentialReplaceCommand("fixture-replacement-key", 1),
        "custom-credential-001",
    )

    assert replayed is False
    assert saved["credential_version"] == 2
    assert saved["allowed_model_ids"] == ["manual-model"]
    assert database.system_connection["encrypted_credential"] != old_ciphertext
    assert [(item[1], item[2]["x-api-key"], item[3]["model"]) for item in transport.requests] == [
        (url, "fixture-replacement-key", "manual-model"),
    ]
    assert "fixture-replacement-key" not in repr(saved) + repr(database.idempotency) + repr(database.outbox)


@pytest.mark.parametrize(
    ("adapter_type", "probe_path", "probe_payload"),
    [
        ("openai_compatible", "/chat/completions", {"choices": [{"message": {"content": "ready"}}]}),
        ("anthropic_compatible", "/messages", {"content": [{"type": "text", "text": "ready"}]}),
    ],
)
def test_custom_public_health_skips_without_replacing_manual_probe_result(
    monkeypatch, adapter_type, probe_path, probe_payload,
) -> None:
    transport = FixtureTransport()
    transport.responses[f"https://models.example/v1{probe_path}"] = TransportResponse(200, probe_payload)
    service, database = compatible_service(monkeypatch, transport)
    saved, _ = service.create_connection(
        context(), custom_command(adapter_type=adapter_type), f"create-health-{adapter_type}",
    )
    assert saved["verification_status"] == "verified"
    assert saved["allowed_model_ids"] == ["manual-model"]
    before = durable_state(database)
    transport.requests.clear()
    transport.responses["https://models.example/v1/models"] = TransportResponse(405, {})
    database.health_updates = []
    service._store = HealthStore(database)

    result = service.check_active_connection(context(), "custom-1")

    assert result == {"connection_id": "custom-1", "status": "skipped"}
    assert transport.requests == []
    assert database.health_updates == []
    assert durable_state(database) == before


@pytest.mark.parametrize(
    ("upstream_status", "expected_status"), [(200, "verified"), (503, "failed")],
)
def test_existing_ollama_public_health_still_checks_and_updates(
    monkeypatch, upstream_status, expected_status,
) -> None:
    transport = FixtureTransport()
    transport.responses["http://ollama.internal:11434/api/tags"] = TransportResponse(
        upstream_status, {"models": [{"name": "qwen3"}]},
    )
    service, database = compatible_service(monkeypatch, transport)
    database.system_connection = {
        "connection_id": "ollama-lan", "provider_code": "OLLAMA", "display_name": "LAN Ollama",
        "base_url": "http://ollama.internal:11434", "encrypted_credential": None,
        "credential_nonce": None, "encryption_key_version": None,
        "credential_schema_version": None, "credential_version": 0, "enabled": True,
        "verification_status": "unverified", "verified_at": None, "version": 1,
        "access_mode": "public", "credential_requirement": "none", "short_code": "OL",
        "adapter_type": "OLLAMA", "provider_name": "Ollama",
    }
    database.health_updates = []
    service._store = HealthStore(database)

    result = service.check_active_connection(context(), "ollama-lan")

    assert result["status"] == expected_status
    assert result["connection_id"] == "ollama-lan"
    assert "checked_at" in result
    assert database.system_connection["verification_status"] == expected_status
    assert len(database.health_updates) == 1
    assert [(item[0], item[1]) for item in transport.requests] == [
        ("GET", "http://ollama.internal:11434/api/tags"),
    ]
