"""R1-A 현재 DB 원장으로 선택 계보만 해석한다. 새 연결/쓰기/암호화/API는 없다.

호출자는 현재 정식 principal/session과 app scope가 결속된 실제 connection을 제공한다.
이 결과는 인증 토큰/보존 receipt/ready가 아니다. 긴 capture 이후 원장 확정 직전에
현재 principal을 별도 재검증하고 recheck해야 한다. 두 읽기와 commit의 원자 직렬화는
보장하지 않는다. 행/최종 인코딩 상한은 DB driver/JSON 처리의 peak RAM 보장이 아니다.
"""
from __future__ import annotations

import hashlib
import hmac
import json
import re
from dataclasses import dataclass, field
from datetime import datetime
from types import SimpleNamespace

from psycopg import Error
from psycopg_pool import PoolTimeout

from .authorization import Action
from .cloud_storage import CloudDatabaseError
from .data_canon import CanonError, canonical_json_bytes, transition_allowed
from .document_index_postgres import _source_access_allowed
from .notification import NotificationError
from .notification_source_access import _special_source_access_allowed
from .open_law_source_access import OpenLawAccessError, _AuthReadConnection, _current_auth_grant
from .organization_access import OrganizationAccessBridge
from .organization_membership import OrganizationWorkflowError
from .recovery import BackupSelectionInput, RecoveryContext, RecoveryError, _safe_canon_id


MAX_VERSIONS = MAX_SOURCES = MAX_OBJECTS = 100
MAX_BYTES = 512 * 1024 * 1024
MAX_METADATA_BYTES = 32 * 1024 * 1024
MAX_TRANSITIONS = MAX_ATTEMPTS = 10000
_DIGEST = re.compile(r"[0-9a-f]{64}")
_REFERENCE_FIELDS = frozenset({"object_key", "staging_key", "raw_object_key", "credential", "credentials",
    "password", "access_token", "refresh_token", "api_key", "secret_key", "dsn"})
_CANON = ("tenant_id", "workspace_id", "record_id", "aggregate_id", "version", "schema_version",
    "previous_version_id", "canonical_json", "canonical_text", "digest_sha256", "created_by", "trace_id", "created_at")
_EVENT = ("tenant_id", "workspace_id", "record_id", "entity_type", "actor_id", "source_state", "target_state",
    "reason_code", "trace_id", "policy_version", "occurred_at")
# 실제 0002/0003/0005/0020 열의 최소 allowlist. locator/credentials/object 경로를 읽지 않는다.
_COLUMNS = {
    "notebooks": ("tenant_id", "workspace_id", "notebook_id", "created_by", "created_at"),
    "notebook_metadata_versions": ("tenant_id", "workspace_id", "notebook_id", "version", "title", "description", "is_current", "updated_by", "updated_at"),
    "notebook_bindings": ("tenant_id", "workspace_id", "notebook_id", "binding_kind", "record_id", "version_id", "created_by", "created_at"),
    "sources": (*_CANON, "state"), "source_versions": (*_CANON, "source_id", "object_id"),
    "object_records": ("tenant_id", "workspace_id", "object_id", "area", "digest_sha256", "byte_size", "content_type", "status"),
    "canon_state_transitions": (*_EVENT, "transition_id", "transition_version"),
    "canon_transition_attempts": (*_EVENT, "attempt_id", "expected_version", "current_version", "safe_error_code", "outcome", "result_state", "result_version"),
    "deletion_requests": ("tenant_id", "workspace_id", "request_id", "source_id", "state", "version", "source_active", "purge_started", "policy_version"),
    "legal_holds": ("tenant_id", "workspace_id", "hold_id", "source_id", "state", "version", "policy_version"),
}


def _invalid(): return RecoveryError("BACKUP_MANIFEST_INVALID", 409)
def _unavailable(): return RecoveryError("BACKUP_UNAVAILABLE", 404)
def _large(): return RecoveryError("BACKUP_SELECTION_TOO_LARGE", 413)


def _json(value):
    try: return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False, allow_nan=False).encode("utf-8")
    except (ValueError, TypeError, RecursionError, UnicodeError): raise _invalid() from None


def _positive(value): return type(value) is int and value > 0


def _timestamp(value):
    if not isinstance(value, str): raise _invalid()
    try:
        parsed = datetime.fromisoformat(value)
        if parsed.tzinfo is None: raise ValueError()
    except ValueError: raise _invalid() from None


