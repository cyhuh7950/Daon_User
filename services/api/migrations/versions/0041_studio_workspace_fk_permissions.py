"""Restore FK lock privileges for immutable Studio Canon tables."""

from __future__ import annotations

from alembic import op


revision = "0041"
down_revision = "0040"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(
        r"""
        DO $$
        DECLARE
          v_table text;
          v_rls_enabled boolean;
          v_rls_forced boolean;
        BEGIN
          IF NOT EXISTS (
            SELECT 1
              FROM pg_roles
             WHERE rolname = 'daon_app'
               AND NOT rolbypassrls
          ) THEN
            RAISE EXCEPTION 'STUDIO_FK_PERMISSION_RLS_PRECONDITION_FAILED'
              USING ERRCODE = '55000';
          END IF;

          FOREACH v_table IN ARRAY ARRAY[
            'workspace_policies',
            'knowledge_scopes',
            'weight_profiles',
            'ruleset_references',
            'ruleset_version_snapshots',
            'ruleset_bindings'
          ] LOOP
            SELECT relation.relrowsecurity, relation.relforcerowsecurity
              INTO v_rls_enabled, v_rls_forced
              FROM pg_class AS relation
              JOIN pg_namespace AS namespace
                ON namespace.oid = relation.relnamespace
             WHERE namespace.nspname = 'public'
               AND relation.relname = v_table
               AND relation.relkind = 'r';

            IF NOT FOUND OR NOT v_rls_enabled OR NOT v_rls_forced THEN
              RAISE EXCEPTION 'STUDIO_FK_PERMISSION_RLS_PRECONDITION_FAILED:%', v_table
                USING ERRCODE = '55000';
            END IF;

            IF NOT EXISTS (
              SELECT 1
                FROM pg_trigger AS trigger
               WHERE trigger.tgrelid = format('public.%I', v_table)::regclass
                 AND trigger.tgname = v_table || '_immutable'
                 AND NOT trigger.tgisinternal
                 AND trigger.tgenabled IN ('O', 'A')
                 AND trigger.tgfoid =
                   'public.reject_canon_immutable_mutation()'::regprocedure
            ) THEN
              RAISE EXCEPTION
                'STUDIO_FK_PERMISSION_IMMUTABILITY_PRECONDITION_FAILED:%',
                v_table
                USING ERRCODE = '55000';
            END IF;
          END LOOP;
        END $$;

        -- PostgreSQL referential-integrity checks lock referenced rows with
        -- SELECT ... FOR KEY SHARE.  That lock requires UPDATE privilege for
        -- the referenced relation owner.  The immutable triggers above still
        -- reject every direct UPDATE or DELETE with SQLSTATE 55000.
        GRANT UPDATE ON
          workspace_policies,
          knowledge_scopes,
          weight_profiles,
          ruleset_references,
          ruleset_version_snapshots,
          ruleset_bindings
        TO daon_app;

        DO $$
        DECLARE
          v_table text;
        BEGIN
          FOREACH v_table IN ARRAY ARRAY[
            'workspace_policies',
            'knowledge_scopes',
            'weight_profiles',
            'ruleset_references',
            'ruleset_version_snapshots',
            'ruleset_bindings'
          ] LOOP
            IF NOT has_table_privilege(
              'daon_app', format('public.%I', v_table), 'UPDATE'
            ) THEN
              RAISE EXCEPTION 'STUDIO_FK_PERMISSION_POSTCONDITION_FAILED:%', v_table
                USING ERRCODE = '55000';
            END IF;
          END LOOP;
        END $$;
        """
    )


def downgrade() -> None:
    raise RuntimeError("STUDIO_FK_PERMISSION_DOWNGRADE_BLOCKED")
