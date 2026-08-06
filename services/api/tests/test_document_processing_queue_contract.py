from __future__ import annotations

import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[3]


class DocumentProcessingQueueContractTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.migration = (
            ROOT / "services/api/migrations/versions/0008_document_processing_queue.py"
        ).read_text("utf-8")

    def test_document_parser_role_is_added_without_changing_provider_secrets(self) -> None:
        self.assertIn("'document_parser'", self.migration)
        self.assertIn("provider_setting_deployments_roles_check", self.migration)
        self.assertIn("provider_setting_role_bindings_role_check", self.migration)
        self.assertNotIn("api_key", self.migration.casefold())
        self.assertNotIn("credential", self.migration.casefold())

    def test_queue_has_rls_leases_immutable_attempts_and_canonical_foreign_keys(self) -> None:
        for token in (
            "CREATE TABLE document_processing_jobs",
            "CREATE TABLE document_processing_job_attempts",
            "FORCE ROW LEVEL SECURITY",
            "FOR UPDATE SKIP LOCKED",
            "SECURITY DEFINER",
            "REVOKE ALL ON FUNCTION claim_document_processing_job",
            "document_processing_job_attempts_immutable",
            "REFERENCES source_versions",
            "REFERENCES processing_runs",
        ):
            self.assertIn(token, self.migration)

    def test_claim_contract_is_bounded_and_does_not_return_content_or_secrets(self) -> None:
        self.assertIn("requested_lease_seconds NOT BETWEEN 10 AND 600", self.migration)
        signature = self.migration.split(") RETURNS TABLE (", 1)[1].split(") LANGUAGE", 1)[0]
        for forbidden in ("content", "api_key", "credential", "object_key"):
            self.assertNotIn(forbidden, signature.casefold())

    def test_expired_worker_lease_is_reclaimable_and_audited(self) -> None:
        recovery = (
            ROOT / "services/api/migrations/versions/0011_document_processing_claim_conflict.py"
        ).read_text("utf-8")

        self.assertIn("job.state = 'leased' AND job.lease_until <= clock_timestamp()", recovery)
        self.assertIn("'lease_lost'", recovery)
        self.assertIn(
            "ON CONFLICT ON CONSTRAINT document_processing_job_attempts_pkey DO NOTHING",
            recovery,
        )
        self.assertNotIn("ON CONFLICT (tenant_id,workspace_id,job_id,attempt_number)", recovery)
        for column in ("tenant_id", "workspace_id", "job_id", "attempt", "lease_owner"):
            self.assertIn(f"candidate.{column}", recovery)


if __name__ == "__main__":
    unittest.main()
