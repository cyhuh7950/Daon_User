"""M2-1 고정 서버 publisher의 보호 SQL 소비. DDL/role/권한 자동 생성·fallback0."""
from __future__ import annotations

import os
import threading
from contextlib import contextmanager
from dataclasses import fields

from psycopg import Connection,Error,OperationalError
from psycopg.rows import dict_row
from psycopg.conninfo import conninfo_to_dict
from psycopg_pool import ConnectionPool,PoolTimeout

from .operations_telemetry import (KINDS,WorkerSample,ReceivedWorkerSample,TelemetryUnavailable,
    validate_worker_sample,project_worker,_id,_utc)


_FIELDS=tuple(field.name for field in fields(WorkerSample))


class _TelemetryConnection(Connection):
    @classmethod
    def connect(cls,*args,**kwargs):
        # psycopg_pool의 자체 background warning보다 먼저 접속 오류 원문을 폐기한다.
        try:return super().connect(*args,**kwargs)
        except (Error,OSError,ValueError,TypeError):raise OperationalError("OPERATIONS_TELEMETRY_UNAVAILABLE") from None


def expected_worker_ids():
    return {kind:os.environ.get(name,kind+"-worker-1") for kind,name in
        (("document","DAON_DOCUMENT_WORKER_ID"),("studio","DAON_STUDIO_WORKER_ID"),("object","DAON_OBJECT_WORKER_ID"))}


class PostgresOperationsTelemetryRepository:
    def __init__(self,database_dsn,*,expected_workers=None):
        try:
            if type(database_dsn) is not str or not database_dsn.strip():raise ValueError()
            conninfo_to_dict(database_dsn)
        except (Error,ValueError,TypeError):raise TelemetryUnavailable() from None
        expected=dict(expected_worker_ids() if expected_workers is None else expected_workers)
        if set(expected)!=set(KINDS) or len(set(expected.values()))!=3:raise ValueError("WORKER_OBSERVATION_INVALID")
        for identity in expected.values():_id(identity)
        self.expected=expected;self._lock=threading.Lock();self._closed=False
        self._pool=ConnectionPool(conninfo=database_dsn,min_size=0,max_size=1,open=False,timeout=1,reconnect_timeout=1,
            connection_class=_TelemetryConnection,
            kwargs={"autocommit":False,"row_factory":dict_row,"connect_timeout":1})

    @contextmanager
    def _transaction(self):
        try:
            with self._lock:
                if self._closed:raise TelemetryUnavailable()
                if self._pool.closed:self._pool.open(wait=False)
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

    def close(self):
        with self._lock:
            if self._closed:return
            self._closed=True
        self._pool.close()
