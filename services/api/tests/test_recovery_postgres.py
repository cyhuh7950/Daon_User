from __future__ import annotations

import hashlib
import io
import os
import secrets
import shutil
import unittest
import copy
import tempfile
from dataclasses import replace
from contextlib import contextmanager
from types import SimpleNamespace
from unittest.mock import patch
from datetime import datetime, timedelta, timezone
from pathlib import Path

import psycopg

from daon_user_api import recovery_postgres
from daon_user_api.data_canon import canonical_json_bytes

from daon_user_api.cloud_storage import CloudAccessContext, PostgresCloudStore
from daon_user_api.object_queue import (
    DurableObjectWorker,
    MinioObjectStorageAdapter,
    ObjectQueueCoordinator,
    ObjectKeyPolicy,
    PostgresObjectQueueStore,
)
from daon_user_api.recovery import BackupObjectInput, BackupSelectionInput, RecoveryContext, RecoveryError, RestoreDestination
from daon_user_api.recovery_capture import FileCaptureStore, decode_manifest


class RegisteredRestoreHistoryTests(unittest.TestCase):
    """실제 preview AEAD/조회 decoder, DB 현재 scope 경계는 대역. 파일/DB 생성0."""
    def setUp(self):
        self.service = object.__new__(recovery_postgres.PostgresRecoveryService)
        self.service._destination_registry = None
        self.service._destination_probe = None
        self.service._registered_target_factory = None
        self.service._lineage_capture_store = SimpleNamespace(key_version="v3", key_reference="history-key")
        self.service._manifest_master_key = b"h" * 32
        self.service._key = hashlib.sha256(b"h" * 32).digest()
        self.context = RecoveryContext("tenant-a", "workspace-a", "actor-a", "trace-a", "policy-a", True)
        now = datetime(2026, 10, 20, tzinfo=timezone.utc)
        roots = ["version-a"]
        self.row = dict(tenant_id="tenant-a", workspace_id="workspace-a", actor_id="actor-a",
            request_id="restore-a", backup_id="backup-a", state="completed", version=6,
            preview_version=1, registry_generation_id="generation-a", destination_id="destination-a",
            destination_version=1, source_manifest_digest="a" * 64, closure_fingerprint="b" * 64,
            source_policy_version="policy-a", retention_watermark="c" * 64,
            included_roots_fingerprint=hashlib.sha256(b'["version-a"]').hexdigest(),
            excluded_roots_fingerprint=hashlib.sha256(b'[]').hexdigest(),
            expires_at=now + timedelta(minutes=5), verification_digest="d" * 64)
        payload = dict(schema_version=1, current_retention_watermark="c" * 64,
            current_policy_version="policy-a", current_authorization_fingerprint="e" * 64,
            runtime_policy_version="policy-a", included_roots=roots, excluded_roots=[], exclusion_reasons=[],
            session_id="session-a", device_id="device-a", request_version=2,
            destination_summary=dict(destination_id="destination-a", version=1, display_name="QA 격리",
                expires_at=(now + timedelta(minutes=10)).isoformat()), catalog_digest="f" * 64,
            created_at=now.isoformat())
        self.row["encrypted_preview_receipt"] = self.service._seal_registered_preview(self.row, payload)
        self.queries = []
        self.legacy_row = None
        self.registered_tables = dict(previews_present=True, reservations_present=True)
        owner = self
        class Connection:
            def execute(inner, sql, params=()):
                owner.queries.append(sql)
                if sql.startswith("SELECT to_regclass("):
                    if isinstance(owner.registered_tables, Exception):
                        raise owner.registered_tables
                    return SimpleNamespace(fetchone=lambda: owner.registered_tables)
                if "public.registered_restore_previews" in sql:
                    # 실제 SQL이 현재 actor를 결속해야만 대역도 해당 필터를 적용한다.
                    if "r.actor_id=nullif(current_setting('app.actor_id',true),'')" not in sql:
                        raise AssertionError("CURRENT_ACTOR_FILTER_REQUIRED")
                    visible = owner.scope == ("tenant-a", "workspace-a", "actor-a")
                    return SimpleNamespace(fetchone=lambda: owner.row if visible and params == ("restore-a",) else None)
                return SimpleNamespace(fetchone=lambda: owner.legacy_row)
        @contextmanager
        def transaction(*scope):
            self.scope = scope
            yield Connection()
        self.service._transaction = transaction

    def test_completed_registered_history_remains_readable_after_destination_retirement(self):
        view = self.service.get_restore_request(self.context, "restore-a")
        self.assertEqual((view.request_id, view.state, view.version), ("restore-a", "completed", 6))
        self.assertEqual(view.verification_digest, "d" * 64)
        self.assertEqual((view.preview.kind, view.preview.included_root_count), ("selected_source_lineage", 1))

    def test_retired_destination_does_not_allow_execution_or_destination_lookup(self):
        for call in (
            lambda: self.service.list_restore_destinations(self.context, "backup-a", recheck=lambda: None),
            lambda: self.service._execute_registered_restore_owned(self.context, "restore-a", expected_version=6,
                preview_version=1, idempotency_key="execute-a", step_up_verified=True, recheck=lambda: None,
                ownership_check=lambda: None),
        ):
            with self.assertRaises(RecoveryError) as caught:
                call()
            self.assertEqual(caught.exception.status, 503)
        self.assertEqual(self.queries, [])

    def test_registered_history_keeps_current_scope_and_admin_checks(self):
        for context, status in ((replace(self.context, actor_id="actor-b"), 404),
                                (replace(self.context, workspace_id="workspace-b"), 404),
                                (replace(self.context, organization_admin=False), 403)):
            with self.subTest(context=context), self.assertRaises(RecoveryError) as caught:
                self.service.get_restore_request(context, "restore-a")
            self.assertEqual(caught.exception.status, status)

    def test_registered_history_cipher_damage_is_not_hidden_by_legacy_fallback(self):
        raw = self.row["encrypted_preview_receipt"]
        self.row["encrypted_preview_receipt"] = raw[:-1] + bytes([raw[-1] ^ 1])
        with self.assertRaises(RecoveryError) as caught:
            self.service.get_restore_request(self.context, "restore-a")
        self.assertEqual((caught.exception.code, caught.exception.status), ("BACKUP_MANIFEST_INVALID", 409))
        self.assertFalse(any("FROM restore_requests r" in sql for sql in self.queries))

    def test_legacy_without_lineage_store_never_queries_registered_tables(self):
        for absent in (False, True):
            self.service._lineage_capture_store = None
            if absent:
                del self.service._lineage_capture_store
            self.queries.clear()
            with self.subTest(absent=absent), self.assertRaises(RecoveryError) as caught:
                self.service.get_restore_request(self.context, "legacy-missing")
            self.assertEqual(caught.exception.status, 404)
            self.assertEqual(len(self.queries), 1)
            self.assertNotIn("registered_restore_previews", self.queries[0])

    def test_legacy_preview_remains_readable_when_registered_history_is_absent(self):
        self.legacy_row = dict(self.row, request_id="legacy-a", destination_tenant_id="target-tenant", destination_workspace_id="target-workspace",
            destination_database_id="target-db", destination_bucket_id="target-bucket",
            encrypted_adjustment_manifest=self.service._encode_preview(("object-a",), (), ()),
            preview_created_at=datetime(2026, 10, 20, tzinfo=timezone.utc))
        view = self.service.get_restore_request(self.context, "legacy-a")
        self.assertEqual((view.request_id, view.state), ("legacy-a", "completed"))
        self.assertEqual(view.preview.included_object_ids, ("object-a",))

    def test_legacy_schema_with_lineage_store_skips_missing_registered_tables(self):
        self.registered_tables = dict(previews_present=False, reservations_present=False)
        with self.assertRaises(RecoveryError) as caught:
            self.service.get_restore_request(self.context, "legacy-missing")
        self.assertEqual(caught.exception.status, 404)
        self.assertTrue(self.queries[0].startswith("SELECT to_regclass("))
        self.assertFalse(any("FROM public.registered_restore_previews" in sql for sql in self.queries))

    def test_partial_or_invalid_registered_schema_never_falls_back_to_legacy(self):
        for tables in (dict(previews_present=True, reservations_present=False),
                       dict(previews_present=False, reservations_present=True), None,
                       dict(previews_present=1, reservations_present=True),
                       dict(previews_present=True, reservations_present=True, extra=True)):
            self.registered_tables = tables
            self.queries.clear()
            with self.subTest(tables=tables), self.assertRaises(RecoveryError) as caught:
                self.service.get_restore_request(self.context, "restore-a")
            self.assertEqual((caught.exception.code, caught.exception.status), ("RESOURCE_UNAVAILABLE", 503))
            self.assertEqual(len(self.queries), 1)

    def test_registered_schema_read_failure_is_not_hidden_as_missing_history(self):
        self.registered_tables = psycopg.OperationalError("synthetic database unavailable")
        with self.assertRaises(RecoveryError) as caught:
            self.service.get_restore_request(self.context, "restore-a")
        self.assertEqual((caught.exception.code, caught.exception.status), ("RESOURCE_UNAVAILABLE", 503))
        self.assertEqual(len(self.queries), 1)


