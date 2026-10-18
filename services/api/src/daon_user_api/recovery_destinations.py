"""R2 내부 서명 registry/읽기 전용 PG·MinIO physical probe. 공개 API/운영 등록0.

TrustedPhysicalProbe는 운영자 코드 주입 경계다. 호출자가 만든 ref/name의 hash나
검증 boolean을 받지 않는다. 실제 identity/소유/전역 empty를 고정 서버 ref에서
읽으며 app/outbound는 명시 operator provisioning 설정이지 live health가 아니다.
현재 authorization은 caller가
검사하고, R4 연결에서 큰 검증 뒤에도 재검사해야 한다.
"""
from __future__ import annotations

from abc import ABC, abstractmethod
import ctypes
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
import hashlib
import hmac
import json
import os
from pathlib import Path
import re
import stat
import ssl
import time
from types import MappingProxyType
from urllib.parse import urlsplit,urlunsplit,urlencode,quote
from uuid import UUID
from xml.etree import ElementTree

from minio.credentials import StaticProvider
from minio.signer import sign_v4_s3
from psycopg import sql as pgsql
from urllib3 import PoolManager
from urllib3.util import Timeout

from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.kdf.hkdf import HKDF

from .recovery import RecoveryContext, RecoveryError
from .recovery_capture import _PosixFiles, _WindowsFiles
from .object_queue import MinioObjectStorageAdapter


PURPOSE = b"restore-destination-registry-v1"
MAX_REGISTRY_BYTES = 1024 * 1024
LINEAGE_KIND = "selected-source-lineage-v3"
_ID = re.compile(r"[A-Za-z0-9][A-Za-z0-9_.:-]{0,127}\Z")
_DIGEST = re.compile(r"[0-9a-f]{64}\Z")
_TIME = re.compile(r"\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(?:\.\d{1,6})?(?:Z|[+-]\d{2}:\d{2})\Z")
_ENTRY_FIELDS = frozenset(("destination_id", "version", "display_name", "capture_kind", "source_instance_ref",
    "authorized_tenant_id", "authorized_workspace_id", "authorized_actor_id", "target_instance_ref",
    "database_ref", "bucket_ref", "physical_receipt_digest", "owner_operation_id", "created_at", "expires_at",
    "application_enabled", "outbound_enabled"))
_STUDIO_DEFAULTS=("workspace_policies","knowledge_scopes","weight_profiles","ruleset_references","ruleset_version_snapshots","ruleset_bindings")
# 0036_authorization_postgres_tables.py INSERT(1,1). SQLite의 schema3과 별개다.
_POSTGRES_AUTHORIZATION_SCHEMA_VERSION=1


def _reject(status=409):
    # 원문/locator/다른 후보의 존재를 예외에 반사하지 않는다. 기존 공개 코드만 사용.
    return RecoveryError("RESOURCE_UNAVAILABLE" if status == 503 else "RECOVERY_INPUT_INVALID", status)


def _json(value):
    return json.dumps(value, sort_keys=True, ensure_ascii=False, separators=(",", ":"), allow_nan=False).encode("utf-8")


def _digest(value):
    if not isinstance(value, str) or not _DIGEST.fullmatch(value): raise _reject()
    return value


def _id(value):
    if not isinstance(value, str) or not _ID.fullmatch(value): raise _reject()
    return value


def _time(value):
    if not isinstance(value, str) or not _TIME.fullmatch(value): raise _reject()
    try: return datetime.fromisoformat(value.replace("Z", "+00:00")).astimezone(timezone.utc)
    except ValueError: raise _reject() from None


def _utc(value):
    if not isinstance(value, datetime) or value.tzinfo is None or value.utcoffset() != timedelta(0): raise _reject()
    return value


def _unique(pairs):
    result = {}
    for key, value in pairs:
        if key in result: raise _reject()
        result[key] = value
    return result


def _windows_protected(path):
    """pin 안의 이름만 검사. owner/current process SID·SYSTEM·Administrators만 쓰기.

    null DACL/이해하지 못하는 ACE는 허용으로 추정하지 않는다. 실제 운영자 ACL을
    설정하는 함수가 아니며 레지스트리/파일/root를 생성하거나 권한을 바꾸지 않는다.
    """
    from ctypes import wintypes
    security = ctypes.WinDLL("advapi32", use_last_error=True)
    kernel = ctypes.WinDLL("kernel32", use_last_error=True)
    pointer = ctypes.c_void_p
    security.OpenProcessToken.argtypes = [wintypes.HANDLE, wintypes.DWORD, ctypes.POINTER(wintypes.HANDLE)]
    security.GetTokenInformation.argtypes = [wintypes.HANDLE, ctypes.c_int, pointer, wintypes.DWORD, ctypes.POINTER(wintypes.DWORD)]
    security.ConvertSidToStringSidW.argtypes = [pointer, ctypes.POINTER(wintypes.LPWSTR)]
    security.GetNamedSecurityInfoW.argtypes = [wintypes.LPWSTR, ctypes.c_int, wintypes.DWORD,
        ctypes.POINTER(pointer), pointer, ctypes.POINTER(pointer), pointer, ctypes.POINTER(pointer)]
    security.GetNamedSecurityInfoW.restype = wintypes.DWORD
    security.GetAce.argtypes = [pointer, wintypes.DWORD, ctypes.POINTER(pointer)]
    kernel.CloseHandle.argtypes = [wintypes.HANDLE]; kernel.LocalFree.argtypes = [pointer]
    token = wintypes.HANDLE(); descriptor = pointer()

    def sid_string(sid):
        text = wintypes.LPWSTR()
        if not security.ConvertSidToStringSidW(sid, ctypes.byref(text)): raise _reject(503)
        try: return text.value
        finally: kernel.LocalFree(text)

    try:
        if not security.OpenProcessToken(wintypes.HANDLE(-1), 8, ctypes.byref(token)): raise _reject(503)
        needed = wintypes.DWORD()
        security.GetTokenInformation(token, 1, None, 0, ctypes.byref(needed))
        if not 0 < needed.value <= 65536: raise _reject(503)
        buffer = ctypes.create_string_buffer(needed.value)
        if not security.GetTokenInformation(token, 1, buffer, needed, ctypes.byref(needed)): raise _reject(503)
        current_sid = sid_string(pointer.from_buffer(buffer))
        allowed = {current_sid, "S-1-5-18", "S-1-5-32-544"}
        owner = pointer(); dacl = pointer()
        if security.GetNamedSecurityInfoW(str(path), 1, 5, ctypes.byref(owner), None,
                ctypes.byref(dacl), None, ctypes.byref(descriptor)) != 0 or not owner or not dacl: raise _reject(503)
        owner_sid = sid_string(owner)
        if owner_sid not in allowed: raise _reject(503)
        # ACL header: revision/sbz1/size/ace_count/sbz2. GetAce bounds-checks entries.
        count = ctypes.c_ushort.from_address(dacl.value+4).value
        for index in range(count):
            ace = pointer()
            if not security.GetAce(dacl, index, ctypes.byref(ace)) or not ace: raise _reject(503)
            kind = ctypes.c_ubyte.from_address(ace.value).value
            flags = ctypes.c_ubyte.from_address(ace.value+1).value
            if flags & 8 or kind == 1: continue  # inherit-only / DENY (grant로 해석0)
            if kind != 0: raise _reject(503)
            mask = ctypes.c_uint32.from_address(ace.value+4).value
            if mask & 0x500D0156:
                sid = sid_string(pointer(ace.value+8))
                if sid not in allowed and not (sid == "S-1-3-4" and owner_sid in allowed): raise _reject(503)
    finally:
        if descriptor: kernel.LocalFree(descriptor)
        if token: kernel.CloseHandle(token)


