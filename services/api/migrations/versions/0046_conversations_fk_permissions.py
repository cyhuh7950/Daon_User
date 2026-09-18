"""Restore the conversations FK row-lock privilege required by question writes."""

from __future__ import annotations

from alembic import op


revision = "0046"
down_revision = "0045"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(
        """
        DO $$
        DECLARE
          v_rls_enabled boolean;
          v_rls_forced boolean;
          v_owner name;
          v_guard boolean;
        BEGIN
          IF NOT EXISTS (
            SELECT 1 FROM pg_roles
             WHERE rolname = 'daon_app' AND NOT rolbypassrls
          ) THEN
            RAISE EXCEPTION 'CONVERSATIONS_FK_PRIVILEGE_RLS_PRECONDITION_FAILED'
              USING ERRCODE = '55000';
          END IF;

          IF NOT EXISTS (
            SELECT 1 FROM pg_roles
             WHERE rolname = current_user AND (rolsuper OR rolbypassrls)
          ) THEN
            RAISE EXCEPTION 'CONVERSATIONS_FK_PRIVILEGE_MIGRATION_ROLE_REQUIRED'
              USING ERRCODE = '55000';
          END IF;

          SELECT c.relrowsecurity, c.relforcerowsecurity, r.rolname
            INTO v_rls_enabled, v_rls_forced, v_owner
            FROM pg_class AS c
            JOIN pg_namespace AS n ON n.oid = c.relnamespace
            JOIN pg_roles AS r ON r.oid = c.relowner
           WHERE n.nspname = 'public'
             AND c.relname = 'conversations'
             AND c.relkind = 'r';

          IF NOT FOUND OR NOT v_rls_enabled OR NOT v_rls_forced
             OR v_owner <> 'daon_app' THEN
            RAISE EXCEPTION 'CONVERSATIONS_FK_PRIVILEGE_RLS_PRECONDITION_FAILED'
              USING ERRCODE = '55000';
          END IF;

          SELECT EXISTS (
            SELECT 1
              FROM pg_trigger AS t
             WHERE t.tgrelid = 'public.conversations'::regclass
               AND t.tgname = 'conversations_immutable'
               AND NOT t.tgisinternal
               AND t.tgenabled IN ('O', 'A')
               AND t.tgfoid = 'public.reject_canon_immutable_mutation()'::regprocedure
          )
          AND EXISTS (
            SELECT 1
              FROM pg_trigger AS t
             WHERE t.tgrelid = 'public.conversations'::regclass
               AND t.tgname = 'conversations_validate'
               AND NOT t.tgisinternal
               AND t.tgenabled IN ('O', 'A')
               AND t.tgfoid = 'public.validate_canon_insert()'::regprocedure
          ) INTO v_guard;

          IF NOT v_guard THEN
            RAISE EXCEPTION 'CONVERSATIONS_FK_PRIVILEGE_IMMUTABILITY_PRECONDITION_FAILED'
              USING ERRCODE = '55000';
          END IF;
        END $$;

        GRANT UPDATE ON conversations TO daon_app;

        DO $$
        BEGIN
          IF NOT has_table_privilege('daon_app', 'public.conversations', 'UPDATE') THEN
            RAISE EXCEPTION 'CONVERSATIONS_FK_PRIVILEGE_POSTCONDITION_FAILED'
              USING ERRCODE = '55000';
          END IF;
        END $$;
        """
    )


def downgrade() -> None:
    raise RuntimeError("CONVERSATIONS_FK_PRIVILEGE_DOWNGRADE_BLOCKED")