class RegisteredRecoveryBoundaryTests(unittest.TestCase):
    def test_r4_registered_restore_row_reservation_join_uses_parent_scope_without_ambiguous_using(self):
        # SQL 출력 경계 검사이며 실제 PostgreSQL 실행 증거는 아니다.
        emitted=[];row=object()
        class Connection:
            def execute(self,sql,params=()):
                emitted.append((sql,params))
                return SimpleNamespace(fetchone=lambda:row)
        service=object.__new__(PostgresRecoveryService)
        self.assertIs(service._registered_restore_row(Connection(),"request-a"),row)
        self.assertEqual(len(emitted),1)
        sql,params=emitted[0]
        self.assertEqual(params,("request-a",))
        self.assertIn("JOIN public.registered_restore_target_reservations d ON "
            "(d.tenant_id,d.workspace_id,d.request_id)=(r.tenant_id,r.workspace_id,r.request_id) ",sql)
        self.assertNotIn("USING(",sql)
        self.assertIn("FROM public.restore_requests r JOIN LATERAL (SELECT * FROM public.registered_restore_previews p0 ",sql)
        self.assertIn("WHERE (p0.tenant_id,p0.workspace_id,p0.request_id)=(r.tenant_id,r.workspace_id,r.request_id) "
            "ORDER BY p0.preview_version DESC LIMIT 1) p ON true ",sql)
        self.assertIn("LEFT JOIN LATERAL (SELECT verification_digest FROM public.restore_verifications v0 WHERE "
            "(v0.tenant_id,v0.workspace_id,v0.request_id)=(r.tenant_id,r.workspace_id,r.request_id) "
            "ORDER BY v0.created_at DESC LIMIT 1) v ON true ",sql)
        self.assertTrue(sql.endswith("WHERE r.request_id=%s AND r.actor_id=nullif(current_setting('app.actor_id',true),'')"))

    def test_r4_purged_missing_version_excludes_only_proven_root_without_bypassing_acl(self):
        from test_recovery_lineage import Ledger,recanonical
        from daon_user_api.recovery_lineage_capture import LineageCaptureScope
        ledger=Ledger()
        for table in ("sources","source_versions","object_records","canon_state_transitions","canon_transition_attempts","notebook_bindings"):
            for original in list(ledger.tables[table]):
                row=copy.deepcopy(original)
                for field in ("record_id","aggregate_id","previous_version_id","source_id","object_id","version_id","transition_id","attempt_id"):
                    if row.get(field) is not None:row[field]=row[field]+"-b"
                if table=="source_versions":recanonical(row,{**row["canonical_json"],"object_id":row["object_id"]})
                ledger.tables[table].append(row)
        ledger.selection=BackupSelectionInput("notebook-a",("root-version","root-version-b"))
        selection=ledger.resolve();metadata=selection.metadata();scope=LineageCaptureScope.from_selection(ledger.context,selection,capture_id="capture-mixed")
        for table in ("sources","source_versions","notebook_bindings"):
            ledger.tables[table]=[r for r in ledger.tables[table] if (r.get("source_id") or r["record_id"])!="source-a"]
        ledger.tables["deletion_requests"]=[dict(tenant_id=ledger.context.tenant_id,workspace_id=ledger.context.workspace_id,request_id="purge-a",
            source_id="source-a",state="purged",version=4,source_active=False,purge_started=True,policy_version="purge-policy")]
        class Connection:
            info=ledger.info
            def cursor(self,*,row_factory):return ledger
        service=object.__new__(PostgresRecoveryService);connection=Connection()
        result=service._registered_current_decision(connection,ledger.context,scope,metadata)
        self.assertEqual(result["included_roots"],["root-version-b"])
        self.assertEqual(result["exclusion_reasons"],[["root-version","purged"]])
        ledger.revoked.add("prior-version")
        with self.assertRaises(RecoveryError) as denied:service._registered_current_decision(connection,ledger.context,scope,metadata)
        self.assertEqual(denied.exception.status,403)
        ledger.revoked.clear();ledger.tables["deletion_requests"][0]["state"]="cleanup_pending"
        with self.assertRaises(RecoveryError):service._registered_current_decision(connection,ledger.context,scope,metadata)
        ledger.tables["deletion_requests"][0]["state"]="purged"
        ledger.tables["deletion_requests"].append({**ledger.tables["deletion_requests"][0],"request_id":"purge-b","source_id":"source-a-b"})
        with self.assertRaises(RecoveryError) as excluded:service._registered_current_decision(connection,ledger.context,scope,metadata)
        self.assertEqual(excluded.exception.status,409)

    def test_r4_actual_policy_acl_membership_witness_changes_hash_without_losing_manage(self):
        from test_recovery_lineage import Ledger
        from daon_user_api.recovery_lineage_capture import LineageCaptureScope
        ledger=Ledger();selected=ledger.resolve();scope=LineageCaptureScope.from_selection(ledger.context,selected,capture_id="capture-policy")
        class Connection:
            info=ledger.info
            def cursor(self,*,row_factory):return ledger
        service=object.__new__(PostgresRecoveryService);connection=Connection()
        first=service._registered_current_decision(connection,ledger.context,scope,selected.metadata())
        original=ledger.auth.execute;mode=[None]
        def rows(sql,params=()):
            cursor=original(sql,params);row=cursor.fetchone()
            if row is not None and isinstance(row,dict):
                row=dict(row)
                if mode[0]=="policy" and "FROM identity_auth_tenant_policies" in sql:row["version"]=2
                if mode[0]=="workspace-policy" and "FROM identity_auth_workspace_policies" in sql:row["version"]=2
                if mode[0]=="acl" and "FROM identity_auth_workspaces" in sql:row["acl_version"]=2
                if mode[0]=="membership" and "FROM identity_auth_tenant_roles" in sql:row["version"]=2
            return SimpleNamespace(fetchone=lambda:row)
        ledger.auth.execute=rows
        for kind in ("policy","workspace-policy","acl","membership"):
            with self.subTest(witness=kind):
                mode[0]=kind
                decision=service._registered_current_decision(connection,ledger.context,scope,selected.metadata())
                self.assertNotEqual(decision["current_retention_watermark"],first["current_retention_watermark"])
                if kind=="policy":self.assertEqual(decision["current_policy_version"],"tenant-2:workspace-1")
                if kind=="workspace-policy":self.assertEqual(decision["current_policy_version"],"tenant-1:workspace-2")
        self.assertEqual(ledger.context.policy_version,"policy-current")

    def test_r4_fixed_target_factory_requires_exact_region_and_reuses_caller_owned_queue(self):
        from daon_user_api.recovery_destinations import PhysicalProbeInstance,ProbeFoundation,ProbeRequest,RegisteredDestination,TrustedPhysicalProbe,DatabaseIdentity
        factory_type=getattr(recovery_postgres,"PostgresRegisteredTargetFactory",None)
        descriptor_type=getattr(recovery_postgres,"RegisteredTargetDescriptor",None)
        self.assertTrue(callable(factory_type) and callable(descriptor_type),"fixed R4 target builder missing")
        calls=[];context=RecoveryContext("tenant-a","workspace-a","actor-a","trace-a","policy-a",True)
        class Connection:
            def execute(self,sql,params=()):
                self_sql=sql
                if "pg_control_system" not in self_sql:raise AssertionError("READ_ONLY_FIXED_QUEUE_IDENTITY_REQUIRED")
                return SimpleNamespace(fetchone=lambda:("daon_app",False,False,"qa-target","10001",222))
        @contextmanager
        def connection():yield Connection()
        queue=object.__new__(PostgresObjectQueueStore)
        queue._transaction=lambda *args:connection()
        queue.close=lambda:calls.append("forbidden-shared-close")
        storage=MinioObjectStorageAdapter(endpoint="target.invalid:9000",bucket="s44-target",access_key=secrets.token_hex(12),secret_key=secrets.token_hex(24),secure=False)
        instance=PhysicalProbeInstance("database-a","bucket-a","qa-target",connection,storage,"us-east-1",
            ProbeFoundation("tenant-a","workspace-a","actor-a","0070","a"*64),"provision-a",False,False)
        request=ProbeRequest("source-a","target-a","database-a","bucket-a","owner-a")
        class Probe(TrustedPhysicalProbe):
            def observe(self,*args):raise AssertionError("no network during facade construction")
        destination=RegisteredDestination("generation-a","destination-a",1,"격리","2099-01-01T00:00:00Z",context.tenant_id,context.workspace_id,context.actor_id,
            "a"*64,DatabaseIdentity("10001",222).fingerprint(),"b"*64,"owner-a",datetime.now(timezone.utc))
        entry=descriptor_type(instance,queue,request,"c"*64)
        factory=factory_type({("generation-a","destination-a",1):entry},Probe())
        target,digest=factory(context,destination)
        self.assertEqual((target.region,digest),("us-east-1","c"*64))
        self.assertIs(factory(context,destination)[0],target)
        self.assertEqual(calls,[])
        with self.assertRaises(RecoveryError):factory(context,replace(destination,target_database_fingerprint="f"*64))
        with self.assertRaises(RecoveryError):factory_type({("generation-a","destination-a",1):replace(entry,instance=replace(instance,region=None))},Probe())

    def test_r4_registered_preview_off_and_exact_persistence_replay_no_fixture_rows(self):
        from test_recovery_lineage import Ledger
        from daon_user_api.recovery_lineage_capture import LineageCaptureScope
        from daon_user_api.recovery_lineage_restore import PostgresLineageTarget
        from daon_user_api.recovery_destinations import SignedDestinationRegistry,TrustedPhysicalProbe,RegisteredDestination
        from datetime import timedelta
        service=object.__new__(PostgresRecoveryService)
        method=getattr(service,"preview_registered_restore",None)
        self.assertTrue(callable(method),"R4 registered preview consumer missing")
        ledger=Ledger(); context=replace(ledger.context,session_id="session-a",device_id="device-a");ledger.context=context
        with self.assertRaises(RecoveryError) as off:
            method(context,"backup-a",destination_id="destination-a",destination_version=1,idempotency_key="preview-a",step_up_verified=True,recheck=lambda:None)
        self.assertEqual(off.exception.status,503)
        selection=ledger.resolve();scope=LineageCaptureScope.from_selection(context,selection,capture_id="capture-a")
        now=datetime.now(timezone.utc)
        destination=RegisteredDestination("generation-a","destination-a",1,"격리",(now+timedelta(hours=1)).isoformat(),context.tenant_id,
            context.workspace_id,context.actor_id,"1"*64,"2"*64,"3"*64,"owner-a",now)
        registry=object.__new__(SignedDestinationRegistry)
        registry.resolve_registered_destination=lambda *args:destination
        class Probe(TrustedPhysicalProbe):
            def observe(self,request): raise AssertionError("registry probe covered separately")
        target=object.__new__(PostgresLineageTarget); target.check=lambda *args,**kwargs:None
        service._destination_registry=registry;service._destination_probe=Probe();service._registered_target_factory=lambda *args:(target,"4"*64)
        service._lineage_capture_store=SimpleNamespace(key_version="v3-test",key_reference="memory-only")
        service._manifest_master_key=secrets.token_bytes(32);service._clock=lambda:now
        receipt={"synthetic-port-receipt":"not-real-capture-evidence"}
        service._registered_capture=lambda *args,**kwargs: (dict(manifest_digest="a"*64),scope,receipt,SimpleNamespace(metadata=selection.metadata()))
        requests={};previews={};log=[];audits=[];operations={}
        class Cursor:
            def __init__(self,row=None): self.row=row
            def fetchone(self): return self.row
        class Connection:
            info=ledger.info
            def cursor(self,*,row_factory): return ledger
            def execute(self,sql,params=()):
                log.append((sql,params))
                if sql.startswith("SELECT request_id,request_fingerprint"):
                    return Cursor(next((r for r in requests.values() if r["actor_id"]==params[0] and r["idempotency_key"]==params[1]),None))
                if sql.startswith("SELECT request_fingerprint,result FROM idempotency_records"):return Cursor(operations.get(tuple(params)))
                if sql.startswith("INSERT INTO idempotency_records"):
                    operations[(params[2],params[3],params[4])]=dict(request_fingerprint=params[5],result=params[6].obj);return Cursor()
                if sql.startswith("SELECT * FROM public.registered_restore_target_reservations"):return Cursor()
                if sql.startswith("INSERT INTO public.restore_requests"):
                    requests[params[2]]=dict(tenant_id=params[0],workspace_id=params[1],request_id=params[2],backup_id=params[3],actor_id=params[4],
                        state="requested",version=1,idempotency_key=params[8],request_fingerprint=params[9]);return Cursor()
                if sql.startswith("INSERT INTO public.registered_restore_previews"):
                    columns=sql.split("(",1)[1].split(")",1)[0].split(",")
                    previews[params[2]]=dict(zip(columns,params,strict=True));return Cursor()
                if sql.startswith("UPDATE public.restore_requests"):
                    requests[params[-1]].update(state="preview_ready",version=2);return Cursor()
                if sql.startswith("SELECT r.*") and "registered_restore_previews" in sql:
                    row=requests.get(params[0]);return Cursor(None if row is None else {**row,**previews[row["request_id"]],"verification_digest":None})
                if sql.startswith(("INSERT INTO public.restore_request_locator","INSERT INTO public.registered_restore_target_reservations")): return Cursor()
                if sql=="SELECT state FROM public.identity_org_tenant_memberships WHERE tenant_id=%s AND user_id=%s": return ledger.execute(sql,params)
                raise AssertionError("UNEXPECTED_REGISTERED_SQL")
        @contextmanager
        def transaction(*args): yield Connection()
        service._transaction=transaction
        service._append_audit=lambda *args,**kwargs:audits.append(args[2]) or "audit-a"
        checks=[]
        kwargs=dict(destination_id="destination-a",destination_version=1,idempotency_key="preview-a",step_up_verified=True,recheck=lambda:checks.append(True))
        view=method(context,"backup-a",**kwargs)
        self.assertEqual((view.state,view.version,view.preview.included_root_count),("preview_ready",2,1))
        self.assertEqual(view.preview.destination,destination.summary())
        saved=previews[view.request_id]
        self.assertEqual(saved["retention_watermark"],scope.retention_watermark)
        self.assertEqual(saved["source_policy_version"],scope.policy_version)
        decoded=service._open_registered_preview(saved,bytes(saved["encrypted_preview_receipt"]))
        self.assertNotEqual(decoded["current_retention_watermark"],scope.retention_watermark)
        before=len(log);self.assertEqual(method(context,"backup-a",**kwargs),view)
        self.assertEqual(audits,["restore.previewed"])
        self.assertFalse(any(sql.startswith(("INSERT","UPDATE")) for sql,_ in log[before:]))
        self.assertFalse(any("INSERT INTO restore_previews" in sql for sql,_ in log))
        with self.assertRaises(RecoveryError): method(context,"backup-a",**{**kwargs,"destination_version":2})
        self.assertGreater(len(checks),3)

    def test_r4_current_decision_reads_actual_bindings_retention_and_never_rewrites_archive(self):
        from test_recovery_lineage import Ledger
        from daon_user_api.recovery_lineage_capture import LineageCaptureScope
        ledger=Ledger(); selected=ledger.resolve()
        scope=LineageCaptureScope.from_selection(ledger.context,selected,capture_id="capture-a")
        class Connection:
            info=ledger.info
            def cursor(self,*,row_factory): return ledger
        service=object.__new__(PostgresRecoveryService)
        method=getattr(service,"_registered_current_decision",None)
        self.assertTrue(callable(method),"R4 actual current retention decision missing")
        first=method(Connection(),ledger.context,scope,selected.metadata())
        self.assertEqual(first["included_roots"],["root-version"])
        self.assertEqual(first["excluded_roots"],[])
        original=scope.retention_watermark
        ledger.tables["legal_holds"].append(dict(tenant_id=ledger.context.tenant_id,workspace_id=ledger.context.workspace_id,
            hold_id="hold-a",source_id="source-a",state="released",version=2,policy_version="policy-new"))
        changed=method(Connection(),ledger.context,scope,selected.metadata())
        self.assertNotEqual(changed["current_retention_watermark"],first["current_retention_watermark"])
        self.assertEqual(scope.retention_watermark,original)
        ledger.tables["legal_holds"][0]["state"]="active"
        with self.assertRaises(RecoveryError) as held: method(Connection(),ledger.context,scope,selected.metadata())
        self.assertEqual(held.exception.status,409)  # 全root除外から準備成功を作らない。
        ledger.tables["legal_holds"].clear(); ledger.unbound.add("root-version")
        with self.assertRaises(RecoveryError): method(Connection(),ledger.context,scope,selected.metadata())
        ledger.unbound.clear(); ledger.revoked.add("prior-version")
        with self.assertRaises(RecoveryError): method(Connection(),ledger.context,scope,selected.metadata())

    def test_r4_versioned_preview_receipt_binds_archive_current_actor_registry_and_sets(self):
        import json
        service=object.__new__(PostgresRecoveryService)
        service._manifest_master_key=secrets.token_bytes(32)
        service._lineage_capture_store=SimpleNamespace(key_version="key-v3",key_reference="memory-only")
        row=dict(tenant_id="tenant-a",workspace_id="workspace-a",actor_id="manager-a",request_id="request-a",backup_id="backup-a",
            preview_version=1,registry_generation_id="generation-a",destination_id="destination-a",destination_version=1,
            source_manifest_digest="a"*64,closure_fingerprint="b"*64,source_policy_version="policy-original",retention_watermark="c"*64,
            included_roots_fingerprint=hashlib.sha256(b'["root-a"]').hexdigest(),excluded_roots_fingerprint=hashlib.sha256(b'[]').hexdigest(),
            expires_at=datetime(2030,1,1,tzinfo=timezone.utc))
        payload=dict(schema_version=1,current_retention_watermark="d"*64,current_policy_version="tenant-1:workspace-1",current_authorization_fingerprint="9"*64,runtime_policy_version="policy-current",
            included_roots=["root-a"],excluded_roots=[],exclusion_reasons=[],session_id="session-a",device_id="device-a",request_version=2,
            destination_summary=dict(destination_id="destination-a",version=1,display_name="격리",expires_at="2030-01-01T00:00:00+00:00"),
            catalog_digest="e"*64,created_at="2026-10-19T00:00:00+00:00")
        seal=getattr(service,"_seal_registered_preview",None)
        self.assertTrue(callable(seal),"R4 AEAD registered preview receipt missing")
        envelope=seal(row,payload)
        self.assertTrue(envelope.startswith(b"DRP1"))
        self.assertNotIn(b"manager-a",envelope)
        self.assertEqual(service._open_registered_preview(row,envelope),payload)
        from dataclasses import asdict
        view=service._registered_preview_view(row,payload)
        public=asdict(view)
        self.assertEqual(set(public),{"kind","version","created_at","expires_at","destination","source_manifest_digest",
            "closure_fingerprint","included_root_count","excluded_root_count","exclusion_counts"})
        self.assertEqual(public["included_root_count"],1)
        self.assertEqual(public["destination"],payload["destination_summary"])
        self.assertNotIn("current_retention_watermark",public)
        self.assertNotIn("root-a",json.dumps(public,default=str))
        for key,value in (("actor_id","other-manager"),("request_id","other-request"),("registry_generation_id","other-generation"),
                ("destination_version",2),("source_manifest_digest","f"*64),("closure_fingerprint","f"*64),("retention_watermark","f"*64)):
            with self.subTest(binding=key),self.assertRaises(RecoveryError): service._open_registered_preview({**row,key:value},envelope)
        for name,value in (("current_retention_watermark","invalid"),("included_roots",[]),("extra","secret-canary"),
                ("exclusion_reasons",[["root-a","unknown"]]),("schema_version",True)):
            with self.subTest(payload=name),self.assertRaises(RecoveryError): seal(row,{**payload,name:value})
        with self.assertRaises(RecoveryError): service._open_registered_preview(row,envelope[:-1]+bytes([envelope[-1]^1]))
        service._manifest_master_key=secrets.token_bytes(32)
        with self.assertRaises(RecoveryError): service._open_registered_preview(row,envelope)

    def test_r4_current_archive_authorization_uses_real_current_actor_and_all_dependencies(self):
        from test_recovery_lineage import Ledger
        from daon_user_api.recovery_postgres import PostgresRecoveryService
        ledger=Ledger(); ledger.auth.auth_role="organization_admin"
        context=replace(ledger.context,actor_id="current-manager"); ledger.context=context
        scope=SimpleNamespace(tenant_id=context.tenant_id,workspace_id=context.workspace_id,actor_id="original-author",
            notebook_id="notebook-a",root_source_version_ids=("root-version",),dependency_source_version_ids=("prior-version",))
        method=getattr(PostgresRecoveryService,"_registered_authorization",None)
        self.assertTrue(callable(method),"R4 current principal/archive provenance boundary missing")
        class DictRowConnection:
            info=ledger.info
            def cursor(self,*,row_factory):
                from psycopg.rows import tuple_row
                self_expected=row_factory is tuple_row
                if not self_expected: raise AssertionError("existing helpers require tuple cursor")
                return ledger
            def execute(self,sql,params=()):
                # 실제 recovery pool은 dict_row. helper가 직접 [0]을 쓰면 실패해야 한다.
                return SimpleNamespace(fetchone=lambda:{"state":"active"})
        connection=DictRowConnection()
        method(connection,context,scope)
        queries=[params for sql,params in ledger.log if "FROM identity_auth_source_access" in sql]
        self.assertEqual({p[-1] for p in queries},{"root-version","prior-version"})
        self.assertTrue(all(p[2]=="current-manager" for p in queries))
        ledger.revoked.add("prior-version")
        with self.assertRaises(RecoveryError): method(connection,context,scope)
        ledger.revoked.clear(); ledger.workflow_state="suspended"
        with self.assertRaises(RecoveryError): method(connection,context,scope)
        self.assertEqual(scope.actor_id,"original-author")

    def test_r4_unconfigured_registry_is_explicit_off_without_database_or_fixture_fallback(self):
        from daon_user_api.recovery_postgres import PostgresRecoveryService
        service=object.__new__(PostgresRecoveryService)
        method=getattr(service,"list_restore_destinations",None)
        self.assertTrue(callable(method),"R4 registered destination consumer missing")
        context=RecoveryContext("tenant-a","workspace-a","actor-a","trace-a","policy-a",True)
        with self.assertRaises(RecoveryError) as caught: method(context,"backup-a",recheck=lambda:None)
        self.assertEqual((caught.exception.code,caught.exception.status),("RESOURCE_UNAVAILABLE",503))
