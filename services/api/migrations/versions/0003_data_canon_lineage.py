"""Canonical entities, lineage, immutable snapshots and state transitions."""

from __future__ import annotations

from alembic import op


revision = "0003"
down_revision = "0002"
branch_labels = None
depends_on = None


ENTITY_TABLES = (
    "workspace_policies", "step_up_authorizations", "access_decisions",
    "sources", "source_versions", "processing_runs", "understanding_results",
    "extraction_evidence", "transcription_runs", "transcript_versions",
    "transcript_segments", "evidence_spans", "index_versions",
    "knowledge_scopes", "weight_profiles", "scope_snapshots", "conflict_records",
    "ruleset_references", "ruleset_version_snapshots", "ruleset_bindings",
    "rule_evaluations", "provider_profiles", "runtime_nodes", "model_artifacts",
    "model_installations", "model_deployments", "role_bindings",
    "routing_policy_versions", "routing_decisions", "model_attempts",
    "conversations", "messages", "runs", "run_steps", "run_snapshots",
    "run_results", "citations", "generation_requests",
    "generation_settings_snapshots", "studio_outputs", "output_versions",
    "evidence_references", "review_requests", "approval_requests", "approvals",
    "deliveries", "knowledge_registrations", "connectors", "external_references",
    "egress_decisions",
)

IMMUTABLE_TABLES = frozenset({
    "workspace_policies", "step_up_authorizations", "access_decisions",
    "source_versions", "understanding_results", "extraction_evidence",
    "transcription_runs", "transcript_versions", "transcript_segments",
    "evidence_spans", "index_versions", "knowledge_scopes", "weight_profiles",
    "scope_snapshots", "conflict_records", "ruleset_references",
    "ruleset_version_snapshots", "ruleset_bindings", "rule_evaluations",
    "provider_profiles", "runtime_nodes", "model_artifacts", "model_installations",
    "model_deployments", "role_bindings", "routing_policy_versions",
    "routing_decisions", "model_attempts", "conversations", "messages",
    "run_steps", "run_snapshots", "run_results", "citations",
    "generation_settings_snapshots", "studio_outputs", "evidence_references",
    "review_requests", "approvals", "deliveries", "connectors",
    "external_references", "egress_decisions",
})

STATE_TABLES = {
    "Source": ("sources", "registered"),
    "ProcessingRun": ("processing_runs", "accepted"),
    "Run": ("runs", "accepted"),
    "GenerationRequest": ("generation_requests", "configuring"),
    "OutputVersion": ("output_versions", "generating"),
    "ApprovalRequest": ("approval_requests", "pending"),
    "KnowledgeRegistration": ("knowledge_registrations", "requested"),
}

