from __future__ import annotations

from contextlib import contextmanager

import pytest

from daon_user_api.provider_credentials import ProviderCredentialCipher
from daon_user_api.workspace_model_defaults import (
    PostgresWorkspaceModelDefaultsService,
    PostgresWorkspaceModelResolver,
    WorkspaceModelDefaultsContext,
    WorkspaceModelDefaultsError,
    WorkspaceModelContext,
    WorkspaceModelUnavailable,
)


class Cursor:
    def __init__(self, row):
        self._row = row

    def fetchone(self):
        return self._row


class Connection:
    def __init__(self, database) -> None:
        self.database = database

    def execute(self, sql, params=()):
        normalized = " ".join(sql.split())
        if not normalized.startswith("SELECT d.version,d.connection_id,d.model_id"):
            raise AssertionError(f"unexpected SQL: {normalized}")
        self.database.queries.append((normalized, params))
        return Cursor(self.database.row)


class Database:
    def __init__(self, row) -> None:
        self.row = row
        self.queries = []
        self.contexts = []


class Store:
    def __init__(self, database: Database) -> None:
        self.database = database

    @contextmanager
    def _transaction(self, context):
        self.database.contexts.append(context)
        yield Connection(self.database)


def context() -> WorkspaceModelContext:
    return WorkspaceModelContext(
        tenant_id="tenant-001", workspace_id="workspace-001", actor_id="actor-001",
    )


def row(
    cipher: ProviderCredentialCipher, *, credential: bytes | None = b"client-key-v1",
    connection_id: str = "eoul-primary", provider_code: str = "EOUL_GATEWAY",
    capability: str = "text_generation", enabled: bool = True,
    verification_status: str = "verified", catalog_status: str = "ready",
    effective_capabilities: list[str] | None = None, credential_version: int = 4,
):
    sealed = None if credential is None else cipher.encrypt(
        connection_id, provider_code, credential_version, credential,
    )
    return (
        3, connection_id, "assistant-default", provider_code,
        "https://gateway.example.com", None if sealed is None else sealed.ciphertext,
        None if sealed is None else sealed.nonce,
        None if sealed is None else sealed.encryption_key_version,
        None if sealed is None else sealed.schema_version,
        credential_version, enabled, verification_status,
        effective_capabilities if effective_capabilities is not None else [capability],
        catalog_status, 9,
    )


def test_resolve_revalidates_workspace_default_and_releases_latest_credential() -> None:
    cipher = ProviderCredentialCipher(b"r" * 32, encryption_key_version=1)
    database = Database(row(cipher))
    resolver = PostgresWorkspaceModelResolver(Store(database), cipher)

    with resolver.resolve(context(), "text_generation") as resolved:
        assert resolved.connection_id == "eoul-primary"
        assert resolved.provider_code == "EOUL_GATEWAY"
        assert resolved.model_id == "assistant-default"
        assert resolved.credential_version == 4
        assert resolved.catalog_version == 9
        assert resolved.routing_owner == "gateway"
        assert resolved.daon_fallback_allowed is False
        assert resolved.credential_text() == "client-key-v1"
        assert "client-key-v1" not in repr(resolved)

    with pytest.raises(WorkspaceModelUnavailable, match="^PROVIDER_CREDENTIAL_RELEASED$"):
        resolved.credential_text()
    assert database.contexts[0].tenant_id == "tenant-001"
    assert database.contexts[0].workspace_id == "workspace-001"
    assert database.queries[0][1] == ("text_generation",)


def test_resolve_reads_latest_credential_version_on_each_request() -> None:
    cipher = ProviderCredentialCipher(b"v" * 32, encryption_key_version=1)
    database = Database(row(cipher, credential=b"client-key-v1", credential_version=4))
    resolver = PostgresWorkspaceModelResolver(Store(database), cipher)

    with resolver.resolve(context(), "text_generation") as first:
        assert (first.credential_version, first.credential_text()) == (4, "client-key-v1")
    database.row = row(cipher, credential=b"client-key-v2", credential_version=5)
    with resolver.resolve(context(), "text_generation") as second:
        assert (second.credential_version, second.credential_text()) == (5, "client-key-v2")

    assert len(database.queries) == 2