from daon_user_api.runtime import RuntimeSettings, build_dependencies
from daon_user_api.recovery_postgres import (
    MinioRecoveryStorageAdapter,
    PostgresRecoveryService,
)


class RecoveryLineageConsumerTests(unittest.TestCase):
    """SQL commit 포트만 대역이다. v3 파일/AEAD/source bytes는 실제로 실행한다."""
    def setUp(self):
        from test_recovery_lineage import Ledger, recanonical
        from daon_user_api.recovery_lineage_capture import FileLineageCaptureStore
        parent = Path(os.environ.get("TMP", ""))
        self.assertIn(parent, {Path("D:/tmp/daon-user-s44-d1-eoul2-20261019"), Path("D:/tmp/daon-user-s44-d1-main-20261019")})
        self.tmp = tempfile.TemporaryDirectory(prefix="r1c-", dir=parent); self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name); archive = self.root / "archive"; archive.mkdir()
        self.ledger = Ledger(); self.context = self.ledger.context
        self.content = {"object-a": b"prior-source" * 20, "object-b": b"root-source" * 40}
        for row in self.ledger.tables["object_records"]:
            raw = self.content[row["object_id"]]; row.update(byte_size=len(raw), digest_sha256=hashlib.sha256(raw).hexdigest())
            (self.root / row["object_id"]).write_bytes(raw)
        for row in self.ledger.tables["source_versions"]:
            obj = next(x for x in self.ledger.tables["object_records"] if x["object_id"] == row["object_id"])
            recanonical(row, dict(object_id=obj["object_id"], byte_size=obj["byte_size"], content_digest_sha256=obj["digest_sha256"]))
        self.selection = self.ledger.resolve(); self.key = secrets.token_bytes(32)
        service = self.service = object.__new__(PostgresRecoveryService)
        service._key = hashlib.sha256(self.key).digest(); service._manifest_master_key = self.key
        service._capture_store = None; service._notification_inbox_enabled = False
        service._lineage_capture_store = FileLineageCaptureStore(archive, master_key=self.key, key_version="v3-test", key_reference="memory-only")
        service._clock = lambda: datetime.now(timezone.utc)
        self.opened, self.sql, self.committed, self.mode = [], [], {}, None
        def source(context, item):
            self.assertEqual(context, self.context); self.opened.append(item.object_id)
            return (self.root / item.object_id).open("rb")
        service._storage = SimpleNamespace(open_source=source)
        @contextmanager
        def transaction(tenant, workspace, actor):
            self.assertEqual((tenant, workspace, actor), (self.context.tenant_id,self.context.workspace_id,self.context.actor_id))
            working = copy.deepcopy(self.committed)
            class Connection:
                def execute(inner, sql, params=()):
                    self.sql.append((sql, params)); row = None; rows = []
                    if sql.startswith("SELECT backup_id, request_fingerprint"):
                        row = next((r for r in working.values() if r.get("actor_id") == params[0] and r.get("idempotency_key") == params[1]), None)
                    elif sql.startswith("INSERT INTO backup_records"):
                        working[params[2]] = dict(tenant_id=params[0],workspace_id=params[1],backup_id=params[2],actor_id=params[3],trigger_type=params[4],
                            schema_revision=params[5],retention_watermark=params[6],policy_version=params[7],trace_id=params[8],idempotency_key=params[10],request_fingerprint=params[11],
                            created_at=params[12],verified_at=None,state="queued",version=1)
                    elif sql.startswith("INSERT INTO backup_manifests"):
                        working[params[2]].update(manifest_version=params[3],manifest_digest=params[4],encrypted_inventory=params[5],object_count=params[6])
                    elif sql.startswith("UPDATE backup_records"):
                        if "state='ready'" in sql:
                            working[params[1]].update(state="ready",verified_at=params[0]); working[params[1]]["version"]+=1
                        else: working[params[1]]["state"]=params[0]; working[params[1]]["version"]+=1
                    elif sql.startswith("SELECT b.*"):
                        rows = list(working.values()) if not params else [r for r in working.values() if r["backup_id"] == params[0]]
                        row = rows[0] if rows else None
                    elif sql.startswith("SELECT metadata->>"): row = None
                    return SimpleNamespace(fetchone=lambda: row, fetchall=lambda: rows)
            yield Connection()
            if self.mode == "rollback": raise RecoveryError("RESOURCE_UNAVAILABLE", 503)
            self.committed.clear(); self.committed.update(working)
            if self.mode == "response-lost": raise RecoveryError("RESOURCE_UNAVAILABLE", 503)
        service._transaction = transaction

    def create(self, **kwargs):
        return self.service.create_lineage_backup(self.context, self.selection, idempotency_key="lineage-request",
            recheck=self.ledger.resolve, **kwargs)

    def test_r1c_v3_capture_manifest_list_get_restart_replay_and_kind_conflict(self):
        method = getattr(self.service, "create_lineage_backup", None)
        self.assertTrue(callable(method), "실제 v3 보존/원장 consumer 필요")
        view = self.create()
        self.assertEqual((view.state,view.object_count), ("ready",2))
        row = self.committed[view.backup_id]
        self.assertEqual(row["manifest_version"],3); self.assertTrue(bytes(row["encrypted_inventory"]).startswith(b"DRM3"))
        self.assertEqual(self.service.get_backup(self.context,view.backup_id),view)
        self.assertEqual(self.service.list_backups(self.context),(view,))
        for identity in self.content: (self.root/identity).unlink()
        self.assertEqual(self.create(),view)
        self.assertEqual(self.opened,["object-a","object-b"])
        inserts=[sql for sql,_ in self.sql if sql.startswith("INSERT INTO backup_records")]
        self.assertEqual(len(inserts),1)
        with self.assertRaises(RecoveryError): self.service._capture_from_row({**row,"manifest_version":2})
        self.ledger.tables["notebook_metadata_versions"][0]["title"]="current metadata changed"
        self.selection=self.ledger.resolve()
        with self.assertRaises(RecoveryError) as caught: self.create()
        self.assertEqual(caught.exception.status,409)
        with self.assertRaises(RecoveryError) as caught:
            with self.service._transaction(self.context.tenant_id,self.context.workspace_id,self.context.actor_id) as conn:
                self.service._require_independent_capture(conn,self.context,view.backup_id,preview=True)
        self.assertEqual(caught.exception.status,503)

    def test_r1c_final_revocation_and_commit_uncertainty_preserve_exact_artifact(self):
        self.assertTrue(callable(getattr(self.service,"create_lineage_backup",None)))
        store=self.service._lineage_capture_store; original=store.verify; revoked=[]
        def verify(*args,**kwargs):
            value=original(*args,**kwargs); revoked.append(True); return value
        def recheck():
            if revoked: raise RecoveryError("CURRENT_ACCESS_DENIED",403)
            return self.ledger.resolve()
        with patch.object(store,"verify",side_effect=verify), self.assertRaises(RecoveryError) as caught:
            self.service.create_lineage_backup(self.context,self.selection,idempotency_key="revoked-request",recheck=recheck)
        self.assertEqual(caught.exception.status,403); self.assertEqual(self.committed,{})
        folder=next(p for p in store.root.iterdir() if p.is_dir())
        self.assertEqual({p.name for p in folder.iterdir()},{"owner.bin"})
        self.mode="response-lost"
        with self.assertRaises(RecoveryError) as caught: self.create()
        self.assertEqual(caught.exception.status,503); self.assertEqual(len(self.committed),1)
        opened=list(self.opened); self.mode=None
        from daon_user_api.recovery_lineage_capture import FileLineageCaptureStore
        self.service._lineage_capture_store=FileLineageCaptureStore(store.root,master_key=self.key,key_version="v3-test",key_reference="memory-only")
        self.assertEqual(self.create().state,"ready"); self.assertEqual(self.opened,opened)


def _environment_or_file(name: str) -> str | None:
    value = os.environ.get(name)
    if value:
        return value
    file_name = os.environ.get(f"{name}_FILE")
    if not file_name:
        return None
    return Path(file_name).read_text(encoding="utf-8").strip()


def _environment_or_bytes(name: str) -> bytes | None:
    value = os.environ.get(name)
    if value:
        return value.encode("utf-8")
    file_name = os.environ.get(f"{name}_FILE")
    if not file_name:
        return None
    return Path(file_name).read_bytes()


DSN = _environment_or_file("DAON_RECOVERY_INTEGRATION_DSN")
ENDPOINT = os.environ.get("DAON_RECOVERY_INTEGRATION_OBJECT_ENDPOINT")
BUCKET = os.environ.get("DAON_RECOVERY_INTEGRATION_BUCKET")
ACCESS_KEY = _environment_or_file("DAON_RECOVERY_INTEGRATION_ACCESS_KEY")
SECRET_KEY = _environment_or_file("DAON_RECOVERY_INTEGRATION_SECRET_KEY")
MANIFEST_KEY = _environment_or_bytes("DAON_RECOVERY_INTEGRATION_MANIFEST_KEY")
CONFIGURED = all((DSN, ENDPOINT, BUCKET, ACCESS_KEY, SECRET_KEY, MANIFEST_KEY))