@dataclass(frozen=True, slots=True, repr=False)
class DatabaseIdentity:
    # PostgreSQL pg_control_system().system_identifier와 실제 pg_database.oid.
    system_identifier: str
    database_oid: int

    def fingerprint(self):
        if (not isinstance(self.system_identifier, str) or not re.fullmatch(r"[1-9][0-9]{0,19}", self.system_identifier)
                or int(self.system_identifier) >= 2**64 or type(self.database_oid) is not int
                or not 0 < self.database_oid < 2**32): raise _reject(503)
        return hashlib.sha256(_json(["postgres-database-v1", self.system_identifier, self.database_oid])).hexdigest()


@dataclass(frozen=True, slots=True, repr=False)
class ObjectIdentity:
    # backend_identifier는 운영자 검증 실제 저장 backend identity다. URL/ref가 아니다.
    backend_identifier: str
    bucket: str

    def fingerprint(self):
        if (not isinstance(self.backend_identifier, str) or not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_-]{0,255}", self.backend_identifier)
                or not isinstance(self.bucket, str) or not re.fullmatch(r"[a-z0-9][a-z0-9.-]{1,61}[a-z0-9]", self.bucket)
                or ".." in self.bucket): raise _reject(503)
        return hashlib.sha256(_json(["object-bucket-v1", self.backend_identifier, self.bucket])).hexdigest()


@dataclass(frozen=True, slots=True, repr=False)
class ProbeRequest:
    source_instance_ref: str
    target_instance_ref: str
    database_ref: str
    bucket_ref: str
    owner_operation_id: str


@dataclass(frozen=True, slots=True, repr=False)
class PhysicalObservation:
    request: ProbeRequest
    source_database: DatabaseIdentity
    target_database: DatabaseIdentity
    source_object: ObjectIdentity
    target_object: ObjectIdentity
    observed_at: datetime
    database_empty: bool
    bucket_empty: bool
    owner_operation_id: str
    application_enabled: bool
    outbound_enabled: bool


class TrustedPhysicalProbe(ABC):
    """운영자 allowlist로만 접속하는 port. 사용자 HTTP 입력으로 구현/주입하지 않는다.

    실제 source/target identity·빈 상태·소유 operation을 같은 관측에서 수집해야 한다.
    ABC 타입 그 자체는 외부 identity 증명이 아니며 concrete probe 인수는 별도 필수다.
    """
    @abstractmethod
    def observe(self, request: ProbeRequest) -> PhysicalObservation: ...


@dataclass(frozen=True,slots=True,repr=False)
class ProbeFoundation:
    """운영자 준비 최소 문맥. archive의 과거 actor/credential을 복사하지 않는다."""
    tenant_id: str
    workspace_id: str
    actor_id: str
    schema_revision: str
    studio_defaults_protection_digest: str | None = None


@dataclass(frozen=True,slots=True,repr=False)
class PhysicalProbeInstance:
    """서버 코드만 구성. 사용자 URL/DSN/key 또는 라이브 비활성 증명이 아니다."""
    database_ref: str
    bucket_ref: str
    database_name: str
    connection_provider: object
    storage: MinioObjectStorageAdapter
    region: str
    foundation: ProbeFoundation | None = None
    provisioning_ref: str | None = None
    application_enabled: bool = True
    outbound_enabled: bool = True