@pytest.mark.parametrize(
    ("changed", "code"),
    [
        ({"enabled": False}, "PROVIDER_CONNECTION_UNAVAILABLE"),
        ({"verification_status": "unverified"}, "PROVIDER_CONNECTION_UNAVAILABLE"),
        ({"catalog_status": "stale"}, "PROVIDER_MODEL_NOT_READY"),
        ({"effective_capabilities": ["image_understanding"]}, "PROVIDER_CAPABILITY_UNSUPPORTED"),
        ({"provider_code": "OPENAI"}, "PROVIDER_EXECUTION_NOT_READY"),
    ],
)
def test_resolve_fails_closed_when_selected_model_is_not_executable(changed, code) -> None:
    cipher = ProviderCredentialCipher(b"f" * 32, encryption_key_version=1)
    database = Database(row(cipher, **changed))
    resolver = PostgresWorkspaceModelResolver(Store(database), cipher)

    with pytest.raises(WorkspaceModelUnavailable, match=f"^{code}$"):
        with resolver.resolve(context(), "text_generation"):
            pass


def test_resolve_fails_closed_for_missing_default_or_required_credential() -> None:
    cipher = ProviderCredentialCipher(b"m" * 32, encryption_key_version=1)
    database = Database(None)
    resolver = PostgresWorkspaceModelResolver(Store(database), cipher)

    with pytest.raises(WorkspaceModelUnavailable, match="^WORKSPACE_MODEL_DEFAULT_UNAVAILABLE$"):
        with resolver.resolve(context(), "text_generation"):
            pass

    database.row = row(cipher, credential=None, credential_version=0)
    with pytest.raises(WorkspaceModelUnavailable, match="^PROVIDER_CREDENTIAL_REQUIRED$"):
        with resolver.resolve(context(), "text_generation"):
            pass


def test_ollama_default_allows_nullable_credential_without_legacy_fallback() -> None:
    cipher = ProviderCredentialCipher(b"o" * 32, encryption_key_version=1)
    database = Database(row(
        cipher, credential=None, credential_version=0,
        connection_id="ollama-lan", provider_code="OLLAMA",
    ))
    resolver = PostgresWorkspaceModelResolver(Store(database), cipher)

    with resolver.resolve(context(), "text_generation") as resolved:
        assert resolved.connection_id == "ollama-lan"
        assert resolved.credential_text(required=False) is None
        assert resolved.routing_owner == "provider"
        assert resolved.daon_fallback_allowed is True


@pytest.mark.parametrize(
    ("provider_code", "base_url"),
    [
        ("GROQ", "https://api.groq.com/openai/v1"),
        ("MISTRAL", "https://api.mistral.ai/v1"),
        ("UPSTAGE", "https://api.upstage.ai/v1"),
        ("OPENROUTER", "https://openrouter.ai/api/v1"),
    ],
)
def test_text_resolution_preserves_every_provider_with_an_execution_adapter(
    provider_code: str, base_url: str,
) -> None:
    cipher = ProviderCredentialCipher(b"a" * 32, encryption_key_version=1)
    values = list(row(
        cipher, connection_id=f"{provider_code.lower()}-primary",
        provider_code=provider_code,
    ))
    values[4] = base_url
    database = Database(tuple(values))

    with PostgresWorkspaceModelResolver(Store(database), cipher).resolve(
        context(), "text_generation",
    ) as resolved:
        assert resolved.provider_code == provider_code


class DefaultsDatabase:
    def __init__(self) -> None:
        self.models = [(
            "ollama-lan", "OLLAMA", "LAN Ollama", False, 0, "verified", 3,
            "qwen3:8b", ["text_generation"], "ready", 2,
        )]
        self.defaults = []
        self.idempotency = {}
        self.contexts = []


