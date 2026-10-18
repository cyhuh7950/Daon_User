"""Durable PostgreSQL and MinIO adapters for R1-M5-07 recovery."""

from __future__ import annotations

import hashlib
import hmac
import json
import secrets
import threading
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
from typing import Any, Callable, Iterator, Mapping
from types import SimpleNamespace

from psycopg import Connection, Error
from psycopg.rows import dict_row
from psycopg.types.json import Jsonb
from psycopg_pool import ConnectionPool, PoolTimeout

from daon_user_api.cloud_storage import CloudAccessContext, CloudDatabaseError, PostgresCloudStore
from daon_user_api.authorization import Action
from daon_user_api.data_canon import canonical_json_bytes
from daon_user_api.document_index_postgres import _source_access_allowed
from daon_user_api.open_law_source_access import OpenLawAccessError, _current_auth_grant
from daon_user_api.notification_source_access import _special_source_access_allowed
from daon_user_api.object_queue import (
    MinioObjectStorageAdapter,
    ObjectKeyPolicy,
    ObjectStorageError,
)
from daon_user_api.recovery import (
    BackupObjectInput,
    BackupSelectionInput,
    BackupView,
    RecoveryContext,
    RecoveryError,
    RestoreDestination,
    RestorePreviewView,
    RestoreRequestView,
    _fingerprint,
    _safe,
    _resolved_backup_metadata,
)
from daon_user_api.recovery_capture import (
    CaptureScope, METADATA_COLUMNS, decode_manifest, encode_manifest, validate_metadata,
)
from daon_user_api.recovery_lineage import resolve_lineage_selection
from daon_user_api.recovery_lineage_capture import (
    LineageCaptureScope, decode_manifest as decode_lineage_manifest, encode_manifest as encode_lineage_manifest,
    _Object as LineageObjectInput,
)


UTC = timezone.utc


class PostgresBackupSelectionResolver:
    """현재 접근 가능한 원본 metadata만 읽는다. capture/ready 행은 만들지 않는다."""

    def __init__(self, cloud_store) -> None:
        self._store = cloud_store

    @staticmethod
    def _metadata(connection, context, selection):
        rows = connection.execute(
            "SELECT sv.record_id,sv.source_id,sv.object_id,sv.canonical_json,sv.digest_sha256,"
            "o.digest_sha256,o.byte_size FROM source_versions sv JOIN sources s ON "
            "s.tenant_id=sv.tenant_id AND s.workspace_id=sv.workspace_id AND s.record_id=sv.source_id "
            "JOIN object_records o ON o.tenant_id=sv.tenant_id AND o.workspace_id=sv.workspace_id "
            "AND o.object_id=sv.object_id JOIN notebook_bindings b ON b.tenant_id=sv.tenant_id "
            "AND b.workspace_id=sv.workspace_id AND b.binding_kind='source' AND b.record_id=sv.source_id "
            "AND b.version_id=sv.record_id JOIN notebooks n ON n.tenant_id=b.tenant_id "
            "AND n.workspace_id=b.workspace_id AND n.notebook_id=b.notebook_id "
            "WHERE sv.tenant_id=%s AND sv.workspace_id=%s AND b.notebook_id=%s "
            "AND sv.record_id=ANY(%s) AND o.status='completed' AND o.area='source' "
            "AND s.state NOT IN ('expired','disabled','deleting','deleted') "
            "AND NOT EXISTS(SELECT 1 FROM notebook_source_unbindings u WHERE u.tenant_id=b.tenant_id "
            "AND u.workspace_id=b.workspace_id AND u.notebook_id=b.notebook_id "
            "AND u.source_id=b.record_id AND u.source_version_id=b.version_id) "
            "AND NOT EXISTS(SELECT 1 FROM notebook_deletion_requests d WHERE d.tenant_id=n.tenant_id "
            "AND d.workspace_id=n.workspace_id AND d.notebook_id=n.notebook_id "
            "AND d.state IN ('accepted','deleting','completed')) "
            "AND NOT EXISTS(SELECT 1 FROM deletion_requests d WHERE d.tenant_id=s.tenant_id "
            "AND d.workspace_id=s.workspace_id AND d.source_id=s.record_id "
            "AND (d.source_active=false OR d.state='purged')) ORDER BY sv.record_id",
            (context.tenant_id, context.workspace_id, selection.notebook_id, list(selection.source_version_ids)),
        ).fetchall()
        revision = connection.execute("SELECT version_num FROM alembic_version").fetchone()
        # 같은 tenant/Workspace의 실제 원장 전체를 결정적으로 결속한다. client watermark는 사용하지 않는다.
        deletions = connection.execute(
            "SELECT request_id,source_id,state,version,source_active,purge_started,policy_version "
            "FROM deletion_requests WHERE tenant_id=%s AND workspace_id=%s ORDER BY request_id",
            (context.tenant_id, context.workspace_id),
        ).fetchall()
        holds = connection.execute(
            "SELECT hold_id,source_id,state,version,policy_version FROM legal_holds "
            "WHERE tenant_id=%s AND workspace_id=%s ORDER BY hold_id",
            (context.tenant_id, context.workspace_id),
        ).fetchall()
        if revision is None:
            raise RecoveryError("RESOURCE_UNAVAILABLE", 503)
        return rows, str(revision[0]), _fingerprint((deletions, holds))

    @staticmethod
    def _access(connection, context, selection) -> None:
        _current_auth_grant(context, connection, Action.POLICY_MANAGE)
        for version in selection.source_version_ids:
            if not _source_access_allowed(connection, context, version) or not _special_source_access_allowed(connection, context, version):
                raise RecoveryError("BACKUP_UNAVAILABLE", 404)

    @staticmethod
    def _resolve_rows(context, selection, snapshot, *, trigger="manual"):
        rows, revision, watermark = snapshot
        bindings = []
        for version_id, source_id, object_id, canonical, canonical_digest, digest, byte_size in rows:
            if (not isinstance(canonical, dict) or hashlib.sha256(canonical_json_bytes(canonical)).hexdigest() != canonical_digest
                    or canonical.get("object_id") != object_id or type(canonical.get("byte_size")) is not int
                    or canonical["byte_size"] != byte_size):
                raise RecoveryError("BACKUP_MANIFEST_INVALID", 409)
            expected = canonical.get("text_sha256") if canonical.get("origin_kind") in {"website", "mcp_open_law"} else canonical.get("content_digest_sha256")
            if digest != expected:
                raise RecoveryError("BACKUP_MANIFEST_INVALID", 409)
            bindings.append((version_id, source_id, BackupObjectInput(object_id, digest, byte_size), canonical_digest))
        return _resolved_backup_metadata(context, selection, revision, watermark, tuple(bindings), trigger=trigger)

    def _resolve(self, connection, context, selection, ready_revision, idempotency_key, *, trigger="manual"):
        scoped = SimpleNamespace(tenant_id=context.tenant_id, workspace_id=context.workspace_id,
            actor_id=context.actor_id, trace_id=context.trace_id, policy_version=context.policy_version,
            notebook_id=selection.notebook_id)
        self._access(connection, scoped, selection)
        snapshot = self._metadata(connection, context, selection)
        if snapshot[1] != ready_revision:
            raise RecoveryError("RESOURCE_UNAVAILABLE", 503)
        result = self._resolve_rows(context, selection, snapshot, trigger=trigger)
        # READ COMMITTED의 마지막 재조회로 도중 ACL/삭제/해제/metadata 변경도 거부한다.
        if self._metadata(connection, context, selection) != snapshot:
            raise RecoveryError("BACKUP_MANIFEST_INVALID", 409)
        self._access(connection, scoped, selection)
        if idempotency_key is not None:
            _safe(idempotency_key)
            previous = connection.execute(
                "SELECT request_fingerprint FROM backup_records WHERE tenant_id=%s AND workspace_id=%s "
                "AND actor_id=%s AND idempotency_key=%s",
                (context.tenant_id, context.workspace_id, context.actor_id, idempotency_key),
            ).fetchone()
            if previous is not None:
                result.require_replay(str(previous[0]))
        return result

    @contextmanager
    def _read(self, context):
        if not context.organization_admin:
            raise RecoveryError("CURRENT_ACCESS_DENIED", 403)
        ready = self._store.readiness()
        if ready.ready is not True or ready.schema_revision is None:
            raise RecoveryError("RESOURCE_UNAVAILABLE", 503)
        try:
            with self._store._transaction(CloudAccessContext(context.tenant_id, context.workspace_id, context.actor_id, "recovery")) as connection:
                connection.execute("SET LOCAL ROLE daon_app")
                yield connection, ready.schema_revision
        except RecoveryError:
            raise
        except OpenLawAccessError:
            raise RecoveryError("CURRENT_ACCESS_DENIED", 403) from None
        except (CloudDatabaseError, Error, PoolTimeout):
            raise RecoveryError("RESOURCE_UNAVAILABLE", 503) from None

    def resolve(self, context, selection, *, idempotency_key=None):
        with self._read(context) as (connection, revision):
            return self._resolve(connection, context, selection, revision, idempotency_key)

    def resolve_lineage(self, context, selection):
        with self._read(context) as (connection, _):
            return resolve_lineage_selection(context, selection, connection)

    def capture_metadata(self, context, resolved, *, trigger="manual"):
        """하나의 SELECT snapshot에서 허용 관계만 읽는다. 전체 DB/이전 Version 조회는 없다."""
        selection = resolved.selection
        with self._read(context) as (connection, revision):
            current = self._resolve(connection, context, selection, revision, None, trigger=trigger)
            resolved.require_replay(current.fingerprint)
            expressions, params = [], []
            for table, columns in METADATA_COLUMNS.items():
                pairs = ",".join("'%s',r.%s" % (column, column) for column in sorted(columns))
                where = "r.tenant_id=%s AND r.workspace_id=%s"
                values = [context.tenant_id, context.workspace_id]
                if table in {"notebooks", "notebook_metadata_versions", "notebook_bindings"}:
                    where += " AND r.notebook_id=%s"
                    values.append(selection.notebook_id)
                if table == "notebook_metadata_versions":
                    where += " AND r.is_current=true"
                    order = "r.version"
                elif table == "notebook_bindings":
                    where += " AND r.binding_kind='source' AND r.version_id=ANY(%s)"
                    values.append(list(selection.source_version_ids)); order = "r.version_id"
                elif table == "sources":
                    where += " AND r.record_id IN (SELECT sv.source_id FROM source_versions sv " \
                        "WHERE sv.tenant_id=r.tenant_id AND sv.workspace_id=r.workspace_id AND sv.record_id=ANY(%s))"
                    values.append(list(selection.source_version_ids)); order = "r.record_id"
                elif table == "source_versions":
                    where += " AND r.record_id=ANY(%s)"
                    values.append(list(selection.source_version_ids)); order = "r.record_id"
                elif table == "object_records":
                    where += " AND r.object_id=ANY(%s)"
                    values.append([item.object_id for item in resolved.objects]); order = "r.object_id"
                else:
                    order = "r.notebook_id"
                expressions.append("'%s',(SELECT jsonb_agg(jsonb_build_object(%s) ORDER BY %s) FROM %s r WHERE %s)"
                    % (table, pairs, order, table, where))
                params.extend(values)
            row = connection.execute("SELECT jsonb_build_object(" + ",".join(expressions) + ")", tuple(params)).fetchone()
            if row is None:
                raise RecoveryError("BACKUP_UNAVAILABLE", 404)
            metadata = row[0]
            scope = CaptureScope("metadata-validation", context.tenant_id, context.workspace_id, selection.notebook_id,
                selection.source_version_ids, resolved.fingerprint, resolved.schema_revision, context.policy_version, resolved.retention_watermark)
            validate_metadata(scope, metadata, resolved.objects)
            after = self._resolve(connection, context, selection, revision, None, trigger=trigger)
            resolved.require_replay(after.fingerprint)
            return metadata

    def resolve_legacy(self, context, *, schema_revision, retention_watermark, objects, idempotency_key=None, trigger="manual"):
        if not objects or len({item.object_id for item in objects}) != len(objects):
            raise RecoveryError("BACKUP_MANIFEST_INVALID", 400)
        with self._read(context) as (connection, revision):
            candidates = connection.execute(
                "SELECT b.notebook_id,sv.record_id,sv.object_id FROM source_versions sv JOIN notebook_bindings b "
                "ON b.tenant_id=sv.tenant_id AND b.workspace_id=sv.workspace_id AND b.binding_kind='source' "
                "AND b.record_id=sv.source_id AND b.version_id=sv.record_id "
                "WHERE sv.tenant_id=%s AND sv.workspace_id=%s AND sv.object_id=ANY(%s) "
                "ORDER BY b.notebook_id,sv.record_id",
                (context.tenant_id, context.workspace_id, [item.object_id for item in objects]),
            ).fetchall()
            grouped = {}
            for notebook_id, version_id, object_id in candidates:
                grouped.setdefault(notebook_id, []).append((version_id, object_id))
            target_ids = {item.object_id for item in objects}
            matches = []
            for notebook, rows in grouped.items():
                if {row[1] for row in rows} != target_ids or len(rows) != len(target_ids):
                    continue
                selection = BackupSelectionInput(notebook, tuple(row[0] for row in rows))
                try:
                    self._resolve(connection, context, selection, revision, None, trigger=trigger)
                except RecoveryError as error:
                    # 현재 비공개/해제/삭제만 제외한다. 장애와 정본 불일치는 실패를 유지한다.
                    if error.status == 404 and error.code == "BACKUP_UNAVAILABLE":
                        continue
                    raise
                matches.append(selection)
            if len(matches) != 1:
                raise RecoveryError("BACKUP_UNAVAILABLE", 404)
            # 다른 후보 검사 중 권한/결속이 바뀔 수 있으므로 선택 후보도 마지막에 재검증한다.
            selection = matches[0]
            result = self._resolve(connection, context, selection, revision, idempotency_key, trigger=trigger)
            result.require_legacy_match(schema_revision=schema_revision, retention_watermark=retention_watermark, objects=objects)
            return result


