"""Provision deterministic Studio policy Canon for every workspace."""

from __future__ import annotations

from alembic import op


revision = "0013"
down_revision = "0012"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.get_bind().exec_driver_sql(
        r"""
        CREATE FUNCTION ensure_studio_workspace_defaults(
          p_tenant_id text,
          p_workspace_id text
        ) RETURNS void LANGUAGE plpgsql AS $$
        DECLARE
          v_suffix text := md5(p_tenant_id || '|' || p_workspace_id);
          v_workspace_policy_id text := 'studio-default:workspace-policy:' || v_suffix;
          v_knowledge_scope_id text := 'studio-default:knowledge-scope:' || v_suffix;
          v_weight_profile_id text := 'studio-default:weight-profile:' || v_suffix;
          v_ruleset_reference_id text := 'studio-default:ruleset-reference:' || v_suffix;
          v_ruleset_snapshot_id text := 'studio-default:ruleset-snapshot:' || v_suffix;
          v_ruleset_binding_id text := 'studio-default:ruleset-binding:' || v_suffix;
          v_selected_scope_id text;
          v_complete_ruleset boolean;
          v_latest_count integer;
          v_latest_json jsonb;
          v_latest_record_id text;
          v_text text;
        BEGIN
          SELECT count(*) INTO v_latest_count FROM workspace_policies
           WHERE tenant_id = p_tenant_id AND workspace_id = p_workspace_id
             AND version = (SELECT max(version) FROM workspace_policies WHERE tenant_id = p_tenant_id AND workspace_id = p_workspace_id);
          IF v_latest_count > 1 THEN
            RAISE EXCEPTION 'STUDIO_DEFAULT_POLICY_HISTORY_AMBIGUOUS' USING ERRCODE = '55000';
          ELSIF v_latest_count = 1 THEN
            SELECT canonical_json INTO v_latest_json FROM workspace_policies
             WHERE tenant_id = p_tenant_id AND workspace_id = p_workspace_id
               AND version = (SELECT max(version) FROM workspace_policies WHERE tenant_id = p_tenant_id AND workspace_id = p_workspace_id);
            IF v_latest_json->>'active' <> 'true'
               OR v_latest_json->>'current' <> 'true'
               OR v_latest_json->>'workspace_id' IS DISTINCT FROM p_workspace_id
               OR nullif(v_latest_json->>'authority_policy', '') IS NULL
               OR nullif(v_latest_json->>'data_area', '') IS NULL THEN
              RAISE EXCEPTION 'STUDIO_DEFAULT_POLICY_LATEST_INVALID' USING ERRCODE = '55000';
            END IF;
          ELSE
            v_text := '{"active":true,"authority_policy":"workspace_admin","current":true,"data_area":"cloud_sync","version":1,"workspace_id":'
              || to_jsonb(p_workspace_id)::text || '}';
            INSERT INTO workspace_policies (
              tenant_id,workspace_id,record_id,aggregate_id,version,schema_version,
              canonical_json,canonical_text,digest_sha256,created_by,trace_id
            ) VALUES (
              p_tenant_id,p_workspace_id,v_workspace_policy_id,v_workspace_policy_id,1,1,
              v_text::jsonb,v_text,encode(sha256(convert_to(v_text,'UTF8')),'hex'),
              'migration:0013','migration:0013:workspace-policy:' || v_suffix
            ) ON CONFLICT DO NOTHING;
            IF NOT EXISTS (
              SELECT 1 FROM workspace_policies
               WHERE tenant_id = p_tenant_id AND workspace_id = p_workspace_id
                 AND record_id = v_workspace_policy_id
                 AND created_by = 'migration:0013'
                 AND canonical_text = v_text
                 AND digest_sha256 = encode(sha256(convert_to(v_text,'UTF8')),'hex')
            ) THEN
              RAISE EXCEPTION 'STUDIO_DEFAULT_POLICY_ID_CONFLICT' USING ERRCODE = '55000';
            END IF;
          END IF;

          SELECT count(*) INTO v_latest_count FROM knowledge_scopes
           WHERE tenant_id = p_tenant_id AND workspace_id = p_workspace_id
             AND version = (SELECT max(version) FROM knowledge_scopes WHERE tenant_id = p_tenant_id AND workspace_id = p_workspace_id);
          IF v_latest_count > 1 THEN
            RAISE EXCEPTION 'STUDIO_DEFAULT_POLICY_HISTORY_AMBIGUOUS' USING ERRCODE = '55000';
          ELSIF v_latest_count = 1 THEN
            SELECT record_id,canonical_json INTO v_selected_scope_id,v_latest_json FROM knowledge_scopes
             WHERE tenant_id = p_tenant_id AND workspace_id = p_workspace_id
               AND version = (SELECT max(version) FROM knowledge_scopes WHERE tenant_id = p_tenant_id AND workspace_id = p_workspace_id);
            IF v_latest_json->>'active' <> 'true'
               OR v_latest_json->>'current' <> 'true'
               OR v_latest_json->>'workspace_id' IS DISTINCT FROM p_workspace_id
               OR v_latest_json->>'scope' <> 'workspace' THEN
              RAISE EXCEPTION 'STUDIO_DEFAULT_POLICY_LATEST_INVALID' USING ERRCODE = '55000';
            END IF;
          ELSE
            v_text := '{"active":true,"current":true,"scope":"workspace","version":1,"workspace_id":'
              || to_jsonb(p_workspace_id)::text || '}';
            INSERT INTO knowledge_scopes (
              tenant_id,workspace_id,record_id,aggregate_id,version,schema_version,
              canonical_json,canonical_text,digest_sha256,created_by,trace_id
            ) VALUES (
              p_tenant_id,p_workspace_id,v_knowledge_scope_id,v_knowledge_scope_id,1,1,
              v_text::jsonb,v_text,encode(sha256(convert_to(v_text,'UTF8')),'hex'),
              'migration:0013','migration:0013:knowledge-scope:' || v_suffix
            ) ON CONFLICT DO NOTHING;
            IF NOT EXISTS (
              SELECT 1 FROM knowledge_scopes
               WHERE tenant_id = p_tenant_id AND workspace_id = p_workspace_id
                 AND record_id = v_knowledge_scope_id
                 AND created_by = 'migration:0013'
                 AND canonical_text = v_text
                 AND digest_sha256 = encode(sha256(convert_to(v_text,'UTF8')),'hex')
            ) THEN
              RAISE EXCEPTION 'STUDIO_DEFAULT_POLICY_ID_CONFLICT' USING ERRCODE = '55000';
            END IF;
            v_selected_scope_id := v_knowledge_scope_id;
          END IF;

          SELECT count(*) INTO v_latest_count FROM weight_profiles
           WHERE tenant_id = p_tenant_id AND workspace_id = p_workspace_id
             AND version = (SELECT max(version) FROM weight_profiles WHERE tenant_id = p_tenant_id AND workspace_id = p_workspace_id);
          IF v_latest_count > 1 THEN
            RAISE EXCEPTION 'STUDIO_DEFAULT_POLICY_HISTORY_AMBIGUOUS' USING ERRCODE = '55000';
          ELSIF v_latest_count = 1 THEN
            SELECT canonical_json,knowledge_scope_id INTO v_latest_json,v_latest_record_id FROM weight_profiles
             WHERE tenant_id = p_tenant_id AND workspace_id = p_workspace_id
               AND version = (SELECT max(version) FROM weight_profiles WHERE tenant_id = p_tenant_id AND workspace_id = p_workspace_id);
            IF v_latest_json->>'active' <> 'true'
               OR v_latest_json->>'current' <> 'true'
               OR v_latest_json->>'workspace_id' IS DISTINCT FROM p_workspace_id
               OR v_latest_json->>'profile' <> 'trusted-source-v2'
               OR v_latest_record_id IS DISTINCT FROM v_selected_scope_id THEN
              RAISE EXCEPTION 'STUDIO_DEFAULT_POLICY_LATEST_INVALID' USING ERRCODE = '55000';
            END IF;
          ELSE
            v_text := '{"active":true,"current":true,"profile":"trusted-source-v2","version":1,"workspace_id":'
              || to_jsonb(p_workspace_id)::text || '}';
            INSERT INTO weight_profiles (
              tenant_id,workspace_id,record_id,aggregate_id,version,schema_version,
              canonical_json,canonical_text,digest_sha256,created_by,trace_id,
              knowledge_scope_id
            ) VALUES (
              p_tenant_id,p_workspace_id,v_weight_profile_id,v_weight_profile_id,1,1,
              v_text::jsonb,v_text,encode(sha256(convert_to(v_text,'UTF8')),'hex'),
              'migration:0013','migration:0013:weight-profile:' || v_suffix,
              v_selected_scope_id
            ) ON CONFLICT DO NOTHING;
            IF NOT EXISTS (
              SELECT 1 FROM weight_profiles
               WHERE tenant_id = p_tenant_id AND workspace_id = p_workspace_id
                 AND record_id = v_weight_profile_id
                 AND created_by = 'migration:0013'
                 AND canonical_text = v_text
                 AND digest_sha256 = encode(sha256(convert_to(v_text,'UTF8')),'hex')
                 AND knowledge_scope_id = v_selected_scope_id
            ) THEN
              RAISE EXCEPTION 'STUDIO_DEFAULT_POLICY_ID_CONFLICT' USING ERRCODE = '55000';
            END IF;
          END IF;

          SELECT count(*) INTO v_latest_count FROM ruleset_bindings
           WHERE tenant_id = p_tenant_id AND workspace_id = p_workspace_id
             AND version = (SELECT max(version) FROM ruleset_bindings WHERE tenant_id = p_tenant_id AND workspace_id = p_workspace_id);
          IF v_latest_count > 1 THEN
            RAISE EXCEPTION 'STUDIO_DEFAULT_POLICY_HISTORY_AMBIGUOUS' USING ERRCODE = '55000';
          ELSIF v_latest_count = 1 THEN
            SELECT EXISTS (
              SELECT 1
                FROM ruleset_bindings binding
              JOIN ruleset_version_snapshots snapshot
                ON (snapshot.tenant_id,snapshot.workspace_id,snapshot.record_id) =
                   (binding.tenant_id,binding.workspace_id,binding.ruleset_version_snapshot_id)
              JOIN ruleset_references reference
                ON (reference.tenant_id,reference.workspace_id,reference.record_id) =
                   (binding.tenant_id,binding.workspace_id,binding.ruleset_reference_id)
               AND snapshot.ruleset_reference_id = reference.record_id
             WHERE binding.tenant_id = p_tenant_id
                 AND binding.workspace_id = p_workspace_id
                 AND binding.version = (SELECT max(version) FROM ruleset_bindings WHERE tenant_id = p_tenant_id AND workspace_id = p_workspace_id)
                 AND binding.canonical_json->>'active' = 'true'
                 AND binding.canonical_json->>'current' = 'true'
                 AND binding.canonical_json->>'workspace_id' = p_workspace_id
                 AND binding.canonical_json->>'review_condition' = 'review_required'
                 AND coalesce(binding.canonical_json->>'ruleset_version_id','') = snapshot.record_id
                 AND snapshot.canonical_json->>'active' = 'true'
                 AND snapshot.canonical_json->>'current' = 'true'
                 AND snapshot.canonical_json->>'workspace_id' = p_workspace_id
                 AND snapshot.canonical_json->>'review_condition' = 'review_required'
                 AND reference.canonical_json->>'active' = 'true'
                 AND reference.canonical_json->>'current' = 'true'
                 AND reference.canonical_json->>'workspace_id' = p_workspace_id
                 AND reference.canonical_json->>'name' = 'default-review-required'
            ) INTO v_complete_ruleset;
            IF NOT v_complete_ruleset THEN
              RAISE EXCEPTION 'STUDIO_DEFAULT_POLICY_LATEST_INVALID' USING ERRCODE = '55000';
            END IF;
          ELSE
            v_complete_ruleset := false;
          END IF;

          IF NOT v_complete_ruleset THEN
            v_text := '{"active":true,"current":true,"name":"default-review-required","version":1,"workspace_id":'
            || to_jsonb(p_workspace_id)::text || '}';
          INSERT INTO ruleset_references (
            tenant_id,workspace_id,record_id,aggregate_id,version,schema_version,
            canonical_json,canonical_text,digest_sha256,created_by,trace_id
          ) VALUES (
            p_tenant_id,p_workspace_id,v_ruleset_reference_id,v_ruleset_reference_id,1,1,
            v_text::jsonb,v_text,encode(sha256(convert_to(v_text,'UTF8')),'hex'),
            'migration:0013','migration:0013:ruleset-reference:' || v_suffix
          ) ON CONFLICT DO NOTHING;
          IF NOT EXISTS (
            SELECT 1 FROM ruleset_references
             WHERE tenant_id = p_tenant_id AND workspace_id = p_workspace_id
               AND record_id = v_ruleset_reference_id
               AND created_by = 'migration:0013'
               AND canonical_text = v_text
               AND digest_sha256 = encode(sha256(convert_to(v_text,'UTF8')),'hex')
          ) THEN
            RAISE EXCEPTION 'STUDIO_DEFAULT_POLICY_ID_CONFLICT' USING ERRCODE = '55000';
          END IF;

          v_text := '{"active":true,"current":true,"review_condition":"review_required","rules":[],"version":1,"workspace_id":'
            || to_jsonb(p_workspace_id)::text || '}';
          INSERT INTO ruleset_version_snapshots (
            tenant_id,workspace_id,record_id,aggregate_id,version,schema_version,
            canonical_json,canonical_text,digest_sha256,created_by,trace_id,
            ruleset_reference_id
          ) VALUES (
            p_tenant_id,p_workspace_id,v_ruleset_snapshot_id,v_ruleset_snapshot_id,1,1,
            v_text::jsonb,v_text,encode(sha256(convert_to(v_text,'UTF8')),'hex'),
            'migration:0013','migration:0013:ruleset-snapshot:' || v_suffix,
            v_ruleset_reference_id
          ) ON CONFLICT DO NOTHING;
          IF NOT EXISTS (
            SELECT 1 FROM ruleset_version_snapshots
             WHERE tenant_id = p_tenant_id AND workspace_id = p_workspace_id
               AND record_id = v_ruleset_snapshot_id
               AND created_by = 'migration:0013'
               AND canonical_text = v_text
               AND digest_sha256 = encode(sha256(convert_to(v_text,'UTF8')),'hex')
               AND ruleset_reference_id = v_ruleset_reference_id
          ) THEN
            RAISE EXCEPTION 'STUDIO_DEFAULT_POLICY_ID_CONFLICT' USING ERRCODE = '55000';
          END IF;

          v_text := '{"active":true,"current":true,"review_condition":"review_required","ruleset_version_id":'
            || to_jsonb(v_ruleset_snapshot_id)::text || ',"version":1,"workspace_id":'
            || to_jsonb(p_workspace_id)::text || '}';
          INSERT INTO ruleset_bindings (
            tenant_id,workspace_id,record_id,aggregate_id,version,schema_version,
            canonical_json,canonical_text,digest_sha256,created_by,trace_id,
            ruleset_reference_id,ruleset_version_snapshot_id
          ) VALUES (
            p_tenant_id,p_workspace_id,v_ruleset_binding_id,v_ruleset_binding_id,1,1,
            v_text::jsonb,v_text,encode(sha256(convert_to(v_text,'UTF8')),'hex'),
            'migration:0013','migration:0013:ruleset-binding:' || v_suffix,
            v_ruleset_reference_id,v_ruleset_snapshot_id
          ) ON CONFLICT DO NOTHING;
          IF NOT EXISTS (
            SELECT 1 FROM ruleset_bindings
             WHERE tenant_id = p_tenant_id AND workspace_id = p_workspace_id
               AND record_id = v_ruleset_binding_id
               AND created_by = 'migration:0013'
               AND canonical_text = v_text
               AND digest_sha256 = encode(sha256(convert_to(v_text,'UTF8')),'hex')
               AND ruleset_reference_id = v_ruleset_reference_id
               AND ruleset_version_snapshot_id = v_ruleset_snapshot_id
          ) THEN
            RAISE EXCEPTION 'STUDIO_DEFAULT_POLICY_ID_CONFLICT' USING ERRCODE = '55000';
          END IF;
          END IF;

          IF NOT EXISTS (
            SELECT 1 FROM workspace_policies
             WHERE tenant_id = p_tenant_id AND workspace_id = p_workspace_id
               AND canonical_json->>'active' = 'true'
               AND canonical_json->>'current' = 'true'
               AND canonical_json->>'workspace_id' = p_workspace_id
               AND nullif(canonical_json->>'authority_policy','') IS NOT NULL
               AND nullif(canonical_json->>'data_area','') IS NOT NULL
          ) OR NOT EXISTS (
            SELECT 1 FROM knowledge_scopes
             WHERE tenant_id = p_tenant_id AND workspace_id = p_workspace_id
               AND canonical_json->>'active' = 'true'
               AND canonical_json->>'current' = 'true'
               AND canonical_json->>'workspace_id' = p_workspace_id
               AND canonical_json->>'scope' = 'workspace'
          ) OR NOT EXISTS (
            SELECT 1 FROM weight_profiles
             WHERE tenant_id = p_tenant_id AND workspace_id = p_workspace_id
               AND canonical_json->>'active' = 'true'
               AND canonical_json->>'current' = 'true'
               AND canonical_json->>'workspace_id' = p_workspace_id
               AND canonical_json->>'profile' = 'trusted-source-v2'
          ) THEN
            RAISE EXCEPTION 'STUDIO_DEFAULT_POLICY_POSTCONDITION_FAILED' USING ERRCODE = '55000';
          END IF;

          SELECT EXISTS (
            SELECT 1
              FROM (
                SELECT * FROM ruleset_bindings
                 WHERE tenant_id = p_tenant_id AND workspace_id = p_workspace_id
                 ORDER BY version DESC, created_at DESC LIMIT 1
              ) binding
              JOIN ruleset_version_snapshots snapshot
                ON (snapshot.tenant_id,snapshot.workspace_id,snapshot.record_id) =
                   (binding.tenant_id,binding.workspace_id,binding.ruleset_version_snapshot_id)
              JOIN ruleset_references reference
                ON (reference.tenant_id,reference.workspace_id,reference.record_id) =
                   (binding.tenant_id,binding.workspace_id,binding.ruleset_reference_id)
               AND snapshot.ruleset_reference_id = reference.record_id
             WHERE binding.canonical_json->>'active' = 'true'
               AND binding.canonical_json->>'current' = 'true'
               AND binding.canonical_json->>'workspace_id' = p_workspace_id
               AND binding.canonical_json->>'review_condition' = 'review_required'
               AND coalesce(binding.canonical_json->>'ruleset_version_id','') = snapshot.record_id
               AND snapshot.canonical_json->>'active' = 'true'
               AND snapshot.canonical_json->>'current' = 'true'
               AND snapshot.canonical_json->>'workspace_id' = p_workspace_id
               AND snapshot.canonical_json->>'review_condition' = 'review_required'
               AND reference.canonical_json->>'active' = 'true'
               AND reference.canonical_json->>'current' = 'true'
               AND reference.canonical_json->>'workspace_id' = p_workspace_id
               AND reference.canonical_json->>'name' = 'default-review-required'
          ) INTO v_complete_ruleset;
          IF NOT v_complete_ruleset THEN
            RAISE EXCEPTION 'STUDIO_DEFAULT_POLICY_POSTCONDITION_FAILED' USING ERRCODE = '55000';
          END IF;
        END $$;

        DO $$
        DECLARE v_workspace record;
        BEGIN
          FOR v_workspace IN SELECT tenant_id, workspace_id FROM workspaces LOOP
            PERFORM ensure_studio_workspace_defaults(
              v_workspace.tenant_id, v_workspace.workspace_id
            );
          END LOOP;
        END $$;

        CREATE FUNCTION initialize_studio_workspace_defaults()
        RETURNS trigger LANGUAGE plpgsql AS $$
        BEGIN
          PERFORM ensure_studio_workspace_defaults(NEW.tenant_id, NEW.workspace_id);
          RETURN NEW;
        END $$;

        CREATE TRIGGER studio_workspace_defaults_after_insert
          AFTER INSERT ON workspaces
          FOR EACH ROW EXECUTE FUNCTION initialize_studio_workspace_defaults();
        """
    )


