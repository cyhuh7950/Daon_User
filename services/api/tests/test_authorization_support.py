from __future__ import annotations

import sys
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

SRC = Path(__file__).resolve().parents[1] / "src"
sys.path.insert(0, str(SRC))

from daon_user_api.audit import AuditEventStore  # noqa: E402
from daon_user_api.identity import IdentityError, IdentityPrincipal  # noqa: E402


NOW = datetime(2026, 7, 29, 0, 0, tzinfo=timezone.utc)
TRACE_ID = "trace-authorization-001"
POLICY_VERSION = "authorization-policy-v1"


@dataclass
class FixedClock:
    current: datetime = NOW

    def __call__(self) -> datetime:
        return self.current


class FakeIdentityBoundary:
    def __init__(self, principal: IdentityPrincipal) -> None:
        self.principal = principal
        self.access_token = "-".join(("opaque", "access", "authorization", "tests"))
        self.grants: dict[str, tuple[str, str, str]] = {}

    def validate_access(self, access_token: str, *, trace_id: str, policy_version: str) -> IdentityPrincipal:
        if access_token != self.access_token:
            raise IdentityError("ACCESS_INVALID", 401)
        return self.principal

    def grant(self, authorization: str, *, action_group: str, target_id: str, policy_version: str) -> None:
        self.grants[authorization] = (action_group, target_id, policy_version)

    def consume_step_up(
        self, *, step_up_authorization: str | None, access_token: str,
        action_group: str, target_id: str, policy_version: str, trace_id: str,
    ) -> None:
        self.validate_access(access_token, trace_id=trace_id, policy_version=policy_version)
        if step_up_authorization is None:
            raise IdentityError("STEP_UP_REQUIRED", 403)
        expected = self.grants.pop(step_up_authorization, None)
        if expected != (action_group, target_id, policy_version):
            raise IdentityError("STEP_UP_BINDING_DENIED", 403)


class SelectiveFailAuditStore:
    def __init__(self) -> None:
        self.backing = AuditEventStore()
        self.fail_actions: set[str] = set()

    def append(self, draft: object) -> object:
        if getattr(draft, "action", "") in self.fail_actions:
            raise RuntimeError("sensitive content must not escape")
        return self.backing.append(draft)  # type: ignore[arg-type]


def principal(user_id: str, tenant_id: str = "tenant-001") -> IdentityPrincipal:
    return IdentityPrincipal(user_id, f"session-{user_id}", f"device-{user_id}", tenant_id)