EXTRA_COLUMNS = {
    "step_up_authorizations": "workspace_policy_id text, action text NOT NULL, target_id text NOT NULL, expires_at timestamptz NOT NULL, used_at timestamptz",
    "access_decisions": "workspace_policy_id text, action text NOT NULL, resource_type text NOT NULL, resource_id text NOT NULL, access_state text NOT NULL CHECK (access_state IN ('available','partially_redacted','access_blocked'))",
    "source_versions": "source_id text NOT NULL, object_id text",
    "processing_runs": "source_version_id text NOT NULL, retry_of_processing_run_id text, modality text NOT NULL CHECK (modality IN ('document','table','image','audio')), trigger_type text NOT NULL CHECK (trigger_type IN ('initial','readiness_event','manual_request','retry')), trigger_event_id text, ready_gate_result text",
    "understanding_results": "processing_run_id text NOT NULL, source_version_id text NOT NULL",
    "extraction_evidence": "understanding_result_id text NOT NULL, source_version_id text NOT NULL",
    "transcription_runs": "processing_run_id text NOT NULL, source_version_id text NOT NULL",
    "transcript_versions": "transcription_run_id text NOT NULL, source_version_id text NOT NULL",
    "transcript_segments": "transcript_version_id text NOT NULL",
    "evidence_spans": "source_version_id text NOT NULL, transcript_version_id text",
    "index_versions": "source_version_id text NOT NULL, object_id text",
    "weight_profiles": "knowledge_scope_id text NOT NULL",
    "scope_snapshots": "knowledge_scope_id text NOT NULL",
    "conflict_records": "scope_snapshot_id text NOT NULL",
    "ruleset_version_snapshots": "ruleset_reference_id text NOT NULL",
    "ruleset_bindings": "ruleset_reference_id text NOT NULL, ruleset_version_snapshot_id text",
    "rule_evaluations": "ruleset_binding_id text NOT NULL, ruleset_version_snapshot_id text NOT NULL, run_id text",
    "model_installations": "runtime_node_id text NOT NULL, model_artifact_id text NOT NULL",
    "model_deployments": "provider_profile_id text NOT NULL, runtime_node_id text, model_artifact_id text NOT NULL, model_installation_id text",
    "role_bindings": "model_deployment_id text NOT NULL, role text NOT NULL CHECK (role IN ('text','vision','audio_understanding','speech_to_text','embedding','reranker'))",
    "routing_decisions": "run_id text NOT NULL, routing_policy_version_id text NOT NULL, egress_decision_id text",
    "model_attempts": "routing_decision_id text NOT NULL, model_deployment_id text NOT NULL, model_artifact_id text NOT NULL, candidate_order integer NOT NULL CHECK (candidate_order > 0), started_at timestamptz NOT NULL, finished_at timestamptz, safe_error_code text, cost_amount numeric(18,6), usage_json jsonb NOT NULL DEFAULT '{}'::jsonb CHECK (jsonb_typeof(usage_json) = 'object')",
    "messages": "conversation_id text NOT NULL",
    "runs": "conversation_id text",
    "run_steps": "run_id text NOT NULL",
    "run_snapshots": "run_id text NOT NULL, scope_snapshot_id text NOT NULL, routing_policy_version_id text NOT NULL, generation_settings_snapshot_id text",
    "run_results": "run_id text NOT NULL, routing_decision_id text, selected_model_attempt_id text",
    "citations": "run_result_id text NOT NULL, source_version_id text NOT NULL, evidence_span_id text NOT NULL",
    "generation_requests": "generation_settings_snapshot_id text",
    "studio_outputs": "generation_request_id text NOT NULL",
    "output_versions": "studio_output_id text NOT NULL, generation_settings_snapshot_id text NOT NULL",
    "evidence_references": "output_version_id text NOT NULL, source_version_id text NOT NULL, evidence_span_id text NOT NULL",
    "review_requests": "output_version_id text NOT NULL",
    "approval_requests": "output_version_id text NOT NULL, review_request_id text",
    "approvals": "approval_request_id text NOT NULL, output_version_id text NOT NULL, decision text NOT NULL CHECK (decision IN ('approved','rejected'))",
    "deliveries": "output_version_id text NOT NULL, approval_id text NOT NULL",
    "knowledge_registrations": "output_version_id text NOT NULL, registered_source_version_id text",
    "external_references": "connector_id text NOT NULL, source_id text",
    "egress_decisions": "run_id text, provider_profile_id text",
}

