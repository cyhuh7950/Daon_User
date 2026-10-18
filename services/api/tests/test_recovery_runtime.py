from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from contextlib import nullcontext

from daon_user_api.runtime import RuntimeSettings, build_dependencies, create_app


class RecoveryRuntimeContractTests(unittest.TestCase):
    def test_r4_startup_config_from_env_default_off_and_api_only(self):
        import os
        from unittest.mock import patch
        from daon_user_api import runtime
        with patch.dict(os.environ,{"DAON_RECOVERY_RESTORE_CONFIG_FILE":"D:/fixed/operator.json"}):
            settings=RuntimeSettings.from_env()
        self.assertEqual(getattr(settings,"recovery_restore_config_file",None),Path("D:/fixed/operator.json"))
        with patch.dict(os.environ,{},clear=True):
            self.assertIsNone(getattr(RuntimeSettings.from_env(),"recovery_restore_config_file",None))
        source=Path(runtime.__file__).parent
        callers=[p.name for p in source.glob("*.py") if p.name!="runtime.py" and "build_dependencies(" in p.read_text(encoding="utf-8")]
        self.assertEqual(callers,["main.py"],"operator Secret 소비는 API entry만")

    def test_r4_startup_closed_configuration_and_owned_lazy_ports(self):
        import copy,json,secrets
        from unittest.mock import patch
        from daon_user_api import recovery_postgres as api
        loader=getattr(api,"_load_registered_restore_ports",None)
        self.assertTrue(callable(loader),"startup fixed configuration consumer missing")
        with tempfile.TemporaryDirectory(prefix="r4-startup-") as directory:
            root=Path(directory)
            instance=dict(instance_ref="source-a",database_ref="source-db",bucket_ref="source-bucket",database_name="source_db",
                operator_dsn_file=str(root/"operator-ref"),app_dsn_file=None,object_endpoint="object.invalid:9000",
                object_bucket="s44-source",object_region="us-east-1",object_secure=True,
                object_access_key_file=str(root/"access-ref"),object_secret_key_file=str(root/"secret-ref"),
                provisioning_ref=None,foundation=None,application_enabled=True,outbound_enabled=True)
            target={**instance,"instance_ref":"target-a","database_ref":"target-db","bucket_ref":"target-bucket",
                "database_name":"target_db","app_dsn_file":str(root/"app-ref"),"object_bucket":"s44-target",
                "provisioning_ref":"operator-v1","foundation":dict(tenant_id="tenant-a",workspace_id="workspace-a",actor_id="actor-a",
                    schema_revision="0069",studio_defaults_protection_digest="a"*64),"application_enabled":False,"outbound_enabled":False}
            config=dict(schema_version=1,source_instance_ref="source-a",registry_root=str(root/"registry"),instances=[instance,target],
                targets=[dict(registry_generation_id="generation-a",destination_id="destination-a",destination_version=1,
                    target_instance_ref="target-a",owner_operation_id="owner-a",catalog_digest="b"*64)])
            entry=dict(destination_id="destination-a",version=1,source_instance_ref="source-a",target_instance_ref="target-a",
                database_ref="target-db",bucket_ref="target-bucket",owner_operation_id="owner-a",authorized_tenant_id="tenant-a",
                authorized_workspace_id="workspace-a",authorized_actor_id="actor-a",capture_kind="selected-source-lineage-v3")
            def read(path,limit):
                name=Path(path).name
                if name=="config":return json.dumps(config).encode()
                if name in {"operator-ref","app-ref"}:return b"host=pg.invalid dbname="+(b"target_db" if name=="app-ref" else (b"source_db"))
                return secrets.token_hex(24).encode()
            #  두 operator ref는 서로 다른 fixed DB로 읽도록 분리한다. 실제 자격 파일 쓰기0.
            target["operator_dsn_file"]=str(root/"target-operator-ref")
            base_read=read
            def read(path,limit):
                if Path(path).name=="target-operator-ref":return b"host=pg.invalid dbname=target_db"
                if Path(path).name=="memory-key-ref":return key
                return base_read(path,limit)
            key=secrets.token_bytes(32)
            with patch.object(api,"_read_restore_protected_file",side_effect=read),patch.object(api.SignedDestinationRegistry,"_read",return_value=("generation-a",[entry])):
                ports=loader(root/"config",master_key=key,key_version="v1")
                registry,probe,factory,owned=ports
                self.assertEqual(set(probe._instances),{"source-a","target-a"})
                self.assertEqual(len(factory._descriptors),1)
                pools=[resource for resource in owned if hasattr(resource,"closed")]
                self.assertEqual(len(pools),2);self.assertTrue(all(p.closed for p in pools))
                queue=next(iter(factory._descriptors.values())).queue
                self.assertTrue(queue._pool.closed,"startup performs no target network")
                # 공급자는 대여만: 실제 probe가 최상위 REPEATABLE READ Tx를 소유한다.
                from contextlib import contextmanager
                events=[];depth=[0];broken=[False]
                class Session:
                    @contextmanager
                    def transaction(inner):
                        self.assertEqual(depth[0],0,"provider가 열어 둔 Tx/SAVEPOINT 금지")
                        depth[0]+=1;events.append("consumer-begin")
                        try:yield
                        finally:depth[0]-=1;events.append("consumer-end")
                    def execute(inner,sql,params=()):
                        self.assertEqual(depth[0],1)
                        if sql=="SET TRANSACTION ISOLATION LEVEL REPEATABLE READ, READ ONLY":
                            if broken[0]:raise RuntimeError("synthetic-consumer-failure")
                            return SimpleNamespace()
                        self.assertIn("pg_catalog.pg_control_system()",sql)
                        return SimpleNamespace(fetchone=lambda:("source_db","10001",123,None,True,False))
                @contextmanager
                def borrowed(**kwargs):
                    self.assertEqual(kwargs,{"timeout":2});events.append("borrow")
                    try:yield Session()
                    finally:events.append("return")
                source_instance=probe._instances["source-a"]
                with patch.object(pools[0],"connection",side_effect=borrowed),patch.object(pools[0],"open"):
                    with source_instance.connection_provider():
                        self.assertEqual(depth[0],0,"provider 진입만으로 BEGIN하지 않음")
                    self.assertEqual(events,["borrow","return"]);events.clear()
                    measured,_=probe._database(source_instance)
                    self.assertEqual((measured.system_identifier,measured.database_oid),("10001",123))
                    self.assertEqual(events,["borrow","consumer-begin","consumer-end","return"]);events.clear()
                    broken[0]=True
                    with self.assertRaisesRegex(RuntimeError,"^synthetic-consumer-failure$"):
                        probe._database(source_instance)
                    self.assertEqual(events,["borrow","consumer-begin","consumer-end","return"])
                    self.assertEqual(depth[0],0)
                from dataclasses import replace
                from daon_user_api import runtime
                settings=replace(RuntimeSettings.for_test(database_path=root/"runtime.sqlite",policy_version="policy-a"),
                    cloud_database_dsn="dbname=never_connect",recovery_manifest_key_file=root/"memory-key-ref",
                    recovery_capture_root=root,recovery_capture_key_version="v1",recovery_capture_key_reference="DAON_RECOVERY_MANIFEST_KEY_FILE",
                    recovery_restore_config_file=root/"config")
                closes=[]
                for resource in owned:
                    original_close=resource.close
                    resource.close=lambda resource=resource,original_close=original_close:(closes.append(id(resource)),original_close())[1]
                with patch.object(Path,"read_bytes",return_value=key),patch.object(api,"_load_registered_restore_ports",return_value=ports):
                    service=runtime._build_recovery_service(settings,SimpleNamespace())
                self.assertIs(service._destination_registry,registry);self.assertIs(service._registered_target_factory,factory)
                service.close();service.close()
                self.assertEqual(sorted(closes),sorted(id(r) for r in owned),"서비스 shutdown에 소유 자원 각1회")
                # loader가 만든 자원만, 중간 생성 실패에서도 모두 회수한다.
                created=[];original_pool=api.ConnectionPool
                def pool(*args,**kwargs):
                    value=original_pool(*args,**kwargs);created.append(value)
                    value.close=__import__("unittest.mock",fromlist=["Mock"]).Mock(wraps=value.close)
                    return value
                with patch.object(api,"ConnectionPool",side_effect=pool),patch.object(api,"MinioObjectStorageAdapter",side_effect=ValueError("PRIVATE_CANARY")):
                    with self.assertRaisesRegex(ValueError,"^REGISTERED_RESTORE_CONFIG_INVALID$"):
                        loader(root/"config",master_key=key,key_version="v1")
                self.assertEqual(len(created),1);created[0].close.assert_called_once()
                for mutation in (lambda:config.update(extra=True),lambda:target.update(object_region=None),
                        lambda:target.update(application_enabled=True),lambda:target["foundation"].update(extra=True),
                        lambda:config["targets"][0].update(destination_version=True),lambda:config.update(source_instance_ref="unknown"),
                        lambda:config.update(instances=[instance]*65),lambda:target.update(app_dsn_file=None)):
                    original=copy.deepcopy(config)
                    mutation()
                    with self.subTest(mutation=repr(mutation)),self.assertRaisesRegex(ValueError,"^REGISTERED_RESTORE_CONFIG_INVALID$"):
                        loader(root/"config",master_key=key,key_version="v1")
                    config.clear();config.update(original);instance,target=config["instances"]

    def test_r4_startup_duplicate_json_secret_limits_and_protected_file_rejection(self):
        import json,os,secrets
        from unittest.mock import patch
        from daon_user_api import recovery_postgres as api
        from test_recovery_destinations import protect_windows
        with tempfile.TemporaryDirectory(prefix="r4-protected-") as directory:
            root=Path(directory);protect_windows(root)
            path=root/"benign-reference";path.write_bytes(b"benign-utf8-reference");protect_windows(path)
            # 현재 D:의 상위 ACL은 안전 기준을 통과하지 않음. 제품 guard는 그대로 거부.
            if os.name=="nt":
                with self.assertRaisesRegex(ValueError,"^REGISTERED_RESTORE_CONFIG_INVALID$"):
                    api._read_restore_protected_file(path,4096)
            original=api._windows_protected
            def exact_file_acl(item):
                if Path(item) in {root,path}:original(item)
            # 상위 ACL만 명시 대역, 실제 pinned file/ACL/size/hardlink를 검증한다.
            with patch.object(api,"_windows_protected",side_effect=exact_file_acl):
                self.assertEqual(api._read_restore_protected_file(path,4096),b"benign-utf8-reference")
                with self.assertRaisesRegex(ValueError,"^REGISTERED_RESTORE_CONFIG_INVALID$"):
                    api._read_restore_protected_file(path,1)
                link=root/"other-name";os.link(path,link)
                try:
                    with self.assertRaisesRegex(ValueError,"^REGISTERED_RESTORE_CONFIG_INVALID$"):
                        api._read_restore_protected_file(path,4096)
                finally:link.unlink()
                protect_windows(path,broad_write=True)
                with self.assertRaisesRegex(ValueError,"^REGISTERED_RESTORE_CONFIG_INVALID$"):
                    api._read_restore_protected_file(path,4096)
            for raw in (b'{"schema_version":1,"schema_version":1}',b'{"schema_version":NaN}',b'[]',b'\xff'):
                with patch.object(api,"_read_restore_protected_file",return_value=raw),patch.object(api,"ConnectionPool") as pool:
                    with self.assertRaisesRegex(ValueError,"^REGISTERED_RESTORE_CONFIG_INVALID$"):
                        api._load_registered_restore_ports(root/"config",master_key=secrets.token_bytes(32),key_version="v1")
                    pool.assert_not_called()

    def test_r4_startup_unset_builder_keeps_existing_api_and_reads_no_operator_refs(self):
        from unittest.mock import patch
        from daon_user_api import recovery_postgres as api
        with tempfile.TemporaryDirectory(prefix="r4-off-") as directory,patch.object(api,"_read_restore_protected_file") as read:
            settings=RuntimeSettings.for_test(database_path=Path(directory)/"runtime.sqlite",policy_version="policy-a")
            dependencies=build_dependencies(settings)
            try:
                self.assertIsNotNone(create_app(dependencies));read.assert_not_called()
            finally:dependencies.close();dependencies.close()

    def test_r4_registered_preview_exact_four_and_legacy_oneof(self):
        from daon_user_api.runtime import RestorePreviewBody
        body=dict(workspace_id="workspace-a",destination_id="destination-a",destination_version=1,
            step_up_authorization_id="opaque-step-up")
        self.assertEqual(RestorePreviewBody.model_validate(body).model_dump(),body)
        legacy=dict(destination=dict(tenant_id="fixture-t",workspace_id="fixture-w",database_id="fixture-d",bucket_id="fixture-b"),
            step_up_authorization_id="legacy-token")
        self.assertEqual(RestorePreviewBody.model_validate(legacy).model_dump(),legacy)
        for change in ({"destination_version":True},{"destination_version":0},{"destination_version":"1"},
                {"workspace_id":"../bad"},{"destination_id":"x"*129},{"destination":legacy["destination"]},
                {"registry_generation_id":"client-forged"},{"actor_id":"archive-actor"},{"step_up_authorization_id":None}):
            with self.subTest(change=next(iter(change))),self.assertRaises(ValueError):
                RestorePreviewBody.model_validate({**body,**change})
        for key in body:
            with self.subTest(missing=key),self.assertRaises(ValueError):
                RestorePreviewBody.model_validate({k:v for k,v in body.items() if k!=key})

    def test_r1c_existing_configuration_injects_both_purposes_and_default_off(self):
        from dataclasses import replace
        from unittest.mock import patch
        import secrets
        from daon_user_api import runtime
        with tempfile.TemporaryDirectory(prefix="r1c-di-") as directory:
            root=Path(directory); manifest_path=root/"existing-protected-key-ref"
            settings=replace(RuntimeSettings.for_test(database_path=root/"runtime.sqlite3",policy_version="policy-a"),
                cloud_database_dsn="dbname=never_connect",recovery_manifest_key_file=manifest_path,
                recovery_capture_root=root,recovery_capture_key_version="v3-test",recovery_capture_key_reference="DAON_RECOVERY_MANIFEST_KEY_FILE")
            memory_key=secrets.token_bytes(32)
            with patch.object(Path,"read_bytes",return_value=memory_key):
                service=runtime._build_recovery_service(settings,SimpleNamespace())
                try:
                    self.assertEqual(service._capture_store.root,root)
                    self.assertEqual(service._lineage_capture_store.root,root)
                    self.assertEqual(service._lineage_capture_store.key_version,"v3-test")
                    self.assertTrue(service._pool.closed)
                finally: service.close()
                default=runtime._build_recovery_service(replace(settings,recovery_capture_root=None,recovery_capture_key_version=None,recovery_capture_key_reference=None),SimpleNamespace())
                try:
                    self.assertIsNone(default._capture_store);self.assertIsNone(default._lineage_capture_store)
                    self.assertTrue(default._pool.closed)
                finally: default.close()
            self.assertFalse(manifest_path.exists(),"시험 key 파일 생성0")

    def test_r1c_exact_preflight_and_lineage_create_inputs(self):
        from daon_user_api import runtime
        preflight = dict(workspace_id="workspace-a", notebook_id="notebook-a", source_version_ids=["version-a"], lineage_mode="exact")
        cls = getattr(runtime, "LineageSelectionPreviewBody", None)
        self.assertIsNotNone(cls, "exact4 사전 확인 DTO 필요")
        self.assertEqual(cls.model_validate(preflight).model_dump(), preflight)
        consent = dict(include_prior_versions=True, closure_fingerprint="a" * 64)
        create = {**preflight, "trigger": "manual", "closure_consent": consent}
        self.assertEqual(runtime.BackupCreateBody.model_validate(create).model_dump(), create)
        for change in ({"trigger": "automatic"}, {"schema_revision": "forged"}, {"objects": []},
                {"closure_consent": {**consent, "include_prior_versions": False}},
                {"closure_consent": {**consent, "extra": True}}, {"closure_consent": {**consent, "closure_fingerprint": "x"}},
                {"source_version_ids": ["v", "v"]}, {"source_version_ids": ["x" * 129]}, {"lineage_mode": "loose"}):
            with self.subTest(change=next(iter(change))), self.assertRaises(ValueError):
                runtime.BackupCreateBody.model_validate({**create, **change})
        for change in ({"trigger": "manual"}, {"closure_consent": consent}, {"source_version_ids": []},
                {"source_version_ids": ["v"] * 101}, {"source_version_ids": [1]}, {"workspace_id": "../bad"}):
            with self.subTest(preflight=next(iter(change))), self.assertRaises(ValueError): cls.model_validate({**preflight, **change})

    def test_l1_legacy_workspace_shape_has_no_notebook_and_exposes_multinotebook_resolver_gap(self) -> None:
        from daon_user_api.recovery import BackupObjectInput, RecoveryContext, RecoveryError
        from daon_user_api.recovery_postgres import PostgresBackupSelectionResolver
        from daon_user_api.runtime import BackupCreateBody, LegacyBackupCreateBody
        objects = [dict(object_id=f"object-{i}", checksum_sha256="a" * 64, byte_size=1) for i in range(101)]
        # 기존 Desktop 소비도 Notebook 필드 없이 workspace의 objects 집합을 전달한다.
        body = BackupCreateBody.model_validate(dict(workspace_id="workspace-a", trigger="manual",
            schema_revision="revision-a", retention_watermark="watermark-a", objects=objects))
        self.assertIsInstance(body.root, LegacyBackupCreateBody)
        self.assertEqual(len(body.root.objects), 101)
        self.assertNotIn("notebook_id", body.model_dump())
        queries = []
        class Reader:
            def execute(self, sql, params=()):
                queries.append(sql)
                candidates = [("notebook-a", "version-a", "object-a"), ("notebook-b", "version-b", "object-b")]
                return SimpleNamespace(fetchall=lambda: candidates, fetchone=lambda: None)
        store = SimpleNamespace(readiness=lambda: SimpleNamespace(ready=True, schema_revision="revision-a"),
            _transaction=lambda context: nullcontext(Reader()))
        resolver = PostgresBackupSelectionResolver(store)
        ctx = RecoveryContext("tenant-a", "workspace-a", "actor-a", "trace-a", "policy-a", True)
        with self.assertRaises(RecoveryError) as caught:
            resolver.resolve_legacy(ctx, schema_revision="revision-a", retention_watermark="watermark-a",
                objects=(BackupObjectInput("object-a", "a" * 64, 1), BackupObjectInput("object-b", "a" * 64, 1)))
        self.assertEqual((caught.exception.code, caught.exception.status), ("BACKUP_UNAVAILABLE", 404))
        self.assertFalse(any(query.startswith("SELECT sv.record_id") for query in queries),
                         "현재 단일Notebook 가정은 정본/ACL 검증 전에 유효한 workspace 묶음을 거부하는 미해결 경계")

    def test_l1_context_retention_preserves_existing_fields_without_recovery_scope(self) -> None:
        from daon_user_api.identity import IdentityPrincipal
        from daon_user_api.retention import RetentionContext
        from daon_user_api.runtime import _retention_context
        principal = IdentityPrincipal("actor-a", "session-a", "device-a", "tenant-a")
        request = SimpleNamespace(state=SimpleNamespace(trace_id="trace-a"))
        dependencies = SimpleNamespace(settings=SimpleNamespace(policy_version="policy-a"))
        self.assertEqual(_retention_context(principal, "workspace-a", request, dependencies),
                         RetentionContext("tenant-a", "workspace-a", "actor-a", "trace-a", "policy-a", False))

    def test_l1_context_recovery_binds_exact_principal_session_and_device(self) -> None:
        from daon_user_api.identity import IdentityPrincipal
        from daon_user_api.runtime import _recovery_context
        principal = IdentityPrincipal("actor-a", "session-a", "device-a", "tenant-a")
        request = SimpleNamespace(state=SimpleNamespace(trace_id="trace-a"))
        dependencies = SimpleNamespace(settings=SimpleNamespace(policy_version="policy-a"))
        result = _recovery_context(principal, "workspace-a", request, dependencies)
        self.assertEqual((result.tenant_id, result.actor_id, result.workspace_id, result.session_id, result.device_id),
                         (principal.tenant_id, principal.user_id, "workspace-a", principal.session_id, principal.device_id))

    def test_l1_exact_selection_body_and_runtime_one_of(self) -> None:
        from daon_user_api.runtime import BackupCreateBody
        body = {"workspace_id": "workspace-a", "notebook_id": "notebook-a", "trigger": "manual",
                "source_version_ids": ["version-a"]}
        self.assertEqual(BackupCreateBody.model_validate(body).model_dump(), body)
        for extra in ({"objects": []}, {"schema_revision": "forged"}, {"unexpected": "hidden"}):
            with self.assertRaises(ValueError):
                BackupCreateBody.model_validate({**body, **extra})
        schema = BackupCreateBody.model_json_schema()
        self.assertEqual(len(schema["oneOf"]), 3)
        self.assertNotIn("anyOf", schema)

    def test_runtime_registers_exact_approved_cloud_recovery_paths(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            dependencies = build_dependencies(RuntimeSettings.for_test(
                database_path=Path(directory) / "runtime.sqlite3",
                policy_version="recovery-policy-v1",
            ))
            try:
                paths = create_app(dependencies).openapi()["paths"]
            finally:
                dependencies.close()
        expected = {
            "/api/v1/backups/selection-previews": {"post"},
            "/api/v1/backups": {"post", "get"},
            "/api/v1/backups/{id}": {"get"},
            "/api/v1/backups/{id}/restore-previews": {"post"},
            "/api/v1/restore-requests/{id}": {"get"},
            "/api/v1/restore-requests/{id}/execute": {"post"},
            "/api/v1/restore-requests/{id}/cancel": {"post"},
        }
        actual = {
            path: set(paths[path]) & {"get", "post", "put", "patch", "delete"}
            for path in paths if path.startswith("/api/v1/backups")
            or path.startswith("/api/v1/restore-requests")
        }
        self.assertEqual(actual, expected)


if __name__ == "__main__":
    unittest.main()
