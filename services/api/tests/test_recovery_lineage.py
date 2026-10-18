"""R1-A 실제 SQL 소비/도메인 직접시험. 주입 원장 대역이며 실제 PG/bytes 복원이 아니다."""
from __future__ import annotations

import copy
import hashlib
import importlib
import importlib.util
import json
import re
from dataclasses import replace
from types import SimpleNamespace

import pytest
from psycopg.pq import TransactionStatus

from daon_user_api.recovery import BackupSelectionInput, RecoveryContext, RecoveryError


T = "2026-10-19T00:00:00+00:00"


def api():
    assert importlib.util.find_spec("daon_user_api.recovery_lineage") is not None, "R1-A 실제 SQL lineage resolver 미구현"
    return importlib.import_module("daon_user_api.recovery_lineage")


def canonical(record_id, aggregate, version, payload, previous=None, **extra):
    text = json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return dict(tenant_id="tenant-1", workspace_id="workspace-lineage", record_id=record_id,
        aggregate_id=aggregate, version=version, schema_version=1, previous_version_id=previous,
        canonical_json=copy.deepcopy(payload), canonical_text=text, digest_sha256=hashlib.sha256(text.encode()).hexdigest(),
        created_by="actor-1", trace_id="trace-original", created_at=T, **extra)


def recanonical(row, payload):
    text = json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    row.update(canonical_json=payload, canonical_text=text, digest_sha256=hashlib.sha256(text.encode()).hexdigest())


