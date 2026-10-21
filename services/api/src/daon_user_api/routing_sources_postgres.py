"""A2 불변 예산 SQL 저장소. 실제 schema 적용/readiness 변경/runtime 연결은 별도다."""
from dataclasses import replace
from datetime import datetime,timedelta,timezone
from decimal import Decimal
from types import SimpleNamespace
import hashlib,json
from psycopg.types.json import Jsonb
from .cloud_storage import CloudAccessContext,CloudDatabaseError
from .routing_sources import (BudgetSource,BudgetPeriodView,RoutingSourceError,parse_budget_input,validate_budget_history,_check,_id)
from .routing_budget_reauthentication import canonical,digest


def _cloud(context,capability):return CloudAccessContext(context.tenant_id,context.workspace_id,context.actor_id,capability)
def _identifier(prefix,*values):return prefix+'-'+digest(list(values))[:40]

def _authorize(conn,context,*,manager=True,now,require_session=True):
    from .open_law_source_access import _current_auth_grant,_AuthReadConnection
    from .authorization import Action,Role,RoleScope
    from .organization_access import OrganizationAccessBridge
    # 기존에 존재하는 권한/세션/workflow row만 같은 Cloud 거래에서 공유 잠금한다.
    # legacy workflow 부재나 별도 Identity 거래까지 원자적이라고 주장하지 않는다.
    for statement,params in (
        ('SELECT user_id FROM identity_auth_tenant_roles WHERE tenant_id=%s AND user_id=%s FOR SHARE',(context.tenant_id,context.actor_id)),
        ('SELECT workspace_id FROM identity_auth_workspaces WHERE tenant_id=%s ORDER BY workspace_id FOR SHARE',(context.tenant_id,)),
        ('SELECT user_id FROM identity_org_tenant_memberships WHERE tenant_id=%s AND user_id=%s FOR SHARE',(context.tenant_id,context.actor_id))):
        conn.execute(statement,params).fetchall()
    if require_session:conn.execute('SELECT session_id FROM identity_sessions WHERE tenant_id=%s AND user_id=%s AND session_id=%s FOR SHARE',(context.tenant_id,context.actor_id,context.session_id)).fetchall()
    grant=_current_auth_grant(context,conn,Action.POLICY_MANAGE if manager else Action.VIEW)
    if manager:_check(grant.role is Role.ORGANIZATION_ADMIN and grant.role_scope is RoleScope.TENANT,'BUDGET_CURRENT_ACCESS_DENIED',403)
    current=_AuthReadConnection(conn)
    OrganizationAccessBridge(postgres=True).assert_organization_active(connection=current,tenant_id=context.tenant_id,user_id=context.actor_id)
    row=conn.execute("SELECT workspace_kind FROM identity_auth_workspaces WHERE tenant_id=%s AND workspace_id=%s",(context.tenant_id,context.workspace_id)).fetchone()
    _check(row is not None and row[0]=='organization' and context.tenant_id==getattr(context,'organization_id',context.tenant_id),'BUDGET_ANCHOR_UNSUPPORTED',403)
    count=conn.execute("SELECT count(*) FROM identity_auth_workspaces WHERE tenant_id=%s AND workspace_kind='organization'",(context.tenant_id,)).fetchone()
    _check(count==(1,) or count is not None and count[0]==1,'BUDGET_ANCHOR_UNSUPPORTED',403)
    if require_session:
        session=conn.execute('SELECT state,access_expires_at FROM identity_sessions WHERE tenant_id=%s AND user_id=%s AND session_id=%s AND device_id=%s',(context.tenant_id,context.actor_id,context.session_id,context.device_id)).fetchone()
        _check(session is not None and session[0]=='active','BUDGET_CURRENT_ACCESS_DENIED',403)
        expiry=datetime.fromisoformat(session[1]) if type(session[1]) is str else session[1]
        _check(type(expiry) is datetime and expiry.tzinfo is not None and now<expiry,'BUDGET_CURRENT_ACCESS_DENIED',403)

