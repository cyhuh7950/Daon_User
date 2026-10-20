"""M2-1 고정 서버 publisher의 보호 SQL 소비. DDL/role/권한 자동 생성·fallback0."""
from __future__ import annotations

import os
import hashlib
import json
import re
import threading
from contextlib import contextmanager
from dataclasses import fields
from datetime import datetime,timezone
from decimal import Decimal
from types import SimpleNamespace

from psycopg import Connection,Error,OperationalError
from psycopg.rows import dict_row
from psycopg.conninfo import conninfo_to_dict
from psycopg_pool import ConnectionPool,PoolTimeout

from .operations_telemetry import (KINDS,WorkerSample,ReceivedWorkerSample,TelemetryUnavailable,
    validate_worker_sample,project_worker,CostReceipt,_id,_utc)


_FIELDS=tuple(field.name for field in fields(WorkerSample))


class PostgresHostCapacityRepository:
    """기존 telemetry 연결을 재사용한다. 전역 host 관측에 가짜 tenant/Node를 붙이지 않는다."""
    def __init__(self,telemetry):
        self._telemetry=telemetry

    @classmethod
    def using_cloud_store(cls,store):
        @contextmanager
        def transaction():
            store._ensure_pool_open()
            with store._pool.connection(timeout=2) as connection,connection.transaction():
                connection.execute('SET LOCAL ROLE daon_app')
                connection.execute("SET LOCAL statement_timeout='1500ms'")
                connection.execute("SET LOCAL lock_timeout='500ms'")
                connection.execute("SET LOCAL TIME ZONE 'UTC'")
                yield connection
        return cls(PostgresOperationsTelemetryRepository.using_transaction(transaction))

    @staticmethod
    def _stored(row):
        from .operations_host_capacity import HostCapacitySample,ReceivedHostCapacitySample
        names=tuple(f.name for f in fields(HostCapacitySample))
        if type(row) is not dict or set(row)!=set(names)|{'received_at'}:
            raise TelemetryUnavailable()
        try:
            values={k:row[k] for k in names}
            if type(values['cpu_counters']) is not list:raise ValueError()
            values['cpu_counters']=tuple(values['cpu_counters'])
            return ReceivedHostCapacitySample(HostCapacitySample(**values),row['received_at'])
        except (ValueError,TypeError,KeyError):raise TelemetryUnavailable() from None

    def record_host_sample(self,sample):
        from .operations_host_capacity import HostCapacitySample
        if type(sample) is not HostCapacitySample:raise TelemetryUnavailable()
        try:sample.__post_init__()
        except (ValueError,TypeError):raise TelemetryUnavailable() from None
        values=(sample.host_ref,sample.boot_id,sample.sequence,sample.observed_at,
            sample.cpu_logical_millicores,sample.cpu_usage_basis_points,sample.memory_total_bytes,
            sample.memory_available_bytes,list(sample.cpu_counters))
        try:
            with self._telemetry._transaction() as connection:
                row=connection.execute('SELECT * FROM public.record_operations_host_capacity('
                    +','.join(['%s']*9)+')',values).fetchone()
                stored=self._stored(row)
                if stored.sample!=sample:raise TelemetryUnavailable()
            return stored
        except (Error,OSError):raise TelemetryUnavailable() from None

    def read_host_sample_for_collector(self):
        # API 수집자/이미 시스템관리 인가를 마친 내부 소비자만 호출한다. HTTP 직접 진입점이 아니다.
        return self._read(None)

    def read_host_sample(self,context):
        if context is None:raise TelemetryUnavailable()
        return self._read(context)

    def _read(self,context):
        try:
            with self._telemetry._transaction() as connection:
                if context is not None:
                    for key in ('tenant_id','workspace_id','actor_id'):
                        value=getattr(context,key,None);_id(value)
                        connection.execute('SELECT set_config(%s,%s,true)',('app.'+key,value))
                    self._telemetry._authorize(connection,context)
                rows=connection.execute('SELECT * FROM public.read_operations_host_capacity()').fetchall()
                if len(rows)>1:raise TelemetryUnavailable()
                stored=self._stored(rows[0]) if rows else None
                if context is not None:self._telemetry._authorize(connection,context)
            return stored
        except (Error,OSError):raise TelemetryUnavailable() from None