def _xor_bytes(left: bytes, right: bytes) -> bytes:
    return bytes(a ^ b for a, b in zip(left, right, strict=True))


def _seal(key: bytes, plaintext: bytes) -> bytes:
    """Encrypt then authenticate an internal opaque manifest envelope."""
    nonce = secrets.token_bytes(16)
    blocks: list[bytes] = []
    for counter in range((len(plaintext) + 31) // 32):
        blocks.append(hmac.digest(key, b"stream\x00" + nonce + counter.to_bytes(4), "sha256"))
    stream = b"".join(blocks)[: len(plaintext)]
    ciphertext = _xor_bytes(plaintext, stream)
    tag = hmac.digest(key, b"tag\x00" + nonce + ciphertext, "sha256")
    return b"DRM1" + nonce + tag + ciphertext


def _open(key: bytes, envelope: bytes) -> bytes:
    if len(envelope) < 52 or envelope[:4] != b"DRM1":
        raise RecoveryError("BACKUP_MANIFEST_INVALID", 409)
    nonce, tag, ciphertext = envelope[4:20], envelope[20:52], envelope[52:]
    expected = hmac.digest(key, b"tag\x00" + nonce + ciphertext, "sha256")
    if not hmac.compare_digest(tag, expected):
        raise RecoveryError("BACKUP_MANIFEST_INVALID", 409)
    blocks: list[bytes] = []
    for counter in range((len(ciphertext) + 31) // 32):
        blocks.append(hmac.digest(key, b"stream\x00" + nonce + counter.to_bytes(4), "sha256"))
    return _xor_bytes(ciphertext, b"".join(blocks)[: len(ciphertext)])


class MinioRecoveryStorageAdapter:
    """Verify canonical source objects and copy only into a fixture scope."""

    def __init__(self, storage: MinioObjectStorageAdapter) -> None:
        self._storage = storage
        self._keys = ObjectKeyPolicy()

    @staticmethod
    def _scope(tenant_id: str, workspace_id: str, capability: str) -> CloudAccessContext:
        return CloudAccessContext(tenant_id, workspace_id, "recovery-service", capability)

    def _read_verified(
        self, context: RecoveryContext, item: BackupObjectInput
    ) -> bytes:
        scope = self._scope(context.tenant_id, context.workspace_id, "object.read")
        key = self._keys.final_key(scope, "source", item.object_id)
        try:
            content = self._storage.get(key)
        except ObjectStorageError as error:
            raise RecoveryError("RESOURCE_UNAVAILABLE", 503, retryable=error.retryable) from None
        if len(content) != item.byte_size or hashlib.sha256(content).hexdigest() != item.checksum_sha256:
            raise RecoveryError("RESOURCE_UNAVAILABLE", 503, retryable=False)
        return content

    def verify_source(
        self, context: RecoveryContext, objects: tuple[BackupObjectInput, ...]
    ) -> None:
        if not self._storage.health():
            raise RecoveryError("RESOURCE_UNAVAILABLE", 503, retryable=True)
        for item in objects:
            self._read_verified(context, item)

    @contextmanager
    def open_source(self, context: RecoveryContext, item: BackupObjectInput):
        """v2 전용 bounded read. 기존 .get()/verify_source 전체 읽기를 호출하지 않는다."""
        scope = self._scope(context.tenant_id, context.workspace_id, "object.read")
        key = self._keys.final_key(scope, "source", item.object_id)
        response = None
        try:
            response = self._storage._client.get_object(self._storage._bucket, key)
            yield response
        except RecoveryError:
            raise
        except Exception:
            raise RecoveryError("RESOURCE_UNAVAILABLE", 503, retryable=False) from None
        finally:
            if response is not None:
                cleanup_failed = False
                try:
                    response.close()
                except Exception:
                    cleanup_failed = True
                try:
                    response.release_conn()
                except Exception:
                    cleanup_failed = True
                if cleanup_failed:
                    raise RecoveryError("RESOURCE_UNAVAILABLE", 503, retryable=False) from None

    def restore(
        self,
        context: RecoveryContext,
        destination: RestoreDestination,
        objects: tuple[BackupObjectInput, ...],
    ) -> bool:
        contents = tuple((item, self._read_verified(context, item)) for item in objects)
        target = self._scope(destination.tenant_id, destination.workspace_id, "object.write")
        try:
            for item, content in contents:
                staging_key = self._keys.staging_key(target, "source", item.object_id)
                final_key = self._keys.final_key(target, "source", item.object_id)
                staged = self._storage.put_staged(
                    staging_key, content, "application/octet-stream", item.checksum_sha256
                )
                self._storage.promote(
                    staged,
                    final_key,
                    expected_digest=item.checksum_sha256,
                    expected_size=item.byte_size,
                    content_type="application/octet-stream",
                )
        except ObjectStorageError as error:
            raise RecoveryError("RESOURCE_UNAVAILABLE", 503, retryable=error.retryable) from None
        return True

    def read_fixture(self, destination: RestoreDestination, object_id: str) -> bytes:
        scope = self._scope(destination.tenant_id, destination.workspace_id, "object.read")
        return self._storage.get(self._keys.final_key(scope, "source", object_id))


class PostgresRecoveryService:
    """RLS-scoped durable recovery service; no in-memory success fallback."""

    def __init__(
        self,
        dsn: str,
        storage: MinioRecoveryStorageAdapter,
        *,
        manifest_key: bytes,
        clock: Callable[[], datetime],
        fixture_prefix: str = "fixture-",
        rpo: timedelta = timedelta(minutes=15),
        notification_inbox_enabled: bool = False,
        capture_store=None,
        lineage_capture_store=None,
    ) -> None:
        if type(notification_inbox_enabled) is not bool:
            raise ValueError("NOTIFICATION_INBOX_FLAG_INVALID")
        if not isinstance(dsn, str) or not dsn:
            raise ValueError("CLOUD_DATABASE_DSN_REQUIRED")
        if not isinstance(manifest_key, bytes) or len(manifest_key) < 32:
            raise ValueError("RECOVERY_MANIFEST_KEY_INVALID")
        self._storage = storage
        self._key = hashlib.sha256(manifest_key).digest()
        self._manifest_master_key = manifest_key
        # root/key version은 승인된 주입 port만 소비한다. runtime 기본 구성은 여전히 미준비다.
        self._capture_store = capture_store
        self._lineage_capture_store = lineage_capture_store
        self._clock = clock
        self._fixture_prefix = fixture_prefix
        self._rpo = rpo
        self._notification_inbox_enabled = notification_inbox_enabled
        self._pool = ConnectionPool[Mapping[str, Any]](
            conninfo=dsn,
            min_size=1,
            max_size=4,
            kwargs={"autocommit": False, "row_factory": dict_row},
            timeout=2.0,
            reconnect_timeout=5.0,
            open=False,
        )
        self._open_lock = threading.Lock()

    def _ensure_open(self) -> None:
        if not self._pool.closed:
            return
        with self._open_lock:
            if self._pool.closed:
                self._pool.open(wait=False)

    def close(self) -> None:
        self._pool.close()

    @contextmanager
    def _transaction(
        self, tenant_id: str, workspace_id: str | None, actor_id: str = "recovery-service"
    ) -> Iterator[Connection[Mapping[str, Any]]]:
        try:
            self._ensure_open()
            with self._pool.connection(timeout=2.0) as connection:
                with connection.transaction():
                    connection.execute("SET LOCAL ROLE daon_app")
                    connection.execute("SELECT set_config('app.tenant_id', %s, true)", (tenant_id,))
                    connection.execute(
                        "SELECT set_config('app.workspace_id', %s, true)",
                        ("" if workspace_id is None else workspace_id,),
                    )
                    connection.execute("SELECT set_config('app.actor_id', %s, true)", (actor_id,))
                    connection.execute("SELECT set_config('app.capability', %s, true)", ("recovery",))
                    yield connection
        except RecoveryError:
            raise
        except PoolTimeout:
            raise RecoveryError("RESOURCE_UNAVAILABLE", 503, retryable=True) from None
        except Error:
            raise RecoveryError("RESOURCE_UNAVAILABLE", 503, retryable=False) from None

    @staticmethod
    def _require_admin(context: RecoveryContext) -> None:
        if not context.organization_admin:
            raise RecoveryError("CURRENT_ACCESS_DENIED", 403)

    @classmethod
    def _require_step_up(cls, context: RecoveryContext, verified: bool) -> None:
        cls._require_admin(context)
        if not verified:
            raise RecoveryError("STEP_UP_REQUIRED", 403)

    @staticmethod
    def _backup_transitions(state: str) -> tuple[str, ...]:
        paths = {
            "queued": ("queued",),
            "capturing": ("queued", "capturing"),
            "verifying": ("queued", "capturing", "verifying"),
            "ready": ("queued", "capturing", "verifying", "ready"),
            "failed": ("queued", "failed"),
            "expired": ("queued", "capturing", "verifying", "ready", "expired"),
        }
        return paths[state]

    @staticmethod
    def _restore_transitions(state: str) -> tuple[str, ...]:
        happy = ("requested", "preview_ready", "authorized", "restoring", "verifying", "completed")
        if state in happy:
            return happy[: happy.index(state) + 1]
        return ("requested", state)

    def _encode_objects(self, objects: tuple[BackupObjectInput, ...]) -> bytes:
        raw = json.dumps(
            [
                {
                    "object_id": item.object_id,
                    "checksum_sha256": item.checksum_sha256,
                    "byte_size": item.byte_size,
                }
                for item in objects
            ],
            sort_keys=True,
            separators=(",", ":"),
        ).encode()
        return _seal(self._key, raw)

    def _decode_objects(self, envelope: bytes) -> tuple[BackupObjectInput, ...]:
        try:
            if bytes(envelope).startswith((b"DRM2", b"DRM3")):
                scope, receipt = self._decode_capture_manifest(bytes(envelope))
                object_type = LineageObjectInput if isinstance(scope, LineageCaptureScope) else BackupObjectInput
                return tuple(object_type(row["object_id"], row["plaintext_digest"], row["byte_size"])
                             for row in receipt["objects"])
            values = json.loads(_open(self._key, bytes(envelope)))
            return tuple(BackupObjectInput(**item) for item in values)
        except (KeyError, TypeError, ValueError, json.JSONDecodeError):
            raise RecoveryError("BACKUP_MANIFEST_INVALID", 409) from None

    def _decode_capture_manifest(self, envelope):
        lineage = bytes(envelope).startswith(b"DRM3")
        store = getattr(self, "_lineage_capture_store", None) if lineage else self._capture_store
        if store is None:
            raise RecoveryError("RESOURCE_UNAVAILABLE", 503)
        return (decode_lineage_manifest if lineage else decode_manifest)(envelope, master_key=self._manifest_master_key,
            key_version=store.key_version, key_reference=store.key_reference)

    def _capture_identity(self, tenant_id, workspace_id, actor_id, idempotency_key):
        return hmac.digest(self._key, canonical_json_bytes({"tenant_id": tenant_id,
            "workspace_id": workspace_id, "actor_id": actor_id, "idempotency_key": idempotency_key}), "sha256").hex()

    def _capture_from_row(self, row, *, requested_backup_id=None):
        try:
            envelope = bytes(row["encrypted_inventory"])
            scope, receipt = self._decode_capture_manifest(envelope)
            token = self._capture_identity(row["tenant_id"], row["workspace_id"], row["actor_id"], row["idempotency_key"])
            lineage = isinstance(scope, LineageCaptureScope)
            if lineage and (row.get("manifest_version") != 3 or scope.actor_id != row["actor_id"]):
                raise RecoveryError("BACKUP_MANIFEST_INVALID", 409)
            if not lineage and row.get("manifest_version", 2) != 2:
                raise RecoveryError("BACKUP_MANIFEST_INVALID", 409)
            object_type = LineageObjectInput if lineage else BackupObjectInput
            objects = tuple(object_type(item["object_id"], item["plaintext_digest"], item["byte_size"])
                for item in receipt["objects"])
            if (row["backup_id"] != "backup-" + token or scope.capture_id != "capture-" + token
                    or (requested_backup_id is not None and row["backup_id"] != requested_backup_id)
                    or hashlib.sha256(envelope).hexdigest() != row["manifest_digest"]
                    or len(objects) != row["object_count"] or len({item.object_id for item in objects}) != len(objects)
                    or (scope.tenant_id, scope.workspace_id, scope.schema_revision, scope.retention_watermark,
                        scope.policy_version, scope.closure_fingerprint if lineage else scope.selection_fingerprint) !=
                       (row["tenant_id"], row["workspace_id"], row["schema_revision"], row["retention_watermark"],
                        row["policy_version"], row["request_fingerprint"])):
                raise RecoveryError("BACKUP_MANIFEST_INVALID", 409)
            return scope, receipt, objects
        except (KeyError, TypeError, ValueError):
            raise RecoveryError("BACKUP_MANIFEST_INVALID", 409) from None

    def _require_independent_capture(self, connection, context, backup_id, *, execute=False, preview=False, manifest_version=None):
        row = connection.execute(
            "SELECT b.*,m.manifest_version,m.manifest_digest,m.encrypted_inventory,m.object_count "
            "FROM backup_records b JOIN backup_manifests m USING(tenant_id,workspace_id,backup_id) "
            "WHERE m.backup_id=%s ORDER BY m.manifest_version DESC LIMIT 1", (backup_id,)).fetchone()
        if row is None:
            raise RecoveryError("BACKUP_UNAVAILABLE", 404)
        envelope = bytes(row["encrypted_inventory"])
        if ((row["manifest_version"], envelope[:4]) not in {(2, b"DRM2"), (3, b"DRM3")}
                or manifest_version is not None and row["manifest_version"] != manifest_version):
            raise RecoveryError("BACKUP_NOT_READY", 409)
        scope, receipt, _ = self._capture_from_row(row, requested_backup_id=backup_id)
        if (scope.tenant_id, scope.workspace_id) != (context.tenant_id, context.workspace_id):
            raise RecoveryError("BACKUP_UNAVAILABLE", 404)
        if row["state"] != "ready":
            raise RecoveryError("BACKUP_NOT_READY", 409)
        store = self._lineage_capture_store if isinstance(scope, LineageCaptureScope) else self._capture_store
        store.verify(scope, expected_receipt=receipt)
        if execute or preview:
            # L2-A file/metadata 복호화는 L3 DB materialization/계보/RLS 검증이 아니다.
            raise RecoveryError("RESOURCE_UNAVAILABLE", 503, retryable=False)

    def _encode_preview(
        self,
        included: tuple[str, ...],
        excluded: tuple[str, ...],
        reasons: tuple[tuple[str, str], ...],
    ) -> bytes:
        raw = json.dumps(
            {"included": included, "excluded": excluded, "reasons": reasons},
            sort_keys=True,
            separators=(",", ":"),
        ).encode()
        return _seal(self._key, raw)

    def _decode_preview(self, envelope: bytes) -> tuple[tuple[str, ...], tuple[str, ...], tuple[tuple[str, str], ...]]:
        try:
            value = json.loads(_open(self._key, bytes(envelope)))
            return (
                tuple(value["included"]),
                tuple(value["excluded"]),
                tuple((str(item[0]), str(item[1])) for item in value["reasons"]),
            )
        except (KeyError, TypeError, ValueError, json.JSONDecodeError):
            raise RecoveryError("BACKUP_MANIFEST_INVALID", 409) from None

    def _append_audit(
        self, connection: Connection[Mapping[str, Any]], context: RecoveryContext,
        action: str, target_type: str, target_id: str,
        *, outcome: str = "succeeded", before: Mapping[str, Any] | None = None,
        after: Mapping[str, Any] | None = None, reason_code: str | None = None,
    ) -> str:
        if outcome not in {"succeeded", "failed"} or reason_code not in {None, "RESTORE_PROCESSING_FAILED"}:
            raise RecoveryError("RESOURCE_UNAVAILABLE", 503)
        row = connection.execute(
            "SELECT metadata->>'event_hash' AS event_hash FROM audit_events "
            "ORDER BY sequence DESC LIMIT 1"
        ).fetchone()
        previous = "0" * 64 if row is None or row["event_hash"] is None else str(row["event_hash"])
        event_id = f"audit-{secrets.token_hex(16)}"
        occurred_at = self._clock()
        event_hash = _fingerprint((
            previous, event_id, context.tenant_id, context.workspace_id,
            context.actor_id, action, target_id, context.trace_id,
            context.policy_version, occurred_at.isoformat(),
        ))
        connection.execute(
            "INSERT INTO audit_events "
            "(event_id, tenant_id, workspace_id, actor_id, action, target_type, target_id, "
            "outcome, trace_id, policy_version, metadata, occurred_at,before_value,after_value) "
            "VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)",
            (
                event_id, context.tenant_id, context.workspace_id, context.actor_id,
                action, target_type, target_id, outcome, context.trace_id, context.policy_version,
                Jsonb({"previous_hash": previous, "event_hash": event_hash,
                       **({"reason_code": reason_code} if reason_code is not None else {})}), occurred_at,
                None if before is None else Jsonb(dict(before)), None if after is None else Jsonb(dict(after)),
            ),
        )
        return event_id

    def _backup_from_row(
        self, row: Mapping[str, Any]
    ) -> tuple[BackupView, tuple[BackupObjectInput, ...]]:
        envelope = bytes(row["encrypted_inventory"])
        if envelope.startswith((b"DRM2", b"DRM3")):
            _, _, objects = self._capture_from_row(row)
        else:
            objects = self._decode_objects(envelope)
        view = BackupView(
            str(row["backup_id"]), str(row["tenant_id"]), str(row["workspace_id"]),
            str(row["state"]), int(row["version"]), str(row["trigger_type"]),
            row["created_at"], row["verified_at"], str(row["schema_revision"]),
            str(row["retention_watermark"]), str(row["manifest_digest"]),
            int(row["object_count"]), self._backup_transitions(str(row["state"])),
        )
        return view, objects

    def _select_backup(
        self, connection: Connection[Mapping[str, Any]], backup_id: str
    ) -> tuple[BackupView, tuple[BackupObjectInput, ...]]:
        row = connection.execute(
            "SELECT b.*, m.manifest_version, m.manifest_digest, m.encrypted_inventory, m.object_count "
            "FROM backup_records b JOIN backup_manifests m USING (tenant_id,workspace_id,backup_id) "
            "WHERE b.backup_id=%s ORDER BY m.manifest_version DESC LIMIT 1",
            (backup_id,),
        ).fetchone()
        if row is None:
            raise RecoveryError("BACKUP_UNAVAILABLE", 404)
        return self._backup_from_row(row)

    def _select_restore(
        self, connection: Connection[Mapping[str, Any]], request_id: str
    ) -> RestoreRequestView:
        row = connection.execute(
            "SELECT r.*, p.preview_version, p.destination_tenant_id, "
            "p.destination_workspace_id, p.destination_database_id, p.destination_bucket_id, "
            "p.encrypted_adjustment_manifest, p.created_at AS preview_created_at, "
            "v.verification_digest FROM restore_requests r "
            "JOIN LATERAL (SELECT * FROM restore_previews p0 WHERE p0.tenant_id=r.tenant_id "
            "AND p0.workspace_id=r.workspace_id AND p0.request_id=r.request_id "
            "ORDER BY p0.preview_version DESC LIMIT 1) p ON true "
            "LEFT JOIN LATERAL (SELECT verification_digest FROM restore_verifications v0 "
            "WHERE v0.tenant_id=r.tenant_id AND v0.workspace_id=r.workspace_id "
            "AND v0.request_id=r.request_id ORDER BY v0.created_at DESC LIMIT 1) v ON true "
            "WHERE r.request_id=%s",
            (request_id,),
        ).fetchone()
        if row is None:
            raise RecoveryError("RESTORE_REQUEST_UNAVAILABLE", 404)
        included, excluded, reasons = self._decode_preview(bytes(row["encrypted_adjustment_manifest"]))
        preview = RestorePreviewView(
            int(row["preview_version"]), included, excluded, reasons,
            RestoreDestination(
                str(row["destination_tenant_id"]), str(row["destination_workspace_id"]),
                str(row["destination_database_id"]), str(row["destination_bucket_id"]),
            ),
            row["preview_created_at"],
        )
        return RestoreRequestView(
            str(row["request_id"]), str(row["backup_id"]), str(row["tenant_id"]),
            str(row["workspace_id"]), str(row["state"]), int(row["version"]), preview,
            self._restore_transitions(str(row["state"])),
            None if row["verification_digest"] is None else str(row["verification_digest"]),
        )

    def create_backup(
        self, context: RecoveryContext, *, trigger: str, schema_revision: str,
        retention_watermark: str, objects: tuple[BackupObjectInput, ...],
        idempotency_key: str,
    ) -> BackupView:
        self._require_admin(context)
        for value in (schema_revision, retention_watermark, idempotency_key):
            _safe(value)
        if trigger not in {"automatic", "manual"} or not objects:
            raise RecoveryError("RECOVERY_INPUT_INVALID", 400)
        if len({item.object_id for item in objects}) != len(objects):
            raise RecoveryError("BACKUP_MANIFEST_INVALID", 400)
        # 이 과거 직접 입구에는 L1의 현재 관계/선택 receipt가 없다. 새 v1 ready를 만들지 않는다.
        # 기존 공개 legacy 입력은 runtime resolver를 거쳐 create_selected_backup에서만 생성한다.
        raise RecoveryError("RESOURCE_UNAVAILABLE", 503, retryable=False)

    def create_selected_backup(self, context, resolved, metadata, *, idempotency_key, recheck, trigger="manual"):
        """검증 v2 선택 자료만 원장에 확정한다. 파일/DB 불확정은 사본 보존 후 명시 실패다."""
        self._require_admin(context)
        _safe(idempotency_key)
        if self._capture_store is None:
            raise RecoveryError("RESOURCE_UNAVAILABLE", 503, retryable=False)
        if trigger not in {"manual", "automatic"}:
            raise RecoveryError("RECOVERY_INPUT_INVALID", 400)
        # 동일 key의 metadata가 바뀌어도 capture ID는 바뀌지 않는다(덮어쓰기/재생성 금지).
        token = self._capture_identity(context.tenant_id, context.workspace_id, context.actor_id, idempotency_key)
        scope = CaptureScope("capture-" + token, context.tenant_id, context.workspace_id,
            resolved.selection.notebook_id, resolved.selection.source_version_ids, resolved.fingerprint,
            resolved.schema_revision, context.policy_version, resolved.retention_watermark)
        backup_id = "backup-" + token
        owner_id = secrets.token_hex(16)
        created_new, ledger_started = False, False
        try:
            with self._transaction(context.tenant_id, context.workspace_id, context.actor_id) as connection:
                connection.execute("SELECT pg_advisory_xact_lock(%s)", (int.from_bytes(bytes.fromhex(token[:16]), signed=True),))
                current, initial_metadata = recheck()  # replay조차 현재 사용자/권한/원장 검증을 생략하지 않는다.
                resolved.require_replay(current.fingerprint)
                replay = connection.execute("SELECT backup_id, request_fingerprint FROM backup_records "
                    "WHERE actor_id=%s AND idempotency_key=%s", (context.actor_id, idempotency_key)).fetchone()
                if replay is not None:
                    resolved.require_replay(str(replay["request_fingerprint"]))
                    self._require_independent_capture(connection, context, str(replay["backup_id"]), manifest_version=2)
                    replay_view = self._select_backup(connection, str(replay["backup_id"]))[0]
                    final, final_metadata = recheck()
                    resolved.require_replay(final.fingerprint)
                    # 이전 receipt는 덮어쓰지 않는다. 이번 긴 조회 도중의 metadata 변경만 거부한다.
                    if validate_metadata(scope, initial_metadata, current.objects) != validate_metadata(scope, final_metadata, final.objects):
                        raise RecoveryError("BACKUP_MANIFEST_INVALID", 409)
                    return replay_view
                raw = validate_metadata(scope, metadata, resolved.objects)
                if self._capture_store.exists(scope):
                    verified = self._capture_store.verify(scope)
                    receipt = verified.receipt
                    if hashlib.sha256(raw).hexdigest() != receipt["metadata_digest"]:
                        raise RecoveryError("IDEMPOTENCY_KEY_REUSED", 409)
                else:
                    created_new = True
                    receipt = self._capture_store.capture(scope, metadata, resolved.objects,
                        lambda item: self._storage.open_source(context, item), owner_id=owner_id)
                self._capture_store.verify(scope, expected_receipt=receipt)
                encrypted = encode_manifest(scope, receipt, master_key=self._manifest_master_key,
                    key_version=self._capture_store.key_version, key_reference=self._capture_store.key_reference)
                now = self._clock()
                # 긴 파일 검증/암호화 이후의 최종 검사다. 별도 auth writer와의 원자 직렬화는 아니다.
                current, final_metadata = recheck()
                resolved.require_replay(current.fingerprint)
                if hashlib.sha256(validate_metadata(scope, final_metadata, current.objects)).hexdigest() != receipt["metadata_digest"]:
                    raise RecoveryError("BACKUP_MANIFEST_INVALID", 409)
                ledger_started = True
                audit_event_id = self._append_audit(connection, context, "backup.requested", "backup", backup_id)
                connection.execute("INSERT INTO backup_record_locator (tenant_id,backup_id,workspace_id,created_at) VALUES (%s,%s,%s,%s)",
                    (context.tenant_id, backup_id, context.workspace_id, now))
                connection.execute("INSERT INTO backup_records "
                    "(tenant_id,workspace_id,backup_id,actor_id,trigger_type,state,version,schema_revision,retention_watermark,policy_version,trace_id,audit_event_id,idempotency_key,request_fingerprint,created_at) "
                    "VALUES (%s,%s,%s,%s,%s,'queued',1,%s,%s,%s,%s,%s,%s,%s,%s)",
                    (context.tenant_id, context.workspace_id, backup_id, context.actor_id, trigger,
                     resolved.schema_revision, resolved.retention_watermark, context.policy_version, context.trace_id,
                     audit_event_id, idempotency_key, resolved.fingerprint, now))
                connection.execute("INSERT INTO backup_manifests "
                    "(tenant_id,workspace_id,backup_id,manifest_version,manifest_digest,encrypted_inventory,object_count,created_at) "
                    "VALUES (%s,%s,%s,%s,%s,%s,%s,%s)",
                    (context.tenant_id, context.workspace_id, backup_id, 2, hashlib.sha256(encrypted).hexdigest(), encrypted, len(resolved.objects), now))
                for state in ("capturing", "verifying"):
                    connection.execute("UPDATE backup_records SET state=%s,version=version+1 WHERE backup_id=%s", (state, backup_id))
                connection.execute("UPDATE backup_records SET state='ready',version=version+1,verified_at=%s WHERE backup_id=%s", (now, backup_id))
                # 기존 ready 사건은 검증된 선택 세트만 뜻한다. 실패 알림/전체 DB 성공 사건을 새로 만들지 않는다.
                ready_event = self._append_audit(connection, context, "backup.ready", "backup", backup_id,
                    before={"state": "verifying", "version": 3}, after={"state": "ready", "version": 4})
                if self._notification_inbox_enabled is True:
                    from .notification_recovery import enqueue_terminal_recovery_event
                    enqueue_terminal_recovery_event(connection, context=context, origin_event_id=ready_event, target_id=backup_id)
                return self._select_backup(connection, backup_id)[0]
        except RecoveryError as error:
            if created_new and not ledger_started and error.status in {400, 403, 404, 409, 413}:
                @contextmanager
                def unreferenced_ledger():
                    with self._transaction(context.tenant_id, context.workspace_id, context.actor_id) as check:
                        check.execute("SELECT pg_advisory_xact_lock(%s)", (int.from_bytes(bytes.fromhex(token[:16]), signed=True),))
                        row = check.execute("SELECT backup_id, request_fingerprint FROM backup_records "
                            "WHERE actor_id=%s AND idempotency_key=%s", (context.actor_id, idempotency_key)).fetchone()
                        yield row
                self._capture_store.discard_unreferenced(scope, owner_id=owner_id, ledger_guard=unreferenced_ledger)
            # 503/commit 불확정/이전 artifact는 자동 삭제하지 않는다.
            raise
        except OSError:
            # 재시도/삭제는 하지 않는다. DB가 확정됐는지는 정확 원장 재조회로만 결정한다.
            raise RecoveryError("RESOURCE_UNAVAILABLE", 503, retryable=False) from None

    def create_lineage_backup(self, context, selection, *, idempotency_key, recheck, trigger="manual"):
        """동의한 exact v3만 확정한다. 별도 auth tx와 commit의 원자 직렬화는 보장하지 않는다."""
        self._require_admin(context); _safe(idempotency_key)
        store = getattr(self, "_lineage_capture_store", None)
        if store is None: raise RecoveryError("RESOURCE_UNAVAILABLE", 503, retryable=False)
        if trigger != "manual" or not callable(recheck): raise RecoveryError("RECOVERY_INPUT_INVALID", 400)
        token = self._capture_identity(context.tenant_id, context.workspace_id, context.actor_id, idempotency_key)
        scope = LineageCaptureScope.from_selection(context, selection, capture_id="capture-" + token)
        backup_id, owner_id = "backup-" + token, secrets.token_hex(16)
        lock_number = int.from_bytes(bytes.fromhex(token[:16]), signed=True)
        created_new = ledger_started = False
        def current_selection():
            current = recheck()
            selection.require_consent(current.fingerprint, True)
            if current._encoded != selection._encoded: raise RecoveryError("BACKUP_MANIFEST_INVALID", 409)
            return current
        try:
            with self._transaction(context.tenant_id, context.workspace_id, context.actor_id) as connection:
                connection.execute("SELECT pg_advisory_xact_lock(%s)", (lock_number,))
                current_selection()
                replay = connection.execute("SELECT backup_id, request_fingerprint FROM backup_records WHERE actor_id=%s AND idempotency_key=%s",
                    (context.actor_id, idempotency_key)).fetchone()
                if replay is not None:
                    selection.require_consent(str(replay["request_fingerprint"]), True)
                    self._require_independent_capture(connection, context, str(replay["backup_id"]), manifest_version=3)
                    view = self._select_backup(connection, str(replay["backup_id"]))[0]
                    current_selection()
                    return view
                # 동결된 v3 포트의 보호 root/file port만 읽는다. 손상을 부재로 해석하지 않는다.
                with store._root():
                    exists = store._fs.exists(store.root / ("lineage-v3-" + scope.capture_id))
                if exists:
                    receipt = store.verify(scope).receipt
                    if receipt["metadata_digest"] != hashlib.sha256(selection._encoded).hexdigest():
                        raise RecoveryError("IDEMPOTENCY_KEY_REUSED", 409)
                else:
                    created_new = True
                    receipt = store.capture(scope, selection, lambda item: self._storage.open_source(context, item),
                        closure_fingerprint=selection.fingerprint, include_prior_versions=True, recheck=current_selection, owner_id=owner_id)
                store.verify(scope, expected_receipt=receipt)
                encrypted = encode_lineage_manifest(scope, receipt, master_key=self._manifest_master_key,
                    key_version=store.key_version, key_reference=store.key_reference)
                current_selection()  # 긴 bytes 검증/암호화 뒤, 원장 쓰기 직전 현재 세션/접근/metadata
                now = self._clock(); ledger_started = True
                audit_id = self._append_audit(connection, context, "backup.requested", "backup", backup_id)
                connection.execute("INSERT INTO backup_record_locator (tenant_id,backup_id,workspace_id,created_at) VALUES (%s,%s,%s,%s)",
                    (context.tenant_id, backup_id, context.workspace_id, now))
                connection.execute("INSERT INTO backup_records "
                    "(tenant_id,workspace_id,backup_id,actor_id,trigger_type,state,version,schema_revision,retention_watermark,policy_version,trace_id,audit_event_id,idempotency_key,request_fingerprint,created_at) "
                    "VALUES (%s,%s,%s,%s,%s,'queued',1,%s,%s,%s,%s,%s,%s,%s,%s)",
                    (context.tenant_id,context.workspace_id,backup_id,context.actor_id,trigger,selection.schema_revision,
                     selection.retention_watermark,context.policy_version,context.trace_id,audit_id,idempotency_key,selection.fingerprint,now))
                connection.execute("INSERT INTO backup_manifests "
                    "(tenant_id,workspace_id,backup_id,manifest_version,manifest_digest,encrypted_inventory,object_count,created_at) VALUES (%s,%s,%s,%s,%s,%s,%s,%s)",
                    (context.tenant_id,context.workspace_id,backup_id,3,hashlib.sha256(encrypted).hexdigest(),encrypted,receipt["object_count"],now))
                for state in ("capturing", "verifying"):
                    connection.execute("UPDATE backup_records SET state=%s,version=version+1 WHERE backup_id=%s", (state,backup_id))
                connection.execute("UPDATE backup_records SET state='ready',version=version+1,verified_at=%s WHERE backup_id=%s", (now,backup_id))
                ready = self._append_audit(connection,context,"backup.ready","backup",backup_id,
                    before={"state":"verifying","version":3},after={"state":"ready","version":4})
                if self._notification_inbox_enabled is True:
                    from .notification_recovery import enqueue_terminal_recovery_event
                    enqueue_terminal_recovery_event(connection,context=context,origin_event_id=ready,target_id=backup_id)
                return self._select_backup(connection, backup_id)[0]
        except RecoveryError as error:
            if created_new and not ledger_started and error.status in {400,401,403,404,409,413}:
                @contextmanager
                def reference_guard():
                    with self._transaction(context.tenant_id,context.workspace_id,context.actor_id) as check:
                        check.execute("SELECT pg_advisory_xact_lock(%s)",(lock_number,))
                        yield check.execute("SELECT backup_id, request_fingerprint FROM backup_records WHERE actor_id=%s AND idempotency_key=%s",
                            (context.actor_id,idempotency_key)).fetchone()
                store.discard_unreferenced(scope,owner_id=owner_id,reference_guard=reference_guard)
            raise
        except OSError: raise RecoveryError("RESOURCE_UNAVAILABLE",503,retryable=False) from None

    def list_backups(self, context: RecoveryContext) -> tuple[BackupView, ...]:
        self._require_admin(context)
        with self._transaction(context.tenant_id, context.workspace_id, context.actor_id) as connection:
            rows = connection.execute(
                "SELECT b.*,m.manifest_version,m.manifest_digest,m.encrypted_inventory,m.object_count "
                "FROM backup_records b JOIN backup_manifests m USING (tenant_id,workspace_id,backup_id) "
                "ORDER BY b.created_at DESC"
            ).fetchall()
            return tuple(self._backup_from_row(row)[0] for row in rows)

    def get_backup(self, context: RecoveryContext, backup_id: str) -> BackupView:
        self._require_admin(context)
        with self._transaction(context.tenant_id, context.workspace_id, context.actor_id) as connection:
            return self._select_backup(connection, backup_id)[0]

    def locate_backup_workspace(self, tenant_id: str, backup_id: str) -> str:
        with self._transaction(tenant_id, None) as connection:
            row = connection.execute(
                "SELECT workspace_id FROM backup_record_locator WHERE backup_id=%s", (backup_id,)
            ).fetchone()
        if row is None:
            raise RecoveryError("BACKUP_UNAVAILABLE", 404)
        return str(row["workspace_id"])

    def locate_restore_workspace(self, tenant_id: str, request_id: str) -> str:
        with self._transaction(tenant_id, None) as connection:
            row = connection.execute(
                "SELECT workspace_id FROM restore_request_locator WHERE request_id=%s", (request_id,)
            ).fetchone()
        if row is None:
            raise RecoveryError("RESTORE_REQUEST_UNAVAILABLE", 404)
        return str(row["workspace_id"])

    def backup_due(self, context: RecoveryContext) -> bool:
        self._require_admin(context)
        with self._transaction(context.tenant_id, context.workspace_id, context.actor_id) as connection:
            row = connection.execute(
                "SELECT max(verified_at) AS verified_at FROM backup_records WHERE state='ready'"
            ).fetchone()
        return row is None or row["verified_at"] is None or self._clock() - row["verified_at"] >= self._rpo

    def _retention(
        self, connection: Connection[Mapping[str, Any]]
    ) -> dict[str, set[str]]:
        rows = connection.execute(
            "SELECT sv.object_id, "
            "bool_or(dr.state='purged') AS purged, "
            "bool_or(lh.state='active') AS held, "
            "bool_or(dr.state IN ('deactivated','grace_period','cleanup_pending','blocked_by_hold')) AS tombstoned "
            "FROM source_versions sv "
            "LEFT JOIN deletion_requests dr ON dr.tenant_id=sv.tenant_id "
            "AND dr.workspace_id=sv.workspace_id AND dr.source_id=sv.source_id "
            "LEFT JOIN legal_holds lh ON lh.tenant_id=sv.tenant_id "
            "AND lh.workspace_id=sv.workspace_id AND lh.source_id=sv.source_id "
            "WHERE sv.object_id IS NOT NULL GROUP BY sv.object_id"
        ).fetchall()
        result = {"purged": set(), "held": set(), "tombstoned": set()}
        for row in rows:
            object_id = str(row["object_id"])
            for state in result:
                if bool(row[state]):
                    result[state].add(object_id)
        return result

    def _preview(
        self, connection: Connection[Mapping[str, Any]], objects: tuple[BackupObjectInput, ...],
        destination: RestoreDestination, *, version: int,
    ) -> RestorePreviewView:
        if any(
            not value.startswith(self._fixture_prefix)
            for value in (
                destination.tenant_id, destination.workspace_id,
                destination.database_id, destination.bucket_id,
            )
        ):
            raise RecoveryError("FIXTURE_ONLY_RESTORE_REQUIRED", 403)
        retention = self._retention(connection)
        included: list[str] = []
        reasons: list[tuple[str, str]] = []
        for item in objects:
            reason = next(
                (state for state in ("purged", "held", "tombstoned") if item.object_id in retention[state]),
                None,
            )
            if reason is None:
                included.append(item.object_id)
            else:
                reasons.append((item.object_id, reason))
        return RestorePreviewView(
            version, tuple(included), tuple(item[0] for item in reasons), tuple(reasons),
            destination, self._clock(),
        )

    def create_restore_preview(
        self, context: RecoveryContext, backup_id: str, *, destination: RestoreDestination,
        idempotency_key: str, step_up_verified: bool,
    ) -> RestoreRequestView:
        self._require_step_up(context, step_up_verified)
        _safe(idempotency_key)
        fingerprint = _fingerprint((backup_id, destination, context.policy_version))
        with self._transaction(context.tenant_id, context.workspace_id, context.actor_id) as connection:
            self._require_independent_capture(connection, context, backup_id, preview=True)
            replay = connection.execute(
                "SELECT request_id,request_fingerprint FROM restore_requests "
                "WHERE actor_id=%s AND idempotency_key=%s",
                (context.actor_id, idempotency_key),
            ).fetchone()
            if replay is not None:
                if str(replay["request_fingerprint"]) != fingerprint:
                    raise RecoveryError("IDEMPOTENCY_KEY_REUSED", 409)
                return self._select_restore(connection, str(replay["request_id"]))
            backup, objects = self._select_backup(connection, backup_id)
            if backup.state != "ready":
                raise RecoveryError("BACKUP_NOT_READY", 409)
            if (
                destination.tenant_id == context.tenant_id
                or destination.workspace_id == context.workspace_id
            ):
                raise RecoveryError("IN_PLACE_RESTORE_FORBIDDEN", 403)
            preview = self._preview(connection, objects, destination, version=1)
            request_id = f"restore-{secrets.token_hex(12)}"
            now = self._clock()
            audit_event_id = self._append_audit(
                connection, context, "restore.previewed", "restore_request", request_id
            )
            connection.execute(
                "INSERT INTO restore_requests "
                "(tenant_id,workspace_id,request_id,backup_id,actor_id,state,version,policy_version,"
                "trace_id,audit_event_id,idempotency_key,request_fingerprint,created_at,updated_at) "
                "VALUES (%s,%s,%s,%s,%s,'requested',1,%s,%s,%s,%s,%s,%s,%s)",
                (
                    context.tenant_id, context.workspace_id, request_id, backup_id,
                    context.actor_id, context.policy_version, context.trace_id, audit_event_id,
                    idempotency_key, fingerprint, now, now,
                ),
            )
            connection.execute(
                "INSERT INTO restore_request_locator (tenant_id,request_id,workspace_id,created_at) "
                "VALUES (%s,%s,%s,%s)",
                (context.tenant_id, request_id, context.workspace_id, now),
            )
            connection.execute(
                "INSERT INTO restore_previews "
                "(tenant_id,workspace_id,request_id,preview_version,destination_tenant_id,"
                "destination_workspace_id,destination_database_id,destination_bucket_id,"
                "encrypted_adjustment_manifest,created_at) VALUES (%s,%s,%s,1,%s,%s,%s,%s,%s,%s)",
                (
                    context.tenant_id, context.workspace_id, request_id,
                    destination.tenant_id, destination.workspace_id, destination.database_id,
                    destination.bucket_id,
                    self._encode_preview(
                        preview.included_object_ids, preview.excluded_object_ids,
                        preview.exclusion_reasons,
                    ),
                    preview.created_at,
                ),
            )
            connection.execute(
                "UPDATE restore_requests SET state='preview_ready',version=version+1,updated_at=%s "
                "WHERE request_id=%s",
                (now, request_id),
            )
            return self._select_restore(connection, request_id)

    def get_restore_request(self, context: RecoveryContext, request_id: str) -> RestoreRequestView:
        self._require_admin(context)
        with self._transaction(context.tenant_id, context.workspace_id, context.actor_id) as connection:
            return self._select_restore(connection, request_id)

    def _operation_replay(
        self, connection: Connection[Mapping[str, Any]], context: RecoveryContext,
        operation: str, idempotency_key: str, fingerprint: str,
    ) -> str | None:
        row = connection.execute(
            "SELECT request_fingerprint,result FROM idempotency_records "
            "WHERE actor_id=%s AND operation=%s AND idempotency_key=%s",
            (context.actor_id, operation, idempotency_key),
        ).fetchone()
        if row is None:
            return None
        if str(row["request_fingerprint"]) != fingerprint:
            raise RecoveryError("IDEMPOTENCY_KEY_REUSED", 409)
        return str(row["result"]["request_id"])

    def _record_operation(
        self, connection: Connection[Mapping[str, Any]], context: RecoveryContext,
        operation: str, idempotency_key: str, fingerprint: str, request_id: str,
    ) -> None:
        connection.execute(
            "INSERT INTO idempotency_records "
            "(tenant_id,workspace_id,actor_id,operation,idempotency_key,request_fingerprint,"
            "result,status,expires_at) VALUES (%s,%s,%s,%s,%s,%s,%s,'completed',%s)",
            (
                context.tenant_id, context.workspace_id, context.actor_id, operation,
                idempotency_key, fingerprint, Jsonb({"request_id": request_id}),
                self._clock() + timedelta(hours=24),
            ),
        )

    def _materialize_fixture(
        self,
        context: RecoveryContext,
        request_id: str,
        destination: RestoreDestination,
        objects: tuple[BackupObjectInput, ...],
    ) -> None:
        now = self._clock()
        with self._transaction(
            destination.tenant_id, destination.workspace_id, context.actor_id
        ) as connection:
            connection.execute(
                "INSERT INTO tenants (tenant_id,display_name) VALUES (%s,%s) "
                "ON CONFLICT (tenant_id) DO NOTHING",
                (destination.tenant_id, destination.tenant_id),
            )
            connection.execute(
                "INSERT INTO workspaces (tenant_id,workspace_id,display_name) VALUES (%s,%s,%s) "
                "ON CONFLICT (tenant_id,workspace_id) DO NOTHING",
                (
                    destination.tenant_id,
                    destination.workspace_id,
                    destination.workspace_id,
                ),
            )
            for item in objects:
                staging_key = (
                    f"_staging/{destination.tenant_id}/{destination.workspace_id}/source/"
                    f"{item.object_id}"
                )
                object_key = (
                    f"{destination.tenant_id}/{destination.workspace_id}/source/"
                    f"{item.object_id}"
                )
                fingerprint = _fingerprint((request_id, destination, item))
                connection.execute(
                    "INSERT INTO object_records "
                    "(tenant_id,workspace_id,object_id,area,staging_key,object_key,digest_sha256,"
                    "byte_size,content_type,status,cleanup_pending,created_by,trace_id,"
                    "idempotency_key,request_fingerprint,version,created_at,completed_at) "
                    "VALUES (%s,%s,%s,'source',%s,%s,%s,%s,'application/octet-stream',"
                    "'completed',false,%s,%s,%s,%s,1,%s,%s) "
                    "ON CONFLICT (tenant_id,workspace_id,object_id) DO NOTHING",
                    (
                        destination.tenant_id, destination.workspace_id, item.object_id,
                        staging_key, object_key, item.checksum_sha256, item.byte_size,
                        context.actor_id, context.trace_id,
                        f"restore-{request_id}-{item.object_id}", fingerprint, now, now,
                    ),
                )

    def execute_restore(
        self, context: RecoveryContext, request_id: str, *, expected_version: int,
        preview_version: int, idempotency_key: str, step_up_verified: bool,
    ) -> RestoreRequestView:
        self._require_step_up(context, step_up_verified)
        _safe(idempotency_key)
        fingerprint = _fingerprint((
            request_id, expected_version, preview_version, context.policy_version,
        ))
        with self._transaction(context.tenant_id, context.workspace_id, context.actor_id) as connection:
            existing_request = self._select_restore(connection, request_id)
            self._require_independent_capture(connection, context, existing_request.backup_id, execute=True)
            replay = self._operation_replay(
                connection, context, "restore-execute", idempotency_key, fingerprint
            )
            if replay is not None:
                return self._select_restore(connection, replay)
            request = self._select_restore(connection, request_id)
            if request.version != expected_version or request.preview.version != preview_version:
                raise RecoveryError("RESTORE_VERSION_CONFLICT", 409)
            if request.state != "preview_ready":
                raise RecoveryError("RESTORE_STATE_INVALID", 409)
            _, objects = self._select_backup(connection, request.backup_id)
            current = self._preview(
                connection, objects, request.preview.destination,
                version=request.preview.version + 1,
            )
            connection.execute(
                "INSERT INTO restore_previews "
                "(tenant_id,workspace_id,request_id,preview_version,destination_tenant_id,"
                "destination_workspace_id,destination_database_id,destination_bucket_id,"
                "encrypted_adjustment_manifest,created_at) VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)",
                (
                    context.tenant_id, context.workspace_id, request_id, current.version,
                    current.destination.tenant_id, current.destination.workspace_id,
                    current.destination.database_id, current.destination.bucket_id,
                    self._encode_preview(
                        current.included_object_ids, current.excluded_object_ids,
                        current.exclusion_reasons,
                    ),
                    current.created_at,
                ),
            )
            for state in ("authorized", "restoring"):
                connection.execute(
                    "UPDATE restore_requests SET state=%s,version=version+1,updated_at=%s "
                    "WHERE request_id=%s",
                    (state, self._clock(), request_id),
                )
        allowed = set(current.included_object_ids)
        restore_objects = tuple(item for item in objects if item.object_id in allowed)
        try:
            self._storage.restore(context, current.destination, restore_objects)
            self._materialize_fixture(
                context, request_id, current.destination, restore_objects
            )
        except RecoveryError:
            with self._transaction(context.tenant_id, context.workspace_id, context.actor_id) as connection:
                failed_from = connection.execute("SELECT state,version FROM restore_requests WHERE request_id=%s FOR UPDATE",
                    (request_id,)).fetchone()
                changed = connection.execute(
                    "UPDATE restore_requests SET state='failed',version=version+1,updated_at=%s "
                    "WHERE request_id=%s AND state='restoring'",
                    (self._clock(), request_id),
                )
                if changed.rowcount == 1:
                    if failed_from is None or failed_from["state"] != "restoring":
                        raise RecoveryError("RESOURCE_UNAVAILABLE", 503)
                    failed_event = self._append_audit(connection, context, "restore.failed", "restore_request", request_id,
                        outcome="failed", before={"state": "restoring", "version": int(failed_from["version"])},
                        after={"state": "failed", "version": int(failed_from["version"]) + 1},
                        reason_code="RESTORE_PROCESSING_FAILED")
                    if self._notification_inbox_enabled is True:
                        from .notification_recovery import enqueue_terminal_recovery_event
                        enqueue_terminal_recovery_event(connection, context=context,
                            origin_event_id=failed_event, target_id=request_id)
            raise
        with self._transaction(context.tenant_id, context.workspace_id, context.actor_id) as connection:
            connection.execute(
                "UPDATE restore_requests SET state='verifying',version=version+1,updated_at=%s "
                "WHERE request_id=%s",
                (self._clock(), request_id),
            )
            audit_event_id = self._append_audit(
                connection, context, "restore.completed", "restore_request", request_id
            )
            verification_id = f"verification-{secrets.token_hex(12)}"
            digest = _fingerprint((
                request_id, current.destination, restore_objects, audit_event_id,
                context.policy_version,
            ))
            connection.execute(
                "INSERT INTO restore_verifications "
                "(tenant_id,workspace_id,verification_id,request_id,verification_digest,"
                "lineage_verified,rls_verified,audit_chain_verified,retention_rechecked,created_at) "
                "VALUES (%s,%s,%s,%s,%s,true,true,true,true,%s)",
                (
                    context.tenant_id, context.workspace_id, verification_id, request_id,
                    digest, self._clock(),
                ),
            )
            connection.execute(
                "UPDATE restore_requests SET state='completed',version=version+1,updated_at=%s "
                "WHERE request_id=%s",
                (self._clock(), request_id),
            )
            self._record_operation(
                connection, context, "restore-execute", idempotency_key, fingerprint, request_id
            )
            if self._notification_inbox_enabled is True:
                from .notification_recovery import enqueue_restore_completed_event
                enqueue_restore_completed_event(connection, context=context,
                    origin_event_id=audit_event_id, request_id=request_id)
            return self._select_restore(connection, request_id)

    def cancel_restore(
        self, context: RecoveryContext, request_id: str, *, expected_version: int,
        idempotency_key: str,
    ) -> RestoreRequestView:
        self._require_admin(context)
        _safe(idempotency_key)
        fingerprint = _fingerprint((request_id, expected_version))
        with self._transaction(context.tenant_id, context.workspace_id, context.actor_id) as connection:
            replay = self._operation_replay(
                connection, context, "restore-cancel", idempotency_key, fingerprint
            )
            if replay is not None:
                return self._select_restore(connection, replay)
            request = self._select_restore(connection, request_id)
            if request.version != expected_version:
                raise RecoveryError("RESTORE_VERSION_CONFLICT", 409)
            if request.state not in {"requested", "preview_ready", "authorized"}:
                raise RecoveryError("RESTORE_STATE_INVALID", 409)
            connection.execute(
                "UPDATE restore_requests SET state='cancelled',version=version+1,updated_at=%s "
                "WHERE request_id=%s",
                (self._clock(), request_id),
            )
            self._record_operation(
                connection, context, "restore-cancel", idempotency_key, fingerprint, request_id
            )
            self._append_audit(
                connection, context, "restore.cancelled", "restore_request", request_id
            )
            return self._select_restore(connection, request_id)
