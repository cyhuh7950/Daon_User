"""실제 로컬 Identity/Authorization QA. 외부 인증/DB 서버 호출 없음."""
from dataclasses import replace
from datetime import datetime,timedelta,timezone
import hashlib,json,secrets
import pytest
from daon_user_api.identity import IdentityError,DevicePlatform,PASSWORD_HASHER
from daon_user_api.authorization import AuthorizationService,AuthorizationError,SqliteAuthorizationRepository,Role
from daon_user_api.routing_sources import BudgetContext,BudgetPreconditions,RoutingSourceError
from daon_user_api.routing_budget_reauthentication import BudgetReauthenticationAdapter
from test_identity_support import create_service,MutableClock

@pytest.fixture
def qa(tmp_path):
 clock=MutableClock(datetime(2026,10,21,tzinfo=timezone.utc));path=tmp_path/'identity.sqlite3'
 identity,repository,audit,_=create_service(path,clock=clock)
 password=secrets.token_urlsafe(28)
 with repository.transaction() as conn:
  conn.execute("INSERT INTO tenants(tenant_id) VALUES ('tenant-001')")
  conn.execute("INSERT INTO users(user_id,issuer,subject,login_id,email,password_digest,email_verified_at,state) VALUES (?,?,?,?,?,?,?,'active')",('owner','local','qa-owner','qa-owner','qa@example.com',PASSWORD_HASHER.hash(password),clock().isoformat()))
  conn.execute("INSERT INTO memberships VALUES ('tenant-001','owner','member')")
  conn.execute('CREATE TABLE tenant_memberships(tenant_id TEXT,user_id TEXT,state TEXT)')
  conn.execute("INSERT INTO tenant_memberships VALUES ('tenant-001','owner','active')")
 credentials=identity.local_login(login_id='qa-owner',password=password,platform=DevicePlatform.WEB,trace_id='trace-qa',policy_version='policy-qa')
 auth_repo=SqliteAuthorizationRepository(path);auth_repo.bootstrap_workspace(tenant_id='tenant-001',workspace_id='workspace-001',owner_user_id='owner',owner_role=Role.ORGANIZATION_ADMIN,workspace_kind='organization',data_area='cloud_sync',cost_limit_cents=0,now=clock())
 authorization=AuthorizationService(repository=auth_repo,audit_store=audit,clock=clock,identity_service=identity)
 adapter=BudgetReauthenticationAdapter(identity,authorization)
 principal=identity.validate_access(credentials.access_token,trace_id='trace-qa',policy_version='policy-qa')
 context=BudgetContext.from_principal(principal,workspace_id='workspace-001',trace_id='trace-qa',policy_version='policy-qa')
 yield dict(identity=identity,repo=repository,auth_repo=auth_repo,auth=authorization,adapter=adapter,ctx=context,credentials=credentials,password=password,clock=clock,path=path)
 repository.close()

def binding(q,**changes):
 arguments=dict(context=q['ctx'],operation='routing_budget.issue',body_digest='a'*64,preconditions=BudgetPreconditions(0,if_none_match='*'),idempotency_key='issue-key-0000001')
 return q['adapter'].binding(**{**arguments,**changes})

def counts(q):
 with q['repo'].transaction() as conn:return tuple(conn.execute('SELECT count(*) FROM '+name).fetchone()[0] for name in ('step_up_authorizations','step_up_consumptions'))

def test_real_password_required_and_exact_target_consume_replay_writes_once(qa):
 q=qa;b=binding(q)
 with pytest.raises(IdentityError) as wrong:q['adapter'].issue(q['ctx'],b,access_token=q['credentials'].access_token,password=secrets.token_urlsafe(28))
 assert wrong.value.code=='STEP_UP_REAUTH_REQUIRED' and counts(q)==(0,0)
 proof=q['adapter'].issue(q['ctx'],b,access_token=q['credentials'].access_token,password=q['password'])
 assert b.target_id.startswith('routing-budget-issue:') and len(b.target_id)<128
 q['adapter'].consume(q['ctx'],b,access_token=q['credentials'].access_token,proof=proof)
 assert counts(q)==(1,1)
 q['clock'].advance(minutes=6)
 q['adapter'].verify_consumed(q['ctx'],b,access_token=q['credentials'].access_token,proof=proof)
 assert counts(q)==(1,1)

@pytest.mark.parametrize('change',[{'body_digest':'b'*64},{'idempotency_key':'issue-key-0000002'},{'operation':'routing_budget.revoke'},{'preconditions':BudgetPreconditions(1,if_match='"budget:1"')}])
def test_changed_body_key_operation_or_preconditions_cannot_reuse_proof(qa,change):
 q=qa;b=binding(q);proof=q['adapter'].issue(q['ctx'],b,access_token=q['credentials'].access_token,password=q['password'])
 with pytest.raises(IdentityError):q['adapter'].consume(q['ctx'],binding(q,**change),access_token=q['credentials'].access_token,proof=proof)
 assert counts(q)==(1,0)

def test_initial_expiry_and_current_session_role_workflow_revoke_fail_closed(qa):
 q=qa;b=binding(q);proof=q['adapter'].issue(q['ctx'],b,access_token=q['credentials'].access_token,password=q['password'])
 q['clock'].advance(minutes=6)
 with pytest.raises(IdentityError) as expired:q['adapter'].consume(q['ctx'],b,access_token=q['credentials'].access_token,proof=proof)
 assert expired.value.code=='STEP_UP_EXPIRED' and counts(q)==(1,0)
 with q['auth_repo'].transaction() as conn:conn.execute("UPDATE auth_tenant_roles SET state='inactive'")
 with pytest.raises(AuthorizationError):q['adapter'].current(q['ctx'],access_token=q['credentials'].access_token)

def test_new_session_or_forged_actor_cannot_use_old_binding(qa):
 q=qa;b=binding(q);proof=q['adapter'].issue(q['ctx'],b,access_token=q['credentials'].access_token,password=q['password'])
 other=q['identity'].local_login(login_id='qa-owner',password=q['password'],platform=DevicePlatform.WEB,trace_id='trace-qa',policy_version='policy-qa')
 with pytest.raises(RoutingSourceError):q['adapter'].current(q['ctx'],access_token=other.access_token)
 with pytest.raises(RoutingSourceError):q['adapter'].current(replace(q['ctx'],actor_id='other'),access_token=q['credentials'].access_token)
 assert counts(q)==(1,0)


@pytest.mark.parametrize('change',[dict(operation='routing_budget.revoke'),dict(target_id='routing-budget-issue:'+'b'*64),dict(fingerprint='b'*64),dict(idempotency_key='issue-key-0000002')])
def test_direct_binding_replacement_cannot_forge_other_operation_or_material(qa,change):
 b=binding(qa)
 with pytest.raises(RoutingSourceError):replace(b,**change)
