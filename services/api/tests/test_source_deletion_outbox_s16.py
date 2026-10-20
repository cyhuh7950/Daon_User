"""S16 completed object queue cleanup in real isolated SQL, no external storage."""
from uuid import uuid4

import psycopg
from psycopg.types.json import Jsonb

from daon_user_api.cloud_storage import CloudAccessContext
from daon_user_api.object_queue import ObjectKeyPolicy
from test_source_deletion_device_s16 import pg
from test_source_deletion_postgres_s16 import seed, canon, ObjectPort, service, request


def completed_job(connection, f, oid):
    event, job = "outbox-" + oid, "job-" + oid
    payload = Jsonb({"schema_version": 1, "object_id": oid})
    connection.execute("INSERT INTO object_outbox_events(tenant_id,workspace_id,event_id,object_id,event_kind,payload_reference,schema_version,status,trace_id,created_at,completed_at) "
        "VALUES(%s,%s,%s,%s,'object.promote',%s,1,'completed',%s,now(),now())",
        (f.ctx.tenant_id, f.ctx.workspace_id, event, oid, payload, f.ctx.trace_id))
    connection.execute("INSERT INTO durable_jobs(tenant_id,workspace_id,job_id,event_id,job_kind,payload_reference,payload_schema_version,deduplication_key,state,attempt,max_attempts,next_attempt_at,created_by,trace_id,created_at,completed_at) "
        "VALUES(%s,%s,%s,%s,'object.promote',%s,1,%s,'completed',1,5,now(),%s,%s,now(),now())",
        (f.ctx.tenant_id, f.ctx.workspace_id, job, event, payload, job, f.ctx.actor_id, f.ctx.trace_id))
    connection.execute("INSERT INTO job_attempts(tenant_id,workspace_id,job_id,attempt_number,worker_id,outcome,trace_id,started_at,finished_at) "
        "VALUES(%s,%s,%s,1,'s16-fixture','completed',%s,now(),now())",
        (f.ctx.tenant_id, f.ctx.workspace_id, job, f.ctx.trace_id))
    return event, job


def test_s16_pg_source_cleanup_removes_completed_queue_graph_and_preserves_other_objects(pg):
    f = seed(pg)
    other_source, other_version = "src-" + uuid4().hex, "sv-" + uuid4().hex
    others = [uuid4().hex, uuid4().hex]  # Another Source's object, then an unrelated object.
    policy = ObjectKeyPolicy()
    access = CloudAccessContext(f.ctx.tenant_id, f.ctx.workspace_id, f.ctx.actor_id, "s16.fixture")
    other_keys = {key for oid in others for key in (policy.staging_key(access, "source", oid), policy.final_key(access, "source", oid))}
    with psycopg.connect(pg.admin) as connection:
        revision = connection.execute("SELECT version_num FROM alembic_version").fetchone()[0]
        # Isolate the queue defect from the separately approved device fix.
        # 0063's historical active receipt is deliberately synthetic, not login.
        connection.execute("UPDATE identity_devices SET state=%s WHERE device_id=%s",
            ("active" if revision == "0063" else "registered", f.device))
        for oid in others:
            connection.execute("INSERT INTO object_records(tenant_id,workspace_id,object_id,area,staging_key,object_key,digest_sha256,byte_size,content_type,status,created_by,trace_id,idempotency_key,request_fingerprint,created_at) "
                "VALUES(%s,%s,%s,'source',%s,%s,%s,2,'text/plain','completed',%s,%s,%s,%s,now())",
                (f.ctx.tenant_id, f.ctx.workspace_id, oid, policy.staging_key(access, "source", oid),
                 policy.final_key(access, "source", oid), "f" * 64, f.ctx.actor_id, f.ctx.trace_id, oid, "f" * 64))
        canon(connection, f.ctx, "sources", other_source, {})
        canon(connection, f.ctx, "source_versions", other_version,
            {"filename": "S16-other.txt", "object_id": others[0], "content_type": "text/plain", "byte_size": 2,
             "content_digest_sha256": "f" * 64, "deletion_policy": "delete_with_notebook"},
            extra=",source_id,object_id", params=(other_source, others[0]))
        event, job = completed_job(connection, f, f.oid)
        other_graphs = [completed_job(connection, f, oid) for oid in others]
        assert event != f.oid
    storage = ObjectPort(pg, f)
    storage.keys.update(other_keys)
    store, svc = service(pg, storage)
    try:
        result = request(svc, f)
        assert result.state == "purged" and result.source_active is False
        assert len(storage.calls) == 2 and set(storage.calls) == set(f.keys)
        assert storage.keys == other_keys
        with psycopg.connect(pg.admin) as connection:
            for table, column, identifier in (("sources", "record_id", f.source),
                ("source_versions", "record_id", f.version), ("object_records", "object_id", f.oid),
                ("object_outbox_events", "event_id", event), ("durable_jobs", "job_id", job),
                ("job_attempts", "job_id", job)):
                assert connection.execute(f"SELECT count(*) FROM {table} WHERE {column}=%s", (identifier,)).fetchone()[0] == 0
            assert connection.execute("SELECT count(*) FROM sources WHERE record_id=%s", (other_source,)).fetchone()[0] == 1
            assert connection.execute("SELECT count(*) FROM source_versions WHERE record_id=%s", (other_version,)).fetchone()[0] == 1
            assert connection.execute("SELECT count(*) FROM object_records WHERE object_id=ANY(%s)", (others,)).fetchone()[0] == 2
            for other_event, other_job in other_graphs:
                assert connection.execute("SELECT count(*) FROM object_outbox_events WHERE event_id=%s AND status='completed'", (other_event,)).fetchone()[0] == 1
                assert connection.execute("SELECT count(*) FROM durable_jobs WHERE job_id=%s AND state='completed'", (other_job,)).fetchone()[0] == 1
                assert connection.execute("SELECT count(*) FROM job_attempts WHERE job_id=%s AND outcome='completed'", (other_job,)).fetchone()[0] == 1
    finally:
        store.close()
