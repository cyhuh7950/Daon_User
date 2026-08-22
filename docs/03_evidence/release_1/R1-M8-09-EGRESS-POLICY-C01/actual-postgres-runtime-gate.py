from __future__ import annotations

import os
import threading
import time

from psycopg import connect

from daon_user_api.cloud_storage import PostgresCloudStore
from daon_user_api.egress_policy import (
    EgressPolicyContext,
    EgressPolicyError,
    EgressPolicyPayload,
    EgressPolicyService,
)
from daon_user_api.egress_policy_postgres import PostgresEgressPolicyRepository


dsn = os.environ["DAON_TEST_POSTGRES_DSN"]
context = EgressPolicyContext(
    "egress_it_org",
    "egress_it_org",
    "egress_it_ws_a",
    "egress_gate_actor",
    "egress_gate_trace",
    "gate-policy-v1",
)
store = PostgresCloudStore(dsn, min_size=1, max_size=2)
repository = PostgresEgressPolicyRepository(store)
service = EgressPolicyService(repository)

try:
    effective = service.get_effective(context)
    assert effective.mode == "deny_external"
    assert effective.parent_locked is True
    assert effective.etag.startswith('"egress-effective:')
    assert effective.organization_etag.startswith('"egress-policy:organization:')
    assert effective.workspace_etag.startswith('"egress-policy:workspace:')

    with connect(dsn) as connection:
        before_versions = connection.execute(
            "SELECT count(*) FROM egress_policy_versions"
        ).fetchone()[0]
        before_bindings = connection.execute(
            "SELECT count(*) FROM egress_policy_bindings"
        ).fetchone()[0]
        before_audit = connection.execute(
            "SELECT count(*) FROM audit_events WHERE action='egress_policy.activate'"
        ).fetchone()[0]

    allow = EgressPolicyPayload(
        "allow_approved_external",
        ("external_api",),
        ("provider.example",),
        "restricted",
        4096,
        True,
        True,
        "organization_admin",
    )
    try:
        service.create_and_activate(
            context,
            scope_type="workspace",
            payload=allow,
            expected_etag=effective.workspace_etag,
            idempotency_key="egress-gate-parent-deny",
        )
        raise AssertionError("ORGANIZATION_DENY_WAS_RELAXED")
    except EgressPolicyError as error:
        assert error.code == "EGRESS_POLICY_DENIED"

    with connect(dsn) as connection:
        after_versions = connection.execute(
            "SELECT count(*) FROM egress_policy_versions"
        ).fetchone()[0]
        after_bindings = connection.execute(
            "SELECT count(*) FROM egress_policy_bindings"
        ).fetchone()[0]
        after_audit = connection.execute(
            "SELECT count(*) FROM audit_events WHERE action='egress_policy.activate' "
            "AND outcome='denied' AND metadata->>'safe_error_code'='EGRESS_POLICY_DENIED'"
        ).fetchone()[0]
    assert after_versions == before_versions
    assert after_bindings == before_bindings
    assert after_audit == before_audit + 1

    lock_key = "question-egress|egress_it_org|egress_it_ws_a|egress_gate_run"
    first_has_lock = threading.Event()
    release_first = threading.Event()
    second_wait: list[float] = []

    def first_transaction() -> None:
        with connect(dsn) as connection:
            with connection.transaction():
                connection.execute(
                    "SELECT pg_advisory_xact_lock(hashtextextended(%s,0))", (lock_key,)
                )
                first_has_lock.set()
                assert release_first.wait(timeout=5)

    def second_transaction() -> None:
        assert first_has_lock.wait(timeout=5)
        started = time.monotonic()
        with connect(dsn) as connection:
            with connection.transaction():
                connection.execute(
                    "SELECT pg_advisory_xact_lock(hashtextextended(%s,0))", (lock_key,)
                )
        second_wait.append(time.monotonic() - started)

    first = threading.Thread(target=first_transaction)
    second = threading.Thread(target=second_transaction)
    first.start()
    second.start()
    assert first_has_lock.wait(timeout=5)
    time.sleep(0.75)
    assert second.is_alive(), "QUESTION_ADVISORY_LOCK_DID_NOT_BLOCK"
    release_first.set()
    first.join(timeout=5)
    second.join(timeout=5)
    assert not first.is_alive() and not second.is_alive()
    assert second_wait and second_wait[0] >= 0.5

    print("ACTUAL_POSTGRES_RUNTIME_GATE_PASS")
    print("organization_deny_precedence=pass")
    print("etag_and_denial_audit=pass")
    print("question_advisory_lock_concurrency=pass")
finally:
    store.close()
