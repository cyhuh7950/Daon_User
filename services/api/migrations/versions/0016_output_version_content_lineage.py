"""Separate OutputVersion content lineage from state transition version."""

from __future__ import annotations

from alembic import op


revision = "0016"
down_revision = "0015"
branch_labels = None
depends_on = None


_VALIDATE_WITH_CONTENT_VERSION = r"""
CREATE OR REPLACE FUNCTION validate_canon_insert() RETURNS trigger LANGUAGE plpgsql AS $$
DECLARE previous_row record;
BEGIN
  IF jsonb_typeof(NEW.canonical_json) <> 'object'
     OR NEW.canonical_text::jsonb <> NEW.canonical_json THEN
    RAISE EXCEPTION 'CANON_SNAPSHOT_INVALID' USING ERRCODE = '22023';
  END IF;
  IF encode(sha256(convert_to(NEW.canonical_text, 'UTF8')), 'hex') <> NEW.digest_sha256 THEN
    RAISE EXCEPTION 'CANON_DIGEST_MISMATCH' USING ERRCODE = '22023';
  END IF;
  IF TG_TABLE_NAME = 'output_versions' THEN
    IF NEW.content_version = 1 THEN
      IF NEW.previous_version_id IS NOT NULL THEN
        RAISE EXCEPTION 'CANON_PREVIOUS_VERSION_INVALID' USING ERRCODE = '23514';
      END IF;
    ELSE
      IF NEW.previous_version_id IS NULL THEN
        RAISE EXCEPTION 'CANON_PREVIOUS_VERSION_INVALID' USING ERRCODE = '23514';
      END IF;
      SELECT aggregate_id, content_version INTO previous_row
        FROM output_versions
       WHERE tenant_id=NEW.tenant_id AND workspace_id=NEW.workspace_id
         AND record_id=NEW.previous_version_id;
      IF previous_row IS NULL OR previous_row.aggregate_id <> NEW.aggregate_id
         OR previous_row.content_version <> NEW.content_version - 1 THEN
        RAISE EXCEPTION 'CANON_PREVIOUS_VERSION_INVALID' USING ERRCODE = '23514';
      END IF;
    END IF;
  ELSIF NEW.version = 1 THEN
    IF NEW.previous_version_id IS NOT NULL THEN
      RAISE EXCEPTION 'CANON_PREVIOUS_VERSION_INVALID' USING ERRCODE = '23514';
    END IF;
  ELSE
    IF NEW.previous_version_id IS NULL THEN
      RAISE EXCEPTION 'CANON_PREVIOUS_VERSION_INVALID' USING ERRCODE = '23514';
    END IF;
    EXECUTE format(
      'SELECT aggregate_id, version FROM public.%I WHERE tenant_id = $1 AND workspace_id = $2 AND record_id = $3',
      TG_TABLE_NAME
    ) INTO previous_row USING NEW.tenant_id, NEW.workspace_id, NEW.previous_version_id;
    IF previous_row IS NULL OR previous_row.aggregate_id <> NEW.aggregate_id
       OR previous_row.version <> NEW.version - 1 THEN
      RAISE EXCEPTION 'CANON_PREVIOUS_VERSION_INVALID' USING ERRCODE = '23514';
    END IF;
  END IF;
  IF TG_TABLE_NAME = 'run_snapshots' AND NOT (
    NEW.canonical_json ?& ARRAY[
      'source_version_ids','knowledge_scope_id','authority','weights_requested',
      'weights_effective','weight_clamps','ruleset_snapshot_ids',
      'routing_policy_version_id','candidate_order','data_area',
      'data_classification','egress_decision_id','user_policy_version',
      'organization_policy_version','cost_limit','currency','prompt_version','tool_version'
    ]
  ) THEN
    RAISE EXCEPTION 'CANON_SNAPSHOT_INVALID' USING ERRCODE = '22023';
  END IF;
  IF (TG_TABLE_NAME, to_jsonb(NEW)->>'state', NEW.version) IN (
    ('sources', 'registered', 1), ('processing_runs', 'accepted', 1),
    ('runs', 'accepted', 1), ('generation_requests', 'configuring', 1),
    ('output_versions', 'generating', 1), ('approval_requests', 'pending', 1),
    ('knowledge_registrations', 'requested', 1)
  ) THEN NULL;
  ELSIF TG_TABLE_NAME IN (
    'sources','processing_runs','runs','generation_requests','output_versions',
    'approval_requests','knowledge_registrations'
  ) THEN
    RAISE EXCEPTION 'CANON_STATE_INITIAL_INVALID' USING ERRCODE = '23514';
  END IF;
  RETURN NEW;
END $$;
"""


