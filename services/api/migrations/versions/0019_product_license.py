"""Append-only organization product license claims and apply idempotency."""

from alembic import op


revision = "0019"
down_revision = "0018"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(r"""
      CREATE TABLE organization_license_versions (
        tenant_id text NOT NULL,
        version bigint NOT NULL CHECK (version > 0),
        license_id text NOT NULL CHECK (license_id ~ '^[A-Za-z0-9][A-Za-z0-9._:-]{0,127}$'),
        product text NOT NULL CHECK (product ~ '^[A-Za-z0-9][A-Za-z0-9._:-]{0,127}$'),
        edition text NOT NULL CHECK (edition ~ '^[A-Za-z0-9][A-Za-z0-9._:-]{0,127}$'),
        issued_at timestamptz NOT NULL,
        expires_at timestamptz NOT NULL CHECK (expires_at > issued_at),
        features text[] NOT NULL CHECK (cardinality(features) BETWEEN 1 AND 64),
        resource_limits jsonb NOT NULL CHECK (jsonb_typeof(resource_limits)='object'),
        claims_digest text NOT NULL CHECK (claims_digest ~ '^[0-9a-f]{64}$'),
        signing_key_id text NOT NULL CHECK (signing_key_id ~ '^[A-Za-z0-9][A-Za-z0-9._:-]{0,127}$'),
        applied_by text NOT NULL,
        applied_at timestamptz NOT NULL,
        trace_id text NOT NULL,
        policy_version text NOT NULL,
        PRIMARY KEY (tenant_id,version),
        UNIQUE (tenant_id,license_id,claims_digest)
      );
      CREATE TABLE license_apply_idempotency (
        tenant_id text NOT NULL,
        actor_id text NOT NULL,
        idempotency_key text NOT NULL,
        request_fingerprint text NOT NULL CHECK (request_fingerprint ~ '^[0-9a-f]{64}$'),
        license_version bigint NOT NULL,
        created_at timestamptz NOT NULL,
        PRIMARY KEY (tenant_id,actor_id,idempotency_key),
        FOREIGN KEY (tenant_id,license_version)
          REFERENCES organization_license_versions(tenant_id,version)
      );
      CREATE OR REPLACE FUNCTION reject_license_mutation() RETURNS trigger LANGUAGE plpgsql AS $$
      BEGIN RAISE EXCEPTION 'LICENSE_IMMUTABLE' USING ERRCODE='55000'; END $$;
      CREATE TRIGGER organization_license_versions_immutable
        BEFORE UPDATE OR DELETE ON organization_license_versions
        FOR EACH ROW EXECUTE FUNCTION reject_license_mutation();
      CREATE TRIGGER license_apply_idempotency_immutable
        BEFORE UPDATE OR DELETE ON license_apply_idempotency
        FOR EACH ROW EXECUTE FUNCTION reject_license_mutation();
      ALTER TABLE organization_license_versions ENABLE ROW LEVEL SECURITY;
      ALTER TABLE organization_license_versions FORCE ROW LEVEL SECURITY;
      CREATE POLICY organization_license_versions_scope ON organization_license_versions
        USING (tenant_id=nullif(current_setting('app.tenant_id', true),''))
        WITH CHECK (tenant_id=nullif(current_setting('app.tenant_id', true),''));
      ALTER TABLE license_apply_idempotency ENABLE ROW LEVEL SECURITY;
      ALTER TABLE license_apply_idempotency FORCE ROW LEVEL SECURITY;
      CREATE POLICY license_apply_idempotency_scope ON license_apply_idempotency
        USING (tenant_id=nullif(current_setting('app.tenant_id', true),''))
        WITH CHECK (tenant_id=nullif(current_setting('app.tenant_id', true),''));
      GRANT SELECT, INSERT ON organization_license_versions,license_apply_idempotency TO daon_app;
      REVOKE UPDATE, DELETE ON organization_license_versions,license_apply_idempotency FROM daon_app;
    """)


def downgrade() -> None:
    op.execute(r"""
      DO $$ BEGIN
        IF EXISTS (SELECT 1 FROM organization_license_versions)
           OR EXISTS (SELECT 1 FROM license_apply_idempotency) THEN
          RAISE EXCEPTION 'LICENSE_DOWNGRADE_BLOCKED' USING ERRCODE='55000';
        END IF;
      END $$;
      DROP TABLE license_apply_idempotency;
      DROP TABLE organization_license_versions;
      DROP FUNCTION reject_license_mutation();
    """)
