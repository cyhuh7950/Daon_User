from __future__ import annotations

import unittest
from types import SimpleNamespace
from unittest.mock import Mock

from daon_user_api.source_upload import PostgresSourceUploadService


class PostgresSourceUploadServiceTests(unittest.TestCase):
    def test_registration_uses_deterministic_ids_and_reconciles_canon_after_object_completion(self) -> None:
        service = object.__new__(PostgresSourceUploadService)
        service._queue_store = Mock()  # type: ignore[attr-defined]
        service._canon_store = Mock()  # type: ignore[attr-defined]
        service._coordinator = Mock()  # type: ignore[attr-defined]
        service._worker = Mock()  # type: ignore[attr-defined]
        service._coordinator.submit.side_effect = (  # type: ignore[attr-defined]
            lambda *_args, **kwargs: SimpleNamespace(
                object_id=kwargs["object_id"], replayed=False
            )
        )
        service._queue_store.get_object.return_value = SimpleNamespace(status="completed")  # type: ignore[attr-defined]

        result = service.register_pdf(
            tenant_id="tenant-001",
            workspace_id="workspace-001",
            notebook_id="notebook-001",
            actor_id="user-001",
            filename="fixture.pdf",
            content=b"%PDF-1.7\nfixture",
            idempotency_key="upload-001",
            trace_id="trace-upload-001",
        )

        submitted_id = service._coordinator.submit.call_args.kwargs["object_id"]  # type: ignore[attr-defined]
        self.assertRegex(submitted_id, r"^[0-9a-f]{32}$")
        self.assertEqual(result.object_id, submitted_id)
        self.assertEqual(result.source_id, f"src-{submitted_id}")
        self.assertEqual(result.source_version_id, f"sv-{submitted_id}")
        self.assertEqual(result.status, "accepted")
        service._canon_store.register_uploaded_source.assert_called_once()  # type: ignore[attr-defined]
        self.assertEqual(
            service._canon_store.register_uploaded_source.call_args.kwargs["digest_sha256"],  # type: ignore[attr-defined]
            result.digest_sha256,
        )
        self.assertEqual(
            service._canon_store.register_uploaded_source.call_args.kwargs["notebook_id"],  # type: ignore[attr-defined]
            "notebook-001",
        )


if __name__ == "__main__":
    unittest.main()
