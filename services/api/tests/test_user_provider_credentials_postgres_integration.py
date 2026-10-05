from __future__ import annotations

import os
import secrets
from contextlib import contextmanager

import psycopg
import pytest

from daon_user_api import user_provider_credentials as user_credentials_module
from daon_user_api.provider_credentials import ProviderCredentialCipher
from daon_user_api.user_provider_credentials import PostgresUserProviderCredentialService


INTEGRATION_DSN = os.environ.get("DAON_USER_CREDENTIAL_INTEGRATION_DSN")


class _RollbackFixtureTransaction(Exception):
    pass


def _assert_secret_matches(actual: bytes | None, expected: bytes, label: str) -> None:
    if actual != expected:
        raise AssertionError(f"{label} mismatch")


class _SharedTransactionStore:
    """Use real PostgreSQL statements while keeping every fixture in one rollback."""

    def __init__(self, connection: psycopg.Connection) -> None:
        self.connection = connection

    @contextmanager
    def _transaction(self, context):
        with self.connection.transaction():
            self.connection.execute("SELECT set_config('app.tenant_id', %s, true)", (context.tenant_id,))
            self.connection.execute("SELECT set_config('app.workspace_id', %s, true)", (context.workspace_id,))
            self.connection.execute("SELECT set_config('app.actor_id', %s, true)", (context.actor_id,))
            self.connection.execute("SELECT set_config('app.capability', %s, true)", (context.capability,))
            yield self.connection


