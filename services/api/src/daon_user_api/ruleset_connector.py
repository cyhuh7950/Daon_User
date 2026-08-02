from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone


class RuleSetError(ValueError):
    pass


@dataclass(frozen=True)
class RuleSetSnapshot:
    ruleset_id: str
    version: int
    expires_at: str
    revoked: bool = False


@dataclass(frozen=True)
class RuleSetEvaluation:
    status: str
    ruleset_id: str
    version: int | None
    binding_mode: str
    audit_reason: str


class RuleSetConnector:
    def __init__(self) -> None:
        self._snapshots: dict[str, RuleSetSnapshot] = {}

    def publish(self, ruleset_id: str, *, version: int, expires_at: str) -> None:
        if not ruleset_id or version < 1:
            raise ValueError("invalid ruleset")
        self._snapshots[ruleset_id] = RuleSetSnapshot(ruleset_id, version, expires_at)

    def revoke(self, ruleset_id: str) -> None:
        snapshot = self._snapshots.get(ruleset_id)
        if snapshot is not None:
            self._snapshots[ruleset_id] = RuleSetSnapshot(snapshot.ruleset_id, snapshot.version, snapshot.expires_at, True)

    @staticmethod
    def _valid(snapshot: RuleSetSnapshot) -> bool:
        expires = datetime.fromisoformat(snapshot.expires_at.replace("Z", "+00:00"))
        return not snapshot.revoked and expires > datetime.now(timezone.utc)

    def evaluate(self, feature_id: str, *, binding_mode: str, ruleset_id: str) -> RuleSetEvaluation:
        if binding_mode not in {"optional", "forced"}:
            raise RuleSetError("BINDING_MODE_INVALID")
        snapshot = self._snapshots.get(ruleset_id)
        if snapshot is None or not self._valid(snapshot):
            if binding_mode == "forced":
                raise RuleSetError("RULESET_UNAVAILABLE")
            return RuleSetEvaluation("warn_and_skip", ruleset_id, None, binding_mode, "RULESET_UNAVAILABLE")
        return RuleSetEvaluation("applied", snapshot.ruleset_id, snapshot.version, binding_mode, "RULESET_APPLIED")
