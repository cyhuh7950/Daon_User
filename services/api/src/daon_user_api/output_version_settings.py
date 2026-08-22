"""Workspace output format defaults and immutable version policy."""

from __future__ import annotations

import re
from dataclasses import dataclass
from threading import RLock
from typing import Mapping, Protocol

_SAFE_ID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:-]{0,127}$")
OUTPUT_FORMATS = {
    "evidence_report": frozenset({"pdf", "docx"}),
    "compliance_checklist": frozenset({"xlsx", "csv", "pdf"}),
    "comparison_table": frozenset({"xlsx", "csv", "pdf"}),
    "knowledge_graph": frozenset({"json", "svg", "png"}),
    "business_draft": frozenset({"docx", "pdf"}),
}
DEFAULT_OUTPUT_FORMATS = {
    "evidence_report": "pdf",
    "compliance_checklist": "xlsx",
    "comparison_table": "xlsx",
    "knowledge_graph": "json",
    "business_draft": "docx",
}


class OutputVersionSettingsError(RuntimeError):
    def __init__(self, code: str, status: int = 400, *, retryable: bool = False) -> None:
        self.code, self.status, self.retryable = code, status, retryable
        super().__init__(code)


@dataclass(frozen=True, slots=True)
class OutputVersionSettingsContext:
    tenant_id: str
    workspace_id: str
    actor_id: str

    def __post_init__(self) -> None:
        if any(not isinstance(value, str) or not _SAFE_ID.fullmatch(value) for value in (self.tenant_id, self.workspace_id, self.actor_id)):
            raise OutputVersionSettingsError("OUTPUT_VERSION_SETTINGS_CONTEXT_INVALID")


@dataclass(frozen=True, slots=True)
class OutputVersionSettingsView:
    workspace_id: str
    default_formats: dict[str, str]
    version_save_mode: str
    version: int

    @property
    def etag(self) -> str:
        return f'"output-version-settings:{self.workspace_id}:{self.version}"'


class OutputVersionSettingsRepository(Protocol):
    def read(self, context: OutputVersionSettingsContext) -> OutputVersionSettingsView | None: ...
    def save(self, context: OutputVersionSettingsContext, formats: dict[str, str], expected_version: int, idempotency_key: str) -> OutputVersionSettingsView: ...


def validate_formats(formats: Mapping[str, object]) -> dict[str, str]:
    if set(formats) != set(OUTPUT_FORMATS):
        raise OutputVersionSettingsError("OUTPUT_VERSION_SETTINGS_INVALID")
    validated: dict[str, str] = {}
    for output_type, allowed in OUTPUT_FORMATS.items():
        value = formats.get(output_type)
        if not isinstance(value, str) or value not in allowed:
            raise OutputVersionSettingsError("OUTPUT_VERSION_SETTINGS_INVALID")
        validated[output_type] = value
    return validated


class OutputVersionSettingsService:
    def __init__(self, repository: OutputVersionSettingsRepository) -> None:
        self._repository = repository

    def get(self, context: OutputVersionSettingsContext) -> OutputVersionSettingsView:
        stored = self._repository.read(context)
        return stored or OutputVersionSettingsView(context.workspace_id, dict(DEFAULT_OUTPUT_FORMATS), "append_only", 0)

    def save(self, context: OutputVersionSettingsContext, formats: Mapping[str, object], *, expected_version: int, idempotency_key: str) -> OutputVersionSettingsView:
        if not isinstance(expected_version, int) or expected_version < 0 or not isinstance(idempotency_key, str) or not 16 <= len(idempotency_key) <= 128 or _SAFE_ID.fullmatch(idempotency_key) is None:
            raise OutputVersionSettingsError("OUTPUT_VERSION_SETTINGS_INVALID")
        return self._repository.save(context, validate_formats(formats), expected_version, idempotency_key)


class ReferenceOutputVersionSettingsRepository:
    """Process-local adapter used only when the cloud repository is unavailable."""

    def __init__(self) -> None:
        self._lock = RLock()
        self._values: dict[tuple[str, str], OutputVersionSettingsView] = {}
        self._replays: dict[tuple[str, str, str, str], tuple[dict[str, str], int, int]] = {}

    @staticmethod
    def _scope(context: OutputVersionSettingsContext) -> tuple[str, str]:
        return context.tenant_id, context.workspace_id

    def read(self, context: OutputVersionSettingsContext) -> OutputVersionSettingsView | None:
        with self._lock:
            return self._values.get(self._scope(context))

    def save(
        self,
        context: OutputVersionSettingsContext,
        formats: dict[str, str],
        expected_version: int,
        idempotency_key: str,
    ) -> OutputVersionSettingsView:
        scope = self._scope(context)
        replay_key = (*scope, context.actor_id, idempotency_key)
        with self._lock:
            replay = self._replays.get(replay_key)
            if replay is not None:
                replay_formats, replay_expected_version, replay_version = replay
                if replay_formats != formats or replay_expected_version != expected_version:
                    raise OutputVersionSettingsError("IDEMPOTENCY_KEY_REUSED", 409)
                return OutputVersionSettingsView(
                    context.workspace_id, dict(replay_formats), "append_only", replay_version,
                )
            current = self._values.get(scope)
            actual = 0 if current is None else current.version
            if actual != expected_version:
                raise OutputVersionSettingsError("VERSION_CONFLICT", 409)
            stored = OutputVersionSettingsView(
                context.workspace_id, dict(formats), "append_only", actual + 1,
            )
            self._values[scope] = stored
            self._replays[replay_key] = (dict(formats), expected_version, stored.version)
            return stored
