from __future__ import annotations

import unittest

from daon_user_api.source_ingest import SourceIngestor, SourceRejected


class SourceLifecycleContractTests(unittest.TestCase):
    def setUp(self) -> None:
        self.ingestor = SourceIngestor()

    def test_supported_file_source_is_not_pdf_only_and_is_notebook_scoped(self) -> None:
        record = self.ingestor.register_file(
            "brief.txt",
            "text/plain",
            b"source text",
            notebook_id="notebook-a",
        )

        self.assertEqual(record.notebook_id, "notebook-a")
        self.assertEqual(record.content_type, "text/plain")
        self.assertEqual(record.deletion_policy, "delete_with_notebook")
        self.assertTrue(record.usable)

    def test_retention_policy_is_explicit_and_invalid_values_are_rejected(self) -> None:
        retained = self.ingestor.register_file(
            "brief.pdf",
            "application/pdf",
            b"%PDF-1.7\nsource",
            notebook_id="notebook-a",
            deletion_policy="retain_after_notebook_delete",
        )
        self.assertEqual(retained.deletion_policy, "retain_after_notebook_delete")

        with self.assertRaisesRegex(SourceRejected, "INVALID_DELETION_POLICY"):
            self.ingestor.register_file(
                "brief.txt", "text/plain", b"source", deletion_policy="temporary"
            )

    def test_external_source_loss_is_visible_without_automatic_deletion(self) -> None:
        record = self.ingestor.register_file(
            "brief.md", "text/markdown", b"source", notebook_id="notebook-a"
        )
        unavailable = self.ingestor.unavailable(record)

        self.assertEqual(unavailable.source_id, record.source_id)
        self.assertEqual(unavailable.digest_sha256, record.digest_sha256)
        self.assertEqual(unavailable.status, "unavailable")
        self.assertFalse(unavailable.usable)
        self.assertEqual(unavailable.notebook_id, "notebook-a")


if __name__ == "__main__":
    unittest.main()
