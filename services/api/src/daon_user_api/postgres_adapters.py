"""PostgreSQL compatibility adapters for the local domain repositories."""
from __future__ import annotations

import re
import sqlite3
import json
from contextlib import contextmanager
from threading import RLock
from typing import Any, Iterable, Iterator

import psycopg
from psycopg.rows import dict_row

from .authorization import AuthorizationError, SqliteAuthorizationRepository
from .organization_membership import OrganizationWorkflowError, SqliteOrganizationRepository, _parse

_ADMIN_ROLE_AUDIT_LOCK_TIMEOUT_SECONDS = 2.0

# 실행 SQL 본문 지문은 0068 후보와 함께 검토·고정한다. DB 자체의 준비완료 주장을 신뢰하지 않는다.
_ORGANIZATION_FUNCTION_PROTECTION = {
    "guard_identity_org_workspace": ("e88db93edd545a4a029c184a4efb223d", "plpgsql", "v", False, False),
    "valid_identity_org_operation": ("13b4f1087c98a6a9d3c18cba33016ee1", "plpgsql", "i", False, True),
    "guard_identity_org_operation": ("4649aba7ea3d2a1f6639ebcdd1f40de6", "plpgsql", "v", False, False),
    "pending_identity_org_audits": ("3b3572462c62e37b38fb9219ba0abb0b", "sql", "s", True, True),
    "ack_identity_org_audit": ("de54bfc5a2ed3bb4ed3716ee17a6b386", "plpgsql", "v", True, True),
}

# PostgreSQL의 CHECK 역변환 결과를 고정한다. 공백만 무시하며 논리식·상수는 바꾸지 않는다.
_ORGANIZATION_CHECK_PROTECTION = (
    "CHECK ((actor_id ~ '^[A-Za-z0-9][A-Za-z0-9._:-]{0,159}$'::text))",
    "CHECK ((operation = ANY (ARRAY['organization.join.create'::text, 'organization.join.decide'::text, 'organization.member.role'::text, 'organization.member.state'::text, 'organization.invitation.create'::text, 'organization.invitation.revoke'::text])))",
    "CHECK (((char_length(idempotency_key) >= 16) AND (char_length(idempotency_key) <= 128)))",
    "CHECK ((tenant_id ~ '^[A-Za-z0-9][A-Za-z0-9._:-]{0,159}$'::text))",
    "CHECK ((target_id ~ '^[A-Za-z0-9][A-Za-z0-9._:-]{0,159}$'::text))",
    "CHECK ((request_fingerprint ~ '^[0-9a-f]{64}$'::text))",
    "CHECK ((event_id ~ '^[A-Za-z0-9][A-Za-z0-9._:-]{0,159}$'::text))",
    "CHECK (valid_identity_org_operation(operation, response_json, audit_json))",
    """CHECK (((((response_json)::jsonb ->> 'tenant_id'::text) = tenant_id) AND
      (CASE WHEN (operation = ANY (ARRAY['organization.join.create'::text, 'organization.join.decide'::text])) THEN ((response_json)::jsonb ->> 'request_id'::text)
      WHEN (operation = ANY (ARRAY['organization.invitation.create'::text, 'organization.invitation.revoke'::text])) THEN ((response_json)::jsonb ->> 'invitation_id'::text)
      ELSE ((response_json)::jsonb ->> 'user_id'::text) END = target_id)))""",
)


