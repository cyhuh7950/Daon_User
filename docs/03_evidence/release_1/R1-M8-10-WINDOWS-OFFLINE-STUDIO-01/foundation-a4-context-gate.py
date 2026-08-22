from __future__ import annotations

import hashlib
import json
import tempfile
from pathlib import Path

from daon_user_local_service.local_storage import LocalEncryptedStore
from daon_user_local_service.main import build_production_offline_studio
from daon_user_local_service.raw_source import RawSourceService


WORKSPACE_ID = "33333333-3333-4333-8333-333333333333"
KEY = bytes(range(32))


def canonical_digest(value: object) -> str:
    return hashlib.sha256(
        json.dumps(value, sort_keys=True, separators=(",", ":")).encode("utf-8")
    ).hexdigest()


def run_gate(root: Path) -> dict[str, object]:
    knowledge_text = "Daon3 approved knowledge evidence."
    package = json.dumps(
        {
            "knowledge": [{"citation_id": "citation-a4", "text": knowledge_text}],
            "schema_version": 1,
        },
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    manifest = {
        "workspace_id": WORKSPACE_ID,
        "copy_id": "copy-a4",
        "package_id": "package-a4",
        "producer_product": "daon3",
        "producer_version": "3.0.0",
        "knowledge_registration_id": "registration-a4",
        "output_version_id": "output-version-a4",
        "authority": "approved",
        "registration_state": "registered",
        "review_state": "approved",
        "effective_at": "2026-08-13T00:00:00Z",
        "expires_at": "2033-08-13T00:00:00Z",
        "schema_version": 1,
        "content_digest_sha256": hashlib.sha256(package).hexdigest(),
    }
    raw_content = "Explicit raw evidence for A4 mixed context.".encode("utf-8")

    store = LocalEncryptedStore.open(root, KEY)
    store.import_knowledge_copy(
        manifest=manifest,
        manifest_digest_sha256=canonical_digest(manifest),
        canonical_package=package,
        idempotency_key="a4-knowledge-import-0001",
    )
    raw = RawSourceService(store).import_source(
        workspace_id=WORKSPACE_ID,
        filename="a4-raw-source.txt",
        content_type="text/plain",
        content=raw_content,
        content_digest_sha256=hashlib.sha256(raw_content).hexdigest(),
        idempotency_key="a4-raw-import-0001",
    )
    service = build_production_offline_studio(store, environment={})
    snapshot = service.prepare_context(
        workspace_id=WORKSPACE_ID,
        mode="mixed",
        daon_knowledge_ids=("copy-a4",),
        raw_source_version_ids=(raw.source_version_id,),
        idempotency_key="a4-context-prepare-0001",
    )
    store.close()

    persisted = b"".join(path.read_bytes() for path in root.rglob("*") if path.is_file())
    plaintext_absent = knowledge_text.encode() not in persisted and raw_content not in persisted

    reopened = LocalEncryptedStore.open(root, KEY)
    replay_service = build_production_offline_studio(reopened, environment={})
    replay = replay_service.prepare_context(
        workspace_id=WORKSPACE_ID,
        mode="mixed",
        daon_knowledge_ids=("copy-a4",),
        raw_source_version_ids=(raw.source_version_id,),
        idempotency_key="a4-context-prepare-0001",
    )
    reopened.close()

    if snapshot.snapshot_id != replay.snapshot_id or snapshot.digest != replay.digest:
        raise AssertionError("A4_CONTEXT_RESTART_REPLAY_FAILED")
    if [item.origin for item in replay.items] != ["daon_knowledge", "raw_source"]:
        raise AssertionError("A4_CONTEXT_ORIGIN_FAILED")
    daon, raw_item = replay.items
    if (
        daon.registration_id != "registration-a4"
        or daon.producer_version != "3.0.0"
        or daon.review_state != "approved"
        or raw_item.source_id != raw.source_id
        or raw_item.index_version_id != raw.index_version_id
        or raw_item.evidence_span_ids != raw.evidence_span_ids
        or raw_item.review_state != "unverified"
        or raw_item.conflict_state != "none"
    ):
        raise AssertionError("A4_CONTEXT_LINEAGE_FAILED")
    if not plaintext_absent:
        raise AssertionError("A4_PLAINTEXT_AT_REST")

    return {
        "status": "PASS",
        "context_schema_version": replay.schema_version,
        "origins": [item.origin for item in replay.items],
        "daon_registration_state": daon.registration_state,
        "daon_review_state": daon.review_state,
        "raw_processing_state": raw_item.processing_state,
        "raw_review_state": raw_item.review_state,
        "raw_conflict_state": raw_item.conflict_state,
        "raw_evidence_span_count": len(raw_item.evidence_span_ids),
        "restart_replay_exact": True,
        "plaintext_at_rest_count": 0,
    }


def main() -> None:
    with tempfile.TemporaryDirectory(prefix="daon-a4-context-") as directory:
        print(json.dumps(run_gate(Path(directory)), sort_keys=True))


if __name__ == "__main__":
    main()