class RegisteredExecuteTests(unittest.TestCase):
    """Windows 실제 v3/AEAD→실R3 구현+SQL/Object/process 경계 대역. 실PG/MinIO 아님."""
    def setUp(self):
        import daon_user_api.recovery_lineage_restore as r3
        from test_recovery_lineage_restore import RestoreFixture
        from daon_user_api.recovery_lineage_capture import encode_manifest
        from daon_user_api.recovery_destinations import SignedDestinationRegistry,TrustedPhysicalProbe
        self.case=case=RestoreFixture(self,r3)
        case.context=replace(case.context,session_id="session-current",device_id="device-current")
        case.capture_case.ledger.context=case.context
        service=self.service=object.__new__(PostgresRecoveryService)
        # PostgreSQL advisory SQL/소유 세션 경계 대역. 실제 PG 잠금 경합 증거 아님.
        import threading
        from psycopg.pq import TransactionStatus
        backend={"keys":{},"mutex":threading.Lock(),"next_pid":100,"sessions":[]}
        class GuardPool:
            closed=False
            def clone(self):return GuardPool()
            @contextmanager
            def connection(self,**kwargs):
                with backend["mutex"]:
                    backend["next_pid"]+=1;pid=backend["next_pid"]
                class Session:
                    info=SimpleNamespace(transaction_status=TransactionStatus.IDLE)
                    @contextmanager
                    def transaction(inner):
                        inner.info.transaction_status=TransactionStatus.INTRANS
                        try:yield
                        finally:
                            with backend["mutex"]:
                                for key,owner in list(backend["keys"].items()):
                                    if owner==pid:del backend["keys"][key]
                            inner.info.transaction_status=TransactionStatus.IDLE
                    def execute(inner,sql,params=()):
                        if sql=="SET LOCAL ROLE daon_app":return SimpleNamespace(fetchone=lambda:None)
                        if sql=="SELECT pg_backend_pid() AS owner_pid":return SimpleNamespace(fetchone=lambda:dict(owner_pid=pid))
                        if sql=="SELECT pg_try_advisory_xact_lock(%s) AS owned,pg_backend_pid() AS owner_pid":
                            self_test=fixture
                            self_test.assertEqual(len(params),1);self_test.assertIs(type(params[0]),int)
                            with backend["mutex"]:
                                owned=params[0] not in backend["keys"]
                                if owned:backend["keys"][params[0]]=pid
                            return SimpleNamespace(fetchone=lambda:dict(owned=owned,owner_pid=pid))
                        raise AssertionError("UNEXPECTED_EXECUTION_GUARD_SQL")
                session=Session();backend["sessions"].append(session)
                yield session
        service._pool=GuardPool();self.guard_backend=backend
        service._key=hashlib.sha256(case.capture_case.key).digest();service._manifest_master_key=case.capture_case.key
        service._clock=lambda:case.now;service._notification_inbox_enabled=True;service._lineage_capture_store=case.store
        token=service._capture_identity(case.context.tenant_id,case.context.workspace_id,case.context.actor_id,"backup-key")
        case.scope=replace(case.scope,capture_id="capture-"+token)
        case.receipt=case.store.capture(case.scope,case.capture_case.selection,lambda item:io.BytesIO(case.content[item.object_id]),
            closure_fingerprint=case.scope.closure_fingerprint,include_prior_versions=True,recheck=case.capture_case.ledger.resolve,owner_id="owned-r4")
        envelope=encode_manifest(case.scope,case.receipt,master_key=case.capture_case.key,key_version=case.store.key_version,key_reference=case.store.key_reference)
        case.binding=replace(case.binding,backup_id="backup-"+token,receipt_digest=r3._hash(case.receipt),manifest_digest=hashlib.sha256(envelope).hexdigest())
        self.backup=dict(tenant_id=case.context.tenant_id,workspace_id=case.context.workspace_id,actor_id=case.scope.actor_id,
            backup_id=case.binding.backup_id,idempotency_key="backup-key",schema_revision=case.scope.schema_revision,
            retention_watermark=case.scope.retention_watermark,policy_version=case.scope.policy_version,request_fingerprint=case.scope.closure_fingerprint,
            encrypted_inventory=envelope,manifest_digest=case.binding.manifest_digest,manifest_version=3,object_count=2,state="ready")
        self.parent=dict(tenant_id=case.context.tenant_id,workspace_id=case.context.workspace_id,actor_id=case.context.actor_id,
            request_id="request-a",backup_id=case.binding.backup_id,state="preview_ready",version=2,verification_digest=None)
        self.events=[];self.verifications=[];self.operations={};self.lost_final=False;self.notified=[];self.failed_notified=[];self.current_hook=lambda:None
        self.sql=[];self.preview_history=[]
        ledger=case.capture_case.ledger;fixture=self
        class Cursor:
            def __init__(self,row=None,rows=(),count=1):self.row,self.rows,self.rowcount=row,list(rows),count
            def fetchone(self):return self.row
            def fetchall(self):return self.rows
        class Connection:
            info=ledger.info
            def cursor(self,*,row_factory):return ledger
            def execute(self,sql,params=()):
                fixture.sql.append((sql,params))
                if sql.startswith("SELECT b.*"):return Cursor(fixture.backup)
                if sql.startswith("SELECT r.*") and "registered_restore_previews" in sql:
                    return Cursor({**fixture.parent,**fixture.preview,**fixture.reservation})
                if sql.startswith("SELECT * FROM public.restore_requests"):return Cursor(dict(fixture.parent))
                if sql.startswith("SELECT request_id,request_fingerprint"):
                    return Cursor(dict(fixture.parent) if params==(fixture.parent["actor_id"],fixture.parent["idempotency_key"]) else None)
                if sql.startswith("SELECT * FROM public.registered_restore_target_reservations"):
                    fixture.assertEqual(params,("generation-a","destination-a"));return Cursor(dict(fixture.reservation))
                if sql.startswith("SELECT EXISTS(SELECT 1 FROM public.registered_restore_phase_receipts"):
                    fixture.assertEqual(params,(case.context.tenant_id,case.context.workspace_id,"request-a"))
                    return Cursor(dict(present=bool(case.journal.rows)))
                if sql=="SELECT pg_try_advisory_xact_lock(%s) AS owned":
                    with backend["mutex"]:
                        owned=params[0] not in backend["keys"]
                        if owned:backend["keys"][params[0]]=-1
                    return Cursor(dict(owned=owned))
                if sql.startswith("INSERT INTO public.restore_requests"):
                    fixture.parent=dict(tenant_id=params[0],workspace_id=params[1],request_id=params[2],backup_id=params[3],actor_id=params[4],
                        state="requested",version=1,idempotency_key=params[8],request_fingerprint=params[9],verification_digest=None)
                    return Cursor()
                if sql.startswith("INSERT INTO public.restore_request_locator"):return Cursor()
                if sql.startswith("INSERT INTO public.registered_restore_target_reservations"):
                    raise psycopg.errors.UniqueViolation("synthetic existing global reservation")
                if sql.startswith("INSERT INTO public.registered_restore_previews"):
                    columns=sql.split("(",1)[1].split(")",1)[0].split(",")
                    row=dict(zip(columns,params,strict=True))
                    fixture.assertEqual(row["request_id"],"request-a")
                    fixture.assertEqual(row["preview_version"],fixture.preview["preview_version"]+1)
                    fixture.assertEqual(case.journal.rows,[])
                    fixture.preview=row;fixture.preview_history.append(copy.deepcopy(row));return Cursor()
                if sql.startswith("SELECT request_fingerprint,result FROM idempotency_records"):
                    return Cursor(fixture.operations.get(tuple(params)))
                if sql.startswith("UPDATE public.restore_requests"):
                    fixture.parent["state"]=params[0];fixture.parent["version"]+=1;return Cursor()
                if sql.startswith("INSERT INTO restore_verifications"):
                    fixture.verifications.append(params);fixture.parent["verification_digest"]=params[4];return Cursor()
                if sql.startswith("INSERT INTO idempotency_records"):
                    fixture.operations[(params[2],params[3],params[4])]=dict(request_fingerprint=params[5],result=params[6].obj);return Cursor()
                if sql.startswith("SELECT metadata->>"):
                    return Cursor(None if not fixture.events else dict(event_hash=fixture.events[-1][10].obj["event_hash"]))
                if sql.startswith("INSERT INTO audit_events"):fixture.events.append(params);return Cursor()
                raise AssertionError("UNEXPECTED_R4_EXECUTION_SQL")
        self.connection=Connection()
        @contextmanager
        def transaction(*args):
            before=copy.deepcopy((self.parent,self.events,self.verifications,self.operations,self.notified,self.failed_notified,self.preview_history,getattr(self,"preview",None)))
            try:
                yield self.connection
                if self.lost_final and self.parent["state"]=="completed": raise RecoveryError("RESOURCE_UNAVAILABLE",503)
            except BaseException:
                self.parent,self.events,self.verifications,self.operations,self.notified,self.failed_notified,self.preview_history,self.preview=before
                raise
            finally:
                with backend["mutex"]:
                    for key,owner in list(backend["keys"].items()):
                        if owner==-1:del backend["keys"][key]
        service._transaction=transaction
        decision=service._registered_current_decision(self.connection,case.context,case.scope,case.metadata)
        self.preview=dict(tenant_id=case.context.tenant_id,workspace_id=case.context.workspace_id,actor_id=case.context.actor_id,
            request_id="request-a",backup_id=case.binding.backup_id,preview_version=1,registry_generation_id="generation-a",destination_id="destination-a",
            destination_version=1,source_manifest_digest=case.binding.manifest_digest,closure_fingerprint=case.scope.closure_fingerprint,
            retention_watermark=case.scope.retention_watermark,source_policy_version=case.scope.policy_version,
            included_roots_fingerprint=r3._hash(decision["included_roots"]),excluded_roots_fingerprint=r3._hash(decision["excluded_roots"]),expires_at=case.binding.preview_expires_at)
        payload=dict(schema_version=1,**decision,runtime_policy_version=case.context.policy_version,session_id=case.context.session_id,device_id=case.context.device_id,request_version=2,
            destination_summary=case.destination.summary(),catalog_digest=case.binding.catalog_digest,created_at=case.now.isoformat())
        self.preview["encrypted_preview_receipt"]=service._seal_registered_preview(self.preview,payload)
        self.preview_history=[copy.deepcopy(self.preview)]
        self.parent.update(idempotency_key="preview-original",request_fingerprint=recovery_postgres._fingerprint((
            "registered-lineage-preview-v1",case.binding.backup_id,case.binding.manifest_digest,case.scope.closure_fingerprint,
            case.context.tenant_id,case.context.workspace_id,case.context.actor_id,case.context.session_id,case.context.device_id,case.context.policy_version,
            "generation-a","destination-a",1,case.destination.physical_receipt_digest,decision)))
        self.reservation=dict(registry_generation_id="generation-a",destination_id="destination-a",tenant_id=case.context.tenant_id,
            workspace_id=case.context.workspace_id,actor_id=case.context.actor_id,request_id="request-a",destination_version=1,
            backup_id=case.binding.backup_id,manifest_version=3,source_manifest_digest=case.binding.manifest_digest,
            owner_operation_id="owner-a",physical_receipt_digest=case.destination.physical_receipt_digest,
            target_database_fingerprint=case.destination.target_database_fingerprint,target_bucket_fingerprint=case.destination.target_bucket_fingerprint)
        registry=object.__new__(SignedDestinationRegistry);registry._source="source-a"
        entry=dict(destination_id="destination-a",version=1,display_name="격리",capture_kind="selected-source-lineage-v3",source_instance_ref="source-a",
            authorized_tenant_id=case.context.tenant_id,authorized_workspace_id=case.context.workspace_id,authorized_actor_id=case.context.actor_id,
            target_instance_ref="target-a",database_ref="database-a",bucket_ref="bucket-a",physical_receipt_digest=case.destination.physical_receipt_digest,
            owner_operation_id="owner-a",created_at=(case.now-timedelta(minutes=1)).isoformat(),expires_at=case.destination.expires_at,
            application_enabled=False,outbound_enabled=False)
        registry._read=lambda:("generation-a",[entry])  # registry 서명/실파일은 별도 R2 시험. 여기서는 공급 포트만 대역.
        registry.resolve_registered_destination=lambda *args:case.destination
        class Probe(TrustedPhysicalProbe):
            def observe(self,request):return case.observation
        service._destination_registry=registry;service._destination_probe=Probe()
        service._registered_target_factory=lambda *args:(case.target,case.binding.catalog_digest)
        def journal(context,binding):case.binding=binding;return case.journal
        service._registered_phase_journal=journal
        self.notify=patch("daon_user_api.notification_recovery.enqueue_restore_completed_event",side_effect=lambda *args,**kwargs:self.notified.append(kwargs))
        self.notify.start();self.addCleanup(self.notify.stop)
        terminal=patch("daon_user_api.notification_recovery.enqueue_terminal_recovery_event",side_effect=lambda *args,**kwargs:self.failed_notified.append(kwargs))
        terminal.start();self.addCleanup(terminal.stop)

    def execute(self,**kwargs):
        return self.service.execute_registered_restore(self.case.context,"request-a",expected_version=2,preview_version=1,
            idempotency_key="execute-r4",step_up_verified=True,recheck=self.current_hook,**kwargs)

    def preview_again(self,key="preview-next"):
        return self.service.preview_registered_restore(self.case.context,self.case.binding.backup_id,
            destination_id="destination-a",destination_version=1,idempotency_key=key,step_up_verified=True,recheck=self.current_hook)

    def test_r4_step_up_protected_preview_requires_actual_current_source_acl_and_workflow(self):
        method=self.service.registered_restore_step_up_preview
        with patch.object(self.service._destination_probe,"observe",side_effect=AssertionError("NO_TARGET_PROBE_AT_ISSUE")):
            view=method(self.case.context,"request-a",recheck=self.current_hook)
            self.assertEqual((view.request_id,view.preview.version),("request-a",1))
            ledger=self.case.capture_case.ledger
            ledger.revoked.add("prior-version")
            with self.assertRaises(RecoveryError) as denied:method(self.case.context,"request-a",recheck=self.current_hook)
            self.assertEqual(denied.exception.status,403)
            ledger.revoked.clear();ledger.auth.auth_role="organization_admin";ledger.workflow_state="suspended"
            with self.assertRaises(RecoveryError) as suspended:method(self.case.context,"request-a",recheck=self.current_hook)
            self.assertEqual(suspended.exception.status,403)
            ledger.workflow_state="active"
            with self.assertRaises(RecoveryError) as foreign:
                method(replace(self.case.context,actor_id="another-manager"),"request-a",recheck=self.current_hook)
            self.assertEqual(foreign.exception.status,403)
        self.assertEqual(self.case.database.writes,0);self.assertEqual(self.events,[])

    def test_r4_step_up_rechecks_latest_preview_after_capture_read(self):
        capture=self.service._registered_capture
        payload=self.service._open_registered_preview(self.preview,self.preview["encrypted_preview_receipt"])
        def changed(*args,**kwargs):
            result=capture(*args,**kwargs)
            self.preview["preview_version"]+=1
            self.preview["encrypted_preview_receipt"]=self.service._seal_registered_preview(self.preview,payload)
            return result
        with patch.object(self.service,"_registered_capture",side_effect=changed),self.assertRaises(RecoveryError) as stale:
            self.service.registered_restore_step_up_preview(self.case.context,"request-a",recheck=self.current_hook)
        self.assertEqual(stale.exception.status,409);self.assertEqual(self.case.database.writes,0)

    def test_r4_new_confirmation_same_reserved_request_next_preview_then_execute(self):
        original_parent=copy.deepcopy(self.parent);original_preview=copy.deepcopy(self.preview);original_reservation=copy.deepcopy(self.reservation)
        ledger=self.case.capture_case.ledger;read=ledger.auth.execute
        def policy(sql,params=()):
            cursor=read(sql,params);row=cursor.fetchone()
            if isinstance(row,dict) and "FROM identity_auth_tenant_policies" in sql:row={**row,"version":2}
            return SimpleNamespace(fetchone=lambda:row)
        ledger.auth.execute=policy
        with self.assertRaises(RecoveryError) as stale:self.execute()
        self.assertEqual(stale.exception.status,409);self.assertEqual(self.case.journal.rows,[])
        self.assertEqual(self.parent,original_parent)
        with self.assertRaises(RecoveryError) as old_key:self.preview_again("preview-original")
        self.assertEqual(old_key.exception.status,409)
        fresh=self.preview_again()
        self.assertEqual((fresh.request_id,fresh.version,fresh.preview.version),("request-a",2,2))
        self.assertEqual(self.reservation,original_reservation);self.assertEqual(self.preview_history[0],original_preview)
        self.assertEqual(self.parent,original_parent,"0006 부모 불변, 같은 state UPDATE0")
        payload=self.service._open_registered_preview(self.preview,self.preview["encrypted_preview_receipt"])
        self.assertEqual(payload["current_policy_version"],"tenant-2:workspace-1")
        with self.assertRaises(RecoveryError) as old_body:self.execute()
        self.assertEqual(old_body.exception.status,409)
        self.assertEqual(self.case.database.writes,0);self.assertEqual(self.case.journal.rows,[])
        with self.assertRaises(RecoveryError) as no_step:
            self.service.execute_registered_restore(self.case.context,"request-a",expected_version=2,preview_version=2,
                idempotency_key="execute-new",step_up_verified=False,recheck=self.current_hook)
        self.assertEqual(no_step.exception.status,403)
        self.assertEqual(self.service.get_restore_request(self.case.context,"request-a"),fresh)
        completed=self.service.execute_registered_restore(self.case.context,"request-a",expected_version=2,preview_version=2,
            idempotency_key="execute-new",step_up_verified=True,recheck=self.current_hook)
        self.assertEqual((completed.state,completed.preview.version),("completed",2))
        self.assertEqual(len(self.case.journal.rows),14);self.assertEqual(len(self.notified),1)
        self.assertEqual(self.preview_history[0],original_preview);self.assertEqual(self.reservation,original_reservation)

    def test_r4_confirmation_replay_binds_its_own_preview_never_substitutes_latest(self):
        second=self.preview_again()
        def persisted():
            events=[tuple(value.obj if isinstance(value,psycopg.types.json.Jsonb) else value for value in event) for event in self.events]
            return copy.deepcopy((self.preview_history,events,self.operations))
        before=persisted()
        self.assertEqual(self.preview_again(),second)
        self.assertEqual(persisted(),before)
        third=self.preview_again("preview-third")
        self.assertEqual(third.preview.version,3)
        with self.assertRaises(RecoveryError) as old_key:self.preview_again()
        self.assertEqual(old_key.exception.status,409)
        self.assertEqual(len(self.preview_history),3)
        with self.assertRaises(RecoveryError):self.preview_again("preview-original")

    def test_r4_confirmation_all_held_requires_release_then_explicit_new_key(self):
        ledger=self.case.capture_case.ledger
        ledger.tables["legal_holds"].append(dict(tenant_id=self.case.context.tenant_id,workspace_id=self.case.context.workspace_id,
            hold_id="new-hold",source_id="source-a",state="active",version=1,policy_version=self.case.context.policy_version))
        for action in (self.execute,self.preview_again):
            with self.assertRaises(RecoveryError) as held:action()
            self.assertEqual(held.exception.status,409)
        self.assertEqual(self.parent["state"],"preview_ready");self.assertEqual(self.case.journal.rows,[])
        ledger.tables["legal_holds"][0].update(state="released",version=2)
        self.assertEqual(self.preview_again().preview.version,2)
        with self.assertRaises(RecoveryError) as old_body:self.execute()
        self.assertEqual(old_body.exception.status,409)
        with self.assertRaises(RecoveryError) as wrong_etag:
            self.service.execute_registered_restore(self.case.context,"request-a",expected_version=1,preview_version=2,
                idempotency_key="execute-new",step_up_verified=True,recheck=self.current_hook)
        self.assertEqual(wrong_etag.exception.status,409)
        self.assertEqual(self.case.database.writes,0)

    def test_r4_confirmation_refuses_foreign_reservation_or_started_parent(self):
        reservation=copy.deepcopy(self.reservation)
        for field,value in (("actor_id","other-actor"),("backup_id","other-backup"),("tenant_id","other-tenant"),
                ("registry_generation_id","other-generation"),("destination_version",2),("physical_receipt_digest","f"*64)):
            with self.subTest(field=field):
                self.reservation={**reservation,field:value}
                with self.assertRaises(RecoveryError) as denied:self.preview_again()
                self.assertEqual(denied.exception.status,409)
                self.assertEqual(len(self.preview_history),1)
        self.reservation=reservation
        for state in ("authorized","restoring","verifying","completed","failed","cancelled"):
            with self.subTest(state=state):
                self.parent["state"]=state
                with self.assertRaises(RecoveryError) as denied:self.preview_again()
                self.assertEqual(denied.exception.status,409)
        self.parent["state"]="preview_ready"
        self.case.journal.rows.append(dict(sequence=1))
        with self.assertRaises(RecoveryError) as begun:self.preview_again()
        self.assertEqual(begun.exception.status,409)
        self.assertEqual(self.case.database.writes,0);self.assertEqual(len(self.preview_history),1)

    def test_r4_confirmation_cannot_enter_while_execute_owns_request(self):
        with self.service._registered_execution_guard(self.case.context,"request-a"):
            with self.assertRaises(RecoveryError) as busy:self.preview_again()
            self.assertEqual(busy.exception.status,409)
        self.assertEqual(self.parent["state"],"preview_ready");self.assertEqual(len(self.preview_history),1)

    def test_r4_confirmation_hidden_or_racing_global_reservation_conflict_rolls_back(self):
        # 다른 actor/RLS로 보이지 않거나 새 generation alias인 예약은 INSERT UNIQUE가 최종 차단한다.
        before=copy.deepcopy((self.parent,self.preview_history,self.operations,self.reservation))
        execute=self.connection.execute
        def unseen(sql,params=()):
            if sql.startswith("SELECT * FROM public.registered_restore_target_reservations"):
                return SimpleNamespace(fetchone=lambda:None)
            return execute(sql,params)
        with patch.object(self.connection,"execute",side_effect=unseen),self.assertRaises(RecoveryError) as conflict:
            self.preview_again()
        self.assertEqual((conflict.exception.status,conflict.exception.retryable),(409,False))
        self.assertEqual((self.parent,self.preview_history,self.operations,self.reservation),before)
        self.assertEqual(self.events,[]);self.assertEqual(self.case.database.writes,0)

    def test_r4_inflight_same_request_duplicate_cannot_fail_active_owner(self):
        import threading
        from concurrent.futures import ThreadPoolExecutor
        checkpoint,release=threading.Event(),threading.Event();owner=[]
        original_append=self.case.journal.append;original_objects=self.case.target.objects
        def append(*args,**kwargs):
            result=original_append(*args,**kwargs)
            if args[2:4]==(1,"succeeded") and threading.get_ident()==owner[0]:
                checkpoint.set()
                if not release.wait(5):raise AssertionError("TEST_OWNER_NOT_RELEASED")
            return result
        def objects(*args,**kwargs):
            if threading.get_ident()!=owner[0]:raise RecoveryError("RESOURCE_UNAVAILABLE",503)
            return original_objects(*args,**kwargs)
        def first():owner.append(threading.get_ident());return self.execute()
        with patch.object(self.case.journal,"append",side_effect=append),patch.object(self.case.target,"objects",side_effect=objects), \
                ThreadPoolExecutor(max_workers=1,thread_name_prefix="s44-r4-inflight-owner") as executor:
            pending=executor.submit(first)
            duplicate_status=None;first_error=None;result=None
            try:
                self.assertTrue(checkpoint.wait(5),"첫 실행이 phase1 성공 prefix에서 실제 진행 중")
                # 별도 service/별도 pool이어도 같은 DB advisory namespace를 소비한다.
                duplicate=object.__new__(PostgresRecoveryService);duplicate.__dict__.update(self.service.__dict__)
                duplicate._pool=self.service._pool.clone()
                try:duplicate.execute_registered_restore(self.case.context,"request-a",expected_version=2,preview_version=1,
                    idempotency_key="execute-r4",step_up_verified=True,recheck=self.current_hook)
                except RecoveryError as error:duplicate_status=error.status
                state_after_duplicate=self.parent["state"]
                prefix_after_duplicate=len(self.case.journal.rows)
            finally:
                release.set()
                try:result=pending.result(timeout=5)
                except Exception as error:first_error=type(error).__name__
        self.assertEqual((duplicate_status,state_after_duplicate,prefix_after_duplicate,first_error),(409,"restoring",2,None),
            "진행 중 중복은 busy 거부, 첫 부모/phase 실패 기록0")
        self.assertEqual((state_after_duplicate,prefix_after_duplicate),("restoring",2))
        self.assertIsNone(first_error);self.assertEqual(result.state,"completed")
        self.assertEqual(len(self.case.journal.rows),14);self.assertEqual(len(self.verifications),1)
        self.assertEqual(len(self.notified),1);self.assertEqual(self.failed_notified,[])
        self.assertEqual(self.guard_backend["keys"],{})

    def test_r4_execution_guard_is_request_scoped_nonblocking_and_exception_released(self):
        other=object.__new__(PostgresRecoveryService);other.__dict__.update(self.service.__dict__);other._pool=self.service._pool.clone()
        with self.service._registered_execution_guard(self.case.context,"request-a") as owner:
            owner()
            with self.assertRaises(RecoveryError) as busy:
                with other._registered_execution_guard(self.case.context,"request-a"):self.fail("busy must not enter")
            self.assertEqual((busy.exception.status,busy.exception.retryable),(409,False))
            owner()
            with other._registered_execution_guard(self.case.context,"request-b") as independent:independent()
        self.assertEqual(self.guard_backend["keys"],{})
        with self.assertRaisesRegex(RuntimeError,"owned-test-error"):
            with self.service._registered_execution_guard(self.case.context,"request-a"):raise RuntimeError("owned-test-error")
        with other._registered_execution_guard(self.case.context,"request-a") as reacquired:reacquired()
        self.assertEqual(self.guard_backend["keys"],{})

    def test_r4_execution_guard_ownership_loss_preserves_prefix_without_false_parent_failure(self):
        from psycopg.pq import TransactionStatus
        original=self.case.journal.append
        def append(*args,**kwargs):
            result=original(*args,**kwargs)
            if args[2:4]==(1,"succeeded"):
                self.guard_backend["sessions"][0].info.transaction_status=TransactionStatus.UNKNOWN
            return result
        with patch.object(self.case.journal,"append",side_effect=append),self.assertRaises(RecoveryError) as caught:self.execute()
        self.assertEqual(caught.exception.status,503);self.assertEqual(self.parent["state"],"restoring")
        self.assertEqual(len(self.case.journal.rows),2);self.assertEqual(self.failed_notified,[])
        self.assertEqual(self.guard_backend["keys"],{})
        self.assertEqual(self.execute().state,"completed")

    def _final_probe_change(self,kind):
        from daon_user_api import recovery_lineage_restore as materializer
        finished=[];changed=[];actual=materializer.materialize_lineage
        def materialized(*args,**kwargs):
            result=actual(*args,**kwargs);finished.append(True);return result
        original=self.service._destination_probe.observe
        ledger=self.case.capture_case.ledger
        def observe(request):
            result=original(request)
            if finished and not changed:
                changed.append(True)
                if kind=="acl":ledger.revoked.add("prior-version")
                elif kind=="hold":ledger.tables["legal_holds"].append(dict(tenant_id=self.case.context.tenant_id,workspace_id=self.case.context.workspace_id,
                    hold_id="probe-hold",source_id="source-a",state="released",version=2,policy_version=self.case.context.policy_version))
                else:ledger.tables["deletion_requests"].append(dict(tenant_id=self.case.context.tenant_id,workspace_id=self.case.context.workspace_id,
                    request_id="probe-purge",source_id="source-a",state="purged",version=4,source_active=False,purge_started=True,policy_version=self.case.context.policy_version))
            return result
        with patch.object(materializer,"materialize_lineage",side_effect=materialized),patch.object(self.service._destination_probe,"observe",side_effect=observe):
            with self.assertRaises(RecoveryError) as caught:self.execute()
        self.assertEqual(caught.exception.status,403 if kind=="acl" else 409)
        self.assertTrue(changed);self.assertEqual(len(self.case.journal.rows),14)
        self.assertEqual(self.parent["state"],"restoring")
        self.assertEqual(self.verifications,[]);self.assertEqual(self.notified,[])
        self.assertNotIn("restore.completed",[event.get("action") for event in self.events if isinstance(event,dict)])

    def test_r4_final_physical_probe_acl_revocation_prevents_completion(self):self._final_probe_change("acl")

    def test_r4_final_physical_probe_hold_change_prevents_completion(self):self._final_probe_change("hold")

    def test_r4_final_physical_probe_purge_change_prevents_completion(self):self._final_probe_change("purge")

    def test_r4_probe_completion_clock_and_during_probe_expiry_are_rechecked(self):
        base=self.case.now;ticks=[base]
        self.service._clock=lambda:ticks[0]
        row={**self.parent,**self.preview,**self.reservation}
        payload=self.service._open_registered_preview(row,bytes(row["encrypted_preview_receipt"]))
        def observed(request):
            ticks[0]+=timedelta(seconds=2)
            return replace(self.case.observation,observed_at=ticks[0])
        self.service._destination_probe.observe=observed
        value=self.service._registered_current_destination(self.case.context,row,payload)
        self.assertEqual(value.observed_at,base+timedelta(seconds=2))
        def expired(request):
            ticks[0]=base+timedelta(hours=2)
            return replace(self.case.observation,observed_at=ticks[0])
        ticks[0]=base;self.service._destination_probe.observe=expired
        with self.assertRaises(RecoveryError):self.service._registered_current_destination(self.case.context,row,payload)
        ticks[0]=base;self.service._destination_probe.observe=observed
        with self.assertRaises(RecoveryError):self.service._registered_current_destination(self.case.context,{**row,"expires_at":base+timedelta(seconds=1)},payload)

    def test_r4_execute_calls_real_r3_from_independent_bytes_then_verified_source_commit_once(self):
        self.assertTrue(callable(getattr(self.service,"execute_registered_restore",None)),"R4 Execute consumer missing")
        result=self.execute()
        self.assertEqual((result.state,result.version),("completed",6))
        self.assertEqual(len(self.case.journal.rows),14)
        self.assertEqual(len(self.verifications),1)
        self.assertEqual(tuple(self.verifications[0][5:9]),(True,True,True,True))
        self.assertEqual(len(self.notified),1)
        for row in self.case.metadata["source_versions"]:
            self.assertEqual(self.case.database.rows["source_versions"][row["record_id"]],row)
            self.assertEqual(self.case.storage.get(self.case.objects.records[row["object_id"]].object_key),self.case.content[row["object_id"]])
        before=(self.case.database.writes,self.case.storage.puts,len(self.events))
        replay=self.execute()
        self.assertEqual(replay,result)
        self.assertEqual((self.case.database.writes,self.case.storage.puts,len(self.events)),before)
        self.assertEqual(len(self.notified),1)

    def test_r4_late_current_retention_and_session_change_never_start_target(self):
        self.assertTrue(callable(getattr(self.service,"execute_registered_restore",None)),"R4 Execute consumer missing")
        self.case.context=replace(self.case.context,session_id="another-session")
        with self.assertRaises(RecoveryError):self.execute()
        self.assertEqual(self.case.database.writes,0)
        self.case.context=replace(self.case.context,session_id="session-current")
        ledger=self.case.capture_case.ledger
        self.current_hook=lambda:ledger.revoked.add("prior-version")
        with self.assertRaises(RecoveryError):self.execute()
        self.assertEqual(self.case.database.writes,0)
        self.assertEqual(self.notified,[])

    def test_r4_retention_changes_during_heavy_verify_fail_before_target_write(self):
        original_scope=self.case.scope;original_receipt=copy.deepcopy(self.case.receipt)
        verify=self.case.store._verify_data;calls=[]
        def changed(*args,**kwargs):
            result=verify(*args,**kwargs);calls.append(True)
            if len(calls)==2:
                self.case.capture_case.ledger.tables["legal_holds"].append(dict(tenant_id=self.case.context.tenant_id,workspace_id=self.case.context.workspace_id,
                    hold_id="late-hold",source_id="source-a",state="released",version=2,policy_version=self.case.context.policy_version))
            return result
        with patch.object(self.case.store,"_verify_data",side_effect=changed),self.assertRaises(RecoveryError) as caught:self.execute()
        self.assertEqual(caught.exception.status,409)
        self.assertEqual(self.case.database.writes,0)
        self.assertEqual(self.case.journal.rows,[])
        self.assertEqual(self.parent["state"],"failed")
        self.assertEqual(self.notified,[])
        self.assertEqual(self.case.scope,original_scope);self.assertEqual(self.case.receipt,original_receipt)

    def test_r4_target_uncertainty_never_marks_source_completed_or_deletes_target(self):
        self.assertTrue(callable(getattr(self.service,"execute_registered_restore",None)),"R4 Execute consumer missing")
        self.case.storage.fail_promote=True
        with self.assertRaises(RecoveryError):self.execute()
        self.assertEqual(self.parent["state"],"failed")
        self.assertEqual(self.case.journal.rows[-1]["outcome"],"uncertain")
        self.assertEqual(self.verifications,[]);self.assertEqual(self.notified,[])
        self.assertEqual(len(self.failed_notified),1)
        self.assertEqual(self.case.storage.deletes,0)

    def test_r4_source_final_commit_uncertainty_keeps_target_and_exact_reentry(self):
        self.assertTrue(callable(getattr(self.service,"execute_registered_restore",None)),"R4 Execute consumer missing")
        self.lost_final=True
        with self.assertRaises(RecoveryError):self.execute()
        self.assertEqual(len(self.case.journal.rows),14)
        self.assertEqual(self.parent["state"],"restoring")
        self.assertEqual(self.verifications,[])
        self.assertEqual(self.notified,[])
        before=(self.case.database.writes,self.case.storage.puts)
        self.lost_final=False
        self.assertEqual(self.execute().state,"completed")
        self.assertEqual((self.case.database.writes,self.case.storage.puts),before)
        self.assertEqual(len(self.notified),1)


