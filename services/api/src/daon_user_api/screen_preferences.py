"""User-scoped screen preferences, deliberately separate from Notebook data."""

from __future__ import annotations

import re
from dataclasses import dataclass
from threading import RLock
from typing import Mapping, Protocol


_SAFE_ID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:-]{0,127}$")
_THEMES = frozenset({"system", "light", "dark"})
DEFAULT_SCREEN_PREFERENCES = {"theme": "system"}


class ScreenPreferenceError(RuntimeError):
    def __init__(self, code: str, status: int = 400) -> None:
        self.code, self.status = code, status
        super().__init__(code)


@dataclass(frozen=True, slots=True)
class ScreenPreferenceContext:
    tenant_id: str
    actor_id: str

    def __post_init__(self) -> None:
        if any(not isinstance(value, str) or _SAFE_ID.fullmatch(value) is None for value in (self.tenant_id, self.actor_id)):
            raise ScreenPreferenceError("SCREEN_PREFERENCE_CONTEXT_INVALID")


class ScreenPreferenceRepository(Protocol):
    def read(self, context: ScreenPreferenceContext) -> dict[str, str] | None: ...
    def save(self, context: ScreenPreferenceContext, preferences: dict[str, str]) -> dict[str, str]: ...


def validate_screen_preferences(value: Mapping[str, object]) -> dict[str, str]:
    if set(value) != {"theme"} or value.get("theme") not in _THEMES:
        raise ScreenPreferenceError("SCREEN_PREFERENCE_INVALID")
    return {"theme": str(value["theme"])}


class ScreenPreferenceService:
    def __init__(self, repository: ScreenPreferenceRepository) -> None:
        self._repository = repository

    def get(self, context: ScreenPreferenceContext) -> dict[str, str]:
        stored = self._repository.read(context)
        return dict(DEFAULT_SCREEN_PREFERENCES if stored is None else stored)

    def save(self, context: ScreenPreferenceContext, preferences: Mapping[str, object]) -> dict[str, str]:
        return self._repository.save(context, validate_screen_preferences(preferences))


class ReferenceScreenPreferenceRepository:
    """Non-production adapter for isolated Runtime tests only."""

    def __init__(self) -> None:
        self._lock = RLock()
        self._values: dict[tuple[str, str], dict[str, str]] = {}

    @staticmethod
    def _scope(context: ScreenPreferenceContext) -> tuple[str, str]:
        return context.tenant_id, context.actor_id

    def read(self, context: ScreenPreferenceContext) -> dict[str, str] | None:
        with self._lock:
            value = self._values.get(self._scope(context))
            return None if value is None else dict(value)

    def save(self, context: ScreenPreferenceContext, preferences: dict[str, str]) -> dict[str, str]:
        with self._lock:
            value = dict(preferences)
            self._values[self._scope(context)] = value
            return dict(value)
