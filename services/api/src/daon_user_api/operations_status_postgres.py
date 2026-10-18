"""PostgreSQL counts for the safe operations status projection."""

from __future__ import annotations

import math
import json
from datetime import datetime, timezone
from types import SimpleNamespace
from psycopg import Error

from .cloud_storage import CloudAccessContext, CloudDatabaseError, PostgresCloudStore
from .operations_status import (OperationsCounts, OperationsStatusContext, OperationsObservation,
    OBSERVATION_IDS, OBSERVATION_STATES, observation_scope, unknown_observations)
from .document_index_postgres import _source_access_allowed
from .notification_source_access import _special_source_access_allowed
from .notification import NotificationError


class OperationsStatusError(RuntimeError):
    def __init__(self, code: str = "OPERATIONS_STATUS_UNAVAILABLE") -> None:
        self.code = code
        super().__init__(code)


class PostgresOperationsStatusRepository:
    def __init__(self, cloud_store: PostgresCloudStore) -> None:
        self._cloud_store = cloud_store

    def read_counts(self, context: OperationsStatusContext) -> OperationsCounts:
        try:
            with self._cloud_store._transaction(CloudAccessContext(context.tenant_id, context.workspace_id, context.actor_id, "operations.read")) as connection:
                row = connection.execute(
                    "SELECT "
                    "(SELECT count(*) FROM sync_operations WHERE tenant_id=%s AND workspace_id=%s AND state IN ('preview','awaiting_approval','approved','transferring','conflict')),"
                    "(SELECT count(*) FROM document_processing_jobs WHERE tenant_id=%s AND workspace_id=%s AND state IN ('pending','leased','retry_wait')),"
                    "(SELECT count(*) FROM document_processing_jobs WHERE tenant_id=%s AND workspace_id=%s AND state='dead_letter')",
                    (context.tenant_id, context.workspace_id) * 3,
                ).fetchone()
        except CloudDatabaseError as error:
            raise OperationsStatusError() from error
        if row is None:
            raise OperationsStatusError()
        return OperationsCounts(int(row[0]), int(row[1]), int(row[2]))

    def _transaction(self, context):
        return self._cloud_store._transaction(CloudAccessContext(context.tenant_id, context.workspace_id, context.actor_id, "operations.read"))

    @staticmethod
    def _metric(metric_id, value, when, state="measured"):
        unit, coverage = observation_scope(metric_id)
        return OperationsObservation(metric_id, state, value, unit, when, coverage, OBSERVATION_STATES[state])

    def _queue(self, context, *, objects):
        table = "durable_jobs" if objects else "document_processing_jobs"
        with self._transaction(context) as connection:
            row = connection.execute("SELECT count(*) FILTER(WHERE state='pending'),count(*) FILTER(WHERE state='retry_wait'),"
                "count(*) FILTER(WHERE state='leased'),count(*) FILTER(WHERE state='dead_letter')" +
                (",COALESCE(extract(epoch FROM (CURRENT_TIMESTAMP-min(created_at) FILTER(WHERE state IN ('pending','retry_wait')))),0)" if objects else "") +
                f" FROM {table} WHERE tenant_id=%s AND workspace_id=%s", (context.tenant_id, context.workspace_id)).fetchone()
        if row is None: raise ValueError()
        values = list(row)
        if objects:
            if isinstance(values[-1], bool) or not math.isfinite(float(values[-1])) or values[-1] < 0: raise ValueError()
            values[-1] = math.floor(values[-1])
        return values

    def _actor_data(self, context):
        if not context.trace_id or not context.policy_version: raise ValueError("OPERATIONS_SOURCE_CONTEXT_UNAVAILABLE")
        with self._transaction(context) as connection:
            source_query = (
                "SELECT DISTINCT n.tenant_id,n.workspace_id,n.notebook_id,n.created_by,s.record_id,s.state,sv.record_id,"
                "o.object_id,o.byte_size,o.status,ix.record_id FROM notebooks n JOIN notebook_bindings b ON "
                "b.tenant_id=n.tenant_id AND b.workspace_id=n.workspace_id AND b.notebook_id=n.notebook_id AND b.binding_kind='source' "
                "JOIN source_versions sv ON sv.tenant_id=b.tenant_id AND sv.workspace_id=b.workspace_id AND sv.record_id=b.version_id AND sv.source_id=b.record_id "
                "JOIN sources s ON s.tenant_id=sv.tenant_id AND s.workspace_id=sv.workspace_id AND s.record_id=sv.source_id "
                "LEFT JOIN object_records o ON o.tenant_id=sv.tenant_id AND o.workspace_id=sv.workspace_id AND o.object_id=sv.object_id "
                "LEFT JOIN LATERAL (SELECT record_id FROM index_versions i WHERE i.tenant_id=sv.tenant_id AND i.workspace_id=sv.workspace_id "
                "AND i.source_version_id=sv.record_id ORDER BY i.created_at DESC,i.record_id DESC LIMIT 1) ix ON true "
                "WHERE n.tenant_id=%s AND n.workspace_id=%s AND n.created_by=%s "
                "AND s.state NOT IN ('expired','disabled','deleting','deleted') "
                "AND NOT EXISTS(SELECT 1 FROM notebook_deletion_requests d WHERE d.tenant_id=n.tenant_id AND d.workspace_id=n.workspace_id "
                "AND d.notebook_id=n.notebook_id AND d.state IN ('accepted','deleting','completed')) "
                "AND NOT EXISTS(SELECT 1 FROM notebook_source_unbindings u WHERE u.tenant_id=b.tenant_id AND u.workspace_id=b.workspace_id "
                "AND u.notebook_id=b.notebook_id AND u.source_id=b.record_id AND u.source_version_id=b.version_id) "
                "AND NOT EXISTS(SELECT 1 FROM deletion_requests d WHERE d.tenant_id=s.tenant_id AND d.workspace_id=s.workspace_id "
                "AND d.source_id=s.record_id AND (d.source_active=false OR d.state='purged'))")
            source_parameters = (context.tenant_id, context.workspace_id, context.actor_id)
            sources = connection.execute(source_query, source_parameters).fetchall()
            valid, waiting, indexes, objects, incomplete = set(), set(), set(), {}, False
            for tenant, workspace, notebook, owner, source, state, version, obj, size, status, index in sources:
                if (tenant, workspace, owner) != (context.tenant_id, context.workspace_id, context.actor_id): continue
                scoped = SimpleNamespace(tenant_id=tenant, workspace_id=workspace, actor_id=context.actor_id, notebook_id=notebook,
                    trace_id=context.trace_id, policy_version=context.policy_version)
                if not _source_access_allowed(connection, scoped, version) or not _special_source_access_allowed(connection, scoped, version): continue
                valid.add((notebook, version))
                if state == "waiting_model": waiting.add(source)
                if index is not None: indexes.add(index)
                if obj is None or status != "completed" or type(size) is not int or size < 0:
                    incomplete = True; continue
                if obj in objects and objects[obj] != size: raise ValueError()
                objects[obj] = size
            attempt_query = (
                "SELECT DISTINCT n.tenant_id,n.workspace_id,n.notebook_id,n.created_by,r.record_id,r.created_by,"
                "rs.canonical_json->'source_version_ids',m.record_id,m.usage_json FROM notebooks n JOIN notebook_bindings b "
                "ON b.tenant_id=n.tenant_id AND b.workspace_id=n.workspace_id AND b.notebook_id=n.notebook_id AND b.binding_kind='conversation_thread' "
                "JOIN runs r ON r.tenant_id=b.tenant_id AND r.workspace_id=b.workspace_id AND r.conversation_id=b.record_id "
                "JOIN run_snapshots rs ON rs.tenant_id=r.tenant_id AND rs.workspace_id=r.workspace_id AND rs.run_id=r.record_id "
                "JOIN routing_decisions rd ON rd.tenant_id=r.tenant_id AND rd.workspace_id=r.workspace_id AND rd.run_id=r.record_id "
                "JOIN model_attempts m ON m.tenant_id=rd.tenant_id AND m.workspace_id=rd.workspace_id AND m.routing_decision_id=rd.record_id "
                "WHERE n.tenant_id=%s AND n.workspace_id=%s AND n.created_by=%s AND r.created_by=%s AND m.created_by=%s "
                "AND NOT EXISTS(SELECT 1 FROM notebook_deletion_requests d WHERE d.tenant_id=n.tenant_id AND d.workspace_id=n.workspace_id "
                "AND d.notebook_id=n.notebook_id AND d.state IN ('accepted','deleting','completed'))")
            attempt_parameters = (context.tenant_id, context.workspace_id, context.actor_id, context.actor_id, context.actor_id)
            attempts = connection.execute(attempt_query, attempt_parameters).fetchall()
            usage = {}
            for tenant, workspace, notebook, owner, run, actor, versions, attempt, value in attempts:
                if (tenant, workspace, owner, actor) != (context.tenant_id, context.workspace_id, context.actor_id, context.actor_id): continue
                if not isinstance(versions, list) or any(not isinstance(version, str) or (notebook, version) not in valid for version in versions): continue
                if attempt in usage and usage[attempt] != value: raise ValueError()
                usage[attempt] = value
            # 결과 직전 ACL을 다시 읽는다. 현재 권한과 commit의 원자 직렬화를 주장하지 않는다.
            for notebook, version in valid:
                scoped = SimpleNamespace(tenant_id=context.tenant_id, workspace_id=context.workspace_id, actor_id=context.actor_id, notebook_id=notebook,
                    trace_id=context.trace_id, policy_version=context.policy_version)
                if not _source_access_allowed(connection, scoped, version) or not _special_source_access_allowed(connection, scoped, version): raise ValueError()
            if set(connection.execute(source_query, source_parameters).fetchall()) != set(sources): raise ValueError()
            normalize = lambda rows: sorted(json.dumps(row, sort_keys=True, separators=(",", ":"), allow_nan=False) for row in rows)
            if normalize(connection.execute(attempt_query, attempt_parameters).fetchall()) != normalize(attempts): raise ValueError()
        return len(waiting), len(indexes), sum(objects.values()), incomplete, usage

    def read_observations(self, context):
        result = []
        for objects, ids in ((False, OBSERVATION_IDS[:4]), (True, OBSERVATION_IDS[4:9])):
            try:
                values = self._queue(context, objects=objects)
                when = datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")
                if len(values) != len(ids): raise ValueError()
                group = tuple(self._metric(key, value, when) for key, value in zip(ids, values))
                result.extend(group)
            except (CloudDatabaseError, Error, ValueError, OverflowError): result.extend(unknown_observations("unavailable", ids))
        try:
            waiting, indexes, size, incomplete, usage = self._actor_data(context)
            when = datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")
            result.extend((self._metric("source_waiting_model", waiting, when), self._metric("index_records_current", indexes, when),
                self._metric("source_storage_bytes", size, when, "partial" if incomplete else "measured")))
            valid_integer = lambda value: type(value) is int and 0 <= value <= 9007199254740991
            fields = ("prompt_tokens", "completion_tokens", "total_tokens")
            for field, metric in zip(fields, OBSERVATION_IDS[12:15]):
                values = [row[field] for row in usage.values() if isinstance(row, dict) and valid_integer(row.get(field))]
                if not values: result.extend(unknown_observations("not_collected", (metric,)))
                else: result.append(self._metric(metric, sum(values), when, "partial" if len(values) != len(usage) else "measured"))
            missing = sum(not isinstance(row, dict) or any(not valid_integer(row.get(field)) for field in fields) for row in usage.values())
            result.append(self._metric("attempts_without_reported_usage", missing, when))
        except NotificationError as error:
            if error.code != "DATABASE_UNAVAILABLE" or error.http_status != 503: raise
            result = result[:9] + list(unknown_observations("unavailable", OBSERVATION_IDS[9:]))
        except (CloudDatabaseError, Error, ValueError, OverflowError):
            result = result[:9] + list(unknown_observations("unavailable", OBSERVATION_IDS[9:]))
        return tuple(result)
