"""신뢰된 canonical marker 판정 뒤 현재 연결/Notebook 접근을 재확인한다."""
import os
from dataclasses import dataclass
from types import SimpleNamespace
from .authorization import Action,AuthorizationService,Role
from .postgres_adapters import PostgresCompatConnection,_compat_row
from .open_law_mcp_server import PROTOCOL,SCHEMA_DIGEST


class OpenLawAccessError(ValueError):
    pass


@dataclass(frozen=True)
class _StoredActor:
    """검증·저장된 scope 투영일 뿐 Principal/session/token을 만들지 않는다."""
    tenant_id:str
    user_id:str


class _AuthReadConnection(PostgresCompatConnection):
    def __init__(self,connection):
        self._conn=connection
        self._prefixes=('auth_',)

    def execute(self,statement,params=()):
        if not statement.lstrip().upper().startswith('SELECT '):
            raise OpenLawAccessError('OPEN_LAW_ACCESS_DENIED')
        cursor=self._conn.execute(self._sql(statement),params)
        row=cursor.fetchone()
        if row is not None and not isinstance(row,dict):
            row=dict(zip((column.name for column in cursor.description),row))
        return SimpleNamespace(fetchone=lambda:_compat_row(row))


class _CurrentAuthorizationRules:
    # 기존 판정의 읽기 메서드만 같은 transaction에 결합. 정책/감사 쓰기 없음.
    _membership=AuthorizationService._membership
    _tenant_role=AuthorizationService._tenant_role
    _role_binding=AuthorizationService._role_binding
    _policy_versions=AuthorizationService._policy_versions
    _effective_permission=AuthorizationService._effective_permission
    _required_step_up=AuthorizationService._required_step_up
    _action_grant=AuthorizationService._action_grant


def _current_auth_grant(context,connection,action):
    if not isinstance(action,Action):raise OpenLawAccessError('OPEN_LAW_ACCESS_DENIED')
    identity=connection.execute('SELECT state FROM identity_users WHERE user_id=%s FOR SHARE',
        (context.actor_id,)).fetchone()
    if identity is None or identity[0]!='active':raise OpenLawAccessError('OPEN_LAW_ACCESS_DENIED')
    current=_AuthReadConnection(connection)
    subject=_StoredActor(context.tenant_id,context.actor_id)
    workspace=current.execute('SELECT * FROM auth_workspaces WHERE tenant_id=? AND workspace_id=?',
        (context.tenant_id,context.workspace_id)).fetchone()
    if workspace is None:raise OpenLawAccessError('OPEN_LAW_ACCESS_DENIED')
    rules=_CurrentAuthorizationRules()
    binding=rules._role_binding(current,subject,workspace)
    grant=rules._action_grant(current,principal=subject,workspace=workspace,binding=binding,
        action=action,requested_permissions=())
    if grant is None:raise OpenLawAccessError('OPEN_LAW_ACCESS_DENIED')
    return grant


def open_law_enabled(context):
    return (os.environ.get('DAON_OPEN_LAW_ENABLED','false').lower()=='true'
        and bool(os.environ.get('DAON_OPEN_LAW_QA_WORKSPACE_ID',''))
        and os.environ.get('DAON_OPEN_LAW_QA_WORKSPACE_ID')==context.workspace_id)


def current_open_law_policy(context,connection,*,action=Action.VIEW):
    """기존 Egress resolver를 같은 transaction에서 재사용한다. 정책 쓰기 없음."""
    from .egress_policy import EgressPolicyContext,EgressPolicyService
    from .egress_policy_postgres import PostgresEgressPolicyRepository
    class CurrentRepository:
        def current(self,ctx,scope):
            row=PostgresEgressPolicyRepository._select_current(connection,ctx,scope,None if scope=='organization' else ctx.workspace_id)
            return PostgresEgressPolicyRepository._view(row)
    try:
        ctx=EgressPolicyContext(context.tenant_id,context.tenant_id,context.workspace_id,context.actor_id,context.trace_id,context.policy_version)
        effective=EgressPolicyService(CurrentRepository()).get_effective(ctx)
        grant=_current_auth_grant(context,connection,action)
        approvers={'organization_admin':{Role.PERSONAL_OWNER,Role.ORGANIZATION_ADMIN},
            'workspace_manager':{Role.PERSONAL_OWNER,Role.ORGANIZATION_ADMIN,Role.WORKSPACE_ADMIN}}
        if (effective.mode!='allow_approved_external' or effective.classification!='public'
                or 'external_api' not in effective.allowed_provider_kinds or 'www.law.go.kr' not in effective.allowed_destinations
                or effective.max_bytes<=0 or grant.role not in approvers.get(effective.required_approver,set())):
            raise ValueError
        # Mask/redact flags remain unchanged. This is a local stored-copy read;
        # no Source/question/private fields are sent by this access predicate.
        return effective
    except Exception:raise OpenLawAccessError('OPEN_LAW_ACCESS_DENIED') from None


