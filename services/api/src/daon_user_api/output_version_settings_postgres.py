"""PostgreSQL workspace output/version settings repository."""

from __future__ import annotations

import hashlib
import json

from .cloud_storage import CloudAccessContext, CloudDatabaseError, PostgresCloudStore
from .output_version_settings import (
    OutputVersionSettingsContext, OutputVersionSettingsError, OutputVersionSettingsView,
)


class PostgresOutputVersionSettingsRepository:
    def __init__(self, store: PostgresCloudStore) -> None:
        self._store = store

    @staticmethod
    def _context(context: OutputVersionSettingsContext) -> CloudAccessContext:
        return CloudAccessContext(context.tenant_id, context.workspace_id, context.actor_id, "output_version_settings.write")

    def read(self, context: OutputVersionSettingsContext) -> OutputVersionSettingsView | None:
        try:
            with self._store._transaction(self._context(context)) as connection:
                row = connection.execute(
                    "SELECT default_formats,version_save_mode,version FROM workspace_output_version_settings WHERE tenant_id=%s AND workspace_id=%s",
                    (context.tenant_id, context.workspace_id),
                ).fetchone()
        except CloudDatabaseError as error:
            raise OutputVersionSettingsError("OUTPUT_VERSION_SETTINGS_UNAVAILABLE", 503, retryable=True) from error
        return None if row is None else OutputVersionSettingsView(context.workspace_id, dict(row[0]), str(row[1]), int(row[2]))

    def save(self, context: OutputVersionSettingsContext, formats: dict[str, str], expected_version: int, idempotency_key: str) -> OutputVersionSettingsView:
        formats_canonical = json.dumps(formats, sort_keys=True, separators=(",", ":"))
        request_canonical = json.dumps(
            {"default_formats": formats, "expected_version": expected_version},
            sort_keys=True, separators=(",", ":"),
        )
        fingerprint = hashlib.sha256(request_canonical.encode()).hexdigest()
        try:
            with self._store._transaction(self._context(context)) as connection:
                connection.execute("SELECT pg_advisory_xact_lock(hashtextextended(%s,0))", (f"{context.tenant_id}|{context.workspace_id}|output-version-settings",))
                replay = connection.execute(
                    "SELECT request_fingerprint,response_version FROM workspace_output_version_settings_idempotency WHERE tenant_id=%s AND workspace_id=%s AND actor_id=%s AND idempotency_key=%s",
                    (context.tenant_id, context.workspace_id, context.actor_id, idempotency_key),
                ).fetchone()
                if replay is not None:
                    if str(replay[0]) != fingerprint:
                        raise OutputVersionSettingsError("IDEMPOTENCY_KEY_REUSED", 409)
                    row = connection.execute("SELECT default_formats,version_save_mode,version FROM workspace_output_version_settings WHERE tenant_id=%s AND workspace_id=%s", (context.tenant_id, context.workspace_id)).fetchone()
                    if row is None or int(row[2]) < int(replay[1]):
                        raise OutputVersionSettingsError("OUTPUT_VERSION_SETTINGS_UNAVAILABLE", 503, retryable=True)
                    return OutputVersionSettingsView(context.workspace_id, formats, "append_only", int(replay[1]))
                current = connection.execute("SELECT version FROM workspace_output_version_settings WHERE tenant_id=%s AND workspace_id=%s FOR UPDATE", (context.tenant_id, context.workspace_id)).fetchone()
                actual = 0 if current is None else int(current[0])
                if actual != expected_version:
                    raise OutputVersionSettingsError("VERSION_CONFLICT", 409)
                version = actual + 1
                connection.execute(
                    "INSERT INTO workspace_output_version_settings (tenant_id,workspace_id,default_formats,version_save_mode,version,updated_by,updated_at) VALUES (%s,%s,%s::jsonb,'append_only',%s,%s,now()) ON CONFLICT (tenant_id,workspace_id) DO UPDATE SET default_formats=EXCLUDED.default_formats,version=EXCLUDED.version,updated_by=EXCLUDED.updated_by,updated_at=EXCLUDED.updated_at",
                    (context.tenant_id, context.workspace_id, formats_canonical, version, context.actor_id),
                )
                connection.execute(
                    "INSERT INTO workspace_output_version_settings_idempotency (tenant_id,workspace_id,actor_id,idempotency_key,request_fingerprint,response_version,created_at) VALUES (%s,%s,%s,%s,%s,%s,now())",
                    (context.tenant_id, context.workspace_id, context.actor_id, idempotency_key, fingerprint, version),
                )
                return OutputVersionSettingsView(context.workspace_id, formats, "append_only", version)
        except OutputVersionSettingsError:
            raise
        except CloudDatabaseError as error:
            raise OutputVersionSettingsError("OUTPUT_VERSION_SETTINGS_UNAVAILABLE", 503, retryable=True) from error
