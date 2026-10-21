"""미검증 가격 저장소. 실제DB 적용/가격attestation/quote 발행 기능은 없다."""
from datetime import datetime,timedelta
from psycopg.types.json import Jsonb
from .cloud_storage import CloudAccessContext,CloudDatabaseError
from .routing_sources import RoutingSourceError
from .routing_sources_postgres import _authorize as _budget_authorize
from .routing_pricing import RoutingPriceStorageError,PricePreconditions,PriceSourceView,parse_price_input,price_input_dict
from .routing_price_reauthentication import canonical,digest

def _check(value,code='PRICE_SOURCE_UNAVAILABLE',status=503):
    if not value:raise RoutingPriceStorageError(code,status)
def _id(value):
    import re
    _check(type(value) is str and re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9_.:-]{0,127}',value),'PRICE_INPUT_INVALID',422)
def _identifier(prefix,*values):return prefix+'-'+digest(list(values))[:40]
def _cloud(ctx,cap):return CloudAccessContext(ctx.tenant_id,ctx.workspace_id,ctx.actor_id,cap)
def _authorize(conn,ctx,now):
    try:_budget_authorize(conn,ctx,now=now)
    except RoutingSourceError:raise RoutingPriceStorageError('PRICE_CURRENT_ACCESS_DENIED',403) from None
    except ValueError as error:
        from .open_law_source_access import OpenLawAccessError
        if isinstance(error,OpenLawAccessError):raise RoutingPriceStorageError('PRICE_CURRENT_ACCESS_DENIED',403) from None
        raise

_TARGET_KEYS=('connection_version','connection_id','model_id','provider_code','catalog_version','credential_version','enabled','verification_status','access_mode','credential_requirement','has_credential','adapter_type','endpoint_digest','catalog_status','capabilities','allowed')
def _target_valid(value):
    import re
    _check(type(value) is dict and set(value)==set(_TARGET_KEYS))
    for key in ('connection_version','catalog_version'):_check(type(value[key]) is int and value[key]>=1)
    _check(type(value['credential_requirement']) is str and value['credential_requirement'] in {'required','none'})
    _check(type(value['credential_version']) is int and value['credential_version']>=0)
    _id(value['connection_id'])
    _check(type(value['model_id']) is str and re.fullmatch(r'[^\x00-\x20\x7f]{1,255}',value['model_id']) is not None)
    _check(type(value['provider_code']) is str and re.fullmatch('[A-Z][A-Z0-9_]{0,63}',value['provider_code']) is not None)
    for key in ('enabled','has_credential','allowed'):_check(type(value[key]) is bool)
    for key in ('verification_status','access_mode','credential_requirement','adapter_type','catalog_status'):_check(type(value[key]) is str and len(value[key])<=128)
    _check(type(value['endpoint_digest']) is str and re.fullmatch('[0-9a-f]{64}',value['endpoint_digest']) is not None)
    _check(type(value['capabilities']) is list and len(value['capabilities'])<=64 and all(type(x) is str and re.fullmatch('[a-z_]{1,64}',x) for x in value['capabilities']) and len(set(value['capabilities']))==len(value['capabilities']))