@pytest.mark.skipif(not INTEGRATION_DSN, reason="set DAON_USER_CREDENTIAL_INTEGRATION_DSN for PostgreSQL integration")
def test_postgres_personal_v1_reentry_v2_scope_and_system_v1_rollback(monkeypatch) -> None:
    assert INTEGRATION_DSN
    suffix = secrets.token_hex(8)
    connection_id = f"c8-personal-{suffix}"
    system_connection_id = f"c8-system-{suffix}"
    tenant_id, user_id = f"tenant-{suffix}", f"user-{suffix}"
    other_tenant_id, other_user_id = f"tenant-other-{suffix}", f"user-other-{suffix}"
    provider_code = "UPSTAGE"
    cipher = ProviderCredentialCipher(secrets.token_bytes(32), encryption_key_version=1)
    legacy_credential = secrets.token_urlsafe(32).encode("utf-8")
    personal_credential = secrets.token_urlsafe(32)
    system_credential = secrets.token_urlsafe(32).encode("utf-8")
    adapter_requests = []
    verification_calls = []

    class _Adapter:
        def verify(self, profile, credential, **_kwargs) -> None:
            verification_calls.append((
                profile.connection_id, profile.provider_code, credential == personal_credential,
            ))
            return None

    class _AdapterRegistryStub:
        def adapter(self, requested_provider, adapter_type=""):
            adapter_requests.append((requested_provider, adapter_type))
            return _Adapter()

    monkeypatch.setattr(user_credentials_module, "AdapterRegistry", _AdapterRegistryStub)

    # The application service opens nested transactions on this same real DB
    # connection; the sentinel rolls back the enclosing transaction at the end.
    try:
        with psycopg.connect(INTEGRATION_DSN) as connection:
            with connection.transaction():
                revision = connection.execute("SELECT version_num FROM alembic_version").fetchone()
                assert revision is not None and revision[0] == "0053", "C8 integration requires schema revision 0053"

                connection.execute(
                    "INSERT INTO system_provider_connections "
                    "(connection_id,provider_code,display_name,base_url,access_mode,credential_requirement,"
                    "enabled,updated_by,trace_id,policy_version) "
                    "VALUES (%s,%s,%s,%s,'personal','required',true,%s,%s,%s)",
                    (connection_id, provider_code, "C8 synthetic personal", "https://provider.invalid/v1",
                     "c8-integration", f"trace-{suffix}", "policy-c8-integration"),
                )
                legacy_v1 = cipher.encrypt(connection_id, provider_code, 1, legacy_credential)
                connection.execute(
                    "INSERT INTO user_provider_credentials "
                    "(tenant_id,user_id,connection_id,provider_code,encrypted_credential,credential_nonce,"
                    "encryption_key_version,credential_schema_version,credential_version,verification_status,verified_at) "
                    "VALUES (%s,%s,%s,%s,%s,%s,%s,%s,1,'verified',now())",
                    (tenant_id, user_id, connection_id, provider_code, legacy_v1.ciphertext, legacy_v1.nonce,
                     legacy_v1.encryption_key_version, legacy_v1.schema_version),
                )

                system_v1 = cipher.encrypt(system_connection_id, provider_code, 1, system_credential)
                connection.execute(
                    "INSERT INTO system_provider_connections "
                    "(connection_id,provider_code,display_name,base_url,encrypted_credential,credential_nonce,"
                    "encryption_key_version,credential_schema_version,credential_version,enabled,verification_status,"
                    "verified_at,version,updated_by,trace_id,policy_version,access_mode,credential_requirement) "
                    "VALUES (%s,%s,%s,%s,%s,%s,%s,%s,1,true,'verified',now(),1,%s,%s,%s,'public','required')",
                    (system_connection_id, provider_code, "C8 synthetic system", "https://provider.invalid/v1",
                     system_v1.ciphertext, system_v1.nonce, system_v1.encryption_key_version,
                     system_v1.schema_version, "c8-integration", f"trace-system-{suffix}", "policy-c8-integration"),
                )

                service = PostgresUserProviderCredentialService(_SharedTransactionStore(connection), cipher)
                legacy_view = service.list_credentials(tenant_id=tenant_id, user_id=user_id)
                assert len(legacy_view) == 1
                assert legacy_view[0].configured is False
                assert legacy_view[0].verification_status == "unverified"
                assert service.resolve_credential(
                    tenant_id=tenant_id, user_id=user_id, connection_id=connection_id,
                ).source == "none"

                replaced = service.replace_credential(
                    tenant_id=tenant_id, user_id=user_id, connection_id=connection_id,
                    credential=personal_credential, expected_version=1,
                )
                assert replaced.configured is True and replaced.verification_status == "verified"
                assert replaced.credential_version == 2
                assert adapter_requests == [(provider_code, "")]
                assert verification_calls == [(connection_id, provider_code, True)]
                listed = service.list_credentials(tenant_id=tenant_id, user_id=user_id)
                assert listed[0].configured is True and listed[0].verification_status == "verified"

                stored = connection.execute(
                    "SELECT encrypted_credential,credential_nonce,encryption_key_version,"
                    "credential_schema_version,credential_version FROM user_provider_credentials "
                    "WHERE tenant_id=%s AND user_id=%s AND connection_id=%s",
                    (tenant_id, user_id, connection_id),
                ).fetchone()
                assert stored is not None and stored[3:] == (2, 2)
                decrypted = cipher.decrypt(
                    connection_id, provider_code, 2,
                    user_credentials_module._sealed(stored), tenant_id=tenant_id, user_id=user_id,
                )
                _assert_secret_matches(decrypted, personal_credential.encode("utf-8"), "personal credential")
                resolved = service.resolve_credential(
                    tenant_id=tenant_id, user_id=user_id, connection_id=connection_id,
                )
                assert resolved.source == "user" and resolved.credential_version == 2
                _assert_secret_matches(
                    resolved.credential, personal_credential.encode("utf-8"), "resolved personal credential",
                )

                # Simulate ciphertext substitution under both a different user
                # and a different tenant; the database row is present, but AAD
                # must make it unusable in either scope.
                for substituted_tenant, substituted_user in (
                    (tenant_id, other_user_id), (other_tenant_id, user_id),
                ):
                    connection.execute(
                        "INSERT INTO user_provider_credentials "
                        "(tenant_id,user_id,connection_id,provider_code,encrypted_credential,credential_nonce,"
                        "encryption_key_version,credential_schema_version,credential_version,verification_status,verified_at) "
                        "VALUES (%s,%s,%s,%s,%s,%s,%s,2,2,'verified',now())",
                        (substituted_tenant, substituted_user, connection_id, provider_code,
                         stored[0], stored[1], stored[2]),
                    )
                    denied = service.resolve_credential(
                        tenant_id=substituted_tenant, user_id=substituted_user, connection_id=connection_id,
                    )
                    assert denied.source == "none" and denied.credential is None

                system_resolved = service.resolve_credential(
                    tenant_id=tenant_id, user_id=user_id, connection_id=system_connection_id,
                )
                assert system_resolved.source == "system"
                _assert_secret_matches(system_resolved.credential, system_credential, "system credential")
                raise _RollbackFixtureTransaction()
    except _RollbackFixtureTransaction:
        pass
    finally:
        # Verify rollback from another connection even if a SQL statement or
        # assertion above fails; never leave synthetic rows behind on failure.
        with psycopg.connect(INTEGRATION_DSN) as verification:
            revision = verification.execute("SELECT version_num FROM alembic_version").fetchone()
            assert revision is not None and revision[0] == "0053"
            remaining = verification.execute(
                "SELECT count(*) FROM user_provider_credentials WHERE connection_id=%s", (connection_id,),
            ).fetchone()[0]
            assert remaining == 0
            system_remaining = verification.execute(
                "SELECT count(*) FROM system_provider_connections WHERE connection_id IN (%s,%s)",
                (connection_id, system_connection_id),
            ).fetchone()[0]
            assert system_remaining == 0