class PostgresBudgetSourceRepository:
    def __init__(self,store,*,registry,clock):self.store=store;self.registry=registry;self.clock=clock
    @staticmethod
    def _ready(conn):
        """보호 catalog가 미적용/불일치면 거부한다. 실제 PG 형식은 후속 격리시험 대상이다."""
        tables=conn.execute("""SELECT count(*)=3 AND bool_and(c.relrowsecurity AND c.relforcerowsecurity AND pg_get_userbyid(c.relowner)='postgres'
          AND has_table_privilege('daon_app',c.oid,'SELECT') AND has_table_privilege('daon_app',c.oid,'INSERT')
          AND NOT has_table_privilege('daon_app',c.oid,'UPDATE') AND NOT has_table_privilege('daon_app',c.oid,'DELETE') AND NOT has_table_privilege('daon_app',c.oid,'TRUNCATE')
          AND EXISTS(SELECT 1 FROM pg_roles r WHERE r.rolname='daon_app' AND NOT r.rolsuper AND NOT r.rolbypassrls AND NOT r.rolcreaterole AND NOT r.rolcreatedb)
          AND NOT EXISTS(SELECT 1 FROM aclexplode(coalesce(c.relacl,acldefault('r',c.relowner))) x WHERE x.grantee=0)
          AND (SELECT count(*) FROM pg_catalog.pg_trigger t WHERE t.tgrelid=c.oid AND NOT t.tgisinternal)=3
          AND (SELECT count(*) FROM pg_catalog.pg_constraint k WHERE k.conrelid=c.oid AND k.contype='p')=1
          AND NOT EXISTS(SELECT 1 FROM pg_catalog.pg_constraint k WHERE k.conrelid=c.oid AND NOT k.convalidated)
          AND EXISTS(SELECT 1 FROM pg_catalog.pg_trigger t WHERE t.tgrelid=c.oid AND t.tgname=c.relname||'_insert' AND t.tgtype=7 AND t.tgenabled='O' AND t.tgqual IS NULL AND t.tgnargs=0 AND t.tgfoid=to_regprocedure('public.guard_routing_budget_insert()'))
          AND EXISTS(SELECT 1 FROM pg_catalog.pg_trigger t WHERE t.tgrelid=c.oid AND t.tgname=c.relname||'_immutable' AND t.tgtype=27 AND t.tgenabled='O' AND t.tgqual IS NULL AND t.tgnargs=0 AND t.tgfoid=to_regprocedure('public.guard_routing_budget_immutable()'))
          AND EXISTS(SELECT 1 FROM pg_catalog.pg_trigger t WHERE t.tgrelid=c.oid AND t.tgname=c.relname||'_truncate' AND t.tgtype=34 AND t.tgenabled='O' AND t.tgqual IS NULL AND t.tgnargs=0 AND t.tgfoid=to_regprocedure('public.guard_routing_budget_immutable()')))
          FROM pg_catalog.pg_class c JOIN pg_catalog.pg_namespace n ON n.oid=c.relnamespace
          WHERE n.nspname='public' AND c.relname IN('routing_budget_periods','routing_budget_allocations','routing_budget_revocations')""").fetchone()
        _check(tables is not None and tables[0] is True,'BUDGET_SOURCE_UNAVAILABLE',503)
        functions=conn.execute("""SELECT count(*)=5 AND bool_and(md5(p.prosrc)=e.digest AND NOT p.prosecdef AND p.provolatile::text=e.volatility
          AND p.prolang=(SELECT oid FROM pg_language WHERE lanname=CASE WHEN e.return_type='boolean' THEN 'sql' ELSE 'plpgsql' END) AND p.prorettype=to_regtype(e.return_type) AND pg_get_userbyid(p.proowner)='postgres' AND p.proconfig=ARRAY['search_path=pg_catalog, pg_temp']::text[]
          AND ((e.argument='' AND p.pronargs=0) OR (e.argument<>'' AND p.pronargs=1 AND p.proargtypes[0]=to_regtype(e.argument)))
          AND has_function_privilege('daon_app',p.oid,'EXECUTE')=(e.return_type='boolean')
          AND NOT EXISTS(SELECT 1 FROM aclexplode(coalesce(p.proacl,acldefault('f',p.proowner))) x WHERE x.grantee=0))
          FROM pg_catalog.pg_proc p JOIN pg_catalog.pg_namespace n ON n.oid=p.pronamespace
          JOIN (VALUES ('valid_routing_budget_period','10f011efd6291296d4343c31f39cd6f4','i','boolean','public.routing_budget_periods'),('valid_routing_budget_allocation','cb0e62577a45360e405435dd153160ea','i','boolean','public.routing_budget_allocations'),('valid_routing_budget_revocation','4eab2e314a82fe181482b3b2c7c97505','i','boolean','public.routing_budget_revocations'),('guard_routing_budget_immutable','4187a470ed0dca3b143373a68adad28b','v','trigger',''),('guard_routing_budget_insert','aced19375bfd5bbf615b1117199a4238','v','trigger','')) AS e(name,digest,volatility,return_type,argument) ON p.proname=e.name
          WHERE n.nspname='public'""").fetchone()
        _check(functions is not None and functions[0] is True,'BUDGET_SOURCE_UNAVAILABLE',503)
        foreign=conn.execute("""SELECT count(*)=4 AND bool_and(k.convalidated AND NOT k.condeferrable AND k.confupdtype='a' AND k.confdeltype='a'
          AND ARRAY(SELECT a.attname::text FROM unnest(k.conkey) WITH ORDINALITY x(attnum,pos) JOIN pg_attribute a ON a.attrelid=k.conrelid AND a.attnum=x.attnum ORDER BY x.pos)=e.child_columns
          AND ARRAY(SELECT a.attname::text FROM unnest(k.confkey) WITH ORDINALITY x(attnum,pos) JOIN pg_attribute a ON a.attrelid=k.confrelid AND a.attnum=x.attnum ORDER BY x.pos)=e.parent_columns)
          FROM pg_catalog.pg_constraint k JOIN (VALUES
          ('routing_budget_period_workspace_fk','routing_budget_periods','workspaces',ARRAY['tenant_id','anchor_workspace_id'],ARRAY['tenant_id','workspace_id']),
          ('routing_budget_period_anchor_fk','routing_budget_periods','routing_budget_periods',ARRAY['tenant_id','anchor_workspace_id','anchor_record_id','anchor_kind'],ARRAY['tenant_id','anchor_workspace_id','period_id','record_kind']),
          ('routing_budget_allocation_period_fk','routing_budget_allocations','routing_budget_periods',ARRAY['tenant_id','anchor_workspace_id','period_id','parent_kind'],ARRAY['tenant_id','anchor_workspace_id','period_id','record_kind']),
          ('routing_budget_revocation_period_fk','routing_budget_revocations','routing_budget_periods',ARRAY['tenant_id','anchor_workspace_id','period_id','parent_kind'],ARRAY['tenant_id','anchor_workspace_id','period_id','record_kind'])
          ) AS e(name,child,parent,child_columns,parent_columns)
          ON k.conname=e.name AND k.conrelid=to_regclass('public.'||e.child) AND k.confrelid=to_regclass('public.'||e.parent) AND k.contype='f'""").fetchone()
        _check(foreign is not None and foreign[0] is True,'BUDGET_SOURCE_UNAVAILABLE',503)
        indexes=conn.execute("""SELECT count(*)=6 AND bool_and(EXISTS(SELECT 1 FROM pg_catalog.pg_index i WHERE i.indrelid=to_regclass('public.'||e.tab)
          AND i.indisunique AND i.indisvalid AND i.indisready AND NOT i.indisexclusion AND i.indpred IS NULL
          AND ARRAY(SELECT a.attname::text FROM unnest(i.indkey::smallint[]) WITH ORDINALITY x(attnum,pos) JOIN pg_attribute a ON a.attrelid=i.indrelid AND a.attnum=x.attnum ORDER BY x.pos)=e.columns))
          FROM (VALUES ('routing_budget_periods',ARRAY['tenant_id','anchor_workspace_id','period_id']),('routing_budget_periods',ARRAY['tenant_id','anchor_workspace_id','period_id','record_kind']),('routing_budget_periods',ARRAY['tenant_id','source_id']),
          ('routing_budget_allocations',ARRAY['tenant_id','anchor_workspace_id','period_id']),('routing_budget_allocations',ARRAY['tenant_id','budget_scope_id']),('routing_budget_revocations',ARRAY['tenant_id','anchor_workspace_id','period_id'])) AS e(tab,columns)""").fetchone()
        _check(indexes is not None and indexes[0] is True,'BUDGET_SOURCE_UNAVAILABLE',503)
        anchor=conn.execute("""SELECT i.indisunique AND i.indisvalid AND i.indisready AND i.indrelid='public.routing_budget_periods'::regclass
          AND ARRAY(SELECT a.attname::text FROM unnest(i.indkey::smallint[]) WITH ORDINALITY x(attnum,pos) JOIN pg_attribute a ON a.attrelid=i.indrelid AND a.attnum=x.attnum ORDER BY x.pos)=ARRAY['tenant_id']::text[]
          AND pg_get_expr(i.indpred,i.indrelid)='(record_kind = ''anchor''::text)'
          FROM pg_catalog.pg_index i WHERE i.indexrelid=to_regclass('public.routing_budget_one_anchor')""").fetchone()
        _check(anchor is not None and anchor[0] is True,'BUDGET_SOURCE_UNAVAILABLE',503)
        checks=conn.execute("""SELECT count(*)=3 AND bool_and(k.convalidated AND regexp_replace(pg_get_constraintdef(k.oid),'[[:space:]]','','g') IN
          ('CHECK(('||e.fn||'('||e.tab||'.*)ISTRUE))','CHECK((public.'||e.fn||'('||e.tab||'.*)ISTRUE))'))
          FROM pg_catalog.pg_constraint k JOIN (VALUES ('routing_budget_periods','valid_routing_budget_period'),('routing_budget_allocations','valid_routing_budget_allocation'),('routing_budget_revocations','valid_routing_budget_revocation')) AS e(tab,fn)
          ON k.conrelid=to_regclass('public.'||e.tab) AND k.conname=e.tab||'_valid' AND k.contype='c'""").fetchone()
        _check(checks is not None and checks[0] is True,'BUDGET_SOURCE_UNAVAILABLE',503)
        policies=conn.execute("""SELECT c.relname,p.polcmd,p.polroles,p.polpermissive,pg_get_expr(p.polqual,p.polrelid),pg_get_expr(p.polwithcheck,p.polrelid)
          FROM pg_catalog.pg_policy p JOIN pg_catalog.pg_class c ON c.oid=p.polrelid WHERE p.polrelid IN('public.routing_budget_periods'::regclass,'public.routing_budget_allocations'::regclass,'public.routing_budget_revocations'::regclass) ORDER BY c.relname""").fetchall()
        role=conn.execute("SELECT oid FROM pg_roles WHERE rolname='daon_app'").fetchone()
        _check(role is not None and len(policies)==3,'BUDGET_SOURCE_UNAVAILABLE',503)
        scope="(tenant_id = NULLIF(current_setting('app.tenant_id'::text, true), ''::text)) AND (anchor_workspace_id = NULLIF(current_setting('app.workspace_id'::text, true), ''::text))"
        using="("+scope+" AND (current_setting('app.capability'::text, true) = ANY (ARRAY['routing_budget.issue'::text, 'routing_budget.revoke'::text, 'routing_budget.read'::text, 'routing_budget.upper'::text])))"
        import re
        seen=set()
        for name,command,roles,permissive,qual,with_check in policies:
            cap='routing_budget.revoke' if name=='routing_budget_revocations' else 'routing_budget.issue'
            expected="("+scope+" AND (current_setting('app.capability'::text, true) = '"+cap+"'::text))"
            _check(name not in seen and command=='*' and roles==[role[0]] and permissive is True and type(qual) is str and type(with_check) is str and re.sub(r'\s+','',qual)==re.sub(r'\s+','',using) and re.sub(r'\s+','',with_check)==re.sub(r'\s+','',expected),'BUDGET_SOURCE_UNAVAILABLE',503)
            seen.add(name)
    def _view(self,context,row):
        _check(row is not None,'BUDGET_SOURCE_NOT_FOUND',404)
        text,value,sha,scope_id,revoked=row
        _check(type(text) is str and type(value) is dict and canonical(value)==text and digest(value)==sha,'BUDGET_SOURCE_UNAVAILABLE',503)
        _check(set(value)=={'schema_version','tenant_id','organization_id','anchor_workspace_id','period_id','source_id','version','created_by','issued_at','budget'} and type(value['schema_version']) is int and value['schema_version']==1,'BUDGET_SOURCE_UNAVAILABLE',503)
        _check((value['tenant_id'],value['organization_id'],value['anchor_workspace_id'])==(context.tenant_id,getattr(context,'organization_id',context.tenant_id),context.workspace_id),'BUDGET_SOURCE_UNAVAILABLE',503)
        source=BudgetSource(tenant_id=value['tenant_id'],organization_id=value['organization_id'],period_id=value['period_id'],source_id=value['source_id'],version=value['version'],issued_by=value['created_by'],issued_at=datetime.fromisoformat(value['issued_at']),budget=parse_budget_input(value['budget'],registry=self.registry),revoked=revoked)
        expected=_identifier('budget-scope',source.tenant_id,source.period_id,context.workspace_id)
        _check(scope_id==expected,'BUDGET_SOURCE_UNAVAILABLE',503)
        return BudgetPeriodView(source,scope_id,sha)
    def _read(self,conn,context,period_id):
        row=conn.execute("SELECT p.canonical_text,p.payload,p.content_digest,a.budget_scope_id,r.period_id IS NOT NULL FROM routing_budget_periods p JOIN routing_budget_allocations a ON (a.tenant_id,a.anchor_workspace_id,a.period_id)=(p.tenant_id,p.anchor_workspace_id,p.period_id) LEFT JOIN routing_budget_revocations r ON (r.tenant_id,r.anchor_workspace_id,r.period_id)=(p.tenant_id,p.anchor_workspace_id,p.period_id) WHERE p.tenant_id=%s AND p.anchor_workspace_id=%s AND p.period_id=%s AND p.record_kind='period'",(context.tenant_id,context.workspace_id,period_id)).fetchone()
        return self._view(context,row)
    def _history(self,conn,context):
        rows=conn.execute("SELECT canonical_text,payload,content_digest FROM routing_budget_periods WHERE tenant_id=%s AND anchor_workspace_id=%s AND record_kind='period' ORDER BY starts_at",(context.tenant_id,context.workspace_id)).fetchall()
        result=[]
        for text,value,sha in rows:
            _check(canonical(value)==text and digest(value)==sha,'BUDGET_SOURCE_UNAVAILABLE',503)
            result.append(self._read(conn,context,value['period_id']).source)
        return tuple(result)
    def get(self,context,period_id):
        _id(period_id)
        try:
            with self.store._transaction(_cloud(context,'routing_budget.read')) as conn:
                self._ready(conn);_authorize(conn,context,now=self.clock())
                return self._read(conn,context,period_id)
        except CloudDatabaseError:raise RoutingSourceError('BUDGET_SOURCE_UNAVAILABLE',503) from None
    @staticmethod
    def _conditions(conditions,current,operation):
        if operation=='routing_budget.issue':
            _check(conditions.if_none_match is not None or conditions.if_match is not None,'BUDGET_PRECONDITION_REQUIRED',428)
            _check(conditions.if_none_match=='*' and conditions.if_match is None,'BUDGET_ETAG_MISMATCH',412)
            _check(conditions.expected_version==0,'BUDGET_VERSION_CONFLICT',409)
        else:
            _check(conditions.if_match is not None or conditions.if_none_match is not None,'BUDGET_PRECONDITION_REQUIRED',428)
            _check(conditions.if_match==current.etag and conditions.if_none_match is None,'BUDGET_ETAG_MISMATCH',412)
            _check(conditions.expected_version==(2 if current.source.revoked else 1),'BUDGET_VERSION_CONFLICT',409)
    def write(self,context,body,*,operation,preconditions,binding,current,consume,verify_consumed):
        try:
            with self.store._transaction(_cloud(context,operation)) as conn:
                conn.execute('SELECT pg_advisory_xact_lock(hashtextextended(%s,0))',('routing-budget|'+context.tenant_id,))
                current();self._ready(conn);_authorize(conn,context,now=self.clock())
                args=(context.tenant_id,context.workspace_id,context.actor_id,operation,binding.idempotency_key)
                replay=conn.execute('SELECT request_fingerprint,result FROM idempotency_records WHERE tenant_id=%s AND workspace_id=%s AND actor_id=%s AND operation=%s AND idempotency_key=%s',args).fetchone()
                if replay:
                    _check(replay[0]==binding.fingerprint and type(replay[1]) is dict and set(replay[1])=={'period_id','binding'} and replay[1]['binding']==binding.target_id,'IDEMPOTENCY_KEY_REUSED',409)
                    verify_consumed();return self._read(conn,context,replay[1]['period_id'])
                previous=None if operation.endswith('.issue') else self._read(conn,context,body['period_id'])
                self._conditions(preconditions,previous,operation)
                consume();current();_authorize(conn,context,now=self.clock())
                self._conditions(preconditions,previous,operation)
                if operation.endswith('.issue'):
                    budget=parse_budget_input(body,registry=self.registry)
                    _check(len(budget.allocations)==1 and budget.allocations[0].workspace_id==context.workspace_id,'BUDGET_ANCHOR_UNSUPPORTED',422)
                    validate_budget_history(budget,tenant_id=context.tenant_id,organization_id=context.organization_id,history=self._history(conn,context),registry=self.registry)
                    period_id=_identifier('budget-period',context.tenant_id,binding.fingerprint)
                    source_id=_identifier('budget-source',context.tenant_id,period_id)
                    root=_identifier('budget-anchor',context.tenant_id)
                    root_value=dict(schema_version=1,record_kind='anchor',tenant_id=context.tenant_id,anchor_workspace_id=context.workspace_id,period_id=root)
                    conn.execute("INSERT INTO routing_budget_periods(tenant_id,anchor_workspace_id,period_id,record_kind,anchor_record_id,created_by,issued_at,canonical_text,payload,content_digest) VALUES(%s,%s,%s,'anchor',%s,%s,%s,%s,%s,%s) ON CONFLICT(tenant_id,anchor_workspace_id,period_id) DO NOTHING",(context.tenant_id,context.workspace_id,root,root,context.actor_id,self.clock(),canonical(root_value),Jsonb(root_value),digest(root_value)))
                    issued=self.clock()
                    value=dict(schema_version=1,tenant_id=context.tenant_id,organization_id=context.organization_id,anchor_workspace_id=context.workspace_id,period_id=period_id,source_id=source_id,version=1,created_by=context.actor_id,issued_at=issued.isoformat(),budget=body)
                    conn.execute("INSERT INTO routing_budget_periods(tenant_id,anchor_workspace_id,period_id,record_kind,anchor_record_id,source_id,version,currency,period_limit,starts_at,expires_at,created_by,issued_at,canonical_text,payload,content_digest) VALUES(%s,%s,%s,'period',%s,%s,1,%s,%s,%s,%s,%s,%s,%s,%s,%s)",(context.tenant_id,context.workspace_id,period_id,root,source_id,budget.currency,budget.period_limit,budget.starts_at,budget.expires_at,context.actor_id,issued,canonical(value),Jsonb(value),digest(value)))
                    allocation=budget.allocations[0];scope_id=_identifier('budget-scope',context.tenant_id,period_id,context.workspace_id)
                    conn.execute('INSERT INTO routing_budget_allocations(tenant_id,anchor_workspace_id,period_id,budget_scope_id,period_limit,run_limit) VALUES(%s,%s,%s,%s,%s,%s)',(context.tenant_id,context.workspace_id,period_id,scope_id,allocation.period_limit,allocation.run_limit))
                else:
                    _check(not previous.source.revoked,'BUDGET_ALREADY_REVOKED',409);period_id=previous.source.period_id
                    revoked_at=self.clock()
                    revoked=dict(tenant_id=context.tenant_id,anchor_workspace_id=context.workspace_id,period_id=period_id,version=2,actor_id=context.actor_id,revoked_at=revoked_at.isoformat(),reason_code=body['reason_code'])
                    conn.execute('INSERT INTO routing_budget_revocations(tenant_id,anchor_workspace_id,period_id,created_by,revoked_at,reason_code,canonical_text,payload,content_digest) VALUES(%s,%s,%s,%s,%s,%s,%s,%s,%s)',(context.tenant_id,context.workspace_id,period_id,context.actor_id,revoked_at,body['reason_code'],canonical(revoked),Jsonb(revoked),digest(revoked)))
                conn.execute("INSERT INTO idempotency_records(tenant_id,workspace_id,actor_id,operation,idempotency_key,request_fingerprint,result,status,expires_at) VALUES(%s,%s,%s,%s,%s,%s,%s,'completed',%s)",(*args,binding.fingerprint,Jsonb(dict(period_id=period_id,binding=binding.target_id)),self.clock()+timedelta(days=3650)))
                conn.execute("INSERT INTO audit_events(event_id,tenant_id,workspace_id,actor_id,action,target_type,target_id,outcome,trace_id,policy_version,metadata) VALUES(%s,%s,%s,%s,%s,'routing_budget',%s,'succeeded',%s,%s,%s)",(_identifier('audit-budget',context.tenant_id,binding.fingerprint),context.tenant_id,context.workspace_id,context.actor_id,operation,period_id,context.trace_id,context.policy_version,Jsonb(dict(binding_digest=binding.fingerprint,version=1 if operation.endswith('.issue') else 2))))
                return self._read(conn,context,period_id)
        except CloudDatabaseError:raise RoutingSourceError('BUDGET_SOURCE_UNAVAILABLE',503) from None
    def upper_budget(self,conn,context):
        from .routing_policy import VerifiedUpperBudget
        from .routing_sources import BudgetContext
        from .workspace_model_defaults import WorkspaceModelDefaultsContext
        _check(type(context) in {BudgetContext,WorkspaceModelDefaultsContext},'BUDGET_CURRENT_ACCESS_DENIED',403)
        self._ready(conn);_authorize(conn,context,manager=False,now=self.clock(),require_session=type(context) is BudgetContext)
        active=[x for x in self._history(conn,context) if not x.revoked and x.budget.starts_at<=self.clock()<x.budget.expires_at]
        _check(len(active)==1,'BUDGET_SOURCE_UNAVAILABLE',503)
        source=active[0];return VerifiedUpperBudget(context.tenant_id,context.workspace_id,context.actor_id,'1',source.source_id,source.budget.currency,format(source.budget.allocations[0].run_limit,'f'),source.budget.starts_at,source.budget.expires_at)