class Ledger:
    def __init__(self):
        # 재사용하는 것은 현재 원장 SQL 응답뿐이다. authorization/law/web 함수는 실제 코드다.
        from test_open_law_source_runtime import Connection as AuthorizationRows
        self.auth = AuthorizationRows()
        self.info = SimpleNamespace(transaction_status=TransactionStatus.INTRANS)
        self.context = RecoveryContext("tenant-1", "workspace-lineage", "actor-1", "trace-current", "policy-current", True,
            session_id="session-a", device_id="device-a")
        self.selection = BackupSelectionInput("notebook-a", ("root-version",))
        self.log, self.revoked, self.unbound, self.notebook_deleted = [], set(), set(), False
        self.isolation = "read committed"
        self.on_read, self.reads = None, {}
        self.workflow_state, self.workflow_reads, self.on_workflow_read = "active", 0, None
        self.tables = {
            "notebooks": [dict(tenant_id="tenant-1", workspace_id="workspace-lineage", notebook_id="notebook-a", created_by="actor-1", created_at=T)],
            "notebook_metadata_versions": [dict(tenant_id="tenant-1", workspace_id="workspace-lineage", notebook_id="notebook-a", version=1,
                title="합성 Notebook", description=None, is_current=True, updated_by="actor-1", updated_at=T)],
            "notebook_bindings": [dict(tenant_id="tenant-1", workspace_id="workspace-lineage", notebook_id="notebook-a", binding_kind="source",
                record_id="source-a", version_id="root-version", created_by="actor-1", created_at=T)],
            "sources": [canonical("source-a", "source-aggregate", 5, {"filename": "synthetic.txt"}, state="ready")],
            "source_versions": [], "object_records": [], "canon_state_transitions": [], "canon_transition_attempts": [],
            "deletion_requests": [], "legal_holds": [],
        }
        for identity, size, digest in (("object-a", 10, "a" * 64), ("object-b", 20, "b" * 64)):
            self.tables["object_records"].append(dict(tenant_id="tenant-1", workspace_id="workspace-lineage", object_id=identity,
                area="source", digest_sha256=digest, byte_size=size, content_type="application/octet-stream", status="completed",
                object_key="NEVER_COPY_RAW_PATH", staging_key="NEVER_COPY_STAGING"))
        for identity, number, obj, previous in (("prior-version", 1, self.tables["object_records"][0], None),
                ("root-version", 2, self.tables["object_records"][1], "prior-version")):
            self.tables["source_versions"].append(canonical(identity, "version-aggregate", number,
                {"object_id": obj["object_id"], "byte_size": obj["byte_size"], "content_digest_sha256": obj["digest_sha256"]},
                previous, source_id="source-a", object_id=obj["object_id"]))
        states = ("registered", "security_check", "processing", "indexing", "ready")
        for version, (before, after) in enumerate(zip(states, states[1:]), 2):
            common = dict(tenant_id="tenant-1", workspace_id="workspace-lineage", record_id="source-a", entity_type="Source",
                actor_id="actor-original", source_state=before, target_state=after, reason_code="SYNTHETIC_TRANSITION",
                trace_id="trace-original", policy_version="policy-original", occurred_at=T)
            identity = f"transition-{version}"
            self.tables["canon_state_transitions"].append(dict(common, transition_id=identity, transition_version=version))
            self.tables["canon_transition_attempts"].append(dict(common, attempt_id=identity, expected_version=version-1,
                current_version=version-1, safe_error_code=None, outcome="succeeded", result_state=after, result_version=version))

    def execute(self, sql, params=()):
        self.log.append((sql, copy.deepcopy(params)))
        assert not sql.lstrip().upper().startswith(("INSERT", "UPDATE", "DELETE", "GRANT", "ALTER", "CREATE"))
        if sql.startswith("SELECT jsonb_build_object("):
            match = re.search(r"FROM public\.([a-z_]+) r WHERE", sql)
            assert match, "정본 public schema와 명시 row projection 필요"
            table = match[1]
            assert "r.tenant_id=%s AND r.workspace_id=%s" in sql
            assert params[:2] == (self.context.tenant_id, self.context.workspace_id)
            self.reads[table] = self.reads.get(table, 0) + 1
            if self.on_read: self.on_read(table, self.reads[table])
            rows = copy.deepcopy(self.tables[table])
            if table == "notebooks":
                assert "r.created_by=%s" not in sql and "notebook_deletion_requests" in sql
                rows = [r for r in rows if r["notebook_id"] == params[2] and not self.notebook_deleted]
            elif table == "notebook_metadata_versions":
                assert "r.is_current=true" in sql
                rows = [r for r in rows if r["notebook_id"] == params[2] and r["is_current"]]
            elif table == "notebook_bindings":
                assert "notebook_source_unbindings" in sql and "r.version_id=ANY(%s)" in sql
                rows = [r for r in rows if r["notebook_id"] == params[2] and r["version_id"] in params[3] and r["version_id"] not in self.unbound]
            elif table == "source_versions":
                assert "notebook_source_unbindings" in sql
                rows = [r for r in rows if r["record_id"] == params[2] and r["record_id"] not in self.unbound]
            elif table == "sources": rows = [r for r in rows if r["record_id"] == params[2]]
            elif table == "object_records": rows = [r for r in rows if r["object_id"] == params[2]]
            elif table in {"canon_state_transitions", "canon_transition_attempts"}:
                assert "r.entity_type='Source'" in sql
                if table == "canon_transition_attempts":
                    assert "r.outcome='succeeded'" in sql
                    rows = [r for r in rows if r["outcome"] == "succeeded"]
                rows = [r for r in rows if r["record_id"] == params[2]]
            else: rows = [r for r in rows if r["source_id"] in params[2]]
            fields = re.findall(r"'([a-z0-9_]+)',r\.\1", sql)
            assert fields and "object_key" not in fields and "staging_key" not in fields
            result = [({key: row[key] for key in fields},) for row in rows]
        elif sql == "SELECT state FROM public.identity_org_tenant_memberships WHERE tenant_id=%s AND user_id=%s":
            assert params == (self.context.tenant_id, self.context.actor_id)
            self.workflow_reads += 1
            if self.on_workflow_read: self.on_workflow_read(self.workflow_reads)
            result = [] if self.workflow_state is None else [{"state": self.workflow_state}]
        elif "transaction_isolation" in sql: result = [(self.isolation,)]
        elif "FROM public.alembic_version" in sql: result = [("0069",)]
        elif "current_setting('app.actor_id'" in sql: result = [("actor-1", "")]
        elif sql.startswith("SELECT NOT EXISTS (SELECT 1 FROM identity_auth_source_access"):
            result = [(params[-1] not in self.revoked,)]
        elif sql.startswith(("SELECT canonical_json FROM source_versions", "SELECT canonical_json FROM public.source_versions")):
            result = [(copy.deepcopy(r["canonical_json"]),) for r in self.tables["source_versions"] if r["record_id"] == params[-1]]
        else: return self.auth.execute(sql, params)
        return SimpleNamespace(fetchone=lambda: result[0] if result else None, fetchall=lambda: result, __iter__=lambda: iter(result)) if not sql.startswith("SELECT jsonb_build_object(") else RowCursor(result)

    def resolve(self): return api().resolve_lineage_selection(self.context, self.selection, self)

    def chain(self, count):
        obj = self.tables["object_records"][0]
        self.tables["object_records"] = [obj]
        self.tables["source_versions"] = [canonical("root-version" if n == count else f"prior-{n}", "version-aggregate", n,
            {"object_id": obj["object_id"], "byte_size": obj["byte_size"], "content_digest_sha256": obj["digest_sha256"]},
            None if n == 1 else f"prior-{n-1}", source_id="source-a", object_id=obj["object_id"]) for n in range(1, count+1)]


