from __future__ import annotations

import os
import hashlib
import sqlite3
from datetime import datetime, timezone
from pathlib import Path

from argon2 import PasswordHasher

from daon_user_api.cloud_storage import PostgresCloudStore
from daon_user_api.data_canon import PostgresDataCanonStore
from daon_user_api.document_index_postgres import PostgresDocumentIndex
from daon_user_api.document_processing import DocumentProcessingContext
from daon_user_api.document_processing_postgres import PostgresDocumentProcessingRepository
from daon_user_api.document_processing_queue import PostgresDocumentProcessingQueue
from daon_user_api.document_understanding_adapter import (
    DocumentUnderstandingResult, ParserValidation, SemanticUnderstanding,
)
from daon_user_api.authorization import SqliteAuthorizationRepository
from daon_user_api.authorization import Role
from daon_user_api.identity import SqliteIdentityRepository
from daon_user_api.object_queue import (
    PostgresObjectQueueStore, StagedObject, StoredObject,
)
from daon_user_api.source_upload import PostgresSourceUploadService


TENANT = "tenant-external-gate"
USER = "user-external-gate"
WORKSPACE = f"workspace-{hashlib.sha256(TENANT.encode()).hexdigest()[:24]}"
PDF = b"%PDF-1.7\nExternal deny evidence fixture\n%%EOF"


class MemoryObjectStorage:
    def __init__(self) -> None:
        self.values: dict[str, tuple[bytes, str, str]] = {}

    def health(self) -> bool:
        return True

    def put_staged(self, key: str, content: bytes, content_type: str, digest: str) -> StagedObject:
        self.values[key] = (content, content_type, digest)
        return StagedObject(key, digest, len(content), content_type, "etag-staged", None)

    def promote(self, staged: StagedObject, final_key: str, *, expected_digest: str,
                expected_size: int, content_type: str) -> StoredObject:
        content, _, digest = self.values[staged.key]
        assert digest == expected_digest and len(content) == expected_size
        self.values[final_key] = (content, content_type, digest)
        return StoredObject(final_key, digest, len(content), content_type, "etag-final", None)

    def get(self, key: str) -> bytes:
        return self.values[key][0]


db = Path(os.environ["DAON_API_DATABASE_PATH"])
login = os.environ["DAON_GATE_LOGIN"]
password = os.environ["DAON_GATE_PASSWORD"]
now = datetime.now(timezone.utc).isoformat()
identity_schema = SqliteIdentityRepository(db)
authorization_schema = SqliteAuthorizationRepository(db)
with sqlite3.connect(db) as connection:
    connection.execute("INSERT OR REPLACE INTO users(user_id,issuer,subject,login_id,email,password_digest,email_verified_at,state) VALUES (?,?,?,?,?,?,?,'active')", (USER, "local", login, login, f"{login}@example.invalid", PasswordHasher().hash(password), now))
    connection.execute("INSERT OR REPLACE INTO memberships(tenant_id,user_id,role) VALUES (?,?,?)", (TENANT, USER, "personal_owner"))
    identity_schema._ensure_tenant(connection, TENANT)
identity_schema.close()
authorization_schema.bootstrap_workspace(
    tenant_id=TENANT, workspace_id=WORKSPACE, owner_user_id=USER,
    owner_role=Role.PERSONAL_OWNER, workspace_kind="personal",
    data_area="cloud_sync", cost_limit_cents=1000,
    now=datetime.now(timezone.utc),
)
authorization_schema.close()