def downgrade() -> None:
    op.get_bind().exec_driver_sql(
        r"""
        DROP TRIGGER IF EXISTS studio_workspace_defaults_after_insert ON workspaces;
        DROP FUNCTION IF EXISTS initialize_studio_workspace_defaults();
        DROP FUNCTION IF EXISTS ensure_studio_workspace_defaults(text, text);

        DO $$
        BEGIN
          IF EXISTS (
            SELECT 1 FROM workspace_policies owned JOIN workspace_policies child
              ON (child.tenant_id,child.workspace_id,child.previous_version_id) =
                 (owned.tenant_id,owned.workspace_id,owned.record_id)
             WHERE owned.created_by = 'migration:0013' AND child.created_by <> 'migration:0013'
          ) OR EXISTS (
            SELECT 1 FROM knowledge_scopes owned JOIN knowledge_scopes child
              ON (child.tenant_id,child.workspace_id,child.previous_version_id) =
                 (owned.tenant_id,owned.workspace_id,owned.record_id)
             WHERE owned.created_by = 'migration:0013' AND child.created_by <> 'migration:0013'
          ) OR EXISTS (
            SELECT 1 FROM weight_profiles owned JOIN weight_profiles child
              ON (child.tenant_id,child.workspace_id,child.previous_version_id) =
                 (owned.tenant_id,owned.workspace_id,owned.record_id)
             WHERE owned.created_by = 'migration:0013' AND child.created_by <> 'migration:0013'
          ) OR EXISTS (
            SELECT 1 FROM ruleset_references owned JOIN ruleset_references child
              ON (child.tenant_id,child.workspace_id,child.previous_version_id) =
                 (owned.tenant_id,owned.workspace_id,owned.record_id)
             WHERE owned.created_by = 'migration:0013' AND child.created_by <> 'migration:0013'
          ) OR EXISTS (
            SELECT 1 FROM ruleset_version_snapshots owned JOIN ruleset_version_snapshots child
              ON (child.tenant_id,child.workspace_id,child.previous_version_id) =
                 (owned.tenant_id,owned.workspace_id,owned.record_id)
             WHERE owned.created_by = 'migration:0013' AND child.created_by <> 'migration:0013'
          ) OR EXISTS (
            SELECT 1 FROM ruleset_bindings owned JOIN ruleset_bindings child
              ON (child.tenant_id,child.workspace_id,child.previous_version_id) =
                 (owned.tenant_id,owned.workspace_id,owned.record_id)
             WHERE owned.created_by = 'migration:0013' AND child.created_by <> 'migration:0013'
          ) OR EXISTS (
            SELECT 1
              FROM ruleset_bindings owned
              JOIN rule_evaluations child
                ON (child.tenant_id,child.workspace_id,child.ruleset_binding_id) =
                   (owned.tenant_id,owned.workspace_id,owned.record_id)
             WHERE owned.created_by = 'migration:0013'
               AND child.created_by <> 'migration:0013'
          ) OR EXISTS (
            SELECT 1
              FROM ruleset_version_snapshots owned
              JOIN rule_evaluations child
                ON (child.tenant_id,child.workspace_id,child.ruleset_version_snapshot_id) =
                   (owned.tenant_id,owned.workspace_id,owned.record_id)
             WHERE owned.created_by = 'migration:0013'
               AND child.created_by <> 'migration:0013'
          ) OR EXISTS (
            SELECT 1
              FROM ruleset_version_snapshots owned
              JOIN ruleset_bindings child
                ON (child.tenant_id,child.workspace_id,child.ruleset_version_snapshot_id) =
                   (owned.tenant_id,owned.workspace_id,owned.record_id)
             WHERE owned.created_by = 'migration:0013'
               AND child.created_by <> 'migration:0013'
          ) OR EXISTS (
            SELECT 1
              FROM ruleset_references owned
              JOIN ruleset_version_snapshots child
                ON (child.tenant_id,child.workspace_id,child.ruleset_reference_id) =
                   (owned.tenant_id,owned.workspace_id,owned.record_id)
             WHERE owned.created_by = 'migration:0013'
               AND child.created_by <> 'migration:0013'
          ) OR EXISTS (
            SELECT 1
              FROM ruleset_references owned
              JOIN ruleset_bindings child
                ON (child.tenant_id,child.workspace_id,child.ruleset_reference_id) =
                   (owned.tenant_id,owned.workspace_id,owned.record_id)
             WHERE owned.created_by = 'migration:0013'
               AND child.created_by <> 'migration:0013'
          ) OR EXISTS (
            SELECT 1
              FROM knowledge_scopes owned
              JOIN weight_profiles child
                ON (child.tenant_id,child.workspace_id,child.knowledge_scope_id) =
                   (owned.tenant_id,owned.workspace_id,owned.record_id)
             WHERE owned.created_by = 'migration:0013'
               AND child.created_by <> 'migration:0013'
          ) OR EXISTS (
            SELECT 1
              FROM knowledge_scopes owned
              JOIN scope_snapshots child
                ON (child.tenant_id,child.workspace_id,child.knowledge_scope_id) =
                   (owned.tenant_id,owned.workspace_id,owned.record_id)
             WHERE owned.created_by = 'migration:0013'
               AND child.created_by <> 'migration:0013'
          ) OR EXISTS (
            SELECT 1
              FROM workspace_policies owned
              JOIN step_up_authorizations child
                ON (child.tenant_id,child.workspace_id,child.workspace_policy_id) =
                   (owned.tenant_id,owned.workspace_id,owned.record_id)
             WHERE owned.created_by = 'migration:0013'
               AND child.created_by <> 'migration:0013'
          ) OR EXISTS (
            SELECT 1
              FROM workspace_policies owned
              JOIN access_decisions child
                ON (child.tenant_id,child.workspace_id,child.workspace_policy_id) =
                   (owned.tenant_id,owned.workspace_id,owned.record_id)
             WHERE owned.created_by = 'migration:0013'
               AND child.created_by <> 'migration:0013'
          ) THEN
            RAISE EXCEPTION 'STUDIO_DEFAULT_POLICY_ROLLBACK_BLOCKED'
              USING ERRCODE = '55000';
          END IF;

          ALTER TABLE workspace_policies DISABLE TRIGGER workspace_policies_immutable;
          ALTER TABLE knowledge_scopes DISABLE TRIGGER knowledge_scopes_immutable;
          ALTER TABLE weight_profiles DISABLE TRIGGER weight_profiles_immutable;
          ALTER TABLE ruleset_references DISABLE TRIGGER ruleset_references_immutable;
          ALTER TABLE ruleset_version_snapshots DISABLE TRIGGER ruleset_version_snapshots_immutable;
          ALTER TABLE ruleset_bindings DISABLE TRIGGER ruleset_bindings_immutable;

          DELETE FROM ruleset_bindings
           WHERE created_by = 'migration:0013'
             AND record_id = 'studio-default:ruleset-binding:' || md5(tenant_id || '|' || workspace_id);
          DELETE FROM ruleset_version_snapshots
           WHERE created_by = 'migration:0013'
             AND record_id = 'studio-default:ruleset-snapshot:' || md5(tenant_id || '|' || workspace_id);
          DELETE FROM ruleset_references
           WHERE created_by = 'migration:0013'
             AND record_id = 'studio-default:ruleset-reference:' || md5(tenant_id || '|' || workspace_id);
          DELETE FROM weight_profiles
           WHERE created_by = 'migration:0013'
             AND record_id = 'studio-default:weight-profile:' || md5(tenant_id || '|' || workspace_id);
          DELETE FROM knowledge_scopes
           WHERE created_by = 'migration:0013'
             AND record_id = 'studio-default:knowledge-scope:' || md5(tenant_id || '|' || workspace_id);
          DELETE FROM workspace_policies
           WHERE created_by = 'migration:0013'
             AND record_id = 'studio-default:workspace-policy:' || md5(tenant_id || '|' || workspace_id);

          ALTER TABLE workspace_policies ENABLE TRIGGER workspace_policies_immutable;
          ALTER TABLE knowledge_scopes ENABLE TRIGGER knowledge_scopes_immutable;
          ALTER TABLE weight_profiles ENABLE TRIGGER weight_profiles_immutable;
          ALTER TABLE ruleset_references ENABLE TRIGGER ruleset_references_immutable;
          ALTER TABLE ruleset_version_snapshots ENABLE TRIGGER ruleset_version_snapshots_immutable;
          ALTER TABLE ruleset_bindings ENABLE TRIGGER ruleset_bindings_immutable;
        END $$;
        """
    )
