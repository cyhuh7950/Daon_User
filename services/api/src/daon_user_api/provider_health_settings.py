"""Validation and storage contracts for the system provider health interval."""

from __future__ import annotations

from dataclasses import dataclass
from threading import RLock
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from .cloud_storage import PostgresCloudStore
    from .provider_connection_admin import ProviderConnectionAdminContext


DEFAULT_PROVIDER_HEALTH_CHECK_INTERVAL_MINUTES = 60
MIN_PROVIDER_HEALTH_CHECK_INTERVAL_MINUTES = 1
MAX_PROVIDER_HEALTH_CHECK_INTERVAL_MINUTES = 24 * 60


class ProviderHealthSettingsError(ValueError):
    def __init__(self, code: str, status: int = 400) -> None:
        self.code = code
        self.status = status
        super().__init__(code)


@dataclass(frozen=True, slots=True)
class ProviderHealthCheckSettings:
    interval_minutes: int
    version: int


class ReferenceProviderHealthSettingsRepository:
    def __init__(self) -> None:
        self._lock = RLock()
        self._settings = ProviderHealthCheckSettings(
            DEFAULT_PROVIDER_HEALTH_CHECK_INTERVAL_MINUTES, 0,
        )

    def get(self) -> ProviderHealthCheckSettings:
        with self._lock:
            return self._settings

    def save(self, interval_minutes: int, expected_version: int) -> ProviderHealthCheckSettings:
        with self._lock:
            if self._settings.version != expected_version:
                raise ProviderHealthSettingsError("VERSION_CONFLICT", 409)
            self._settings = ProviderHealthCheckSettings(
                interval_minutes, self._settings.version + 1,
            )
            return self._settings


def _validate_interval(value: object) -> int:
    if (
        isinstance(value, bool)
        or not isinstance(value, int)
        or not MIN_PROVIDER_HEALTH_CHECK_INTERVAL_MINUTES <= value <= MAX_PROVIDER_HEALTH_CHECK_INTERVAL_MINUTES
    ):
        raise ProviderHealthSettingsError("PROVIDER_HEALTH_INTERVAL_INVALID")
    return value


class ProviderHealthCheckSettingsService:
    def __init__(self, repository: ReferenceProviderHealthSettingsRepository) -> None:
        self._repository = repository

    def get(self) -> ProviderHealthCheckSettings:
        return self._repository.get()

    def save(self, *, interval_minutes: object, expected_version: int) -> ProviderHealthCheckSettings:
        return self._repository.save(_validate_interval(interval_minutes), expected_version)


class PostgresProviderHealthSettingsRepository:
    def __init__(self, store: "PostgresCloudStore") -> None:
        self._store = store

    @staticmethod
    def _cloud(context: "ProviderConnectionAdminContext"):
        from .cloud_storage import CloudAccessContext

        return CloudAccessContext(
            context.tenant_id, f"system:{context.tenant_id}", context.actor_id,
            "provider_admin.write",
        )

    def get(self, context: "ProviderConnectionAdminContext") -> ProviderHealthCheckSettings:
        with self._store._transaction(self._cloud(context)) as connection:
            row = connection.execute(
                "SELECT interval_minutes,version FROM system_provider_health_settings "
                "WHERE setting_id='global'",
            ).fetchone()
            if row is None:
                return ProviderHealthCheckSettings(
                    DEFAULT_PROVIDER_HEALTH_CHECK_INTERVAL_MINUTES, 0,
                )
            return ProviderHealthCheckSettings(int(row[0]), int(row[1]))

    def save(
        self, context: "ProviderConnectionAdminContext", interval_minutes: int,
        expected_version: int,
    ) -> ProviderHealthCheckSettings:
        with self._store._transaction(self._cloud(context)) as connection:
            row = connection.execute(
                "UPDATE system_provider_health_settings SET interval_minutes=%s,"
                "version=version+1,updated_by=%s,trace_id=%s,policy_version=%s,updated_at=now() "
                "WHERE setting_id='global' AND version=%s "
                "RETURNING interval_minutes,version",
                (interval_minutes, context.actor_id, context.trace_id,
                 context.policy_version, expected_version),
            ).fetchone()
            if row is None:
                raise ProviderHealthSettingsError("VERSION_CONFLICT", 409)
            return ProviderHealthCheckSettings(int(row[0]), int(row[1]))


class PostgresProviderHealthCheckSettingsService:
    def __init__(self, repository: PostgresProviderHealthSettingsRepository) -> None:
        self._repository = repository

    def get(self, context: "ProviderConnectionAdminContext") -> ProviderHealthCheckSettings:
        return self._repository.get(context)

    def save(
        self, context: "ProviderConnectionAdminContext", *, interval_minutes: object,
        expected_version: int,
    ) -> ProviderHealthCheckSettings:
        return self._repository.save(
            context, _validate_interval(interval_minutes), expected_version,
        )