class RecoverySelectionResolverTests(unittest.TestCase):
    def test_r1c_lineage_adapter_consumes_real_resolver_and_current_acl_workflow(self):
        from test_recovery_lineage import Ledger
        ledger=Ledger(); controls=[]
        class Read:
            info=ledger.info
            def execute(inner,sql,params=()):
                if sql=="SET LOCAL ROLE daon_app":
                    controls.append(sql); return SimpleNamespace(fetchone=lambda:None)
                return ledger.execute(sql,params)
        @contextmanager
        def transaction(context):
            self.assertEqual((context.tenant_id,context.workspace_id,context.actor_id,context.capability),
                (ledger.context.tenant_id,ledger.context.workspace_id,ledger.context.actor_id,"recovery"))
            yield Read()
        resolver=recovery_postgres.PostgresBackupSelectionResolver(SimpleNamespace(readiness=lambda:SimpleNamespace(ready=True,schema_revision="0069"),_transaction=transaction))
        resolved=resolver.resolve_lineage(ledger.context,ledger.selection)
        self.assertEqual(resolved.source_version_count,2)
        self.assertEqual(controls,["SET LOCAL ROLE daon_app"])
        ledger.revoked.add("prior-version")
        with self.assertRaises(RecoveryError) as caught: resolver.resolve_lineage(ledger.context,ledger.selection)
        self.assertEqual(caught.exception.status,404)
        ledger.revoked.clear(); ledger.auth.auth_role="organization_admin"; ledger.workflow_state="suspended"
        with self.assertRaises(RecoveryError) as caught: resolver.resolve_lineage(ledger.context,ledger.selection)
        self.assertEqual(caught.exception.status,403)
        self.assertFalse(any(sql.startswith(("INSERT","UPDATE","DELETE")) for sql,_ in ledger.log))

    """실제 resolver의 SQL 읽기 포트만 대역. PG/RLS·capture 성공 증거가 아니다."""
    def test_l2_inventory_only_durable_create_refuses_before_storage_or_database(self):
        from daon_user_api.recovery import RecoveryError
        service = object.__new__(PostgresRecoveryService)
        service._storage = SimpleNamespace(verify_source=lambda *args: self.fail("inventory-only source read forbidden"))
        service._transaction = lambda *args: self.fail("inventory-only ledger write forbidden")
        with self.assertRaises(RecoveryError) as caught:
            service.create_backup(RecoveryContext("tenant-a", "workspace-a", "actor-a", "trace-a", "policy-a", True),
                trigger="manual", schema_revision="schema-a", retention_watermark="watermark-a",
                objects=(BackupObjectInput("object-a", "a" * 64, 7),), idempotency_key="legacy-create")
        self.assertEqual((caught.exception.status, caught.exception.retryable), (503, False))

    def test_l2_stream_port_uses_scoped_key_and_always_releases_response(self):
        import io
        response = io.BytesIO(b"synthetic source")
        released = []
        response.release_conn = lambda: released.append(True)
        keys = []
        class Client:
            def get_object(self, bucket, key):
                self_bucket = bucket
                keys.append((self_bucket, key))
                return response
        adapter = MinioRecoveryStorageAdapter(SimpleNamespace(_client=Client(), _bucket="existing-bucket"))
        context = RecoveryContext("tenant-a", "workspace-a", "actor-a", "trace-a", "policy-a", True)
        with adapter.open_source(context, BackupObjectInput("a" * 32, "a" * 64, 16)) as stream:
            self.assertEqual(stream.read(4), b"synt")
        self.assertTrue(response.closed)
        self.assertEqual(released, [True])
        self.assertEqual(keys, [("existing-bucket", "tenant-a/workspace-a/source/" + "a" * 32)])
        class BrokenClose:
            def close(self): raise RuntimeError("synthetic close failure")
            def release_conn(self): released.append(True)
        adapter._storage._client.get_object = lambda *args: BrokenClose()
        from daon_user_api.recovery import RecoveryError
        with self.assertRaises(RecoveryError):
            with adapter.open_source(context, BackupObjectInput("a" * 32, "a" * 64, 16)): pass
        self.assertEqual(released, [True, True])

    def test_l2_v1_get_decode_preserved_but_old_execute_and_preview_replay_cannot_bypass(self):
        service = object.__new__(PostgresRecoveryService)
        service._key = hashlib.sha256(secrets.token_bytes(32)).digest()
        service._capture_store = None
        item = BackupObjectInput("object-a", "a" * 64, 7)
        old = service._encode_objects((item,))
        self.assertEqual(service._decode_objects(old), (item,))
        queries = []
        class Connection:
            def execute(self, sql, params=()):
                queries.append(sql)
                return SimpleNamespace(fetchone=lambda: {"manifest_version": 1, "encrypted_inventory": old})
        @contextmanager
        def transaction(*args): yield Connection()
        context = RecoveryContext("tenant-a", "workspace-a", "actor-a", "trace-a", "policy-a", True)
        service._transaction = transaction
        request = SimpleNamespace(backup_id="backup-old")
        with patch.object(service, "_select_restore", return_value=request), \
             patch.object(service, "_operation_replay", return_value="restore-old") as replay:
            from daon_user_api.recovery import RecoveryError
            with self.assertRaises(RecoveryError) as caught:
                service.execute_restore(context, "restore-old", expected_version=2, preview_version=1,
                    idempotency_key="old-successful-key", step_up_verified=True)
            self.assertEqual(caught.exception.status, 409)
            self.assertEqual(replay.call_count, 0)
        queries.clear()
        with patch.object(service, "_select_restore", return_value=request):
            with self.assertRaises(RecoveryError) as caught:
                service.create_restore_preview(context, "backup-old", destination=RestoreDestination(
                    "fixture-tenant-b", "fixture-workspace-b", "fixture-database-b", "fixture-bucket-b"),
                    idempotency_key="old-preview-key", step_up_verified=True)
            self.assertEqual(caught.exception.status, 409)
        self.assertFalse(any(sql.startswith(("INSERT", "UPDATE", "DELETE")) for sql in queries))

    def test_l2_v2_inventory_decode_and_missing_materializer_refuse_execute(self):
        from daon_user_api.recovery_capture import CaptureScope, encode_manifest
        from daon_user_api.recovery import RecoveryError
        key = secrets.token_bytes(32)
        service = object.__new__(PostgresRecoveryService)
        service._key = hashlib.sha256(key).digest()
        token = service._capture_identity("tenant-a", "workspace-a", "actor-a", "synthetic-key")
        scope = CaptureScope("capture-" + token, "tenant-a", "workspace-a", "notebook-a", ("version-a",),
            "a" * 64, "schema-a", "policy-a", "watermark-a")
        receipt = {**scope.binding(), "key_version": "key-v1", "key_reference": "existing-manifest-key",
            "objects": [{"object_id": "object-a", "plaintext_digest": "a" * 64, "byte_size": 7}]}
        envelope = encode_manifest(scope, receipt, master_key=key, key_version="key-v1", key_reference="existing-manifest-key")
        service._manifest_master_key = key
        service._capture_store = SimpleNamespace(key_version="key-v1", key_reference="existing-manifest-key", verify=lambda *args, **kwargs: None)
        self.assertEqual(service._decode_objects(envelope), (BackupObjectInput("object-a", "a" * 64, 7),))
        now = datetime.now(timezone.utc)
        row = {"encrypted_inventory": envelope, "manifest_digest": hashlib.sha256(envelope).hexdigest(),
            "backup_id": "backup-" + token, "actor_id": "actor-a", "idempotency_key": "synthetic-key",
            "manifest_version": 2, "tenant_id": "tenant-a", "workspace_id": "workspace-a", "state": "ready",
            "version": 4, "trigger_type": "manual", "created_at": now, "verified_at": now, "schema_revision": "schema-a",
            "retention_watermark": "watermark-a", "policy_version": "policy-a", "request_fingerprint": "a" * 64, "object_count": 1}
        service._backup_from_row(row)
        for key, value in (("tenant_id", "other-tenant"), ("request_fingerprint", "f" * 64),
                           ("manifest_digest", "f" * 64), ("object_count", 2)):
            with self.assertRaises(RecoveryError): service._backup_from_row({**row, key: value})
        @contextmanager
        def transaction(*args):
            yield SimpleNamespace(execute=lambda *args: SimpleNamespace(fetchone=lambda: row))
        service._transaction = transaction
        with patch.object(service, "_select_restore", return_value=SimpleNamespace(backup_id="backup-" + token)), \
             patch.object(service, "_operation_replay", return_value="restore-old") as replay:
            with self.assertRaises(RecoveryError) as caught:
                service.execute_restore(RecoveryContext("tenant-a", "workspace-a", "actor-a", "trace-a", "policy-a", True),
                    "restore-v2", expected_version=2, preview_version=1, idempotency_key="v2-execute", step_up_verified=True)
            self.assertEqual(caught.exception.status, 503)
            self.assertEqual(replay.call_count, 0)

    def test_l1_legacy_counts_only_current_candidates_and_never_hides_database_failure(self) -> None:
        from daon_user_api.recovery import BackupSelectionInput, RecoveryError
        ctx = RecoveryContext("tenant-a", "workspace-a", "actor-a", "trace-a", "policy-a", True)
        payload = {"object_id": "object-a", "content_digest_sha256": "a" * 64, "byte_size": 7}
        digest = hashlib.sha256(canonical_json_bytes(payload)).hexdigest()
        for mode in ("private", "unbound", "notebook-deleted", "source-deleted", "both-valid", "database-failure", "canonical-mismatch"):
            class Reader:
                def execute(self, query, params=()):
                    values = []
                    if query.startswith("SELECT b.notebook_id"):
                        values = [("notebook-a", "version-a", "object-a"), ("notebook-b", "version-b", "object-a")]
                    elif query.startswith("SELECT sv.record_id"):
                        if params[2] == "notebook-b" and mode == "database-failure":
                            raise psycopg.OperationalError("synthetic DB failure, no connection data")
                        if params[2] == "notebook-a" or mode in ("private", "both-valid", "canonical-mismatch"):
                            suffix = "a" if params[2] == "notebook-a" else "b"
                            actual_digest = "f" * 64 if suffix == "b" and mode == "canonical-mismatch" else digest
                            values = [("version-" + suffix, "source-" + suffix, "object-a", payload, actual_digest, "a" * 64, 7)]
                    elif "FROM alembic_version" in query:
                        values = [("test-revision",)]
                    return SimpleNamespace(fetchone=lambda: values[0] if values else None, fetchall=lambda: list(values))
            class Store:
                def readiness(self): return SimpleNamespace(ready=True, schema_revision="test-revision")
                @contextmanager
                def _transaction(self, _context): yield Reader()
            resolver = recovery_postgres.PostgresBackupSelectionResolver(Store())
            with self.subTest(mode=mode), patch.object(recovery_postgres, "_current_auth_grant"), \
                 patch.object(recovery_postgres, "_source_access_allowed", side_effect=lambda connection, context, version: not (mode == "private" and version == "version-b")), \
                 patch.object(recovery_postgres, "_special_source_access_allowed", return_value=True):
                expected = resolver.resolve(ctx, BackupSelectionInput("notebook-a", ("version-a",)))
                arguments = dict(schema_revision=expected.schema_revision, retention_watermark=expected.retention_watermark, objects=expected.objects)
                if mode in ("both-valid", "database-failure", "canonical-mismatch"):
                    with self.assertRaises(RecoveryError) as caught: resolver.resolve_legacy(ctx, **arguments)
                    self.assertEqual(caught.exception.status, {"both-valid": 404, "database-failure": 503, "canonical-mismatch": 409}[mode])
                else:
                    actual = resolver.resolve_legacy(ctx, **arguments)
                    self.assertEqual(actual.selection.notebook_id, "notebook-a")
                    self.assertEqual(actual.fingerprint, expected.fingerprint)

    def test_l1_resolver_canonical_metadata_acl_recheck_and_capture_zero(self) -> None:
        resolver_type = getattr(recovery_postgres, "PostgresBackupSelectionResolver", None)
        self.assertTrue(callable(resolver_type), "실제 SELECT 기반 metadata resolver 필요")
        from daon_user_api.recovery import BackupSelectionInput, RecoveryError
        ctx = RecoveryContext("tenant-a", "workspace-a", "actor-a", "trace-a", "policy-a", True,
                              session_id="session-a", device_id="device-a")
        payload = {"object_id": "object-a", "content_digest_sha256": "a" * 64, "byte_size": 7}
        canonical_digest = hashlib.sha256(canonical_json_bytes(payload)).hexdigest()
        row = ("version-a", "source-a", "object-a", payload, canonical_digest, "a" * 64, 7)
        class Reader:
            def __init__(self): self.rows = [row]; self.log = []; self.retention = []; self.replay = None
            def execute(self, query, params=()):
                self.log.append((query, params))
                if query.startswith("SELECT sv.record_id"): values = self.rows
                elif "FROM alembic_version" in query: values = [("test-revision",)]
                elif "FROM deletion_requests" in query: values = self.retention
                elif "FROM legal_holds" in query: values = []
                elif "FROM backup_records" in query: values = [] if self.replay is None else [(self.replay,)]
                else: values = []
                return SimpleNamespace(fetchone=lambda: values[0] if values else None, fetchall=lambda: list(values))
        reader = Reader()
        class Store:
            def readiness(self): return SimpleNamespace(ready=True, schema_revision="test-revision")
            @contextmanager
            def _transaction(self, _context): yield reader
        resolver = resolver_type(Store())
        selection = BackupSelectionInput("notebook-a", ("version-a",))
        with patch.object(recovery_postgres, "_current_auth_grant"), \
             patch.object(recovery_postgres, "_source_access_allowed", return_value=True) as acl, \
             patch.object(recovery_postgres, "_special_source_access_allowed", return_value=True):
            result = resolver.resolve(ctx, selection, idempotency_key="request-a")
            self.assertEqual((result.objects[0].object_id, result.objects[0].byte_size, result.schema_revision), ("object-a", 7, "test-revision"))
            self.assertGreaterEqual(acl.call_count, 2)
            reader.replay = "f" * 64
            with self.assertRaises(RecoveryError) as caught: resolver.resolve(ctx, selection, idempotency_key="request-a")
            self.assertEqual(caught.exception.status, 409)
            reader.replay = None
            acl.side_effect = [True, False]
            with self.assertRaises(RecoveryError) as caught: resolver.resolve(ctx, selection)
            self.assertEqual(caught.exception.status, 404)
            acl.side_effect = None
            for changed in ([], [(*row[:4], "f" * 64, *row[5:])], [(*row[:5], "b" * 64, 7)], [(*row[:6], 8)]):
                reader.rows = changed
                with self.assertRaises(RecoveryError): resolver.resolve(ctx, selection)
            queries = [q for q, _ in reader.log]
            self.assertFalse(any(q.lstrip().upper().startswith(("INSERT", "UPDATE", "DELETE")) for q in queries))
            metadata_sql = next(q for q in queries if q.startswith("SELECT sv.record_id"))
            for expected in ("notebook_bindings", "notebook_source_unbindings", "source_active=false", "o.status='completed'"):
                self.assertIn(expected, metadata_sql)
            self.assertNotIn("index_versions", metadata_sql)
            self.assertNotIn("s.state='ready'", metadata_sql)


