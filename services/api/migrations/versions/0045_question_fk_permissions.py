"""Restore FK row-lock privileges required by question execution canon writes."""

from __future__ import annotations

from alembic import op


revision = "0045"
down_revision = "0044"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(
        """
        DO $$
        DECLARE
          v_table text;
          v_trigger text;
          v_function text;
          v_rls_enabled boolean;
          v_rls_forced boolean;
          v_owner name;
          v_guard boolean;
          v_tables text[] := ARRAY[
            'egress_decisions',
            'generation_settings_snapshots',
            'model_artifacts',
            'model_deployments',
            'model_installations',
            'provider_profiles',
            'routing_decisions',
            'routing_policy_versions',
            'run_snapshots',
            'runs',
            'runtime_nodes',
            'scope_snapshots'
          ];
        BEGIN
          IF NOT EXISTS (
            SELECT 1 FROM pg_roles
             WHERE rolname = 'daon_app' AND NOT rolbypassrls
          ) THEN
            RAISE EXCEPTION 'QUESTION_FK_PRIVILEGE_RLS_PRECONDITION_FAILED'
              USING ERRCODE = '55000';
          END IF;

          IF NOT EXISTS (
            SELECT 1 FROM pg_roles
             WHERE rolname = current_user AND (rolsuper OR rolbypassrls)
          ) THEN
            RAISE EXCEPTION 'QUESTION_FK_PRIVILEGE_MIGRATION_ROLE_REQUIRED'
              USING ERRCODE = '55000';
          END IF;

          FOREACH v_table IN ARRAY v_tables LOOP
            SELECT c.relrowsecurity, c.relforcerowsecurity, r.rolname
              INTO v_rls_enabled, v_rls_forced, v_owner
              FROM pg_class AS c
              JOIN pg_namespace AS n ON n.oid = c.relnamespace
              JOIN pg_roles AS r ON r.oid = c.relowner
             WHERE n.nspname = 'public' AND c.relname = v_table AND c.relkind = 'r';

            IF NOT FOUND OR NOT v_rls_enabled OR NOT v_rls_forced
               OR v_owner <> 'daon_app' THEN
              RAISE EXCEPTION 'QUESTION_FK_PRIVILEGE_RLS_PRECONDITION_FAILED:%', v_table
                USING ERRCODE = '55000';
            END IF;

            IF v_table = 'runs' THEN
              v_trigger := 'runs_state_guard';
              v_function := 'guard_canon_state_mutation';
            ELSE
              v_trigger := v_table || '_immutable';
              v_function := 'reject_canon_immutable_mutation';
            END IF;

            EXECUTE format(
              'SELECT EXISTS (SELECT 1 FROM pg_trigger t '
              'WHERE t.tgrelid = %L::regclass AND t.tgname = %L '
              'AND NOT t.tgisinternal AND t.tgenabled IN (''O'', ''A'') '
              'AND t.tgfoid = %L::regprocedure)',
              'public.' || v_table, v_trigger, 'public.' || v_function || '()'
            ) INTO v_guard;
            IF NOT v_guard THEN
              RAISE EXCEPTION 'QUESTION_FK_PRIVILEGE_IMMUTABILITY_PRECONDITION_FAILED:%', v_table
                USING ERRCODE = '55000';
            END IF;
          END LOOP;
        END $$;

        GRANT UPDATE ON egress_decisions TO daon_app;
        GRANT UPDATE ON generation_settings_snapshots TO daon_app;
        GRANT UPDATE ON model_artifacts TO daon_app;
        GRANT UPDATE ON model_deployments TO daon_app;
        GRANT UPDATE ON model_installations TO daon_app;
        GRANT UPDATE ON provider_profiles TO daon_app;
        GRANT UPDATE ON routing_decisions TO daon_app;
        GRANT UPDATE ON routing_policy_versions TO daon_app;
        GRANT UPDATE ON run_snapshots TO daon_app;
        GRANT UPDATE ON runs TO daon_app;
        GRANT UPDATE ON runtime_nodes TO daon_app;
        GRANT UPDATE ON scope_snapshots TO daon_app;

        DO $$
        DECLARE
          v_table text;
          v_tables text[] := ARRAY[
            'egress_decisions',
            'generation_settings_snapshots',
            'model_artifacts',
            'model_deployments',
            'model_installations',
            'provider_profiles',
            'routing_decisions',
            'routing_policy_versions',
            'run_snapshots',
            'runs',
            'runtime_nodes',
            'scope_snapshots'
          ];
        BEGIN
          FOREACH v_table IN ARRAY v_tables LOOP
            IF NOT has_table_privilege('daon_app', 'public.' || v_table, 'UPDATE') THEN
              RAISE EXCEPTION 'QUESTION_FK_PRIVILEGE_POSTCONDITION_FAILED:%', v_table
                USING ERRCODE = '55000';
            END IF;
          END LOOP;
        END $$;
        """
    )


def downgrade() -> None:
    raise RuntimeError("QUESTION_FK_PRIVILEGE_DOWNGRADE_BLOCKED")
