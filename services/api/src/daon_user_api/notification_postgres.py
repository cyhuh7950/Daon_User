"""현재 서버 범위에 결속한 지속 알림 저장소. 원업무 resolver 연결은 별도 단계다."""
from __future__ import annotations

import base64
import hashlib
import json
import re
from contextlib import contextmanager
from dataclasses import replace
from datetime import datetime, timezone
from typing import Callable

from psycopg import Error, connect
from psycopg.rows import dict_row
from psycopg.types.json import Jsonb

from .audit import AuditEventDraft, _validate_draft
from .cloud_storage import CloudAccessContext
from .notification import DeliveryState, Notification, NotificationError, ReadTransition, _checked_id, _checked_time


def _time(value):
    if value is None:
        return None
    if isinstance(value, str):
        try:
            value = datetime.fromisoformat(value.replace("Z", "+00:00"))
        except ValueError:
            raise NotificationError("DATABASE_RESPONSE_INVALID", 503) from None
    if not isinstance(value, datetime) or value.tzinfo is None:
        raise NotificationError("DATABASE_RESPONSE_INVALID", 503)
    return value.astimezone(timezone.utc)


def notification_from_row(row) -> Notification:
    """변환 실패는 저장 transaction 안에서 발생시켜 부분 성공을 남기지 않는다."""
    try:
        return Notification(notification_id=_checked_id(row["notification_id"]),
            tenant_id=_checked_id(row["tenant_id"]), workspace_id=_checked_id(row["workspace_id"]),
            recipient_id=_checked_id(row["recipient_id"]), kind=row["kind"], severity=row["severity"],
            title=row["title"], summary=row["summary"], source_event_id=_checked_id(row["source_event_id"]),
            resource_type=_checked_id(row["resource_type"]), resource_id=_checked_id(row["resource_id"]),
            deep_link=row["deep_link"], audit_event_id=_checked_id(row["audit_event_id"]),
            trace_id=_checked_id(row["trace_id"]), delivery_state=DeliveryState(row["delivery_state"]),
            created_at=_time(row["created_at"]), delivered_at=_time(row["delivered_at"]),
            read_at=_time(row["read_at"]), version=int(row["version"]))
    except (KeyError, ValueError, TypeError):
        raise NotificationError("DATABASE_RESPONSE_INVALID", 503) from None


def serialize_notification_audit(draft: AuditEventDraft) -> dict:
    checked = _validate_draft(draft)
    # 중앙 감사의 기존 검증을 먼저 적용한다. 입력 검증 불가와 전달 장애를 구분한다.
    return json.loads(json.dumps(checked, ensure_ascii=False, sort_keys=True, allow_nan=False,
        default=lambda value: value.isoformat() if isinstance(value, datetime) else dict(value)))


_SELECT = """
SELECT n.*,d.kind,d.severity,d.title,d.summary,d.resource_type,d.resource_id,
  d.deep_link,d.trace_id,d.delivery_state,d.delivered_at,
  'audit-notification-created-'||md5(n.tenant_id||'|'||n.notification_id) AS audit_event_id
FROM public.notifications n JOIN public.notification_details d
  ON d.tenant_id=n.tenant_id AND d.notification_id=n.notification_id
WHERE n.tenant_id=%s AND n.workspace_id=%s AND n.recipient_id=%s
"""