FOREIGN_KEYS = {
    "step_up_authorizations": (("workspace_policy_id", "workspace_policies"),),
    "access_decisions": (("workspace_policy_id", "workspace_policies"),),
    "source_versions": (("source_id", "sources", "record_id"), ("object_id", "object_records", "object_id")),
    "processing_runs": (("source_version_id", "source_versions"), ("retry_of_processing_run_id", "processing_runs")),
    "understanding_results": (("processing_run_id", "processing_runs"), ("source_version_id", "source_versions")),
    "extraction_evidence": (("understanding_result_id", "understanding_results"), ("source_version_id", "source_versions")),
    "transcription_runs": (("processing_run_id", "processing_runs"), ("source_version_id", "source_versions")),
    "transcript_versions": (("transcription_run_id", "transcription_runs"), ("source_version_id", "source_versions")),
    "transcript_segments": (("transcript_version_id", "transcript_versions"),),
    "evidence_spans": (("source_version_id", "source_versions"), ("transcript_version_id", "transcript_versions")),
    "index_versions": (("source_version_id", "source_versions", "record_id"), ("object_id", "object_records", "object_id")),
    "weight_profiles": (("knowledge_scope_id", "knowledge_scopes"),),
    "scope_snapshots": (("knowledge_scope_id", "knowledge_scopes"),),
    "conflict_records": (("scope_snapshot_id", "scope_snapshots"),),
    "ruleset_version_snapshots": (("ruleset_reference_id", "ruleset_references"),),
    "ruleset_bindings": (("ruleset_reference_id", "ruleset_references"), ("ruleset_version_snapshot_id", "ruleset_version_snapshots")),
    "rule_evaluations": (("ruleset_binding_id", "ruleset_bindings"), ("ruleset_version_snapshot_id", "ruleset_version_snapshots"), ("run_id", "runs")),
    "model_installations": (("runtime_node_id", "runtime_nodes"), ("model_artifact_id", "model_artifacts")),
    "model_deployments": (("provider_profile_id", "provider_profiles"), ("runtime_node_id", "runtime_nodes"), ("model_artifact_id", "model_artifacts"), ("model_installation_id", "model_installations")),
    "role_bindings": (("model_deployment_id", "model_deployments"),),
    "routing_decisions": (("run_id", "runs"), ("routing_policy_version_id", "routing_policy_versions"), ("egress_decision_id", "egress_decisions")),
    "model_attempts": (("routing_decision_id", "routing_decisions"), ("model_deployment_id", "model_deployments"), ("model_artifact_id", "model_artifacts")),
    "messages": (("conversation_id", "conversations"),),
    "runs": (("conversation_id", "conversations"),),
    "run_steps": (("run_id", "runs"),),
    "run_snapshots": (("run_id", "runs"), ("scope_snapshot_id", "scope_snapshots"), ("routing_policy_version_id", "routing_policy_versions"), ("generation_settings_snapshot_id", "generation_settings_snapshots")),
    "run_results": (("run_id", "runs"), ("routing_decision_id", "routing_decisions"), ("selected_model_attempt_id", "model_attempts")),
    "citations": (("run_result_id", "run_results"), ("source_version_id", "source_versions"), ("evidence_span_id", "evidence_spans")),
    "generation_requests": (("generation_settings_snapshot_id", "generation_settings_snapshots"),),
    "studio_outputs": (("generation_request_id", "generation_requests"),),
    "output_versions": (("studio_output_id", "studio_outputs"), ("generation_settings_snapshot_id", "generation_settings_snapshots")),
    "evidence_references": (("output_version_id", "output_versions"), ("source_version_id", "source_versions"), ("evidence_span_id", "evidence_spans")),
    "review_requests": (("output_version_id", "output_versions"),),
    "approval_requests": (("output_version_id", "output_versions"), ("review_request_id", "review_requests")),
    "approvals": (("approval_request_id", "approval_requests"), ("output_version_id", "output_versions")),
    "deliveries": (("output_version_id", "output_versions"), ("approval_id", "approvals")),
    "knowledge_registrations": (("output_version_id", "output_versions"), ("registered_source_version_id", "source_versions")),
    "external_references": (("connector_id", "connectors"), ("source_id", "sources")),
    "egress_decisions": (("run_id", "runs"), ("provider_profile_id", "provider_profiles")),
}

# The remaining references use the canonical record_id key.  Explicit triples
# above preserve links to predecessor tables whose physical identifier differs.
FOREIGN_KEYS = {
    table: tuple(
        edge if len(edge) == 3 else (edge[0], edge[1], "record_id")
        for edge in edges
    )
    for table, edges in FOREIGN_KEYS.items()
}


