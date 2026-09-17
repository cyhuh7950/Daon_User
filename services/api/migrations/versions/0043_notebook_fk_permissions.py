"""Restore the FK row-lock privilege for immutable notebooks."""

from __future__ import annotations

from alembic import op


revision = "0043"
down_revision = "0042"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(
        r"""
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
            RAISE EXCEPTION 'NOTEBOOK_FK_PERMISSION_RLS_PRECONDITION_FAILED'
              USING ERRCODE = '55000';
          END IF;

          IF NOT EXISTS (
            SELECT 1
              FROM pg_roles
             WHERE rolname = current_user
               AND (rolsuper OR rolbypassrls)
          ) THEN
            RAISE EXCEPTION 'NOTEBOOK_FK_PERMISSION_MIGRATION_ROLE_REQUIRED'
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
             AND relation.relname = 'notebooks'
             AND relation.relkind = 'r';

          IF NOT FOUND
             OR NOT v_rls_enabled
             OR NOT v_rls_forced
             OR v_owner <> 'daon_app' THEN
            RAISE EXCEPTION 'NOTEBOOK_FK_PERMISSION_RLS_PRECONDITION_FAILED'
              USING ERRCODE = '55000';
          END IF;

          IF NOT EXISTS (
            SELECT 1
              FROM pg_trigger AS trigger
             WHERE trigger.tgrelid = 'public.notebooks'::regclass
               AND trigger.tgname = 'notebooks_immutable'
               AND NOT trigger.tgisinternal
               AND trigger.tgenabled IN ('O', 'A')
               AND trigger.tgfoid =
                   'public.reject_notebook_immutable_mutation()'::regprocedure
          ) THEN
            RAISE EXCEPTION
              'NOTEBOOK_FK_PERMISSION_IMMUTABILITY_PRECONDITION_FAILED'
              USING ERRCODE = '55000';
          END IF;
        END $$;

        -- Notebook child-table foreign keys lock the referenced notebook row
        -- with SELECT ... FOR KEY SHARE. PostgreSQL requires UPDATE privilege
        -- for that lock. The immutable trigger above still rejects every
        -- direct UPDATE or DELETE with SQLSTATE 55000.
        GRANT UPDATE ON notebooks TO daon_app;

        DO $$
        BEGIN
          IF NOT has_table_privilege(
            'daon_app',
            'public.notebooks',
            'UPDATE'
          ) THEN
            RAISE EXCEPTION 'NOTEBOOK_FK_PERMISSION_POSTCONDITION_FAILED'
              USING ERRCODE = '55000';
          END IF;
        END $$;
        """
    )


def downgrade() -> None:
    raise RuntimeError("NOTEBOOK_FK_PERMISSION_DOWNGRADE_BLOCKED")
