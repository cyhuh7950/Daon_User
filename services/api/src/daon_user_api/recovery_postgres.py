"""Durable PostgreSQL and MinIO adapters for R1-M5-07 recovery."""

from __future__ import annotations

import hashlib
import hmac
import json
import os
import stat
import re
import secrets
import threading
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Callable, Iterator, Mapping
from types import SimpleNamespace,MappingProxyType

from psycopg import Connection, Error
from psycopg.errors import UniqueViolation
from psycopg.rows import dict_row
from psycopg.types.json import Jsonb
from psycopg_pool import ConnectionPool, PoolTimeout
from cryptography.exceptions import InvalidTag
from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.ciphers.aead import AESGCM
from cryptography.hazmat.primitives.kdf.hkdf import HKDF

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
    PostgresObjectQueueStore,
)
from daon_user_api.recovery import (
    BackupObjectInput,
    BackupSelectionInput,
    BackupView,
    RecoveryContext,
    RecoveryError,
    RestoreDestination,
    RestorePreviewView,
    RegisteredRestorePreviewView,
    RestoreRequestView,
    _fingerprint,
    _safe,
    _resolved_backup_metadata,
    _safe_canon_id,
)
from daon_user_api.recovery_capture import (
    CaptureScope, METADATA_COLUMNS, decode_manifest, encode_manifest, validate_metadata,
)
from daon_user_api.recovery_lineage import (resolve_lineage_selection, _read as _lineage_read,
    _one as _lineage_one, _json as _lineage_json, _canon as _lineage_canon)
from daon_user_api.recovery_lineage_capture import (
    LineageCaptureScope, decode_manifest as decode_lineage_manifest, encode_manifest as encode_lineage_manifest,
    _Object as LineageObjectInput,
)
from daon_user_api.recovery_destinations import (SignedDestinationRegistry,TrustedPhysicalProbe,RegisteredDestination,
    PhysicalProbeInstance,ProbeFoundation,PostgresMinioPhysicalProbe,ProbeRequest,DatabaseIdentity,physical_receipt_digest,
    _windows_protected)
from daon_user_api.organization_access import OrganizationAccessBridge
from daon_user_api.organization_membership import OrganizationWorkflowError


UTC = timezone.utc


class _RegisteredExecutionOwnershipLost(RecoveryError):
    def __init__(self):super().__init__("RESOURCE_UNAVAILABLE",503,retryable=False)


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


@dataclass(frozen=True,slots=True,repr=False)
class RegisteredTargetDescriptor:
    instance: PhysicalProbeInstance
    queue: PostgresObjectQueueStore
    probe_request: ProbeRequest
    catalog_digest: str


def _read_restore_protected_file(value, limit):
    """기존 pin/NOFOLLOW port. 설정/Secret 원문과 경로는 예외에 반사하지 않는다."""
    from .recovery_capture import _WindowsFiles, _PosixFiles
    try:
        path=Path(value)
        if not path.is_absolute() or ".." in path.parts or path==Path(path.anchor):raise ValueError()
        fs=_WindowsFiles() if os.name=="nt" else _PosixFiles()
        with fs.pin(path.parent):
            for parent in (*reversed(path.parent.parents),path.parent):
                if os.name=="nt":_windows_protected(parent)
                else:
                    info=parent.lstat()
                    if info.st_uid not in {0,os.geteuid()} or info.st_mode & 0o022:raise ValueError()
            with fs.file(path) as stream:
                if os.name=="nt":
                    _windows_protected(path)
                    # 기존 ACL 검사에 더해 파일 owner는 실제 서비스 token SID여야 한다.
                    import ctypes
                    from ctypes import wintypes
                    api=ctypes.WinDLL("advapi32",use_last_error=True);kernel=ctypes.WinDLL("kernel32",use_last_error=True)
                    pointer=ctypes.c_void_p;owner=pointer();descriptor=pointer();token=wintypes.HANDLE()
                    api.GetNamedSecurityInfoW.argtypes=[wintypes.LPWSTR,ctypes.c_int,wintypes.DWORD,ctypes.POINTER(pointer),pointer,pointer,pointer,ctypes.POINTER(pointer)]
                    api.OpenProcessToken.argtypes=[wintypes.HANDLE,wintypes.DWORD,ctypes.POINTER(wintypes.HANDLE)]
                    api.GetTokenInformation.argtypes=[wintypes.HANDLE,ctypes.c_int,pointer,wintypes.DWORD,ctypes.POINTER(wintypes.DWORD)]
                    api.EqualSid.argtypes=[pointer,pointer];kernel.LocalFree.argtypes=[pointer];kernel.CloseHandle.argtypes=[wintypes.HANDLE]
                    try:
                        if api.GetNamedSecurityInfoW(str(path),1,1,ctypes.byref(owner),None,None,None,ctypes.byref(descriptor))!=0:raise ValueError()
                        if not api.OpenProcessToken(wintypes.HANDLE(-1),8,ctypes.byref(token)):raise ValueError()
                        needed=wintypes.DWORD();api.GetTokenInformation(token,1,None,0,ctypes.byref(needed))
                        if not 0<needed.value<=65536:raise ValueError()
                        buffer=ctypes.create_string_buffer(needed.value)
                        if not api.GetTokenInformation(token,1,buffer,needed,ctypes.byref(needed)) or not api.EqualSid(owner,pointer.from_buffer(buffer)):raise ValueError()
                    finally:
                        if descriptor:kernel.LocalFree(descriptor)
                        if token:kernel.CloseHandle(token)
                before=os.fstat(stream.fileno())
                if (not stat.S_ISREG(before.st_mode) or before.st_nlink!=1 or not 0<before.st_size<=limit
                    or (os.name=="posix" and (before.st_uid!=os.geteuid() or stat.S_IMODE(before.st_mode)!=0o600))):raise ValueError()
                raw=stream.read(limit+1);after=os.fstat(stream.fileno())
                if (len(raw)!=before.st_size or any(getattr(before,f)!=getattr(after,f) for f in
                    ("st_dev","st_ino","st_size","st_mtime_ns","st_nlink"))):raise ValueError()
                return raw
    except Exception:
        raise ValueError("REGISTERED_RESTORE_CONFIG_INVALID") from None