def require_open_law_source_access(context,source_version_id,connection,*,action=Action.VIEW):
    row=connection.execute('SELECT canonical_json FROM source_versions WHERE tenant_id=%s AND workspace_id=%s AND record_id=%s',
        (context.tenant_id,context.workspace_id,source_version_id)).fetchone()
    if row is None:raise OpenLawAccessError('OPEN_LAW_ACCESS_DENIED')
    payload=row[0]
    if not isinstance(payload,dict):raise OpenLawAccessError('OPEN_LAW_ACCESS_DENIED')
    if payload.get('origin_kind')!='mcp_open_law':return
    notebook=getattr(context,'notebook_id',None)
    if (not open_law_enabled(context) or not notebook
            or payload.get('connector_id')!='mcp-open-law-go-kr' or not payload.get('snapshot_id')):
        raise OpenLawAccessError('OPEN_LAW_ACCESS_DENIED')
    current_open_law_policy(context,connection,action=action)
    source_rule=_AuthReadConnection(connection).execute(
        'SELECT allowed FROM auth_source_access WHERE tenant_id=? AND workspace_id=? AND user_id=? AND source_version_id=?',
        (context.tenant_id,context.workspace_id,context.actor_id,source_version_id)).fetchone()
    if source_rule is not None and not bool(source_rule['allowed']):raise OpenLawAccessError('OPEN_LAW_ACCESS_DENIED')
    connection.execute("SELECT set_config('app.notebook_id',%s,true)",(notebook,))
    found=connection.execute("SELECT w.snapshot_id FROM open_law_source_snapshots w "
        "JOIN workspace_connectors c ON c.tenant_id=w.tenant_id AND c.workspace_id=w.workspace_id AND c.connector_id=w.connector_id "
        "JOIN source_versions v ON v.tenant_id=w.tenant_id AND v.workspace_id=w.workspace_id AND v.record_id=w.source_version_id "
        "JOIN sources s ON s.tenant_id=v.tenant_id AND s.workspace_id=v.workspace_id AND s.record_id=v.source_id "
        "JOIN notebooks n ON n.tenant_id=w.tenant_id AND n.workspace_id=w.workspace_id AND n.notebook_id=w.notebook_id "
        "WHERE w.tenant_id=%s AND w.workspace_id=%s AND w.source_version_id=%s AND w.snapshot_id=%s "
        "AND w.notebook_id=%s AND w.created_by=%s AND n.created_by=%s AND s.state='ready' "
        "AND w.raw_sha256=v.canonical_json->>'raw_sha256' AND w.text_sha256=v.canonical_json->>'text_sha256' "
        "AND c.status='connected' AND c.verification_json->>'protocol_version'=%s AND c.verification_json->>'tool_schema_digest'=%s "
        "AND EXISTS(SELECT 1 FROM notebook_bindings b WHERE b.tenant_id=w.tenant_id AND b.workspace_id=w.workspace_id "
        "AND b.notebook_id=w.notebook_id AND b.binding_kind='source' AND b.record_id=w.source_id AND b.version_id=w.source_version_id) "
        "AND NOT EXISTS(SELECT 1 FROM notebook_source_unbindings u WHERE u.tenant_id=w.tenant_id AND u.workspace_id=w.workspace_id "
        "AND u.notebook_id=w.notebook_id AND u.source_id=w.source_id AND u.source_version_id=w.source_version_id) "
        "AND NOT EXISTS(SELECT 1 FROM source_versions newer WHERE newer.tenant_id=v.tenant_id AND newer.workspace_id=v.workspace_id "
        "AND newer.source_id=v.source_id AND newer.version>v.version) "
        "AND NOT EXISTS(SELECT 1 FROM deletion_requests d WHERE d.tenant_id=s.tenant_id AND d.workspace_id=s.workspace_id "
        "AND d.source_id=s.record_id AND d.source_active=false) "
        "AND NOT EXISTS(SELECT 1 FROM notebook_deletion_requests d WHERE d.tenant_id=n.tenant_id AND d.workspace_id=n.workspace_id "
        "AND d.notebook_id=n.notebook_id AND d.state IN ('accepted','deleting','completed')) FOR SHARE OF c",
        (context.tenant_id,context.workspace_id,source_version_id,payload['snapshot_id'],notebook,context.actor_id,context.actor_id,PROTOCOL,SCHEMA_DIGEST)).fetchone()
    if found is None:raise OpenLawAccessError('OPEN_LAW_ACCESS_DENIED')