class RowCursor:
    def __init__(self, rows): self.rows = rows
    def __iter__(self): return iter(self.rows)
    def fetchone(self): return self.rows[0] if self.rows else None
    def fetchall(self): return list(self.rows)


def test_lineage_closure_exact_predecessors_and_source_state_counter():
    api()
    case = Ledger()
    result = case.resolve()
    assert result.root_source_version_ids == ("root-version",)
    assert result.dependency_source_version_ids == ("prior-version",)
    assert result.dependency_source_ids == ("source-a",)
    assert result.source_version_count == 2 and result.source_count == 1 and result.unique_object_count == 2 and result.total_bytes == 30
    meta = result.metadata()
    assert [row["record_id"] for row in meta["source_versions"]] == ["prior-version", "root-version"]
    assert meta["sources"][0]["state"] == "ready" and meta["sources"][0]["version"] == 5
    assert len(meta["canon_state_transitions"]) == len(meta["canon_transition_attempts"]) == 4
    assert len(meta["notebook_bindings"]) == 1 and meta["notebook_bindings"][0]["version_id"] == "root-version"
    assert "NEVER_COPY" not in json.dumps(meta) and "canonical_text" in meta["source_versions"][0]
    meta["sources"][0]["state"] = "deleted"
    assert result.metadata()["sources"][0]["state"] == "ready", "외부 변경 가능한 지문 원장 금지"
    assert case.reads["notebooks"] >= 2 and case.reads["source_versions"] >= 4
    assert all(sql.startswith("SELECT ") for sql, _ in case.log)
    # D2의 현재 Workspace 공유 접근은 M1의 본인기록 집계와 다르다.
    shared = Ledger(); shared.tables["notebooks"][0]["created_by"] = "another-creator"
    assert shared.resolve().total_bytes == 30
    # 새 root 입력128은 유지하되 내부 정본의 Source/aggregate/predecessor ID256은 축소하지 않는다.
    wide = Ledger(); parent, aggregate, predecessor = "S" * 256, "A" * 256, "P" * 256
    wide.tables["sources"][0]["record_id"] = parent
    wide.tables["sources"][0]["aggregate_id"] = aggregate
    wide.tables["notebook_bindings"][0]["record_id"] = parent
    for row in wide.tables["source_versions"]:
        row["source_id"], row["aggregate_id"] = parent, aggregate
    wide.tables["source_versions"][0]["record_id"] = predecessor
    wide.tables["source_versions"][1]["previous_version_id"] = predecessor
    for table in ("canon_state_transitions", "canon_transition_attempts"):
        for row in wide.tables[table]: row["record_id"] = parent
    assert wide.resolve().dependency_source_version_ids == (predecessor,)
    assert wide.resolve().dependency_source_ids == (parent,)