def _load_registered_restore_ports(path, *, master_key, key_version):
    """API-only 고정 서버 구성. 연결은 실행 시까지 지연하며 provisioning/DDL은 없다."""
    from psycopg.conninfo import conninfo_to_dict
    owned=[]
    def exact(value,fields):
        if type(value) is not dict or set(value)!=set(fields.split()):raise ValueError()
    def identity(value):
        if type(value) is not str or re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._:-]{0,127}",value) is None:raise ValueError()
        return value
    def digest(value):
        if type(value) is not str or re.fullmatch(r"[0-9a-f]{64}",value) is None:raise ValueError()
        return value
    def ref(value):
        if type(value) is not str or not value or len(value)>4096:raise ValueError()
        item=Path(value)
        if not item.is_absolute() or ".." in item.parts or item==Path(item.anchor):raise ValueError()
        return item
    def unique(pairs):
        result={}
        for key,value in pairs:
            if key in result:raise ValueError()
            result[key]=value
        return result
    def secret(value,limit):
        raw=_read_restore_protected_file(ref(value),limit)
        text=raw.decode("utf-8").removesuffix("\n").removesuffix("\r")
        if not text or any(ord(c)<32 or ord(c)==127 for c in text):raise ValueError()
        return text
    def dsn(value,name):
        text=secret(value,16384); fields=conninfo_to_dict(text)
        # 외부 service 파일/PGOPTIONS·다중host 해석을 새 신뢰경계로 만들지 않는다.
        allowed={"host","port","dbname","user","password","sslmode","sslrootcert","sslcert","sslkey","connect_timeout","application_name"}
        if (set(fields)-allowed or fields.get("dbname")!=name or not fields.get("host")
            or "," in fields["host"] or any(os.environ.get(key) is not None for key in
                ("PGHOSTADDR","PGSERVICE","PGSERVICEFILE","PGOPTIONS"))):raise ValueError()
        return text
    def provider(pool):
        lock=threading.Lock()
        @contextmanager
        def connection():
            with lock:
                if pool.closed:pool.open(wait=False)
            with pool.connection(timeout=2) as item:
                # repeatable-read probe/app materializer가 각자의 최상위 Tx를 소유한다.
                yield item
        return connection
    try:
        identity(key_version)
        if type(master_key) is not bytes or len(master_key)<32:raise ValueError()
        document=json.loads(_read_restore_protected_file(path,1024*1024),object_pairs_hook=unique,
            parse_constant=lambda _:(_ for _ in ()).throw(ValueError()))
        exact(document,"schema_version source_instance_ref registry_root instances targets")
        if type(document["schema_version"]) is not int or document["schema_version"]!=1:raise ValueError()
        source=identity(document["source_instance_ref"]);registry_root=ref(document["registry_root"])
        for name in ("instances","targets"):
            if type(document[name]) is not list or not 1<=len(document[name])<=64:raise ValueError()
        records={}
        for row in document["instances"]:
            exact(row,"instance_ref database_ref bucket_ref database_name operator_dsn_file app_dsn_file object_endpoint object_bucket object_region object_secure object_access_key_file object_secret_key_file provisioning_ref foundation application_enabled outbound_enabled")
            name=identity(row["instance_ref"])
            if name in records:raise ValueError()
            for field in ("database_ref","bucket_ref","object_region"):identity(row[field])
            db=row["database_name"]
            if type(db) is not str or not 1<=len(db.encode("utf-8"))<=63 or any(ord(c)<32 or ord(c)==127 for c in db):raise ValueError()
            for field in ("operator_dsn_file","object_access_key_file","object_secret_key_file"):ref(row[field])
            for field in ("object_secure","application_enabled","outbound_enabled"):
                if type(row[field]) is not bool:raise ValueError()
            if name==source:
                if any(row[field] is not None for field in ("app_dsn_file","provisioning_ref","foundation")):raise ValueError()
            else:
                ref(row["app_dsn_file"]);identity(row["provisioning_ref"])
                if row["application_enabled"] or row["outbound_enabled"]:raise ValueError()
                foundation=row["foundation"]
                exact(foundation,"tenant_id workspace_id actor_id schema_revision studio_defaults_protection_digest")
                for field in ("tenant_id","workspace_id","actor_id","schema_revision"):identity(foundation[field])
                digest(foundation["studio_defaults_protection_digest"])
            records[name]=row
        if source not in records:raise ValueError()
        keys=set();target_refs=set()
        for target in document["targets"]:
            exact(target,"registry_generation_id destination_id destination_version target_instance_ref owner_operation_id catalog_digest")
            for field in ("registry_generation_id","destination_id","target_instance_ref","owner_operation_id"):identity(target[field])
            version=target["destination_version"]
            if type(version) is not int or not 1<=version<=2147483647:raise ValueError()
            digest(target["catalog_digest"])
            key=(target["registry_generation_id"],target["destination_id"],version)
            if key in keys or target["target_instance_ref"] not in records or target["target_instance_ref"]==source:raise ValueError()
            keys.add(key);target_refs.add(target["target_instance_ref"])
        if target_refs!=set(records)-{source}:raise ValueError()
        registry=SignedDestinationRegistry(registry_root,{key_version:master_key},source)
        generation,entries=registry._read()  # 미지정 OFF와 잘못된 지정을 구분. 네트워크 읽기0.
        for target in document["targets"]:
            row=records[target["target_instance_ref"]]
            matching=[entry for entry in entries if (generation,entry["destination_id"],entry["version"])==
                (target["registry_generation_id"],target["destination_id"],target["destination_version"])]
            if len(matching)!=1:raise ValueError()
            entry=matching[0];foundation=row["foundation"]
            if (entry["capture_kind"]!="selected-source-lineage-v3" or tuple(entry[field] for field in
                ("source_instance_ref","target_instance_ref","database_ref","bucket_ref","owner_operation_id","authorized_tenant_id","authorized_workspace_id","authorized_actor_id"))!=
                (source,row["instance_ref"],row["database_ref"],row["bucket_ref"],target["owner_operation_id"],foundation["tenant_id"],foundation["workspace_id"],foundation["actor_id"])):raise ValueError()
        instances={};queues={}
        for name,row in records.items():
            operator=dsn(row["operator_dsn_file"],row["database_name"])
            pool=ConnectionPool(conninfo=operator,min_size=0,max_size=2,kwargs={"autocommit":False},timeout=2,reconnect_timeout=5,open=False)
            owned.append(pool)
            access=secret(row["object_access_key_file"],4096);key=secret(row["object_secret_key_file"],4096)
            if any(c.isspace() for c in access+key):raise ValueError()
            storage=MinioObjectStorageAdapter(endpoint=row["object_endpoint"],bucket=row["object_bucket"],
                access_key=access,secret_key=key,secure=row["object_secure"])
            # 전용 client만 소유한다. 공유 source adapter는 닫지 않는다.
            class OwnedHttp:
                def __init__(self,http):self.http=http
                def close(self):self.http.clear()
            owned.append(OwnedHttp(storage._client._http))
            instances[name]=PhysicalProbeInstance(row["database_ref"],row["bucket_ref"],row["database_name"],provider(pool),storage,
                row["object_region"],None if row["foundation"] is None else ProbeFoundation(**row["foundation"]),
                row["provisioning_ref"],row["application_enabled"],row["outbound_enabled"])
            if name!=source:
                queue=PostgresObjectQueueStore(dsn(row["app_dsn_file"],row["database_name"]),min_size=0)
                owned.append(queue);queues[name]=queue
        probe=PostgresMinioPhysicalProbe(instances)
        descriptors={}
        for row in document["targets"]:
            name=row["target_instance_ref"];instance=instances[name]
            request=ProbeRequest(source,name,instance.database_ref,instance.bucket_ref,row["owner_operation_id"])
            descriptors[(row["registry_generation_id"],row["destination_id"],row["destination_version"])]=RegisteredTargetDescriptor(instance,queues[name],request,row["catalog_digest"])
        return registry,probe,PostgresRegisteredTargetFactory(descriptors,probe),tuple(owned)
    except BaseException as error:
        for resource in reversed(owned):
            try:resource.close()
            except Exception:pass
        if not isinstance(error,Exception):raise
        raise ValueError("REGISTERED_RESTORE_CONFIG_INVALID") from None