class DefaultsConnection:
    def __init__(self, database: DefaultsDatabase) -> None:
        self.database = database

    def execute(self, sql, params=()):
        normalized = " ".join(sql.split())
        if normalized.startswith("SELECT c.connection_id,c.provider_code,c.display_name"):
            return CursorRows(self.database.models)
        if normalized.startswith("SELECT capability,connection_id,model_id,version"):
            return CursorRows(list(self.database.defaults))
        if normalized.startswith("SELECT pg_advisory_xact_lock"):
            return Cursor(None)
        if normalized.startswith("SELECT request_fingerprint,result FROM idempotency_records"):
            return Cursor(self.database.idempotency.get(tuple(params)))
        if normalized.startswith("SELECT version FROM workspace_model_defaults"):
            capability = params[2]
            item = next((row for row in self.database.defaults if row[0] == capability), None)
            return Cursor(None if item is None else (item[3],))
        if normalized.startswith("SELECT 1 FROM system_provider_connections"):
            connection_id, model_id, capability = params
            allowed = any(
                row[0] == connection_id and row[7] == model_id and capability in row[8]
                for row in self.database.models
            )
            return Cursor((1,) if allowed else None)
        if normalized.startswith("INSERT INTO workspace_model_defaults"):
            capability, connection_id, model_id, version = params[2], params[3], params[4], params[5]
            self.database.defaults = [row for row in self.database.defaults if row[0] != capability]
            self.database.defaults.append((capability, connection_id, model_id, version))
            return Cursor(None)
        if normalized.startswith("INSERT INTO idempotency_records"):
            key = tuple(params[:5])
            self.database.idempotency[key] = (params[5], getattr(params[6], "obj", params[6]))
            return Cursor(None)
        raise AssertionError(f"unexpected SQL: {normalized}")


class CursorRows(Cursor):
    def __init__(self, rows):
        super().__init__(None)
        self._rows = rows

    def fetchall(self):
        return self._rows


class DefaultsStore:
    def __init__(self, database: DefaultsDatabase) -> None:
        self.database = database

    @contextmanager
    def _transaction(self, access):
        self.database.contexts.append(access)
        yield DefaultsConnection(self.database)


def defaults_context() -> WorkspaceModelDefaultsContext:
    return WorkspaceModelDefaultsContext(
        tenant_id="tenant-001", workspace_id="workspace-001", actor_id="actor-001",
        trace_id="trace-001", policy_version="policy-v1",
    )


def test_workspace_model_defaults_are_safe_versioned_and_durably_replayed() -> None:
    database = DefaultsDatabase()
    service = PostgresWorkspaceModelDefaultsService(DefaultsStore(database))
    initial = service.read(defaults_context())

    assert initial["available_models"][0]["display_name"] == "LAN Ollama"
    assert "base_url" not in repr(initial)
    assert "credential_digest" not in repr(initial)
    assert "encrypted_credential" not in repr(initial)
    result, replayed = service.save(
        defaults_context(), capability="text_generation", connection_id="ollama-lan",
        model_id="qwen3:8b", expected_version=0, expected_etag=initial["etag"],
        idempotency_key="workspace-default-0001",
    )
    retry, was_replayed = service.save(
        defaults_context(), capability="text_generation", connection_id="ollama-lan",
        model_id="qwen3:8b", expected_version=0, expected_etag=initial["etag"],
        idempotency_key="workspace-default-0001",
    )

    assert replayed is False
    assert was_replayed is True
    assert retry == result
    assert result["defaults"] == [{
        "capability": "text_generation", "connection_id": "ollama-lan",
        "model_id": "qwen3:8b", "version": 1,
    }]
    assert database.contexts[-1].workspace_id == "workspace-001"


def test_workspace_model_defaults_reject_reused_key_and_unavailable_model() -> None:
    database = DefaultsDatabase()
    service = PostgresWorkspaceModelDefaultsService(DefaultsStore(database))
    initial = service.read(defaults_context())
    service.save(
        defaults_context(), capability="text_generation", connection_id="ollama-lan",
        model_id="qwen3:8b", expected_version=0, expected_etag=initial["etag"],
        idempotency_key="workspace-default-0002",
    )
    with pytest.raises(WorkspaceModelDefaultsError, match="^IDEMPOTENCY_KEY_REUSED$"):
        service.save(
            defaults_context(), capability="text_generation", connection_id="ollama-lan",
            model_id="different", expected_version=0, expected_etag=initial["etag"],
            idempotency_key="workspace-default-0002",
        )

    next_snapshot = service.read(defaults_context())
    with pytest.raises(WorkspaceModelDefaultsError, match="^WORKSPACE_MODEL_DEFAULT_UNAVAILABLE$"):
        service.save(
            defaults_context(), capability="text_generation", connection_id="ollama-lan",
            model_id="missing-model", expected_version=1, expected_etag=next_snapshot["etag"],
            idempotency_key="workspace-default-0003",
        )