def test_lineage_cycle_gap_foreign_scope_and_digest_mismatch():
    api()
    for defect in ("cycle", "missing", "foreign", "aggregate", "source", "gap", "digest", "text", "json", "base", "transition", "attempt", "embedded-reference"):
        case = Ledger(); rows = case.tables["source_versions"]
        if defect == "cycle": rows[1]["previous_version_id"] = "root-version"
        elif defect == "missing": rows.pop(0)
        elif defect == "foreign": rows[0]["workspace_id"] = "private-workspace"
        elif defect == "aggregate": rows[0]["aggregate_id"] = "other-aggregate"
        elif defect == "source": rows[0]["source_id"] = "other-source"
        elif defect == "gap": rows[1]["version"] = 3
        elif defect == "digest": rows[0]["digest_sha256"] = "f" * 64
        elif defect == "text": rows[0]["canonical_text"] = "{}"
        elif defect == "json": rows[0]["canonical_json"] = {"unexpected": True}
        elif defect == "base": case.tables["sources"][0]["previous_version_id"] = "invented-source"
        elif defect == "transition": case.tables["canon_state_transitions"][1]["target_state"] = "ready"
        elif defect == "attempt": case.tables["canon_transition_attempts"].pop()
        else: recanonical(rows[0], dict(rows[0]["canonical_json"], private={"object_key": "S44_FORBIDDEN_LOCATOR"}))
        with pytest.raises(RecoveryError) as caught: case.resolve()
        assert caught.value.status in {404, 409}
        assert all(hidden not in str(caught.value) for hidden in ("prior-version", "private-workspace", "other-source"))


def test_lineage_current_acl_special_source_and_deleted_predecessor(monkeypatch):
    api()
    for defect in ("acl", "unbound", "dependency-unbound", "notebook-deleted", "deleted", "purged", "tombstoned", "law-disabled", "web-disconnected"):
        case = Ledger()
        if defect == "acl": case.revoked.add("prior-version")
        elif defect == "unbound": case.unbound.add("root-version")
        elif defect == "dependency-unbound": case.unbound.add("prior-version")
        elif defect == "notebook-deleted": case.notebook_deleted = True
        elif defect == "deleted": case.tables["sources"][0]["state"] = "deleted"
        elif defect in {"purged", "tombstoned"}:
            case.tables["deletion_requests"] = [dict(tenant_id="tenant-1", workspace_id="workspace-lineage", request_id="deletion-a",
                source_id="source-a", state="purged" if defect == "purged" else "grace_period", version=2, source_active=False, purge_started=False, policy_version="policy-old")]
        else:
            row = case.tables["source_versions"][0]
            recanonical(row, dict(row["canonical_json"], origin_kind="mcp_open_law" if defect == "law-disabled" else "website",
                connector_id="mcp-open-law-go-kr", snapshot_id="snapshot-original", text_sha256="a" * 64))
            monkeypatch.setenv("DAON_OPEN_LAW_ENABLED", "false")
            if defect == "web-disconnected":
                original = case.auth.execute
                case.auth.execute = lambda sql, params=(): RowCursor([]) if "FROM public.website_source_snapshots" in sql else original(sql, params)
        with pytest.raises(RecoveryError) as caught: case.resolve()
        assert caught.value.status == 404 and str(caught.value) == "BACKUP_UNAVAILABLE"
    case = Ledger(); case.auth.auth_active = False
    with pytest.raises(RecoveryError) as caught: case.resolve()
    assert caught.value.status == 403
    # 정상 법령과 웹은 실제 helper/현재 정책을 소비한다. 과거 법령은 기존 latest SQL로 거부한다.
    for kind in ("mcp_open_law", "website"):
        case = Ledger(); case.chain(1); row = case.tables["source_versions"][0]
        recanonical(row, dict(row["canonical_json"], origin_kind=kind, connector_id="mcp-open-law-go-kr",
            snapshot_id="snapshot-law", raw_sha256="f" * 64, text_sha256="a" * 64))
        monkeypatch.setenv("DAON_OPEN_LAW_ENABLED", "true")
        monkeypatch.setenv("DAON_OPEN_LAW_QA_WORKSPACE_ID", "workspace-lineage")
        assert case.resolve().total_bytes == 10
        queries = [sql for sql, _ in case.auth.queries]
        assert sum("SELECT w.snapshot_id FROM " in sql for sql in queries) == 2
        if kind == "mcp_open_law": assert sum(sql.startswith("SELECT binding.tenant_id") for sql in queries) == 4
    case = Ledger(); execute = case.execute
    def failed(sql, params=()):
        if "FROM identity_auth_source_access" in sql:
            import psycopg
            raise psycopg.OperationalError("S44_PRIVATE_CONNECTION_DETAIL")
        return execute(sql, params)
    case.execute = failed
    with pytest.raises(RecoveryError) as caught: case.resolve()
    assert caught.value.status == 503 and "PRIVATE" not in str(caught.value)