def _read(connection, context, table, where, values, *, order, maximum=None):
    # table/열/조건/순서는 이 모듈의 고정 문자열만. 외부 입력은 항상 params다.
    columns = _COLUMNS[table]
    projection = ",".join(f"'{column}',r.{column}" for column in columns)
    sql = (f"SELECT jsonb_build_object({projection}) FROM public.{table} r "
        f"WHERE r.tenant_id=%s AND r.workspace_id=%s {where} ORDER BY {order}")
    params = (context.tenant_id, context.workspace_id, *values)
    if maximum is not None:
        sql += " LIMIT %s"; params += (maximum + 1,)
    result, size = [], 0
    for row in connection.execute(sql, params):
        if maximum is not None and len(result) >= maximum: raise _large()
        if not isinstance(row, (tuple, list)) or len(row) != 1 or type(row[0]) is not dict: raise _invalid()
        value = row[0]
        if set(value) != set(columns): raise _invalid()
        if (value["tenant_id"], value["workspace_id"]) != (context.tenant_id, context.workspace_id): raise _unavailable()
        size += len(_json(value))
        if size > MAX_METADATA_BYTES: raise _large()
        result.append(value)
    return result


def _one(rows):
    if not rows: raise _unavailable()
    if len(rows) != 1: raise _invalid()
    return rows[0]


def _canon(row, *, mutable=False):
    for key in ("record_id", "aggregate_id", "created_by", "trace_id"): _safe_canon_id(row[key])
    if not _positive(row["version"]) or not _positive(row["schema_version"]): raise _invalid()
    previous = row["previous_version_id"]
    if previous is not None: _safe_canon_id(previous)
    if mutable:
        # Source.version만 상태 counter이며 이전 Source 행을 합성해 맞추지 않는다.
        if previous is not None: raise _invalid()
    elif (row["version"] == 1) != (previous is None): raise _invalid()
    text, payload, digest = row["canonical_text"], row["canonical_json"], row["digest_sha256"]
    if not isinstance(text, str) or type(payload) is not dict or not isinstance(digest, str) or not _DIGEST.fullmatch(digest): raise _invalid()
    try:
        decoded = json.loads(text)
        if (type(decoded) is not dict or canonical_json_bytes(decoded) != canonical_json_bytes(payload)
                or hashlib.sha256(text.encode("utf-8")).hexdigest() != digest): raise _invalid()
    except (CanonError, ValueError, UnicodeError, RecursionError): raise _invalid() from None
    _timestamp(row["created_at"])
    # immutable Canon을 임의 redact해 digest를 바꾸지 않는다. 금지 ref가 실제 payload에
    # 포함된 형식은 보존 대상으로 지원하지 않으며 정상 빈/가짜 Canon으로 대체하지 않는다.
    pending = [payload]
    while pending:
        value = pending.pop()
        if isinstance(value, dict):
            if any(key.casefold() in _REFERENCE_FIELDS for key in value): raise _invalid()
            pending.extend(value.values())
        elif isinstance(value, list): pending.extend(value)


def _source_history(connection, context, source):
    identity = source["record_id"]
    transitions = _read(connection, context, "canon_state_transitions", "AND r.entity_type='Source' AND r.record_id=%s",
        (identity,), order="r.transition_version,r.transition_id", maximum=MAX_TRANSITIONS)
    attempts = _read(connection, context, "canon_transition_attempts", "AND r.entity_type='Source' AND r.record_id=%s AND r.outcome='succeeded'",
        (identity,), order="r.result_version,r.attempt_id", maximum=MAX_ATTEMPTS)
    if len(transitions) != len(attempts) or len(transitions) != source["version"] - 1: raise _invalid()
    by_id = {}
    for row in attempts:
        _safe_canon_id(row["attempt_id"])
        if row["attempt_id"] in by_id: raise _invalid()
        by_id[row["attempt_id"]] = row
    state, counter, seen = "registered", 1, set()
    for row in transitions:
        _safe_canon_id(row["transition_id"])
        if row["transition_id"] in seen: raise _invalid()
        seen.add(row["transition_id"])
        for key in ("actor_id", "trace_id", "policy_version"): _safe_canon_id(row[key])
        _timestamp(row["occurred_at"])
        if (row["entity_type"] != "Source" or row["record_id"] != identity
                or not _positive(row["transition_version"]) or row["transition_version"] != counter + 1
                or row["source_state"] != state or not isinstance(row["target_state"], str)
                or not transition_allowed("Source", state, row["target_state"])): raise _invalid()
        attempt = by_id.get(row["transition_id"])
        if attempt is None or any(attempt[key] != row[key] for key in _EVENT): raise _invalid()
        if (attempt["outcome"] != "succeeded" or attempt["safe_error_code"] is not None
                or any(type(attempt[key]) is not int or attempt[key] != counter for key in ("expected_version", "current_version"))
                or type(attempt["result_version"]) is not int or attempt["result_version"] != counter + 1
                or attempt["result_state"] != row["target_state"]): raise _invalid()
        state, counter = row["target_state"], counter + 1
    if state != source["state"] or counter != source["version"]: raise _invalid()
    return transitions, attempts


