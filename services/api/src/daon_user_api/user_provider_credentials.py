"""User-owned Provider credentials and system-first fallback resolution."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, TYPE_CHECKING, Sequence

from .provider_connection_adapters import AdapterError, AdapterRegistry
from .provider_credentials import EncryptedCredential, ProviderConnection, ProviderCredentialCipher, ProviderCredentialError

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
    verification_status: str = "unverified"


@dataclass(frozen=True, slots=True)
class UserProviderCredentialResult:
    credential: bytes | None = field(repr=False)
    source: str
    credential_version: int = 0


def resolve_credential_candidates(
    system_credential: bytes | None, user_credential: bytes | None, *, access_mode: str = "public",
) -> UserProviderCredentialResult:
    if access_mode == "public" and system_credential:
        return UserProviderCredentialResult(system_credential, "system")
    if access_mode == "personal" and user_credential:
        return UserProviderCredentialResult(user_credential, "user")
    return UserProviderCredentialResult(None, "none")


def _sealed(row: Sequence[Any]) -> EncryptedCredential:
    return EncryptedCredential(
        ciphertext=bytes(row[0]), nonce=bytes(row[1]),
        encryption_key_version=int(row[2]), credential_version=int(row[4]),
        schema_version=int(row[3]),
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
                "SELECT connection_id,provider_code,credential_version,verification_status "
                "FROM user_provider_credentials WHERE tenant_id=%s AND user_id=%s "
                "ORDER BY connection_id",
                (tenant_id, user_id),
            ).fetchall()
        return [UserProviderCredentialView(str(row[0]), str(row[1]), True, int(row[2]), str(row[3])) for row in rows]

    def replace_credential(
        self, *, tenant_id: str, user_id: str, connection_id: str, credential: str,
        expected_version: int,
    ) -> UserProviderCredentialView:
        self._validate_version(expected_version)
        if not credential.strip() or len(credential) > 16384:
            raise UserProviderCredentialError("PROVIDER_CREDENTIAL_INVALID")
        with self._store._transaction(self._cloud(tenant_id, user_id, "provider_credential.write")) as connection:
            provider = connection.execute(
                "SELECT provider_code,base_url,access_mode,credential_requirement,enabled,adapter_type "
                "FROM system_provider_connections "
                "WHERE connection_id=%s AND enabled=true",
                (connection_id,),
            ).fetchone()
            if provider is None:
                raise UserProviderCredentialError("PROVIDER_CONNECTION_NOT_FOUND", 404)
            provider_code = str(provider[0])
            if str(provider[2]) != "personal" or str(provider[3]) != "required":
                raise UserProviderCredentialError("PROVIDER_PERSONAL_KEY_NOT_ALLOWED", 409)
            current = connection.execute(
                "SELECT credential_version FROM user_provider_credentials "
                "WHERE tenant_id=%s AND user_id=%s AND connection_id=%s FOR UPDATE",
                (tenant_id, user_id, connection_id),
            ).fetchone()
            actual = 0 if current is None else int(current[0])
            if actual != expected_version:
                raise UserProviderCredentialError("VERSION_CONFLICT", 409)
            profile = ProviderConnection(
                connection_id, provider_code, provider_code, str(provider[1]), None,
                bool(provider[4]), 1,
            )
            try:
                if provider_code in {"CUSTOM", "OMNIROUTE"}:
                    allowed = connection.execute(
                        "SELECT model_id FROM system_provider_allowed_models "
                        "WHERE connection_id=%s ORDER BY model_id",
                        (connection_id,),
                    ).fetchall()
                    model_ids = tuple(str(row[0]) for row in allowed)
                    if provider_code == "CUSTOM":
                        if not model_ids:
                            raise UserProviderCredentialError("PROVIDER_MODEL_IDS_INVALID", 409)
                        AdapterRegistry().adapter(provider_code, str(provider[5])).verify_models(
                            profile, credential, model_ids,
                        )
                    else:
                        AdapterRegistry(logical_models={connection_id: model_ids}).adapter(
                            provider_code,
                        ).verify(profile, credential)
                else:
                    AdapterRegistry().adapter(provider_code).verify(profile, credential)
            except AdapterError as error:
                raise UserProviderCredentialError(error.code, error.status) from None
            next_version = actual + 1
            sealed = self._cipher.encrypt(
                connection_id, provider_code, next_version, credential.encode("utf-8"),
                tenant_id=tenant_id, user_id=user_id,
            )
            connection.execute(
                "INSERT INTO user_provider_credentials "
                "(tenant_id,user_id,connection_id,provider_code,encrypted_credential,credential_nonce,"
                "encryption_key_version,credential_schema_version,credential_version,verification_status,verified_at) "
                "VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,'verified',now()) "
                "ON CONFLICT (tenant_id,user_id,connection_id) DO UPDATE SET "
                "provider_code=excluded.provider_code,encrypted_credential=excluded.encrypted_credential,"
                "credential_nonce=excluded.credential_nonce,encryption_key_version=excluded.encryption_key_version,"
                "credential_schema_version=excluded.credential_schema_version,credential_version=excluded.credential_version,"
                "verification_status='verified',verified_at=now(),updated_at=now()",
                (tenant_id, user_id, connection_id, provider_code, sealed.ciphertext, sealed.nonce,
                 sealed.encryption_key_version, sealed.schema_version, next_version),
            )
        return UserProviderCredentialView(connection_id, provider_code, True, next_version, "verified")

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
        """Resolve only the credential source selected by the connection policy."""
        with self._store._transaction(self._cloud(tenant_id, user_id, "provider_credential.resolve")) as connection:
            system = connection.execute(
                "SELECT provider_code,encrypted_credential,credential_nonce,encryption_key_version,"
                "credential_schema_version,credential_version,access_mode,credential_requirement "
                "FROM system_provider_connections "
                "WHERE connection_id=%s AND enabled=true",
                (connection_id,),
            ).fetchone()
            if system is None:
                raise UserProviderCredentialError("PROVIDER_CONNECTION_NOT_FOUND", 404)
            provider_code = str(system[0])
            access_mode = str(system[6])
            if access_mode == "public" and system[1] is not None:
                try:
                    system_value = self._cipher.decrypt(
                        connection_id, provider_code, int(system[5]), _sealed(system[1:6])
                    )
                    return UserProviderCredentialResult(system_value, "system", int(system[5]))
                except ProviderCredentialError:
                    return resolve_credential_candidates(None, None, access_mode="public")
            if access_mode != "personal":
                return resolve_credential_candidates(None, None, access_mode="public")
            personal = connection.execute(
                "SELECT encrypted_credential,credential_nonce,encryption_key_version,"
                "credential_schema_version,credential_version,verification_status FROM user_provider_credentials "
                "WHERE tenant_id=%s AND user_id=%s AND connection_id=%s",
                (tenant_id, user_id, connection_id),
            ).fetchone()
            if personal is not None and str(personal[5]) == "verified":
                try:
                    user_value = self._cipher.decrypt(
                        connection_id, provider_code, int(personal[4]), _sealed(personal),
                        tenant_id=tenant_id, user_id=user_id,
                    )
                    return UserProviderCredentialResult(user_value, "user", int(personal[4]))
                except ProviderCredentialError:
                    pass
        return resolve_credential_candidates(None, None, access_mode="personal")
