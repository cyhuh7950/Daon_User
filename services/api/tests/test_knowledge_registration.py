import unittest

from daon_user_api.knowledge_registration import KnowledgeRegistration


class KnowledgeRegistrationTests(unittest.TestCase):
    def test_requires_explicit_step_up_and_preserves_lineage(self):
        result = KnowledgeRegistration().register("src-v1", "run-1", "model-1", "user-1", explicit=True, step_up=True)
        self.assertEqual(result.source_version, "src-v1")
        self.assertEqual(result.lineage["run_id"], "run-1")

    def test_rejects_missing_step_up(self):
        with self.assertRaisesRegex(ValueError, "STEP_UP_REQUIRED"):
            KnowledgeRegistration().register("src-v1", "run-1", "model-1", "user-1", explicit=True, step_up=False)

    def test_rejects_implicit_registration(self):
        with self.assertRaisesRegex(ValueError, "EXPLICIT_CONFIRMATION_REQUIRED"):
            KnowledgeRegistration().register("src-v1", "run-1", "model-1", "user-1", explicit=False, step_up=True)