def organization_management_storage_is_protected(connection) -> bool:
    """읽기 전용 catalog 검사: owner/RLS/ACL/trigger/index/함수의 누락·변조는 준비 실패다."""
    checks = connection.execute("""
      SELECT ARRAY[
        EXISTS(SELECT 1 FROM pg_catalog.pg_roles WHERE rolname='daon_organization_owner'
          AND NOT rolcanlogin AND NOT rolinherit AND NOT rolsuper AND NOT rolbypassrls
          AND NOT rolcreaterole AND NOT rolcreatedb AND NOT rolreplication)
          AND NOT EXISTS(SELECT 1 FROM pg_catalog.pg_auth_members WHERE roleid=(SELECT oid FROM pg_catalog.pg_roles WHERE rolname='daon_organization_owner')),
        EXISTS(SELECT 1 FROM pg_catalog.pg_class WHERE oid=to_regclass('public.identity_org_operation_outbox')
          AND relkind='r' AND pg_get_userbyid(relowner)='daon_organization_owner' AND relrowsecurity AND relforcerowsecurity)
          AND (SELECT array_agg(attname::text ORDER BY attnum)=ARRAY['actor_id','operation','idempotency_key','tenant_id','target_id','request_fingerprint','response_json','event_id','audit_json','trace_id','policy_version','created_at','delivered_at']
            AND bool_and(atttypid=CASE WHEN attname IN ('created_at','delivered_at') THEN 'timestamptz'::regtype ELSE 'text'::regtype END
              AND attnotnull=(attname<>'delivered_at') AND NOT atthasdef AND attidentity='' AND attgenerated='')
            FROM pg_catalog.pg_attribute WHERE attrelid=to_regclass('public.identity_org_operation_outbox') AND attnum>0 AND NOT attisdropped),
        has_table_privilege('daon_app',to_regclass('public.identity_org_operation_outbox'),'SELECT')
          AND has_table_privilege('daon_app',to_regclass('public.identity_org_operation_outbox'),'INSERT')
          AND NOT has_table_privilege('daon_app',to_regclass('public.identity_org_operation_outbox'),'UPDATE,DELETE,TRUNCATE,REFERENCES,TRIGGER')
          AND NOT EXISTS(SELECT 1 FROM pg_catalog.pg_class c,LATERAL aclexplode(c.relacl) a
            WHERE c.oid=to_regclass('public.identity_org_operation_outbox')
              AND (a.grantee NOT IN(c.relowner,(SELECT oid FROM pg_catalog.pg_roles WHERE rolname='daon_app'))
                OR a.grantee<>c.relowner AND (a.privilege_type NOT IN ('SELECT','INSERT') OR a.is_grantable)))
          AND NOT EXISTS(SELECT 1 FROM pg_catalog.pg_attribute WHERE attrelid=to_regclass('public.identity_org_operation_outbox') AND attacl IS NOT NULL),
        (SELECT count(*)=2 AND bool_and(polpermissive AND polcmd='*' AND
          CASE polname WHEN 'organization_operation_scope' THEN polroles=ARRAY[(SELECT oid FROM pg_catalog.pg_roles WHERE rolname='daon_app')]
            AND pg_get_expr(polqual,polrelid)='((actor_id = current_setting(''daon.organization_actor''::text, true)) AND (tenant_id = current_setting(''daon.organization_tenant''::text, true)))'
            AND pg_get_expr(polwithcheck,polrelid)=pg_get_expr(polqual,polrelid)
          WHEN 'organization_operation_dispatch' THEN polroles=ARRAY[(SELECT oid FROM pg_catalog.pg_roles WHERE rolname='daon_organization_owner')]
            AND pg_get_expr(polqual,polrelid)='true' AND pg_get_expr(polwithcheck,polrelid)='true'
          ELSE false END) FROM pg_catalog.pg_policy WHERE polrelid=to_regclass('public.identity_org_operation_outbox')),
        (SELECT count(*)=3 AND bool_and(tgenabled='O' AND NOT tgisinternal AND
          CASE tgname WHEN 'identity_org_invitation_workspace_guard' THEN tgrelid=to_regclass('public.identity_org_invitation_codes') AND tgtype=23 AND tgfoid=to_regprocedure('public.guard_identity_org_workspace()')
          WHEN 'identity_org_membership_workspace_guard' THEN tgrelid=to_regclass('public.identity_org_tenant_memberships') AND tgtype=23 AND tgfoid=to_regprocedure('public.guard_identity_org_workspace()')
          WHEN 'identity_org_operation_guard' THEN tgrelid=to_regclass('public.identity_org_operation_outbox') AND tgtype=31 AND tgfoid=to_regprocedure('public.guard_identity_org_operation()') ELSE false END)
          FROM pg_catalog.pg_trigger WHERE tgname IN ('identity_org_invitation_workspace_guard','identity_org_membership_workspace_guard','identity_org_operation_guard')),
        (SELECT count(*)=2 AND bool_and(atttypid='text'::regtype AND NOT attnotnull AND NOT atthasdef AND attidentity='' AND attgenerated='')
          FROM pg_catalog.pg_attribute WHERE (attrelid=to_regclass('public.identity_org_invitation_codes') AND attname='workspace_id'
            OR attrelid=to_regclass('public.identity_org_tenant_memberships') AND attname='join_workspace_id') AND NOT attisdropped),
        EXISTS(SELECT 1 FROM pg_catalog.pg_index i JOIN pg_catalog.pg_class c ON c.oid=i.indexrelid
          WHERE i.indexrelid=to_regclass('public.identity_org_join_pending_unique') AND i.indrelid=to_regclass('public.identity_org_join_requests')
            AND i.indisunique AND i.indisvalid AND i.indisready AND i.indnkeyatts=2 AND i.indnatts=2 AND i.indexprs IS NULL
            AND c.relam=(SELECT oid FROM pg_catalog.pg_am WHERE amname='btree')
            AND pg_get_indexdef(i.indexrelid,1,true)='tenant_id' AND pg_get_indexdef(i.indexrelid,2,true)='user_id'
            AND pg_get_expr(i.indpred,i.indrelid)='(state = ''pending''::text)'),
        (SELECT count(*)=11 AND bool_and(convalidated) AND count(*) FILTER(WHERE contype='c')=9
          AND count(*) FILTER(WHERE contype='p' AND pg_get_constraintdef(oid)='PRIMARY KEY (actor_id, operation, idempotency_key)')=1
          AND count(*) FILTER(WHERE contype='u' AND pg_get_constraintdef(oid)='UNIQUE (event_id)')=1
          FROM pg_catalog.pg_constraint WHERE conrelid=to_regclass('public.identity_org_operation_outbox')),
        (SELECT count(*)=5 AND bool_and(NOT EXISTS(SELECT 1 FROM aclexplode(coalesce(proacl,acldefault('f',proowner))) a
            WHERE a.grantee NOT IN(proowner,(SELECT oid FROM pg_catalog.pg_roles WHERE rolname='daon_app'))
              OR a.grantee<>proowner AND a.is_grantable) AND
          CASE proname WHEN 'guard_identity_org_workspace' THEN pronargs=0 AND prorettype='trigger'::regtype
          WHEN 'guard_identity_org_operation' THEN pronargs=0 AND prorettype='trigger'::regtype
          WHEN 'valid_identity_org_operation' THEN oid=to_regprocedure('public.valid_identity_org_operation(text,text,text)') AND prorettype='boolean'::regtype
          WHEN 'ack_identity_org_audit' THEN oid=to_regprocedure('public.ack_identity_org_audit(text)') AND prorettype='boolean'::regtype
          WHEN 'pending_identity_org_audits' THEN oid=to_regprocedure('public.pending_identity_org_audits(integer)') AND prorettype='record'::regtype AND proretset
            AND proargnames=ARRAY['requested_limit','event_id','actor_id','tenant_id','operation','target_id','audit_json','trace_id','policy_version','created_at']
            AND proallargtypes=ARRAY['integer'::regtype::oid,'text'::regtype::oid,'text'::regtype::oid,'text'::regtype::oid,'text'::regtype::oid,'text'::regtype::oid,'text'::regtype::oid,'text'::regtype::oid,'text'::regtype::oid,'timestamptz'::regtype::oid]
          ELSE false END) FROM pg_catalog.pg_proc WHERE pronamespace='public'::regnamespace
            AND proname IN ('guard_identity_org_workspace','valid_identity_org_operation','guard_identity_org_operation','pending_identity_org_audits','ack_identity_org_audit'))
      ]
    """).fetchone()
    if checks is None or checks[0] != [True] * 9:
        return False
    functions = connection.execute("""
      SELECT p.proname,md5(p.prosrc),l.lanname,p.provolatile,p.prosecdef,pg_get_userbyid(p.proowner),p.proconfig,
        EXISTS(SELECT 1 FROM aclexplode(coalesce(p.proacl,acldefault('f',p.proowner))) a WHERE a.grantee=0 AND a.privilege_type='EXECUTE'),
        has_function_privilege('daon_app',p.oid,'EXECUTE')
      FROM pg_catalog.pg_proc p JOIN pg_catalog.pg_language l ON l.oid=p.prolang
      WHERE p.pronamespace='public'::regnamespace AND p.proname IN
        ('guard_identity_org_workspace','valid_identity_org_operation','guard_identity_org_operation','pending_identity_org_audits','ack_identity_org_audit')
    """).fetchall()
    if len(functions) != len(_ORGANIZATION_FUNCTION_PROTECTION):
        return False
    for name, digest, language, volatility, definer, owner, config, public_execute, app_execute in functions:
        if (digest, language, volatility, definer, app_execute) != _ORGANIZATION_FUNCTION_PROTECTION.get(name):
            return False
        if owner != "daon_organization_owner" or config != ["search_path=pg_catalog, pg_temp"] or public_execute:
            return False
    constraints = connection.execute("""
      SELECT pg_get_constraintdef(oid) FROM pg_catalog.pg_constraint
      WHERE conrelid=to_regclass('public.identity_org_operation_outbox') AND contype='c'
    """).fetchall()
    def normalized(value):
        value = value.replace("public.valid_identity_org_operation(", "valid_identity_org_operation(")
        return "".join(re.findall(r"'(?:''|[^'])*'|[^\s]", value))
    return (len(constraints) == len(_ORGANIZATION_CHECK_PROTECTION)
            and {normalized(row[0]) for row in constraints} == {normalized(value) for value in _ORGANIZATION_CHECK_PROTECTION})