def _canon_id(value):
    if type(value) is not str or re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_.:-]{0,255}",value) is None:
        raise TelemetryUnavailable()
    return value


def record_cost_in_transaction(connection,context,run_id,receipt,*,model_attempt_id=None):
    """호출자의 실제 Run transaction을 그대로 사용. 별도 commit/재시도/가짜 attempt0."""
    if type(receipt) is not CostReceipt:raise TelemetryUnavailable()
    receipt.__post_init__()
    for value in (context.tenant_id,context.workspace_id,context.actor_id,run_id):_canon_id(value)
    if model_attempt_id is not None:_canon_id(model_attempt_id)
    values=(context.tenant_id,context.workspace_id,run_id,context.actor_id,receipt.receipt_id,receipt.provider_connection_id,
        model_attempt_id,receipt.basis,receipt.amount,receipt.currency,receipt.usage_digest,receipt.price_version,receipt.state,receipt.observed_at)
    canonical=[format(v,"f") if type(v) is Decimal else v.isoformat() if isinstance(v,datetime) else v for v in values]
    digest=hashlib.sha256(json.dumps(["operations-cost-receipt-v1",*canonical],separators=(",",":"),allow_nan=False).encode()).hexdigest()
    try:
        writer='record_operations_price_card_receipt' if receipt.basis=='price_card' else 'record_operations_cost_receipt'
        row=connection.execute('SELECT public.'+writer+'('+','.join(['%s']*15)+')',(*values,digest)).fetchone()
        actual=tuple(row.values()) if isinstance(row,dict) else tuple(row) if row is not None else ()
        if actual!=(digest,):raise TelemetryUnavailable()
    except (Error,OSError):raise TelemetryUnavailable() from None
    return digest


def record_deployment_in_operator_transaction(connection,receipt):
    """기존 운영자 연결만 소비. app repository/HTTP에는 쓰기 메서드가 없다."""
    from .operations_telemetry import DeploymentReceipt
    if type(receipt) is not DeploymentReceipt:raise TelemetryUnavailable()
    receipt.__post_init__()
    try:
        row=connection.execute('SELECT current_user,rolsuper FROM pg_catalog.pg_roles WHERE rolname=current_user').fetchone()
        role,superuser=tuple(row.values()) if isinstance(row,dict) else tuple(row) if row else (None,False)
        if role=='daon_app' or not (superuser is True or role=='daon_notebook_delete'):raise TelemetryUnavailable()
        values=tuple(getattr(receipt,key) for key in receipt.__dataclass_fields__)
        digest=receipt.digest()
        row=connection.execute('SELECT public.record_operations_deployment_receipt('+','.join(['%s']*9)+')',(*values,digest)).fetchone()
        if (tuple(row.values()) if isinstance(row,dict) else tuple(row) if row else ())!=(digest,):raise TelemetryUnavailable()
    except (Error,OSError):raise TelemetryUnavailable() from None
    return digest


class _TelemetryConnection(Connection):
    @classmethod
    def connect(cls,*args,**kwargs):
        # psycopg_pool의 자체 background warning보다 먼저 접속 오류 원문을 폐기한다.
        try:return super().connect(*args,**kwargs)
        except (Error,OSError,ValueError,TypeError):raise OperationalError("OPERATIONS_TELEMETRY_UNAVAILABLE") from None


def expected_worker_ids():
    return {kind:os.environ.get(name,kind+"-worker-1") for kind,name in
        (("document","DAON_DOCUMENT_WORKER_ID"),("studio","DAON_STUDIO_WORKER_ID"),("object","DAON_OBJECT_WORKER_ID"))}