class PostgresMinioPhysicalProbe(TrustedPhysicalProbe):
    """조회만. 초기 registry는 true empty만 소비, R3 fresh=False는 실제 false도 소비.

    후속 phase의 허용 내용/정확 행·보호 정의는 R3가 검증한다. 이 probe가 쓰기 뒤
    재등록/빈 상태 위장·target 생성·owner marker 생성·role 전환을 하지 않는다.
    socket timeout/총 응답 읽기 deadline은 hard OS DNS/process 취소 보장이 아니다.
    """
    def __init__(self,instances,*,clock=None,_pool_factory=PoolManager):
        if not isinstance(instances,dict) or not 1<=len(instances)<=1000: raise _reject(503)
        for ref,instance in instances.items():
            _id(ref)
            if type(instance) is not PhysicalProbeInstance or not callable(instance.connection_provider): raise _reject(503)
            for value in (instance.database_ref,instance.bucket_ref,instance.region): _id(value)
            if (not isinstance(instance.database_name,str) or not 1<=len(instance.database_name.encode())<=63
                or any(ord(c)<32 or ord(c)==127 for c in instance.database_name)
                or not isinstance(instance.storage,MinioObjectStorageAdapter)): raise _reject(503)
            if instance.foundation is not None:
                if type(instance.foundation) is not ProbeFoundation: raise _reject(503)
                for value in (instance.foundation.tenant_id,instance.foundation.workspace_id,instance.foundation.actor_id,instance.foundation.schema_revision): _id(value)
                if instance.foundation.studio_defaults_protection_digest is not None: _digest(instance.foundation.studio_defaults_protection_digest)
        self._instances=MappingProxyType(dict(instances))
        self._clock=clock or (lambda:datetime.now(timezone.utc))
        self._pool_factory=_pool_factory

    @staticmethod
    def _studio_protection(connection,foundation):
        # expected는 operator의 사전 확정 기준만. target 관측 hash를 expected로 등록0.
        expected=foundation.studio_defaults_protection_digest
        if expected is None: raise _reject(503)
        tables=list(connection.execute("SELECT c.relname,pg_catalog.pg_get_userbyid(c.relowner),c.relrowsecurity,c.relforcerowsecurity,"
            "(SELECT jsonb_agg(jsonb_build_array(CASE WHEN a.grantee=0 THEN 'PUBLIC' ELSE pg_catalog.pg_get_userbyid(a.grantee) END,"
            "pg_catalog.pg_get_userbyid(a.grantor),a.privilege_type,a.is_grantable) ORDER BY "
            "CASE WHEN a.grantee=0 THEN 'PUBLIC' ELSE pg_catalog.pg_get_userbyid(a.grantee) END,pg_catalog.pg_get_userbyid(a.grantor),a.privilege_type,a.is_grantable) "
            "FROM pg_catalog.aclexplode(COALESCE(c.relacl,pg_catalog.acldefault('r',c.relowner))) a),"
            "(SELECT jsonb_agg(jsonb_build_array(a.attname,pg_catalog.format_type(a.atttypid,a.atttypmod),a.attnotnull,a.attidentity,a.attgenerated,"
            "pg_catalog.pg_get_expr(d.adbin,d.adrelid),(SELECT jsonb_agg(jsonb_build_array("
            "CASE WHEN ca.grantee=0 THEN 'PUBLIC' ELSE pg_catalog.pg_get_userbyid(ca.grantee) END,"
            "pg_catalog.pg_get_userbyid(ca.grantor),ca.privilege_type,ca.is_grantable) ORDER BY "
            "CASE WHEN ca.grantee=0 THEN 'PUBLIC' ELSE pg_catalog.pg_get_userbyid(ca.grantee) END,"
            "pg_catalog.pg_get_userbyid(ca.grantor),ca.privilege_type,ca.is_grantable) "
            "FROM pg_catalog.aclexplode(CASE WHEN cardinality(a.attacl)>0 THEN a.attacl ELSE NULL::pg_catalog.aclitem[] END) ca)) ORDER BY a.attnum) "
            "FROM pg_catalog.pg_attribute a LEFT JOIN pg_catalog.pg_attrdef d ON d.adrelid=a.attrelid AND d.adnum=a.attnum "
            "WHERE a.attrelid=c.oid AND a.attnum>0 AND NOT a.attisdropped),"
            "(SELECT jsonb_agg(jsonb_build_array(x.conname,pg_catalog.pg_get_constraintdef(x.oid,true),x.convalidated,x.condeferrable,x.condeferred) "
            "ORDER BY x.conname) FROM pg_catalog.pg_constraint x WHERE x.conrelid=c.oid),"
            "(SELECT jsonb_agg(signature ORDER BY signature::text) FROM (SELECT jsonb_build_array("
            "CASE WHEN t.tgisinternal THEN NULL ELSE t.tgname END,t.tgisinternal,t.tgtype,t.tgenabled,t.tgdeferrable,t.tginitdeferred,"
            "pg_catalog.pg_get_constraintdef(x.oid,true),pn.nspname,p.proname,pg_catalog.pg_get_function_identity_arguments(p.oid),"
            "encode(t.tgargs,'hex'),pg_catalog.pg_get_expr(t.tgqual,t.tgrelid),CASE WHEN t.tgisinternal THEN NULL ELSE pg_catalog.pg_get_triggerdef(t.oid,true) END) signature "
            "FROM pg_catalog.pg_trigger t JOIN pg_catalog.pg_proc p ON p.oid=t.tgfoid JOIN pg_catalog.pg_namespace pn ON pn.oid=p.pronamespace "
            "LEFT JOIN pg_catalog.pg_constraint x ON x.oid=t.tgconstraint WHERE t.tgrelid=c.oid) s),"
            "(SELECT jsonb_agg(jsonb_build_array(pol.polname,pol.polcmd,pol.polpermissive,"
            "(SELECT jsonb_agg(CASE WHEN role_id=0 THEN 'PUBLIC' ELSE pg_catalog.pg_get_userbyid(role_id) END "
            "ORDER BY CASE WHEN role_id=0 THEN 'PUBLIC' ELSE pg_catalog.pg_get_userbyid(role_id) END) FROM unnest(pol.polroles) role_id),"
            "pg_catalog.pg_get_expr(pol.polqual,pol.polrelid),pg_catalog.pg_get_expr(pol.polwithcheck,pol.polrelid)) ORDER BY pol.polname) "
            "FROM pg_catalog.pg_policy pol WHERE pol.polrelid=c.oid) "
            "FROM pg_catalog.pg_class c JOIN pg_catalog.pg_namespace n ON n.oid=c.relnamespace "
            "WHERE n.nspname='public' AND c.relname=ANY(%s) AND c.relkind='r' ORDER BY c.relname",(list((*_STUDIO_DEFAULTS,"workspaces")),)))
        functions=list(connection.execute("SELECT p.proname,pg_catalog.pg_get_function_identity_arguments(p.oid),pg_catalog.pg_get_userbyid(p.proowner),"
            "p.prosecdef,p.proconfig,pg_catalog.pg_get_functiondef(p.oid),"
            "(SELECT jsonb_agg(jsonb_build_array(CASE WHEN a.grantee=0 THEN 'PUBLIC' ELSE pg_catalog.pg_get_userbyid(a.grantee) END,"
            "pg_catalog.pg_get_userbyid(a.grantor),a.privilege_type,a.is_grantable) ORDER BY "
            "CASE WHEN a.grantee=0 THEN 'PUBLIC' ELSE pg_catalog.pg_get_userbyid(a.grantee) END,pg_catalog.pg_get_userbyid(a.grantor),a.privilege_type,a.is_grantable) "
            "FROM pg_catalog.aclexplode(COALESCE(p.proacl,pg_catalog.acldefault('f',p.proowner))) a) "
            "FROM pg_catalog.pg_proc p JOIN pg_catalog.pg_namespace n ON n.oid=p.pronamespace WHERE n.nspname='public' "
            "AND p.proname IN ('ensure_studio_workspace_defaults','initialize_studio_workspace_defaults','validate_canon_insert','reject_canon_immutable_mutation') "
            "ORDER BY p.proname,pg_catalog.pg_get_function_identity_arguments(p.oid)"))
        triggers=list(connection.execute("SELECT t.tgname,t.tgenabled,t.tgtype,n.nspname,p.proname,pg_catalog.pg_get_function_identity_arguments(p.oid),"
            "pg_catalog.pg_get_triggerdef(t.oid,true) FROM pg_catalog.pg_trigger t JOIN pg_catalog.pg_proc p ON p.oid=t.tgfoid "
            "JOIN pg_catalog.pg_namespace n ON n.oid=p.pronamespace WHERE t.tgrelid='public.workspaces'::regclass "
            "AND t.tgname='studio_workspace_defaults_after_insert' AND NOT t.tgisinternal"))
        if (len(tables)!=7 or {r[0] for r in tables}!=set((*_STUDIO_DEFAULTS,"workspaces"))
            or any(r[2] is not True or r[3] is not True for r in tables)
            or len(functions)!=4 or {r[0] for r in functions}!={"ensure_studio_workspace_defaults","initialize_studio_workspace_defaults","validate_canon_insert","reject_canon_immutable_mutation"}
            or len(triggers)!=1 or tuple(triggers[0][1:6]) not in {("O",5,"public","initialize_studio_workspace_defaults",""),("A",5,"public","initialize_studio_workspace_defaults","")}): raise _reject(503)
        actual=hashlib.sha256(_json([tables,functions,triggers])).hexdigest()
        if not hmac.compare_digest(expected,actual): raise _reject(503)

    @staticmethod
    def _studio_rows(connection,foundation):
        workspace=connection.execute("SELECT created_at FROM public.workspaces WHERE tenant_id=%s AND workspace_id=%s",
            (foundation.tenant_id,foundation.workspace_id)).fetchone()
        if workspace is None or len(workspace)!=1 or not isinstance(workspace[0],datetime): return False
        if workspace[0].tzinfo is None or workspace[0].utcoffset() is None: return False
        created=workspace[0].astimezone(timezone.utc)
        suffix=hashlib.md5((foundation.tenant_id+"|"+foundation.workspace_id).encode(),usedforsecurity=False).hexdigest()
        parts=("workspace-policy","knowledge-scope","weight-profile","ruleset-reference","ruleset-snapshot","ruleset-binding")
        ids={part:"studio-default:"+part+":"+suffix for part in parts}
        values={"active":True,"current":True,"version":1,"workspace_id":foundation.workspace_id}
        specs={
            "workspace_policies":({**values,"authority_policy":"workspace_admin","data_area":"cloud_sync"},{}),
            "knowledge_scopes":({**values,"scope":"workspace"},{}),
            "weight_profiles":({**values,"profile":"trusted-source-v2"},{"knowledge_scope_id":ids["knowledge-scope"]}),
            "ruleset_references":({**values,"name":"default-review-required"},{}),
            "ruleset_version_snapshots":({**values,"review_condition":"review_required","rules":[]},{"ruleset_reference_id":ids["ruleset-reference"]}),
            "ruleset_bindings":({**values,"review_condition":"review_required","ruleset_version_id":ids["ruleset-snapshot"]},
                {"ruleset_reference_id":ids["ruleset-reference"],"ruleset_version_snapshot_id":ids["ruleset-snapshot"]})}
        exact=True
        for table,part in zip(_STUDIO_DEFAULTS,parts,strict=True):
            rows=list(connection.execute(pgsql.SQL("SELECT to_jsonb(r) FROM {} r LIMIT 2").format(pgsql.Identifier("public",table))))
            if len(rows)!=1: exact=False; continue
            row=rows[0][0]; payload,extra=specs[table]; text=_json(payload).decode()
            expected=dict(tenant_id=foundation.tenant_id,workspace_id=foundation.workspace_id,record_id=ids[part],aggregate_id=ids[part],
                version=1,schema_version=1,previous_version_id=None,canonical_json=payload,canonical_text=text,
                digest_sha256=hashlib.sha256(text.encode()).hexdigest(),created_by="migration:0013",trace_id="migration:0013:"+part+":"+suffix,**extra)
            if (type(row) is not dict or set(row)!=set(expected)|{"created_at"} or type(row.get("version")) is not int
                or type(row.get("schema_version")) is not int): exact=False; continue
            actual={k:v for k,v in row.items() if k!="created_at"}
            if _json(actual)!=_json(expected) or _time(row["created_at"])!=created: exact=False
        return exact

    @staticmethod
    def _empty_database(connection,foundation):
        from .data_canon import _TRANSITIONS
        namespaces=[row[0] for row in connection.execute("SELECT nspname FROM pg_catalog.pg_namespace "
            "WHERE nspname NOT IN ('pg_catalog','information_schema') AND nspname !~ '^pg_(toast|temp)'")]
        if namespaces!=["public"]: raise _reject(503)
        relations=list(connection.execute("SELECT n.nspname,c.relname,c.relkind FROM pg_catalog.pg_class c "
            "JOIN pg_catalog.pg_namespace n ON n.oid=c.relnamespace WHERE n.nspname='public' "
            "AND c.relkind IN ('r','p','f','m','v') ORDER BY c.relname LIMIT 513"))
        if len(relations)>512 or any(row[0]!="public" or row[2] not in {"r","p"} for row in relations): raise _reject(503)
        names={row[1] for row in relations}
        if not {"alembic_version","canon_transition_rules","identity_auth_schema_metadata",*_STUDIO_DEFAULTS}<=names: raise _reject(503)
        PostgresMinioPhysicalProbe._studio_protection(connection,foundation)
        # 업무 nonempty와 보호된 기본행 훼손을 구분한다. fresh=False 소비자도
        # 무결성 실패를 nonempty 정상 재진입으로 해석할 수 없어야 한다.
        if not PostgresMinioPhysicalProbe._studio_rows(connection,foundation): raise _reject()
        empty=True
        foundation_keys={"tenants":("tenant_id",),"workspaces":("tenant_id","workspace_id"),
            "user_accounts":("tenant_id","user_id"),"memberships":("tenant_id","workspace_id","user_id"),
            "identity_auth_workspaces":("tenant_id","workspace_id"),
            "identity_auth_tenant_roles":("tenant_id","user_id"),
            "identity_auth_memberships":("tenant_id","workspace_id","user_id"),
            "identity_auth_tenant_policies":("tenant_id",),"identity_auth_workspace_policies":("tenant_id","workspace_id")}
        for _,name,_ in relations:
            if name in _STUDIO_DEFAULTS: continue  # 위의 전역 exact 검증만 통과한6행, 생략/일반허용0.
            if name=="alembic_version":
                rows=list(connection.execute("SELECT version_num FROM public.alembic_version LIMIT 2"))
                if rows!=[(foundation.schema_revision,)]: empty=False
            elif name=="canon_transition_rules":
                expected=sorted((entity,*edge) for entity,edges in _TRANSITIONS.items() for edge in edges)
                rows=list(connection.execute("SELECT entity_type,source_state,target_state FROM public.canon_transition_rules "
                    "ORDER BY entity_type,source_state,target_state LIMIT 10001"))
                if rows!=expected: empty=False
            elif name=="identity_auth_schema_metadata":
                rows=list(connection.execute("SELECT singleton,schema_version FROM public.identity_auth_schema_metadata LIMIT 2"))
                if rows!=[(1,_POSTGRES_AUTHORIZATION_SCHEMA_VERSION)] or any(type(value) is not int for value in rows[0]): raise _reject(503)
            elif name in foundation_keys:
                keys=foundation_keys[name]
                predicate=pgsql.SQL(" AND ").join(pgsql.SQL("{} IS NOT DISTINCT FROM %s").format(pgsql.Identifier(k)) for k in keys)
                statement=pgsql.SQL("SELECT count(*),count(*) FILTER (WHERE NOT ({})) FROM {}").format(predicate,pgsql.Identifier("public",name))
                values=tuple(foundation.actor_id if k=="user_id" else getattr(foundation,k) for k in keys)
                row=connection.execute(statement,values).fetchone()
                if row is None or len(row)!=2 or any(type(n) is not int or n<0 for n in row): raise _reject(503)
                if row[0]>1 or row[1]!=0: empty=False
            else:
                # 미지의 자료 표도 생략하지 않는다. global operator 읽기에서 한 행이면 false.
                row=connection.execute(pgsql.SQL("SELECT EXISTS(SELECT 1 FROM {} LIMIT 1)").format(pgsql.Identifier("public",name))).fetchone()
                if row is None or len(row)!=1 or type(row[0]) is not bool: raise _reject(503)
                if row[0]: empty=False
        return empty

    def _database(self,instance,owner=None):
        with instance.connection_provider() as connection,connection.transaction():
            connection.execute("SET TRANSACTION ISOLATION LEVEL REPEATABLE READ, READ ONLY")
            row=connection.execute("SELECT current_database(),c.system_identifier::text,d.oid::bigint,"
                "pg_catalog.shobj_description(d.oid,'pg_database'),r.rolsuper,r.rolbypassrls "
                "FROM pg_catalog.pg_control_system() c CROSS JOIN pg_catalog.pg_database d "
                "JOIN pg_catalog.pg_roles r ON r.rolname=current_user WHERE d.datname=current_database()").fetchone()
            if row is None or len(row)!=6 or row[0]!=instance.database_name: raise _reject(503)
            identity=DatabaseIdentity(row[1],row[2]); identity.fingerprint()
            if owner is None: return identity,None
            if row[3]!="daon-restore-owner:"+owner: raise _reject()
            if not (row[4] is True or row[5] is True): raise _reject(503)
            return identity,self._empty_database(connection,instance.foundation)

    @staticmethod
    def _xml(raw,root_name):
        text=raw.decode("utf-8")  # UTF16/null 우회로 entity 선언을 parser에 전달하지 않는다.
        if "<!DOCTYPE" in text.upper() or "<!ENTITY" in text.upper(): raise _reject(503)
        root=ElementTree.fromstring(text)
        namespace="{http://s3.amazonaws.com/doc/2006-03-01/}"
        if root.tag not in {root_name,namespace+root_name}: raise _reject(503)
        for node in root.iter():
            if node.tag.startswith(namespace): node.tag=node.tag[len(namespace):]
            elif "{" in node.tag: raise _reject(503)
        return root

    def _object(self,instance,owner=None):
        client=instance.storage._client
        if type(client._provider) is not StaticProvider: raise _reject(503)
        credentials=client._provider.retrieve()
        if credentials.is_expired(): raise _reject(503)
        base=client._base_url; options=client._http.connection_pool_kw
        if base.is_https and options.get("cert_reqs") not in {"CERT_REQUIRED",ssl.CERT_REQUIRED}: raise _reject(503)
        bucket=instance.storage._bucket
        ObjectIdentity("validation",bucket).fingerprint()
        pool=self._pool_factory(maxsize=1,retries=False,timeout=Timeout(total=5,connect=2,read=2),
            cert_reqs=ssl.CERT_REQUIRED,ca_certs=options.get("ca_certs"))
        try:
            def get(path,query=(),method="GET"):
                url=urlsplit(urlunsplit(("https" if base.is_https else "http",base.host,path,urlencode(sorted(query)),"")))
                now=_utc(self._clock()); payload=hashlib.sha256(b"").hexdigest()
                headers={"Host":url.netloc,"x-amz-date":now.strftime("%Y%m%dT%H%M%SZ"),"x-amz-content-sha256":payload}
                if credentials.session_token: headers["X-Amz-Security-Token"]=credentials.session_token
                headers=sign_v4_s3(method=method,url=url,region=instance.region,headers=headers,credentials=credentials,
                    content_sha256=payload,date=now)
                response=pool.urlopen(method,urlunsplit(url),headers=headers,retries=False,redirect=False,
                    preload_content=False,decode_content=False)
                try:
                    if response.status!=200: raise _reject(503)  # 원 오류/redirect body read0.
                    if method=="HEAD": return b""
                    result=bytearray(); deadline=time.monotonic()+5
                    while True:
                        if time.monotonic()>=deadline: raise _reject(503)
                        size=min(65536,1024*1024+1-len(result))
                        chunk=response.read1(size,decode_content=False)
                        if not isinstance(chunk,bytes) or len(chunk)>size: raise _reject(503)
                        if not chunk: break
                        result.extend(chunk)
                        if len(result)>1024*1024: raise _reject(503)
                    return bytes(result)
                finally:
                    try: response.close()
                    finally: response.release_conn()
            raw=get("/minio/admin/v3/info",(("metrics","false"),("no-cache","true")))
            info=json.loads(raw,object_pairs_hook=_unique,parse_constant=lambda _:(_ for _ in ()).throw(_reject(503)))
            if type(info) is not dict or type(info.get("deploymentID")) is not str: raise _reject(503)
            deployment=UUID(info["deploymentID"])
            if deployment.int==0: raise _reject(503)
            identity=ObjectIdentity(str(deployment),bucket); identity.fingerprint()
            del raw,info  # 서버 상세/endpoint·나머지 응답을 관측/registry/state에 보존0.
            path="/"+quote(bucket,safe="")
            get(path,method="HEAD")
            if owner is None: return identity,None
            root=self._xml(get(path,(("tagging",""),)),"Tagging")
            owners=[]
            for tag in root.findall("./TagSet/Tag"):
                keys=tag.findall("Key"); values=tag.findall("Value")
                if len(keys)!=1 or len(values)!=1: raise _reject(503)
                if keys[0].text=="daon-restore-owner-operation": owners.append(values[0].text)
            if owners!=[owner]: raise _reject()
            empty=True
            for query,root_name,field,children in (
                ((("versions",""),("max-keys","1")),"ListVersionsResult","Name",{"Version","DeleteMarker","CommonPrefixes"}),
                ((("uploads",""),("max-uploads","1")),"ListMultipartUploadsResult","Bucket",{"Upload","CommonPrefixes"})):
                root=self._xml(get(path,query),root_name)
                if len(root.findall(field))!=1 or root.findtext(field)!=bucket: raise _reject(503)
                truncated=root.findall("IsTruncated")
                if len(truncated)!=1 or truncated[0].text not in {"true","false"}: raise _reject(503)
                if truncated[0].text=="true" or any(child.tag in children for child in root): empty=False
            return identity,empty
        finally: pool.clear()

    def observe(self,request):
        try:
            if type(request) is not ProbeRequest: raise _reject()
            for value in (request.source_instance_ref,request.target_instance_ref,request.database_ref,request.bucket_ref,request.owner_operation_id): _id(value)
            if request.source_instance_ref==request.target_instance_ref: raise _reject()
            source=self._instances.get(request.source_instance_ref); target=self._instances.get(request.target_instance_ref)
            if source is None or target is None or (request.database_ref,request.bucket_ref)!=(target.database_ref,target.bucket_ref): raise _reject()
            if (target.foundation is None or target.application_enabled is not False or target.outbound_enabled is not False
                or target.provisioning_ref is None): raise _reject(503)
            _id(target.provisioning_ref)
            source_db,_=self._database(source)
            target_db,database_empty=self._database(target,request.owner_operation_id)
            if source_db.fingerprint()==target_db.fingerprint(): raise _reject()
            source_object,_=self._object(source)
            target_object,bucket_empty=self._object(target,request.owner_operation_id)
            if source_object.fingerprint()==target_object.fingerprint(): raise _reject()
            return PhysicalObservation(request,source_db,target_db,source_object,target_object,_utc(self._clock()),
                database_empty,bucket_empty,request.owner_operation_id,False,False)
        except RecoveryError: raise
        except Exception: raise _reject(503) from None