TRANSITIONS = {
    "Source": {
        ("registered", "security_check"), ("security_check", "processing"),
        ("processing", "indexing"), ("indexing", "ready"),
        ("processing", "waiting_model"), ("processing", "partial_understanding"),
        ("processing", "needs_review"), ("processing", "failed"),
        ("waiting_model", "processing"), ("partial_understanding", "processing"),
        ("partial_understanding", "needs_review"), ("partial_understanding", "disabled"),
        ("needs_review", "processing"), ("failed", "processing"),
        ("ready", "expired"), ("ready", "disabled"), ("expired", "disabled"),
        ("disabled", "deleting"), ("deleting", "deleted"),
    },
    "ProcessingRun": {
        ("accepted", "vision_llm_understanding"), ("accepted", "audio_llm_understanding"),
        ("accepted", "speech_to_text"),
        ("vision_llm_understanding", "parser_ocr_validation"),
        ("audio_llm_understanding", "transcript_timecode_validation"),
        ("speech_to_text", "llm_semantic_understanding"),
        ("llm_semantic_understanding", "transcript_timecode_validation"),
        ("parser_ocr_validation", "evidence_reconciliation"),
        ("transcript_timecode_validation", "evidence_reconciliation"),
        ("evidence_reconciliation", "completed"),
        *((state, "failed") for state in (
            "accepted", "vision_llm_understanding", "audio_llm_understanding",
            "speech_to_text", "llm_semantic_understanding", "parser_ocr_validation",
            "transcript_timecode_validation", "evidence_reconciliation",
        )),
        ("accepted", "policy_blocked"),
    },
    "Run": {
        ("accepted", "planning"), ("planning", "retrieving"),
        ("retrieving", "generating"), ("generating", "validating"),
        ("validating", "completed"), ("waiting_user", "planning"),
        ("waiting_approval", "planning"),
        *((state, branch) for state in (
            "accepted", "planning", "retrieving", "generating", "validating"
        ) for branch in ("waiting_user", "policy_blocked", "failed", "cancelled")),
        (("accepted", "waiting_approval")), (("planning", "waiting_approval")),
        (("waiting_user", "cancelled")), (("waiting_approval", "cancelled")),
    },
    "GenerationRequest": {
        ("configuring", "confirmed"),
        ("confirmed", "configuring"),
        ("confirmed", "submitted"),
    },
    "OutputVersion": {("generating", "draft"), ("draft", "review_requested"), ("review_requested", "in_review"), ("in_review", "revision_requested"), ("in_review", "approved"), ("approved", "delivered")},
    "ApprovalRequest": {("pending", "approved"), ("pending", "rejected"), ("pending", "expired"), ("pending", "withdrawn")},
    "KnowledgeRegistration": {("requested", "registered"), ("requested", "rejected")},
}


def _state_for_table(table: str) -> str | None:
    return next((initial for mapped, initial in STATE_TABLES.values() if mapped == table), None)


def _entity_for_table(table: str) -> str | None:
    return next((entity for entity, (mapped, _) in STATE_TABLES.items() if mapped == table), None)


def _create_entity_table(table: str) -> None:
    initial_state = _state_for_table(table)
    entity = _entity_for_table(table)
    if initial_state is None or entity is None:
        state_column = ""
    else:
        states = sorted({value for edge in TRANSITIONS[entity] for value in edge})
        allowed = ",".join(_quote(value) for value in states)
        state_column = (
            f"state text NOT NULL DEFAULT '{initial_state}' CHECK (state IN ({allowed})),"
        )
    extra = EXTRA_COLUMNS.get(table)
    optional_parts = [part for part in (extra,) if part]
    optional_sql = (",\n          " + ",\n          ".join(optional_parts)) if optional_parts else ""
    op.execute(f"""
        CREATE TABLE {table} (
          tenant_id text NOT NULL,
          workspace_id text NOT NULL,
          record_id text NOT NULL CHECK (record_id ~ '^[A-Za-z0-9][A-Za-z0-9._:-]{{0,255}}$'),
          aggregate_id text NOT NULL CHECK (aggregate_id ~ '^[A-Za-z0-9][A-Za-z0-9._:-]{{0,255}}$'),
          version integer NOT NULL DEFAULT 1 CHECK (version > 0),
          schema_version integer NOT NULL DEFAULT 1 CHECK (schema_version > 0),
          previous_version_id text,
          canonical_json jsonb NOT NULL DEFAULT '{{}}'::jsonb CHECK (jsonb_typeof(canonical_json) = 'object'),
          canonical_text text NOT NULL DEFAULT '{{}}',
          digest_sha256 text NOT NULL CHECK (digest_sha256 ~ '^[0-9a-f]{{64}}$'),
          {state_column}
          created_by text NOT NULL,
          trace_id text NOT NULL,
          created_at timestamptz NOT NULL DEFAULT now(),
          PRIMARY KEY (tenant_id, workspace_id, record_id),
          UNIQUE (tenant_id, workspace_id, aggregate_id, version),
          FOREIGN KEY (tenant_id, workspace_id) REFERENCES workspaces(tenant_id, workspace_id),
          FOREIGN KEY (tenant_id, workspace_id, previous_version_id)
            REFERENCES {table}(tenant_id, workspace_id, record_id)
          {optional_sql}
        )
    """)