class PostgresPriceSourceRepository:
    def __init__(self,store,*,registry,clock):self.store=store;self.registry=registry;self.clock=clock
    @staticmethod
    def _ready(conn):
        result=conn.execute("""SELECT count(*)=3 AND bool_and(c.relrowsecurity AND c.relforcerowsecurity AND pg_get_userbyid(c.relowner)='postgres'
          AND has_table_privilege('daon_app',c.oid,'SELECT') AND has_table_privilege('daon_app',c.oid,'INSERT')
          AND has_table_privilege('daon_app',c.oid,'UPDATE')=(c.relname='routing_price_current')
          AND NOT has_table_privilege('daon_app',c.oid,'DELETE') AND NOT has_table_privilege('daon_app',c.oid,'TRUNCATE')
          AND NOT EXISTS(SELECT 1 FROM aclexplode(coalesce(c.relacl,acldefault('r',c.relowner))) x WHERE x.grantee=0)
          AND EXISTS(SELECT 1 FROM pg_roles r WHERE r.rolname='daon_app' AND NOT r.rolsuper AND NOT r.rolbypassrls AND NOT r.rolcreatedb AND NOT r.rolcreaterole)
          AND (SELECT count(*) FROM pg_constraint k WHERE k.conrelid=c.oid AND k.contype='p')=1
          AND (SELECT count(*) FROM pg_constraint k WHERE k.conrelid=c.oid AND k.contype='f')=1
          AND (SELECT count(*) FROM pg_constraint k WHERE k.conrelid=c.oid AND k.contype='c')=1
          AND (SELECT count(*) FROM pg_index i WHERE i.indrelid=c.oid AND i.indisunique)=1
          AND NOT EXISTS(SELECT 1 FROM pg_constraint k WHERE k.conrelid=c.oid AND NOT k.convalidated)
          AND (SELECT count(*) FROM pg_trigger t WHERE t.tgrelid=c.oid AND NOT t.tgisinternal)=3
          AND EXISTS(SELECT 1 FROM pg_trigger t WHERE t.tgrelid=c.oid AND t.tgname=c.relname||'_write' AND t.tgtype=CASE WHEN c.relname='routing_price_current' THEN 23 ELSE 7 END AND t.tgenabled='O' AND t.tgqual IS NULL AND t.tgnargs=0 AND t.tgfoid=to_regprocedure('public.guard_routing_price_write()'))
          AND EXISTS(SELECT 1 FROM pg_trigger t WHERE t.tgrelid=c.oid AND t.tgname=c.relname||'_immutable' AND t.tgtype=CASE WHEN c.relname='routing_price_current' THEN 11 ELSE 27 END AND t.tgenabled='O' AND t.tgqual IS NULL AND t.tgnargs=0 AND t.tgfoid=to_regprocedure('public.guard_routing_price_immutable()'))
          AND EXISTS(SELECT 1 FROM pg_trigger t WHERE t.tgrelid=c.oid AND t.tgname=c.relname||'_truncate' AND t.tgtype=34 AND t.tgenabled='O' AND t.tgqual IS NULL AND t.tgnargs=0 AND t.tgfoid=to_regprocedure('public.guard_routing_price_immutable()')))
          FROM pg_catalog.pg_class c JOIN pg_namespace n ON n.oid=c.relnamespace WHERE n.nspname='public' AND c.relname IN('routing_price_versions','routing_price_current','routing_price_revocations') /* price_catalog_tables */""").fetchone()
        _check(result is not None and result[0] is True)
        functions=conn.execute("""SELECT count(*)=5 AND bool_and(md5(p.prosrc)=e.digest AND NOT p.prosecdef AND p.provolatile::text=e.volatility
          AND p.prolang=(SELECT oid FROM pg_language WHERE lanname=CASE WHEN e.return_type='boolean' THEN 'sql' ELSE 'plpgsql' END)
          AND p.prorettype=to_regtype(e.return_type) AND pg_get_userbyid(p.proowner)='postgres' AND p.proconfig=ARRAY['search_path=pg_catalog, pg_temp']::text[]
          AND ((e.argument='' AND p.pronargs=0) OR (e.argument<>'' AND p.pronargs=1 AND p.proargtypes[0]=to_regtype(e.argument)))
          AND has_function_privilege('daon_app',p.oid,'EXECUTE')=(e.return_type='boolean')
          AND NOT EXISTS(SELECT 1 FROM aclexplode(coalesce(p.proacl,acldefault('f',p.proowner))) x WHERE x.grantee=0))
          FROM pg_catalog.pg_proc p JOIN pg_namespace n ON n.oid=p.pronamespace JOIN (VALUES ('valid_routing_price_version','437fb4ea21be4f7b64b93a88c914b975','i','boolean','public.routing_price_versions'),('valid_routing_price_current','3c59b7442d04423c97ad879eb7de2820','i','boolean','public.routing_price_current'),('valid_routing_price_revocation','16dea159c9d565baf91ac82f0307bc42','i','boolean','public.routing_price_revocations'),('guard_routing_price_immutable','5dabfa71628ebf7dc09a1387b896aaf2','v','trigger',''),('guard_routing_price_write','94c548affdf872750309155131464b0c','v','trigger','')) AS e(name,digest,volatility,return_type,argument) ON p.proname=e.name WHERE n.nspname='public' /* price_catalog_functions */""").fetchone()
        _check(functions is not None and functions[0] is True)
        foreign=conn.execute("""SELECT count(*)=3 AND bool_and(k.convalidated AND NOT k.condeferrable AND k.confupdtype='a' AND k.confdeltype='a'
          AND ARRAY(SELECT a.attname::text FROM unnest(k.conkey) WITH ORDINALITY x(attnum,pos) JOIN pg_attribute a ON a.attrelid=k.conrelid AND a.attnum=x.attnum ORDER BY x.pos)=e.child_columns
          AND ARRAY(SELECT a.attname::text FROM unnest(k.confkey) WITH ORDINALITY x(attnum,pos) JOIN pg_attribute a ON a.attrelid=k.confrelid AND a.attnum=x.attnum ORDER BY x.pos)=e.parent_columns)
          FROM pg_catalog.pg_constraint k JOIN (VALUES
          ('routing_price_version_workspace_fk','routing_price_versions','workspaces',ARRAY['tenant_id','anchor_workspace_id'],ARRAY['tenant_id','workspace_id']),
          ('routing_price_current_version_fk','routing_price_current','routing_price_versions',ARRAY['tenant_id','anchor_workspace_id','series_id','price_version'],ARRAY['tenant_id','anchor_workspace_id','series_id','price_version']),
          ('routing_price_revocation_version_fk','routing_price_revocations','routing_price_versions',ARRAY['tenant_id','anchor_workspace_id','series_id','price_version'],ARRAY['tenant_id','anchor_workspace_id','series_id','price_version'])
          ) AS e(name,child,parent,child_columns,parent_columns) ON k.conname=e.name AND k.conrelid=to_regclass('public.'||e.child) AND k.confrelid=to_regclass('public.'||e.parent) AND k.contype='f' /* price_catalog_constraints */""").fetchone()
        _check(foreign is not None and foreign[0] is True)
        indexes=conn.execute("""SELECT count(*)=3 AND bool_and(EXISTS(SELECT 1 FROM pg_index i WHERE i.indrelid=to_regclass('public.'||e.tab)
          AND i.indisunique AND i.indisvalid AND i.indisready AND NOT i.indisexclusion AND i.indpred IS NULL
          AND ARRAY(SELECT a.attname::text FROM unnest(i.indkey::smallint[]) WITH ORDINALITY x(attnum,pos) JOIN pg_attribute a ON a.attrelid=i.indrelid AND a.attnum=x.attnum ORDER BY x.pos)=e.columns))
          FROM pg_catalog.pg_class c JOIN (VALUES ('routing_price_versions',ARRAY['tenant_id','anchor_workspace_id','series_id','price_version']),('routing_price_current',ARRAY['tenant_id','anchor_workspace_id','series_id']),('routing_price_revocations',ARRAY['tenant_id','anchor_workspace_id','series_id','price_version'])) AS e(tab,columns) ON c.oid=to_regclass('public.'||e.tab) /* price_catalog_indexes */""").fetchone()
        _check(indexes is not None and indexes[0] is True)
        checks=conn.execute("""SELECT count(*)=3 AND bool_and(k.convalidated AND regexp_replace(pg_get_constraintdef(k.oid),'[[:space:]]','','g') IN
          ('CHECK(('||e.fn||'('||e.tab||'.*)ISTRUE))','CHECK((public.'||e.fn||'('||e.tab||'.*)ISTRUE))'))
          FROM pg_catalog.pg_constraint k JOIN (VALUES ('routing_price_versions','valid_routing_price_version'),('routing_price_current','valid_routing_price_current'),('routing_price_revocations','valid_routing_price_revocation')) AS e(tab,fn)
          ON k.conrelid=to_regclass('public.'||e.tab) AND k.conname=e.tab||'_valid' AND k.contype='c' /* price_catalog_checks */""").fetchone()
        _check(checks is not None and checks[0] is True)
        scope="(tenant_id = NULLIF(current_setting('app.tenant_id'::text, true), ''::text)) AND (anchor_workspace_id = NULLIF(current_setting('app.workspace_id'::text, true), ''::text))"
        using="("+scope+" AND (current_setting('app.capability'::text, true) = ANY (ARRAY['routing_price.register'::text, 'routing_price.revoke'::text, 'routing_price.read'::text])))"
        current="("+scope+" AND (current_setting('app.capability'::text, true) = ANY (ARRAY['routing_price.register'::text, 'routing_price.revoke'::text])))"
        register="("+scope+" AND (current_setting('app.capability'::text, true) = 'routing_price.register'::text))"
        revoke="("+scope+" AND (current_setting('app.capability'::text, true) = 'routing_price.revoke'::text))"
        policies=conn.execute("""SELECT count(*)=3 AND bool_and(p.polcmd='*' AND p.polroles=ARRAY[(SELECT oid FROM pg_roles WHERE rolname='daon_app')] AND p.polpermissive
          AND p.polname=c.relname||'_scope' AND regexp_replace(pg_get_expr(p.polqual,p.polrelid),'[[:space:]]','','g')=regexp_replace(%s,'[[:space:]]','','g')
          AND regexp_replace(pg_get_expr(p.polwithcheck,p.polrelid),'[[:space:]]','','g')=regexp_replace(CASE WHEN c.relname='routing_price_versions' THEN %s WHEN c.relname='routing_price_current' THEN %s ELSE %s END,'[[:space:]]','','g'))
          FROM pg_catalog.pg_policy p JOIN pg_class c ON c.oid=p.polrelid WHERE p.polrelid IN('public.routing_price_versions'::regclass,'public.routing_price_current'::regclass,'public.routing_price_revocations'::regclass) /* price_catalog_policies */""",(using,register,current,revoke)).fetchone()
        _check(policies is not None and policies[0] is True)
    def _metadata(self,conn,connection_id,model_id):
        row=conn.execute("""SELECT c.version,c.connection_id,m.model_id,c.provider_code,m.catalog_version,c.credential_version,c.enabled,c.verification_status,c.access_mode,c.credential_requirement,(c.encrypted_credential IS NOT NULL),c.adapter_type,encode(sha256(convert_to(c.base_url,'UTF8')),'hex'),m.catalog_status,m.effective_capabilities,(a.model_id IS NOT NULL)
          FROM system_provider_connections c JOIN system_provider_models m ON m.connection_id=c.connection_id JOIN system_provider_allowed_models a ON a.connection_id=m.connection_id AND a.model_id=m.model_id
          WHERE c.connection_id=%s AND m.model_id=%s FOR SHARE OF c,m,a""",(connection_id,model_id)).fetchone()
        if row is None:return None
        _check(type(row) is tuple and len(row)==len(_TARGET_KEYS))
        value=dict(zip(_TARGET_KEYS,row,strict=True));_target_valid(value);return value
    def _usable_target(self,conn,ctx,body):
        value=self._metadata(conn,body['connection_id'],body['model_id'])
        _check(value is not None,'PRICE_TARGET_UNAVAILABLE',409)
        _check((value['connection_version'],value['catalog_version'],value['connection_version'])==(body['connection_version'],body['catalog_version'],body['endpoint_version']),'PRICE_TARGET_CHANGED',409)
        _check(value['enabled'] and value['verification_status']=='verified' and value['access_mode']=='public' and value['catalog_status']=='ready' and 'text_generation' in value['capabilities'] and value['allowed'] and (value['credential_requirement']!='required' or value['has_credential']),'PRICE_TARGET_UNAVAILABLE',409)
        from .open_law_source_access import _current_auth_grant,_CurrentAuthorizationRules,_AuthReadConnection
        from .authorization import Action,Permission
        try:grant=_current_auth_grant(ctx,conn,Action.QUERY)
        except ValueError as error:
            from .open_law_source_access import OpenLawAccessError
            if isinstance(error,OpenLawAccessError):raise RoutingPriceStorageError('PRICE_TARGET_ACCESS_DENIED',403) from None
            raise
        permission=Permission.LOCAL_INTERNAL_LLM if value['provider_code'] in {'OLLAMA','SENTENCE_TRANSFORMERS'} else Permission.EXTERNAL_LLM
        decision=_CurrentAuthorizationRules()._effective_permission(_AuthReadConnection(conn),role=grant.role,tenant_id=ctx.tenant_id,workspace_id=ctx.workspace_id,permission=permission,requested=True)
        _check(decision.effective,'PRICE_TARGET_ACCESS_DENIED',403)
        _check(parse_price_input(body,self.registry,now=self.clock()).valid_until>self.clock(),'PRICE_INPUT_EXPIRED',409)
        return value
    @staticmethod
    def _series(ctx,body):return _identifier('price-series','routing-price-series-v1',ctx.tenant_id,ctx.workspace_id,body['connection_id'],body['model_id'],'workspace')
    @staticmethod
    def _current(conn,ctx,series):
        row=conn.execute('SELECT current_revision,price_version,revoked FROM routing_price_current WHERE tenant_id=%s AND anchor_workspace_id=%s AND series_id=%s',(ctx.tenant_id,ctx.workspace_id,series)).fetchone()
        if row is not None:_check(len(row)==3 and type(row[0]) is int and row[0]>=1 and type(row[1]) is int and row[1]>=1 and type(row[2]) is bool)
        return row
    def _version(self,conn,ctx,series,version):
        row=conn.execute('SELECT canonical_text,payload,content_digest FROM routing_price_versions WHERE tenant_id=%s AND anchor_workspace_id=%s AND series_id=%s AND price_version=%s',(ctx.tenant_id,ctx.workspace_id,series,version)).fetchone()
        _check(row is not None,'PRICE_SOURCE_NOT_FOUND',404)
        try:
            text,value,sha=row
            _check(type(value) is dict and set(value)=={'schema_version','tenant_id','anchor_workspace_id','series_id','price_version','verification_state','created_by','issued_at','body','target'} and canonical(value)==text and digest(value)==sha)
            _check(value['schema_version']==1 and type(value['schema_version']) is int and value['verification_state']=='unverified' and (value['tenant_id'],value['anchor_workspace_id'],value['series_id'],value['price_version'])==(ctx.tenant_id,ctx.workspace_id,series,version))
            _target_valid(value['target']);_id(value['created_by'])
            issued=datetime.fromisoformat(value['issued_at']);_check(issued.tzinfo is not None and issued<=self.clock())
            parsed=parse_price_input(value['body'],self.registry,now=self.clock())
            _check(self._series(ctx,value['body'])==series and parsed.workspace_id==ctx.workspace_id)
            return value,sha,parsed
        except RoutingPriceStorageError:raise
        except (ValueError,TypeError,KeyError,AttributeError,ArithmeticError):raise RoutingPriceStorageError() from None
    def _read(self,conn,ctx,series,version=None):
        _id(series);current=self._current(conn,ctx,series);_check(current is not None,'PRICE_SOURCE_NOT_FOUND',404)
        if version is None:version=current[1]
        _check(type(version) is int and version>=1,'PRICE_INPUT_INVALID',422)
        value,sha,parsed=self._version(conn,ctx,series,version)
        revoked=conn.execute('SELECT price_version FROM routing_price_revocations WHERE tenant_id=%s AND anchor_workspace_id=%s AND series_id=%s AND price_version=%s',(ctx.tenant_id,ctx.workspace_id,series,version)).fetchone() is not None
        _check(version!=current[1] or revoked==current[2])
        metadata=self._metadata(conn,parsed.connection_id,parsed.model_id)
        return PriceSourceView(series,version,current[0],parsed,sha,'unverified',revoked,self.clock()>=parsed.valid_until,metadata!=value['target'])
    def get(self,ctx,series_id,price_version=None):
        try:
            with self.store._transaction(_cloud(ctx,'routing_price.read')) as conn:
                self._ready(conn);_authorize(conn,ctx,self.clock());return self._read(conn,ctx,series_id,price_version)
        except CloudDatabaseError:raise RoutingPriceStorageError() from None
    @staticmethod
    def _conditions(conditions,body,operation,current,view=None):
        _check(type(conditions) is PricePreconditions,'PRICE_INPUT_INVALID',422)
        try:conditions.__post_init__()
        except RoutingSourceError:raise RoutingPriceStorageError('PRICE_INPUT_INVALID',422) from None
        if operation=='routing_price.register':
            revision=0 if current is None else current[0]
            _check(body['expected_version']==conditions.expected_version==revision,'PRICE_VERSION_CONFLICT',409)
            if revision==0:_check(conditions.if_none_match=='*' and conditions.if_match is None,'PRICE_ETAG_MISMATCH',412)
            else:_check(conditions.if_none_match is None and conditions.if_match=='"routing-price-current:'+view.series_id+':'+str(revision)+'"','PRICE_ETAG_MISMATCH',412)
        else:
            _check(conditions.expected_version==body['price_version'],'PRICE_VERSION_CONFLICT',409)
            _check(not view.revoked,'PRICE_ALREADY_REVOKED',409)
            _check(conditions.if_none_match is None and conditions.if_match==view.version_etag,'PRICE_ETAG_MISMATCH',412)
    def _prepare(self,conn,ctx,body,operation,conditions):
        series=self._series(ctx,body) if operation=='routing_price.register' else body['series_id']
        current=self._current(conn,ctx,series)
        if operation=='routing_price.register':
            view=None if current is None else self._read(conn,ctx,series)
            self._conditions(conditions,body,operation,current,view)
            target=self._usable_target(conn,ctx,body);target_digest=digest(target)
        else:
            view=self._read(conn,ctx,series,body['price_version']);self._conditions(conditions,body,operation,current,view)
            target=None;target_digest=view.content_digest
        return series,current,target,target_digest
    def prepare_binding(self,ctx,body,*,operation,preconditions,idempotency_key,reauthentication,access_token):
        series=self._series(ctx,body) if operation=='routing_price.register' else body['series_id']
        try:
            with self.store._transaction(_cloud(ctx,operation)) as conn:
                conn.execute('SELECT pg_advisory_xact_lock(hashtextextended(%s,0))',('routing-price|'+ctx.tenant_id+'|'+series,))
                self._ready(conn);reauthentication.current(ctx,access_token=access_token);_authorize(conn,ctx,self.clock())
                _,_,_,target_digest=self._prepare(conn,ctx,body,operation,preconditions)
                return reauthentication.binding(ctx,operation=operation,body_digest=digest(body),target_digest=target_digest,preconditions=preconditions,idempotency_key=idempotency_key)
        except CloudDatabaseError:raise RoutingPriceStorageError() from None
    @staticmethod
    def _point(conn,ctx,series,previous,version,revoked,now,operation):
        revision=1 if previous is None else previous[0]+1
        if previous is None:
            result=conn.execute('INSERT INTO routing_price_current(tenant_id,anchor_workspace_id,series_id,current_revision,price_version,revoked,changed_by,changed_at,operation) VALUES(%s,%s,%s,%s,%s,%s,%s,%s,%s)',(ctx.tenant_id,ctx.workspace_id,series,revision,version,revoked,ctx.actor_id,now,operation))
        else:
            result=conn.execute('UPDATE routing_price_current SET current_revision=%s,price_version=%s,revoked=%s,changed_by=%s,changed_at=%s,operation=%s WHERE tenant_id=%s AND anchor_workspace_id=%s AND series_id=%s AND current_revision=%s',(revision,version,revoked,ctx.actor_id,now,operation,ctx.tenant_id,ctx.workspace_id,series,previous[0]))
        _check(result.rowcount==1,'PRICE_VERSION_CONFLICT',409)
    def write(self,ctx,body,*,operation,preconditions,idempotency_key,reauthentication,access_token,proof):
        series=self._series(ctx,body) if operation=='routing_price.register' else body['series_id']
        try:
            with self.store._transaction(_cloud(ctx,operation)) as conn:
                conn.execute('SELECT pg_advisory_xact_lock(hashtextextended(%s,0))',('routing-price|'+ctx.tenant_id+'|'+series,))
                self._ready(conn);reauthentication.current(ctx,access_token=access_token);_authorize(conn,ctx,self.clock())
                args=(ctx.tenant_id,ctx.workspace_id,ctx.actor_id,operation,idempotency_key)
                replay=conn.execute('SELECT request_fingerprint,result FROM idempotency_records WHERE tenant_id=%s AND workspace_id=%s AND actor_id=%s AND operation=%s AND idempotency_key=%s',args).fetchone()
                if replay:
                    result=replay[1]
                    _check(type(result) is dict and set(result)=={'series_id','price_version','target_digest','body_digest','conditions_digest'} and result['series_id']==series and result['body_digest']==digest(body) and result['conditions_digest']==digest(preconditions.as_dict()),'IDEMPOTENCY_KEY_REUSED',409)
                    binding=reauthentication.binding(ctx,operation=operation,body_digest=digest(body),target_digest=result['target_digest'],preconditions=preconditions,idempotency_key=idempotency_key)
                    _check(replay[0]==binding.fingerprint,'IDEMPOTENCY_KEY_REUSED',409)
                    reauthentication.verify_consumed(ctx,binding,access_token=access_token,proof=proof)
                    return self._read(conn,ctx,series,result['price_version'])
                series,current,target,target_digest=self._prepare(conn,ctx,body,operation,preconditions)
                binding=reauthentication.binding(ctx,operation=operation,body_digest=digest(body),target_digest=target_digest,preconditions=preconditions,idempotency_key=idempotency_key)
                reauthentication.consume(ctx,binding,access_token=access_token,proof=proof)
                reauthentication.current(ctx,access_token=access_token);_authorize(conn,ctx,self.clock())
                repeated=self._prepare(conn,ctx,body,operation,preconditions)
                _check(repeated[1:]==(current,target,target_digest),'PRICE_TARGET_CHANGED',409)
                now=self.clock()
                if operation=='routing_price.register':
                    maximum=conn.execute('SELECT coalesce(max(price_version),0) FROM routing_price_versions WHERE tenant_id=%s AND anchor_workspace_id=%s AND series_id=%s',(ctx.tenant_id,ctx.workspace_id,series)).fetchone()[0]
                    version=maximum+1
                    value=dict(schema_version=1,tenant_id=ctx.tenant_id,anchor_workspace_id=ctx.workspace_id,series_id=series,price_version=version,verification_state='unverified',created_by=ctx.actor_id,issued_at=now.isoformat(),body=body,target=target)
                    conn.execute('INSERT INTO routing_price_versions(tenant_id,anchor_workspace_id,series_id,price_version,created_by,issued_at,canonical_text,payload,content_digest) VALUES(%s,%s,%s,%s,%s,%s,%s,%s,%s)',(ctx.tenant_id,ctx.workspace_id,series,version,ctx.actor_id,now,canonical(value),Jsonb(value),digest(value)))
                    self._point(conn,ctx,series,current,version,False,now,operation)
                else:
                    version=body['price_version'];value=dict(tenant_id=ctx.tenant_id,anchor_workspace_id=ctx.workspace_id,series_id=series,price_version=version,created_by=ctx.actor_id,revoked_at=now.isoformat(),reason_code=body['reason_code'])
                    conn.execute('INSERT INTO routing_price_revocations(tenant_id,anchor_workspace_id,series_id,price_version,created_by,revoked_at,canonical_text,payload,content_digest) VALUES(%s,%s,%s,%s,%s,%s,%s,%s,%s)',(ctx.tenant_id,ctx.workspace_id,series,version,ctx.actor_id,now,canonical(value),Jsonb(value),digest(value)))
                    if current[1]==version:self._point(conn,ctx,series,current,version,True,now,operation)
                result=dict(series_id=series,price_version=version,target_digest=target_digest,body_digest=digest(body),conditions_digest=digest(preconditions.as_dict()))
                conn.execute("INSERT INTO idempotency_records(tenant_id,workspace_id,actor_id,operation,idempotency_key,request_fingerprint,result,status,expires_at) VALUES(%s,%s,%s,%s,%s,%s,%s,'completed',%s)",(*args,binding.fingerprint,Jsonb(result),now+timedelta(days=3650)))
                conn.execute("INSERT INTO audit_events(event_id,tenant_id,workspace_id,actor_id,action,target_type,target_id,outcome,trace_id,policy_version,metadata) VALUES(%s,%s,%s,%s,%s,'routing_price',%s,'succeeded',%s,%s,%s)",(_identifier('audit-price',ctx.tenant_id,binding.fingerprint),ctx.tenant_id,ctx.workspace_id,ctx.actor_id,operation,series,ctx.trace_id,ctx.policy_version,Jsonb(dict(binding_digest=binding.fingerprint,price_version=version,verification_state='unverified'))))
                return self._read(conn,ctx,series,version)
        except CloudDatabaseError:raise RoutingPriceStorageError() from None
