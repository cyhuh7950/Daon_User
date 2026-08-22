"""PostgreSQL adapter for append-only organization product licenses."""

from __future__ import annotations

import json
import hashlib

from psycopg.types.json import Jsonb

from .cloud_storage import CloudAccessContext, CloudDatabaseError, PostgresCloudStore
from .license import LicenseContext, LicenseError, VerifiedLicense


_CREATION_REQUIREMENTS = {
    "studio.generate": (
        "studio_generation",
        frozenset({"generation_runs", "studio_outputs"}),
    ),
    "source.create": ("citation", frozenset({"source_versions", "storage_bytes"})),
    "notebook.create": ("notebook_management", frozenset({"notebooks"})),
}
_USAGE_SQL = {
    "notebooks": "SELECT count(*) FROM notebooks WHERE tenant_id=%s",
    "generation_runs": "SELECT count(*) FROM generation_requests WHERE tenant_id=%s",
    "studio_outputs": "SELECT count(*) FROM studio_outputs WHERE tenant_id=%s",
    "source_versions": "SELECT count(*) FROM source_versions WHERE tenant_id=%s",
    "storage_bytes": (
        "SELECT coalesce(sum(byte_size),0) FROM object_records "
        "WHERE tenant_id=%s AND status IN ('pending','completed')"
    ),
}


def enforce_license_creation(
    connection, tenant_id: str, action: str, increments: dict[str, int]
) -> None:
    """Check feature/quota under one tenant lock inside the caller's creation transaction."""
    requirement = _CREATION_REQUIREMENTS.get(action)
    if requirement is None or not increments:
        raise LicenseError("LICENSE_CREATION_ACTION_INVALID")
    feature, allowed_resources = requirement
    if any(
        resource not in allowed_resources
        or not isinstance(amount, int)
        or isinstance(amount, bool)
        or amount < 1
        for resource, amount in increments.items()
    ):
        raise LicenseError("LICENSE_CREATION_ACTION_INVALID")
    connection.execute(
        "SELECT pg_advisory_xact_lock(hashtextextended(%s,0))",
        (f"{tenant_id}|license-creation",),
    )
    row = connection.execute(
        "SELECT expires_at,features,resource_limits FROM organization_license_versions "
        "WHERE tenant_id=%s ORDER BY version DESC LIMIT 1",
        (tenant_id,),
    ).fetchone()
    if row is None:
        raise LicenseError("LICENSE_NOT_CONFIGURED", 409)
    if connection.execute("SELECT %s <= now()", (row[0],)).fetchone()[0] is True:
        raise LicenseError("LICENSE_EXPIRED", 409)
    if feature not in tuple(str(value) for value in row[1]):
        raise LicenseError("LICENSE_FEATURE_NOT_ALLOWED", 409)
    limits = json.loads(row[2]) if isinstance(row[2], str) else dict(row[2])
    for resource, amount in increments.items():
        if resource not in limits:
            continue
        used = int(connection.execute(_USAGE_SQL[resource], (tenant_id,)).fetchone()[0])
        if used + amount > int(limits[resource]):
            raise LicenseError("LICENSE_RESOURCE_LIMIT_REACHED", 409)


