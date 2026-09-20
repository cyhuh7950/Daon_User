"""Restore the FK row-lock privilege for immutable license versions."""

from __future__ import annotations

from alembic import op


revision = "0042"
down_revision = "0041"
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
            RAISE EXCEPTION 'LICENSE_FK_PERMISSION_RLS_PRECONDITION_FAILED'
              USING ERRCODE = '55000';
          END IF;

          IF NOT EXISTS (
            SELECT 1
              FROM pg_roles
             WHERE rolname = current_user
               AND (rolsuper OR rolbypassrls)
          ) THEN
            RAISE EXCEPTION 'LICENSE_FK_PERMISSION_MIGRATION_ROLE_REQUIRED'
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
             AND relation.relname = 'organization_license_versions'
             AND relation.relkind = 'r';

          IF NOT FOUND
             OR NOT v_rls_enabled
             OR NOT v_rls_forced
             OR v_owner <> 'daon_app' THEN
            RAISE EXCEPTION 'LICENSE_FK_PERMISSION_RLS_PRECONDITION_FAILED'
              USING ERRCODE = '55000';
          END IF;

          IF NOT EXISTS (
            SELECT 1
              FROM pg_trigger AS trigger
             WHERE trigger.tgrelid =
                   'public.organization_license_versions'::regclass
               AND trigger.tgname =
                   'organization_license_versions_immutable'
               AND NOT trigger.tgisinternal
               AND trigger.tgenabled IN ('O', 'A')
               AND trigger.tgfoid =
                   'public.reject_license_mutation()'::regprocedure
          ) THEN
            RAISE EXCEPTION
              'LICENSE_FK_PERMISSION_IMMUTABILITY_PRECONDITION_FAILED'
              USING ERRCODE = '55000';
          END IF;
        END $$;

        -- The foreign key from license_apply_idempotency locks the referenced
        -- license row with SELECT ... FOR KEY SHARE. PostgreSQL requires
        -- UPDATE privilege for that lock. The immutable trigger above still
        -- rejects every direct UPDATE or DELETE with SQLSTATE 55000.
        GRANT UPDATE ON organization_license_versions TO daon_app;

        DO $$
        BEGIN
          IF NOT has_table_privilege(
            'daon_app',
            'public.organization_license_versions',
            'UPDATE'
          ) THEN
            RAISE EXCEPTION 'LICENSE_FK_PERMISSION_POSTCONDITION_FAILED'
              USING ERRCODE = '55000';
          END IF;
        END $$;
        """
    )


def downgrade() -> None:
    raise RuntimeError("LICENSE_FK_PERMISSION_DOWNGRADE_BLOCKED")
