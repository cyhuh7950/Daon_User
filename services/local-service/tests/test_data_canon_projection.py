from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Mapping

import pytest

from daon_user_local_service.local_storage import LocalEncryptedStore, LocalStorageError


WORKSPACE_A = "11111111-1111-4111-8111-111111111111"
WORKSPACE_B = "22222222-2222-4222-8222-222222222222"
MASTER_KEY = bytes(range(32))


def _digest(payload: Mapping[str, object]) -> str:
    encoded = json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode()
    return hashlib.sha256(encoded).hexdigest()


def test_local_projection_is_immutable_restartable_and_scope_isolated(tmp_path: Path) -> None:
    root = tmp_path / "canon-local"
    payload = {"title": "offline source", "source_version_id": "source-version-local-1"}
    with LocalEncryptedStore.open(root, MASTER_KEY) as store:
        store.put_canonical_envelope(
            WORKSPACE_A, "source", entity_type="SourceVersion",
            entity_id="source-version-local-1", aggregate_id="source-local-1",
            version=1, schema_version=1, digest_sha256=_digest(payload),
            created_at="2026-07-30T00:00:00Z", previous_version_id=None, payload=payload,
        )
        envelope = store.get_canonical_envelope(
            WORKSPACE_A, "source", "SourceVersion", "source-version-local-1"
        )
        assert envelope.payload == payload
        assert envelope.data_area == "local_private"
        with pytest.raises(LocalStorageError, match="LOCAL_CANON_NOT_FOUND"):
            store.get_canonical_envelope(
                WORKSPACE_B, "source", "SourceVersion", "source-version-local-1"
            )
        with pytest.raises(LocalStorageError, match="LOCAL_CANON_NOT_FOUND"):
            store.get_canonical_envelope(
                WORKSPACE_A, "artifact", "SourceVersion", "source-version-local-1"
            )
        with pytest.raises(Exception, match="LOCAL_CANON_IMMUTABLE"):
            store.execute_canonical_mutation_for_test(
                "UPDATE canonical_envelopes SET version = 2 WHERE entity_id = ?",
                ("source-version-local-1",),
            )
        with pytest.raises(Exception, match="LOCAL_CANON_IMMUTABLE"):
            store.execute_canonical_mutation_for_test(
                "DELETE FROM canonical_envelopes WHERE entity_id = ?",
                ("source-version-local-1",),
            )

    with LocalEncryptedStore.open(root, MASTER_KEY) as reopened:
        envelope = reopened.get_canonical_envelope(
            WORKSPACE_A, "source", "SourceVersion", "source-version-local-1"
        )
        assert envelope.digest_sha256 == _digest(payload)
    raw = (root / "metadata.db").read_bytes()
    assert b"offline source" not in raw
    assert b"source-version-local-1" not in raw


def test_local_projection_rejects_bad_digest_previous_and_cloud_only_fields(tmp_path: Path) -> None:
    root = tmp_path / "canon-invalid"
    payload = {"title": "offline source"}
    with LocalEncryptedStore.open(root, MASTER_KEY) as store:
        with pytest.raises(LocalStorageError, match="LOCAL_CANON_DIGEST_MISMATCH"):
            store.put_canonical_envelope(
                WORKSPACE_A, "source", entity_type="SourceVersion", entity_id="source-version-bad",
                aggregate_id="source-bad", version=1, schema_version=1,
                digest_sha256="0" * 64, created_at="2026-07-30T00:00:00Z",
                previous_version_id=None, payload=payload,
            )
        with pytest.raises(LocalStorageError, match="LOCAL_CANON_PREVIOUS_VERSION_INVALID"):
            store.put_canonical_envelope(
                WORKSPACE_A, "source", entity_type="SourceVersion", entity_id="source-version-2",
                aggregate_id="source-local-1", version=2, schema_version=1,
                digest_sha256=_digest(payload), created_at="2026-07-30T00:00:01Z",
                previous_version_id="source-version-missing", payload=payload,
            )
        forbidden = {"provider_secret": "must-not-copy"}
        with pytest.raises(LocalStorageError, match="LOCAL_CANON_FIELD_FORBIDDEN"):
            store.put_canonical_envelope(
                WORKSPACE_A, "source", entity_type="RunSnapshot", entity_id="run-snapshot-1",
                aggregate_id="run-1", version=1, schema_version=1,
                digest_sha256=_digest(forbidden), created_at="2026-07-30T00:00:02Z",
                previous_version_id=None, payload=forbidden,
            )
