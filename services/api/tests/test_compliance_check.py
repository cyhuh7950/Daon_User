import unittest

from daon_user_api.compliance_check import ComplianceChecker, ComplianceCheckError


class ComplianceCheckTests(unittest.TestCase):
    def test_preserves_judgement_evidence_ruleset_and_lineage(self):
        result = ComplianceChecker().check(
            items=[{"item_id": "C-1", "judgement": "compliant", "evidence": "cell:B4", "action": "none"}],
            ruleset_id="rs-1", ruleset_version="7", request_id="req-1", model_id="model-1",
        )
        self.assertEqual(result.items[0]["judgement"], "compliant")
        self.assertEqual(result.items[0]["evidence"], "cell:B4")
        self.assertEqual(result.lineage["ruleset_version"], "7")

    def test_missing_evidence_downgrades_compliant_to_review(self):
        result = ComplianceChecker().check(
            items=[{"item_id": "C-2", "judgement": "compliant", "evidence": "", "action": "collect"}],
            ruleset_id="rs-1", ruleset_version="7", request_id="req-2", model_id="model-2",
        )
        self.assertEqual(result.items[0]["judgement"], "needs_review")
        self.assertIn("missing_evidence", result.warnings)

    def test_rejects_unknown_judgement(self):
        with self.assertRaisesRegex(ComplianceCheckError, "JUDGEMENT_UNSUPPORTED"):
            ComplianceChecker().check(
                items=[{"item_id": "C-3", "judgement": "unknown", "evidence": "x", "action": "review"}],
                ruleset_id="rs-1", ruleset_version="7", request_id="req-3", model_id="model-3",
            )