def upgrade() -> None:
    op.execute(r"""
      ALTER TABLE output_versions
        ADD COLUMN content_version bigint NOT NULL DEFAULT 1 CHECK (content_version > 0);
      DO $$
      DECLARE constraint_name text;
      BEGIN
        SELECT conname INTO constraint_name
          FROM pg_constraint
         WHERE conrelid='output_versions'::regclass
           AND contype='u'
           AND pg_get_constraintdef(oid)='UNIQUE (tenant_id, workspace_id, aggregate_id, version)';
        IF constraint_name IS NULL THEN
          RAISE EXCEPTION 'OUTPUT_VERSION_LEGACY_UNIQUE_MISSING' USING ERRCODE='55000';
        END IF;
        EXECUTE format('ALTER TABLE output_versions DROP CONSTRAINT %I', constraint_name);
      END $$;
      ALTER TABLE output_versions ADD CONSTRAINT output_versions_content_version_unique
        UNIQUE (tenant_id, workspace_id, aggregate_id, content_version);
    """)
    op.execute(_VALIDATE_WITH_CONTENT_VERSION)


def downgrade() -> None:
    op.execute(r"""
      DO $$
      BEGIN
        IF EXISTS (
          SELECT 1 FROM output_versions
           GROUP BY tenant_id, workspace_id, aggregate_id HAVING count(*) > 1
        ) THEN
          RAISE EXCEPTION 'OUTPUT_VERSION_DOWNGRADE_BLOCKED' USING ERRCODE = '55000';
        END IF;
      END $$;
      ALTER TABLE output_versions DROP CONSTRAINT output_versions_content_version_unique;
      ALTER TABLE output_versions ADD CONSTRAINT output_versions_state_version_unique
        UNIQUE (tenant_id, workspace_id, aggregate_id, version);
      ALTER TABLE output_versions DROP COLUMN content_version;
    """)
    # The legacy validator is restored only when no multi-content lineage exists.
    legacy = _VALIDATE_WITH_CONTENT_VERSION.replace(
        "  IF TG_TABLE_NAME = 'output_versions' THEN\n"
        "    IF NEW.content_version = 1 THEN\n"
        "      IF NEW.previous_version_id IS NOT NULL THEN\n"
        "        RAISE EXCEPTION 'CANON_PREVIOUS_VERSION_INVALID' USING ERRCODE = '23514';\n"
        "      END IF;\n"
        "    ELSE\n"
        "      IF NEW.previous_version_id IS NULL THEN\n"
        "        RAISE EXCEPTION 'CANON_PREVIOUS_VERSION_INVALID' USING ERRCODE = '23514';\n"
        "      END IF;\n"
        "      SELECT aggregate_id, content_version INTO previous_row\n"
        "        FROM output_versions\n"
        "       WHERE tenant_id=NEW.tenant_id AND workspace_id=NEW.workspace_id\n"
        "         AND record_id=NEW.previous_version_id;\n"
        "      IF previous_row IS NULL OR previous_row.aggregate_id <> NEW.aggregate_id\n"
        "         OR previous_row.content_version <> NEW.content_version - 1 THEN\n"
        "        RAISE EXCEPTION 'CANON_PREVIOUS_VERSION_INVALID' USING ERRCODE = '23514';\n"
        "      END IF;\n"
        "    END IF;\n"
        "  ELSIF NEW.version = 1 THEN",
        "  IF NEW.version = 1 THEN",
    )
    op.execute(legacy)
