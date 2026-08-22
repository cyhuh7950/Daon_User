from __future__ import annotations

import hashlib
import unittest

from daon_user_api.data_canon import canonical_json_bytes
from daon_user_api.offline_studio_import import parse_offline_studio_output_bundle
from daon_user_api.sync import SyncError


def _signed(payload: dict[str, object]) -> dict[str, object]:
    return {
        **payload,
        "digest": hashlib.sha256(canonical_json_bytes(payload)).hexdigest(),
    }


def _bundle() -> bytes:
    document = {
        "schema_version": 1,
        "local_workspace_id": "workspace-local-1",
        "knowledge_context_snapshot": _signed({
            "snapshot_id": "scope-1", "mode": "mixed",
            "items": [
                {"origin": "daon_knowledge", "version_id": "knowledge-output-1", "digest": "a" * 64},
                {"origin": "raw_source", "version_id": "source-version-1", "digest": "b" * 64},
            ],
        }),
        "model_selection_snapshot": _signed({
            "provider_kind": "local_runtime", "deployment_id": "deployment-1",
            "artifact_digest": "c" * 64, "deployment_digest": "d" * 64,
        }),
        "generation_settings_snapshot": _signed({"snapshot_id": "settings-1", "temperature": 0.2}),
        "run_snapshot": _signed({"run_id": "run-1", "workspace_id": "workspace-local-1", "egress": "none"}),
        "studio_output": _signed({"output_id": "output-1", "title": "Draft"}),
        "output_version": _signed({
            "output_version_id": "output-version-1", "previous_version_id": None,
            "sections": [{"title": "Summary", "body": "Grounded", "unverified": True}],
        }),
        "source_dependencies": [{
            "item_id": "item-source-1", "source_version_id": "source-version-1",
            "digest": "b" * 64,
        }],
    }
    return canonical_json_bytes(document)


class OfflineStudioOutputBundleTests(unittest.TestCase):
    def test_exact_canonical_bundle_preserves_origin_model_and_dependencies(self) -> None:
        content = _bundle()
        bundle = parse_offline_studio_output_bundle(content, hashlib.sha256(content).hexdigest())
        self.assertEqual(bundle.local_workspace_id, "workspace-local-1")
        self.assertEqual(bundle.model_selection_snapshot["provider_kind"], "local_runtime")
        self.assertEqual(bundle.source_dependencies[0]["item_id"], "item-source-1")

    def test_digest_noncanonical_extra_key_and_false_verified_evidence_are_rejected(self) -> None:
        content = _bundle()
        with self.assertRaisesRegex(SyncError, "SYNC_CONTENT_DIGEST_MISMATCH"):
            parse_offline_studio_output_bundle(content, "0" * 64)
        with self.assertRaisesRegex(SyncError, "SYNC_OUTPUT_BUNDLE_INVALID"):
            parse_offline_studio_output_bundle(content + b"\n", hashlib.sha256(content + b"\n").hexdigest())
        tampered = content.replace(b'"unverified":true', b'"unverified":false')
        with self.assertRaisesRegex(SyncError, "SYNC_OUTPUT_BUNDLE_INVALID"):
            parse_offline_studio_output_bundle(tampered, hashlib.sha256(tampered).hexdigest())


if __name__ == "__main__":
    unittest.main()