def test_lineage_expanded_100_versions_512mib_and_metadata_limits():
    api()
    case = Ledger(); case.chain(100)
    assert case.resolve().source_version_count == 100
    case = Ledger(); case.chain(101)
    with pytest.raises(RecoveryError) as caught: case.resolve()
    assert caught.value.status == 413
    # 독립 root100개의 실제 관계: Source/Object 각각100개이며 이름만 바꾼 숫자 대역이 아니다.
    case = Ledger(); base = copy.deepcopy(case.tables)
    for table in ("sources", "source_versions", "object_records", "notebook_bindings", "canon_state_transitions", "canon_transition_attempts"):
        case.tables[table] = []
    roots = []
    for number in range(100):
        sid, vid, oid = f"source-{number}", f"version-{number}", f"object-{number}"
        roots.append(vid)
        obj = dict(base["object_records"][0], object_id=oid, byte_size=1)
        case.tables["object_records"].append(obj)
        case.tables["sources"].append(canonical(sid, sid, 1, {"filename": "synthetic.txt"}, state="registered"))
        case.tables["source_versions"].append(canonical(vid, vid, 1,
            {"object_id": oid, "byte_size": 1, "content_digest_sha256": obj["digest_sha256"]}, source_id=sid, object_id=oid))
        case.tables["notebook_bindings"].append(dict(base["notebook_bindings"][0], record_id=sid, version_id=vid))
    case.selection = BackupSelectionInput("notebook-a", tuple(reversed(roots)))
    result = case.resolve()
    assert (result.source_version_count, result.source_count, result.unique_object_count, result.total_bytes) == (100, 100, 100, 100)
    original = result.fingerprint
    case.selection = BackupSelectionInput("notebook-a", tuple(roots))
    assert case.resolve().fingerprint == original
    for overflow in (False, True):
        case = Ledger(); objects = case.tables["object_records"]
        objects[0]["byte_size"] = 512 * 1024 * 1024 - objects[1]["byte_size"] + int(overflow)
        row = case.tables["source_versions"][0]
        recanonical(row, dict(row["canonical_json"], byte_size=objects[0]["byte_size"]))
        if not overflow: assert case.resolve().total_bytes == 512 * 1024 * 1024
        else:
            with pytest.raises(RecoveryError) as caught: case.resolve()
            assert caught.value.status == 413
    for table in ("canon_state_transitions", "canon_transition_attempts"):
        case = Ledger(); case.tables[table] = case.tables[table] * 2501
        with pytest.raises(RecoveryError) as caught: case.resolve()
        assert caught.value.status == 413
    case = Ledger(); row = case.tables["sources"][0]
    recanonical(row, {"metadata": "x" * (17 * 1024 * 1024)})
    with pytest.raises(RecoveryError) as caught: case.resolve()
    assert caught.value.status == 413


def test_lineage_consent_false_changed_fingerprint_or_hidden_dependency():
    api()
    case = Ledger(); result = case.resolve()
    result.require_consent(result.fingerprint, True)
    for value, included in ((result.fingerprint, False), ("f" * 64, True), (result.fingerprint, 1), (None, True)):
        with pytest.raises(RecoveryError) as caught: result.require_consent(value, included)
        assert caught.value.status == 409 and "prior-version" not in str(caught.value)
    case.revoked.add("prior-version")
    with pytest.raises(RecoveryError) as caught: result.recheck(case.context, case)
    assert str(caught.value) == "BACKUP_UNAVAILABLE"