dsn = os.environ["DAON_CLOUD_DATABASE_DSN"]
cloud = PostgresCloudStore(dsn)
cloud._pool.open(wait=True, timeout=30)
storage = MemoryObjectStorage()
queue = PostgresObjectQueueStore(dsn)
upload = PostgresSourceUploadService(
    queue_store=queue, object_storage=storage, canon_store=PostgresDataCanonStore(dsn),
)
context = DocumentProcessingContext(TENANT, WORKSPACE, USER, "trace-external-gate", "runtime-policy-v1")
try:
    with cloud._transaction(type("Scope", (), {"tenant_id":TENANT,"workspace_id":WORKSPACE,"actor_id":USER,"capability":"fixture.seed"})()) as connection:
        connection.execute("INSERT INTO tenants(tenant_id,display_name) VALUES (%s,'External Gate') ON CONFLICT DO NOTHING", (TENANT,))
        connection.execute("INSERT INTO workspaces(tenant_id,workspace_id,display_name) VALUES (%s,%s,'External Gate') ON CONFLICT DO NOTHING", (TENANT, WORKSPACE))
        connection.execute("INSERT INTO provider_setting_profiles(tenant_id,workspace_id,profile_id,provider_code,provider_kind,base_url,active,version,updated_by,policy_version,trace_id) VALUES (%s,%s,'provider-upstage','UPSTAGE','external_api','https://api.upstage.ai/v1',true,1,%s,'runtime-policy-v1','trace-external-gate') ON CONFLICT DO NOTHING", (TENANT, WORKSPACE, USER))
        connection.execute("INSERT INTO provider_setting_deployments(tenant_id,workspace_id,deployment_id,profile_id,model_id,roles,active,selected,version,updated_by,policy_version,trace_id) VALUES (%s,%s,'deployment-upstage','provider-upstage','solar-pro',ARRAY['text'],true,true,1,%s,'runtime-policy-v1','trace-external-gate') ON CONFLICT DO NOTHING", (TENANT, WORKSPACE, USER))
        connection.execute("INSERT INTO provider_setting_role_bindings(tenant_id,workspace_id,role,deployment_id,version,updated_by,policy_version,trace_id) VALUES (%s,%s,'text','deployment-upstage',1,%s,'runtime-policy-v1','trace-external-gate') ON CONFLICT DO NOTHING", (TENANT, WORKSPACE, USER))
    uploaded = upload.register_pdf(tenant_id=TENANT, workspace_id=WORKSPACE, actor_id=USER,
        filename="external-gate.pdf", content=PDF, idempotency_key="external-gate-upload-01",
        trace_id="trace-external-gate")
    result = DocumentUnderstandingResult(
        uploaded.source_id, uploaded.source_version_id, "ready",
        ("vision_llm_understanding", "parser_ocr_validation", "evidence_reconciliation"),
        SemanticUnderstanding("Daon", "정책 외부 전송 차단 근거", ("정책 외부 전송 차단 근거",)),
        ParserValidation("정책 외부 전송 차단 근거", "정책 외부 전송 차단 근거", "<p>정책 외부 전송 차단 근거</p>", (1,), ((1, "정책 외부 전송 차단 근거"),)),
        {"provider_code":"UPSTAGE","parser_role":"validation_only"},
    )
    processing = PostgresDocumentProcessingRepository(cloud, storage)
    with cloud._transaction(type("Scope", (), {"tenant_id":TENANT,"workspace_id":WORKSPACE,"actor_id":USER,"capability":"fixture.read"})()) as connection:
        source_state = connection.execute("SELECT state FROM sources WHERE record_id=%s", (uploaded.source_id,)).fetchone()[0]
    if source_state == "registered":
        run_id = processing.start(context, uploaded.source_version_id, enqueue=True)
        processing_queue = PostgresDocumentProcessingQueue(dsn, cloud)
        processing_queue._pool.open(wait=True, timeout=30)
        job = processing_queue.claim("external-gate-worker")
        assert job is not None and job.processing_run_id == run_id
        processing.complete(context, run_id, result)
        PostgresDocumentIndex(cloud).index_result(context, result)
        processing_queue.complete(
            job, "external-gate-worker", now=datetime.now(timezone.utc),
        )
        processing_queue.close()
    print(f"FIXTURE_READY source_id={uploaded.source_id} source_version_id={uploaded.source_version_id}")
finally:
    upload.close(); queue.close(); cloud.close()
