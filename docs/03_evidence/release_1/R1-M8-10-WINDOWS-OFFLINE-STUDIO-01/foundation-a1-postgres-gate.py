"""Secret-free actual PostgreSQL A1 security-audit gate.

The caller owns a disposable database at schema revision 0015 and provides its
DSN only through DAON_TEST_POSTGRES_DSN. This script never prints the DSN.
"""

from __future__ import annotations

import json
import os
from datetime import datetime, timezone

import psycopg

from daon_user_api.audit import (
    ActorType,
    AuditEventDraft,
    AuditOutcome,
    PostgresSecurityAuditStore,
)


def draft(*, tenant_id: str, event_id: str, sequence: int) -> AuditEventDraft:
    return AuditEventDraft(
        event_id=event_id,
        occurred_at=datetime(2026, 8, 14, 13, sequence, tzinfo=timezone.utc),
        actor_id=f"actor-{tenant_id}",
        actor_type=ActorType.USER,
        tenant_id=tenant_id,
        workspace_id=None,
        action="identity.step_up.issued",
        target_type="step_up",
        target_id=f"step-up-{sequence}",
        outcome=AuditOutcome.SUCCEEDED,
        trace_id=f"trace-a1-{sequence}",
        policy_version="identity-policy-v1",
        metadata={"reason_code": "STEP_UP_ISSUED"},
    )


def main() -> None:
    dsn = os.environ["DAON_TEST_POSTGRES_DSN"]
    first_store = PostgresSecurityAuditStore(dsn)
    first = first_store.append(draft(tenant_id="tenant-a1-a", event_id="audit-a1-a-001", sequence=1))
    second = first_store.append(draft(tenant_id="tenant-a1-a", event_id="audit-a1-a-002", sequence=2))
    foreign = first_store.append(draft(tenant_id="tenant-a1-b", event_id="audit-a1-b-001", sequence=3))
    first_store.close()

    restarted = PostgresSecurityAuditStore(dsn)
    page_a = restarted.list(tenant_id="tenant-a1-a")
    page_b = restarted.list(tenant_id="tenant-a1-b")
    read_a = restarted.read(first.event_id, tenant_id="tenant-a1-a")
    cross_read = restarted.read(first.event_id, tenant_id="tenant-a1-b")
    integrity_a = restarted.verify_integrity(tenant_id="tenant-a1-a")
    restarted.close()

    assert [item.event_id for item in page_a.items] == [first.event_id, second.event_id]
    assert [item.event_id for item in page_b.items] == [foreign.event_id]
    assert read_a == first
    assert cross_read is None
    assert integrity_a.valid and integrity_a.checked_count == 2

    cross_tenant_write_denied = False
    app_update_denied = False
    immutable_trigger_denied = False
    with psycopg.connect(dsn, autocommit=False) as connection:
        try:
            with connection.transaction():
                connection.execute("SET LOCAL ROLE daon_app")
                connection.execute(
                    "SELECT set_config('app.tenant_id', %s, true)", ("tenant-a1-a",)
                )
                connection.execute(
                    """INSERT INTO security_audit_events
                    (tenant_id,sequence,event_id,occurred_at,actor_id,actor_type,workspace_id,
                     action,target_type,target_id,outcome,trace_id,policy_version,before_value,
                     after_value,metadata,safe_code,previous_event_hash,event_hash)
                    VALUES (%s,99,%s,now(),%s,'user',NULL,%s,'step_up',%s,'denied',%s,%s,
                            NULL,NULL,'{}'::jsonb,NULL,%s,%s)""",
                    (
                        "tenant-a1-b",
                        "audit-cross-write-denied",
                        "actor-tenant-a1-b",
                        "identity.step_up.denied",
                        "step-up-cross",
                        "trace-a1-cross",
                        "identity-policy-v1",
                        "0" * 64,
                        "1" * 64,
                    ),
                )
        except psycopg.Error as error:
            cross_tenant_write_denied = error.sqlstate == "42501"

        try:
            with connection.transaction():
                connection.execute("SET LOCAL ROLE daon_app")
                connection.execute(
                    "SELECT set_config('app.tenant_id', %s, true)", ("tenant-a1-a",)
                )
                connection.execute(
                    "UPDATE security_audit_events SET action=%s WHERE tenant_id=%s AND sequence=1",
                    ("identity.step_up.changed", "tenant-a1-a"),
                )
        except psycopg.Error as error:
            app_update_denied = error.sqlstate in {"42501", "55000"}

        try:
            with connection.transaction():
                connection.execute(
                    "UPDATE security_audit_events SET action=%s WHERE tenant_id=%s AND sequence=1",
                    ("identity.step_up.changed", "tenant-a1-a"),
                )
        except psycopg.Error as error:
            immutable_trigger_denied = error.sqlstate == "55000"

        with connection.transaction():
            cross_count = connection.execute(
                "SELECT count(*) FROM security_audit_events WHERE event_id=%s",
                ("audit-cross-write-denied",),
            ).fetchone()

    assert cross_tenant_write_denied
    assert app_update_denied
    assert immutable_trigger_denied
    assert cross_count is not None and int(cross_count[0]) == 0
    print(
        json.dumps(
            {
                "postgres_security_audit_restart": "pass",
                "tenant_a_events": len(page_a.items),
                "tenant_b_events": len(page_b.items),
                "cross_tenant_read": 0,
                "cross_tenant_write": 0,
                "app_update": "denied",
                "immutable_trigger": "denied_55000",
                "integrity": integrity_a.message,
            },
            sort_keys=True,
            separators=(",", ":"),
        )
    )


if __name__ == "__main__":
    main()