def _quote(value: str) -> str:
    return "'" + value.replace("'", "''") + "'"


def upgrade() -> None:
    for table in ENTITY_TABLES:
        _create_entity_table(table)

    # Add cross-entity references only after all canonical tables exist.  This
    # also supports deliberate cycles such as Run -> RoutingDecision -> Run.
    for table, edges in FOREIGN_KEYS.items():
        for column, parent, parent_column in edges:
            op.execute(
                f"ALTER TABLE {table} ADD CONSTRAINT {table}_{column}_fk "
                f"FOREIGN KEY (tenant_id, workspace_id, {column}) "
                f"REFERENCES {parent}(tenant_id, workspace_id, {parent_column})"
            )

    op.execute("""
        CREATE TABLE canon_transition_rules (
          entity_type text NOT NULL,
          source_state text NOT NULL,
          target_state text NOT NULL,
          PRIMARY KEY (entity_type, source_state, target_state)
        );
        CREATE TABLE canon_state_transitions (
          tenant_id text NOT NULL,
          workspace_id text NOT NULL,
          record_id text NOT NULL,
          entity_type text NOT NULL,
          transition_id text NOT NULL,
          transition_version integer NOT NULL CHECK (transition_version > 1),
          actor_id text NOT NULL,
          source_state text NOT NULL,
          target_state text NOT NULL,
          reason_code text NOT NULL,
          trace_id text NOT NULL,
          policy_version text NOT NULL,
          occurred_at timestamptz NOT NULL DEFAULT now(),
          PRIMARY KEY (tenant_id, workspace_id, transition_id),
          UNIQUE (tenant_id, workspace_id, entity_type, record_id, transition_version),
          FOREIGN KEY (tenant_id, workspace_id) REFERENCES workspaces(tenant_id, workspace_id),
          FOREIGN KEY (entity_type, source_state, target_state)
            REFERENCES canon_transition_rules(entity_type, source_state, target_state)
        );
        CREATE TABLE canon_transition_attempts (
          tenant_id text NOT NULL,
          workspace_id text NOT NULL,
          entity_type text NOT NULL,
          record_id text NOT NULL,
          attempt_id text NOT NULL CHECK (attempt_id ~ '^[A-Za-z0-9][A-Za-z0-9._:-]{0,255}$'),
          expected_version integer NOT NULL CHECK (expected_version > 0),
          current_version integer CHECK (current_version > 0),
          source_state text,
          target_state text NOT NULL,
          actor_id text NOT NULL,
          reason_code text NOT NULL,
          safe_error_code text,
          trace_id text NOT NULL,
          policy_version text NOT NULL,
          outcome text NOT NULL CHECK (outcome IN ('succeeded','denied')),
          result_state text,
          result_version integer CHECK (result_version > 0),
          occurred_at timestamptz NOT NULL DEFAULT now(),
          PRIMARY KEY (tenant_id, workspace_id, attempt_id),
          FOREIGN KEY (tenant_id, workspace_id) REFERENCES workspaces(tenant_id, workspace_id),
          CHECK (
            (outcome = 'succeeded' AND safe_error_code IS NULL
              AND result_state IS NOT NULL AND result_version IS NOT NULL)
            OR
            (outcome = 'denied' AND safe_error_code IN (
              'CANON_TRANSITION_INVALID','CANON_VERSION_CONFLICT','CANON_RECORD_NOT_FOUND'
            ))
          )
        );

        CREATE FUNCTION validate_canon_insert() RETURNS trigger LANGUAGE plpgsql AS $$
        DECLARE previous_row record;
        BEGIN
          IF jsonb_typeof(NEW.canonical_json) <> 'object'
             OR NEW.canonical_text::jsonb <> NEW.canonical_json THEN
            RAISE EXCEPTION 'CANON_SNAPSHOT_INVALID' USING ERRCODE = '22023';
          END IF;
          IF encode(sha256(convert_to(NEW.canonical_text, 'UTF8')), 'hex') <> NEW.digest_sha256 THEN
            RAISE EXCEPTION 'CANON_DIGEST_MISMATCH' USING ERRCODE = '22023';
          END IF;
          IF NEW.version = 1 THEN
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
            ('sources', 'registered', 1),
            ('processing_runs', 'accepted', 1),
            ('runs', 'accepted', 1),
            ('generation_requests', 'configuring', 1),
            ('output_versions', 'generating', 1),
            ('approval_requests', 'pending', 1),
            ('knowledge_registrations', 'requested', 1)
          ) THEN
            NULL;
          ELSIF TG_TABLE_NAME IN (
            'sources','processing_runs','runs','generation_requests',
            'output_versions','approval_requests','knowledge_registrations'
          ) THEN
            RAISE EXCEPTION 'CANON_STATE_INITIAL_INVALID' USING ERRCODE = '23514';
          END IF;
          RETURN NEW;
        END $$;

        CREATE FUNCTION reject_canon_immutable_mutation() RETURNS trigger LANGUAGE plpgsql AS $$
        BEGIN
          RAISE EXCEPTION 'CANON_IMMUTABLE_MUTATION' USING ERRCODE = '55000';
        END $$;

        CREATE FUNCTION guard_canon_state_mutation() RETURNS trigger LANGUAGE plpgsql AS $$
        BEGIN
          IF TG_OP = 'DELETE' OR current_setting('app.canon_transition', true) <> '1'
             OR NEW.version <> OLD.version + 1
             OR NEW.state = OLD.state
             OR (to_jsonb(NEW) - ARRAY['state','version']) <> (to_jsonb(OLD) - ARRAY['state','version']) THEN
            RAISE EXCEPTION 'CANON_IMMUTABLE_MUTATION' USING ERRCODE = '55000';
          END IF;
          RETURN NEW;
        END $$;
    """)

    transition_values = ",".join(
        f"({_quote(entity)}, {_quote(source)}, {_quote(target)})"
        for entity, edges in TRANSITIONS.items()
        for source, target in sorted(edges)
    )
    op.execute(
        "INSERT INTO canon_transition_rules(entity_type, source_state, target_state) VALUES "
        + transition_values
    )

    for table in ENTITY_TABLES:
        op.execute(
            f"CREATE TRIGGER {table}_validate BEFORE INSERT ON {table} "
            "FOR EACH ROW EXECUTE FUNCTION validate_canon_insert()"
        )
        if table in IMMUTABLE_TABLES:
            op.execute(
                f"CREATE TRIGGER {table}_immutable BEFORE UPDATE OR DELETE ON {table} "
                "FOR EACH ROW EXECUTE FUNCTION reject_canon_immutable_mutation()"
            )
        else:
            op.execute(
                f"CREATE TRIGGER {table}_state_guard BEFORE UPDATE OR DELETE ON {table} "
                "FOR EACH ROW EXECUTE FUNCTION guard_canon_state_mutation()"
            )
        op.execute(f"CREATE INDEX {table}_created_idx ON {table} (tenant_id, workspace_id, created_at)")

    op.execute("""
        CREATE TRIGGER canon_state_transitions_immutable
          BEFORE UPDATE OR DELETE ON canon_state_transitions
          FOR EACH ROW EXECUTE FUNCTION reject_canon_immutable_mutation();
        CREATE TRIGGER canon_transition_attempts_immutable
          BEFORE UPDATE OR DELETE ON canon_transition_attempts
          FOR EACH ROW EXECUTE FUNCTION reject_canon_immutable_mutation();

        CREATE FUNCTION transition_canon_state(
          p_entity_type text,
          p_record_id text,
          p_expected_version integer,
          p_target_state text,
          p_transition_id text,
          p_reason_code text,
          p_trace_id text,
          p_policy_version text
        ) RETURNS TABLE(state text, version integer, outcome text, error_code text)
        LANGUAGE plpgsql SECURITY DEFINER SET search_path = pg_catalog, public AS $$
        DECLARE
          v_table text;
          v_source_state text;
          v_version integer;
          v_error_code text;
          v_existing public.canon_transition_attempts%ROWTYPE;
          v_tenant text := nullif(current_setting('app.tenant_id', true), '');
          v_workspace text := nullif(current_setting('app.workspace_id', true), '');
          v_actor text := nullif(current_setting('app.actor_id', true), '');
        BEGIN
          IF v_tenant IS NULL OR v_workspace IS NULL OR v_actor IS NULL THEN
            RAISE EXCEPTION 'CANON_SCOPE_DENIED' USING ERRCODE = '42501';
          END IF;
          PERFORM pg_advisory_xact_lock(
            hashtextextended(v_tenant || ':' || v_workspace || ':' || p_transition_id, 0)
          );
          SELECT a.* INTO v_existing
          FROM public.canon_transition_attempts AS a
          WHERE a.tenant_id = v_tenant AND a.workspace_id = v_workspace
            AND a.attempt_id = p_transition_id;
          IF FOUND THEN
            IF v_existing.entity_type <> p_entity_type
               OR v_existing.record_id <> p_record_id
               OR v_existing.expected_version <> p_expected_version
               OR v_existing.target_state <> p_target_state
               OR v_existing.actor_id <> v_actor
               OR v_existing.reason_code <> p_reason_code
               OR v_existing.trace_id <> p_trace_id
               OR v_existing.policy_version <> p_policy_version THEN
              RETURN QUERY SELECT NULL::text, NULL::integer, 'denied'::text,
                'CANON_ATTEMPT_ID_REUSED'::text;
            ELSE
              RETURN QUERY SELECT v_existing.result_state, v_existing.result_version,
                v_existing.outcome, v_existing.safe_error_code;
            END IF;
            RETURN;
          END IF;
          v_table := CASE p_entity_type
            WHEN 'Source' THEN 'sources'
            WHEN 'ProcessingRun' THEN 'processing_runs'
            WHEN 'Run' THEN 'runs'
            WHEN 'GenerationRequest' THEN 'generation_requests'
            WHEN 'OutputVersion' THEN 'output_versions'
            WHEN 'ApprovalRequest' THEN 'approval_requests'
            WHEN 'KnowledgeRegistration' THEN 'knowledge_registrations'
            ELSE NULL
          END;
          IF v_table IS NULL THEN
            v_error_code := 'CANON_TRANSITION_INVALID';
          ELSE
            EXECUTE format(
              'SELECT state, version FROM public.%I WHERE tenant_id = $1 AND workspace_id = $2 AND record_id = $3 FOR UPDATE',
              v_table
            ) INTO v_source_state, v_version USING v_tenant, v_workspace, p_record_id;
          END IF;
          IF v_error_code IS NULL AND v_source_state IS NULL THEN
            v_error_code := 'CANON_RECORD_NOT_FOUND';
          ELSIF v_error_code IS NULL AND v_version <> p_expected_version THEN
            v_error_code := 'CANON_VERSION_CONFLICT';
          ELSIF v_error_code IS NULL AND NOT EXISTS (
            SELECT 1 FROM public.canon_transition_rules
            WHERE entity_type = p_entity_type AND source_state = v_source_state
              AND target_state = p_target_state
          ) THEN
            v_error_code := 'CANON_TRANSITION_INVALID';
          END IF;
          IF v_error_code IS NOT NULL THEN
            INSERT INTO public.canon_transition_attempts (
              tenant_id, workspace_id, entity_type, record_id, attempt_id,
              expected_version, current_version, source_state, target_state,
              actor_id, reason_code, safe_error_code, trace_id, policy_version,
              outcome, result_state, result_version
            ) VALUES (
              v_tenant, v_workspace, p_entity_type, p_record_id, p_transition_id,
              p_expected_version, v_version, v_source_state, p_target_state,
              v_actor, p_reason_code, v_error_code, p_trace_id, p_policy_version,
              'denied', v_source_state, v_version
            );
            INSERT INTO public.audit_events (
              event_id, tenant_id, workspace_id, actor_id, action, target_type,
              target_id, outcome, trace_id, policy_version, before_value, after_value, metadata
            ) VALUES (
              p_transition_id, v_tenant, v_workspace, v_actor, 'canon.transition',
              p_entity_type, p_record_id, 'denied', p_trace_id, p_policy_version,
              CASE WHEN v_source_state IS NULL THEN NULL ELSE
                jsonb_build_object('state', v_source_state, 'version', v_version) END,
              CASE WHEN v_source_state IS NULL THEN NULL ELSE
                jsonb_build_object('state', v_source_state, 'version', v_version) END,
              jsonb_build_object(
                'attempt_id', p_transition_id, 'reason_code', p_reason_code,
                'safe_error_code', v_error_code, 'target_state', p_target_state
              )
            );
            RETURN QUERY SELECT v_source_state, v_version, 'denied'::text, v_error_code;
            RETURN;
          END IF;
          PERFORM set_config('app.canon_transition', '1', true);
          EXECUTE format(
            'UPDATE public.%I SET state = $1, version = version + 1 WHERE tenant_id = $2 AND workspace_id = $3 AND record_id = $4',
            v_table
          ) USING p_target_state, v_tenant, v_workspace, p_record_id;
          INSERT INTO public.canon_state_transitions (
            tenant_id, workspace_id, record_id, entity_type, transition_id,
            transition_version, actor_id, source_state, target_state, reason_code,
            trace_id, policy_version
          ) VALUES (
            v_tenant, v_workspace, p_record_id, p_entity_type, p_transition_id,
            v_version + 1, v_actor, v_source_state, p_target_state, p_reason_code,
            p_trace_id, p_policy_version
          );
          INSERT INTO public.canon_transition_attempts (
            tenant_id, workspace_id, entity_type, record_id, attempt_id,
            expected_version, current_version, source_state, target_state,
            actor_id, reason_code, safe_error_code, trace_id, policy_version,
            outcome, result_state, result_version
          ) VALUES (
            v_tenant, v_workspace, p_entity_type, p_record_id, p_transition_id,
            p_expected_version, v_version, v_source_state, p_target_state,
            v_actor, p_reason_code, NULL, p_trace_id, p_policy_version,
            'succeeded', p_target_state, v_version + 1
          );
          INSERT INTO public.audit_events (
            event_id, tenant_id, workspace_id, actor_id, action, target_type,
            target_id, outcome, trace_id, policy_version, before_value, after_value, metadata
          ) VALUES (
            p_transition_id, v_tenant, v_workspace, v_actor, 'canon.transition',
            p_entity_type, p_record_id, 'succeeded', p_trace_id, p_policy_version,
            jsonb_build_object('state', v_source_state, 'version', v_version),
            jsonb_build_object('state', p_target_state, 'version', v_version + 1),
            jsonb_build_object('attempt_id', p_transition_id, 'reason_code', p_reason_code)
          );
          RETURN QUERY SELECT p_target_state, v_version + 1, 'succeeded'::text, NULL::text;
        END $$;
        REVOKE ALL ON FUNCTION transition_canon_state(text,text,integer,text,text,text,text,text) FROM PUBLIC;
        GRANT EXECUTE ON FUNCTION transition_canon_state(text,text,integer,text,text,text,text,text) TO daon_app;
    """)

    scoped_tables = (*ENTITY_TABLES, "canon_state_transitions", "canon_transition_attempts")
    predicate = (
        "tenant_id = nullif(current_setting('app.tenant_id', true), '') "
        "AND workspace_id = nullif(current_setting('app.workspace_id', true), '')"
    )
    for table in scoped_tables:
        op.execute(f"ALTER TABLE {table} ENABLE ROW LEVEL SECURITY")
        op.execute(f"ALTER TABLE {table} FORCE ROW LEVEL SECURITY")
        op.execute(
            f"CREATE POLICY {table}_scope ON {table} USING ({predicate}) WITH CHECK ({predicate})"
        )
        op.execute(f"GRANT SELECT, INSERT ON {table} TO daon_app")
        op.execute(f"REVOKE UPDATE, DELETE ON {table} FROM daon_app")
    op.execute("GRANT SELECT ON canon_transition_rules TO daon_app")


def downgrade() -> None:
    op.execute("DROP FUNCTION IF EXISTS transition_canon_state(text,text,integer,text,text,text,text,text)")
    op.execute("DROP FUNCTION IF EXISTS guard_canon_state_mutation() CASCADE")
    op.execute("DROP FUNCTION IF EXISTS reject_canon_immutable_mutation() CASCADE")
    op.execute("DROP FUNCTION IF EXISTS validate_canon_insert() CASCADE")
    op.execute("DROP TABLE IF EXISTS canon_state_transitions CASCADE")
    op.execute("DROP TABLE IF EXISTS canon_transition_attempts CASCADE")
    op.execute("DROP TABLE IF EXISTS canon_transition_rules CASCADE")
    for table in reversed(ENTITY_TABLES):
        op.execute(f"DROP TABLE IF EXISTS {table} CASCADE")
