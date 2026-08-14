from __future__ import annotations

import os
import sys

from daon_user_api.cloud_storage import CloudAccessContext, PostgresCloudStore
from daon_user_api.egress_policy import EgressPolicyContext, EgressPolicyService
from daon_user_api.egress_policy_postgres import PostgresEgressPolicyRepository


SCOPES = (
    ("tenant-policy-menu", "workspace-policy-menu", "actor-policy-menu"),
    ("tenant-policy-foreign", "workspace-policy-foreign", "actor-policy-foreign"),
)


def main() -> None:
    store = PostgresCloudStore(os.environ["DAON_ORGANIZATION_POLICY_TEST_DSN"])
    try:
        if len(sys.argv) == 2 and sys.argv[1] == "seed":
            for tenant_id, workspace_id, actor_id in SCOPES:
                store.seed_scope(CloudAccessContext(tenant_id, workspace_id, actor_id, "policy.read"))
            print("ORGANIZATION_POLICY_PG_SEED PASS scopes=2")
            return
        service = EgressPolicyService(PostgresEgressPolicyRepository(store))
        views = []
        for tenant_id, workspace_id, actor_id in SCOPES:
            views.append(service.get_effective(EgressPolicyContext(
                tenant_id, tenant_id, workspace_id, actor_id,
                f"trace-{workspace_id}", "policy-v1",
            )))
        current, foreign = views
        assert current.mode == "deny_external" and current.parent_locked is True
        assert current.organization_policy == {
            "allowed_destinations": [], "allowed_provider_kinds": [], "classification": "restricted",
            "masking_required": True, "max_bytes": 0, "mode": "deny_external",
            "redaction_required": True, "required_approver": "organization_admin",
        }
        assert current.organization_policy_version_id != foreign.organization_policy_version_id
        assert current.organization_binding_id != foreign.organization_binding_id
        assert current.organization_etag.startswith('"egress-policy:organization:')
        assert current.workspace_etag.startswith('"egress-policy:workspace:')
        print("ORGANIZATION_POLICY_PG_GATE PASS scopes=2 effective=deny_external parent_locked=true cross_scope=distinct")
    finally:
        store.close()


if __name__ == "__main__":
    main()