class _SelectionPrivateDsn(str):
    def __repr__(self): return "<private S44 isolated PG DSN>"


@unittest.skipUnless(os.environ.get("DAON_RECOVERY_SELECTION_INTEGRATION_DSN") or os.environ.get("DAON_RECOVERY_SELECTION_INTEGRATION_DSN_FILE"),
                     "NOT RUN: dedicated S44 metadata PostgreSQL DSN required (MinIO not required)")
class RecoverySelectionPostgresIntegrationTests(unittest.TestCase):
    """전용 PG의 실제 RLS/metadata 조회. Object bytes/capture/전체 복구 성공이 아니다.

    fixture는 append-only 표이므로 guard를 끄거나 DELETE하지 않는다.
    연결은 매 시험 닫고 전용 DB 전체 정리는 DB를 만든 main이 수행한다.
    """
    def _connect(self):
        try:
            connection = psycopg.connect(self.dsn, connect_timeout=3)
            name = connection.execute("SELECT current_database()").fetchone()[0]
            if name != "daon_user_s44_l1_qa_20261018":
                connection.close()
                raise AssertionError("S44_DEDICATED_DATABASE_REQUIRED")
            return connection
        except psycopg.Error:
            raise AssertionError("S44_ISOLATED_DATABASE_CONNECTION_FAILED") from None

    def setUp(self):
        from daon_user_api.recovery import BackupSelectionInput
        self.dsn = _SelectionPrivateDsn(_environment_or_file("DAON_RECOVERY_SELECTION_INTEGRATION_DSN"))
        self.suffix = secrets.token_hex(8)
        self.context = RecoveryContext("s44-l1-tenant-" + self.suffix, "s44-l1-workspace-" + self.suffix,
            "s44-l1-actor-" + self.suffix, "s44-l1-trace-" + self.suffix, "runtime-policy-v1", True,
            session_id="s44-session-" + self.suffix, device_id="s44-device-" + self.suffix)
        self.notebook = "s44-l1-notebook-" + self.suffix
        ctx = self.context
        with self._connect() as connection:
            # 이 fixture DML 전에 정확 DB명을 이미 확인했다. 기존 schema/roles/grants 변경은 없다.
            connection.execute("INSERT INTO tenants(tenant_id,display_name) VALUES(%s,'S44 isolated metadata')", (ctx.tenant_id,))
            connection.execute("INSERT INTO workspaces(tenant_id,workspace_id,display_name) VALUES(%s,%s,'S44 isolated metadata')", (ctx.tenant_id, ctx.workspace_id))
            connection.execute("INSERT INTO user_accounts(tenant_id,user_id) VALUES(%s,%s)", (ctx.tenant_id, ctx.actor_id))
            connection.execute("INSERT INTO identity_users(user_id,issuer,subject,state) VALUES(%s,'s44-isolated',%s,'active')", (ctx.actor_id, ctx.actor_id))
            connection.execute("INSERT INTO identity_tenants(tenant_id) VALUES(%s)", (ctx.tenant_id,))
            connection.execute("INSERT INTO identity_memberships(tenant_id,user_id,role) VALUES(%s,%s,'organization_admin')", (ctx.tenant_id, ctx.actor_id))
            connection.execute("INSERT INTO identity_auth_workspaces VALUES(%s,%s,'organization','cloud_sync',1000,1,1,now())", (ctx.workspace_id, ctx.tenant_id))
            connection.execute("INSERT INTO identity_auth_tenant_roles VALUES(%s,%s,'organization_admin','active',1,now())", (ctx.tenant_id, ctx.actor_id))
            connection.execute("INSERT INTO identity_auth_tenant_policies VALUES(%s,1,now())", (ctx.tenant_id,))
            connection.execute("INSERT INTO identity_auth_workspace_policies VALUES(%s,%s,1,now())", (ctx.tenant_id, ctx.workspace_id))
            self._scope(connection)
            connection.execute("INSERT INTO notebooks(tenant_id,workspace_id,notebook_id,created_by,created_at) VALUES(%s,%s,%s,%s,now())",
                (ctx.tenant_id, ctx.workspace_id, self.notebook, ctx.actor_id))
            self.revision = connection.execute("SELECT version_num FROM alembic_version").fetchone()[0]
        self.store = PostgresCloudStore(self.dsn)
        self.resolver = recovery_postgres.PostgresBackupSelectionResolver(self.store)
        self.addCleanup(self.store.close)
        self.version, self.source, self.object_id = self._source("a")
        self.selection = BackupSelectionInput(self.notebook, (self.version,))

    def _scope(self, connection):
        for key, value in (("tenant_id", self.context.tenant_id), ("workspace_id", self.context.workspace_id),
                           ("actor_id", self.context.actor_id), ("notebook_id", self.notebook)):
            connection.execute("SELECT set_config(%s,%s,true)", ("app." + key, value))

    def _source(self, label, *, size=7, canonical_size=None, canonical_hash=None, origin=None, object_state="completed"):
        from daon_user_api.studio_workspace_postgres import PostgresStudioWorkspaceRepository as Canon
        ctx = self.context
        source, version, obj = [f"s44-{kind}-{label}-{self.suffix}" for kind in ("source", "version", "object")]
        payload = {"filename": "S44 isolated metadata.txt", "object_id": obj,
                   "byte_size": size if canonical_size is None else canonical_size,
                   "content_digest_sha256": canonical_hash or "a" * 64}
        if origin is not None:
            payload.update(origin_kind=origin, text_sha256="a" * 64, raw_sha256="b" * 64,
                snapshot_id="s44-missing-snapshot-" + self.suffix, connector_id="mcp-open-law-go-kr")
        with self._connect() as connection:
            self._scope(connection)
            time_columns = ["created_at"]
            if connection.execute("SELECT 1 FROM information_schema.columns WHERE table_schema='public' "
                "AND table_name='object_records' AND column_name='updated_at'").fetchone() is not None:
                time_columns.append("updated_at")
            connection.execute("INSERT INTO object_records(tenant_id,workspace_id,object_id,area,staging_key,object_key,digest_sha256,byte_size,content_type,status,created_by,trace_id,idempotency_key,request_fingerprint,"
                + ",".join(time_columns) + ") VALUES(%s,%s,%s,'source',%s,%s,%s,%s,'text/plain',%s,%s,%s,%s,%s,"
                + ",".join("now()" for _ in time_columns) + ")",
                (ctx.tenant_id, ctx.workspace_id, obj, "s44-isolated-staging/" + obj, "s44-isolated-source/" + obj,
                 "a" * 64, size, object_state, ctx.actor_id, ctx.trace_id, obj, "f" * 64))
            # registered + index 없음도 현재 접근 가능한 원본이면 허용해야 한다.
            Canon._insert(connection, ctx, "sources", source, {"filename": "S44 isolated metadata.txt"}, state="registered")
            Canon._insert(connection, ctx, "source_versions", version, payload,
                extra_columns=("source_id", "object_id"), extra_values=(source, obj))
            connection.execute("INSERT INTO notebook_bindings(tenant_id,workspace_id,notebook_id,binding_kind,record_id,version_id,created_by,created_at) "
                "VALUES(%s,%s,%s,'source',%s,%s,%s,now())", (ctx.tenant_id, ctx.workspace_id, self.notebook, source, version, ctx.actor_id))
        return version, source, obj

    def _denied(self, context=None, selection=None):
        from daon_user_api.recovery import RecoveryError
        with self.assertRaises(RecoveryError) as caught:
            self.resolver.resolve(context or self.context, selection or self.selection)
        self.assertIn(caught.exception.status, (403, 404, 409))

    def _deny_source(self, version):
        ctx = self.context
        with self._connect() as connection:
            connection.execute("INSERT INTO identity_auth_source_access VALUES(%s,%s,%s,%s,0,1,now())",
                (ctx.tenant_id, ctx.workspace_id, ctx.actor_id, version))

    def test_l1_real_pg_metadata_legacy_order_size_and_fingerprint(self):
        from daon_user_api.recovery import BackupSelectionInput, RecoveryError
        second, _, _ = self._source("b", size=512 * 1024 * 1024 - 7)
        selected = BackupSelectionInput(self.notebook, (second, self.version))
        result = self.resolver.resolve(self.context, selected)
        reordered = self.resolver.resolve(self.context, BackupSelectionInput(self.notebook, tuple(reversed(selected.source_version_ids))))
        self.assertEqual(result.fingerprint, reordered.fingerprint)
        self.assertEqual(result.schema_revision, self.revision)
        self.assertEqual(sum(item.byte_size for item in result.objects), 512 * 1024 * 1024)
        legacy = self.resolver.resolve_legacy(self.context, schema_revision=result.schema_revision,
            retention_watermark=result.retention_watermark, objects=tuple(reversed(result.objects)))
        self.assertEqual(legacy.fingerprint, result.fingerprint)
        with self.assertRaises(RecoveryError):
            self.resolver.resolve_legacy(self.context, schema_revision="forged", retention_watermark=result.retention_watermark, objects=result.objects)
        oversized, _, _ = self._source("oversized", size=512 * 1024 * 1024)
        with self.assertRaises(RecoveryError) as caught:
            self.resolver.resolve(self.context, BackupSelectionInput(self.notebook, (self.version, oversized)))
        self.assertEqual(caught.exception.status, 413)
        with self._connect() as connection:
            self._scope(connection)
            self.assertEqual(connection.execute("SELECT count(*) FROM backup_records WHERE tenant_id=%s AND workspace_id=%s",
                (self.context.tenant_id, self.context.workspace_id)).fetchone()[0], 0)

    def test_l1_real_pg_scope_acl_unbinding_and_inflight_revocation(self):
        from daon_user_api.recovery import BackupSelectionInput
        self._denied(context=replace(self.context, tenant_id="s44-other-tenant"))
        self._denied(context=replace(self.context, workspace_id="s44-other-workspace"))
        self._denied(selection=BackupSelectionInput("s44-other-notebook", (self.version,)))
        self._denied(selection=BackupSelectionInput(self.notebook, ("s44-missing-version",)))
        original = self.resolver._metadata
        changed = False
        def revoke_after_read(connection, context, selection):
            nonlocal changed
            value = original(connection, context, selection)
            if not changed:
                changed = True; self._deny_source(self.version)
            return value
        # 접근 판정/결과는 대역이 아니다. 실제 조회 사이에 격리 ACL 행만 INSERT한다.
        with patch.object(self.resolver, "_metadata", side_effect=revoke_after_read):
            self._denied()
        self._denied()
        version, source, _ = self._source("unbind")
        with self._connect() as connection:
            self._scope(connection)
            connection.execute("INSERT INTO notebook_source_unbindings(tenant_id,workspace_id,notebook_id,source_id,source_version_id,binding_version,actor_id,occurred_at) "
                "VALUES(%s,%s,%s,%s,%s,2,%s,now())", (self.context.tenant_id, self.context.workspace_id,
                self.notebook, source, version, self.context.actor_id))
        self._denied(selection=BackupSelectionInput(self.notebook, (version,)))

    def test_l1_real_pg_legacy_counts_only_current_accessible_bindings(self):
        from daon_user_api.recovery import RecoveryError
        from daon_user_api.studio_workspace_postgres import PostgresStudioWorkspaceRepository as Canon
        ctx = self.context

        def add_candidate(mode):
            notebook, source, version, request = [f"s44-{kind}-{mode}-{self.suffix}"
                for kind in ("notebook", "source", "version", "request")]
            with self._connect() as connection:
                self._scope(connection)
                connection.execute("INSERT INTO notebooks(tenant_id,workspace_id,notebook_id,created_by,created_at) VALUES(%s,%s,%s,%s,now())",
                    (ctx.tenant_id, ctx.workspace_id, notebook, ctx.actor_id))
                Canon._insert(connection, ctx, "sources", source, {"filename": "S44 isolated candidate.txt"}, state="registered")
                Canon._insert(connection, ctx, "source_versions", version,
                    {"filename": "S44 isolated candidate.txt", "object_id": self.object_id,
                     "byte_size": 7, "content_digest_sha256": "a" * 64},
                    extra_columns=("source_id", "object_id"), extra_values=(source, self.object_id))
                connection.execute("INSERT INTO notebook_bindings(tenant_id,workspace_id,notebook_id,binding_kind,record_id,version_id,created_by,created_at) "
                    "VALUES(%s,%s,%s,'source',%s,%s,%s,now())", (ctx.tenant_id, ctx.workspace_id, notebook, source, version, ctx.actor_id))
                if mode == "private":
                    connection.execute("INSERT INTO identity_auth_source_access VALUES(%s,%s,%s,%s,0,1,now())",
                        (ctx.tenant_id, ctx.workspace_id, ctx.actor_id, version))
                elif mode == "unbound":
                    connection.execute("INSERT INTO notebook_source_unbindings(tenant_id,workspace_id,notebook_id,source_id,source_version_id,binding_version,actor_id,occurred_at) "
                        "VALUES(%s,%s,%s,%s,%s,2,%s,now())", (ctx.tenant_id, ctx.workspace_id, notebook, source, version, ctx.actor_id))
                elif mode == "notebook-deleted":
                    connection.execute("INSERT INTO notebook_deletion_requests(request_id,tenant_id,workspace_id,notebook_id,actor_id,title_fingerprint,state,current_step,requested_at,idempotency_key,expected_version,request_fingerprint) "
                        "VALUES(%s,%s,%s,%s,%s,%s,'accepted','requested',now(),%s,1,%s)",
                        (request, ctx.tenant_id, ctx.workspace_id, notebook, ctx.actor_id, "a" * 64, request, "f" * 64))
                elif mode == "source-deleted":
                    connection.execute("INSERT INTO deletion_request_locator(tenant_id,request_id,workspace_id,source_id,created_at) VALUES(%s,%s,%s,%s,now())",
                        (ctx.tenant_id, request, ctx.workspace_id, source))
                    connection.execute("INSERT INTO deletion_requests(tenant_id,workspace_id,request_id,source_id,actor_id,state,version,source_active,purge_started,grace_until,policy_version,idempotency_key,request_fingerprint,trace_id,created_at,updated_at) "
                        "VALUES(%s,%s,%s,%s,%s,'purged',1,false,true,now(),%s,%s,%s,%s,now(),now())",
                        (ctx.tenant_id, ctx.workspace_id, request, source, ctx.actor_id, ctx.policy_version, request, "f" * 64, ctx.trace_id))

        # 모든 후보는 실제 동일 Object에 결속하지만 B 네 개는 현재 접근/결속 검증에서 제외된다.
        for mode in ("private", "unbound", "notebook-deleted", "source-deleted"):
            add_candidate(mode)
        expected = self.resolver.resolve(ctx, self.selection)
        arguments = dict(schema_revision=expected.schema_revision, retention_watermark=expected.retention_watermark,
                         objects=expected.objects, idempotency_key="s44-legacy-" + self.suffix)
        actual = self.resolver.resolve_legacy(ctx, **arguments)
        self.assertEqual(actual.selection.notebook_id, self.notebook)
        self.assertEqual(actual.fingerprint, expected.fingerprint)
        add_candidate("valid")
        with self.assertRaises(RecoveryError) as caught:
            self.resolver.resolve_legacy(ctx, **arguments)
        self.assertEqual((caught.exception.code, caught.exception.status), ("BACKUP_UNAVAILABLE", 404))
        with self._connect() as connection:
            self._scope(connection)
            self.assertEqual(connection.execute("SELECT count(*) FROM backup_records WHERE tenant_id=%s AND workspace_id=%s",
                (ctx.tenant_id, ctx.workspace_id)).fetchone()[0], 0)

    def test_l1_real_pg_object_canonical_mismatch_and_special_source_acl(self):
        from daon_user_api.recovery import BackupSelectionInput
        for label, options in (("hash", {"canonical_hash": "b" * 64}), ("size", {"canonical_size": 8}),
                               ("pending", {"object_state": "pending"}), ("web", {"origin": "website"}),
                               ("law", {"origin": "mcp_open_law"})):
            version, _, _ = self._source(label, **options)
            self._denied(selection=BackupSelectionInput(self.notebook, (version,)))