class _RowProxy(dict[str, Any]):
    """Allow SQLite-style positional and PostgreSQL mapping row access."""

    def __getitem__(self, key: str | int) -> Any:
        if isinstance(key, int):
            return tuple(self.values())[key]
        return super().__getitem__(key)


def _compat_row(row: Any) -> Any:
    return _RowProxy(row) if isinstance(row, dict) else row


class _CursorProxy:
    def __init__(self, cursor: Any) -> None:
        self._cursor = cursor

    def fetchone(self) -> Any:
        return _compat_row(self._cursor.fetchone())

    def fetchall(self) -> list[Any]:
        return [_compat_row(row) for row in self._cursor.fetchall()]

    def __iter__(self):
        return (_compat_row(row) for row in self._cursor)

    @property
    def rowcount(self) -> int:
        return self._cursor.rowcount


class PostgresCompatConnection:
    def __init__(
        self, dsn: str, prefixes: Iterable[str], *,
        connect_timeout: int | None = None, options: str | None = None,
    ) -> None:
        connection_options: dict[str, Any] = {}
        if connect_timeout is not None:
            connection_options["connect_timeout"] = connect_timeout
        if options is not None:
            connection_options["options"] = options
        self._conn = psycopg.connect(dsn, row_factory=dict_row, **connection_options)
        self._prefixes = tuple(prefixes)

    def _sql(self, statement: str) -> str:
        ignored = bool(re.search(r"\bINSERT\s+OR\s+IGNORE\s+INTO\b", statement, re.I))
        statement = re.sub(r"\bINSERT\s+OR\s+IGNORE\s+INTO\b", "INSERT INTO", statement, flags=re.I)
        statement = re.sub(r"BEGIN\s+IMMEDIATE", "BEGIN", statement, flags=re.I)
        for prefix in self._prefixes:
            if prefix == "organization_":
                continue
            statement = re.sub(rf"\b{re.escape(prefix)}([A-Za-z0-9_]+)\b", rf"identity_{prefix}\1", statement)
        if "organization_" in self._prefixes:
            for table in ("organization_creation_requests", "invitation_codes", "organization_join_requests", "tenant_memberships", "tenant_membership_role_history", "organization_idempotency", "organization_operation_outbox"):
                physical_table = "identity_org_role_history" if table == "tenant_membership_role_history" else f"identity_org_{table.removeprefix('organization_')}"
                statement = re.sub(rf"\b{table}\b", physical_table, statement)
        statement = statement.replace("?", "%s")
        if ignored and "ON CONFLICT" not in statement.upper():
            statement = statement.rstrip().rstrip(";") + " ON CONFLICT DO NOTHING"
        return statement

    def execute(self, statement: str, params: tuple[Any, ...] = ()) -> Any:
        if statement.lstrip().upper().startswith("PRAGMA"):
            value = 1
            class _Pragma:
                def fetchone(self) -> tuple[int]: return (value,)
                def fetchall(self) -> list[tuple[int]]: return [(value,)]
            return _Pragma()
        try:
            return _CursorProxy(self._conn.execute(self._sql(statement), params))
        except psycopg.errors.UniqueViolation as error:
            raise sqlite3.IntegrityError(str(error)) from error
        except psycopg.errors.ForeignKeyViolation as error:
            raise sqlite3.IntegrityError(str(error)) from error
        except psycopg.Error as error:
            raise sqlite3.OperationalError(str(error)) from error

    @property
    def in_transaction(self) -> bool:
        return self._conn.info.transaction_status != psycopg.pq.TransactionStatus.IDLE

    def close(self) -> None:
        self._conn.close()