class PostgresNotificationRepository:
    """요청의 인증된 tenant/Workspace/actor만 받는다. 전역 조회·암묵 seed는 없다.

    L1은 내부 저장 경계다. 원자료 현재 접근 resolver와 실제 producer가 없는 동안
    runtime의 운영 발행/조회 경로를 이 adapter로 활성화하지 않는다.
    """
    transactional_audit = True

    def __init__(self, dsn: str, *, context: CloudAccessContext):
        if not isinstance(context, CloudAccessContext) or context.capability != "view":
            raise ValueError("NOTIFICATION_SCOPE_INVALID")
        if not isinstance(dsn, str) or not dsn:
            raise ValueError("NOTIFICATION_DSN_REQUIRED")
        self._dsn, self.context = dsn, context

    @contextmanager
    def transaction(self):
        try:
            with connect(self._dsn, connect_timeout=3, row_factory=dict_row) as connection:
                with connection.transaction():
                    for name, value in (("tenant_id", self.context.tenant_id), ("workspace_id", self.context.workspace_id),
                                        ("actor_id", self.context.actor_id), ("capability", "view")):
                        connection.execute("SELECT set_config(%s,%s,true)", ("app." + name, value))
                    connection.execute("SET LOCAL statement_timeout='5s'")
                    connection.execute("SET LOCAL lock_timeout='2s'")
                    yield connection
        except Error as error:
            code, status = {"P0002": ("RESOURCE_UNAVAILABLE", 404), "P0003": ("VERSION_CONFLICT", 412),
                "23505": ("IDEMPOTENCY_CONFLICT", 409), "55000": ("INVALID_STATE_TRANSITION", 409),
                "22023": ("INVALID_INPUT", 400)}.get(error.sqlstate, ("DATABASE_UNAVAILABLE", 503))
            raise NotificationError(code, status) from None

    def _scope(self, tenant_id, recipient_id):
        if tenant_id != self.context.tenant_id or recipient_id != self.context.actor_id:
            raise NotificationError("RESOURCE_UNAVAILABLE", 404)
        return (tenant_id, self.context.workspace_id, recipient_id)

    def insert_if_absent(self, *_args):
        # 원장 resolver/producer가 연결되기 전 reference publish로 지속 사건을 합성하지 않는다.
        raise NotificationError("NOTIFICATION_SOURCE_NOT_CONNECTED", 503)

    def notifications_for(self, *, tenant_id, recipient_id):
        scope = self._scope(tenant_id, recipient_id)
        with self.transaction() as connection:
            return tuple(notification_from_row(row) for row in connection.execute(
                _SELECT + " ORDER BY n.created_at DESC,n.notification_id DESC", scope).fetchall())

    def notification_for(self, *, tenant_id, recipient_id, notification_id):
        scope = self._scope(tenant_id, recipient_id)
        with self.transaction() as connection:
            row = connection.execute(_SELECT + " AND n.notification_id=%s", (*scope, _checked_id(notification_id))).fetchone()
            return None if row is None else notification_from_row(row)

    def transition_read(self, *, notification_id, tenant_id, recipient_id, expected_etag,
                        idempotency_key, fingerprint, read_at, on_first_transition,
                        audit_draft_factory=None):
        scope = self._scope(tenant_id, recipient_id)
        match = re.fullmatch(r'"notification-([1-9][0-9]*)"', expected_etag or "")
        if match is None:
            raise NotificationError("VERSION_CONFLICT", 412)
        if audit_draft_factory is None:
            raise NotificationError("NOTIFICATION_AUDIT_REQUIRED", 503)
        with self.transaction() as connection:
            row = connection.execute(_SELECT + " AND n.notification_id=%s", (*scope, _checked_id(notification_id))).fetchone()
            if row is None:
                raise NotificationError("RESOURCE_UNAVAILABLE", 404)
            item = notification_from_row(row)
            # replay에서도 요청의 원 version으로 draft를 구성하되 SQL은 영수증을 먼저 판정한다.
            original = replace(item, version=int(match[1]), read_at=None)
            after = replace(original, read_at=_checked_time(read_at), version=original.version + 1)
            draft = replace(audit_draft_factory(original, after), event_id="audit-notification-read-" + hashlib.sha256(
                f"{tenant_id}|{self.context.workspace_id}|{recipient_id}|{notification_id}".encode()).hexdigest()[:32])
            payload = serialize_notification_audit(draft)
            result = connection.execute("SELECT public.read_notification_inbox(%s,%s,%s,%s,%s) AS receipt",
                (notification_id, int(match[1]), idempotency_key, fingerprint, Jsonb(payload))).fetchone()["receipt"]
            updated = replace(item, read_at=_time(result["read_at"]), version=int(result["version"]))
            return ReadTransition(updated, result["first_transition"])

    def encode_cursor(self, item_id):
        return base64.urlsafe_b64encode(_checked_id(item_id).encode("ascii")).decode("ascii").rstrip("=")

    def decode_cursor(self, cursor):
        try:
            if not isinstance(cursor, str) or not 1 <= len(cursor) <= 512:
                raise ValueError
            item_id = _checked_id(base64.b64decode(cursor + "=" * (-len(cursor) % 4), altchars=b"-_", validate=True).decode("ascii"))
            if self.encode_cursor(item_id) != cursor:
                raise ValueError
            return item_id
        except (ValueError, UnicodeError):
            raise NotificationError("INVALID_CURSOR") from None