def physical_receipt_digest(observation: PhysicalObservation):
    if type(observation) is not PhysicalObservation or type(observation.request) is not ProbeRequest: raise _reject(503)
    for identity in (observation.source_database, observation.target_database):
        if type(identity) is not DatabaseIdentity: raise _reject(503)
    for identity in (observation.source_object, observation.target_object):
        if type(identity) is not ObjectIdentity: raise _reject(503)
    # 시간/현재 empty는 stable identity 영수증과 별개로 매 probe 검사한다.
    return hashlib.sha256(_json(["destination-physical-receipt-v1",
        observation.source_database.fingerprint(), observation.target_database.fingerprint(),
        observation.source_object.fingerprint(), observation.target_object.fingerprint(),
        _id(observation.owner_operation_id)])).hexdigest()


@dataclass(frozen=True, slots=True, repr=False)
class RegisteredDestination:
    registry_generation_id: str
    destination_id: str
    version: int
    display_name: str
    expires_at: str
    tenant_id: str
    workspace_id: str
    actor_id: str
    physical_receipt_digest: str
    target_database_fingerprint: str
    target_bucket_fingerprint: str
    owner_operation_id: str
    observed_at: datetime

    def summary(self):
        return dict(destination_id=self.destination_id, version=self.version, display_name=self.display_name, expires_at=self.expires_at)


