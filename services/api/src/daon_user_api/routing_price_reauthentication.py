"""가격 작업의 서버 binding과 정상 Identity 재인증 접합. HTTP 경로가 아니다."""
from dataclasses import dataclass
import hashlib,hmac,json
from .routing_sources import BudgetContext,RoutingSourceError,require_organization_budget_manager
from .routing_pricing import RoutingPriceStorageError,PricePreconditions

def _check(condition,code="PRICE_BINDING_DENIED",status=403):
    if not condition:raise RoutingPriceStorageError(code,status)
def _id(value):
    import re
    _check(type(value) is str and re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_.:-]{0,127}",value) is not None)
from .identity import IdentityService,IdentityError

ACTION_GROUP='organization_security_or_connector_policy_change'

def canonical(value):return json.dumps(value,sort_keys=True,separators=(',',':'),ensure_ascii=False,allow_nan=False)
def digest(value):return hashlib.sha256(canonical(value).encode()).hexdigest()

def _validate_context(context):
    _check(type(context) is BudgetContext)
    try:context.__post_init__()
    except RoutingSourceError:raise RoutingPriceStorageError('PRICE_BINDING_DENIED',403) from None

def _material(context,operation,body_digest,target_digest,preconditions,idempotency_key):
    import re
    _validate_context(context)
    _check(operation in {'routing_price.register','routing_price.revoke'})
    _check(type(body_digest) is str and re.fullmatch('[0-9a-f]{64}',body_digest) is not None)
    _check(type(target_digest) is str and re.fullmatch('[0-9a-f]{64}',target_digest) is not None)
    _id(idempotency_key);_check(16<=len(idempotency_key)<=128)
    _check(type(preconditions) is PricePreconditions)
    try:preconditions.__post_init__()
    except RoutingSourceError:raise RoutingPriceStorageError('PRICE_BINDING_DENIED',403) from None
    return dict(schema_version=1,operation=operation,tenant_id=context.tenant_id,organization_id=context.organization_id,workspace_id=context.workspace_id,actor_id=context.actor_id,session_id=context.session_id,device_id=context.device_id,idempotency_key=idempotency_key,body_digest=body_digest,target_digest=target_digest,preconditions=preconditions.as_dict())

@dataclass(frozen=True,slots=True)
class PriceBinding:
    operation:str
    target_id:str
    fingerprint:str
    idempotency_key:str
    context:BudgetContext
    body_digest:str
    target_digest:str
    preconditions:PricePreconditions
    def __post_init__(self):
        fp=digest(_material(self.context,self.operation,self.body_digest,self.target_digest,self.preconditions,self.idempotency_key))
        prefix='routing-price-register:' if self.operation.endswith('.register') else 'routing-price-revoke:'
        _check(self.fingerprint==fp and self.target_id==prefix+fp,'PRICE_BINDING_DENIED',403)

class PriceReauthenticationAdapter:
    def __init__(self,identity,authorization):
        _check(isinstance(identity,IdentityService),'PRICE_AUTHENTICATION_UNAVAILABLE',503)
        self.identity=identity;self.authorization=authorization
    def current(self,context,*,access_token):
        _validate_context(context)
        principal=self.identity.validate_access(access_token,trace_id=context.trace_id,policy_version=context.policy_version)
        _check((principal.tenant_id,principal.user_id,principal.session_id,principal.device_id)==(context.tenant_id,context.actor_id,context.session_id,context.device_id),'PRICE_CURRENT_ACCESS_DENIED',403)
        try:
            manager=require_organization_budget_manager(self.authorization,principal=principal,organization_id=context.organization_id,trace_id=context.trace_id,policy_version=context.policy_version)
        except RoutingSourceError as error:
            raise RoutingPriceStorageError('PRICE_CURRENT_ACCESS_DENIED',403) from None
        _check(manager.workspace_id==context.workspace_id,'PRICE_ANCHOR_UNSUPPORTED',403)
        return principal
    @staticmethod
    def binding(context,*,operation,body_digest,target_digest,preconditions,idempotency_key):
        fingerprint=digest(_material(context,operation,body_digest,target_digest,preconditions,idempotency_key))
        prefix='routing-price-register:' if operation.endswith('.register') else 'routing-price-revoke:'
        return PriceBinding(operation,prefix+fingerprint,fingerprint,idempotency_key,context,body_digest,target_digest,preconditions)
    def _bound(self,context,binding,access_token):
        self.current(context,access_token=access_token)
        _check(type(binding) is PriceBinding and binding.context==context,'PRICE_BINDING_DENIED',403)
        binding.__post_init__()
    def issue(self,context,binding,*,access_token,password):
        self._bound(context,binding,access_token)
        grant=self.identity.issue_step_up_after_reauthentication(access_token=access_token,password=password,action_group=ACTION_GROUP,target_id=binding.target_id,policy_version=context.policy_version,trace_id=context.trace_id,idempotency_key=binding.idempotency_key)
        return grant.authorization
    def consume(self,context,binding,*,access_token,proof):
        self._bound(context,binding,access_token)
        self.identity.consume_step_up(step_up_authorization=proof,access_token=access_token,action_group=ACTION_GROUP,target_id=binding.target_id,policy_version=context.policy_version,trace_id=context.trace_id,operation=binding.operation,idempotency_key=binding.idempotency_key)
    def verify_consumed(self,context,binding,*,access_token,proof):
        """현재 권한+정확 기존 소비 영수증만 읽는다. proof 재소비/재인증/발급 없음."""
        self._bound(context,binding,access_token)
        _check(type(proof) is str and 1<=len(proof)<=512,'PRICE_BINDING_DENIED',403)
        with self.identity._repository.transaction() as connection:
            row=connection.execute('SELECT a.step_up_id,c.request_fingerprint FROM step_up_authorizations a JOIN step_up_consumptions c ON c.step_up_id=a.step_up_id WHERE a.authorization_digest=? AND c.tenant_id=? AND c.actor_id=? AND c.operation=? AND c.idempotency_key=?', (hashlib.sha256(proof.encode()).hexdigest(),context.tenant_id,context.actor_id,binding.operation,binding.idempotency_key)).fetchone()
        expected=None if row is None else hashlib.sha256(json.dumps([str(row[0]),context.actor_id,context.session_id,context.device_id,context.tenant_id,ACTION_GROUP,binding.target_id,context.policy_version],separators=(',',':')).encode()).hexdigest()
        _check(row is not None and hmac.compare_digest(str(row[1]),expected),'PRICE_BINDING_DENIED',403)
