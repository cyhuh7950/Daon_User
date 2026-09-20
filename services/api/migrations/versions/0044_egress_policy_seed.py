"""Backfill fail-closed egress policies and restore their FK lock privilege."""

from __future__ import annotations

from alembic import op


revision = "0044"
down_revision = "0043"
branch_labels = None
depends_on = None


DEFAULT_DENY_CANONICAL_TEXT = (
    '{"allowed_destinations":[],"allowed_provider_kinds":[],'
    '"classification":"restricted","masking_required":true,"max_bytes":0,'
    '"mode":"deny_external","redaction_required":true,'
    '"required_approver":"organization_admin"}'
)
DEFAULT_DENY_DIGEST = "caf695f3de7e3e05feb024b3ff4b8b14cbfad5318b885ac15d8e4da25b819d7f"


def upgrade() -> None:
    op.execute(
        f"""
        DO $$
        DECLARE
          v_rls_enabled boolean;
          v_rls_forced boolean;
          v_owner name;
        BEGIN
          IF NOT EXISTS (
            SELECT 1
              FROM pg_roles
             WHERE rolname = 'daon_app'
               AND NOT rolbypassrls
          ) THEN
            RAISE EXCEPTION 'EGRESS_POLICY_SEED_RLS_PRECONDITION_FAILED'
              USING ERRCODE = '55000';
          END IF;

          IF NOT EXISTS (
            SELECT 1
              FROM pg_roles
             WHERE rolname = current_user
               AND (rolsuper OR rolbypassrls)
          ) THEN
            RAISE EXCEPTION 'EGRESS_POLICY_SEED_MIGRATION_ROLE_REQUIRED'
              USING ERRCODE = '55000';
          END IF;

          SELECT relation.relrowsecurity,
                 relation.relforcerowsecurity,
                 owner.rolname
            INTO v_rls_enabled, v_rls_forced, v_owner
            FROM pg_class AS relation
            JOIN pg_namespace AS namespace
              ON namespace.oid = relation.relnamespace
            JOIN pg_roles AS owner
              ON owner.oid = relation.relowner
           WHERE namespace.nspname = 'public'
             AND relation.relname = 'egress_policy_versions'
             AND relation.relkind = 'r';

          IF NOT FOUND
             OR NOT v_rls_enabled
             OR NOT v_rls_forced
             OR v_owner <> 'daon_app' THEN
            RAISE EXCEPTION 'EGRESS_POLICY_SEED_RLS_PRECONDITION_FAILED'
              USING ERRCODE = '55000';
          END IF;

          IF NOT EXISTS (
            SELECT 1
              FROM pg_trigger AS trigger
             WHERE trigger.tgrelid =
                   'public.egress_policy_versions'::regclass
               AND trigger.tgname = 'egress_policy_versions_immutable'
               AND NOT trigger.tgisinternal
               AND trigger.tgenabled IN ('O', 'A')
               AND trigger.tgfoid =
                   'public.reject_egress_policy_mutation()'::regprocedure
          ) THEN
            RAISE EXCEPTION
              'EGRESS_POLICY_SEED_IMMUTABILITY_PRECONDITION_FAILED'
              USING ERRCODE = '55000';
          END IF;
        END $$;

        -- Binding foreign keys lock the referenced policy version with
        -- SELECT ... FOR KEY SHARE, which requires UPDATE privilege. The
        -- immutable trigger above continues to reject direct mutations.
        GRANT UPDATE ON egress_policy_versions TO daon_app;

        DO $$
        BEGIN
          IF NOT has_table_privilege(
            'daon_app',
            'public.egress_policy_versions',
            'UPDATE'
          ) THEN
            RAISE EXCEPTION
              'EGRESS_POLICY_SEED_PRIVILEGE_POSTCONDITION_FAILED'
              USING ERRCODE = '55000';
          END IF;
        END $$;

        INSERT INTO egress_policy_versions (
          tenant_id, organization_id, workspace_id, policy_version_id,
          scope_type, policy_version, state, canonical_json, canonical_text,
          digest_sha256, created_by, trace_id
        )
        SELECT tenant.tenant_id, tenant.tenant_id, NULL,
          'egress-backfill-policy:' || md5(tenant_id || ':organization'),
          'organization', 1, 'active',
          '{DEFAULT_DENY_CANONICAL_TEXT}'::jsonb,
          '{DEFAULT_DENY_CANONICAL_TEXT}',
          '{DEFAULT_DENY_DIGEST}',
          'migration:0044',
          'migration:0044:' || md5(tenant_id || ':organization')
        FROM tenants AS tenant
        WHERE NOT EXISTS (
          SELECT 1
          FROM egress_policy_bindings AS binding
          WHERE binding.tenant_id = tenant.tenant_id
            AND binding.organization_id = tenant.tenant_id
            AND binding.workspace_id IS NULL
            AND binding.scope_type = 'organization'
            AND binding.current
        )
        ON CONFLICT DO NOTHING;

        INSERT INTO egress_policy_bindings (
          tenant_id, organization_id, workspace_id, binding_id, scope_type,
          policy_version_id, binding_version, active, current, created_by,
          trace_id
        )
        SELECT tenant.tenant_id, tenant.tenant_id, NULL,
          'egress-backfill-binding:' || md5(tenant_id || ':organization'),
          'organization',
          'egress-backfill-policy:' || md5(tenant_id || ':organization'),
          1, true, true, 'migration:0044',
          'migration:0044:' || md5(tenant_id || ':organization')
        FROM tenants AS tenant
        WHERE NOT EXISTS (
          SELECT 1
          FROM egress_policy_bindings AS binding
          WHERE binding.tenant_id = tenant.tenant_id
            AND binding.organization_id = tenant.tenant_id
            AND binding.workspace_id IS NULL
            AND binding.scope_type = 'organization'
            AND binding.current
        )
        ON CONFLICT DO NOTHING;

        INSERT INTO egress_policy_versions (
          tenant_id, organization_id, workspace_id, policy_version_id,
          scope_type, policy_version, state, canonical_json, canonical_text,
          digest_sha256, created_by, trace_id
        )
        SELECT workspace.tenant_id, workspace.tenant_id,
          workspace.workspace_id,
          'egress-backfill-policy:' ||
            md5(tenant_id || ':' || workspace_id),
          'workspace', 1, 'active',
          '{DEFAULT_DENY_CANONICAL_TEXT}'::jsonb,
          '{DEFAULT_DENY_CANONICAL_TEXT}',
          '{DEFAULT_DENY_DIGEST}',
          'migration:0044',
          'migration:0044:' || md5(tenant_id || ':' || workspace_id)
        FROM workspaces AS workspace
        WHERE NOT EXISTS (
          SELECT 1
          FROM egress_policy_bindings AS binding
          WHERE binding.tenant_id = workspace.tenant_id
            AND binding.organization_id = workspace.tenant_id
            AND binding.workspace_id = workspace.workspace_id
            AND binding.scope_type = 'workspace'
            AND binding.current
        )
        ON CONFLICT DO NOTHING;

        INSERT INTO egress_policy_bindings (
          tenant_id, organization_id, workspace_id, binding_id, scope_type,
          policy_version_id, binding_version, active, current, created_by,
          trace_id
        )
        SELECT workspace.tenant_id, workspace.tenant_id,
          workspace.workspace_id,
          'egress-backfill-binding:' ||
            md5(tenant_id || ':' || workspace_id),
          'workspace',
          'egress-backfill-policy:' ||
            md5(tenant_id || ':' || workspace_id),
          1, true, true, 'migration:0044',
          'migration:0044:' || md5(tenant_id || ':' || workspace_id)
        FROM workspaces AS workspace
        WHERE NOT EXISTS (
          SELECT 1
          FROM egress_policy_bindings AS binding
          WHERE binding.tenant_id = workspace.tenant_id
            AND binding.organization_id = workspace.tenant_id
            AND binding.workspace_id = workspace.workspace_id
            AND binding.scope_type = 'workspace'
            AND binding.current
        )
        ON CONFLICT DO NOTHING;

        DO $$
        BEGIN
          IF EXISTS (
            SELECT 1
            FROM tenants AS tenant
            WHERE NOT EXISTS (
              SELECT 1
              FROM egress_policy_bindings AS binding
              WHERE binding.tenant_id = tenant.tenant_id
                AND binding.organization_id = tenant.tenant_id
                AND binding.workspace_id IS NULL
                AND binding.scope_type = 'organization'
                AND binding.current
            )
          ) OR EXISTS (
            SELECT 1
            FROM workspaces AS workspace
            WHERE NOT EXISTS (
              SELECT 1
              FROM egress_policy_bindings AS binding
              WHERE binding.tenant_id = workspace.tenant_id
                AND binding.organization_id = workspace.tenant_id
                AND binding.workspace_id = workspace.workspace_id
                AND binding.scope_type = 'workspace'
                AND binding.current
            )
          ) THEN
            RAISE EXCEPTION
              'EGRESS_POLICY_SEED_BACKFILL_POSTCONDITION_FAILED'
              USING ERRCODE = '55000';
          END IF;
        END $$;
        """
    )


def downgrade() -> None:
    raise RuntimeError("EGRESS_POLICY_SEED_DOWNGRADE_BLOCKED")
