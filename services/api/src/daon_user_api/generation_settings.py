from __future__ import annotations

from dataclasses import dataclass


class GenerationSettingsError(ValueError):
    pass


@dataclass(frozen=True)
class GenerationSettingsSnapshot:
    purpose: str
    audience: str
    source_versions: tuple[str, ...]
    ruleset_version: str | None
    format: str
    review_condition: str


class GenerationRequest:
    def __init__(self, output_type: str) -> None:
        if not output_type:
            raise ValueError("output_type required")
        self.output_type = output_type
        self.status = "configuring"
        self._snapshot: GenerationSettingsSnapshot | None = None

    def configure(
        self,
        purpose: str,
        audience: str,
        source_versions: list[str],
        ruleset_version: str | None,
        format: str,
        review_condition: str,
    ) -> None:
        if self.status != "configuring":
            raise GenerationSettingsError("REQUEST_LOCKED")
        self._snapshot = GenerationSettingsSnapshot(purpose, audience, tuple(source_versions), ruleset_version, format, review_condition)

    def confirm(self) -> GenerationSettingsSnapshot:
        if self.status != "configuring" or self._snapshot is None:
            raise GenerationSettingsError("SETTINGS_INCOMPLETE")
        if not self._snapshot.purpose or not self._snapshot.audience or not self._snapshot.source_versions or not self._snapshot.format or not self._snapshot.review_condition:
            raise GenerationSettingsError("SETTINGS_INCOMPLETE")
        self.status = "confirmed"
        return self._snapshot

    def submit(self) -> str:
        if self.status != "confirmed":
            raise GenerationSettingsError("SETTINGS_NOT_CONFIRMED")
        self.status = "submitted"
        return self.status