_FUNCTIONS = {
    'guard_notification_storage()': ('8943eedc40d6a3bbf1553cd0f1b2f652', False),
    'notification_parent_is_protected(text,text,text)': ('7e00b3b39175cc9f01e4ce9d8763eab7', True),
    'guard_notification_parent()': ('7022f7ea6cee704464a284f87a5954ea', False),
    'read_notification_inbox(text,integer,text,text,jsonb)': ('efb5fc74a837a6ac60a03313ca9b4bea', True),
    'pending_notification_audits(integer)': ('8d2c769634b2404fe529a2b9f3dbcf8f', True),
    'ack_notification_audit(text,text)': ('f6d1ec7ab371ffd75a42c8502aac2dbd', True),
    'enqueue_studio_notification(text,text,text)': ('3bc0696eea9259160aaffab394a8cf4d', True),
    'enqueue_completed_notification(text,text)': ('6a2d101595decb776a35357ba6e7dd1c', True),
    'pending_studio_notification_sources(integer)': ('74b6119e2e33fc321fb1cca3ff66d928', True),
    'current_studio_notification_version(text)': ('a7134c5d8d15b5fb318f004a0ccadc71', True),
    'materialize_studio_notification(text,boolean)': ('bf371f25e55f88e0d853bd56f879a90f', True),
    'current_completed_notification_target(text)': ('84343502194b1cf8433b45cd2e9db255', True),
    'pending_completed_notification_sources(integer)': ('d58f0bd06ae9325c8f68278740930d0d', True),
    'materialize_completed_notification(text,boolean)': ('64b55897b38b012be495378f5fa42b2e', True),
}
_PRIVATE_FUNCTIONS = frozenset({'current_studio_notification_version(text)','current_completed_notification_target(text)'})
_ID = " ~ '^[A-Za-z0-9][A-Za-z0-9._:-]{0,127}$'"
_STORE = "source_store IN ('identity','authorization','organization','document','question','studio','recovery')"
_KIND = "kind IN ('authentication','device','membership','policy','run_state','run_failed','review','approval','delivery','warning','recovery')"
_SEVERITY = "severity IN ('info','warning','critical')"
_ACK = "(state='pending' AND delivered_at IS NULL AND version=1) OR (state IN ('delivered','suppressed') AND delivered_at IS NOT NULL AND version=2)"
_CHECKS = {
    'notification_source_outbox': [field + _ID for field in ('source_event_id','origin_event_id','action','target_type','target_id','notebook_id','trace_id','policy_version')]
        + [_STORE,_KIND,_SEVERITY,"state IN ('pending','delivered','suppressed')",'version>0',_ACK],
    'notification_details': [field + _ID for field in ('notification_id','origin_event_id','source_event_id','resource_type','resource_id','notebook_id','trace_id','policy_version')]
        + [_STORE,_KIND,_SEVERITY,r"length(title) BETWEEN 1 AND 160 AND title=btrim(title) AND title !~ '[<>\x01-\x1f]'",
            r"length(summary) BETWEEN 1 AND 500 AND summary=btrim(summary) AND summary !~ '[<>\x01-\x1f]'",
            r"length(deep_link) BETWEEN 1 AND 512 AND deep_link !~ '[\x01-\x20]' AND (deep_link ~ '^/(operations|inbox|notifications|workspaces)([/?:#]|$)' OR deep_link ~ '^/notebooks/[A-Za-z0-9][A-Za-z0-9._:-]{0,127}[?](output_version_id|source_version_id|run_id)=[A-Za-z0-9][A-Za-z0-9._:-]{0,127}$') AND deep_link !~ '^//'",
            "delivery_state IN ('pending','delivered','failed','suppressed')","(delivery_state='delivered')=(delivered_at IS NOT NULL)"],
    'notification_audit_outbox': ['event_id'+_ID,"action IN ('notification.created','notification.read')",
        "jsonb_typeof(draft)='object'","fingerprint ~ '^[0-9a-f]{64}$'","state IN ('pending','delivered')",'version>0',
        "(state='pending' AND delivered_at IS NULL AND version=1) OR (state='delivered' AND delivered_at IS NOT NULL AND version=2)"],
}

