"""Canonical lineage, immutable snapshots and state-transition repository."""

from __future__ import annotations

import hashlib
import json
import re
import threading
from collections.abc import Iterator, Mapping
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import datetime
from typing import Any, cast

from psycopg import Connection, Error
from psycopg_pool import ConnectionPool, PoolTimeout

from .cloud_storage import CloudAccessContext


_SAFE_ID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:-]{0,255}$")
_DIGEST = re.compile(r"^[0-9a-f]{64}$")

_TRANSITIONS: dict[str, frozenset[tuple[str, str]]] = {
    "Source": frozenset({
        ("registered", "security_check"), ("security_check", "processing"),
        ("processing", "indexing"), ("indexing", "ready"),
        ("processing", "waiting_model"), ("processing", "partial_understanding"),
        ("processing", "needs_review"), ("processing", "failed"),
        ("waiting_model", "processing"), ("partial_understanding", "processing"),
        ("partial_understanding", "needs_review"), ("partial_understanding", "disabled"),
        ("needs_review", "processing"), ("ready", "expired"), ("ready", "disabled"),
        ("failed", "processing"), ("expired", "disabled"), ("disabled", "deleting"),
        ("deleting", "deleted"),
    }),
    "ProcessingRun": frozenset({
        ("accepted", "vision_llm_understanding"),
        ("accepted", "audio_llm_understanding"), ("accepted", "speech_to_text"),
        ("vision_llm_understanding", "parser_ocr_validation"),
        ("audio_llm_understanding", "transcript_timecode_validation"),
        ("speech_to_text", "llm_semantic_understanding"),
        ("llm_semantic_understanding", "transcript_timecode_validation"),
        ("parser_ocr_validation", "evidence_reconciliation"),
        ("transcript_timecode_validation", "evidence_reconciliation"),
        ("evidence_reconciliation", "completed"),
        ("accepted", "policy_blocked"), ("accepted", "failed"),
        ("vision_llm_understanding", "failed"), ("audio_llm_understanding", "failed"),
        ("speech_to_text", "failed"), ("llm_semantic_understanding", "failed"),
        ("parser_ocr_validation", "failed"),
        ("transcript_timecode_validation", "failed"),
        ("evidence_reconciliation", "failed"),
    }),
    "Run": frozenset({
        ("accepted", "planning"), ("planning", "retrieving"),
        ("retrieving", "generating"), ("generating", "validating"),
        ("validating", "completed"), ("accepted", "waiting_user"),
        ("planning", "waiting_user"), ("retrieving", "waiting_user"),
        ("generating", "waiting_user"), ("validating", "waiting_user"),
        ("accepted", "waiting_approval"), ("planning", "waiting_approval"),
        ("accepted", "policy_blocked"), ("planning", "policy_blocked"),
        ("retrieving", "policy_blocked"), ("generating", "policy_blocked"),
        ("validating", "policy_blocked"), ("waiting_user", "planning"),
        ("waiting_approval", "planning"),
        *((state, "failed") for state in ("accepted", "planning", "retrieving", "generating", "validating")),
        *((state, "cancelled") for state in ("accepted", "planning", "retrieving", "generating", "validating", "waiting_user", "waiting_approval")),
    }),
    "GenerationRequest": frozenset({
        ("configuring", "confirmed"), ("confirmed", "configuring"),
        ("confirmed", "submitted"),
    }),
    "OutputVersion": frozenset({
        ("generating", "draft"), ("draft", "review_requested"),
        ("review_requested", "in_review"), ("in_review", "revision_requested"),
        ("in_review", "approved"), ("approved", "delivered"),
    }),
    "ApprovalRequest": frozenset({
        ("pending", "approved"), ("pending", "rejected"),
        ("pending", "expired"), ("pending", "withdrawn"),
    }),
    "KnowledgeRegistration": frozenset({
        ("requested", "registered"), ("requested", "rejected"),
    }),
}


class CanonError(RuntimeError):
    """Stable fail-closed canonical-data error."""

    def __init__(self, code: str) -> None:
        self.code = code
        super().__init__(code)


def canonical_json_bytes(payload: Mapping[str, object]) -> bytes:
    if not isinstance(payload, Mapping):
        raise CanonError("CANON_SNAPSHOT_INVALID")
    try:
        encoded = json.dumps(
            dict(payload), sort_keys=True, separators=(",", ":"), ensure_ascii=False,
            allow_nan=False,
        ).encode("utf-8")
    except (TypeError, ValueError) as error:
        raise CanonError("CANON_SNAPSHOT_INVALID") from error
    return encoded


