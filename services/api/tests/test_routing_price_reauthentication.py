"""실제 로컬 Identity 가격전용 재인증. 서버/실사용자/외부호출0."""
from dataclasses import replace
import pytest
from test_routing_budget_reauthentication import qa,counts
from daon_user_api.identity import IdentityError
from daon_user_api.authorization import AuthorizationError
from daon_user_api.routing_pricing import PricePreconditions
from daon_user_api.routing_pricing import RoutingPriceStorageError
from daon_user_api.routing_price_reauthentication import PriceReauthenticationAdapter

@pytest.fixture
def price_qa(qa):
 q=qa;q['price_adapter']=PriceReauthenticationAdapter(q['identity'],q['auth']);return q

def binding(q,**changes):
 args=dict(operation='routing_price.register',body_digest='a'*64,target_digest='b'*64,preconditions=PricePreconditions(0,if_none_match='*'),idempotency_key='price-register-key-0001')
 return q['price_adapter'].binding(q['ctx'],**{**args,**changes})

def test_password_reauth_current_and_exact_consumed_replay(price_qa):
 q=price_qa;a=q['price_adapter'];b=binding(q)
 with pytest.raises(IdentityError):a.issue(q['ctx'],b,access_token=q['credentials'].access_token,password='not-the-random-password')
 assert counts(q)==(0,0)
 proof=a.issue(q['ctx'],b,access_token=q['credentials'].access_token,password=q['password'])
 a.consume(q['ctx'],b,access_token=q['credentials'].access_token,proof=proof)
 q['clock'].advance(minutes=6)
 a.verify_consumed(q['ctx'],b,access_token=q['credentials'].access_token,proof=proof)
 assert counts(q)==(1,1) and b.target_id.startswith('routing-price-register:')

@pytest.mark.parametrize('changes',[dict(operation='routing_price.revoke'),dict(body_digest='c'*64),dict(target_digest='c'*64),dict(idempotency_key='price-register-key-0002'),dict(preconditions=PricePreconditions(1,if_match='"other"'))])
def test_old_proof_cannot_authorize_other_binding(price_qa,changes):
 q=price_qa;a=q['price_adapter'];b=binding(q);proof=a.issue(q['ctx'],b,access_token=q['credentials'].access_token,password=q['password'])
 with pytest.raises(IdentityError):a.consume(q['ctx'],binding(q,**changes),access_token=q['credentials'].access_token,proof=proof)
 assert counts(q)==(1,0)

@pytest.mark.parametrize('changes',[dict(operation='routing_price.revoke'),dict(target_digest='c'*64),dict(target_id='routing-price-register:'+'d'*64),dict(fingerprint='d'*64)])
def test_constructed_binding_is_revalidated(price_qa,changes):
 with pytest.raises(RoutingPriceStorageError):replace(binding(price_qa),**changes)

def test_initial_expiry_current_role_workflow_and_personal_owner_denied(price_qa):
 q=price_qa;a=q['price_adapter'];b=binding(q);proof=a.issue(q['ctx'],b,access_token=q['credentials'].access_token,password=q['password']);q['clock'].advance(minutes=6)
 with pytest.raises(IdentityError) as expired:a.consume(q['ctx'],b,access_token=q['credentials'].access_token,proof=proof)
 assert expired.value.code=='STEP_UP_EXPIRED'
 with q['auth_repo'].transaction() as c:c.execute("UPDATE auth_tenant_roles SET state='inactive'")
 with pytest.raises(AuthorizationError):a.current(q['ctx'],access_token=q['credentials'].access_token)



def test_personal_owner_passes_old_helper_but_price_guard_denies(price_qa):
 q=price_qa
 with q['auth_repo'].transaction() as c:
  c.execute("UPDATE auth_tenant_roles SET role='personal_owner'");c.execute("UPDATE auth_workspaces SET workspace_kind='personal'")
 principal=q['identity'].validate_access(q['credentials'].access_token,trace_id=q['ctx'].trace_id,policy_version=q['ctx'].policy_version)
 assert q['auth'].organization_admin_workspace(principal=principal,trace_id=q['ctx'].trace_id,policy_version=q['ctx'].policy_version)==q['ctx'].workspace_id
 with pytest.raises(RoutingPriceStorageError) as error:q['price_adapter'].current(q['ctx'],access_token=q['credentials'].access_token)
 assert (error.value.code,error.value.status)==('PRICE_CURRENT_ACCESS_DENIED',403)

@pytest.mark.parametrize('scope',['workflow','session','tenant','device'])
def test_current_price_access_is_not_minted_from_context(price_qa,scope):
 q=price_qa;ctx=q['ctx']
 if scope=='workflow':
  with q['repo'].transaction() as c:c.execute("UPDATE tenant_memberships SET state='suspended'")
 elif scope=='session':
  with q['repo'].transaction() as c:c.execute("UPDATE sessions SET state='revoked'")
 elif scope=='tenant':ctx=replace(ctx,tenant_id='other-tenant',organization_id='other-tenant')
 else:ctx=replace(ctx,device_id='other-device')
 from daon_user_api.organization_membership import OrganizationWorkflowError
 expected=AuthorizationError if scope=='workflow' else IdentityError if scope=='session' else RoutingPriceStorageError
 with pytest.raises(expected) as error:q['price_adapter'].current(ctx,access_token=q['credentials'].access_token)
 assert error.value.code==('ORGANIZATION_MEMBERSHIP_SUSPENDED' if scope=='workflow' else 'SESSION_REVOKED' if scope=='session' else 'PRICE_CURRENT_ACCESS_DENIED')
 assert counts(q)==(0,0)