# 0069가 의존하는 다섯 표만 검사한다. 키 순서·참조 표/열·즉시 검증 의미까지 고정한다.
_KEYS = {
    'notification_source_outbox': [
        ('p',('tenant_id','workspace_id','source_event_id','recipient_id'),None,()),
        ('f',('tenant_id','workspace_id'),'workspaces',('tenant_id','workspace_id')),
        ('f',('tenant_id','recipient_id'),'user_accounts',('tenant_id','user_id'))],
    'notification_details': [
        ('p',('tenant_id','notification_id'),None,()),
        ('f',('tenant_id','notification_id'),'notifications',('tenant_id','notification_id'))],
    'notification_audit_outbox': [
        ('p',('event_id',),None,()),
        ('u',('tenant_id','notification_id','action'),None,()),
        ('f',('tenant_id','workspace_id'),'workspaces',('tenant_id','workspace_id'))],
    'notifications': [
        ('p',('tenant_id','notification_id'),None,()),
        ('u',('tenant_id','recipient_id','source_event_id'),None,()),
        ('f',('tenant_id','workspace_id'),'workspaces',('tenant_id','workspace_id')),
        ('f',('tenant_id','recipient_id'),'user_accounts',('tenant_id','user_id'))],
    'idempotency_records': [
        ('p',('tenant_id','workspace_id','actor_id','operation','idempotency_key'),None,()),
        ('f',('tenant_id','workspace_id'),'workspaces',('tenant_id','workspace_id'))],
}
_COLUMNS = {
    'notification_source_outbox': ('tenant_id','workspace_id','source_event_id','recipient_id','source_store',
        'origin_event_id','action','target_type','target_id','kind','severity','notebook_id','trace_id',
        'policy_version','occurred_at','state','delivered_at','version'),
    'notification_details': ('tenant_id','notification_id','source_store','origin_event_id','source_event_id',
        'kind','severity','title','summary','resource_type','resource_id','notebook_id','deep_link',
        'trace_id','policy_version','delivery_state','delivered_at'),
    'notification_audit_outbox': ('event_id','tenant_id','workspace_id','recipient_id','notification_id',
        'source_event_id','action','draft','fingerprint','state','delivered_at','version'),
    'notifications': ('tenant_id','workspace_id','notification_id','recipient_id','source_event_id',
        'read_at','version','created_at'),
    'idempotency_records': ('tenant_id','workspace_id','actor_id','operation','idempotency_key',
        'request_fingerprint','result','status','expires_at','created_at'),
}


def _normalize_expression(expression):
    expression = re.sub(r'::text\b', '', expression)
    expression = re.sub(r'=\s*ANY\s*\(\s*ARRAY\[(.*?)\]\s*\)', r' IN (\1)', expression, flags=re.S | re.I)
    expression = re.sub(r'(length\(\w+\)) BETWEEN (\d+) AND (\d+)', r'\1>=\2 AND \1<=\3', expression, flags=re.I)
    parts = re.split(r"('(?:''|[^'])*')", expression)
    return ''.join(part if index % 2 else re.sub(r'[\s()]', '', part).lower() for index, part in enumerate(parts))


