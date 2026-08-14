"""Exact Offline Studio Output Bundle parser and Cloud Canon importer."""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Callable, Mapping, cast

from psycopg.types.json import Jsonb

from .cloud_storage import CloudAccessContext, CloudDatabaseError, PostgresCloudStore
from .data_canon import canonical_json_bytes
from .object_queue import ObjectQueueCoordinator, ObjectQueueError
from .sync import SyncContext, SyncError, SyncItemInput, SyncItemKind, TargetVersion


_MAX_BUNDLE_BYTES = 8 * 1024 * 1024
_TOP_LEVEL_KEYS = frozenset({
    "schema_version", "local_workspace_id", "knowledge_context_snapshot",
    "model_selection_snapshot", "generation_settings_snapshot", "run_snapshot",
    "studio_output", "output_version", "source_dependencies",
})
_SIGNED_KEYS = (
    "knowledge_context_snapshot", "model_selection_snapshot",
    "generation_settings_snapshot", "run_snapshot", "studio_output", "output_version",
)


@dataclass(frozen=True, slots=True)
class OfflineStudioOutputBundle:
    schema_version: int
    local_workspace_id: str
    knowledge_context_snapshot: dict[str, object]
    model_selection_snapshot: dict[str, object]
    generation_settings_snapshot: dict[str, object]
    run_snapshot: dict[str, object]
    studio_output: dict[str, object]
    output_version: dict[str, object]
    source_dependencies: tuple[dict[str, str], ...]


def _validate_signed(value: object) -> dict[str, object]:
    if not isinstance(value, dict) or not isinstance(value.get("digest"), str):
        raise SyncError("SYNC_OUTPUT_BUNDLE_INVALID", 400)
    payload = {key: item for key, item in value.items() if key != "digest"}
    if hashlib.sha256(canonical_json_bytes(payload)).hexdigest() != value["digest"]:
        raise SyncError("SYNC_OUTPUT_BUNDLE_INVALID", 400)
    return dict(value)


def parse_offline_studio_output_bundle(
    content: bytes, expected_digest: str,
) -> OfflineStudioOutputBundle:
    if (
        not isinstance(content, bytes)
        or not 0 < len(content) <= _MAX_BUNDLE_BYTES
        or hashlib.sha256(content).hexdigest() != expected_digest
    ):
        raise SyncError("SYNC_CONTENT_DIGEST_MISMATCH", 400)
    try:
        document = json.loads(content)
    except (UnicodeDecodeError, json.JSONDecodeError):
        raise SyncError("SYNC_OUTPUT_BUNDLE_INVALID", 400) from None
    if (
        not isinstance(document, dict)
        or frozenset(document) != _TOP_LEVEL_KEYS
        or canonical_json_bytes(document) != content
        or document.get("schema_version") != 1
        or not isinstance(document.get("local_workspace_id"), str)
    ):
        raise SyncError("SYNC_OUTPUT_BUNDLE_INVALID", 400)
    signed = {key: _validate_signed(document[key]) for key in _SIGNED_KEYS}
    model = signed["model_selection_snapshot"]
    run = signed["run_snapshot"]
    context = signed["knowledge_context_snapshot"]
    if (
        model.get("provider_kind") != "local_runtime"
        or run.get("workspace_id") != document["local_workspace_id"]
        or run.get("egress") != "none"
        or context.get("mode") not in {"daon_priority", "mixed", "raw_only"}
        or not isinstance(context.get("items"), list)
    ):
        raise SyncError("SYNC_OUTPUT_BUNDLE_INVALID", 400)
    items = cast(list[object], context["items"])
    if any(
        not isinstance(item, dict)
        or item.get("origin") not in {"daon_knowledge", "raw_source"}
        or not isinstance(item.get("version_id"), str)
        or not isinstance(item.get("digest"), str)
        for item in items
    ):
        raise SyncError("SYNC_OUTPUT_BUNDLE_INVALID", 400)
    dependencies = document.get("source_dependencies")
    if not isinstance(dependencies, list) or any(
        not isinstance(item, dict)
        or set(item) != {"item_id", "source_version_id", "digest"}
        or not all(isinstance(value, str) for value in item.values())
        for item in dependencies
    ):
        raise SyncError("SYNC_OUTPUT_BUNDLE_INVALID", 400)
    raw_versions = {
        str(item["version_id"]) for item in items
        if isinstance(item, dict) and item.get("origin") == "raw_source"
    }
    dependency_versions = {str(item["source_version_id"]) for item in dependencies}
    output = signed["output_version"]
    sections = output.get("sections")
    if raw_versions != dependency_versions or not isinstance(sections, list) or any(
        not isinstance(section, dict)
        or (raw_versions and section.get("unverified") is not True)
        for section in sections
    ):
        raise SyncError("SYNC_OUTPUT_BUNDLE_INVALID", 400)
    return OfflineStudioOutputBundle(
        schema_version=1,
        local_workspace_id=str(document["local_workspace_id"]),
        knowledge_context_snapshot=signed["knowledge_context_snapshot"],
        model_selection_snapshot=signed["model_selection_snapshot"],
        generation_settings_snapshot=signed["generation_settings_snapshot"],
        run_snapshot=signed["run_snapshot"],
        studio_output=signed["studio_output"],
        output_version=signed["output_version"],
        source_dependencies=tuple(cast(dict[str, str], item) for item in dependencies),
    )