class PostgresLicenseRepository:
    def __init__(self, store: PostgresCloudStore) -> None:
        self._store = store

    @staticmethod
    def _context(context: LicenseContext) -> CloudAccessContext:
        return CloudAccessContext(
            context.tenant_id,
            context.workspace_id,
            context.actor_id,
            "license.read_or_apply",
        )

    @staticmethod
    def _hydrate(row) -> VerifiedLicense:
        limits = json.loads(row[7]) if isinstance(row[7], str) else row[7]
        return VerifiedLicense(
            license_id=str(row[1]),
            product=str(row[2]),
            edition=str(row[3]),
            organization_id=str(row[0]),
            issued_at=row[4],
            expires_at=row[5],
            features=tuple(str(value) for value in row[6]),
            resource_limits=tuple(
                sorted((str(key), int(value)) for key, value in limits.items())
            ),
            claims_digest=str(row[8]),
            key_id=str(row[9]),
        )

    @staticmethod
    def _columns() -> str:
        return (
            "tenant_id,license_id,product,edition,issued_at,expires_at,features,"
            "resource_limits,claims_digest,signing_key_id"
        )

    def current(self, context: LicenseContext) -> VerifiedLicense | None:
        try:
            with self._store._transaction(self._context(context)) as connection:
                row = connection.execute(
                    f"SELECT {self._columns()} FROM organization_license_versions "
                    "WHERE tenant_id=%s ORDER BY version DESC LIMIT 1",
                    (context.tenant_id,),
                ).fetchone()
        except CloudDatabaseError as error:
            raise LicenseError("LICENSE_UNAVAILABLE", 503) from error
        return None if row is None else self._hydrate(row)

    def usage(self, context: LicenseContext) -> dict[str, int]:
        try:
            with self._store._transaction(self._context(context)) as connection:
                row = connection.execute(
                    "SELECT "
                    "(SELECT count(*) FROM user_accounts WHERE tenant_id=%s),"
                    "(SELECT coalesce(sum(byte_size),0) FROM object_records WHERE tenant_id=%s AND status IN ('pending','completed')),"
                    "(SELECT count(*) FROM generation_requests WHERE tenant_id=%s),"
                    "(SELECT count(*) FROM source_versions WHERE tenant_id=%s),"
                    "(SELECT count(*) FROM studio_outputs WHERE tenant_id=%s)",
                    (context.tenant_id,) * 5,
                ).fetchone()
                notebooks = 0
                if (
                    connection.execute("SELECT to_regclass('notebooks')").fetchone()[0]
                    is not None
                ):
                    notebooks = int(
                        connection.execute(
                            "SELECT count(*) FROM notebooks WHERE tenant_id=%s",
                            (context.tenant_id,),
                        ).fetchone()[0]
                    )
        except CloudDatabaseError as error:
            raise LicenseError("LICENSE_USAGE_UNAVAILABLE", 503) from error
        return dict(
            zip(
                (
                    "users",
                    "notebooks",
                    "storage_bytes",
                    "generation_runs",
                    "source_versions",
                    "studio_outputs",
                ),
                (
                    int(row[0]),
                    notebooks,
                    int(row[1]),
                    int(row[2]),
                    int(row[3]),
                    int(row[4]),
                ),
                strict=True,
            )
        )

    def apply(
        self,
        context: LicenseContext,
        license_value: VerifiedLicense,
        *,
        idempotency_key: str,
        request_fingerprint: str,
    ) -> tuple[VerifiedLicense, bool]:
        try:
            with self._store._transaction(self._context(context)) as connection:
                connection.execute(
                    "SELECT pg_advisory_xact_lock(hashtextextended(%s,0))",
                    (context.tenant_id,),
                )
                replay = connection.execute(
                    "SELECT request_fingerprint,license_version FROM license_apply_idempotency "
                    "WHERE tenant_id=%s AND actor_id=%s AND idempotency_key=%s",
                    (context.tenant_id, context.actor_id, idempotency_key),
                ).fetchone()
                if replay is not None:
                    if str(replay[0]) != request_fingerprint:
                        raise LicenseError("IDEMPOTENCY_KEY_REUSED", 409)
                    stored = connection.execute(
                        f"SELECT {self._columns()} FROM organization_license_versions "
                        "WHERE tenant_id=%s AND version=%s",
                        (context.tenant_id, int(replay[1])),
                    ).fetchone()
                    return self._hydrate(stored), True
                existing = connection.execute(
                    f"SELECT version,{self._columns()} FROM organization_license_versions "
                    "WHERE tenant_id=%s AND license_id=%s AND claims_digest=%s",
                    (
                        context.tenant_id,
                        license_value.license_id,
                        license_value.claims_digest,
                    ),
                ).fetchone()
                if existing is None:
                    version = int(
                        connection.execute(
                            "SELECT coalesce(max(version),0)+1 FROM organization_license_versions WHERE tenant_id=%s",
                            (context.tenant_id,),
                        ).fetchone()[0]
                    )
                    connection.execute(
                        "INSERT INTO organization_license_versions "
                        "(tenant_id,version,license_id,product,edition,issued_at,expires_at,features,resource_limits,"
                        "claims_digest,signing_key_id,applied_by,applied_at,trace_id,policy_version) "
                        "VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,now(),%s,%s)",
                        (
                            context.tenant_id,
                            version,
                            license_value.license_id,
                            license_value.product,
                            license_value.edition,
                            license_value.issued_at,
                            license_value.expires_at,
                            list(license_value.features),
                            Jsonb(dict(license_value.resource_limits)),
                            license_value.claims_digest,
                            license_value.key_id,
                            context.actor_id,
                            context.trace_id,
                            context.policy_version,
                        ),
                    )
                    stored = license_value
                else:
                    version = int(existing[0])
                    stored = self._hydrate(existing[1:])
                connection.execute(
                    "INSERT INTO license_apply_idempotency "
                    "(tenant_id,actor_id,idempotency_key,request_fingerprint,license_version,created_at) "
                    "VALUES (%s,%s,%s,%s,%s,now())",
                    (
                        context.tenant_id,
                        context.actor_id,
                        idempotency_key,
                        request_fingerprint,
                        version,
                    ),
                )
                audit_event_id = (
                    "license-"
                    + hashlib.sha256(
                        f"{context.tenant_id}|{context.actor_id}|{idempotency_key}".encode()
                    ).hexdigest()[:32]
                )
                connection.execute(
                    "INSERT INTO audit_events "
                    "(event_id,tenant_id,workspace_id,actor_id,action,target_type,target_id,outcome,"
                    "trace_id,policy_version,after_value,metadata) "
                    "VALUES (%s,%s,%s,%s,'license.organization.applied','organization_license',%s,"
                    "'succeeded',%s,%s,%s,%s)",
                    (
                        audit_event_id,
                        context.tenant_id,
                        context.workspace_id,
                        context.actor_id,
                        context.tenant_id,
                        context.trace_id,
                        context.policy_version,
                        Jsonb(
                            {
                                "product": stored.product,
                                "edition": stored.edition,
                                "expires_at": stored.expires_at.isoformat(),
                            }
                        ),
                        Jsonb(
                            {
                                "reason_code": "LICENSE_APPLIED",
                                "license_version": version,
                            }
                        ),
                    ),
                )
                return stored, False
        except LicenseError:
            raise
        except CloudDatabaseError as error:
            raise LicenseError("LICENSE_UNAVAILABLE", 503) from error

    def replay(
        self,
        context: LicenseContext,
        *,
        idempotency_key: str,
        request_fingerprint: str,
    ) -> VerifiedLicense | None:
        try:
            with self._store._transaction(self._context(context)) as connection:
                connection.execute(
                    "SELECT pg_advisory_xact_lock(hashtextextended(%s,0))",
                    (context.tenant_id,),
                )
                replay = connection.execute(
                    "SELECT request_fingerprint,license_version FROM license_apply_idempotency "
                    "WHERE tenant_id=%s AND actor_id=%s AND idempotency_key=%s",
                    (context.tenant_id, context.actor_id, idempotency_key),
                ).fetchone()
                if replay is None:
                    return None
                if str(replay[0]) != request_fingerprint:
                    raise LicenseError("IDEMPOTENCY_KEY_REUSED", 409)
                stored = connection.execute(
                    f"SELECT {self._columns()} FROM organization_license_versions "
                    "WHERE tenant_id=%s AND version=%s",
                    (context.tenant_id, int(replay[1])),
                ).fetchone()
                return self._hydrate(stored)
        except LicenseError:
            raise
        except CloudDatabaseError as error:
            raise LicenseError("LICENSE_UNAVAILABLE", 503) from error