class PostgresRegisteredTargetFactory:
    """서버 고정 descriptor만 소비. 새 pool/자격/endpoint 생성0.

    queue/provider/storage의 수명은 주입한 서비스 소유자에게 있다. 요청마다 공유
    pool을 닫지 않으며 target facade/uncertain 소유 child 참조는 이 factory에 보존한다.
    서비스 종료 때 소유자가 기존 queue.close를 정확1회 수행해야 한다.
    """
    def __init__(self,descriptors,probe):
        if not isinstance(descriptors,dict) or not 1<=len(descriptors)<=1000 or not isinstance(probe,TrustedPhysicalProbe):
            raise RecoveryError("RESOURCE_UNAVAILABLE",503)
        for key,value in descriptors.items():
            if (not isinstance(key,tuple) or len(key)!=3 or type(key[2]) is not int or key[2]<1 or type(value) is not RegisteredTargetDescriptor
                or type(value.instance) is not PhysicalProbeInstance or not isinstance(value.queue,PostgresObjectQueueStore)
                or type(value.probe_request) is not ProbeRequest or not callable(value.instance.connection_provider)
                or value.instance.application_enabled is not False or value.instance.outbound_enabled is not False or value.instance.foundation is None
                or type(value.catalog_digest) is not str or re.fullmatch(r"[0-9a-f]{64}",value.catalog_digest) is None):raise RecoveryError("RESOURCE_UNAVAILABLE",503)
            for identity in (*key[:2],value.instance.region):_safe(identity)
            if ((value.probe_request.database_ref,value.probe_request.bucket_ref)!=(value.instance.database_ref,value.instance.bucket_ref)
                or value.probe_request.source_instance_ref==value.probe_request.target_instance_ref):raise RecoveryError("RESOURCE_UNAVAILABLE",503)
        self._descriptors=MappingProxyType(dict(descriptors));self._probe=probe;self._targets={};self._lock=threading.Lock()

    def __call__(self,context,destination):
        from .recovery_lineage_restore import PostgresLineageTarget
        key=(destination.registry_generation_id,destination.destination_id,destination.version)
        descriptor=self._descriptors.get(key)
        if descriptor is None:raise RecoveryError("RESOURCE_UNAVAILABLE",503)
        instance=descriptor.instance;foundation=instance.foundation
        if ((foundation.tenant_id,foundation.workspace_id,foundation.actor_id)!=(context.tenant_id,context.workspace_id,context.actor_id)
            or (destination.tenant_id,destination.workspace_id,destination.actor_id)!=(context.tenant_id,context.workspace_id,context.actor_id)
            or descriptor.probe_request.owner_operation_id!=destination.owner_operation_id):raise RecoveryError("CURRENT_ACCESS_DENIED",403)
        # 정상 queue도 operator DB와 동일 실체인지 매번 확인. SUPER 쓰기/다른DB 오주입0.
        cloud=CloudAccessContext(context.tenant_id,context.workspace_id,context.actor_id,"object.write")
        with descriptor.queue._transaction(cloud) as connection:
            row=connection.execute("SELECT current_user,r.rolsuper,r.rolbypassrls,current_database(),c.system_identifier::text,d.oid::bigint "
                "FROM pg_catalog.pg_control_system() c CROSS JOIN pg_catalog.pg_database d JOIN pg_catalog.pg_roles r ON r.rolname=current_user "
                "WHERE d.datname=current_database()").fetchone()
            if (row is None or len(row)!=6 or tuple(row[:4])!=("daon_app",False,False,instance.database_name)
                or DatabaseIdentity(row[4],row[5]).fingerprint()!=destination.target_database_fingerprint):raise RecoveryError("RESOURCE_UNAVAILABLE",503)
        with self._lock:
            target=self._targets.get(key)
            if target is None:
                target=PostgresLineageTarget(instance.connection_provider,descriptor.queue,instance.storage,
                    probe=self._probe,probe_request=descriptor.probe_request,region=instance.region)
                self._targets[key]=target
            return target,descriptor.catalog_digest


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
        destination_registry=None,
        destination_probe=None,
        registered_target_factory=None,
        owned_restore_resources=(),
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
        ports=(destination_registry,destination_probe,registered_target_factory)
        if any(port is not None for port in ports) and (not isinstance(destination_registry,SignedDestinationRegistry)
            or not isinstance(destination_probe,TrustedPhysicalProbe) or not callable(registered_target_factory)):
            raise ValueError("REGISTERED_RESTORE_PORTS_UNAVAILABLE")
        self._destination_registry,self._destination_probe,self._registered_target_factory=ports
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
        self._owned_restore_resources=tuple(owned_restore_resources)
        self._close_lock=threading.Lock()
        self._closed=False

    def _ensure_open(self) -> None:
        if not self._pool.closed:
            return
        with self._open_lock:
            if self._pool.closed:
                self._pool.open(wait=False)

    def close(self) -> None:
        with self._close_lock:
            if self._closed:return
            self._closed=True
        failed=False
        for resource in (self._pool,*reversed(self._owned_restore_resources)):
            try:resource.close()
            except Exception:failed=True
        if failed:raise RuntimeError("REGISTERED_RESTORE_CLOSE_FAILED") from None

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
        if getattr(self,"_destination_registry",None) is not None:
            registered=self._select_registered_restore(connection,request_id)
            if registered is not None: return registered
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

    def _require_registered_ports(self):
        registry=getattr(self,"_destination_registry",None)
        probe=getattr(self,"_destination_probe",None)
        factory=getattr(self,"_registered_target_factory",None)
        if (not isinstance(registry,SignedDestinationRegistry) or not isinstance(probe,TrustedPhysicalProbe)
            or not callable(factory) or getattr(self,"_lineage_capture_store",None) is None):
            raise RecoveryError("RESOURCE_UNAVAILABLE",503)
        return registry,probe,factory

    @staticmethod
    def _registered_authorization(connection,context,scope,*,purged_source_versions=(),defer_special=False):
        """원 archive actor는 provenance다. 모든 현재 검사는 실제 현재 주체로만 수행."""
        if (context.tenant_id,context.workspace_id)!=(scope.tenant_id,scope.workspace_id):
            raise RecoveryError("CURRENT_ACCESS_DENIED",403)
        from .open_law_source_access import _AuthReadConnection
        from .notification_events import _NotificationTupleReadConnection
        connection=_NotificationTupleReadConnection(connection)
        current=SimpleNamespace(tenant_id=context.tenant_id,workspace_id=context.workspace_id,actor_id=context.actor_id,
            notebook_id=scope.notebook_id,trace_id=context.trace_id,policy_version=context.policy_version)
        try:
            grant=_current_auth_grant(current,connection,Action.POLICY_MANAGE)
            auth=_AuthReadConnection(connection)
            workspace=auth.execute("SELECT * FROM auth_workspaces WHERE tenant_id=? AND workspace_id=?",
                (context.tenant_id,context.workspace_id)).fetchone()
            if workspace is None: raise RecoveryError("CURRENT_ACCESS_DENIED",403)
            if workspace["workspace_kind"]=="organization":
                OrganizationAccessBridge(postgres=True).assert_organization_active(connection=auth,
                    tenant_id=context.tenant_id,user_id=context.actor_id)
            for identity in scope.root_source_version_ids+scope.dependency_source_version_ids:
                # 정확 purge 완료 원장으로 제외된 Version은 materialize하지 않는다.
                # 그 경우에도 명시 SourceACL deny는 유지하며 생존 Version의 특수 권한은 생략0.
                if not _source_access_allowed(connection,current,identity) or (not defer_special and identity not in purged_source_versions
                    and not _special_source_access_allowed(connection,current,identity)):
                    raise RecoveryError("CURRENT_ACCESS_DENIED",403)
            return dict(actor_id=grant.actor_id,tenant_id=grant.tenant_id,workspace_id=grant.workspace_id,action=grant.action.value,
                role=grant.role.value,role_scope=grant.role_scope.value,membership_version=grant.membership_version,
                acl_version=grant.acl_version,policy_version=grant.policy_version,
                permissions=sorted((p.permission.value,p.effective,p.locked_by,p.reason,p.policy_version) for p in grant.effective_permissions))
        except (OpenLawAccessError,OrganizationWorkflowError):
            raise RecoveryError("CURRENT_ACCESS_DENIED",403) from None

    def _registered_capture(self,context,backup_id,*,recheck):
        self._require_admin(context)
        if not callable(recheck): raise RecoveryError("RESOURCE_UNAVAILABLE",503)
        recheck()
        with self._transaction(context.tenant_id,context.workspace_id,context.actor_id) as connection:
            row=connection.execute("SELECT b.*,m.manifest_version,m.manifest_digest,m.encrypted_inventory,m.object_count "
                "FROM public.backup_records b JOIN public.backup_manifests m USING(tenant_id,workspace_id,backup_id) "
                "WHERE b.backup_id=%s ORDER BY m.manifest_version DESC LIMIT 1",(backup_id,)).fetchone()
            if row is None: raise RecoveryError("BACKUP_UNAVAILABLE",404)
            scope,receipt,_=self._capture_from_row(row,requested_backup_id=backup_id)
            if type(scope) is not LineageCaptureScope or row["state"]!="ready": raise RecoveryError("BACKUP_NOT_READY",409)
            self._registered_authorization(connection,context,scope,defer_special=True)
        # 원 archive scope/key/actor/AAD 그대로. source Object 원문 재조회0.
        verified=self._lineage_capture_store.verify(scope,expected_receipt=receipt)
        recheck()
        with self._transaction(context.tenant_id,context.workspace_id,context.actor_id) as connection:
            self._registered_current_decision(connection,context,scope,verified.metadata)
        return row,scope,receipt,verified

    def _registered_current_decision(self,connection,context,scope,metadata):
        """원archive와 별도인 현재 읽기 결정. 원본 bytes/경로를 읽거나 쓰지 않는다."""
        from .notification_events import _NotificationTupleReadConnection
        read=_NotificationTupleReadConnection(connection)
        versions={r["record_id"]:r for r in metadata["source_versions"]}
        if set(versions)!=set(scope.root_source_version_ids+scope.dependency_source_version_ids):
            raise RecoveryError("BACKUP_MANIFEST_INVALID",409)
        def collect():
            self._registered_authorization(connection,context,scope,defer_special=True)
            ids=sorted({r["source_id"] for r in versions.values()})
            deletions=_lineage_read(read,context,"deletion_requests","AND r.source_id=ANY(%s)",(ids,),order="r.request_id",maximum=10000)
            holds=_lineage_read(read,context,"legal_holds","AND r.source_id=ANY(%s)",(ids,),order="r.hold_id",maximum=10000)
            for item in deletions:
                if (item["source_id"] not in ids or type(item["source_active"]) is not bool or type(item["purge_started"]) is not bool
                    or type(item["version"]) is not int or item["version"]<1):raise RecoveryError("BACKUP_MANIFEST_INVALID",409)
            purged={r["source_id"] for r in deletions if r["state"]=="purged" and r["source_active"] is False and r["purge_started"] is True}
            purged_versions={identity for identity,r in versions.items() if r["source_id"] in purged}
            authorization=self._registered_authorization(connection,context,scope,purged_source_versions=purged_versions)
            current_roots=set(scope.root_source_version_ids)-purged_versions
            notebook=_lineage_one(_lineage_read(read,context,"notebooks","AND r.notebook_id=%s AND NOT EXISTS("
                "SELECT 1 FROM public.notebook_deletion_requests d WHERE d.tenant_id=r.tenant_id AND d.workspace_id=r.workspace_id "
                "AND d.notebook_id=r.notebook_id AND d.state IN ('accepted','deleting','completed'))",
                (scope.notebook_id,),order="r.notebook_id",maximum=1))
            notebook_meta=_lineage_one(_lineage_read(read,context,"notebook_metadata_versions","AND r.notebook_id=%s AND r.is_current=true",
                (scope.notebook_id,),order="r.version",maximum=1))
            bindings=_lineage_read(read,context,"notebook_bindings","AND r.notebook_id=%s AND r.binding_kind='source' AND r.version_id=ANY(%s) "
                "AND NOT EXISTS(SELECT 1 FROM public.notebook_source_unbindings u WHERE u.tenant_id=r.tenant_id AND u.workspace_id=r.workspace_id "
                "AND u.notebook_id=r.notebook_id AND u.source_id=r.record_id AND u.source_version_id=r.version_id)",
                (scope.notebook_id,sorted(current_roots)),order="r.version_id,r.record_id",maximum=100)
            if ({r["version_id"] for r in bindings}!=current_roots or len(bindings)!=len(current_roots)
                or any(r["record_id"]!=versions[r["version_id"]]["source_id"] for r in bindings)):
                raise RecoveryError("BACKUP_UNAVAILABLE",404)
            sources={}
            for identity,original in versions.items():
                if identity in purged_versions:continue  # exact 원장+원archive 결속으로 제외. current row 존재를 새 허용 근거로 쓰지 않는다.
                current=_lineage_one(_lineage_read(read,context,"source_versions","AND r.record_id=%s AND NOT EXISTS("
                    "SELECT 1 FROM public.notebook_source_unbindings u WHERE u.tenant_id=r.tenant_id AND u.workspace_id=r.workspace_id "
                    "AND u.notebook_id=%s AND u.source_id=r.source_id AND u.source_version_id=r.record_id)",
                    (identity,scope.notebook_id),order="r.record_id",maximum=1))
                _lineage_canon(current)
                if current!=original: raise RecoveryError("BACKUP_MANIFEST_INVALID",409)
                source_id=original["source_id"]
                if source_id not in sources:
                    source=_lineage_one(_lineage_read(read,context,"sources","AND r.record_id=%s",(source_id,),order="r.record_id",maximum=1))
                    _lineage_canon(source,mutable=True)
                    if source["record_id"]!=source_id: raise RecoveryError("BACKUP_UNAVAILABLE",404)
                    sources[source_id]=source
            blocked={k:set() for k in ("purged","held","tombstoned")}
            for row in deletions:
                if row["source_id"] not in ids or type(row["source_active"]) is not bool or type(row["purge_started"]) is not bool:
                    raise RecoveryError("BACKUP_MANIFEST_INVALID",409)
                if row["state"]=="purged" or row["purge_started"]: blocked["purged"].add(row["source_id"])
                elif not row["source_active"] or row["state"] in {"deactivated","grace_period","cleanup_pending","blocked_by_hold"}:
                    blocked["tombstoned"].add(row["source_id"])
            for row in holds:
                if row["source_id"] not in ids or row["state"] not in {"active","released"}: raise RecoveryError("BACKUP_MANIFEST_INVALID",409)
                if row["state"]=="active": blocked["held"].add(row["source_id"])
            for identity,source in sources.items():
                if source["state"] in {"expired","disabled","deleting","deleted"}: blocked["tombstoned"].add(identity)
            included=[]; reasons=[]
            for root in scope.root_source_version_ids:
                dependencies=set(); identity=root
                while identity is not None:
                    if identity in dependencies or identity not in versions: raise RecoveryError("BACKUP_MANIFEST_INVALID",409)
                    dependencies.add(identity); identity=versions[identity]["previous_version_id"]
                source_ids={versions[i]["source_id"] for i in dependencies}
                reason=next((k for k in ("purged","held","tombstoned") if source_ids & blocked[k]),None)
                if reason is None: included.append(root)
                else: reasons.append([root,reason])
            if not included: raise RecoveryError("BACKUP_NOT_READY",409)
            decision=dict(current_policy_version=authorization["policy_version"],
                current_authorization_fingerprint=hashlib.sha256(_lineage_json(authorization)).hexdigest(),included_roots=sorted(included),
                excluded_roots=sorted(r[0] for r in reasons),exclusion_reasons=sorted(reasons))
            decision["current_retention_watermark"]=hashlib.sha256(_lineage_json(dict(tenant_id=context.tenant_id,
                workspace_id=context.workspace_id,actor_id=context.actor_id,notebook=notebook,notebook_metadata=notebook_meta,
                bindings=bindings,sources=[sources[i] for i in sorted(sources)],deletions=deletions,holds=holds,authorization=authorization,decision=decision))).hexdigest()
            return decision
        first=collect()
        if collect()!=first: raise RecoveryError("RESTORE_VERSION_CONFLICT",409)
        return first

    def _registered_preview_cipher(self,row):
        store=self._lineage_capture_store
        bound={key:row[key] for key in ("tenant_id","workspace_id","actor_id","request_id","backup_id","preview_version",
            "registry_generation_id","destination_id","destination_version","source_manifest_digest","closure_fingerprint",
            "source_policy_version","retention_watermark","included_roots_fingerprint","excluded_roots_fingerprint")}
        bound.update(purpose="selected-source-lineage-restore-preview-v1",key_version=store.key_version,key_reference=store.key_reference,
            expires_at=row["expires_at"].astimezone(UTC).isoformat())
        aad=_lineage_json(bound)
        purpose=b"selected-source-lineage-restore-preview-v1"
        key=HKDF(algorithm=hashes.SHA256(),length=32,salt=hashlib.sha256(purpose).digest(),
            info=purpose+b"/"+store.key_version.encode()+b"/"+store.key_reference.encode()).derive(self._manifest_master_key)
        return AESGCM(key),aad

    @staticmethod
    def _validate_registered_preview(row,payload):
        try:
            if type(payload) is not dict or set(payload)!={"schema_version","current_retention_watermark","current_policy_version",
                "current_authorization_fingerprint","runtime_policy_version","included_roots","excluded_roots","exclusion_reasons","session_id","device_id","request_version","destination_summary","catalog_digest","created_at"}:
                raise ValueError()
            if type(payload["schema_version"]) is not int or payload["schema_version"]!=1: raise ValueError()
            for key in ("current_retention_watermark","catalog_digest","current_authorization_fingerprint"):
                if type(payload[key]) is not str or re.fullmatch(r"[0-9a-f]{64}",payload[key]) is None: raise ValueError()
            for key in ("current_policy_version","runtime_policy_version","session_id","device_id"): _safe(payload[key])
            if type(payload["request_version"]) is not int or payload["request_version"]<2: raise ValueError()
            included,excluded=payload["included_roots"],payload["excluded_roots"]
            if (type(included) is not list or type(excluded) is not list or not included or len(included)+len(excluded)>100
                or included!=sorted(set(included)) or excluded!=sorted(set(excluded)) or set(included)&set(excluded)): raise ValueError()
            for identity in included+excluded: _safe_canon_id(identity)
            for values,key in ((included,"included_roots_fingerprint"),(excluded,"excluded_roots_fingerprint")):
                if hashlib.sha256(_lineage_json(values)).hexdigest()!=row[key]: raise ValueError()
            reasons=payload["exclusion_reasons"]
            if (type(reasons) is not list or any(type(r) is not list or len(r)!=2 or r[1] not in {"purged","held","tombstoned"} for r in reasons)
                or reasons!=sorted(reasons) or [r[0] for r in reasons]!=excluded): raise ValueError()
            summary=payload["destination_summary"]
            if (type(summary) is not dict or set(summary)!={"destination_id","version","display_name","expires_at"}
                or summary["destination_id"]!=row["destination_id"] or type(summary["version"]) is not int or summary["version"]!=row["destination_version"]
                or type(summary["display_name"]) is not str or not 1<=len(summary["display_name"])<=80
                or any(ord(c)<32 or ord(c)==127 or c in "<>:/\\" for c in summary["display_name"])): raise ValueError()
            created=datetime.fromisoformat(payload["created_at"].replace("Z","+00:00"))
            expires=datetime.fromisoformat(summary["expires_at"].replace("Z","+00:00"))
            if created.tzinfo is None or expires.tzinfo is None or not created<row["expires_at"]<=expires: raise ValueError()
            return payload
        except (RecoveryError,ValueError,TypeError,KeyError,AttributeError): raise RecoveryError("BACKUP_MANIFEST_INVALID",409) from None

    def _seal_registered_preview(self,row,payload):
        self._validate_registered_preview(row,payload)
        cipher,aad=self._registered_preview_cipher(row); nonce=secrets.token_bytes(12)
        return b"DRP1"+nonce+cipher.encrypt(nonce,_lineage_json(payload),aad)

    def _open_registered_preview(self,row,envelope):
        try:
            if not isinstance(envelope,bytes) or not 32<=len(envelope)<=1024*1024 or envelope[:4]!=b"DRP1": raise ValueError()
            cipher,aad=self._registered_preview_cipher(row)
            payload=json.loads(cipher.decrypt(envelope[4:16],envelope[16:],aad))
            return self._validate_registered_preview(row,payload)
        except (InvalidTag,ValueError,TypeError,KeyError,AttributeError): raise RecoveryError("BACKUP_MANIFEST_INVALID",409) from None

    @staticmethod
    def _registered_preview_view(row,payload):
        PostgresRecoveryService._validate_registered_preview(row,payload)
        return RegisteredRestorePreviewView("selected_source_lineage",row["preview_version"],
            datetime.fromisoformat(payload["created_at"].replace("Z","+00:00")),row["expires_at"],dict(payload["destination_summary"]),
            row["source_manifest_digest"],row["closure_fingerprint"],len(payload["included_roots"]),len(payload["excluded_roots"]),
            {reason:sum(r[1]==reason for r in payload["exclusion_reasons"]) for reason in ("purged","held","tombstoned")})

    def _registered_restore_row(self,connection,request_id):
        return connection.execute("SELECT r.*,p.preview_version,p.registry_generation_id,p.destination_id,p.destination_version,"
            "p.source_manifest_digest,p.closure_fingerprint,p.source_policy_version,p.retention_watermark,"
            "p.included_roots_fingerprint,p.excluded_roots_fingerprint,p.encrypted_preview_receipt,p.expires_at,"
            "d.owner_operation_id,d.physical_receipt_digest,d.target_database_fingerprint,d.target_bucket_fingerprint,"
            "v.verification_digest FROM public.restore_requests r JOIN LATERAL (SELECT * FROM public.registered_restore_previews p0 "
            "WHERE (p0.tenant_id,p0.workspace_id,p0.request_id)=(r.tenant_id,r.workspace_id,r.request_id) ORDER BY p0.preview_version DESC LIMIT 1) p ON true "
            "JOIN public.registered_restore_target_reservations d USING(tenant_id,workspace_id,request_id) "
            "LEFT JOIN LATERAL (SELECT verification_digest FROM public.restore_verifications v0 WHERE "
            "(v0.tenant_id,v0.workspace_id,v0.request_id)=(r.tenant_id,r.workspace_id,r.request_id) ORDER BY v0.created_at DESC LIMIT 1) v ON true "
            "WHERE r.request_id=%s AND r.actor_id=nullif(current_setting('app.actor_id',true),'')",(request_id,)).fetchone()

    def _select_registered_restore(self,connection,request_id):
        row=self._registered_restore_row(connection,request_id)
        if row is None: return None
        payload=self._open_registered_preview(row,bytes(row["encrypted_preview_receipt"]))
        preview=self._registered_preview_view(row,payload)
        return RestoreRequestView(row["request_id"],row["backup_id"],row["tenant_id"],row["workspace_id"],row["state"],row["version"],
            preview,self._restore_transitions(row["state"]),row["verification_digest"])

    @contextmanager
    def _registered_preview_transaction(self,context):
        with self._transaction(context.tenant_id,context.workspace_id,context.actor_id) as connection:
            try:yield connection
            except UniqueViolation:
                # 보이지 않거나 동시에 확정된 다른 예약도 덮지 않는다. 전체 Tx rollback.
                raise RecoveryError("RESTORE_VERSION_CONFLICT",409,retryable=False) from None

    def preview_registered_restore(self,context,backup_id,*,destination_id,destination_version,idempotency_key,step_up_verified,recheck):
        self._require_step_up(context,step_up_verified)
        registry,probe,factory=self._require_registered_ports()
        for value in (backup_id,destination_id,idempotency_key,context.session_id,context.device_id): _safe(value)
        if type(destination_version) is not int or not 0<destination_version<=2147483647: raise RecoveryError("RECOVERY_INPUT_INVALID",400)
        backup,scope,receipt,verified=self._registered_capture(context,backup_id,recheck=recheck)
        destination=registry.resolve_registered_destination(context,destination_id,destination_version,probe)
        if (destination.destination_id,destination.version)!=(destination_id,destination_version): raise RecoveryError("RESTORE_VERSION_CONFLICT",409)
        with self._transaction(context.tenant_id,context.workspace_id,context.actor_id) as connection:
            decision=self._registered_current_decision(connection,context,scope,verified.metadata)
        fingerprint=_fingerprint(("registered-lineage-preview-v1",backup_id,backup["manifest_digest"],scope.closure_fingerprint,
            context.tenant_id,context.workspace_id,context.actor_id,context.session_id,context.device_id,context.policy_version,
            destination.registry_generation_id,destination_id,destination_version,destination.physical_receipt_digest,decision))
        from .recovery_lineage_restore import RestoreBinding,PostgresLineageTarget,_hash
        prepared=factory(context,destination)
        if (not isinstance(prepared,tuple) or len(prepared)!=2 or not isinstance(prepared[0],PostgresLineageTarget)
            or type(prepared[1]) is not str or re.fullmatch(r"[0-9a-f]{64}",prepared[1]) is None): raise RecoveryError("RESOURCE_UNAVAILABLE",503)
        target,catalog_digest=prepared
        recheck()
        with self._registered_preview_transaction(context) as connection:
            current=self._registered_current_decision(connection,context,scope,verified.metadata)
            if current!=decision: raise RecoveryError("RESTORE_VERSION_CONFLICT",409)
            replay=connection.execute("SELECT request_id,request_fingerprint FROM public.restore_requests WHERE actor_id=%s AND idempotency_key=%s",
                (context.actor_id,idempotency_key)).fetchone()
            if replay is not None:
                if replay["request_fingerprint"]!=fingerprint: raise RecoveryError("IDEMPOTENCY_KEY_REUSED",409)
                view=self._select_registered_restore(connection,replay["request_id"])
                if view is None or view.preview.version!=1: raise RecoveryError("RESTORE_VERSION_CONFLICT",409)
                recheck();return view
            confirmation=connection.execute("SELECT request_fingerprint,result FROM idempotency_records "
                "WHERE actor_id=%s AND operation=%s AND idempotency_key=%s",(context.actor_id,"registered-restore-preview",idempotency_key)).fetchone()
            if confirmation is not None:
                result=confirmation["result"]
                if (confirmation["request_fingerprint"]!=fingerprint or type(result) is not dict
                    or set(result)!={"request_id","preview_version"}):raise RecoveryError("IDEMPOTENCY_KEY_REUSED",409)
                view=self._select_registered_restore(connection,result["request_id"])
                if view is None or view.preview.version!=result["preview_version"]:raise RecoveryError("RESTORE_VERSION_CONFLICT",409)
                recheck();return view
            reserved=connection.execute("SELECT * FROM public.registered_restore_target_reservations "
                "WHERE registry_generation_id=%s AND destination_id=%s",(destination.registry_generation_id,destination_id)).fetchone()
            request_id="restore-"+secrets.token_hex(12) if reserved is None else reserved["request_id"]
            parent=None;preview_version=1;request_version=2
            if reserved is not None:
                expected=dict(registry_generation_id=destination.registry_generation_id,destination_id=destination_id,tenant_id=context.tenant_id,
                    workspace_id=context.workspace_id,request_id=request_id,actor_id=context.actor_id,destination_version=destination_version,backup_id=backup_id,
                    manifest_version=3,source_manifest_digest=backup["manifest_digest"],physical_receipt_digest=destination.physical_receipt_digest,
                    target_database_fingerprint=destination.target_database_fingerprint,target_bucket_fingerprint=destination.target_bucket_fingerprint,
                    owner_operation_id=destination.owner_operation_id)
                if any(reserved.get(key)!=value for key,value in expected.items()):raise RecoveryError("RESTORE_VERSION_CONFLICT",409)
                # Execute와 같은 요청별 소유 guard. 이미 실행 중이면 기존 parent/phase를 건드리지 않는다.
                acquired=connection.execute("SELECT pg_try_advisory_xact_lock(%s) AS owned",(self._registered_execution_key(context,request_id),)).fetchone()
                if acquired is None or acquired.get("owned") is not True:raise RecoveryError("RESTORE_STATE_INVALID",409,retryable=False)
                parent=connection.execute("SELECT * FROM public.restore_requests WHERE request_id=%s AND actor_id=%s FOR UPDATE",(request_id,context.actor_id)).fetchone()
                if (parent is None or (parent["tenant_id"],parent["workspace_id"],parent["actor_id"],parent["backup_id"],parent["state"])
                    !=(context.tenant_id,context.workspace_id,context.actor_id,backup_id,"preview_ready")):raise RecoveryError("RESTORE_STATE_INVALID",409)
                # 동시 새 확인이 먼저 commit됐어도 같은 key를 다른 version에 재사용하지 않는다.
                confirmation=connection.execute("SELECT request_fingerprint,result FROM idempotency_records "
                    "WHERE actor_id=%s AND operation=%s AND idempotency_key=%s",(context.actor_id,"registered-restore-preview",idempotency_key)).fetchone()
                if confirmation is not None:raise RecoveryError("RESTORE_VERSION_CONFLICT",409)
                present=connection.execute("SELECT EXISTS(SELECT 1 FROM public.registered_restore_phase_receipts "
                    "WHERE tenant_id=%s AND workspace_id=%s AND request_id=%s) AS present",(context.tenant_id,context.workspace_id,request_id)).fetchone()
                if present is None or present.get("present") is not False:raise RecoveryError("RESTORE_STATE_INVALID",409)
                previous=self._registered_restore_row(connection,request_id)
                if previous is None:raise RecoveryError("RESTORE_VERSION_CONFLICT",409)
                self._open_registered_preview(previous,bytes(previous["encrypted_preview_receipt"]))
                preview_version=previous["preview_version"]+1;request_version=parent["version"]
            now=self._clock()
            expires=min(now+timedelta(minutes=10),datetime.fromisoformat(destination.expires_at.replace("Z","+00:00")))
            if expires<=now: raise RecoveryError("RESTORE_VERSION_CONFLICT",409)
            row=dict(tenant_id=context.tenant_id,workspace_id=context.workspace_id,request_id=request_id,preview_version=preview_version,actor_id=context.actor_id,
                registry_generation_id=destination.registry_generation_id,destination_id=destination_id,destination_version=destination_version,
                backup_id=backup_id,manifest_version=3,source_manifest_digest=backup["manifest_digest"],closure_fingerprint=scope.closure_fingerprint,
                included_roots_fingerprint=_hash(decision["included_roots"]),excluded_roots_fingerprint=_hash(decision["excluded_roots"]),
                source_policy_version=scope.policy_version,retention_watermark=scope.retention_watermark,expires_at=expires)
            binding=RestoreBinding(request_id,backup_id,row["source_manifest_digest"],_hash(receipt),scope.closure_fingerprint,scope.retention_watermark,
                preview_version,tuple(decision["included_roots"]),destination.registry_generation_id,destination_id,destination_version,destination.owner_operation_id,
                "preview-"+secrets.token_hex(12),idempotency_key,expires,catalog_digest,decision["current_retention_watermark"])
            target.check(context,destination,binding,fresh=True)
            recheck()
            if self._registered_current_decision(connection,context,scope,verified.metadata)!=decision: raise RecoveryError("RESTORE_VERSION_CONFLICT",409)
            payload=dict(schema_version=1,**decision,runtime_policy_version=context.policy_version,session_id=context.session_id,device_id=context.device_id,request_version=request_version,
                destination_summary=destination.summary(),catalog_digest=catalog_digest,created_at=now.isoformat())
            encrypted=self._seal_registered_preview(row,payload)
            audit=self._append_audit(connection,context,"restore.previewed","restore_request",request_id)
            if parent is None:
                connection.execute("INSERT INTO public.restore_requests "
                "(tenant_id,workspace_id,request_id,backup_id,actor_id,state,version,policy_version,trace_id,audit_event_id,idempotency_key,request_fingerprint,created_at,updated_at) "
                "VALUES (%s,%s,%s,%s,%s,'requested',1,%s,%s,%s,%s,%s,%s,%s)",
                (context.tenant_id,context.workspace_id,request_id,backup_id,context.actor_id,context.policy_version,context.trace_id,audit,idempotency_key,fingerprint,now,now))
                connection.execute("INSERT INTO public.restore_request_locator (tenant_id,request_id,workspace_id,created_at) VALUES (%s,%s,%s,%s)",
                    (context.tenant_id,request_id,context.workspace_id,now))
            reservation=dict(registry_generation_id=destination.registry_generation_id,destination_id=destination_id,tenant_id=context.tenant_id,
                workspace_id=context.workspace_id,request_id=request_id,actor_id=context.actor_id,destination_version=destination_version,backup_id=backup_id,
                manifest_version=3,source_manifest_digest=row["source_manifest_digest"],physical_receipt_digest=destination.physical_receipt_digest,
                target_database_fingerprint=destination.target_database_fingerprint,target_bucket_fingerprint=destination.target_bucket_fingerprint,
                owner_operation_id=destination.owner_operation_id)
            if parent is None:
                connection.execute("INSERT INTO public.registered_restore_target_reservations ("+",".join(reservation)+") VALUES ("+",".join(["%s"]*len(reservation))+")",tuple(reservation.values()))
            row["encrypted_preview_receipt"]=encrypted
            connection.execute("INSERT INTO public.registered_restore_previews ("+",".join(row)+") VALUES ("+",".join(["%s"]*len(row))+")",tuple(row.values()))
            recheck()
            if self._registered_current_decision(connection,context,scope,verified.metadata)!=decision: raise RecoveryError("RESTORE_VERSION_CONFLICT",409)
            if parent is None:
                connection.execute("UPDATE public.restore_requests SET state='preview_ready',version=version+1,updated_at=%s WHERE request_id=%s",(self._clock(),request_id))
            # 새 확인key는 이 정확version만 가리킨다. 과거 확인을 최신으로 치환하지 않는다.
            connection.execute("INSERT INTO idempotency_records "
                "(tenant_id,workspace_id,actor_id,operation,idempotency_key,request_fingerprint,result,status,expires_at) "
                "VALUES (%s,%s,%s,%s,%s,%s,%s,'completed',%s)",
                (context.tenant_id,context.workspace_id,context.actor_id,"registered-restore-preview",idempotency_key,fingerprint,
                    Jsonb(dict(request_id=request_id,preview_version=preview_version)),self._clock()+timedelta(hours=24)))
            return self._select_registered_restore(connection,request_id)

    def _registered_current_destination(self,context,row,payload):
        # 등록 후 쓰기가 시작된 target은 더 이상 empty가 아니다. 서명/세대/소유와
        # 물리 identity는 계속 검증하되 재등록/empty 위장/사용자 ref 입력은 하지 않는다.
        registry,probe,_=self._require_registered_ports()
        generation,entries=registry._read()
        matches=[e for e in entries if e["destination_id"]==row["destination_id"] and registry._eligible(context,e)]
        if generation!=row["registry_generation_id"] or len(matches)!=1: raise RecoveryError("RESTORE_VERSION_CONFLICT",409)
        entry=matches[0];registry._validate_entry(entry)
        now=self._clock();expires=datetime.fromisoformat(entry["expires_at"].replace("Z","+00:00"))
        created=datetime.fromisoformat(entry["created_at"].replace("Z","+00:00"))
        if (entry["version"]!=row["destination_version"] or not created<=now<expires
            or {k:entry[k] for k in ("destination_id","version","display_name","expires_at")}!=payload["destination_summary"]
            or entry["owner_operation_id"]!=row["owner_operation_id"] or entry["physical_receipt_digest"]!=row["physical_receipt_digest"]):
            raise RecoveryError("RESTORE_VERSION_CONFLICT",409)
        request=ProbeRequest(entry["source_instance_ref"],entry["target_instance_ref"],entry["database_ref"],entry["bucket_ref"],entry["owner_operation_id"])
        observed=probe.observe(request)
        now=self._clock()
        if (not created<=now<expires or now>=row["expires_at"] or observed.request!=request
            or not timedelta(0)<=now-observed.observed_at<=timedelta(seconds=30)
            or observed.owner_operation_id!=row["owner_operation_id"] or observed.application_enabled is not False or observed.outbound_enabled is not False
            or physical_receipt_digest(observed)!=row["physical_receipt_digest"]
            or observed.target_database.fingerprint()!=row["target_database_fingerprint"] or observed.target_object.fingerprint()!=row["target_bucket_fingerprint"]
            or observed.source_database.fingerprint()==observed.target_database.fingerprint()
            or observed.source_object.fingerprint()==observed.target_object.fingerprint()): raise RecoveryError("RESTORE_VERSION_CONFLICT",409)
        return RegisteredDestination(generation,row["destination_id"],entry["version"],entry["display_name"],entry["expires_at"],
            context.tenant_id,context.workspace_id,context.actor_id,row["physical_receipt_digest"],row["target_database_fingerprint"],
            row["target_bucket_fingerprint"],row["owner_operation_id"],observed.observed_at)

    def _registered_phase_journal(self,context,binding):
        from .notification_events import _NotificationTupleReadConnection
        from .recovery_lineage_restore import PostgresPhaseJournal
        @contextmanager
        def connection():
            with self._transaction(context.tenant_id,context.workspace_id,context.actor_id) as current:
                yield _NotificationTupleReadConnection(current)
        return PostgresPhaseJournal(connection)

    def registered_restore_step_up_preview(self,context,request_id,*,recheck):
        """발급 시에도 자기 보호 Preview와 실제 현재 자료 접근을 검사한다. target probe/쓰기0."""
        self._require_admin(context)
        recheck()
        with self._transaction(context.tenant_id,context.workspace_id,context.actor_id) as connection:
            row=self._registered_restore_row(connection,request_id)
            if row is None:raise RecoveryError("RESTORE_REQUEST_UNAVAILABLE",404)
            payload=self._open_registered_preview(row,bytes(row["encrypted_preview_receipt"]))
        if ((row["tenant_id"],row["workspace_id"],row["actor_id"])!=(context.tenant_id,context.workspace_id,context.actor_id)
            or (payload["session_id"],payload["device_id"])!=(context.session_id,context.device_id)
            or payload["runtime_policy_version"]!=context.policy_version):raise RecoveryError("CURRENT_ACCESS_DENIED",403)
        backup,scope,_,verified=self._registered_capture(context,row["backup_id"],recheck=recheck)
        if (backup["manifest_digest"]!=row["source_manifest_digest"] or scope.closure_fingerprint!=row["closure_fingerprint"]
            or scope.retention_watermark!=row["retention_watermark"] or scope.policy_version!=row["source_policy_version"]):
            raise RecoveryError("RESTORE_VERSION_CONFLICT",409)
        recheck()
        with self._transaction(context.tenant_id,context.workspace_id,context.actor_id) as connection:
            latest=self._registered_restore_row(connection,request_id)
            if latest is None:raise RecoveryError("RESTORE_REQUEST_UNAVAILABLE",404)
            if (latest["state"] not in {"preview_ready","authorized","restoring","verifying","completed"}
                or self._clock()>=latest["expires_at"]
                or latest["preview_version"]!=row["preview_version"]
                or self._open_registered_preview(latest,bytes(latest["encrypted_preview_receipt"]))!=payload):
                raise RecoveryError("RESTORE_VERSION_CONFLICT",409)
            current=self._registered_current_decision(connection,context,scope,verified.metadata)
            if any(current[key]!=payload[key] for key in current):raise RecoveryError("RESTORE_VERSION_CONFLICT",409)
            recheck()
            # READ COMMITTED의 추가 SELECT로 미검사 새 Preview를 바꿔 반환하지 않는다.
            return RestoreRequestView(latest["request_id"],latest["backup_id"],latest["tenant_id"],latest["workspace_id"],latest["state"],latest["version"],
                self._registered_preview_view(latest,payload),self._restore_transitions(latest["state"]),latest["verification_digest"])

    @staticmethod
    def _registered_execution_key(context,request_id):
        token=hashlib.sha256(_lineage_json(["registered-restore-execution-v1",context.tenant_id,context.workspace_id,request_id])).digest()
        return int.from_bytes(token[:8],signed=True)

    @contextmanager
    def _registered_execution_guard(self,context,request_id):
        # 전용 연결의 한 read-only 업무 guard Tx만 유지한다. parent/phase Tx와
        # 분리되어 있으며 business row lock/새 표/세션 advisory lock 누수는 없다.
        key=self._registered_execution_key(context,request_id)
        try:
            self._ensure_open()
            with self._pool.connection(timeout=2.0) as connection:
                with connection.transaction():
                    connection.execute("SET LOCAL ROLE daon_app")
                    row=connection.execute("SELECT pg_try_advisory_xact_lock(%s) AS owned,pg_backend_pid() AS owner_pid",(key,)).fetchone()
                    if row is None or row.get("owned") is not True:
                        if row is not None and row.get("owned") is False:raise RecoveryError("RESTORE_STATE_INVALID",409,retryable=False)
                        raise _RegisteredExecutionOwnershipLost()
                    owner=row.get("owner_pid")
                    if type(owner) is not int or owner<=0:raise _RegisteredExecutionOwnershipLost()
                    def current_owner():
                        try:
                            from psycopg.pq import TransactionStatus
                            if connection.info.transaction_status!=TransactionStatus.INTRANS:raise _RegisteredExecutionOwnershipLost()
                            actual=connection.execute("SELECT pg_backend_pid() AS owner_pid").fetchone()
                            if actual is None or actual.get("owner_pid")!=owner:raise _RegisteredExecutionOwnershipLost()
                        except (Error,OSError):raise _RegisteredExecutionOwnershipLost() from None
                    yield current_owner
        except RecoveryError:raise
        except (Error,PoolTimeout,OSError):raise _RegisteredExecutionOwnershipLost() from None

    def execute_registered_restore(self,context,request_id,*,expected_version,preview_version,idempotency_key,step_up_verified,recheck):
        self._require_step_up(context,step_up_verified)
        for value in (context.tenant_id,context.workspace_id,request_id):_safe(value)
        recheck()
        with self._registered_execution_guard(context,request_id) as owner:
            def current():
                owner()
                return recheck()
            return self._execute_registered_restore_owned(context,request_id,expected_version=expected_version,
                preview_version=preview_version,idempotency_key=idempotency_key,step_up_verified=step_up_verified,
                recheck=current,ownership_check=owner)

    def _execute_registered_restore_owned(self,context,request_id,*,expected_version,preview_version,idempotency_key,step_up_verified,recheck,ownership_check):
        self._require_step_up(context,step_up_verified)
        _,_,factory=self._require_registered_ports()
        for value in (request_id,idempotency_key,context.session_id,context.device_id):_safe(value)
        if type(expected_version) is not int or type(preview_version) is not int: raise RecoveryError("RESTORE_VERSION_CONFLICT",409)
        recheck()
        with self._transaction(context.tenant_id,context.workspace_id,context.actor_id) as connection:
            row=self._registered_restore_row(connection,request_id)
            if row is None: raise RecoveryError("RESTORE_REQUEST_UNAVAILABLE",404)
            payload=self._open_registered_preview(row,bytes(row["encrypted_preview_receipt"]))
        backup,scope,receipt,verified=self._registered_capture(context,row["backup_id"],recheck=recheck)
        if (backup["manifest_digest"]!=row["source_manifest_digest"] or scope.closure_fingerprint!=row["closure_fingerprint"]
            or scope.retention_watermark!=row["retention_watermark"] or scope.policy_version!=row["source_policy_version"]
            or (context.session_id,context.device_id)!=(payload["session_id"],payload["device_id"])
            or context.policy_version!=payload["runtime_policy_version"] or preview_version!=row["preview_version"]
            or row["expires_at"]<=self._clock()): raise RecoveryError("RESTORE_VERSION_CONFLICT",409)
        from .recovery_lineage_restore import (RestoreBinding,CurrentRestoreState,PostgresLineageTarget,MaterializationError,
            materialize_lineage,build_restore_plan,_hash)
        operation="restore-op-"+_hash([request_id,idempotency_key])[:32]
        fingerprint=_fingerprint(("registered-lineage-execute-v1",request_id,preview_version,payload["request_version"],context.policy_version,
            row["source_manifest_digest"],row["closure_fingerprint"],payload["current_retention_watermark"],row["registry_generation_id"],row["destination_id"],row["destination_version"]))
        binding=RestoreBinding(request_id,row["backup_id"],row["source_manifest_digest"],_hash(receipt),scope.closure_fingerprint,scope.retention_watermark,
            preview_version,tuple(payload["included_roots"]),row["registry_generation_id"],row["destination_id"],row["destination_version"],row["owner_operation_id"],
            operation,idempotency_key,row["expires_at"],payload["catalog_digest"],payload["current_retention_watermark"])
        destination=self._registered_current_destination(context,row,payload)
        prepared=factory(context,destination)
        if (not isinstance(prepared,tuple) or len(prepared)!=2 or not isinstance(prepared[0],PostgresLineageTarget)
            or prepared[1]!=binding.catalog_digest): raise RecoveryError("RESOURCE_UNAVAILABLE",503)
        target=prepared[0];journal=self._registered_phase_journal(context,binding)
        decision_keys=("current_retention_watermark","current_policy_version","current_authorization_fingerprint","included_roots","excluded_roots","exclusion_reasons")
        def current(connection=None):
            recheck()
            if self._clock()>=row["expires_at"]: raise RecoveryError("RESTORE_VERSION_CONFLICT",409)
            def read(active):
                # 외부 physical 관측이 지연되는 동안 바뀐 주체/권한/보존 결정을
                # 그 뒤에 다시 읽는다. 아래 쓰기에 probe 전 결정을 사용하지 않는다.
                self._registered_current_destination(context,row,payload)
                recheck()
                if self._clock()>=row["expires_at"]:raise RecoveryError("RESTORE_VERSION_CONFLICT",409)
                latest=self._registered_restore_row(active,request_id)
                if latest is None or latest["state"] not in {"preview_ready","authorized","restoring","verifying","completed"}:
                    raise RecoveryError("RESTORE_STATE_INVALID",409)
                if self._open_registered_preview(latest,bytes(latest["encrypted_preview_receipt"]))!=payload: raise RecoveryError("RESTORE_VERSION_CONFLICT",409)
                decision=self._registered_current_decision(active,context,scope,verified.metadata)
                if any(decision[k]!=payload[k] for k in decision_keys): raise RecoveryError("RESTORE_VERSION_CONFLICT",409)
                return CurrentRestoreState(context,binding,decision["current_retention_watermark"])
            if connection is not None:return read(connection)
            with self._transaction(context.tenant_id,context.workspace_id,context.actor_id) as active:return read(active)
        current()
        history=journal.load(context,binding,destination,scope)
        replayed=False
        with self._transaction(context.tenant_id,context.workspace_id,context.actor_id) as connection:
            parent=connection.execute("SELECT * FROM public.restore_requests WHERE request_id=%s AND actor_id=%s FOR UPDATE",(request_id,context.actor_id)).fetchone()
            if parent is None: raise RecoveryError("RESTORE_REQUEST_UNAVAILABLE",404)
            current(connection)  # 부모 lock을 얻기 전 새 Preview가 확정됐으면 옛 payload를 실행하지 않는다.
            replay=self._operation_replay(connection,context,"restore-execute",idempotency_key,fingerprint)
            if replay is not None:
                if replay!=request_id or parent["state"]!="completed":raise RecoveryError("RESTORE_STATE_INVALID",409)
                replayed=True
            elif parent["state"]=="preview_ready":
                if parent["version"]!=expected_version or history:raise RecoveryError("RESTORE_VERSION_CONFLICT",409)
                for state in ("authorized","restoring"):
                    connection.execute("UPDATE public.restore_requests SET state=%s,version=version+1,updated_at=%s WHERE request_id=%s",(state,self._clock(),request_id))
            elif parent["state"] in {"authorized","restoring","verifying"}:
                if (not history or any(h[5]!=operation or h[6]!=idempotency_key for h in history)
                    or expected_version not in {parent["version"],payload["request_version"]}):raise RecoveryError("RESTORE_VERSION_CONFLICT",409)
            else:raise RecoveryError("RESTORE_STATE_INVALID",409)
        try:
            result=materialize_lineage(context,scope,self._lineage_capture_store,receipt,binding,destination,target,journal,recheck=current,clock=self._clock)
        except RecoveryError:
            # 소유 연결이 불명확하면 다른 소유자의 parent를 실패로 바꾸지 않는다.
            # 소유가 확인된 원래 transport/권한/보존 실패 기록은 그대로 수행한다.
            ownership_check()
            # 외부 commit/소유 intent/uncertain은 삭제하지 않는다. source 실패 확정도 별도 Tx다.
            with self._transaction(context.tenant_id,context.workspace_id,context.actor_id) as connection:
                parent=connection.execute("SELECT * FROM public.restore_requests WHERE request_id=%s AND actor_id=%s FOR UPDATE",(request_id,context.actor_id)).fetchone()
                if parent is not None and parent["state"] in {"authorized","restoring","verifying"}:
                    connection.execute("UPDATE public.restore_requests SET state=%s,version=version+1,updated_at=%s WHERE request_id=%s",("failed",self._clock(),request_id))
                    event=self._append_audit(connection,context,"restore.failed","restore_request",request_id,outcome="failed",
                        before={"state":parent["state"],"version":parent["version"]},after={"state":"failed","version":parent["version"]+1},reason_code="RESTORE_PROCESSING_FAILED")
                    if self._notification_inbox_enabled is True:
                        from .notification_recovery import enqueue_terminal_recovery_event
                        enqueue_terminal_recovery_event(connection,context=context,origin_event_id=event,target_id=request_id)
            raise
        plan=build_restore_plan(scope,verified.metadata,binding.included_roots)
        history=journal.load(context,binding,destination,scope)
        lineage=(result.last_phase==7 and result.state=="materialized" and result.target_digest==plan.digest
            and result.provenance_digest==_hash([plan.metadata["canon_state_transitions"],plan.metadata["canon_transition_attempts"]]))
        phases=(len(history)==14 and all(r[0]==i+1 and r[1]==i//2+1 and r[2]==("intent" if i%2==0 else "succeeded")
            and r[3]==plan.digest and (r[4] is None if i%2==0 else r[4]==_hash([plan.digest,i//2+1])) and r[5]==operation and r[6]==idempotency_key
            for i,r in enumerate(history)))
        if not lineage or not phases:raise RecoveryError("RESOURCE_UNAVAILABLE",503)
        with self._transaction(context.tenant_id,context.workspace_id,context.actor_id) as connection:
            checked=current(connection)
            parent=connection.execute("SELECT * FROM public.restore_requests WHERE request_id=%s AND actor_id=%s FOR UPDATE",(request_id,context.actor_id)).fetchone()
            if replayed:
                if parent is None or parent["state"]!="completed":raise RecoveryError("RESTORE_STATE_INVALID",409)
                return self._select_registered_restore(connection,request_id)
            if parent is None or parent["state"] not in {"restoring","verifying"}:raise RecoveryError("RESTORE_STATE_INVALID",409)
            if parent["state"]!="verifying":
                connection.execute("UPDATE public.restore_requests SET state=%s,version=version+1,updated_at=%s WHERE request_id=%s",("verifying",self._clock(),request_id))
            event=self._append_audit(connection,context,"restore.completed","restore_request",request_id)
            flags=(lineage,phases and result.target_digest==plan.digest,phases and result.provenance_digest==_hash([plan.metadata["canon_state_transitions"],plan.metadata["canon_transition_attempts"]]),
                checked.current_retention_watermark==binding.current_retention_watermark)
            if not all(flags):raise RecoveryError("RESOURCE_UNAVAILABLE",503)
            digest=_hash([request_id,binding.manifest_digest,binding.receipt_digest,binding.current_retention_watermark,plan.digest,result.provenance_digest,event,list(flags)])
            connection.execute("INSERT INTO restore_verifications (tenant_id,workspace_id,verification_id,request_id,verification_digest,"
                "lineage_verified,rls_verified,audit_chain_verified,retention_rechecked,created_at) VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)",
                (context.tenant_id,context.workspace_id,"verification-"+secrets.token_hex(12),request_id,digest,*flags,self._clock()))
            connection.execute("UPDATE public.restore_requests SET state=%s,version=version+1,updated_at=%s WHERE request_id=%s",("completed",self._clock(),request_id))
            self._record_operation(connection,context,"restore-execute",idempotency_key,fingerprint,request_id)
            if self._notification_inbox_enabled is True:
                from .notification_recovery import enqueue_restore_completed_event
                enqueue_restore_completed_event(connection,context=context,origin_event_id=event,request_id=request_id)
            return self._select_registered_restore(connection,request_id)

    def list_restore_destinations(self,context,backup_id,*,recheck):
        registry,probe,_=self._require_registered_ports()
        _,scope,_,verified=self._registered_capture(context,backup_id,recheck=recheck)
        result=registry.summaries(context,probe)
        recheck()
        with self._transaction(context.tenant_id,context.workspace_id,context.actor_id) as connection:
            self._registered_current_decision(connection,context,scope,verified.metadata)
        return result

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