class PostgresOfflineStudioImportService:
    def __init__(
        self,
        cloud_store: PostgresCloudStore,
        coordinator: ObjectQueueCoordinator,
        *,
        clock: Callable[[], datetime] = lambda: datetime.now(timezone.utc),
    ) -> None:
        self._cloud_store = cloud_store
        self._coordinator = coordinator
        self._clock = clock

    @staticmethod
    def _id(prefix: str, *parts: str) -> str:
        return f"{prefix}-{hashlib.sha256('|'.join(parts).encode()).hexdigest()[:32]}"

    @staticmethod
    def _insert(
        connection: object, context: SyncContext, table: str, record_id: str,
        payload: Mapping[str, object], *, state: str | None = None,
        extra_columns: tuple[str, ...] = (), extra_values: tuple[object, ...] = (),
    ) -> None:
        text = canonical_json_bytes(payload).decode()
        columns = (
            "tenant_id", "workspace_id", "record_id", "aggregate_id", "version",
            "schema_version", "canonical_json", "canonical_text", "digest_sha256",
            *(("state",) if state else ()), "created_by", "trace_id", *extra_columns,
        )
        values = (
            context.tenant_id, context.workspace_id, record_id, record_id, 1, 1,
            Jsonb(dict(payload)), text, hashlib.sha256(text.encode()).hexdigest(),
            *((state,) if state else ()), context.actor_id, context.trace_id, *extra_values,
        )
        connection.execute(
            f"INSERT INTO {table} ({','.join(columns)}) VALUES ({','.join(('%s',) * len(columns))})",
            values,
        )

    def import_bundle(
        self, context: SyncContext, item: SyncItemInput, content: bytes,
        idempotency_key: str, *, relation: str,
    ) -> TargetVersion:
        if item.item_kind is not SyncItemKind.OUTPUT_VERSION:
            raise SyncError("SYNC_ITEM_INVALID", 400)
        bundle = parse_offline_studio_output_bundle(content, item.digest_sha256)
        dependency_ids = tuple(entry["item_id"] for entry in bundle.source_dependencies)
        if dependency_ids != item.dependency_item_ids:
            raise SyncError("SYNC_DEPENDENCY_REQUIRED", 409)
        scope = (context.tenant_id, context.workspace_id, item.item_id, item.digest_sha256)
        settings_id = self._id("settings", *scope)
        request_id = self._id("generation", *scope)
        output_id = self._id("output", *scope)
        version_id = self._id("output-version", *scope)
        operation = "offline_studio.output.import"
        fingerprint = hashlib.sha256(canonical_json_bytes({
            "item_id": item.item_id, "digest": item.digest_sha256,
            "dependencies": list(item.dependency_item_ids), "relation": relation,
        })).hexdigest()
        try:
            submitted = self._coordinator.submit(
                CloudAccessContext(context.tenant_id, context.workspace_id, context.actor_id, "object.write"),
                area="output", content=content, content_type=item.content_type,
                idempotency_key=idempotency_key, trace_id=context.trace_id,
            )
        except ObjectQueueError as error:
            raise SyncError(error.code, 503 if error.retryable else 409, retryable=error.retryable) from None
        cloud = CloudAccessContext(
            context.tenant_id, context.workspace_id, context.actor_id, "studio.create"
        )
        try:
            with self._cloud_store._transaction(cloud) as connection:
                replay = connection.execute(
                    "SELECT request_fingerprint,result FROM idempotency_records "
                    "WHERE tenant_id=%s AND workspace_id=%s AND actor_id=%s "
                    "AND operation=%s AND idempotency_key=%s",
                    (context.tenant_id, context.workspace_id, context.actor_id, operation, idempotency_key),
                ).fetchone()
                if replay is not None:
                    if str(replay[0]) != fingerprint:
                        raise SyncError("IDEMPOTENCY_KEY_REUSED")
                    result = cast(Mapping[str, object], replay[1])
                    return TargetVersion(
                        str(result["target_version_id"]), str(result["object_id"]),
                        item.item_id, item.digest_sha256, item.base_cloud_version_id, relation,
                    )
                completed = connection.execute(
                    "SELECT item_id FROM sync_target_versions WHERE item_kind='source_version' "
                    "AND item_id=ANY(%s)", (list(item.dependency_item_ids),),
                ).fetchall()
                if {str(row[0]) for row in completed} != set(item.dependency_item_ids):
                    raise SyncError("SYNC_DEPENDENCY_REQUIRED", 409)
                lineage = {
                    "local_workspace_id": bundle.local_workspace_id,
                    "local_output_version_id": bundle.output_version.get("output_version_id"),
                    "bundle_digest": item.digest_sha256,
                }
                settings_payload = {**bundle.generation_settings_snapshot, "offline_import_lineage": lineage}
                request_payload = {"offline_import_lineage": lineage, "run_snapshot": bundle.run_snapshot}
                output_payload = {**bundle.studio_output, "offline_import_lineage": lineage}
                version_payload = {
                    **bundle.output_version,
                    "offline_import_lineage": lineage,
                    "knowledge_context_snapshot": bundle.knowledge_context_snapshot,
                    "model_selection_snapshot": bundle.model_selection_snapshot,
                }
                self._insert(connection, context, "generation_settings_snapshots", settings_id, settings_payload)
                self._insert(connection, context, "generation_requests", request_id, request_payload,
                             state="configuring", extra_columns=("generation_settings_snapshot_id",),
                             extra_values=(settings_id,))
                for target_state in ("confirmed", "submitted"):
                    transition_id = self._id("transition", *scope, target_state)
                    row = connection.execute(
                        "SELECT state,version,outcome FROM transition_canon_state(%s,%s,%s,%s,%s,%s,%s,%s)",
                        ("GenerationRequest", request_id, 1 if target_state == "confirmed" else 2,
                         target_state, transition_id, "OFFLINE_IMPORT", context.trace_id,
                         context.policy_version),
                    ).fetchone()
                    if row is None or str(row[2]) != "succeeded":
                        raise SyncError("SYNC_OUTPUT_BUNDLE_INVALID", 409)
                self._insert(connection, context, "studio_outputs", output_id, output_payload,
                             extra_columns=("generation_request_id",), extra_values=(request_id,))
                self._insert(connection, context, "output_versions", version_id, version_payload,
                             state="generating",
                             extra_columns=("studio_output_id", "generation_settings_snapshot_id"),
                             extra_values=(output_id, settings_id))
                transition = connection.execute(
                    "SELECT state,version,outcome FROM transition_canon_state(%s,%s,%s,%s,%s,%s,%s,%s)",
                    ("OutputVersion", version_id, 1, "draft", self._id("transition", *scope, "draft"),
                     "OFFLINE_IMPORT", context.trace_id, context.policy_version),
                ).fetchone()
                if transition is None or str(transition[2]) != "succeeded":
                    raise SyncError("SYNC_OUTPUT_BUNDLE_INVALID", 409)
                result = {"target_version_id": version_id, "object_id": submitted.object_id}
                connection.execute(
                    "INSERT INTO idempotency_records (tenant_id,workspace_id,actor_id,operation,"
                    "idempotency_key,request_fingerprint,result,status,expires_at) "
                    "VALUES (%s,%s,%s,%s,%s,%s,%s,'completed',%s)",
                    (context.tenant_id, context.workspace_id, context.actor_id, operation,
                     idempotency_key, fingerprint, json.dumps(result),
                     self._clock() + timedelta(hours=24)),
                )
                return TargetVersion(
                    version_id, submitted.object_id, item.item_id, item.digest_sha256,
                    item.base_cloud_version_id, relation,
                )
        except SyncError:
            raise
        except CloudDatabaseError as error:
            raise SyncError(error.code, 503, retryable=error.retryable) from None
