import unittest

from daon_user_api.document_draft import DocumentDraft


class DocumentDraftTests(unittest.TestCase):
    def test_preserves_template_sections_evidence_and_review_state(self):
        draft = DocumentDraft().create("tpl-1", [{"title": "요약", "body": "내용", "evidence": ["p1"]}], "req-1")
        self.assertEqual(draft.template_id, "tpl-1")
        self.assertEqual(draft.review_state, "draft")
        self.assertEqual(draft.sections[0]["evidence"], ["p1"])

    def test_marks_section_without_evidence_unverified(self):
        draft = DocumentDraft().create("tpl-1", [{"title": "결론", "body": "미확인", "evidence": []}], "req-2")
        self.assertIn("unverified", draft.warnings)

    def test_rejects_unknown_review_state(self):
        with self.assertRaisesRegex(ValueError, "REVIEW_STATE_UNSUPPORTED"):
            DocumentDraft().create("tpl-1", [], "req-3", review_state="closed")