class SignedDestinationRegistry:
    def __init__(self, root, master_keys, source_instance_ref, *, clock=None):
        self._root = Path(root)
        if not self._root.is_absolute() or ".." in self._root.parts: raise _reject(503)
        if not isinstance(master_keys, dict) or not master_keys: raise _reject(503)
        self._keys = {}
        for version, key in master_keys.items():
            _id(version)
            if not isinstance(key, bytes) or len(key) < 32: raise _reject(503)
            self._keys[version] = key
        self._source = _id(source_instance_ref)
        self._clock = clock or (lambda: datetime.now(timezone.utc))
        self._fs = _WindowsFiles() if os.name == "nt" else _PosixFiles()
        self._root_identity = None
        self._generation_digests = {}
        self._versions = {}

    def _read(self):
        try:
            with self._fs.pin(self._root):
                identity = self._fs.identity(self._root)
                if self._root_identity is not None and self._root_identity != identity: raise _reject(503)
                if os.name == "nt": _windows_protected(self._root)
                with self._fs.file(self._root / "destinations.json") as stream:
                    if os.name == "nt": _windows_protected(self._root / "destinations.json")
                    info = os.fstat(stream.fileno())
                    if (not stat.S_ISREG(info.st_mode) or info.st_nlink != 1 or info.st_size > MAX_REGISTRY_BYTES
                            or (os.name == "posix" and (info.st_uid != os.geteuid() or info.st_mode & 0o077))): raise _reject(503)
                    raw = stream.read(MAX_REGISTRY_BYTES+1)
                    after = os.fstat(stream.fileno())
                    if len(raw) > MAX_REGISTRY_BYTES or any(getattr(after, field) != getattr(info, field)
                            for field in ("st_dev", "st_ino", "st_size", "st_mtime_ns", "st_nlink")): raise _reject(503)
                self._root_identity = identity
            document = json.loads(raw, object_pairs_hook=_unique,
                                  parse_constant=lambda _: (_ for _ in ()).throw(_reject()))
            if not isinstance(document, dict) or set(document) != {"schema_version", "generation_id", "entries", "key_version", "signature"}: raise _reject()
            if type(document["schema_version"]) is not int or document["schema_version"] != 1: raise _reject()
            version = _id(document["key_version"]); generation = _id(document["generation_id"])
            master = self._keys.get(version)
            if master is None: raise _reject(503)
            signature = _digest(document["signature"])
            payload = {key: value for key, value in document.items() if key != "signature"}
            key = HKDF(algorithm=hashes.SHA256(), length=32, salt=hashlib.sha256(PURPOSE).digest(),
                       info=PURPOSE+b"/"+version.encode()).derive(master)
            if not hmac.compare_digest(signature, hmac.new(key, _json(payload), hashlib.sha256).hexdigest()): raise _reject()
            entries = document["entries"]
            if not isinstance(entries, list) or len(entries) > 1000: raise _reject()
            ids = set(); validated = []
            for entry in entries:
                self._validate_entry(entry)
                if entry["destination_id"] in ids: raise _reject()
                ids.add(entry["destination_id"]); validated.append(dict(entry))
            digest = hashlib.sha256(_json(payload)).hexdigest()
            previous = self._generation_digests.get(generation)
            if previous is not None and previous != digest: raise _reject()
            for entry in validated:
                old = self._versions.get(entry["destination_id"])
                entry_digest = hashlib.sha256(_json(entry)).hexdigest()
                if old and (entry["version"] < old[0] or (entry["version"] == old[0] and entry_digest != old[1])): raise _reject()
            self._generation_digests[generation] = digest
            for entry in validated: self._versions[entry["destination_id"]] = (entry["version"], hashlib.sha256(_json(entry)).hexdigest())
            return generation, validated
        except RecoveryError: raise
        except (OSError, ValueError, TypeError, UnicodeError, RecursionError): raise _reject(503) from None

    @staticmethod
    def _validate_entry(entry):
        if not isinstance(entry, dict) or set(entry) != _ENTRY_FIELDS: raise _reject()
        for field in ("destination_id", "source_instance_ref", "authorized_tenant_id", "authorized_workspace_id", "authorized_actor_id",
                      "target_instance_ref", "database_ref", "bucket_ref", "owner_operation_id"): _id(entry[field])
        if type(entry["version"]) is not int or not 0 < entry["version"] <= 2147483647: raise _reject()
        if entry["capture_kind"] not in {LINEAGE_KIND, "service-snapshot-v1"}: raise _reject()
        name = entry["display_name"]
        if (not isinstance(name, str) or not 1 <= len(name) <= 80 or name != name.strip()
                or any(ord(c) < 32 or ord(c) == 127 or c in "<>:/\\" for c in name)): raise _reject()
        _digest(entry["physical_receipt_digest"])
        created, expires = _time(entry["created_at"]), _time(entry["expires_at"])
        if not timedelta(0) < expires-created <= timedelta(hours=24): raise _reject()
        if entry["application_enabled"] is not False or entry["outbound_enabled"] is not False: raise _reject()

    def _eligible(self, context, entry):
        if not isinstance(context, RecoveryContext): raise _reject()
        return (entry["capture_kind"] == LINEAGE_KIND and entry["source_instance_ref"] == self._source
                and (entry["authorized_tenant_id"], entry["authorized_workspace_id"], entry["authorized_actor_id"]) ==
                (context.tenant_id, context.workspace_id, context.actor_id))

    def _resolve(self, context, generation, entry, probe):
        now = _utc(self._clock())
        if not self._eligible(context, entry) or not _time(entry["created_at"]) <= now < _time(entry["expires_at"]): raise _reject()
        if not isinstance(probe, TrustedPhysicalProbe): raise _reject(503)
        request = ProbeRequest(self._source, entry["target_instance_ref"], entry["database_ref"], entry["bucket_ref"], entry["owner_operation_id"])
        if request.source_instance_ref == request.target_instance_ref: raise _reject()
        try: observation = probe.observe(request)
        except RecoveryError: raise
        except Exception: raise _reject(503) from None
        receipt = physical_receipt_digest(observation)
        now = _utc(self._clock())
        if (observation.request != request or not timedelta(0) <= now-_utc(observation.observed_at) <= timedelta(seconds=30)
                or not _time(entry["created_at"]) <= now < _time(entry["expires_at"])
                or observation.database_empty is not True or observation.bucket_empty is not True
                or observation.owner_operation_id != request.owner_operation_id or observation.application_enabled is not False
                or observation.outbound_enabled is not False or receipt != entry["physical_receipt_digest"]): raise _reject()
        source_db, target_db = observation.source_database.fingerprint(), observation.target_database.fingerprint()
        source_bucket, target_bucket = observation.source_object.fingerprint(), observation.target_object.fingerprint()
        if source_db == target_db or source_bucket == target_bucket: raise _reject()
        return RegisteredDestination(generation, entry["destination_id"], entry["version"], entry["display_name"], entry["expires_at"],
            context.tenant_id, context.workspace_id, context.actor_id, receipt, target_db, target_bucket, entry["owner_operation_id"], observation.observed_at)

    def resolve_registered_destination(self, context, destination_id, expected_version, probe):
        _id(destination_id)
        if type(expected_version) is not int or expected_version <= 0: raise _reject()
        generation, entries = self._read()
        if not any(entry["destination_id"] == destination_id and entry["version"] == expected_version and self._eligible(context, entry)
                   for entry in entries): raise _reject()
        for value in self._destinations(context, generation, entries, probe):
            if value.destination_id == destination_id: return value
        raise _reject()

    def _destinations(self, context, generation, entries, probe):
        result = []; databases = set(); buckets = set()
        now = _utc(self._clock())
        for entry in entries:
            if not self._eligible(context, entry) or not _time(entry["created_at"]) <= now < _time(entry["expires_at"]): continue
            value = self._resolve(context, generation, entry, probe)
            if value.target_database_fingerprint in databases or value.target_bucket_fingerprint in buckets: raise _reject()
            databases.add(value.target_database_fingerprint); buckets.add(value.target_bucket_fingerprint)
            result.append(value)
        return result

    def summaries(self, context, probe):
        generation, entries = self._read()
        return [value.summary() for value in self._destinations(context, generation, entries, probe)]


