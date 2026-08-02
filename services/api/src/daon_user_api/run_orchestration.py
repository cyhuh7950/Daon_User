from __future__ import annotations

from dataclasses import dataclass


class RunTransitionError(ValueError):
    pass


@dataclass(frozen=True)
class RunSnapshot:
    run_id: str
    source_id: str
    source_version: int
    status: str
    history: tuple[str, ...]
    failure_code: str | None = None


class _Run:
    _ORDER = ("accepted", "planning", "retrieving", "generating", "validating", "completed")

    def __init__(self, run_id: str, source_id: str, source_version: int) -> None:
        self._run_id = run_id
        self._source_id = source_id
        self._source_version = source_version
        self._history = ["accepted"]
        self._failure_code: str | None = None

    def transition(self, status: str) -> None:
        if self._history[-1] in {"completed", "failed"}:
            raise RunTransitionError("RUN_TERMINAL")
        expected_index = self._ORDER.index(self._history[-1]) + 1
        if expected_index >= len(self._ORDER) or status != self._ORDER[expected_index]:
            raise RunTransitionError("INVALID_TRANSITION")
        self._history.append(status)

    def fail(self, code: str) -> None:
        if self._history[-1] in {"completed", "failed"}:
            raise RunTransitionError("RUN_TERMINAL")
        if not code:
            raise RunTransitionError("FAILURE_CODE_REQUIRED")
        self._failure_code = code
        self._history.append("failed")

    def snapshot(self) -> RunSnapshot:
        return RunSnapshot(
            self._run_id,
            self._source_id,
            self._source_version,
            self._history[-1],
            tuple(self._history),
            self._failure_code,
        )


class RunOrchestrator:
    def start(self, run_id: str, source_id: str, source_version: int) -> _Run:
        if not run_id or not source_id or source_version < 1:
            raise ValueError("run and source identity are required")
        return _Run(run_id, source_id, source_version)