def _collect(context, selection, connection):
    scoped = SimpleNamespace(tenant_id=context.tenant_id, workspace_id=context.workspace_id, actor_id=context.actor_id,
        notebook_id=selection.notebook_id, trace_id=context.trace_id, policy_version=context.policy_version)
    # RLS/GUC/creator/대표 Workspace projection만으로 현재 허용을 만들지 않는다.
    grant = _current_auth_grant(scoped, connection, Action.POLICY_MANAGE)
    current = _AuthReadConnection(connection)
    workspace = current.execute("SELECT * FROM auth_workspaces WHERE tenant_id=? AND workspace_id=?",
        (context.tenant_id, context.workspace_id)).fetchone()
    if workspace is None: raise OpenLawAccessError("OPEN_LAW_ACCESS_DENIED")
    # 정식 authorization의 조직 workflow 중지 판정. role/permission 존속으로 우회0.
    if workspace["workspace_kind"] == "organization":
        OrganizationAccessBridge(postgres=True).assert_organization_active(connection=current,
            tenant_id=context.tenant_id, user_id=context.actor_id)
    authorization = hashlib.sha256(_json({"actor_id": grant.actor_id, "tenant_id": grant.tenant_id,
        "workspace_id": grant.workspace_id, "action": grant.action.value, "role": grant.role.value,
        "role_scope": grant.role_scope.value, "membership_version": grant.membership_version,
        "acl_version": grant.acl_version, "policy_version": grant.policy_version,
        "effective_permissions": sorted([(row.permission.value, row.effective, row.locked_by, row.reason, row.policy_version)
            for row in grant.effective_permissions])})).hexdigest()
    notebook = _one(_read(connection, context, "notebooks", "AND r.notebook_id=%s AND NOT EXISTS(SELECT 1 FROM public.notebook_deletion_requests d "
        "WHERE d.tenant_id=r.tenant_id AND d.workspace_id=r.workspace_id AND d.notebook_id=r.notebook_id AND d.state IN ('accepted','deleting','completed'))",
        (selection.notebook_id,), order="r.notebook_id", maximum=1))
    if notebook["notebook_id"] != selection.notebook_id: raise _unavailable()
    _timestamp(notebook["created_at"])
    notebook_meta = _one(_read(connection, context, "notebook_metadata_versions", "AND r.notebook_id=%s AND r.is_current=true",
        (selection.notebook_id,), order="r.version", maximum=1))
    if notebook_meta["notebook_id"] != selection.notebook_id or notebook_meta["is_current"] is not True or not _positive(notebook_meta["version"]): raise _invalid()
    _timestamp(notebook_meta["updated_at"])
    bindings = _read(connection, context, "notebook_bindings", "AND r.notebook_id=%s AND r.binding_kind='source' AND r.version_id=ANY(%s) "
        "AND NOT EXISTS(SELECT 1 FROM public.notebook_source_unbindings u WHERE u.tenant_id=r.tenant_id AND u.workspace_id=r.workspace_id "
        "AND u.notebook_id=r.notebook_id AND u.source_id=r.record_id AND u.source_version_id=r.version_id)",
        (selection.notebook_id, list(selection.source_version_ids)), order="r.version_id,r.record_id", maximum=100)
    bound = {row["version_id"]: row for row in bindings}
    if len(bound) != len(bindings) or set(bound) != set(selection.source_version_ids): raise _unavailable()
    versions, sources, objects, transitions, attempts = {}, {}, {}, [], []
    visiting, ordered = set(), []

    def visit(identity):
        if identity in visiting: raise _invalid()
        if identity in versions: return versions[identity]
        if len(versions) + len(visiting) >= MAX_VERSIONS: raise _large()
        _safe_canon_id(identity); visiting.add(identity)
        row = _one(_read(connection, context, "source_versions", "AND r.record_id=%s AND NOT EXISTS(SELECT 1 FROM public.notebook_source_unbindings u "
            "WHERE u.tenant_id=r.tenant_id AND u.workspace_id=r.workspace_id AND u.notebook_id=%s AND u.source_id=r.source_id AND u.source_version_id=r.record_id)",
            (identity, selection.notebook_id), order="r.record_id", maximum=1))
        if row["record_id"] != identity: raise _unavailable()
        # 각 dependency에도 실제 SourceACL/law/web을 적용한다. 별도 binding을 생성하지 않는다.
        if not _source_access_allowed(connection, scoped, identity) or not _special_source_access_allowed(connection, scoped, identity): raise _unavailable()
        _canon(row)
        source_id, object_id = row["source_id"], row["object_id"]
        _safe_canon_id(source_id); _safe_canon_id(object_id)
        if source_id not in sources:
            if len(sources) >= MAX_SOURCES: raise _large()
            source = _one(_read(connection, context, "sources", "AND r.record_id=%s", (source_id,), order="r.record_id", maximum=1))
            if source["record_id"] != source_id or source["state"] in {"expired", "disabled", "deleting", "deleted"}: raise _unavailable()
            _canon(source, mutable=True)
            before, after = _source_history(connection, context, source)
            transitions.extend(before); attempts.extend(after)
            if len(transitions) > MAX_TRANSITIONS or len(attempts) > MAX_ATTEMPTS: raise _large()
            sources[source_id] = source
        if object_id not in objects:
            if len(objects) >= MAX_OBJECTS: raise _large()
            obj = _one(_read(connection, context, "object_records", "AND r.object_id=%s", (object_id,), order="r.object_id", maximum=1))
            if obj["object_id"] != object_id or obj["status"] != "completed" or obj["area"] != "source": raise _unavailable()
            if (not isinstance(obj["digest_sha256"], str) or not _DIGEST.fullmatch(obj["digest_sha256"])
                    or type(obj["byte_size"]) is not int or obj["byte_size"] < 0): raise _invalid()
            objects[object_id] = obj
            if sum(item["byte_size"] for item in objects.values()) > MAX_BYTES: raise _large()
        obj, payload = objects[object_id], row["canonical_json"]
        digest_key = "text_sha256" if payload.get("origin_kind") in {"website", "mcp_open_law"} else "content_digest_sha256"
        if (payload.get("object_id") != object_id or type(payload.get("byte_size")) is not int
                or payload["byte_size"] != obj["byte_size"] or payload.get(digest_key) != obj["digest_sha256"]): raise _invalid()
        previous = row["previous_version_id"]
        if previous is not None:
            parent = visit(previous)
            if (parent["source_id"] != source_id or parent["aggregate_id"] != row["aggregate_id"] or parent["version"] != row["version"]-1): raise _invalid()
        visiting.remove(identity); versions[identity] = row; ordered.append(row)
        return row

    for identity in selection.source_version_ids:
        row = visit(identity); binding = bound[identity]
        if (binding["notebook_id"] != selection.notebook_id or binding["binding_kind"] != "source" or binding["record_id"] != row["source_id"]): raise _unavailable()
        _timestamp(binding["created_at"])
    # schema 값은 현재 DB에서 읽으며 0069 등 특정 배포를 하드코딩하지 않는다.
    revisions = connection.execute("SELECT version_num FROM public.alembic_version ORDER BY version_num").fetchall()
    if len(revisions) != 1 or not isinstance(revisions[0][0], str) or not revisions[0][0]: raise RecoveryError("RESOURCE_UNAVAILABLE", 503)
    source_ids = sorted(sources)
    deletion = _read(connection, context, "deletion_requests", "AND r.source_id=ANY(%s)", (source_ids,), order="r.request_id")
    holds = _read(connection, context, "legal_holds", "AND r.source_id=ANY(%s)", (source_ids,), order="r.hold_id")
    for row in deletion:
        if row["source_id"] not in sources: raise _unavailable()
        if (row["source_active"] is not True or row["purge_started"] is not False or row["state"] in
                {"purged", "deactivated", "grace_period", "cleanup_pending", "blocked_by_hold"}): raise _unavailable()
    # Hold는 capture 자체를 새로 금지하지 않는다. 실제 현재 보존 원장을 지문에 결속한다.
    # 보존본 이전/복원 시 held 제외는 후속 Preview/Execute가 기존 규칙으로 다시 판정한다.
    for row in holds:
        if row["source_id"] not in sources or row["state"] not in {"active", "released"}: raise _invalid()
    metadata = {"notebooks": [notebook], "notebook_metadata_versions": [notebook_meta], "notebook_bindings": bindings,
        "sources": [sources[key] for key in sorted(sources)], "source_versions": ordered,
        "object_records": [objects[key] for key in sorted(objects)],
        "canon_state_transitions": sorted(transitions, key=lambda r: (r["record_id"], r["transition_version"])),
        "canon_transition_attempts": sorted(attempts, key=lambda r: (r["record_id"], r["result_version"], r["attempt_id"]))}
    encoded = _json(metadata)
    if len(encoded) > MAX_METADATA_BYTES: raise _large()
    watermark = hashlib.sha256(_json({"deletions": deletion, "holds": holds})).hexdigest()
    return encoded, revisions[0][0], watermark, authorization


