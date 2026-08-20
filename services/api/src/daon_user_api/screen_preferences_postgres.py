"""PostgreSQL adapter for the user-only screen preference boundary."""

from __future__ import annotations

from .cloud_storage import CloudAccessContext, CloudDatabaseError, PostgresCloudStore
from .screen_preferences import (
    ScreenPreferenceContext,
    ScreenPreferenceError,
)


class PostgresScreenPreferenceRepository:
    def __init__(self, store: PostgresCloudStore) -> None:
        self._store = store

    @staticmethod
    def _context(context: ScreenPreferenceContext) -> CloudAccessContext:
        return CloudAccessContext(
            context.tenant_id, "user-screen-preferences", context.actor_id,
            "screen_preferences.manage",
        )

    def read(self, context: ScreenPreferenceContext) -> dict[str, str] | None:
        try:
            with self._store._transaction(self._context(context)) as connection:
                row = connection.execute(
                    "SELECT theme FROM user_screen_preferences WHERE tenant_id=%s AND actor_id=%s",
                    (context.tenant_id, context.actor_id),
                ).fetchone()
        except CloudDatabaseError as error:
            raise ScreenPreferenceError("SCREEN_PREFERENCE_UNAVAILABLE", 503) from error
        return None if row is None else {"theme": str(row[0])}

    def save(self, context: ScreenPreferenceContext, preferences: dict[str, str]) -> dict[str, str]:
        try:
            with self._store._transaction(self._context(context)) as connection:
                connection.execute(
                    "INSERT INTO user_screen_preferences (tenant_id,actor_id,theme,updated_at) VALUES (%s,%s,%s,now()) "
                    "ON CONFLICT (tenant_id,actor_id) DO UPDATE SET theme=EXCLUDED.theme,updated_at=EXCLUDED.updated_at",
                    (context.tenant_id, context.actor_id, preferences["theme"]),
                )
        except CloudDatabaseError as error:
            raise ScreenPreferenceError("SCREEN_PREFERENCE_UNAVAILABLE", 503) from error
        return dict(preferences)
