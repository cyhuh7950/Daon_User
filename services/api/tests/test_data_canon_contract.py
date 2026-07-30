from __future__ import annotations

import json
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[3]
MIGRATION = ROOT / "services" / "api" / "migrations" / "versions" / "0003_data_canon_lineage.py"
MANIFEST = ROOT / "docs" / "03_architecture" / "data_canon_manifest.json"

EXPECTED_ENTITIES = {
    "WorkspacePolicy", "StepUpAuthorization", "AccessDecision",
    "Source", "SourceVersion", "ProcessingRun", "UnderstandingResult",
    "ExtractionEvidence", "TranscriptionRun", "TranscriptVersion", "TranscriptSegment",
    "EvidenceSpan", "IndexVersion", "KnowledgeScope", "WeightProfile", "ScopeSnapshot",
    "ConflictRecord", "RuleSetReference", "RuleSetVersionSnapshot", "RuleSetBinding",
    "RuleEvaluation", "ProviderProfile", "RuntimeNode", "ModelArtifact",
    "ModelInstallation", "ModelDeployment", "RoleBinding", "RoutingPolicyVersion",
    "RoutingDecision", "ModelAttempt", "Conversation", "Message", "Run", "RunStep",
    "RunSnapshot", "RunResult", "Citation", "GenerationRequest",
    "GenerationSettingsSnapshot", "StudioOutput", "OutputVersion", "EvidenceReference",
    "ReviewRequest", "ApprovalRequest", "Approval", "Delivery", "KnowledgeRegistration",
    "Connector", "ExternalReference", "EgressDecision", "AuditEvent", "Notification",
}


class DataCanonManifestContractTests(unittest.TestCase):
    def test_manifest_maps_every_design_entity_to_constrained_schema(self) -> None:
        manifest = json.loads(MANIFEST.read_text(encoding="utf-8"))
        mappings = manifest["entity_mappings"]
        self.assertEqual(set(mappings), EXPECTED_ENTITIES)
        for entity, mapping in mappings.items():
            with self.subTest(entity=entity):
                self.assertRegex(mapping["table"], r"^[a-z][a-z0-9_]+$")
                self.assertIn("tenant_id", mapping["columns"])
                self.assertIn("record_id", mapping["columns"])
                self.assertTrue(mapping["constraints"])
                self.assertNotEqual(mapping["storage_model"], "unconstrained_json_or_eav")

    def test_migration_declares_rls_immutability_transition_and_lineage_contracts(self) -> None:
        source = MIGRATION.read_text(encoding="utf-8")
        for token in (
            'revision = "0003"',
            'down_revision = "0002"',
            "ENABLE ROW LEVEL SECURITY",
            "FORCE ROW LEVEL SECURITY",
            "CANON_IMMUTABLE_MUTATION",
            "CANON_DIGEST_MISMATCH",
            "CANON_PREVIOUS_VERSION_INVALID",
            "CANON_TRANSITION_INVALID",
            "CANON_VERSION_CONFLICT",
            "canon_state_transitions",
            "transition_canon_state",
            "source_versions",
            "run_snapshots",
            "model_attempts",
            "output_versions",
            "knowledge_registrations",
        ):
            self.assertIn(token, source)


if __name__ == "__main__":
    unittest.main()