class PostgresAuthorizationRepository(SqliteAuthorizationRepository):
    def __init__(self, dsn: str) -> None:
        self._dsn, self._lock, self._closed = dsn, RLock(), False
        connection = self._connect()
        try: connection.execute("SELECT 1")
        finally: connection.close()

    def _connect(self) -> PostgresCompatConnection:
        return PostgresCompatConnection(self._dsn, ("auth_",))

    @contextmanager
    def _admin_audit_transaction(self) -> Iterator[PostgresCompatConnection]:
        """Bound only C9 outbox projection I/O, not ordinary authorization calls."""
        if not self._lock.acquire(timeout=_ADMIN_ROLE_AUDIT_LOCK_TIMEOUT_SECONDS):
            raise AuthorizationError("PERSISTENCE_UNAVAILABLE", 503)
        try:
            self._ensure_open()
            connection: PostgresCompatConnection | None = None
            try:
                connection = PostgresCompatConnection(
                    self._dsn, ("auth_",), connect_timeout=2,
                    options="-c statement_timeout=5000 -c lock_timeout=2000",
                )
                connection.execute("BEGIN")
                yield connection
                connection.execute("COMMIT")
            except sqlite3.IntegrityError as error:
                if connection is not None:
                    connection.execute("ROLLBACK")
                raise AuthorizationError("PERSISTENCE_CONFLICT", 409) from error
            except (sqlite3.Error, psycopg.Error) as error:
                if connection is not None:
                    connection.execute("ROLLBACK")
                raise AuthorizationError("PERSISTENCE_UNAVAILABLE", 503) from error
            except Exception:
                if connection is not None:
                    connection.execute("ROLLBACK")
                raise
            finally:
                if connection is not None:
                    connection.close()
        finally:
            self._lock.release()

    def lock_admin_workspace(self, connection: PostgresCompatConnection, tenant_id: str, workspace_id: str):
        return connection.execute(
            "SELECT * FROM auth_workspaces WHERE tenant_id=? AND workspace_id=? FOR UPDATE",
            (tenant_id, workspace_id),
        ).fetchone()