def notification_storage_is_protected(connection) -> bool:
    """승인 후보의 owner·RLS·정확 CHECK/정책·함수 본문·실행 ACL을 읽기만 검증한다."""
    def values(row): return tuple(row.values()) if isinstance(row, dict) else tuple(row)
    try:
        roles = connection.execute("""SELECT count(*)=3 AND bool_and(NOT rolcanlogin AND NOT rolsuper AND NOT rolbypassrls
            AND NOT rolcreaterole AND NOT rolcreatedb) FROM pg_catalog.pg_roles
            WHERE rolname IN ('daon_notebook_delete','daon_account_owner','daon_organization_owner')""").fetchone()
        if not roles or not values(roles)[0]: return False
        if values(connection.execute("SELECT pg_catalog.pg_has_role('daon_app','daon_notebook_delete','MEMBER') OR NOT pg_catalog.pg_has_role('daon_notebook_delete','daon_app','MEMBER')").fetchone())[0]: return False
        for table, keys in _KEYS.items():
            if table in {'notifications','idempotency_records'}:
                row = connection.execute("""SELECT relowner='daon_app'::regrole AND relrowsecurity AND relforcerowsecurity
                    FROM pg_catalog.pg_class WHERE oid=pg_catalog.to_regclass(%s)""",('public.'+table,)).fetchone()
                if not row or not values(row)[0]: return False
            rows = connection.execute("""SELECT k.contype,
                ARRAY(SELECT a.attname::text FROM unnest(k.conkey) WITH ORDINALITY x(num,pos)
                    JOIN pg_catalog.pg_attribute a ON a.attrelid=k.conrelid AND a.attnum=x.num ORDER BY x.pos),
                CASE WHEN k.contype='f' THEN rn.nspname END, r.relname,
                ARRAY(SELECT a.attname::text FROM unnest(k.confkey) WITH ORDINALITY x(num,pos)
                    JOIN pg_catalog.pg_attribute a ON a.attrelid=k.confrelid AND a.attnum=x.num ORDER BY x.pos),
                k.convalidated,NOT k.condeferrable AND NOT k.condeferred,
                CASE WHEN k.contype='f' THEN k.confupdtype='a' AND k.confdeltype='a' AND k.confmatchtype='s'
                    ELSE EXISTS(SELECT 1 FROM pg_catalog.pg_index i WHERE i.indexrelid=k.conindid
                      AND i.indisunique AND i.indisvalid AND i.indisready AND i.indimmediate
                      AND i.indnkeyatts=cardinality(k.conkey) AND i.indpred IS NULL AND i.indexprs IS NULL) END
                FROM pg_catalog.pg_constraint k LEFT JOIN pg_catalog.pg_class r ON r.oid=k.confrelid
                LEFT JOIN pg_catalog.pg_namespace rn ON rn.oid=r.relnamespace
                WHERE k.conrelid=pg_catalog.to_regclass(%s) AND k.contype IN ('p','u','f')""",('public.'+table,)).fetchall()
            actual_keys = set()
            for row in rows:
                kind, columns, namespace, target, targets, validated, immediate, rules = values(row)
                if not (validated and immediate and rules) or (kind=='f' and namespace!='public'): return False
                actual_keys.add((kind,tuple(columns),target,tuple(targets)))
            if len(rows)!=len(keys) or actual_keys!=set(keys): return False
            rows = connection.execute("""SELECT a.attname,pg_catalog.format_type(a.atttypid,a.atttypmod),a.attnotnull,
                pg_catalog.pg_get_expr(d.adbin,d.adrelid),a.attidentity,a.attgenerated
                FROM pg_catalog.pg_attribute a LEFT JOIN pg_catalog.pg_attrdef d ON d.adrelid=a.attrelid AND d.adnum=a.attnum
                WHERE a.attrelid=pg_catalog.to_regclass(%s) AND a.attnum>0 AND NOT a.attisdropped""",('public.'+table,)).fetchall()
            if len(rows)!=len(_COLUMNS[table]) or {values(r)[0] for r in rows}!=set(_COLUMNS[table]): return False
            for row in rows:
                name, kind, required, default, identity, generated = values(row)
                expected_kind = ('integer' if name=='version' else 'jsonb' if name in {'draft','result'}
                    else 'timestamp with time zone' if name in {'occurred_at','delivered_at','read_at','created_at','expires_at'} else 'text')
                expected_default = ('1' if name=='version' else "'pending'" if name=='state'
                    else 'now' if name=='created_at' else None)
                if (kind,required,identity,generated)!=(expected_kind,name not in {'notebook_id','delivered_at','read_at'},'',''): return False
                if (None if default is None else _normalize_expression(default))!=expected_default: return False
            if table in {'notifications','idempotency_records'}:
                expected_checks = ['version>0'] if table=='notifications' else ["status IN ('completed','failed')"]
                rows = connection.execute("SELECT pg_catalog.pg_get_expr(conbin,conrelid) FROM pg_catalog.pg_constraint WHERE conrelid=pg_catalog.to_regclass(%s) AND contype='c' AND convalidated",('public.'+table,)).fetchall()
                if sorted(_normalize_expression(values(r)[0]) for r in rows)!=sorted(map(_normalize_expression,expected_checks)): return False
                policy = table+'_scope'
                expression = "tenant_id=nullif(current_setting('app.tenant_id',true),'') AND workspace_id=nullif(current_setting('app.workspace_id',true),'')"
                rows = connection.execute("""SELECT polname,polcmd,polpermissive,polroles=ARRAY[0::oid],
                    pg_catalog.pg_get_expr(polqual,polrelid),pg_catalog.pg_get_expr(polwithcheck,polrelid)
                    FROM pg_catalog.pg_policy WHERE polrelid=pg_catalog.to_regclass(%s)""",('public.'+table,)).fetchall()
                if len(rows)!=1: return False
                name,command,permissive,public,using,check = values(rows[0])
                if (name,command,permissive,public)!=(policy,'*',True,True) or using is None or check is None: return False
                if _normalize_expression(using)!=_normalize_expression(expression) or _normalize_expression(check)!=_normalize_expression(expression): return False
        scope = "tenant_id=nullif(current_setting('app.tenant_id',true),'') AND workspace_id=nullif(current_setting('app.workspace_id',true),'') AND recipient_id=nullif(current_setting('app.actor_id',true),'')"
        policies = {
            'notification_source_outbox': [('notification_source_scope','*','daon_notebook_delete',scope)],
            'notification_audit_outbox': [('notification_audit_scope','*','daon_notebook_delete',scope)],
            'notification_details': [('notification_detail_recipient','*','daon_app',"""tenant_id=nullif(current_setting('app.tenant_id',true),'') AND EXISTS(
              SELECT 1 FROM public.notifications n WHERE n.tenant_id=notification_details.tenant_id
              AND n.notification_id=notification_details.notification_id
              AND n.workspace_id=nullif(current_setting('app.workspace_id',true),'')
              AND n.recipient_id=nullif(current_setting('app.actor_id',true),''))"""),
              ('notification_detail_guard_scope','r','daon_notebook_delete',"""tenant_id=nullif(current_setting('app.tenant_id',true),'') AND EXISTS(
              SELECT 1 FROM public.notifications n WHERE n.tenant_id=notification_details.tenant_id
              AND n.notification_id=notification_details.notification_id
              AND n.workspace_id=nullif(current_setting('app.workspace_id',true),''))""")],
        }
        for table, checks in _CHECKS.items():
            row = connection.execute("""SELECT c.relowner='daon_notebook_delete'::regrole AND c.relrowsecurity AND c.relforcerowsecurity
                AND NOT EXISTS(SELECT 1 FROM pg_catalog.aclexplode(coalesce(c.relacl,pg_catalog.acldefault('r',c.relowner))) a
                  WHERE a.grantee NOT IN ('daon_app'::regrole,'daon_notebook_delete'::regrole) OR a.is_grantable)
                AND NOT pg_catalog.has_table_privilege('daon_app',c.oid,'INSERT,UPDATE,DELETE,TRUNCATE,REFERENCES,TRIGGER')
                AND NOT EXISTS(SELECT 1 FROM pg_catalog.pg_attribute WHERE attrelid=c.oid AND attacl IS NOT NULL)
                FROM pg_catalog.pg_class c WHERE c.oid=pg_catalog.to_regclass(%s)""", ('public.'+table,)).fetchone()
            if not row or not values(row)[0]: return False
            definitions = connection.execute("SELECT pg_catalog.pg_get_expr(conbin,conrelid) FROM pg_catalog.pg_constraint WHERE conrelid=pg_catalog.to_regclass(%s) AND contype='c' AND convalidated",('public.'+table,)).fetchall()
            if sorted(_normalize_expression(values(r)[0]) for r in definitions) != sorted(map(_normalize_expression,checks)): return False
            rows = connection.execute("SELECT count(*) FROM pg_catalog.pg_policy WHERE polrelid=pg_catalog.to_regclass(%s)",('public.'+table,)).fetchone()
            if values(rows)[0]!=len(policies[table]): return False
            for name, command, role, expression in policies[table]:
                row = connection.execute("""SELECT polcmd,polpermissive,polroles=ARRAY[%s::regrole::oid],pg_catalog.pg_get_expr(polqual,polrelid),polwithcheck IS NULL
                    FROM pg_catalog.pg_policy WHERE polrelid=pg_catalog.to_regclass(%s) AND polname=%s""",(role,'public.'+table,name)).fetchone()
                if row is None: return False
                actual=values(row)
                # pg_get_expr는 public. 한정을 정규화한다.
                actual_expression=actual[3].replace('public.notifications','notifications')
                expected_expression=expression.replace('public.notifications','notifications')
                if actual[:3]!=(command,True,True) or not actual[4] or _normalize_expression(actual_expression)!=_normalize_expression(expected_expression): return False
        for signature, (digest, definer) in _FUNCTIONS.items():
            row = connection.execute("""SELECT md5(p.prosrc),p.prosecdef,p.proowner='daon_notebook_delete'::regrole,
                p.proconfig=ARRAY['search_path=pg_catalog, pg_temp'],
                NOT EXISTS(SELECT 1 FROM pg_catalog.aclexplode(coalesce(p.proacl,pg_catalog.acldefault('f',p.proowner))) a
                  WHERE a.grantee NOT IN ('daon_app'::regrole,'daon_notebook_delete'::regrole) OR a.is_grantable),
                pg_catalog.has_function_privilege('daon_app',p.oid,'EXECUTE')
                FROM pg_catalog.pg_proc p WHERE p.oid=pg_catalog.to_regprocedure(%s)""",('public.'+signature,)).fetchone()
            if row is None or values(row)!=(digest,definer,True,True,True,definer and signature not in _PRIVATE_FUNCTIONS): return False
        expected_triggers = {
            ('notification_details','notification_details_guard','guard_notification_storage',31),
            ('notification_details','notification_details_created','guard_notification_storage',5),
            ('notification_source_outbox','notification_source_guard','guard_notification_storage',31),
            ('notification_audit_outbox','notification_audit_guard','guard_notification_storage',31),
            ('notifications','notification_parent_read_guard','guard_notification_parent',27),
        }
        rows = connection.execute("""SELECT c.relname,t.tgname,p.proname,t.tgtype FROM pg_catalog.pg_trigger t
            JOIN pg_catalog.pg_class c ON c.oid=t.tgrelid JOIN pg_catalog.pg_proc p ON p.oid=t.tgfoid
            WHERE t.tgname=ANY(%s) AND t.tgrelid=ANY(%s::regclass[])
              AND c.relnamespace='public'::regnamespace
              AND t.tgfoid=pg_catalog.to_regprocedure('public.'||p.proname||'()')
              AND p.pronamespace='public'::regnamespace AND p.pronargs=0
              AND p.prorettype='pg_catalog.trigger'::regtype
              AND t.tgqual IS NULL AND t.tgattr=''::int2vector
              AND NOT t.tgisinternal AND t.tgenabled IN ('O','A')""",
            ([r[1] for r in expected_triggers],sorted({'public.'+r[0] for r in expected_triggers}))).fetchall()
        return {values(r) for r in rows}==expected_triggers
    except (Error,TypeError,ValueError,KeyError):
        return False
