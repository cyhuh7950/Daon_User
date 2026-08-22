from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
import os
import sys
from threading import Barrier

from daon_user_api.cloud_storage import CloudAccessContext, CloudDatabaseError, PostgresCloudStore
from daon_user_api.egress_policy import (
    EgressPolicyContext,
    EgressPolicyError,
    EgressPolicyPayload,
    EgressPolicyService,
)
from daon_user_api.egress_policy_postgres import PostgresEgressPolicyRepository


TENANT_ID = "tenant-egress-lock"
WORKSPACE_ID = "workspace-egress-lock"
ACTOR_ID = "actor-egress-lock"


def seed() -> None:
    store = PostgresCloudStore(os.environ["DAON_EGRESS_LOCK_OWNER_DSN"])
    try:
        store.seed_scope(CloudAccessContext(TENANT_ID, WORKSPACE_ID, ACTOR_ID, "policy.read"))
        print("EGRESS_LOCK_PG_SEED PASS scopes=1")
    finally:
        store.close()


def verify() -> None:
    store = PostgresCloudStore(os.environ["DAON_EGRESS_LOCK_APP_DSN"])
    context = EgressPolicyContext(
        TENANT_ID, TENANT_ID, WORKSPACE_ID, ACTOR_ID,
        "trace-egress-lock", "policy-v1",
    )
    service = EgressPolicyService(PostgresEgressPolicyRepository(store))
    payload = EgressPolicyPayload.deny_external()
    try:
        with store._transaction(CloudAccessContext(
            TENANT_ID, WORKSPACE_ID, ACTOR_ID, "egress_policy.read",
        )) as connection:
            privileges = connection.execute(
                "SELECT "
                "has_table_privilege(current_user,'egress_policy_versions','SELECT'),"
                "has_table_privilege(current_user,'egress_policy_versions','INSERT'),"
                "has_table_privilege(current_user,'egress_policy_versions','UPDATE'),"
                "has_table_privilege(current_user,'egress_policy_versions','DELETE'),"
                "has_table_privilege(current_user,'egress_policy_bindings','SELECT'),"
                "has_table_privilege(current_user,'egress_policy_bindings','INSERT'),"
                "has_table_privilege(current_user,'egress_policy_bindings','UPDATE')",
            ).fetchone()
        assert privileges == (True, True, False, False, True, True, True)

        initial = service.get_effective(context)
        first = service.create_and_activate(
            context, scope_type="organization", payload=payload,
            expected_etag=initial.organization_etag,
            idempotency_key="egress-lock-first",
        )
        replay = service.create_and_activate(
            context, scope_type="organization", payload=payload,
            expected_etag=initial.organization_etag,
            idempotency_key="egress-lock-first",
        )
        assert replay.binding_id == first.binding_id

        current = service.get_effective(context)

        def concurrent_create() -> str:
            stored = service.create_and_activate(
                context, scope_type="organization", payload=payload,
                expected_etag=current.organization_etag,
                idempotency_key="egress-lock-concurrent",
            )
            return stored.binding_id

        with ThreadPoolExecutor(max_workers=2) as executor:
            binding_ids = tuple(executor.map(lambda _: concurrent_create(), range(2)))
        assert binding_ids[0] == binding_ids[1]

        distinct_current = service.get_effective(context)
        distinct_payloads = (
            payload,
            EgressPolicyPayload(
                mode="deny_external", allowed_provider_kinds=(), allowed_destinations=(),
                classification="confidential", max_bytes=0,
                masking_required=False, redaction_required=True,
                required_approver="workspace_manager",
            ),
        )
        with store._transaction(CloudAccessContext(
            TENANT_ID, WORKSPACE_ID, ACTOR_ID, "egress_policy.read",
        )) as connection:
            before_distinct = connection.execute(
                "SELECT "
                "(SELECT count(*) FROM egress_policy_versions "
                " WHERE organization_id=%s AND scope_type='organization'),"
                "(SELECT count(*) FROM egress_policy_bindings "
                " WHERE organization_id=%s AND scope_type='organization'),"
                "(SELECT count(*) FROM idempotency_records "
                " WHERE tenant_id=%s AND workspace_id=%s "
                " AND operation='egress_policy.organization.activate')",
                (TENANT_ID, TENANT_ID, TENANT_ID, WORKSPACE_ID),
            ).fetchone()
        barrier = Barrier(2)

        def distinct_create(index: int) -> tuple[str, str]:
            barrier.wait()
            try:
                stored = service.create_and_activate(
                    context, scope_type="organization", payload=distinct_payloads[index],
                    expected_etag=distinct_current.organization_etag,
                    idempotency_key=f"egress-lock-distinct-{index}",
                )
                return "success", stored.binding_id
            except EgressPolicyError as error:
                return "error", error.code

        with ThreadPoolExecutor(max_workers=2) as executor:
            distinct_results = tuple(executor.map(distinct_create, range(2)))
        assert sorted(result[0] for result in distinct_results) == ["error", "success"]
        assert tuple(result[1] for result in distinct_results if result[0] == "error") == (
            "VERSION_CONFLICT",
        )

        with store._transaction(CloudAccessContext(
            TENANT_ID, WORKSPACE_ID, ACTOR_ID, "egress_policy.read",
        )) as connection:
            counts = connection.execute(
                "SELECT "
                "(SELECT count(*) FROM egress_policy_bindings "
                " WHERE organization_id=%s AND scope_type='organization' AND current=true),"
                "(SELECT count(*) FROM egress_policy_versions "
                " WHERE organization_id=%s AND scope_type='organization'),"
                "(SELECT count(*) FROM egress_policy_bindings "
                " WHERE organization_id=%s AND scope_type='organization'),"
                "(SELECT count(*) FROM idempotency_records "
                " WHERE tenant_id=%s AND workspace_id=%s "
                " AND operation='egress_policy.organization.activate'),"
                "(SELECT count(*) FROM idempotency_records "
                " WHERE tenant_id=%s AND workspace_id=%s "
                " AND operation='egress_policy.organization.activate' "
                " AND idempotency_key LIKE 'egress-lock-distinct-%%')",
                (
                    TENANT_ID, TENANT_ID, TENANT_ID, TENANT_ID, WORKSPACE_ID,
                    TENANT_ID, WORKSPACE_ID,
                ),
            ).fetchone()
        assert counts == (
            1, before_distinct[0] + 1, before_distinct[1] + 1,
            before_distinct[2] + 1, 1,
        )

        for statement in (
            "UPDATE egress_policy_versions SET state='revoked' "
            "WHERE tenant_id=%s AND policy_version_id=%s",
            "DELETE FROM egress_policy_versions "
            "WHERE tenant_id=%s AND policy_version_id=%s",
        ):
            try:
                with store._transaction(CloudAccessContext(
                    TENANT_ID, WORKSPACE_ID, ACTOR_ID, "egress_policy.write",
                )) as connection:
                    connection.execute(statement, (TENANT_ID, first.policy_version_id))
            except CloudDatabaseError as error:
                assert error.code == "DATABASE_ACCESS_DENIED"
            else:
                raise AssertionError("immutable policy version mutation unexpectedly succeeded")

        print(
            "EGRESS_LOCK_PG_GATE PASS create=1 replay_duplicate0 "
            "same_key_concurrent_current=1 distinct_success=1 "
            "distinct_version_conflict=1 distinct_loser_write=0 "
            "version_update_delete=denied sqlstate=42501"
        )
    finally:
        store.close()


if __name__ == "__main__":
    if len(sys.argv) != 2 or sys.argv[1] not in {"seed", "verify"}:
        raise SystemExit("usage: egress-policy-lock-postgres-gate.py seed|verify")
    seed() if sys.argv[1] == "seed" else verify()