@unittest.skipUnless(CONFIGURED, "R1-M5-07 PostgreSQL/MinIO integration environment required")
class PostgresRecoveryIntegrationTests(unittest.TestCase):
    """실제 PG/MinIO + 로컬 보호 파일. 원본은 합성 namespace뿐이다.

    DB는 main이 만든 정확 전용 DB에만 쓴다. append-only 원장은 main이 DB째 회수한다.
    L2-A bytes 복호화는 L3 materialization/restore.completed 증거가 아니다.
    """
    def _connect(self):
        try:
            connection = psycopg.connect(_SelectionPrivateDsn(DSN), connect_timeout=3)
            if connection.execute("SELECT current_database()").fetchone()[0] != "daon_user_s44_l2a_qa_20261018":
                connection.close()
                raise AssertionError("S44_L2A_DEDICATED_DATABASE_REQUIRED")
            return connection
        except psycopg.Error:
            raise AssertionError("S44_L2A_ISOLATED_DATABASE_CONNECTION_FAILED") from None

    def _scope(self, connection):
        for key, value in (("tenant_id", self.tenant_id), ("workspace_id", self.workspace_id),
                           ("actor_id", self.actor_id), ("notebook_id", self.notebook)):
            connection.execute("SELECT set_config(%s,%s,true)", ("app." + key, value))

    def setUp(self) -> None:
        assert DSN and ENDPOINT and BUCKET and ACCESS_KEY and SECRET_KEY and MANIFEST_KEY
        # 어떤 DML/Object 쓰기보다 먼저 독립 DB 경계를 검사한다.
        with self._connect():
            pass
        self.file_root = Path(os.environ.get("DAON_RECOVERY_CAPTURE_TEST_ROOT", "D:/tmp/daon-user-s44-l2a-eoul2-20261018"))
        if not self.file_root.is_absolute() or self.file_root.name not in {
                "daon-user-s44-l2a-eoul2-20261018", "daon-user-s44-l2a-main-20261018"}:
            raise AssertionError("S44_EXACT_TEST_ROOT_REQUIRED")
        if os.name == "nt" and self.file_root.parent != Path("D:/tmp"):
            raise AssertionError("S44_EXACT_TEST_ROOT_REQUIRED")
        if self.file_root.exists() or self.file_root.is_symlink():
            raise AssertionError("S44_TEST_ROOT_MUST_BE_NEW")
        for parent in (self.file_root.parent, *self.file_root.parent.parents):
            if parent.is_symlink() or getattr(parent.lstat(), "st_file_attributes", 0) & 0x400:
                raise AssertionError("S44_TEST_ROOT_LINK_FORBIDDEN")
        self.file_root.mkdir()
        self.addCleanup(self._cleanup_files)
        self.archive = self.file_root / "archive"
        self.archive.mkdir()
        suffix = secrets.token_hex(6)
        self.suffix = suffix
        self.tenant_id = f"fixture-tenant-{suffix}"
        self.workspace_id = f"fixture-workspace-{suffix}"
        self.actor_id = f"actor-{suffix}"
        self.notebook = f"notebook-{suffix}"
        self.version_id = f"version-{suffix}"
        self.source_id = f"source-{suffix}"
        self.object_id = secrets.token_hex(16)
        self.content = f"R1-M5-07-C02:{suffix}".encode()
        self.digest = hashlib.sha256(self.content).hexdigest()
        self.context = RecoveryContext(
            self.tenant_id,
            self.workspace_id,
            self.actor_id,
            f"trace-{suffix}",
            "runtime-policy-v1",
            organization_admin=True,
            session_id=f"session-{suffix}", device_id=f"device-{suffix}",
        )
        self.cloud = PostgresCloudStore(DSN)
        self.addCleanup(self.cloud.close)
        self.queue = PostgresObjectQueueStore(DSN)
        self.addCleanup(self.queue.close)
        self.object_storage = MinioObjectStorageAdapter(
            endpoint=ENDPOINT,
            bucket=BUCKET,
            access_key=ACCESS_KEY,
            secret_key=SECRET_KEY,
            secure=False,
        )
        self.object_keys = set()
        self.addCleanup(self._cleanup_objects)
        scope = CloudAccessContext(
            self.tenant_id, self.workspace_id, self.actor_id, "object.write"
        )
        self.cloud.seed_scope(scope)
        self.object_keys.update((ObjectKeyPolicy().final_key(scope, "source", self.object_id),
                                 ObjectKeyPolicy().staging_key(scope, "source", self.object_id)))
        coordinator = ObjectQueueCoordinator(
            self.queue, self.object_storage, id_factory=lambda: self.object_id
        )
        coordinator.submit(
            scope,
            area="source",
            content=self.content,
            content_type="application/octet-stream",
            idempotency_key=f"seed-{suffix}",
            trace_id=f"trace-seed-{suffix}",
        )
        worker = DurableObjectWorker(self.queue, self.object_storage, f"worker-{suffix}")
        completed = worker.run_once(scope, now=datetime.now(timezone.utc))
        self.assertIsNotNone(completed)
        self.assertEqual(completed.state, "completed")
        self.opens, self.bytes_read = [], [0]
        test = self
        class ObservedMinioStorage(MinioRecoveryStorageAdapter):
            @contextmanager
            def open_source(self, context, item):
                test.opens.append(item.object_id)
                with super().open_source(context, item) as response:
                    class ReadProbe:
                        def read(self, count):
                            value = response.read(count)
                            test.bytes_read[0] += len(value)
                            return value
                    yield ReadProbe()
            def verify_source(self, *args):
                raise AssertionError("V2_MUST_STREAM_NOT_WHOLE_SOURCE_GET")
        self.storage = ObservedMinioStorage(self.object_storage)
        self.capture_store = FileCaptureStore(self.archive, master_key=MANIFEST_KEY,
            key_version="integration-v2", key_reference="DAON_RECOVERY_MANIFEST_KEY_FILE")
        from daon_user_api.studio_workspace_postgres import PostgresStudioWorkspaceRepository as Canon
        with self._connect() as connection:
            ctx = self.context
            connection.execute("INSERT INTO identity_users(user_id,issuer,subject,state) VALUES(%s,'s44-l2a',%s,'active')", (ctx.actor_id, ctx.actor_id))
            connection.execute("INSERT INTO identity_tenants(tenant_id) VALUES(%s)", (ctx.tenant_id,))
            connection.execute("INSERT INTO identity_memberships(tenant_id,user_id,role) VALUES(%s,%s,'organization_admin')", (ctx.tenant_id, ctx.actor_id))
            connection.execute("INSERT INTO identity_auth_workspaces VALUES(%s,%s,'organization','cloud_sync',1000,1,1,now())", (ctx.workspace_id, ctx.tenant_id))
            connection.execute("INSERT INTO identity_auth_tenant_roles VALUES(%s,%s,'organization_admin','active',1,now())", (ctx.tenant_id, ctx.actor_id))
            connection.execute("INSERT INTO identity_auth_tenant_policies VALUES(%s,1,now())", (ctx.tenant_id,))
            connection.execute("INSERT INTO identity_auth_workspace_policies VALUES(%s,%s,1,now())", (ctx.tenant_id, ctx.workspace_id))
            self._scope(connection)
            connection.execute("INSERT INTO notebooks(tenant_id,workspace_id,notebook_id,created_by,created_at) VALUES(%s,%s,%s,%s,now())",
                (ctx.tenant_id, ctx.workspace_id, self.notebook, ctx.actor_id))
            connection.execute("INSERT INTO notebook_metadata_versions(tenant_id,workspace_id,notebook_id,version,title,is_current,updated_by,updated_at) VALUES(%s,%s,%s,1,'S44 synthetic source',true,%s,now())",
                (ctx.tenant_id, ctx.workspace_id, self.notebook, ctx.actor_id))
            Canon._insert(connection, ctx, "sources", self.source_id, {"filename": "S44-synthetic.txt"}, state="registered")
            Canon._insert(connection, ctx, "source_versions", self.version_id,
                {"object_id": self.object_id, "content_digest_sha256": self.digest, "byte_size": len(self.content)},
                extra_columns=("source_id", "object_id"), extra_values=(self.source_id, self.object_id))
            connection.execute("INSERT INTO notebook_bindings(tenant_id,workspace_id,notebook_id,binding_kind,record_id,version_id,created_by,created_at) VALUES(%s,%s,%s,'source',%s,%s,%s,now())",
                (ctx.tenant_id, ctx.workspace_id, self.notebook, self.source_id, self.version_id, ctx.actor_id))
        self.resolver = recovery_postgres.PostgresBackupSelectionResolver(self.cloud)
        self.selection = BackupSelectionInput(self.notebook, (self.version_id,))

    def _cleanup_objects(self):
        # 이 시험이 제출한 정확 합성 key만 회수한다. bucket/DB 전체 삭제는 main 소유다.
        for key in sorted(self.object_keys):
            self.object_storage.delete(key)

    def _cleanup_files(self):
        if self.file_root.resolve() != self.file_root or self.file_root.is_symlink():
            raise AssertionError("S44_TEST_CLEANUP_BOUNDARY_INVALID")
        shutil.rmtree(self.file_root)
        self.assertFalse(self.file_root.exists())

    def _service(self) -> PostgresRecoveryService:
        assert DSN and MANIFEST_KEY
        service = PostgresRecoveryService(
            DSN,
            self.storage,
            manifest_key=MANIFEST_KEY,
            clock=lambda: datetime.now(timezone.utc),
            capture_store=self.capture_store,
        )
        self.addCleanup(service.close)
        return service

    def _create_selected(self, service, key):
        def current():
            resolved = self.resolver.resolve(self.context, self.selection, idempotency_key=key)
            return resolved, self.resolver.capture_metadata(self.context, resolved)
        resolved, metadata = current()
        return service.create_selected_backup(self.context, resolved, metadata,
            idempotency_key=key, recheck=current)

    def test_v2_capture_survives_source_removal_restart_but_materializer_is_unavailable(self) -> None:
        first = self._service()
        backup = self._create_selected(first, f"backup-{self.object_id}")
        self.assertEqual(backup.state, "ready")
        self.assertEqual(self.opens, [self.object_id])
        self.assertEqual(self.bytes_read[0], len(self.content))
        with self._connect() as connection:
            self._scope(connection)
            manifest = connection.execute("SELECT manifest_version,encrypted_inventory FROM backup_manifests WHERE backup_id=%s", (backup.backup_id,)).fetchone()
        self.assertEqual(manifest[0], 2)
        scope, receipt = decode_manifest(bytes(manifest[1]), master_key=MANIFEST_KEY,
            key_version="integration-v2", key_reference="DAON_RECOVERY_MANIFEST_KEY_FILE")
        self.object_storage.delete(ObjectKeyPolicy().final_key(self.context, "source", self.object_id))
        first.close()
        self.capture_store = FileCaptureStore(self.archive, master_key=MANIFEST_KEY,
            key_version="integration-v2", key_reference="DAON_RECOVERY_MANIFEST_KEY_FILE")
        restarted = self._service()
        persisted = restarted.get_backup(self.context, backup.backup_id)
        self.assertEqual(persisted.manifest_digest, backup.manifest_digest)
        result = self.capture_store.restore(scope, self.file_root / "restored", expected_receipt=receipt)
        self.assertEqual(result[self.object_id].read_bytes(), self.content)
        self.assertEqual(self.capture_store.verify(scope).metadata["source_versions"][0]["record_id"], self.version_id)
        # 등록 target/materializer가 없는 L2-A에서 preview_ready/completed를 만들지 않는다.
        with self.assertRaises(RecoveryError) as caught:
            restarted.create_restore_preview(self.context, backup.backup_id, destination=RestoreDestination(
                f"fixture-target-tenant-{self.object_id[:8]}",
                f"fixture-target-workspace-{self.object_id[:8]}",
                f"fixture-target-database-{self.object_id[:8]}",
                f"fixture-target-bucket-{self.object_id[:8]}",
            ), idempotency_key=f"preview-{self.object_id}", step_up_verified=True)
        self.assertEqual(caught.exception.status, 503)
        self.assertEqual(self.opens, [self.object_id], "원본 부재 이후에는 보존본만 읽어야 한다")
        with self._connect() as connection:
            self._scope(connection)
            self.assertEqual(connection.execute("SELECT count(*) FROM restore_requests WHERE tenant_id=%s AND workspace_id=%s",
                (self.tenant_id, self.workspace_id)).fetchone()[0], 0)

        wrong_workspace = RecoveryContext(
            self.tenant_id,
            f"wrong-{self.workspace_id}",
            self.actor_id,
            self.context.trace_id,
            self.context.policy_version,
            organization_admin=True,
        )
        with self.assertRaisesRegex(Exception, "BACKUP_UNAVAILABLE"):
            restarted.get_backup(wrong_workspace, backup.backup_id)
        with self.assertRaisesRegex(Exception, "BACKUP_UNAVAILABLE"):
            restarted.locate_backup_workspace(
                f"wrong-{self.tenant_id}", backup.backup_id
            )
        restarted.close()

    def test_real_minio_missing_and_corrupt_reads_never_create_ready(self) -> None:
        service = self._service()
        key = ObjectKeyPolicy().final_key(self.context, "source", self.object_id)
        self.object_storage.delete(key)
        with self.assertRaises(RecoveryError):
            self._create_selected(service, f"missing-{self.object_id}")
        self.assertEqual(self.opens, [self.object_id], "입구 503가 아니라 실제 MinIO open 시도여야 한다")
        self.assertEqual(self.bytes_read[0], 0)
        corrupt = b"x" * len(self.content)
        self.object_storage._client.put_object(BUCKET, key, io.BytesIO(corrupt), len(corrupt), content_type="application/octet-stream")
        with self.assertRaises(RecoveryError):
            self._create_selected(service, f"corrupt-{self.object_id}")
        self.assertEqual(self.opens, [self.object_id, self.object_id])
        self.assertEqual(self.bytes_read[0], len(corrupt), "손상 bytes를 실제 읽은 뒤 거부해야 한다")
        with self._connect() as connection:
            self._scope(connection)
            self.assertEqual(connection.execute("SELECT count(*) FROM backup_records WHERE tenant_id=%s AND workspace_id=%s",
                (self.tenant_id, self.workspace_id)).fetchone()[0], 0)
        self.assertEqual(list(self.archive.glob("*/receipt.bin")), [])

    def test_historical_v1_get_is_preserved_but_independent_preview_is_409(self):
        service = self._service()
        backup_id = "historical-v1-" + self.suffix
        envelope = service._encode_objects((BackupObjectInput(self.object_id, self.digest, len(self.content)),))
        # 과거 원장을 명시 seed한다. create_backup/v2 capture 성공을 가장하지 않는다.
        with service._transaction(self.tenant_id, self.workspace_id, self.actor_id) as connection:
            now = datetime.now(timezone.utc)
            revision = connection.execute("SELECT version_num FROM alembic_version").fetchone()["version_num"]
            audit = service._append_audit(connection, self.context, "backup.requested", "backup", backup_id)
            connection.execute("INSERT INTO backup_record_locator(tenant_id,backup_id,workspace_id,created_at) VALUES(%s,%s,%s,%s)",
                (self.tenant_id, backup_id, self.workspace_id, now))
            connection.execute("INSERT INTO backup_records(tenant_id,workspace_id,backup_id,actor_id,trigger_type,state,version,schema_revision,retention_watermark,policy_version,trace_id,audit_event_id,idempotency_key,request_fingerprint,created_at) VALUES(%s,%s,%s,%s,'manual','queued',1,%s,'historical-watermark',%s,%s,%s,%s,%s,%s)",
                (self.tenant_id, self.workspace_id, backup_id, self.actor_id, revision, self.context.policy_version, self.context.trace_id, audit, backup_id, "a" * 64, now))
            connection.execute("INSERT INTO backup_manifests(tenant_id,workspace_id,backup_id,manifest_version,manifest_digest,encrypted_inventory,object_count,created_at) VALUES(%s,%s,%s,1,%s,%s,1,%s)",
                (self.tenant_id, self.workspace_id, backup_id, hashlib.sha256(envelope).hexdigest(), envelope, now))
            for state in ("capturing", "verifying", "ready"):
                connection.execute("UPDATE backup_records SET state=%s,version=version+1,verified_at=%s WHERE backup_id=%s", (state, now, backup_id))
        self.assertEqual(service.get_backup(self.context, backup_id).state, "ready")
        with self.assertRaises(RecoveryError) as caught:
            service.create_restore_preview(self.context, backup_id,
                destination=RestoreDestination("fixture-tenant", "fixture-workspace", "fixture-db", "fixture-bucket"),
                idempotency_key="historical-preview-" + self.suffix, step_up_verified=True)
        self.assertEqual(caught.exception.status, 409)
        self.assertEqual(self.opens, [])

    def test_backup_and_restore_survive_service_restart_and_touch_real_stores(self) -> None:
        # 원업무 L3 합격 기준 보존: 현재 v1 준비 입구/실 materializer가 없어 NOT RUN이다.
        # 이 시험을 409/503 기대나 fake v2로 바꾸어 녹색화하지 않는다.
        first = self._service()
        backup = first.create_backup(
            self.context,
            trigger="manual",
            schema_revision="0006",
            retention_watermark="watermark-c02",
            objects=(BackupObjectInput(self.object_id, self.digest, len(self.content)),),
            idempotency_key=f"backup-{self.object_id}",
        )
        self.assertEqual(backup.state, "ready")
        first.close()
        restarted = self._service()
        persisted = restarted.get_backup(self.context, backup.backup_id)
        self.assertEqual(persisted.manifest_digest, backup.manifest_digest)
        preview = restarted.create_restore_preview(
            self.context,
            backup.backup_id,
            destination=RestoreDestination(
                f"fixture-target-tenant-{self.object_id[:8]}",
                f"fixture-target-workspace-{self.object_id[:8]}",
                f"fixture-target-database-{self.object_id[:8]}",
                f"fixture-target-bucket-{self.object_id[:8]}",
            ),
            idempotency_key=f"preview-{self.object_id}",
            step_up_verified=True,
        )
        restored = restarted.execute_restore(
            self.context,
            preview.request_id,
            expected_version=preview.version,
            preview_version=preview.preview.version,
            idempotency_key=f"execute-{self.object_id}",
            step_up_verified=True,
        )
        self.assertEqual(restored.state, "completed")
        self.assertEqual(self.storage.read_fixture(restored.preview.destination, self.object_id), self.content)
        with self._connect() as connection:
            connection.execute("SELECT set_config('app.tenant_id', %s, true)", (restored.preview.destination.tenant_id,))
            connection.execute("SELECT set_config('app.workspace_id', %s, true)", (restored.preview.destination.workspace_id,))
            target = connection.execute("SELECT digest_sha256,byte_size,status FROM object_records WHERE object_id=%s", (self.object_id,)).fetchone()
        self.assertEqual(target, (self.digest, len(self.content), "completed"))
        wrong_workspace = replace(self.context, workspace_id=f"wrong-{self.workspace_id}")
        with self.assertRaisesRegex(Exception, "BACKUP_UNAVAILABLE"):
            restarted.get_backup(wrong_workspace, backup.backup_id)
        with self.assertRaisesRegex(Exception, "BACKUP_UNAVAILABLE"):
            restarted.locate_backup_workspace(f"wrong-{self.tenant_id}", backup.backup_id)
        restarted.close()


class RecoveryRuntimeFailCloseTests(unittest.TestCase):
    def test_build_dependencies_never_installs_reference_recovery_fallback(self) -> None:
        import tempfile
        from pathlib import Path

        with tempfile.TemporaryDirectory() as directory:
            dependencies = build_dependencies(RuntimeSettings.for_test(
                database_path=Path(directory) / "runtime.sqlite3",
                policy_version="policy-c02",
            ))
            try:
                self.assertIsNotNone(dependencies.recovery_service)
                self.assertNotEqual(
                    type(dependencies.recovery_service).__name__, "RecoveryService"
                )
                with self.assertRaisesRegex(Exception, "RESOURCE_UNAVAILABLE"):
                    dependencies.recovery_service.create_backup(  # type: ignore[union-attr]
                        RecoveryContext(
                            "fixture-tenant", "fixture-workspace", "fixture-actor",
                            "fixture-trace", "policy-c02", organization_admin=True,
                        ),
                        trigger="manual", schema_revision="0006",
                        retention_watermark="fixture-watermark",
                        objects=(BackupObjectInput("a" * 32, "b" * 64, 1),),
                        idempotency_key="fixture-idempotency",
                    )
            finally:
                dependencies.close()


if __name__ == "__main__":
    unittest.main()