class _BorrowedTelemetryConnection:
    """dict_row는 빌린 cursor 하나에만 적용. 기본 연결/트랜잭션은 호출자 소유."""
    def __init__(self,connection,cursor):self._connection,self._cursor=connection,cursor

    def execute(self,statement,params=None):return self._cursor.execute(statement,params)

    def __getattr__(self,name):return getattr(self._connection,name)


class PostgresOperationsTelemetryRepository:
    def __init__(self,database_dsn,*,expected_workers=None):
        try:
            if type(database_dsn) is not str or not database_dsn.strip():raise ValueError()
            conninfo_to_dict(database_dsn)
        except (Error,ValueError,TypeError):raise TelemetryUnavailable() from None
        expected=dict(expected_worker_ids() if expected_workers is None else expected_workers)
        if set(expected)!=set(KINDS) or len(set(expected.values()))!=3:raise ValueError("WORKER_OBSERVATION_INVALID")
        for identity in expected.values():_id(identity)
        self.expected=expected;self._lock=threading.Lock();self._closed=False;self._transaction_factory=None
        self._pool=ConnectionPool(conninfo=database_dsn,min_size=0,max_size=1,open=False,timeout=1,reconnect_timeout=1,
            connection_class=_TelemetryConnection,
            kwargs={"autocommit":False,"row_factory":dict_row,"connect_timeout":1})

    @classmethod
    def using_transaction(cls,factory,*,expected_workers=None):
        """기존 인가 transaction의 인자 없는 factory를 주입한다. DSN/새 pool/중첩 Tx0.

        factory가 scope/role/timeout과 commit/rollback을 소유한다. 이 어댑터는
        dict cursor만 한시 소유하며 close()도 API pool/connection에 전파하지 않는다.
        """
        if not callable(factory):raise ValueError('TELEMETRY_TRANSACTION_FACTORY_INVALID')
        expected=dict(expected_worker_ids() if expected_workers is None else expected_workers)
        if set(expected)!=set(KINDS) or len(set(expected.values()))!=3:raise ValueError('WORKER_OBSERVATION_INVALID')
        for identity in expected.values():_id(identity)
        repository=cls.__new__(cls)
        repository.expected=expected;repository._lock=threading.Lock();repository._closed=False
        repository._pool=None;repository._transaction_factory=factory
        return repository

    @contextmanager
    def _transaction(self):
        try:
            with self._lock:
                if self._closed:raise TelemetryUnavailable()
                factory=self._transaction_factory
                if factory is None and self._pool.closed:self._pool.open(wait=False)
            if factory is not None:
                with factory() as connection:
                    with connection.cursor(row_factory=dict_row) as cursor:
                        yield _BorrowedTelemetryConnection(connection,cursor)
                return
            with self._pool.connection(timeout=1) as connection,connection.transaction():
                connection.execute("SET LOCAL ROLE daon_app")
                connection.execute("SET LOCAL statement_timeout='1500ms'")
                connection.execute("SET LOCAL lock_timeout='500ms'")
                connection.execute("SET LOCAL TIME ZONE 'UTC'")
                yield connection
        except (Error,PoolTimeout,OSError):raise TelemetryUnavailable() from None

    def _stored(self,row):
        if row is None or set(row)!=set(_FIELDS)|{"received_at"}:raise TelemetryUnavailable()
        try:
            sample=WorkerSample(**{name:row[name] for name in _FIELDS})
            if self.expected.get(sample.worker_kind)!=sample.worker_id:raise ValueError()
            validate_worker_sample(sample,received_at=row["received_at"])
            return ReceivedWorkerSample(sample,row["received_at"])
        except (ValueError,TypeError,KeyError):raise TelemetryUnavailable() from None

    def record_worker_sample(self,sample):
        # producer가 received_at/commit을 만들지 않는다. protected function 결과+Tx exit만 소비한다.
        validate_worker_sample(sample,received_at=sample.observed_at)
        if self.expected.get(sample.worker_kind)!=sample.worker_id:raise TelemetryUnavailable()
        values=tuple(getattr(sample,name) for name in _FIELDS)
        with self._transaction() as connection:
            row=connection.execute("SELECT * FROM public.record_operations_worker_sample("+",".join(["%s"]*len(values))+")",values).fetchone()
            stored=self._stored(row)
            if stored.sample!=sample:raise TelemetryUnavailable()
        return stored

    @staticmethod
    def _authorize(connection,context):
        from .authorization import Action,AuthorizationError
        from .notification_events import _NotificationTupleReadConnection
        from .open_law_source_access import _current_auth_grant,_AuthReadConnection,OpenLawAccessError
        from .organization_access import OrganizationAccessBridge
        from .organization_membership import OrganizationWorkflowError
        try:
            for value in (context.tenant_id,context.workspace_id,context.actor_id):_id(value)
            connection=_NotificationTupleReadConnection(connection)
            _current_auth_grant(context,connection,Action.VIEW)
            auth=_AuthReadConnection(connection)
            workspace=auth.execute("SELECT * FROM auth_workspaces WHERE tenant_id=? AND workspace_id=?",
                (context.tenant_id,context.workspace_id)).fetchone()
            if workspace is None:raise OpenLawAccessError()
            if workspace["workspace_kind"]=="organization":
                OrganizationAccessBridge(postgres=True).assert_organization_active(connection=auth,
                    tenant_id=context.tenant_id,user_id=context.actor_id)
        except (AttributeError,ValueError,OpenLawAccessError,OrganizationWorkflowError):raise AuthorizationError("ACTION_DENIED",403) from None

    def read_worker_samples(self,context):
        with self._transaction() as connection:
            for key,value in (("tenant_id",context.tenant_id),("workspace_id",context.workspace_id),("actor_id",context.actor_id)):
                _id(value);connection.execute("SELECT set_config(%s,%s,true)",("app."+key,value))
            self._authorize(connection,context)
            rows=connection.execute("SELECT * FROM public.read_operations_worker_samples(%s)",([self.expected[k] for k in KINDS],)).fetchall()
            if len(rows)>6:raise TelemetryUnavailable()
            observed=[self._stored(row) for row in rows]
            now=connection.execute("SELECT clock_timestamp() AS checked_at").fetchone()
            if now is None:raise TelemetryUnavailable()
            now=_utc(now["checked_at"])
            self._authorize(connection,context)
        result=[]
        for kind in KINDS:
            items=[row for row in observed if row.sample.worker_kind==kind]
            items.sort(key=lambda row:(row.sample.process_started_at,row.received_at),reverse=True)
            if len(items)>2 or (len(items)==2 and (items[0].sample.boot_id==items[1].sample.boot_id
                    or items[0].sample.process_started_at==items[1].sample.process_started_at)):
                raise TelemetryUnavailable()
            result.append(project_worker(items[0] if items else None,now=now,worker_kind=kind))
        return tuple(result)

    @staticmethod
    def _cost_authorize(connection,context):
        from .authorization import Action,AuthorizationError
        from .open_law_source_access import _current_auth_grant,OpenLawAccessError
        from .notification_events import _NotificationTupleReadConnection
        PostgresOperationsTelemetryRepository._authorize(connection,context)
        scope=getattr(context,'scope','self')
        if scope not in {'self','workspace_visible'}:raise AuthorizationError('ACTION_DENIED',403)
        if scope=='workspace_visible':
            try:_current_auth_grant(context,_NotificationTupleReadConnection(connection),Action.POLICY_MANAGE)
            except OpenLawAccessError:raise AuthorizationError('ACTION_DENIED',403) from None
        return scope

    @staticmethod
    def _cost_runs(connection,context,run_ids,scope):
        from .authorization import AuthorizationError
        from .document_index_postgres import _source_access_allowed
        from .notification_source_access import _special_source_access_allowed
        from .notification_events import _NotificationTupleReadConnection
        reader=_NotificationTupleReadConnection(connection)
        rows=reader.execute("SELECT /* operations-cost:run */ DISTINCT r.record_id,r.created_by,n.notebook_id,"
            "rs.canonical_json->'source_version_ids' FROM public.runs r JOIN public.run_snapshots rs ON "
            "(rs.tenant_id,rs.workspace_id,rs.run_id)=(r.tenant_id,r.workspace_id,r.record_id) "
            "JOIN public.notebook_bindings b ON (b.tenant_id,b.workspace_id,b.record_id)="
            "(r.tenant_id,r.workspace_id,r.conversation_id) AND b.binding_kind='conversation_thread' "
            "JOIN public.notebooks n ON (n.tenant_id,n.workspace_id,n.notebook_id)=(b.tenant_id,b.workspace_id,b.notebook_id) "
            "WHERE r.tenant_id=%s AND r.workspace_id=%s AND r.record_id=ANY(%s) "
            "AND NOT EXISTS(SELECT 1 FROM public.notebook_deletion_requests d WHERE "
            "(d.tenant_id,d.workspace_id,d.notebook_id)=(n.tenant_id,n.workspace_id,n.notebook_id) "
            "AND d.state IN ('accepted','deleting','completed'))",
            (context.tenant_id,context.workspace_id,list(run_ids))).fetchall()
        found=set();witness=[]
        for run,actor,notebook,versions in rows:
            if run not in run_ids or scope=='self' and actor!=context.actor_id:
                raise AuthorizationError('ACTION_DENIED',403)
            if type(versions) is not list or any(type(v) is not str for v in versions) or len(versions)!=len(set(versions)):
                raise TelemetryUnavailable()
            for value in (run,actor,notebook,*versions):_canon_id(value)
            scoped=SimpleNamespace(tenant_id=context.tenant_id,workspace_id=context.workspace_id,actor_id=context.actor_id,
                notebook_id=notebook,trace_id=context.trace_id,policy_version=context.policy_version)
            for version in versions:
                bound=reader.execute("SELECT /* operations-cost:binding */ 1 FROM public.notebook_bindings b "
                    "JOIN public.source_versions sv ON (sv.tenant_id,sv.workspace_id,sv.record_id,sv.source_id)="
                    "(b.tenant_id,b.workspace_id,b.version_id,b.record_id) JOIN public.sources s ON "
                    "(s.tenant_id,s.workspace_id,s.record_id)=(sv.tenant_id,sv.workspace_id,sv.source_id) "
                    "WHERE b.tenant_id=%s AND b.workspace_id=%s AND b.notebook_id=%s AND b.version_id=%s AND b.binding_kind='source' "
                    "AND s.state NOT IN ('expired','disabled','deleting','deleted') "
                    "AND NOT EXISTS(SELECT 1 FROM public.notebook_source_unbindings u WHERE "
                    "(u.tenant_id,u.workspace_id,u.notebook_id,u.source_id,u.source_version_id)="
                    "(b.tenant_id,b.workspace_id,b.notebook_id,b.record_id,b.version_id)) "
                    "AND NOT EXISTS(SELECT 1 FROM public.deletion_requests d WHERE "
                    "(d.tenant_id,d.workspace_id,d.source_id)=(s.tenant_id,s.workspace_id,s.record_id) "
                    "AND (d.source_active=false OR d.state='purged'))",
                    (context.tenant_id,context.workspace_id,notebook,version)).fetchone()
                if bound is None or not _source_access_allowed(reader,scoped,version) or not _special_source_access_allowed(reader,scoped,version):
                    raise AuthorizationError('ACTION_DENIED',403)
            found.add(run);witness.append((run,actor,notebook,tuple(versions)))
        if found!=set(run_ids):raise AuthorizationError('ACTION_DENIED',403)
        return tuple(sorted(witness))

    def read_cost_receipts(self,context,run_ids):
        if type(run_ids) is not tuple or any(type(v) is not str for v in run_ids) or len(run_ids)!=len(set(run_ids)):
            raise TelemetryUnavailable()
        for value in run_ids:_canon_id(value)
        with self._transaction() as connection:
            for key,value in (('tenant_id',context.tenant_id),('workspace_id',context.workspace_id),('actor_id',context.actor_id)):
                _canon_id(value);connection.execute('SELECT set_config(%s,%s,true)',('app.'+key,value))
            scope=self._cost_authorize(connection,context)
            before=self._cost_runs(connection,context,run_ids,scope)
            rows=connection.execute('SELECT * FROM public.read_operations_cost_receipts(%s,%s)',(list(run_ids),scope)).fetchall()
            names={'run_id','actor_id','model_attempt_id','state','amount','currency','observed_at','received_at'}
            owners={run:actor for run,actor,_,_ in before}
            seen=set()
            for row in rows:
                if (type(row) is not dict or set(row)!=names or row['run_id'] not in owners
                    or row['actor_id']!=owners[row['run_id']] or row['state'] not in {'known','unknown','uncertain'}):raise TelemetryUnavailable()
                if row['model_attempt_id'] is not None:_canon_id(row['model_attempt_id'])
                key=(row['run_id'],row['model_attempt_id'])
                if key in seen:raise TelemetryUnavailable()
                seen.add(key)
                if row['state']=='known':
                    from .operations_telemetry import ReportedCost
                    try:ReportedCost(row['amount'],row['currency'],'read-cost')
                    except ValueError:raise TelemetryUnavailable() from None
                elif row['amount'] is not None or row['currency'] is not None:raise TelemetryUnavailable()
                _utc(row['observed_at']);_utc(row['received_at'])
            if self._cost_authorize(connection,context)!=scope or self._cost_runs(connection,context,run_ids,scope)!=before:
                from .authorization import AuthorizationError
                raise AuthorizationError('ACTION_DENIED',403)
        return tuple(dict(row) for row in rows)

    def record_cost_receipt(self,context,run_id,receipt,model_attempt_id=None):
        with self._transaction() as connection:
            for key,value in (('tenant_id',context.tenant_id),('workspace_id',context.workspace_id),('actor_id',context.actor_id)):
                _canon_id(value);connection.execute('SELECT set_config(%s,%s,true)',('app.'+key,value))
            self._cost_authorize(connection,context)
            before=self._cost_runs(connection,context,(run_id,),'self')
            digest=record_cost_in_transaction(connection,context,run_id,receipt,model_attempt_id=model_attempt_id)
            self._cost_authorize(connection,context)
            if self._cost_runs(connection,context,(run_id,),'self')!=before:raise TelemetryUnavailable()
        return digest

    def read_deployment_receipt(self):
        with self._transaction() as connection:
            rows=connection.execute('SELECT * FROM public.read_operations_deployment_receipt()').fetchall()
            if not rows:return None
            if len(rows)!=1:raise TelemetryUnavailable()
            row=rows[0]
            from .operations_telemetry import DeploymentReceipt
            try:receipt=DeploymentReceipt(**{key:value for key,value in row.items() if key not in {'received_at','receipt_digest'}})
            except (ValueError,TypeError):raise TelemetryUnavailable() from None
            if set(row)!={*receipt.__dataclass_fields__,'received_at','receipt_digest'}:raise TelemetryUnavailable()
            _utc(row['received_at'])
            if row['receipt_digest']!=receipt.digest():raise TelemetryUnavailable()
        return dict(row)

    def close(self):
        with self._lock:
            if self._closed:return
            self._closed=True
        if self._pool is not None:self._pool.close()
