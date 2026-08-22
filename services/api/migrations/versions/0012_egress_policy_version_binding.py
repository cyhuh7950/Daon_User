"""Versioned organization/workspace egress policy and active binding canon."""

from __future__ import annotations

from alembic import op


revision = "0012"
down_revision = "0011"
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
        CREATE TABLE egress_policy_versions (
          tenant_id text NOT NULL,
          organization_id text NOT NULL,
          workspace_id text,
          policy_version_id text NOT NULL
            CHECK (policy_version_id ~ '^[A-Za-z0-9][A-Za-z0-9._:-]{{0,255}}$'),
          scope_type text NOT NULL CHECK (scope_type IN ('organization','workspace')),
          policy_version integer NOT NULL CHECK (policy_version > 0),
          state text NOT NULL CHECK (state IN ('active','superseded')),
          canonical_json jsonb NOT NULL CHECK (jsonb_typeof(canonical_json) = 'object'),
          canonical_text text NOT NULL,
          digest_sha256 text NOT NULL CHECK (digest_sha256 ~ '^[0-9a-f]{{64}}$'),
          created_by text NOT NULL,
          trace_id text NOT NULL,
          created_at timestamptz NOT NULL DEFAULT now(),
          PRIMARY KEY (tenant_id, policy_version_id),
          UNIQUE NULLS NOT DISTINCT
            (tenant_id, organization_id, workspace_id, scope_type, policy_version),
          FOREIGN KEY (tenant_id) REFERENCES tenants(tenant_id),
          FOREIGN KEY (organization_id) REFERENCES tenants(tenant_id),
          FOREIGN KEY (tenant_id, workspace_id) REFERENCES workspaces(tenant_id, workspace_id),
          CHECK (tenant_id = organization_id),
          CHECK ((scope_type = 'organization' AND workspace_id IS NULL)
              OR (scope_type = 'workspace' AND workspace_id IS NOT NULL)),
          CHECK (canonical_json ?& ARRAY[
            'mode','allowed_provider_kinds','allowed_destinations','classification',
            'max_bytes','masking_required','redaction_required','required_approver'
          ]),
          CHECK (canonical_json->>'mode' IN ('deny_external','allow_approved_external')),
          CHECK (jsonb_typeof(canonical_json->'allowed_provider_kinds') = 'array'),
          CHECK (jsonb_typeof(canonical_json->'allowed_destinations') = 'array'),
          CHECK ((canonical_json->>'max_bytes')::bigint >= 0)
        );

        CREATE TABLE egress_policy_bindings (
          tenant_id text NOT NULL,
          organization_id text NOT NULL,
          workspace_id text,
          binding_id text NOT NULL
            CHECK (binding_id ~ '^[A-Za-z0-9][A-Za-z0-9._:-]{{0,255}}$'),
          scope_type text NOT NULL CHECK (scope_type IN ('organization','workspace')),
          policy_version_id text NOT NULL,
          binding_version integer NOT NULL CHECK (binding_version > 0),
          active boolean NOT NULL DEFAULT true,
          current boolean NOT NULL DEFAULT true,
          created_by text NOT NULL,
          trace_id text NOT NULL,
          created_at timestamptz NOT NULL DEFAULT now(),
          PRIMARY KEY (tenant_id, binding_id),
          UNIQUE NULLS NOT DISTINCT
            (tenant_id, organization_id, workspace_id, scope_type, binding_version),
          FOREIGN KEY (tenant_id) REFERENCES tenants(tenant_id),
          FOREIGN KEY (organization_id) REFERENCES tenants(tenant_id),
          FOREIGN KEY (tenant_id, workspace_id) REFERENCES workspaces(tenant_id, workspace_id),
          FOREIGN KEY (tenant_id, policy_version_id)
            REFERENCES egress_policy_versions(tenant_id, policy_version_id),
          CHECK (tenant_id = organization_id),
          CHECK ((scope_type = 'organization' AND workspace_id IS NULL)
              OR (scope_type = 'workspace' AND workspace_id IS NOT NULL)),
          CHECK (current = false OR active = true)
        );

        CREATE UNIQUE INDEX egress_policy_bindings_one_current_scope
          ON egress_policy_bindings (
            tenant_id, organization_id, scope_type, coalesce(workspace_id, '')
          ) WHERE current;
        CREATE INDEX egress_policy_versions_scope_lookup
          ON egress_policy_versions (
            tenant_id, organization_id, scope_type, workspace_id, policy_version DESC
          );
        CREATE INDEX egress_policy_bindings_policy_lookup
          ON egress_policy_bindings (tenant_id, policy_version_id);

        CREATE FUNCTION reject_egress_policy_mutation() RETURNS trigger LANGUAGE plpgsql AS $$
        BEGIN
          RAISE EXCEPTION 'EGRESS_POLICY_IMMUTABLE' USING ERRCODE = '55000';
        END $$;
        CREATE TRIGGER egress_policy_versions_immutable
          BEFORE UPDATE OR DELETE ON egress_policy_versions
          FOR EACH ROW EXECUTE FUNCTION reject_egress_policy_mutation();

        CREATE FUNCTION validate_egress_policy_version_insert()
          RETURNS trigger LANGUAGE plpgsql AS $$
        BEGIN
          IF NEW.canonical_text::jsonb <> NEW.canonical_json THEN
            RAISE EXCEPTION 'CANON_SNAPSHOT_INVALID' USING ERRCODE = '22023';
          END IF;
          IF encode(sha256(convert_to(NEW.canonical_text, 'UTF8')), 'hex')
             <> NEW.digest_sha256 THEN
            RAISE EXCEPTION 'CANON_DIGEST_MISMATCH' USING ERRCODE = '22023';
          END IF;
          RETURN NEW;
        END $$;
        CREATE TRIGGER egress_policy_versions_canon_guard
          BEFORE INSERT ON egress_policy_versions
          FOR EACH ROW EXECUTE FUNCTION validate_egress_policy_version_insert();

        CREATE FUNCTION validate_egress_policy_binding_mutation()
          RETURNS trigger LANGUAGE plpgsql AS $$
        BEGIN
          IF TG_OP = 'DELETE' OR
             OLD.tenant_id IS DISTINCT FROM NEW.tenant_id OR
             OLD.organization_id IS DISTINCT FROM NEW.organization_id OR
             OLD.workspace_id IS DISTINCT FROM NEW.workspace_id OR
             OLD.binding_id IS DISTINCT FROM NEW.binding_id OR
             OLD.scope_type IS DISTINCT FROM NEW.scope_type OR
             OLD.policy_version_id IS DISTINCT FROM NEW.policy_version_id OR
             OLD.binding_version IS DISTINCT FROM NEW.binding_version OR
             OLD.created_by IS DISTINCT FROM NEW.created_by OR
             OLD.trace_id IS DISTINCT FROM NEW.trace_id OR
             OLD.created_at IS DISTINCT FROM NEW.created_at OR
             NOT (OLD.active AND OLD.current AND NOT NEW.active AND NOT NEW.current) THEN
            RAISE EXCEPTION 'EGRESS_POLICY_BINDING_IMMUTABLE' USING ERRCODE = '55000';
          END IF;
          RETURN NEW;
        END $$;
        CREATE TRIGGER egress_policy_bindings_immutable
          BEFORE UPDATE OR DELETE ON egress_policy_bindings
          FOR EACH ROW EXECUTE FUNCTION validate_egress_policy_binding_mutation();

        CREATE FUNCTION validate_egress_policy_binding_scope()
          RETURNS trigger LANGUAGE plpgsql AS $$
        DECLARE policy_row record;
        BEGIN
          SELECT scope_type,state INTO policy_row
          FROM egress_policy_versions
          WHERE tenant_id = NEW.tenant_id
            AND organization_id = NEW.organization_id
            AND workspace_id IS NOT DISTINCT FROM NEW.workspace_id
            AND policy_version_id = NEW.policy_version_id;
          IF NOT FOUND OR policy_row.scope_type <> NEW.scope_type THEN
            RAISE EXCEPTION 'EGRESS_POLICY_SCOPE_MISMATCH' USING ERRCODE = '23514';
          END IF;
          IF policy_row.state <> 'active' THEN
            RAISE EXCEPTION 'EGRESS_POLICY_STALE' USING ERRCODE = '23514';
          END IF;
          RETURN NEW;
        END $$;
        CREATE TRIGGER egress_policy_bindings_scope_guard
          BEFORE INSERT ON egress_policy_bindings
          FOR EACH ROW EXECUTE FUNCTION validate_egress_policy_binding_scope();
        """
    )

    for table in ("egress_policy_versions", "egress_policy_bindings"):
        op.execute(f"ALTER TABLE {table} ENABLE ROW LEVEL SECURITY")
        op.execute(f"ALTER TABLE {table} FORCE ROW LEVEL SECURITY")
        op.execute(
            f"CREATE POLICY {table}_scope ON {table} "
            "USING (tenant_id = nullif(current_setting('app.tenant_id', true), '') "
            "AND (workspace_id IS NULL OR workspace_id = "
            "nullif(current_setting('app.workspace_id', true), ''))) "
            "WITH CHECK (tenant_id = nullif(current_setting('app.tenant_id', true), '') "
            "AND (workspace_id IS NULL OR workspace_id = "
            "nullif(current_setting('app.workspace_id', true), '')))"
        )

    # Organization and Workspace fail-closed bindings are deterministic and
    # idempotent. Existing Runs, RunSnapshots and egress_decisions are untouched.
    op.get_bind().exec_driver_sql(
        f"""
        INSERT INTO egress_policy_versions (
          tenant_id,organization_id,workspace_id,policy_version_id,scope_type,
          policy_version,state,canonical_json,canonical_text,digest_sha256,
          created_by,trace_id
        )
        SELECT tenant_id,tenant_id,NULL,
          'egress-backfill-policy:' || md5(tenant_id || ':organization'),
          'organization',1,'active','{DEFAULT_DENY_CANONICAL_TEXT}'::jsonb,
          '{DEFAULT_DENY_CANONICAL_TEXT}', '{DEFAULT_DENY_DIGEST}',
          'migration:0012','migration:0012:' || md5(tenant_id || ':organization')
        FROM tenants
        ON CONFLICT DO NOTHING;

        INSERT INTO egress_policy_bindings (
          tenant_id,organization_id,workspace_id,binding_id,scope_type,
          policy_version_id,binding_version,active,current,created_by,trace_id
        )
        SELECT tenant_id,tenant_id,NULL,
          'egress-backfill-binding:' || md5(tenant_id || ':organization'),
          'organization','egress-backfill-policy:' || md5(tenant_id || ':organization'),
          1,true,true,'migration:0012',
          'migration:0012:' || md5(tenant_id || ':organization')
        FROM tenants
        ON CONFLICT DO NOTHING;

        INSERT INTO egress_policy_versions (
          tenant_id,organization_id,workspace_id,policy_version_id,scope_type,
          policy_version,state,canonical_json,canonical_text,digest_sha256,
          created_by,trace_id
        )
        SELECT tenant_id,tenant_id,workspace_id,
          'egress-backfill-policy:' || md5(tenant_id || ':' || workspace_id),
          'workspace',1,'active','{DEFAULT_DENY_CANONICAL_TEXT}'::jsonb,
          '{DEFAULT_DENY_CANONICAL_TEXT}', '{DEFAULT_DENY_DIGEST}',
          'migration:0012','migration:0012:' || md5(tenant_id || ':' || workspace_id)
        FROM workspaces
        ON CONFLICT DO NOTHING;

        INSERT INTO egress_policy_bindings (
          tenant_id,organization_id,workspace_id,binding_id,scope_type,
          policy_version_id,binding_version,active,current,created_by,trace_id
        )
        SELECT tenant_id,tenant_id,workspace_id,
          'egress-backfill-binding:' || md5(tenant_id || ':' || workspace_id),
          'workspace','egress-backfill-policy:' || md5(tenant_id || ':' || workspace_id),
          1,true,true,'migration:0012',
          'migration:0012:' || md5(tenant_id || ':' || workspace_id)
        FROM workspaces
        ON CONFLICT DO NOTHING;
        """
    )

    op.execute("GRANT SELECT, INSERT ON egress_policy_versions TO daon_app")
    op.execute("GRANT SELECT, INSERT, UPDATE ON egress_policy_bindings TO daon_app")
    op.execute("REVOKE UPDATE, DELETE ON egress_policy_versions FROM daon_app")
    op.execute("REVOKE DELETE ON egress_policy_bindings FROM daon_app")


def downgrade() -> None:
    op.execute("DROP TABLE IF EXISTS egress_policy_bindings")
    op.execute("DROP TABLE IF EXISTS egress_policy_versions")
    op.execute("DROP FUNCTION IF EXISTS validate_egress_policy_binding_scope()")
    op.execute("DROP FUNCTION IF EXISTS validate_egress_policy_binding_mutation()")
    op.execute("DROP FUNCTION IF EXISTS reject_egress_policy_mutation()")
    op.execute("DROP FUNCTION IF EXISTS validate_egress_policy_version_insert()")
