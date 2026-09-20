"""User-owned Provider credentials and system-first fallback resolution."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, TYPE_CHECKING, Sequence

from .provider_credentials import EncryptedCredential, ProviderCredentialCipher, ProviderCredentialError

if TYPE_CHECKING:
    from .cloud_storage import CloudAccessContext, PostgresCloudStore


class UserProviderCredentialError(RuntimeError):
    def __init__(self, code: str, status: int = 400) -> None:
        self.code = code
        self.status = status
        super().__init__(code)


@dataclass(frozen=True, slots=True)
class UserProviderCredentialView:
    connection_id: str
    provider_code: str
    configured: bool
    credential_version: int


@dataclass(frozen=True, slots=True)
class UserProviderCredentialResult:
    credential: bytes | None = field(repr=False)
    source: str
    credential_version: int = 0


def resolve_credential_candidates(
    system_credential: bytes | None, user_credential: bytes | None,
) -> UserProviderCredentialResult:
    if system_credential:
        return UserProviderCredentialResult(system_credential, "system")
    if user_credential:
        return UserProviderCredentialResult(user_credential, "user")
    return UserProviderCredentialResult(None, "none")


def _sealed(row: Sequence[Any]) -> EncryptedCredential:
    return EncryptedCredential(
        ciphertext=bytes(row[0]), nonce=bytes(row[1]),
        encryption_key_version=int(row[2]), credential_version=int(row[3]),
        schema_version=int(row[4]),
    )


class PostgresUserProviderCredentialService:
    """Keep personal keys tenant/user scoped and never return plaintext to callers."""

    def __init__(self, store: "PostgresCloudStore", cipher: ProviderCredentialCipher) -> None:
        self._store = store
        self._cipher = cipher

    @staticmethod
    def _cloud(tenant_id: str, user_id: str, capability: str) -> "CloudAccessContext":
        from .cloud_storage import CloudAccessContext
        return CloudAccessContext(tenant_id, f"user:{user_id}", user_id, capability)

    @staticmethod
    def _validate_version(version: int) -> None:
        if version < 0:
            raise UserProviderCredentialError("PROVIDER_CREDENTIAL_VERSION_INVALID")

    def list_credentials(self, *, tenant_id: str, user_id: str) -> list[UserProviderCredentialView]:
        with self._store._transaction(self._cloud(tenant_id, user_id, "provider_credential.read")) as connection:
            rows = connection.execute(
                "SELECT connection_id,provider_code,credential_version "
                "FROM user_provider_credentials WHERE tenant_id=%s AND user_id=%s "
                "ORDER BY connection_id",
                (tenant_id, user_id),
            ).fetchall()
        return [UserProviderCredentialView(str(row[0]), str(row[1]), True, int(row[2])) for row in rows]

    def replace_credential(
        self, *, tenant_id: str, user_id: str, connection_id: str, credential: str,
        expected_version: int,
    ) -> UserProviderCredentialView:
        self._validate_version(expected_version)
        if not credential or len(credential) > 16384:
            raise UserProviderCredentialError("PROVIDER_CREDENTIAL_INVALID")
        with self._store._transaction(self._cloud(tenant_id, user_id, "provider_credential.write")) as connection:
            provider = connection.execute(
                "SELECT provider_code FROM system_provider_connections "
                "WHERE connection_id=%s AND enabled=true",
                (connection_id,),
            ).fetchone()
            if provider is None:
                raise UserProviderCredentialError("PROVIDER_CONNECTION_NOT_FOUND", 404)
            provider_code = str(provider[0])
            current = connection.execute(
                "SELECT credential_version FROM user_provider_credentials "
                "WHERE tenant_id=%s AND user_id=%s AND connection_id=%s FOR UPDATE",
                (tenant_id, user_id, connection_id),
            ).fetchone()
            actual = 0 if current is None else int(current[0])
            if actual != expected_version:
                raise UserProviderCredentialError("VERSION_CONFLICT", 409)
            next_version = actual + 1
            sealed = self._cipher.encrypt(connection_id, provider_code, next_version, credential.encode("utf-8"))
            connection.execute(
                "INSERT INTO user_provider_credentials "
                "(tenant_id,user_id,connection_id,provider_code,encrypted_credential,credential_nonce,"
                "encryption_key_version,credential_schema_version,credential_version) "
                "VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s) "
                "ON CONFLICT (tenant_id,user_id,connection_id) DO UPDATE SET "
                "provider_code=excluded.provider_code,encrypted_credential=excluded.encrypted_credential,"
                "credential_nonce=excluded.credential_nonce,encryption_key_version=excluded.encryption_key_version,"
                "credential_schema_version=excluded.credential_schema_version,credential_version=excluded.credential_version,"
                "updated_at=now()",
                (tenant_id, user_id, connection_id, provider_code, sealed.ciphertext, sealed.nonce,
                 sealed.encryption_key_version, sealed.schema_version, next_version),
            )
        return UserProviderCredentialView(connection_id, provider_code, True, next_version)

    def delete_credential(self, *, tenant_id: str, user_id: str, connection_id: str, expected_version: int) -> bool:
        self._validate_version(expected_version)
        with self._store._transaction(self._cloud(tenant_id, user_id, "provider_credential.write")) as connection:
            row = connection.execute(
                "SELECT credential_version FROM user_provider_credentials "
                "WHERE tenant_id=%s AND user_id=%s AND connection_id=%s FOR UPDATE",
                (tenant_id, user_id, connection_id),
            ).fetchone()
            if row is None:
                return False
            if int(row[0]) != expected_version:
                raise UserProviderCredentialError("VERSION_CONFLICT", 409)
            connection.execute(
                "DELETE FROM user_provider_credentials WHERE tenant_id=%s AND user_id=%s AND connection_id=%s",
                (tenant_id, user_id, connection_id),
            )
        return True

    def resolve_credential(
        self, *, tenant_id: str, user_id: str, connection_id: str, prefer_user: bool = False,
    ) -> UserProviderCredentialResult:
        """Resolve system credential first; personal key is strictly same-user fallback."""
        with self._store._transaction(self._cloud(tenant_id, user_id, "provider_credential.resolve")) as connection:
            system = connection.execute(
                "SELECT provider_code,encrypted_credential,credential_nonce,encryption_key_version,"
                "credential_schema_version,credential_version FROM system_provider_connections "
                "WHERE connection_id=%s AND enabled=true",
                (connection_id,),
            ).fetchone()
            if system is None:
                raise UserProviderCredentialError("PROVIDER_CONNECTION_NOT_FOUND", 404)
            provider_code = str(system[0])
            if system[1] is not None and not prefer_user:
                try:
                    system_value = self._cipher.decrypt(
                        connection_id, provider_code, int(system[5]), _sealed(system[1:6])
                    )
                    return UserProviderCredentialResult(system_value, "system", int(system[5]))
                except ProviderCredentialError:
                    pass
            personal = connection.execute(
                "SELECT encrypted_credential,credential_nonce,encryption_key_version,"
                "credential_schema_version,credential_version FROM user_provider_credentials "
                "WHERE tenant_id=%s AND user_id=%s AND connection_id=%s",
                (tenant_id, user_id, connection_id),
            ).fetchone()
            if personal is not None:
                try:
                    user_value = self._cipher.decrypt(
                        connection_id, provider_code, int(personal[4]), _sealed(personal)
                    )
                    return UserProviderCredentialResult(user_value, "user", int(personal[4]))
                except ProviderCredentialError:
                    pass
        return resolve_credential_candidates(None, None)