_RESERVATION_COLUMNS = ("registry_generation_id", "destination_id", "tenant_id", "workspace_id", "request_id", "actor_id",
    "destination_version", "backup_id", "manifest_version", "source_manifest_digest", "physical_receipt_digest",
    "target_database_fingerprint", "target_bucket_fingerprint", "owner_operation_id")


def reserve_destination(connection, context, destination, *, request_id, backup_id, manifest_digest, clock=None):
    """caller의 기존 transaction에만 참여. 실패 시 caller 전체 rollback, commit/close0.

    실 concurrency 보장은 0070 draft의 개별 GLOBAL UNIQUE와 parent-row 잠금이다.
    exact replay도 현재 registry/probe/주체를 먼저 다시 확인한 결과만 주입한다.
    """
    if type(destination) is not RegisteredDestination: raise _reject()
    if (destination.tenant_id, destination.workspace_id, destination.actor_id) != (context.tenant_id, context.workspace_id, context.actor_id): raise _reject()
    now = _utc((clock or (lambda: datetime.now(timezone.utc)))())
    if now >= _time(destination.expires_at) or not timedelta(0) <= now-_utc(destination.observed_at) <= timedelta(seconds=30): raise _reject()
    values = (destination.registry_generation_id, destination.destination_id, context.tenant_id, context.workspace_id,
        _id(request_id), context.actor_id, destination.version, _id(backup_id), 3, _digest(manifest_digest),
        _digest(destination.physical_receipt_digest), _digest(destination.target_database_fingerprint),
        _digest(destination.target_bucket_fingerprint), _id(destination.owner_operation_id))
    columns = ", ".join(_RESERVATION_COLUMNS)
    connection.execute(f"INSERT INTO public.registered_restore_target_reservations ({columns}) VALUES ({', '.join(['%s']*len(values))}) ON CONFLICT DO NOTHING", values)
    row = connection.execute(f"SELECT {columns} FROM public.registered_restore_target_reservations WHERE registry_generation_id = %s AND destination_id = %s",
                             values[:2]).fetchone()
    if row is None or tuple(row) != values: raise _reject()