def transition_allowed(entity_type: str, source_state: str, target_state: str) -> bool:
    return (source_state, target_state) in _TRANSITIONS.get(entity_type, frozenset())


@dataclass(frozen=True, slots=True)
class CanonicalContext:
    tenant_id: str
    workspace_id: str
    actor_id: str
    capability: str
    trace_id: str

    def __post_init__(self) -> None:
        if not all(
            isinstance(value, str) and _SAFE_ID.fullmatch(value)
            for value in (
                self.tenant_id, self.workspace_id, self.actor_id,
                self.capability, self.trace_id,
            )
        ):
            raise CanonError("CANON_CONTEXT_INVALID")

    def cloud_context(self) -> CloudAccessContext:
        return CloudAccessContext(
            self.tenant_id, self.workspace_id, self.actor_id, self.capability
        )


@dataclass(frozen=True, slots=True)
class CanonicalSnapshot:
    record_id: str
    aggregate_id: str
    version: int
    schema_version: int
    digest_sha256: str
    previous_version_id: str | None
    payload: Mapping[str, object]

    def __post_init__(self) -> None:
        if not all(_SAFE_ID.fullmatch(value) for value in (self.record_id, self.aggregate_id)):
            raise CanonError("CANON_SNAPSHOT_INVALID")
        if self.version < 1 or self.schema_version < 1:
            raise CanonError("CANON_SNAPSHOT_INVALID")
        if (self.version == 1) != (self.previous_version_id is None):
            raise CanonError("CANON_PREVIOUS_VERSION_INVALID")
        if self.previous_version_id is not None and not _SAFE_ID.fullmatch(self.previous_version_id):
            raise CanonError("CANON_PREVIOUS_VERSION_INVALID")
        if not isinstance(self.payload, Mapping):
            raise CanonError("CANON_SNAPSHOT_INVALID")
        actual = hashlib.sha256(canonical_json_bytes(self.payload)).hexdigest()
        if not _DIGEST.fullmatch(self.digest_sha256) or actual != self.digest_sha256:
            raise CanonError("CANON_DIGEST_MISMATCH")


@dataclass(frozen=True, slots=True)
class CanonicalState:
    state: str
    version: int


def _database_error(error: Error) -> CanonError:
    primary = getattr(error.diag, "message_primary", "") or ""
    for code in (
        "CANON_IMMUTABLE_MUTATION", "CANON_DIGEST_MISMATCH",
        "CANON_PREVIOUS_VERSION_INVALID", "CANON_SNAPSHOT_INVALID",
        "CANON_STATE_INITIAL_INVALID",
        "CANON_TRANSITION_INVALID", "CANON_VERSION_CONFLICT",
        "CANON_SCOPE_DENIED", "CANON_RECORD_NOT_FOUND",
    ):
        if code in primary:
            return CanonError(code)
    if error.sqlstate in {"40001", "40P01"}:
        return CanonError("CANON_VERSION_CONFLICT")
    if error.sqlstate is not None and error.sqlstate.startswith("23"):
        return CanonError("CANON_RELATION_INVALID")
    if error.sqlstate == "42501":
        return CanonError("CANON_SCOPE_DENIED")
    return CanonError("CANON_DATABASE_UNAVAILABLE")