class PostgresOrganizationRepository(SqliteOrganizationRepository):
    def __init__(self, dsn: str, *, audit_sink: Any = None, audit_store: Any = None,
                 notification_inbox_enabled: bool = False) -> None:
        if type(notification_inbox_enabled) is not bool:
            raise ValueError("NOTIFICATION_INBOX_FLAG_INVALID")
        self._notification_inbox_enabled = notification_inbox_enabled
        from .organization_access import OrganizationAccessBridge
        self._dsn, self._lock, self._closed, self._audit_sink = dsn, RLock(), False, audit_sink
        self._audit_store = audit_store
        self._access_bridge = OrganizationAccessBridge(postgres=True)
        connection = self._connect()
        try: connection.execute("SELECT 1")
        finally: connection.close()

    def _connect(self) -> PostgresCompatConnection:
        return PostgresCompatConnection(self._dsn, ("organization_",))

    def _lock_management_key(self, connection, operation, actor_id, key) -> None:
        connection.execute("SELECT pg_advisory_xact_lock(hashtextextended(?,0))",
                           (json.dumps(["organization-operation", actor_id, operation, key]),))

    def _set_management_scope(self, connection, actor_id, tenant_id) -> None:
        connection.execute("SELECT set_config('daon.organization_actor',?,true),set_config('daon.organization_tenant',?,true)", (actor_id, tenant_id))

    def _management_now(self, connection):
        return _parse(connection.execute("SELECT clock_timestamp()").fetchone()[0])

    def _lock_management_target(self, connection, operation, actor_id, tenant_id, target_id, payload, workspace_id, original) -> None:
        # 작업키 다음 Workspace → 조직/사용자 키 → 신청 → 초대 → 회원 순서를 고정한다.
        if workspace_id is not None:
            workspace = connection.execute(
                "SELECT workspace_id FROM public.identity_auth_workspaces WHERE tenant_id=? AND workspace_id=? AND workspace_kind='organization' FOR UPDATE",
                (tenant_id, workspace_id),
            ).fetchone()
            if workspace is None:
                raise OrganizationWorkflowError("WORKSPACE_NOT_FOUND", 404)
        user_id = (original["user_id"] if original is not None and "user_id" in original.keys()
                   else actor_id if operation == "organization.join.create" else None)
        if user_id is not None:
            connection.execute("SELECT pg_advisory_xact_lock(hashtextextended(?,0))",
                               (json.dumps(["organization-join", tenant_id, user_id]),))
        if operation == "organization.join.decide":
            connection.execute("SELECT request_id FROM organization_join_requests WHERE request_id=? AND tenant_id=? FOR UPDATE", (target_id, tenant_id)).fetchone()
        invitation_id = (original["invitation_id"] if original is not None and "invitation_id" in original.keys() else None)
        if invitation_id is not None:
            connection.execute("SELECT invitation_id FROM invitation_codes WHERE invitation_id=? AND tenant_id=? FOR UPDATE", (invitation_id, tenant_id)).fetchone()
        if user_id is not None:
            connection.execute("SELECT user_id FROM tenant_memberships WHERE tenant_id=? AND user_id=? FOR UPDATE", (tenant_id, user_id)).fetchone()

    @contextmanager
    def _management_audit_transaction(self):
        # 종료 가능한 기존 감사 루프에서만 쓰는 bounded I/O이다.
        if not self._lock.acquire(timeout=2):
            raise OrganizationWorkflowError("PERSISTENCE_UNAVAILABLE", 503)
        connection = None
        try:
            from psycopg.conninfo import conninfo_to_dict
            # DSN의 role 등 기존 옵션을 보존하고 마지막 값으로 timeout 상한만 고정한다.
            existing_options = conninfo_to_dict(self._dsn).get("options", "")
            bounded_options = (existing_options + " -c statement_timeout=5000 -c lock_timeout=2000").strip()
            connection = PostgresCompatConnection(self._dsn, ("organization_",), connect_timeout=2,
                options=bounded_options)
            connection.execute("BEGIN")
            yield connection
            connection.execute("COMMIT")
        except (sqlite3.Error, psycopg.Error) as error:
            raise OrganizationWorkflowError("PERSISTENCE_UNAVAILABLE", 503) from error
        finally:
            if connection is not None:
                # 미완료 transaction은 close로 롤백되어 pending이 보존된다.
                connection.close()
            self._lock.release()

    def _pending_management_audits(self, limit):
        with self._management_audit_transaction() as connection:
            return connection.execute("SELECT * FROM public.pending_identity_org_audits(?)", (limit,)).fetchall()

    def _ack_management_audit(self, event_id):
        with self._management_audit_transaction() as connection:
            return bool(connection.execute("SELECT public.ack_identity_org_audit(?)", (event_id,)).fetchone()[0])
