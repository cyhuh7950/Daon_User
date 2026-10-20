from __future__ import annotations

import json
import tempfile
import unittest
from dataclasses import replace
from unittest.mock import patch
from pathlib import Path

import httpx

from daon_user_api.audit import AuditEventStore
from daon_user_api.authorization import (
    AuthorizationService,
    Role,
    SqliteAuthorizationRepository,
)
from daon_user_api.identity import ClientKind, DevicePlatform
from daon_user_api.recovery import (
    RecoveryService,
    ReferenceRecoveryRepository,
    ReferenceRestorePort,
)
from daon_user_api.runtime import (
    WEB_SESSION_COOKIE,
    RuntimeDependencies,
    RuntimeSettings,
    create_app,
)
from test_identity_support import (
    FakeVerifiedOidcProvider,
    POLICY_VERSION,
    TRACE_ID,
    create_service,
)


class RecoveryRuntimeHttpTests(unittest.IsolatedAsyncioTestCase):
    async def test_r4_execute_body_preview_snapshot_race_never_rebinds_old_authorization(self):
        """실제 Identity token/소비/ASGI. 재인증은 별도 직접 node, 업무 port만 대역."""
        import hashlib
        from datetime import timedelta
        from daon_user_api.recovery import RegisteredRestorePreviewView,RestoreRequestView,RecoveryError
        from daon_user_api.recovery_postgres import PostgresRecoveryService
        for mode in ("future-body","changed-after-consume"):
            with self.subTest(mode=mode):
                now=self.clock();writes=[];consumes=[]
                first=RegisteredRestorePreviewView("selected_source_lineage",1,now,now+timedelta(minutes=10),
                    dict(destination_id="destination-a",version=1,display_name="격리",expires_at=(now+timedelta(hours=1)).isoformat()),
                    "a"*64,"b"*64,1,0,dict(purged=0,held=0,tombstoned=0))
                latest=[first];service=object.__new__(PostgresRecoveryService)
                service.locate_restore_workspace=lambda *args:self.workspace_id
                def read(context,identity):
                    snapshot=RestoreRequestView("request-race","backup-a",context.tenant_id,context.workspace_id,"preview_ready",2,
                        latest[0],("requested","preview_ready"),None)
                    if mode=="future-body":latest[0]=replace(first,version=2)
                    return snapshot
                service.get_restore_request=read
                def execute(context,identity,**kwargs):
                    kwargs["recheck"]()
                    if kwargs["preview_version"]!=latest[0].version:raise RecoveryError("RESTORE_VERSION_CONFLICT",409)
                    writes.append(kwargs["preview_version"])
                    return replace(read(context,identity),state="completed",version=6)
                service.execute_registered_restore=execute
                token=self._step_up("request-race",registered_preview=first)
                actual=self.identity.consume_step_up
                def consume(**kwargs):
                    consumes.append(True);result=actual(**kwargs)
                    if mode=="changed-after-consume":latest[0]=replace(first,version=2)
                    return result
                with patch.object(self.identity,"consume_step_up",side_effect=consume):
                    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=create_app(replace(self.dependencies,recovery_service=service))),
                            base_url="https://app.example.com",cookies={WEB_SESSION_COOKIE:self.credentials.access_token}) as client:
                        response=await client.post("/api/v1/restore-requests/request-race/execute",json=dict(
                            preview_version=2 if mode=="future-body" else 1,step_up_authorization_id=token),
                            headers={"If-Match":'"restore:request-race:2"',"Idempotency-Key":"snapshot-race-"+mode})
                connection=self.identity_repository._connect()
                try:used=connection.execute("SELECT used_at FROM step_up_authorizations WHERE authorization_digest=?",
                    (hashlib.sha256(token.encode()).hexdigest(),)).fetchone()[0]
                finally:connection.close()
                self.assertEqual((response.status_code,writes),(409,[]))
                self.assertEqual(consumes,[] if mode=="future-body" else [True])
                self.assertEqual(used is None,mode=="future-body")

    async def test_r4_new_preview_rejects_unused_execute_step_up_issued_before_confirmation(self):
        """실제 Identity/재인증·ASGI/SQLite. recovery 업무쓰기 포트만 대역, 실PG 아님."""
        import secrets
        from datetime import timedelta
        from daon_user_api.identity import PASSWORD_HASHER
        from daon_user_api.recovery import RegisteredRestorePreviewView,RestoreRequestView,RecoveryError
        from daon_user_api.recovery_postgres import PostgresRecoveryService
        password=secrets.token_urlsafe(24)
        connection=self.identity_repository._connect()
        try:
            connection.execute("INSERT INTO users(user_id,issuer,subject,login_id,email,password_digest,email_verified_at,state) "
                "VALUES (?,?,?,?,?,?,?,'active')",("r4-local","local","r4-local","r4-local","r4-local@example.invalid",
                    PASSWORD_HASHER.hash(password),self.clock().isoformat()))
            connection.execute("INSERT INTO memberships(tenant_id,user_id,role) VALUES (?,?,?)",("tenant-001","r4-local","member"))
            connection.commit()
        finally:connection.close()
        credentials=self.identity.local_login(login_id="r4-local",password=password,platform=DevicePlatform.WEB,
            trace_id=TRACE_ID,policy_version=POLICY_VERSION)
        workspace="workspace-r4-new-confirmation"
        self.authorization_repository.bootstrap_workspace(tenant_id=credentials.tenant_id,workspace_id=workspace,
            owner_user_id=credentials.user_id,owner_role=Role.ORGANIZATION_ADMIN,workspace_kind="organization",data_area="cloud_sync",
            cost_limit_cents=1000,now=self.clock())
        service=object.__new__(PostgresRecoveryService);previews=[];writes=[];business={}
        service.locate_backup_workspace=lambda *args:workspace
        def locate(tenant,identity):
            if identity!="request-refresh":raise RecoveryError("RESTORE_REQUEST_UNAVAILABLE",404)
            return workspace
        service.locate_restore_workspace=locate
        def view(context):
            return RestoreRequestView("request-refresh","backup-refresh",context.tenant_id,workspace,"preview_ready",2,
                previews[-1],("requested","preview_ready"),None)
        def preview(context,identity,**kwargs):
            kwargs["recheck"]();now=self.clock()
            previews.append(RegisteredRestorePreviewView("selected_source_lineage",len(previews)+1,now,now+timedelta(minutes=10),
                dict(destination_id="destination-a",version=1,display_name="격리",expires_at=(now+timedelta(hours=1)).isoformat()),
                "a"*64,"b"*64,1,0,dict(purged=0,held=0,tombstoned=0)))
            return view(context)
        def execute(context,identity,**kwargs):
            kwargs["recheck"]()
            if kwargs["preview_version"]!=previews[-1].version:raise RecoveryError("RESTORE_VERSION_CONFLICT",409)
            self.assertTrue(kwargs["step_up_verified"])
            if kwargs["idempotency_key"] in business:return business[kwargs["idempotency_key"]]
            writes.append(kwargs["idempotency_key"])
            result=replace(view(context),state="completed",version=6);business[kwargs["idempotency_key"]]=result;return result
        service.get_restore_request=lambda context,*args:view(context)
        def protected(context,identity,*,recheck):
            recheck();return view(context)  # 현재 SourceACL/암호 Preview 실제 consumer는 별도 SQL 경계 시험.
        service.registered_restore_step_up_preview=protected
        service.preview_registered_restore=preview;service.execute_registered_restore=execute
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=create_app(replace(self.dependencies,recovery_service=service))),
                base_url="https://app.example.com",cookies={WEB_SESSION_COOKIE:credentials.access_token}) as client:
            async def issue(target,key):
                response=await client.post("/api/v1/session/step-up",json=dict(action_group="permanent_delete_or_restore_rollback",
                    target_id=target,password=password,ttl_seconds=300),headers={"Idempotency-Key":key})
                self.assertEqual(response.status_code,201)
                return response.json()["data"]["step_up_authorization"]
            async def confirm(key):
                token=await issue("backup-refresh","step-up-"+key)
                response=await client.post("/api/v1/backups/backup-refresh/restore-previews",json=dict(workspace_id=workspace,
                    destination_id="destination-a",destination_version=1,step_up_authorization_id=token),headers={"Idempotency-Key":key})
                self.assertEqual(response.status_code,201)
                return response
            first=await confirm("confirm-first-r4")
            self.clock.advance(seconds=1)
            old=await issue("request-refresh","step-up-old-execute-r4")
            self.clock.advance(seconds=1)
            with self.authorization_repository.transaction() as connection:
                connection.execute("UPDATE auth_tenant_policies SET version=version+1 WHERE tenant_id=?",(credentials.tenant_id,))
            second=await confirm("confirm-second-r4")
            self.assertEqual((first.json()["data"]["preview"]["version"],second.json()["data"]["preview"]["version"]),(1,2))
            self.assertEqual(first.headers["etag"],second.headers["etag"])
            response=await client.post("/api/v1/restore-requests/request-refresh/execute",json=dict(preview_version=2,step_up_authorization_id=old),
                headers={"If-Match":second.headers["etag"],"Idempotency-Key":"execute-old-confirmation"})
            with self.subTest(old_token="new-body-must-not-authorize"):
                self.assertEqual((response.status_code,writes),(403,[]))
            self.clock.advance(seconds=1)
            fresh=await issue("request-refresh","step-up-new-execute-r4")
            response=await client.post("/api/v1/restore-requests/request-refresh/execute",json=dict(preview_version=2,step_up_authorization_id=fresh),
                headers={"If-Match":second.headers["etag"],"Idempotency-Key":"execute-new-confirmation"})
            self.assertEqual(response.status_code,200)
            self.assertEqual(writes,["execute-new-confirmation"])
            for version,status in ((2,200),(1,409)):
                with self.subTest(consumed_replay_body=version):
                    replay=await client.post("/api/v1/restore-requests/request-refresh/execute",json=dict(preview_version=version,step_up_authorization_id=fresh),
                        headers={"If-Match":second.headers["etag"],"Idempotency-Key":"execute-new-confirmation"})
                    self.assertEqual(replay.status_code,status);self.assertEqual(writes,["execute-new-confirmation"])
            # NTP/고정시각을 freshness 증명으로 사용하지 않고 정확 Preview에 결속한다.
            for shift,label in ((0,"same-clock"),(-1,"reverse-clock")):
                self.clock.current=previews[-1].created_at
                unused=await issue("request-refresh","step-up-unused-"+label)
                self.clock.advance(seconds=shift)
                latest=await confirm("confirm-"+label)
                before=list(writes)
                denied=await client.post("/api/v1/restore-requests/request-refresh/execute",json=dict(preview_version=previews[-1].version,step_up_authorization_id=unused),
                    headers={"If-Match":latest.headers["etag"],"Idempotency-Key":"execute-unused-"+label})
                with self.subTest(clock=label):self.assertEqual((denied.status_code,writes),(403,before))
            consumed_old=await client.post("/api/v1/restore-requests/request-refresh/execute",json=dict(preview_version=previews[-1].version,step_up_authorization_id=fresh),
                headers={"If-Match":latest.headers["etag"],"Idempotency-Key":"execute-new-confirmation"})
            self.assertEqual((consumed_old.status_code,writes),(403,["execute-new-confirmation"]))
            # 예약 internal namespace는 어느 action_group에서도 client 직접 발급0.
            def issued_count():
                connection=self.identity_repository._connect()
                try:return connection.execute("SELECT count(*) FROM step_up_authorizations").fetchone()[0]
                finally:connection.close()
            for action in ("permanent_delete_or_restore_rollback","final_approval_or_knowledge_registration"):
                before=issued_count()
                denied=await client.post("/api/v1/session/step-up",json=dict(action_group=action,
                    target_id="rrexec-v1:"+"a"*64,password=password,ttl_seconds=300),headers={"Idempotency-Key":"prefix-denied-"+action})
                with self.subTest(prefix_action=action):self.assertEqual((denied.status_code,issued_count()),(400,before))
            self.identity_repository.add_step_up_action(credentials.tenant_id,"final_approval_or_knowledge_registration")
            other_resource=await client.post("/api/v1/session/step-up",json=dict(action_group="final_approval_or_knowledge_registration",
                target_id="studio-output-normal",password=password,ttl_seconds=300),headers={"Idempotency-Key":"other-feature-normal-step-up"})
            self.assertEqual(other_resource.status_code,201)
            connection=self.identity_repository._connect()
            try:
                self.assertEqual(connection.execute("SELECT target_id FROM step_up_authorizations WHERE authorization_digest=?",
                    (__import__("hashlib").sha256(other_resource.json()["data"]["step_up_authorization"].encode()).hexdigest(),)).fetchone()[0],"studio-output-normal")
            finally:connection.close()
            # 현재 POLICY_MANAGE는 유지하지만 자료 consumer가 거부하면 Identity 발급 자체0.
            before=issued_count()
            with patch.object(service,"registered_restore_step_up_preview",side_effect=RecoveryError("CURRENT_ACCESS_DENIED",403)):
                denied=await client.post("/api/v1/session/step-up",json=dict(action_group="permanent_delete_or_restore_rollback",
                    target_id="request-refresh",password=password,ttl_seconds=300),headers={"Idempotency-Key":"source-acl-revoked-issue"})
            self.assertEqual((denied.status_code,issued_count()),(403,before))
            for field,value in (("source_manifest_digest","c"*64),("closure_fingerprint","d"*64),
                    ("created_at",previews[-1].created_at+timedelta(microseconds=1))):
                original=previews[-1]
                token=await issue("request-refresh","step-up-field-"+field)
                previews[-1]=replace(original,**{field:value})
                before=list(writes)
                denied=await client.post("/api/v1/restore-requests/request-refresh/execute",json=dict(preview_version=original.version,step_up_authorization_id=token),
                    headers={"If-Match":latest.headers["etag"],"Idempotency-Key":"bound-field-"+field})
                with self.subTest(bound_field=field):self.assertEqual((denied.status_code,writes),(403,before))
                previews[-1]=original
            changed_issue=await client.post("/api/v1/session/step-up",json=dict(action_group="permanent_delete_or_restore_rollback",
                target_id="request-refresh",password=password,ttl_seconds=300),headers={"Idempotency-Key":"step-up-new-execute-r4"})
            self.assertEqual(changed_issue.status_code,409,"옛 발급key를 새 내부target으로 바꾸지 않음")
            # 변조·다른 logical resource/Preview token·만료·다른 정식 세션/장치 소비 거부.
            current_token=await issue("request-refresh","step-up-current-controls")
            backup_token=await issue("backup-refresh","step-up-preview-controls")
            for token,label in ((current_token+"x","tampered"),(backup_token,"preview")):
                before=list(writes)
                denied=await client.post("/api/v1/restore-requests/request-refresh/execute",json=dict(preview_version=previews[-1].version,step_up_authorization_id=token),
                    headers={"If-Match":latest.headers["etag"],"Idempotency-Key":"control-"+label})
                with self.subTest(token_case=label):self.assertEqual((denied.status_code,writes),(403,before))
            other=self.identity.local_login(login_id="r4-local",password=password,platform=DevicePlatform.WEB,trace_id=TRACE_ID,policy_version=POLICY_VERSION)
            self.assertNotEqual(other.session_id,credentials.session_id);self.assertNotEqual(other.device_id,credentials.device_id)
            async with httpx.AsyncClient(transport=httpx.ASGITransport(app=create_app(replace(self.dependencies,recovery_service=service))),
                    base_url="https://app.example.com",cookies={WEB_SESSION_COOKIE:other.access_token}) as different:
                denied=await different.post("/api/v1/restore-requests/request-refresh/execute",json=dict(preview_version=previews[-1].version,step_up_authorization_id=current_token),
                    headers={"If-Match":latest.headers["etag"],"Idempotency-Key":"other-session-control"})
                self.assertEqual(denied.status_code,403)
            async with httpx.AsyncClient(transport=httpx.ASGITransport(app=create_app(replace(self.dependencies,recovery_service=service))),
                    base_url="https://app.example.com",cookies={WEB_SESSION_COOKIE:self.credentials.access_token}) as other_actor:
                count=issued_count()
                foreign=await other_actor.post("/api/v1/session/step-up",json=dict(action_group="permanent_delete_or_restore_rollback",
                    target_id="request-refresh",password=password,ttl_seconds=300),headers={"Idempotency-Key":"foreign-actor-control"})
                self.assertEqual((foreign.status_code,issued_count()),(403,count))
            denied=await client.post("/api/v1/restore-requests/other-request/execute",json=dict(preview_version=previews[-1].version,step_up_authorization_id=current_token),
                headers={"If-Match":'"restore:other-request:2"',"Idempotency-Key":"other-request-control"})
            self.assertEqual(denied.status_code,404)
            before=list(writes);self.clock.advance(seconds=301)
            denied=await client.post("/api/v1/restore-requests/request-refresh/execute",json=dict(preview_version=previews[-1].version,step_up_authorization_id=current_token),
                headers={"If-Match":latest.headers["etag"],"Idempotency-Key":"expired-control"})
            self.assertEqual((denied.status_code,writes),(403,before))

    async def test_r4_heavy_cancel_rejects_next_write_recheck_without_retry(self):
        import asyncio,threading,time
        from daon_user_api.runtime import _recovery_heavy
        from daon_user_api.recovery import RecoveryError
        entered,release,done=threading.Event(),threading.Event(),threading.Event()
        calls=[];writes=[];errors=[]
        def work(*,recheck):
            calls.append(True);entered.set()
            try:
                release.wait(timeout=1);recheck();writes.append(True)
            except RecoveryError as error:errors.append((error.status,error.retryable))
            finally:done.set()
        pending=asyncio.create_task(_recovery_heavy(work,recheck=lambda:None))
        try:
            deadline=time.monotonic()+2
            while not entered.is_set() and time.monotonic()<deadline:await asyncio.sleep(0.001)
            self.assertTrue(entered.is_set());pending.cancel()
            with self.assertRaises(asyncio.CancelledError):await pending
        finally:
            release.set();self.assertTrue(await asyncio.to_thread(done.wait,2))
        self.assertEqual(calls,[True]);self.assertEqual(writes,[]);self.assertEqual(errors,[(503,False)])

    async def test_r4_heavy_preview_execute_leave_light_health_responsive(self):
        import asyncio,threading,time
        from datetime import timedelta
        from daon_user_api.recovery import RegisteredRestorePreviewView,RestoreRequestView
        from daon_user_api.recovery_postgres import PostgresRecoveryService
        now=self.clock()
        preview=RegisteredRestorePreviewView("selected_source_lineage",1,now,now+timedelta(minutes=10),
            dict(destination_id="destination-a",version=1,display_name="격리",expires_at=(now+timedelta(hours=1)).isoformat()),
            "a"*64,"b"*64,1,0,dict(purged=0,held=0,tombstoned=0))
        for mode in ("preview","execute","destinations"):
            with self.subTest(mode=mode):
                entered,release,finished=threading.Event(),threading.Event(),threading.Event()
                service=object.__new__(PostgresRecoveryService)
                service.locate_backup_workspace=service.locate_restore_workspace=lambda *args:self.workspace_id
                def view(context):return RestoreRequestView("request-a","backup-a",context.tenant_id,context.workspace_id,"preview_ready",2,preview,("requested","preview_ready"),None)
                service.get_restore_request=lambda context,*args:view(context)
                def work(context,*args,**kwargs):
                    entered.set()
                    try:
                        release.wait(timeout=1.0);kwargs["recheck"]()
                        return [] if mode=="destinations" else view(context)
                    finally:finished.set()
                service.preview_registered_restore=service.execute_registered_restore=service.list_restore_destinations=work
                async with httpx.AsyncClient(transport=httpx.ASGITransport(app=create_app(replace(self.dependencies,recovery_service=service))),
                    base_url="https://app.example.com",cookies={WEB_SESSION_COOKIE:self.credentials.access_token}) as client:
                    if mode=="preview":
                        pending=asyncio.create_task(client.post("/api/v1/backups/backup-a/restore-previews",
                            json=dict(workspace_id=self.workspace_id,destination_id="destination-a",destination_version=1,step_up_authorization_id=self._step_up("backup-a")),
                            headers={"Idempotency-Key":"r4-thread-preview"}))
                    elif mode=="execute":
                        pending=asyncio.create_task(client.post("/api/v1/restore-requests/request-a/execute",
                            json=dict(preview_version=1,step_up_authorization_id=self._step_up("request-a",registered_preview=preview)),
                            headers={"Idempotency-Key":"r4-thread-execute","If-Match":'"restore:request-a:2"'}))
                    else:pending=asyncio.create_task(client.get("/api/v1/backups/backup-a/restore-destinations"))
                    try:
                        deadline=time.monotonic()+2
                        while not entered.is_set() and time.monotonic()<deadline:await asyncio.sleep(0.001)
                        self.assertTrue(entered.is_set())
                        health=await asyncio.wait_for(client.get("/health/live"),timeout=0.5)
                        self.assertEqual(health.status_code,200)
                        self.assertFalse(finished.is_set(),"heavy 복원 완료 전에 가벼운 health 요청이 처리되어야 함")
                    finally:
                        release.set();result=await pending
                    self.assertEqual(result.status_code,201 if mode=="preview" else 200)

    async def test_r4_execute_dispatch_requires_request_step_up_exact_etag_and_current_identity(self):
        from daon_user_api import runtime
        from daon_user_api.recovery import RegisteredRestorePreviewView,RestoreRequestView,RecoveryError
        from daon_user_api.recovery_postgres import PostgresRecoveryService
        from daon_user_api.identity import IdentityError
        from daon_user_api.authorization import AuthorizationError
        from datetime import timedelta
        now=self.clock()
        preview=RegisteredRestorePreviewView("selected_source_lineage",1,now,now+timedelta(minutes=10),
            dict(destination_id="destination-a",version=1,display_name="격리",expires_at=(now+timedelta(hours=1)).isoformat()),
            "a"*64,"b"*64,1,0,dict(purged=0,held=0,tombstoned=0))
        for mode in ("normal","preview-token","changed","expired","denied","etag"):
            with self.subTest(mode=mode):
                service=object.__new__(PostgresRecoveryService);calls=[]
                service.locate_restore_workspace=lambda *args:self.workspace_id
                service.get_restore_request=lambda context,identity:RestoreRequestView("request-a","backup-a",context.tenant_id,context.workspace_id,"preview_ready",2,preview,("requested","preview_ready"),None)
                def execute(context,identity,*,expected_version,preview_version,idempotency_key,step_up_verified,recheck):
                    self.assertEqual((identity,expected_version,preview_version),("request-a",2,1));self.assertTrue(step_up_verified)
                    calls.append(True);recheck()
                    return RestoreRequestView(identity,"backup-a",context.tenant_id,context.workspace_id,"completed",6,preview,
                        ("requested","preview_ready","authorized","restoring","verifying","completed"),"c"*64)
                service.execute_registered_restore=execute
                service.execute_restore=lambda *args,**kwargs:(_ for _ in ()).throw(AssertionError("LEGACY_FIXTURE_MUST_NOT_EXECUTE_V3"))
                original=runtime._principal;authorize=self.authorization.authorize_action
                def principal(request,deps):
                    if calls and mode=="expired":raise IdentityError("ACCESS_EXPIRED",401)
                    value=original(request,deps)
                    return replace(value,device_id="different-device") if mode=="changed" and calls else value
                def authorized(*args,**kwargs):
                    if mode=="denied" and calls:raise AuthorizationError("ACTION_DENIED",403)
                    return authorize(*args,**kwargs)
                headers={"Idempotency-Key":"r4-execute-"+mode,"If-Match":'"restore:other-id:2"' if mode=="etag" else '"restore:request-a:2"'}
                body=dict(preview_version=1,step_up_authorization_id=self._step_up("backup-a") if mode=="preview-token" else self._step_up("request-a",registered_preview=preview))
                with patch.object(runtime,"_principal",side_effect=principal),patch.object(self.authorization,"authorize_action",side_effect=authorized):
                    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=create_app(replace(self.dependencies,recovery_service=service))),base_url="https://app.example.com",
                        cookies={WEB_SESSION_COOKIE:self.credentials.access_token}) as client:
                        result=await client.post("/api/v1/restore-requests/request-a/execute",json=body,headers=headers)
                # 다른 request-ID ETag는 기존 IF_MATCH_INVALID 형식 계약400을 유지한다.
                self.assertEqual(result.status_code,dict(normal=200,**{"preview-token":403},changed=403,expired=401,denied=403,etag=400)[mode])
                if mode in {"preview-token","etag"}:self.assertEqual(calls,[])
                if mode=="preview-token":
                    denial=[e for e in self.audit.list(tenant_id=self.credentials.tenant_id).items if e.action=="identity.step_up.binding_denied"][-1]
                    observed=denial.metadata["operations_scope"]
                    self.assertEqual((observed["workspace_id"],observed["resource_type"],observed["resource_id"]),(self.workspace_id,"workspace",self.workspace_id))
                    self.assertEqual(observed["source_version_ids"],())
                if mode=="normal":self.assertEqual(result.headers["etag"],'"restore:request-a:6"')

    async def test_r4_registered_preview_real_step_up_exact_body_session_and_off(self):
        from daon_user_api import runtime
        from daon_user_api.recovery import RegisteredRestorePreviewView,RestoreRequestView
        from daon_user_api.recovery_postgres import PostgresRecoveryService
        from datetime import timedelta
        for mode in ("normal","changed","off","workspace"):
            with self.subTest(mode=mode):
                service=object.__new__(PostgresRecoveryService);called=[]
                service.locate_backup_workspace=lambda tenant,backup:self.workspace_id
                def preview(context,backup_id,*,destination_id,destination_version,idempotency_key,step_up_verified,recheck):
                    self.assertTrue(step_up_verified)
                    self.assertEqual((destination_id,destination_version),("destination-a",1))
                    called.append(True);recheck()
                    now=self.clock()
                    return RestoreRequestView("request-a",backup_id,context.tenant_id,context.workspace_id,"preview_ready",2,
                        RegisteredRestorePreviewView("selected_source_lineage",1,now,now+timedelta(minutes=10),
                            dict(destination_id="destination-a",version=1,display_name="격리",expires_at=(now+timedelta(hours=1)).isoformat()),
                            "a"*64,"b"*64,1,0,dict(purged=0,held=0,tombstoned=0)),("requested","preview_ready"),None)
                if mode!="off": service.preview_registered_restore=preview
                original=runtime._principal
                def principal(request,dependencies):
                    value=original(request,dependencies)
                    return replace(value,session_id="changed-session") if mode=="changed" and called else value
                body=dict(workspace_id="wrong-workspace" if mode=="workspace" else self.workspace_id,destination_id="destination-a",destination_version=1,
                    step_up_authorization_id=self._step_up("backup-current"))
                with patch.object(runtime,"_principal",side_effect=principal):
                    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=create_app(replace(self.dependencies,recovery_service=service))),
                        base_url="https://app.example.com",cookies={WEB_SESSION_COOKIE:self.credentials.access_token}) as client:
                        result=await client.post("/api/v1/backups/backup-current/restore-previews",json=body,headers={"Idempotency-Key":"preview-r4-"+mode})
                self.assertEqual(result.status_code,dict(normal=201,changed=403,off=503,workspace=403)[mode])
                if mode=="normal":
                    self.assertEqual(result.headers["etag"],'"restore:request-a:2"')
                    self.assertEqual(result.json()["data"]["preview"]["kind"],"selected_source_lineage")
                if mode=="workspace": self.assertEqual(called,[])

    async def test_r4_destination_get_uses_locator_current_session_and_safe_four_fields(self):
        from types import SimpleNamespace
        from daon_user_api import runtime
        from daon_user_api.recovery import RecoveryError
        from daon_user_api.recovery_postgres import PostgresRecoveryService
        for mode in ("normal","changed","off"):
            with self.subTest(mode=mode):
                service=object.__new__(PostgresRecoveryService); called=[]
                service.locate_backup_workspace=lambda tenant,backup:self.workspace_id
                def destinations(context,backup_id,*,recheck):
                    self.assertEqual((context.actor_id,context.session_id,context.device_id),
                        (self.credentials.user_id,self.credentials.session_id,self.credentials.device_id))
                    self.assertEqual(backup_id,"backup-current")
                    called.append(True); recheck()
                    if mode=="off": raise RecoveryError("RESOURCE_UNAVAILABLE",503)
                    return [dict(destination_id="destination-a",version=1,display_name="격리 목적지",expires_at="2026-10-20T00:00:00Z",
                        raw_ref="S44_NEVER_REFLECT")]
                service.list_restore_destinations=destinations
                deps=replace(self.dependencies,recovery_service=service)
                original=runtime._principal
                def principal(request,dependencies):
                    value=original(request,dependencies)
                    return replace(value,session_id="changed-session") if mode=="changed" and called else value
                with patch.object(runtime,"_principal",side_effect=principal):
                    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=create_app(deps)),
                        base_url="https://app.example.com",cookies={WEB_SESSION_COOKIE:self.credentials.access_token}) as client:
                        result=await client.get("/api/v1/backups/backup-current/restore-destinations")
                self.assertEqual(result.status_code,{"normal":200,"changed":403,"off":503}[mode])
                self.assertNotIn("S44_NEVER_REFLECT",result.text)
                if mode=="normal":
                    self.assertEqual(set(result.json()["data"][0]),{"destination_id","version","display_name","expires_at"})
                    self.assertEqual(result.json()["data"][0]["destination_id"],"destination-a")
                    self.assertIn("etag",result.headers)

    async def test_r1c_late_real_auth_exceptions_preserve_mapping_and_cleanup_uncertainty(self):
        import hashlib
        import io
        import secrets
        from types import SimpleNamespace
        from test_recovery_lineage import Ledger, RowCursor, recanonical
        from daon_user_api import runtime
        from daon_user_api.authorization import AuthorizationError
        from daon_user_api.identity import IdentityError
        from daon_user_api.recovery import RecoveryContext
        from daon_user_api.recovery_lineage import resolve_lineage_selection
        from daon_user_api.recovery_lineage_capture import FileLineageCaptureStore, LineageCaptureScope
        from daon_user_api.recovery_postgres import PostgresRecoveryService
        ctx=RecoveryContext(self.credentials.tenant_id,self.workspace_id,self.credentials.user_id,"trace-late",POLICY_VERSION,True,
            session_id=self.credentials.session_id,device_id=self.credentials.device_id)
        ledger=Ledger();ledger.context=ctx;ledger.auth.auth_role="organization_admin"
        content={"object-a":b"prior synthetic bytes","object-b":b"root synthetic bytes"}
        for rows in ledger.tables.values():
            for row in rows: row.update(tenant_id=ctx.tenant_id,workspace_id=ctx.workspace_id)
        for row in ledger.tables["object_records"]:
            raw=content[row["object_id"]];row.update(byte_size=len(raw),digest_sha256=hashlib.sha256(raw).hexdigest())
        for row in ledger.tables["source_versions"]:
            obj=next(x for x in ledger.tables["object_records"] if x["object_id"]==row["object_id"])
            recanonical(row,dict(object_id=obj["object_id"],byte_size=obj["byte_size"],content_digest_sha256=obj["digest_sha256"]))
        class Reader:
            info=ledger.info
            def execute(inner,sql,params=()):
                if "current_setting('app.actor_id'" in sql: return RowCursor([(ctx.actor_id,"")])
                return ledger.execute(sql,params)
        def resolve(context,selection): return resolve_lineage_selection(context,selection,Reader())
        selection=resolve(ctx,ledger.selection)
        body=dict(workspace_id=self.workspace_id,notebook_id="notebook-a",source_version_ids=["root-version"],lineage_mode="exact",
            trigger="manual",closure_consent=dict(include_prior_versions=True,closure_fingerprint=selection.fingerprint))
        original_principal=runtime._principal; original_authorize=self.authorization.authorize_action
        for mode,status,code in (("missing",401,"AUTHENTICATION_REQUIRED"),("expired",401,"AUTHENTICATION_REQUIRED"),
                ("denied",403,"FORBIDDEN"),("scope",403,"CURRENT_ACCESS_DENIED"),("uncertain",503,"RESOURCE_UNAVAILABLE")):
            with self.subTest(mode=mode), tempfile.TemporaryDirectory(prefix="r1c-late-auth-") as directory:
                root=Path(directory); store=FileLineageCaptureStore(root,master_key=secrets.token_bytes(32),key_version="test-key",key_reference="memory-only")
                armed=[];actual_types=[];sources=[]
                original_verify=store._verify_data; original_unlink=store._fs.unlink
                def verified(*args,**kwargs):
                    result=original_verify(*args,**kwargs);armed.append(True);return result
                def principal(request,dependencies):
                    if armed and mode=="expired":
                        error=IdentityError("ACCESS_EXPIRED",401);actual_types.append(type(error));raise error
                    value=original_principal(request,dependencies)
                    return replace(value,session_id="changed-during-capture") if armed and mode=="scope" else value
                def authorize(*args,**kwargs):
                    if armed and mode in {"denied","uncertain"}:
                        error=AuthorizationError("ACTION_DENIED",403);actual_types.append(type(error));raise error
                    return original_authorize(*args,**kwargs)
                def unlink(path):
                    if mode=="uncertain" and path.name=="data.bin": raise OSError("SYNTHETIC_CLEANUP_DETAIL")
                    return original_unlink(path)
                service=object.__new__(PostgresRecoveryService)
                def capture(context,actual,**kwargs):
                    scope=LineageCaptureScope.from_selection(context,actual,capture_id="late-auth")
                    def source(ref): sources.append(ref.object_id);return io.BytesIO(content[ref.object_id])
                    store.capture(scope,actual,source,closure_fingerprint=actual.fingerprint,include_prior_versions=True,
                        recheck=kwargs["recheck"],owner_id="writer-owned")
                    self.fail("late denial must not return ready")
                service.create_lineage_backup=capture
                deps=replace(self.dependencies,cloud_store=SimpleNamespace(),recovery_service=service)
                resolver=SimpleNamespace(resolve_lineage=resolve)
                with patch.object(runtime,"PostgresBackupSelectionResolver",return_value=resolver),patch.object(runtime,"_principal",side_effect=principal), \
                        patch.object(self.authorization,"authorize_action",side_effect=authorize),patch.object(store,"_verify_data",side_effect=verified), \
                        patch.object(store._fs,"unlink",side_effect=unlink):
                    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=create_app(deps)),base_url="https://app.example.com",
                        cookies={} if mode=="missing" else {WEB_SESSION_COOKIE:self.credentials.access_token}) as client:
                        result=await client.post("/api/v1/backups",json=body,headers={"Idempotency-Key":"r1c-late-auth"})
                self.assertEqual((result.status_code,result.json()["error"]["code"]),(status,code))
                self.assertNotIn("SYNTHETIC_CLEANUP_DETAIL",result.text)
                self.assertFalse(result.json()["error"]["retryable"])
                folder=root/"lineage-v3-late-auth"
                if mode=="missing": self.assertEqual(sources,[]);self.assertFalse(folder.exists())
                else:
                    self.assertTrue(armed);self.assertEqual(sources,["object-a","object-b"])
                    self.assertFalse((folder/"receipt.bin").exists())
                    self.assertEqual((root/"lineage-v3.lock").exists(),mode=="uncertain")
                    self.assertEqual((folder/"data.bin").exists(),mode=="uncertain")
                if mode=="expired": self.assertEqual(actual_types,[IdentityError])
                if mode in {"denied","uncertain"}: self.assertEqual(actual_types,[AuthorizationError])
                self.assertEqual(self.recovery._repository.backups,{})

    async def test_r1c_preflight_exact_projection_and_session_recheck_no_business_write(self):
        from types import SimpleNamespace
        from unittest.mock import Mock
        from test_recovery_lineage import Ledger
        from daon_user_api import runtime
        selection = Ledger().resolve()
        expected = {"root_source_version_ids", "dependency_source_version_ids", "dependency_source_ids", "source_version_count",
            "source_count", "unique_object_count", "total_bytes", "metadata_bytes", "closure_fingerprint"}
        body = dict(workspace_id=self.workspace_id, notebook_id="notebook-a", source_version_ids=["root-version"], lineage_mode="exact")
        for mode in ("normal", "session", "device"):
            resolver = SimpleNamespace(resolve_lineage=Mock(return_value=selection))
            deps = replace(self.dependencies, cloud_store=SimpleNamespace())
            original, calls = runtime._principal, []
            def principal(request, dependencies):
                value = original(request, dependencies); calls.append(True)
                if mode != "normal" and len(calls) > 1:
                    return replace(value, **{mode + "_id": "changed"})
                return value
            with patch.object(runtime, "PostgresBackupSelectionResolver", return_value=resolver), patch.object(runtime, "_principal", side_effect=principal):
                async with httpx.AsyncClient(transport=httpx.ASGITransport(app=create_app(deps)), base_url="https://app.example.com",
                    cookies={WEB_SESSION_COOKIE: self.credentials.access_token}) as client:
                    result = await client.post("/api/v1/backups/selection-previews", json=body)
            self.assertEqual(result.status_code, 200 if mode == "normal" else 403)
            if mode == "normal":
                self.assertEqual(set(result.json()["data"]), expected)
                self.assertEqual(result.json()["data"]["closure_fingerprint"], selection.fingerprint)
                self.assertIn("etag", result.headers)
                context, actual = resolver.resolve_lineage.call_args.args
                self.assertEqual((context.tenant_id, context.workspace_id, context.actor_id),
                    (self.credentials.tenant_id, self.workspace_id, self.credentials.user_id))
                self.assertEqual(actual.source_version_ids, ("root-version",))
            self.assertEqual(self.recovery._repository.backups, {})

    async def test_r1c_create_consumes_consent_and_rechecks_without_fake_ready(self):
        from types import SimpleNamespace
        from unittest.mock import Mock
        from test_recovery_lineage import Ledger
        from daon_user_api import runtime
        from daon_user_api.recovery import RecoveryError
        from daon_user_api.recovery_postgres import PostgresRecoveryService
        selection = Ledger().resolve()
        body = dict(workspace_id=self.workspace_id, notebook_id="notebook-a", source_version_ids=["root-version"],
            lineage_mode="exact", trigger="manual", closure_consent=dict(include_prior_versions=True, closure_fingerprint=selection.fingerprint))
        service = object.__new__(PostgresRecoveryService)
        def capture(context, actual, **kwargs):
            self.assertEqual(actual, selection)
            self.assertEqual(kwargs["recheck"](), selection)
            raise RecoveryError("RESOURCE_UNAVAILABLE", 503)
        service.create_lineage_backup = Mock(side_effect=capture)
        resolver = SimpleNamespace(resolve_lineage=Mock(return_value=selection))
        deps = replace(self.dependencies, recovery_service=service, cloud_store=SimpleNamespace())
        with patch.object(runtime, "PostgresBackupSelectionResolver", return_value=resolver):
            async with httpx.AsyncClient(transport=httpx.ASGITransport(app=create_app(deps)), base_url="https://app.example.com",
                cookies={WEB_SESSION_COOKIE: self.credentials.access_token}) as client:
                result = await client.post("/api/v1/backups", json=body, headers={"Idempotency-Key": "r1c-explicit-create"})
                self.assertEqual(result.status_code, 503)
                self.assertEqual(service.create_lineage_backup.call_count, 1)
                result = await client.post("/api/v1/backups", json={**body, "closure_consent": {"include_prior_versions": True, "closure_fingerprint": "f"*64}},
                    headers={"Idempotency-Key": "r1c-wrong-consent"})
                self.assertEqual(result.status_code, 409)
                self.assertEqual(service.create_lineage_backup.call_count, 1)
        original=runtime._principal
        for field in ("session_id","device_id","user_id"):
            checks=[];service.create_lineage_backup.reset_mock()
            def current(request,dependencies):
                value=original(request,dependencies);checks.append(True)
                return replace(value,**{field:"changed"}) if len(checks)>=4 else value
            with patch.object(runtime,"PostgresBackupSelectionResolver",return_value=resolver),patch.object(runtime,"_principal",side_effect=current):
                async with httpx.AsyncClient(transport=httpx.ASGITransport(app=create_app(deps)),base_url="https://app.example.com",
                    cookies={WEB_SESSION_COOKIE:self.credentials.access_token}) as client:
                    result=await client.post("/api/v1/backups",json=body,headers={"Idempotency-Key":"r1c-late-session"})
            self.assertEqual(result.status_code,403)
            self.assertEqual(service.create_lineage_backup.call_count,1)
        self.assertEqual(self.recovery._repository.backups, {})

    async def test_m1_observations_preserve_auth_failure_and_recheck_session(self):
        from types import SimpleNamespace
        from daon_user_api import runtime
        from daon_user_api.operations_status import OperationsStatusService, OperationsCounts, unknown_observations
        from daon_user_api.authorization import AuthorizationError
        from daon_user_api.identity import IdentityError
        from daon_user_api.notification import NotificationError
        for mode, expected in (("read", 200), ("auth401", 401), ("auth403", 403),
                ("notification403", 403), ("notification-contract", 503), ("changed", 403)):
            class Repository:
                def read_counts(inner, context): return OperationsCounts(0, 0, 0)
                def read_observations(inner, context):
                    self.assertEqual(context.trace_id, "trace-m1-current-request")
                    self.assertEqual(context.policy_version, self.dependencies.settings.policy_version)
                    if mode == "auth401": raise IdentityError("EXPIRED_SESSION", 401)
                    if mode == "auth403": raise AuthorizationError("ACTION_DENIED", 403)
                    if mode == "notification403": raise NotificationError("ACCESS_DENIED", 403)
                    if mode == "notification-contract": raise NotificationError("DATABASE_RESPONSE_INVALID", 503)
                    return unknown_observations()
            deps = replace(self.dependencies, operations_status_service=OperationsStatusService(Repository()))
            original, calls = runtime._principal, []
            def principal(request, dependencies):
                value = original(request, dependencies); calls.append(True)
                return replace(value, session_id="changed-session") if mode == "changed" and len(calls) > 1 else value
            with patch.object(runtime, "_principal", side_effect=principal):
                async with httpx.AsyncClient(transport=httpx.ASGITransport(app=create_app(deps)), base_url="https://app.example.com",
                    cookies={WEB_SESSION_COOKIE: self.credentials.access_token}) as client:
                    result = await client.get(f"/api/v1/workspaces/{self.workspace_id}/operations/status",
                        headers={"x-trace-id": "trace-m1-current-request"})
            self.assertEqual(result.status_code, expected, mode)
            # 기존 Notification handler는 내부 DB 오류를 RESOURCE_UNAVAILABLE로 안전 투영한다.
            if mode == "notification-contract": self.assertEqual(result.json()["error"]["code"], "RESOURCE_UNAVAILABLE")
            if expected == 200:
                self.assertEqual(len(result.json()["data"]["observations"]), 16)
                self.assertEqual(len(result.json()["data"]["components"]), 5)

    async def test_l2_selection_passes_exact_resolver_metadata_and_rechecks_session_before_capture_commit(self) -> None:
        """ASGI 포트 연결 시험. 실제 capture/PG/ready 성공을 대체하지 않는다."""
        from types import SimpleNamespace
        from unittest.mock import Mock
        from daon_user_api import runtime
        from daon_user_api.recovery import BackupObjectInput, BackupSelectionInput, RecoveryError, _ResolvedBackupSelection
        from daon_user_api.recovery_postgres import PostgresRecoveryService
        resolved = _ResolvedBackupSelection(BackupSelectionInput("notebook-a", ("version-a",)),
            "schema-a", "watermark-a", (BackupObjectInput("a" * 32, "a" * 64, 7),), "a" * 64)
        metadata = {"synthetic-port-only": True}
        for changed in (False, True):
            service = object.__new__(PostgresRecoveryService)
            reached = []
            def capture(context, actual, relations, **kwargs):
                self.assertEqual((actual, relations), (resolved, metadata))
                current, final = kwargs["recheck"]()
                self.assertEqual((current, final), (resolved, metadata))
                reached.append(True)
                raise RecoveryError("RESOURCE_UNAVAILABLE", 503)  # 실제 파일 port는 이 HTTP 시험에 주입하지 않는다.
            service.create_selected_backup = Mock(side_effect=capture)
            resolver = SimpleNamespace(resolve=Mock(return_value=resolved), capture_metadata=Mock(return_value=metadata))
            dependencies = replace(self.dependencies, recovery_service=service, cloud_store=SimpleNamespace())
            original = runtime._principal
            calls = []
            def principal(request, deps):
                current = original(request, deps)
                calls.append(True)
                return replace(current, session_id="changed-session") if changed and len(calls) > 1 else current
            with patch.object(runtime, "PostgresBackupSelectionResolver", return_value=resolver), patch.object(runtime, "_principal", side_effect=principal):
                async with httpx.AsyncClient(transport=httpx.ASGITransport(app=create_app(dependencies)),
                    base_url="https://app.example.com", cookies={WEB_SESSION_COOKIE: self.credentials.access_token}) as client:
                    result = await client.post("/api/v1/backups", headers={"Idempotency-Key": "l2-callback"},
                        json={"workspace_id": self.workspace_id, "notebook_id": "notebook-a", "trigger": "manual", "source_version_ids": ["version-a"]})
            self.assertEqual(service.create_selected_backup.call_count, 1)
            self.assertEqual(result.status_code, 403 if changed else 503)
            self.assertEqual(len(reached), 0 if changed else 1)

    async def test_l1_durable_runtime_legacy_without_capture_is_503_not_reference_ready(self) -> None:
        from daon_user_api.recovery import UnavailableRecoveryService
        unavailable = UnavailableRecoveryService()
        isolated_dependencies = replace(self.dependencies, recovery_service=unavailable, cloud_store=None)
        # 기존 별도 Reference 7route 시험의 성공을 durable runtime fallback으로 사용하지 않는다.
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=create_app(isolated_dependencies)),
            base_url="https://app.example.com", cookies={WEB_SESSION_COOKIE: self.credentials.access_token}) as client:
            with patch.object(unavailable, "create_backup", side_effect=AssertionError("inventory-only create forbidden")) as create:
                result = await client.post("/api/v1/backups", headers={"Idempotency-Key": "l1-legacy-no-capture"},
                    json={"workspace_id": self.workspace_id, "trigger": "manual", "schema_revision": "0006",
                          "retention_watermark": "fixture-watermark", "objects": [{"object_id": "fixture-object-http", "checksum_sha256": "a" * 64, "byte_size": 128}]})
                self.assertEqual(result.status_code, 503)
                self.assertEqual(result.json()["error"]["code"], "RESOURCE_UNAVAILABLE")
                create.assert_not_called()
        self.assertEqual(self.recovery._repository.backups, {})

    async def test_l1_selection_without_capture_never_uses_inventory_ready_fallback(self) -> None:
        body = {"workspace_id": self.workspace_id, "notebook_id": "notebook-a", "trigger": "manual",
                "source_version_ids": ["version-a"]}
        result = await self.client.post("/api/v1/backups", headers={"Idempotency-Key": "l1-no-capture"}, json=body)
        self.assertEqual(result.status_code, 503)
        self.assertEqual(result.json()["error"]["code"], "RESOURCE_UNAVAILABLE")
        self.assertEqual(self.recovery._repository.backups, {})
        for changes in ({"source_version_ids": []}, {"source_version_ids": ["v"] * 2},
                        {"source_version_ids": [f"v-{i}" for i in range(101)]}, {"source_version_ids": "v"},
                        {"source_version_ids": [1]}, {"trigger": "automatic"}, {"schema_revision": "forged"}):
            result = await self.client.post("/api/v1/backups", headers={"Idempotency-Key": "l1-invalid"}, json={**body, **changes})
            self.assertEqual(result.status_code, 400)
        self.assertEqual(self.recovery._repository.backups, {})

    async def asyncSetUp(self) -> None:
        self.directory = tempfile.TemporaryDirectory(prefix="daon-s44-l1-runtime-")
        self.db_path = Path(self.directory.name) / "runtime.sqlite3"
        self.audit = AuditEventStore()
        self.identity, self.identity_repository, _, self.clock = create_service(
            self.db_path, audit_store=self.audit
        )
        login = self.identity.begin_oidc_login(
            issuer="https://login.example.com", client_id="daon-web",
            audience="daon-user-api", redirect_uri="https://app.example.com/auth/callback",
            client_kind=ClientKind.WEB, tenant_id="tenant-001",
            trace_id=TRACE_ID, policy_version=POLICY_VERSION,
        )
        provider = FakeVerifiedOidcProvider()
        provider.expected_nonce = login.nonce
        self.credentials = self.identity.complete_oidc_login(
            state=login.state, authorization_code=provider.authorization_code,
            code_verifier=login.code_verifier, client_id="daon-web",
            redirect_uri="https://app.example.com/auth/callback", provider=provider,
            platform=DevicePlatform.WEB, trace_id=TRACE_ID,
            policy_version=POLICY_VERSION,
        )
        self.workspace_id = "workspace-recovery-http"
        self.authorization_repository = SqliteAuthorizationRepository(self.db_path)
        self.authorization_repository.bootstrap_workspace(
            tenant_id=self.credentials.tenant_id, workspace_id=self.workspace_id,
            owner_user_id=self.credentials.user_id, owner_role=Role.ORGANIZATION_ADMIN,
            workspace_kind="organization", data_area="cloud_sync",
            cost_limit_cents=1000, now=self.clock(),
        )
        self.authorization = AuthorizationService(
            repository=self.authorization_repository, audit_store=self.audit,
            clock=self.clock, identity_service=self.identity,
        )
        self.restore_port = ReferenceRestorePort()
        self.recovery = RecoveryService(
            ReferenceRecoveryRepository(), self.restore_port, clock=self.clock
        )
        settings = RuntimeSettings.for_test(
            database_path=self.db_path, policy_version=POLICY_VERSION
        )
        self.dependencies = RuntimeDependencies(
            settings=settings, identity_service=self.identity,
            authorization_service=self.authorization, audit_store=self.audit,
            identity_repository=self.identity_repository,
            authorization_repository=self.authorization_repository,
            recovery_service=self.recovery,
        )
        self.client = httpx.AsyncClient(
            transport=httpx.ASGITransport(app=create_app(self.dependencies)),
            base_url="https://app.example.com",
            cookies={WEB_SESSION_COOKIE: self.credentials.access_token},
        )

    async def asyncTearDown(self) -> None:
        await self.client.aclose()
        self.dependencies.close()
        self.directory.cleanup()

    def _step_up(self, target_id: str, *, registered_preview=None) -> str:
        if registered_preview is not None:
            import hashlib
            raw=json.dumps(["registered-restore-execute-step-up-v1",target_id,registered_preview.version,
                registered_preview.source_manifest_digest,registered_preview.closure_fingerprint,registered_preview.created_at.isoformat()],
                ensure_ascii=False,separators=(",",":")).encode("utf-8")
            target_id="rrexec-v1:"+hashlib.sha256(raw).hexdigest()
        return self.identity.issue_step_up(
            access_token=self.credentials.access_token,
            action_group="permanent_delete_or_restore_rollback", target_id=target_id,
            policy_version=POLICY_VERSION, trace_id=TRACE_ID,
        ).authorization

    async def test_seven_route_flow_requires_two_step_ups_and_isolates_restore(self) -> None:
        created = await self.client.post(
            "/api/v1/backups", headers={"Idempotency-Key": "idem-backup-http"},
            json={
                "workspace_id": self.workspace_id,
                "trigger": "manual",
                "schema_revision": "0006",
                "retention_watermark": "fixture-watermark",
                "objects": [{
                    "object_id": "fixture-object-http",
                    "checksum_sha256": "a" * 64,
                    "byte_size": 128,
                }],
            },
        )
        self.assertEqual(created.status_code, 201)
        backup_id = created.json()["data"]["backup_id"]
        self.assertEqual(created.json()["data"]["state"], "ready")

        listed = await self.client.get(
            "/api/v1/backups", params={"workspace_id": self.workspace_id}
        )
        fetched = await self.client.get(f"/api/v1/backups/{backup_id}")
        self.assertEqual((listed.status_code, fetched.status_code), (200, 200))
        self.assertEqual(listed.json()["data"][0]["backup_id"], backup_id)

        destination = {
            "tenant_id": "fixture-tenant-http",
            "workspace_id": "fixture-workspace-http",
            "database_id": "fixture-database-http",
            "bucket_id": "fixture-bucket-http",
        }
        preview_step_up = self._step_up(backup_id)
        preview = await self.client.post(
            f"/api/v1/backups/{backup_id}/restore-previews",
            headers={"Idempotency-Key": "idem-preview-http"},
            json={
                "destination": destination,
                "step_up_authorization_id": preview_step_up,
            },
        )
        self.assertEqual(preview.status_code, 201)
        request_id = preview.json()["data"]["request_id"]
        preview_version = preview.json()["data"]["preview"]["version"]

        reused = await self.client.post(
            f"/api/v1/restore-requests/{request_id}/execute",
            headers={"Idempotency-Key": "idem-execute-reused", "If-Match": preview.headers["etag"]},
            json={
                "preview_version": preview_version,
                "step_up_authorization_id": preview_step_up,
            },
        )
        self.assertEqual(reused.status_code, 403)

        executed = await self.client.post(
            f"/api/v1/restore-requests/{request_id}/execute",
            headers={"Idempotency-Key": "idem-execute-http", "If-Match": preview.headers["etag"]},
            json={
                "preview_version": preview_version,
                "step_up_authorization_id": self._step_up(request_id),
            },
        )
        self.assertEqual(executed.status_code, 200)
        self.assertEqual(executed.json()["data"]["state"], "completed")
        fetched_restore = await self.client.get(f"/api/v1/restore-requests/{request_id}")
        self.assertEqual(fetched_restore.json()["data"]["state"], "completed")
        self.assertEqual(self.restore_port.restored_object_ids, ["fixture-object-http"])
        self.assertEqual(self.restore_port.original_mutations, 0)

        openapi_path = Path(__file__).resolve().parents[3] / "packages/contracts/openapi/v1/openapi.json"
        safe_codes = set(json.loads(openapi_path.read_text(encoding="utf-8"))[
            "components"
        ]["schemas"]["SafeErrorCode"]["enum"])
        self.assertIn(reused.json()["error"]["code"], safe_codes)
        combined = created.text + preview.text + reused.text + executed.text
        for forbidden in (str(self.db_path), "fixture/content/path", "content_digest", "secret"):
            self.assertNotIn(forbidden.lower(), combined.lower())


if __name__ == "__main__":
    unittest.main()