class PostgresDataCanonStore:
    """Repository port for canonical records; database constraints remain authoritative."""

    def __init__(self, dsn: str, *, min_size: int = 1, max_size: int = 4) -> None:
        if not isinstance(dsn, str) or not dsn:
            raise CanonError("CANON_DATABASE_DSN_REQUIRED")
        self._open_lock = threading.Lock()
        self._pool = ConnectionPool[tuple[Any, ...]](
            conninfo=dsn, min_size=min_size, max_size=max_size,
            kwargs={"autocommit": False}, timeout=2.0, reconnect_timeout=5.0, open=False,
        )

    def _ensure_open(self) -> None:
        if not self._pool.closed:
            return
        with self._open_lock:
            if self._pool.closed:
                self._pool.open(wait=False)

    def close(self) -> None:
        self._pool.close()

    @contextmanager
    def _transaction(self, context: CanonicalContext) -> Iterator[Connection[tuple[Any, ...]]]:
        try:
            self._ensure_open()
            with self._pool.connection(timeout=2.0) as connection:
                with connection.transaction():
                    for name, value in (
                        ("app.tenant_id", context.tenant_id),
                        ("app.workspace_id", context.workspace_id),
                        ("app.actor_id", context.actor_id),
                        ("app.capability", context.capability),
                        ("app.trace_id", context.trace_id),
                    ):
                        connection.execute("SELECT set_config(%s, %s, true)", (name, value))
                    yield connection
        except CanonError:
            raise
        except PoolTimeout:
            raise CanonError("CANON_DATABASE_UNAVAILABLE") from None
        except Error as error:
            raise _database_error(error) from None

    def create_source(self, context: CanonicalContext, source_id: str) -> None:
        if not _SAFE_ID.fullmatch(source_id):
            raise CanonError("CANON_SNAPSHOT_INVALID")
        with self._transaction(context) as connection:
            connection.execute(
                "INSERT INTO sources "
                "(tenant_id, workspace_id, record_id, aggregate_id, state, version, "
                "schema_version, canonical_json, digest_sha256, created_by, trace_id) "
                "VALUES (%s, %s, %s, %s, 'registered', 1, 1, '{}'::jsonb, "
                "encode(sha256(convert_to('{}'::jsonb::text, 'UTF8')), 'hex'), %s, %s)",
                (context.tenant_id, context.workspace_id, source_id, source_id,
                 context.actor_id, context.trace_id),
            )

    def append_source_version(
        self,
        context: CanonicalContext,
        *,
        source_version_id: str,
        source_id: str,
        version_number: int,
        previous_version_id: str | None,
        canonical_payload: Mapping[str, object],
        digest_sha256: str,
        created_at: datetime,
    ) -> None:
        snapshot = CanonicalSnapshot(
            source_version_id, source_id, version_number, 1,
            digest_sha256, previous_version_id, canonical_payload,
        )
        object_id = canonical_payload.get("object_id")
        if object_id is not None and not isinstance(object_id, str):
            raise CanonError("CANON_SNAPSHOT_INVALID")
        with self._transaction(context) as connection:
            connection.execute(
                "INSERT INTO source_versions "
                "(tenant_id, workspace_id, record_id, aggregate_id, version, schema_version, "
                "previous_version_id, canonical_json, canonical_text, digest_sha256, created_at, created_by, "
                "trace_id, source_id, object_id) "
                "VALUES (%s, %s, %s, %s, %s, %s, %s, %s::jsonb, %s, %s, %s, %s, %s, %s, %s)",
                (
                    context.tenant_id, context.workspace_id, snapshot.record_id,
                    snapshot.aggregate_id, snapshot.version, snapshot.schema_version,
                    snapshot.previous_version_id,
                    canonical_json_bytes(snapshot.payload).decode("utf-8"),
                    canonical_json_bytes(snapshot.payload).decode("utf-8"),
                    snapshot.digest_sha256, created_at, context.actor_id, context.trace_id,
                    source_id, object_id,
                ),
            )

    def transition(
        self,
        context: CanonicalContext,
        *,
        entity_type: str,
        record_id: str,
        expected_version: int,
        target_state: str,
        transition_id: str,
        reason_code: str,
        policy_version: str,
    ) -> CanonicalState:
        if not all(
            _SAFE_ID.fullmatch(value)
            for value in (entity_type, record_id, target_state, transition_id, reason_code, policy_version)
        ) or expected_version < 1:
            raise CanonError("CANON_TRANSITION_INVALID")
        with self._transaction(context) as connection:
            row = connection.execute(
                "SELECT state, version FROM transition_canon_state"
                "(%s, %s, %s, %s, %s, %s, %s, %s)",
                (
                    entity_type, record_id, expected_version, target_state,
                    transition_id, reason_code, context.trace_id, policy_version,
                ),
            ).fetchone()
        if row is None:
            raise CanonError("CANON_RECORD_NOT_FOUND")
        return CanonicalState(str(row[0]), int(row[1]))

    def count(self, context: CanonicalContext, table: str, record_id: str) -> int:
        allowed = {"sources", "source_versions", "canon_state_transitions"}
        if table not in allowed:
            raise CanonError("CANON_SNAPSHOT_INVALID")
        with self._transaction(context) as connection:
            row = connection.execute(
                f"SELECT count(*) FROM {table} WHERE record_id = %s", (record_id,)
            ).fetchone()
        return int(cast(tuple[object, ...], row)[0]) if row is not None else 0