@dataclass(frozen=True, slots=True, repr=False)
class LineageSelection:
    root_source_version_ids: tuple[str, ...]
    dependency_source_version_ids: tuple[str, ...]
    dependency_source_ids: tuple[str, ...]
    source_version_count: int
    source_count: int
    unique_object_count: int
    total_bytes: int
    metadata_bytes: int
    schema_revision: str
    retention_watermark: str
    authorization_fingerprint: str
    fingerprint: str
    _encoded: bytes = field(repr=False)
    _selection: BackupSelectionInput = field(repr=False)

    def metadata(self):
        """독립 사본. 호출자의 mutable dict 변경으로 보존 지문을 바꾸지 못한다."""
        return json.loads(self._encoded)

    def require_consent(self, fingerprint, include_prior_versions):
        if (include_prior_versions is not True or not isinstance(fingerprint, str) or not _DIGEST.fullmatch(fingerprint)
                or not hmac.compare_digest(self.fingerprint, fingerprint)): raise _invalid()

    def recheck(self, context, connection):
        current = resolve_lineage_selection(context, self._selection, connection)
        if not hmac.compare_digest(current.fingerprint, self.fingerprint): raise _invalid()
        return current


def resolve_lineage_selection(context, root_selection, connection) -> LineageSelection:
    """기존 transaction/실제 SQL port를 소비한다. 정식 세션 검증은 호출자의 별도 의무다."""
    if not isinstance(context, RecoveryContext) or not isinstance(root_selection, BackupSelectionInput):
        raise RecoveryError("RECOVERY_INPUT_INVALID", 400)
    try:
        isolation = connection.execute("SELECT pg_catalog.current_setting('transaction_isolation')").fetchone()
        if isolation != ("read committed",):
            raise RecoveryError("RESOURCE_UNAVAILABLE", 503)
        first = _collect(context, root_selection, connection)
        # 현재 ACL/상태/해제·삭제/정본·schema/보존을 한 번 더 전부 읽는다. cache/retry 없음.
        if _collect(context, root_selection, connection) != first: raise _invalid()
        encoded, revision, watermark, authorization = first
        meta = json.loads(encoded)
        roots = root_selection.source_version_ids
        dependencies = tuple(sorted(row["record_id"] for row in meta["source_versions"] if row["record_id"] not in roots))
        dependency_sources = tuple(sorted({row["source_id"] for row in meta["source_versions"] if row["record_id"] in dependencies}))
        fingerprint = hashlib.sha256(_json({"kind": "selected-source-lineage-v3", "tenant_id": context.tenant_id,
            "workspace_id": context.workspace_id, "actor_id": context.actor_id, "policy_version": context.policy_version,
            "notebook_id": root_selection.notebook_id, "roots": roots, "dependencies": dependencies,
            "schema_revision": revision, "retention_watermark": watermark, "authorization_fingerprint": authorization,
            "metadata_digest": hashlib.sha256(encoded).hexdigest()})).hexdigest()
        return LineageSelection(roots, dependencies, dependency_sources, len(meta["source_versions"]), len(meta["sources"]),
            len(meta["object_records"]), sum(row["byte_size"] for row in meta["object_records"]), len(encoded),
            revision, watermark, authorization, fingerprint, encoded, root_selection)
    except RecoveryError: raise
    except OrganizationWorkflowError: raise RecoveryError("CURRENT_ACCESS_DENIED", 403) from None
    except OpenLawAccessError: raise RecoveryError("CURRENT_ACCESS_DENIED", 403) from None
    except NotificationError as error:
        if error.code != "DATABASE_UNAVAILABLE" or error.http_status != 503: raise
        raise RecoveryError("RESOURCE_UNAVAILABLE", 503) from None
    except (Error, CloudDatabaseError, PoolTimeout, OSError):
        raise RecoveryError("RESOURCE_UNAVAILABLE", 503) from None