def test_lineage_session_recheck_does_not_change_actor_selection_fingerprint():
    api()
    case = Ledger(); first = case.resolve()
    changed = replace(case.context, session_id="session-b", device_id="device-b", trace_id="trace-new-request")
    second = first.recheck(changed, case)
    assert first.fingerprint == second.fingerprint and first.metadata() == second.metadata()
    assert case.resolve().fingerprint != api().resolve_lineage_selection(replace(case.context, actor_id="actor-2"), case.selection, case).fingerprint
    # Runtime 설정 문자열이 같아도 현재 정식 정책 원장이 바뀌면 동의 지문을 재사용하지 않는다.
    case = Ledger(); first = case.resolve(); auth_execute = case.auth.execute
    def changed_policy(sql, params=()):
        if "FROM identity_auth_tenant_policies" in sql:
            return SimpleNamespace(fetchone=lambda: {"version": 2})
        return auth_execute(sql, params)
    case.auth.execute = changed_policy
    with pytest.raises(RecoveryError) as caught: first.recheck(case.context, case)
    assert caught.value.status == 409
    # 동일 snapshot의 반복 SELECT를 현재 철회 재조회라고 가장하지 않는다. 설정 변경은 없다.
    case = Ledger(); case.isolation = "repeatable read"
    with pytest.raises(RecoveryError) as caught: case.resolve()
    assert caught.value.status == 503
    for defect in ("acl", "binding", "metadata", "policy", "schema"):
        case = Ledger()
        def change(table, count):
            if table == "notebooks" and count == 2:
                if defect == "acl": case.revoked.add("prior-version")
                elif defect == "binding": case.unbound.add("root-version")
                elif defect == "metadata": case.tables["notebook_metadata_versions"][0]["title"] = "변경됨"
        if defect in {"acl", "binding", "metadata"}:
            case.on_read = change
            with pytest.raises(RecoveryError): case.resolve()
        elif defect == "policy":
            first = case.resolve()
            with pytest.raises(RecoveryError): first.recheck(replace(case.context, policy_version="policy-new"), case)
        else:
            first = case.resolve(); execute = case.execute
            case.execute = lambda sql, params=(): RowCursor([("changed-schema",)]) if "FROM public.alembic_version" in sql else execute(sql, params)
            with pytest.raises(RecoveryError): first.recheck(case.context, case)


def test_lineage_current_organization_workflow_suspended_with_role_retained():
    case = Ledger()
    case.auth.auth_role = "organization_admin"
    case.workflow_state = "suspended"
    with pytest.raises(RecoveryError) as caught: case.resolve()
    assert caught.value.status == 403 and str(caught.value) == "CURRENT_ACCESS_DENIED"
    assert case.auth.auth_active is True and case.auth.auth_role == "organization_admin"
    assert case.workflow_reads == 1 and case.reads.get("notebooks", 0) == 0
    # 기존 helper의 active/미구성 조직 및 개인 Workspace 의미를 그대로 유지한다.
    for state in ("active", None):
        valid = Ledger(); valid.auth.auth_role = "organization_admin"; valid.workflow_state = state
        assert valid.resolve().source_version_count == 2 and valid.workflow_reads == 2
    personal = Ledger(); personal.workflow_state = "suspended"
    assert personal.resolve().source_version_count == 2 and personal.workflow_reads == 0


def test_lineage_final_organization_workflow_suspension_and_read_failure():
    case = Ledger(); case.auth.auth_role = "organization_admin"
    def suspend_before_final(count):
        if count == 2: case.workflow_state = "suspended"
    case.on_workflow_read = suspend_before_final
    with pytest.raises(RecoveryError) as caught: case.resolve()
    assert caught.value.status == 403 and str(caught.value) == "CURRENT_ACCESS_DENIED"
    assert case.auth.auth_active is True and case.workflow_reads == 2
    assert case.reads["notebooks"] == 1
    # 조회 장애는 허용/중지 없음으로 삼키지 않으며 비밀 오류를 반사하지 않는다.
    case = Ledger(); case.auth.auth_role = "organization_admin"; execute = case.execute
    def unavailable(sql, params=()):
        if "FROM public.identity_org_tenant_memberships" in sql:
            from psycopg import OperationalError
            raise OperationalError("S44_PRIVATE_WORKFLOW_READ_FAILURE")
        return execute(sql, params)
    case.execute = unavailable
    with pytest.raises(RecoveryError) as caught: case.resolve()
    assert caught.value.status == 503 and str(caught.value) == "RESOURCE_UNAVAILABLE"
