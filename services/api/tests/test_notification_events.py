"""L2a Studio 원업무 연결 시험. 모형 경계와 실제 전용 PG 증거를 구분한다."""
from contextlib import contextmanager
from pathlib import Path
from types import SimpleNamespace
from uuid import uuid4
import os

import pytest


class _PrivateDsn(str):
    """시험 진단의 repr만 숨긴다. 실제 문자열/driver/환경 전달 값은 바꾸지 않는다."""
    def __repr__(self):
        return '<비공개 DSN>'


def test_l2a_dsn_repr_hides_random_value_without_changing_string():
    """실제 자격을 읽지 않는 순수 시험이다. namespace의 진단 표현도 원문을 포함하지 않는다."""
    originals=['s42-synthetic-'+uuid4().hex for _ in range(2)]
    scope=SimpleNamespace(admin=_PrivateDsn(originals[0]),app=_PrivateDsn(originals[1]))
    for actual,original in zip((scope.admin,scope.app),originals):
        assert isinstance(actual,str)
        assert str(actual)==original and actual.encode('utf-8')==original.encode('utf-8')
        assert repr(actual)=='<비공개 DSN>'
        assert original not in repr(actual) and original not in repr(scope)


def test_l2a_studio_event_is_fixed_and_bound_to_actual_origin():
    from daon_user_api.notification_events import make_studio_event, notebook_notification_link
    context = SimpleNamespace(tenant_id='tenant-1',workspace_id='workspace-1',actor_id='actor-1',
        notebook_id='notebook-1',trace_id='trace-1',policy_version='policy-1')
    event = make_studio_event(context,operation='studio.delivery',target_type='deliveries',
        target_id='delivery-1',origin_event_id='audit-actual-1')
    assert (event.source_store,event.origin_event_id,event.action,event.kind,event.target_id)==(
        'studio','audit-actual-1','studio.delivery','delivery','delivery-1')
    assert event == make_studio_event(context,operation='studio.delivery',target_type='deliveries',
        target_id='delivery-1',origin_event_id='audit-actual-1')
    assert notebook_notification_link('notebook-1','version-1') == '/notebooks/notebook-1?output_version_id=version-1'


@pytest.mark.parametrize('operation,target_type', [('studio.export','deliveries'),
    ('studio.delivery','approvals'),('studio.approval','review_requests')])
def test_l2a_studio_event_rejects_unregistered_or_mismatched_record(operation,target_type):
    from daon_user_api.notification_events import make_studio_event
    from daon_user_api.notification import NotificationError
    context=SimpleNamespace(tenant_id='tenant-1',workspace_id='workspace-1',actor_id='actor-1',
        notebook_id='notebook-1',trace_id='trace-1',policy_version='policy-1')
    with pytest.raises(NotificationError):
        make_studio_event(context,operation=operation,target_type=target_type,target_id='record-1',origin_event_id='audit-1')


@pytest.fixture(scope='module')
def l2a_pg_scope():
    """main이 준비한 자료 없는0068에 후보0069만 적용한다. DB/공유 role을 만들지 않는다."""
    import psycopg
    from psycopg.conninfo import conninfo_to_dict, make_conninfo
    from daon_user_api.studio_workspace import StudioContext
    dsn = os.environ.get('DAON_NOTIFICATION_TEST_DSN')
    if not dsn:
        pytest.skip('DAON_NOTIFICATION_TEST_DSN 미제공: L2a 실제 PG 미실행')
    dsn = _PrivateDsn(dsn)
    if conninfo_to_dict(dsn).get('dbname') != 'daon_user_s42_qa_20261017_l2':
        pytest.fail('승인된 L2a 전용 DB가 아님')
    with psycopg.connect(dsn) as connection:
        assert connection.execute('SELECT current_database()').fetchone()[0] == 'daon_user_s42_qa_20261017_l2'
        assert connection.execute('SELECT version_num FROM alembic_version').fetchall() == [('0068',)]
        assert connection.execute('SELECT count(*) FROM notifications').fetchone()[0] == 0
        sql = Path(__file__).resolve().parents[1]/'migrations/versions/0069_notification_inbox.sql'
        connection.execute(sql.read_text(encoding='utf-8'))
        connection.execute("UPDATE alembic_version SET version_num='0069'")
    suffix = uuid4().hex
    context = StudioContext('s42l2a-tenant-'+suffix,'s42l2a-workspace-'+suffix,
        's42l2a-actor-'+suffix,'s42l2a-trace-'+suffix,'runtime-policy-v1','s42l2a-notebook-'+suffix)
    return SimpleNamespace(admin=dsn,app=_PrivateDsn(make_conninfo(dsn,options='-c role=daon_app')),
        context=context,suffix=suffix,revision='0069')


def test_l2a_studio_record_and_source_intent_share_one_transaction(l2a_pg_scope):
    """실제 record_action의 확정 감사ID와 같은 Studio 기록의 source 의도를 요구한다."""
    import psycopg
    from daon_user_api.cloud_storage import PostgresCloudStore
    from daon_user_api.studio_workspace_postgres import PostgresStudioWorkspaceRepository
    from test_studio_approval_lifecycle_migration import seed_delivery
    scope = l2a_pg_scope
    # 기존 비민감 canonical 준비 helper만 재사용한다. S24 notice/실자료 복사는 없다.
    version, approval, original = seed_delivery(scope)
    context = scope.context
    with psycopg.connect(scope.admin) as connection:
        connection.execute('INSERT INTO identity_tenants(tenant_id) VALUES(%s)',(context.tenant_id,))
        connection.execute("INSERT INTO identity_memberships(tenant_id,user_id,role) VALUES(%s,%s,'personal_owner')",
            (context.tenant_id,context.actor_id))
    store = PostgresCloudStore(scope.app,min_size=0,max_size=2,notification_inbox_enabled=True)
    try:
        repository = PostgresStudioWorkspaceRepository(store)
        key = 's42-l2a-delivery-key-0001'
        payload = {'output_version_id':version,'approval_id':approval,'recipient':'workspace_recipient'}
        # 실제 source INSERT 이후 실패도 원업무·영수증·원감사와 함께 rollback된다.
        from unittest.mock import patch
        from daon_user_api.notification_events import enqueue_studio_event
        def enqueue_then_fail(*args,**kwargs):
            assert enqueue_studio_event(*args,**kwargs)==1
            raise RuntimeError('S42 합성 source 저장 후 실패')
        with psycopg.connect(scope.admin) as connection:
            before=tuple(connection.execute(f'SELECT count(*) FROM {table} WHERE tenant_id=%s',
                (context.tenant_id,)).fetchone()[0] for table in
                ('deliveries','idempotency_records','audit_events','notification_source_outbox','canon_state_transitions'))
        with patch('daon_user_api.notification_events.enqueue_studio_event',side_effect=enqueue_then_fail):
            with pytest.raises(RuntimeError,match='S42 합성 source 저장 후 실패'):
                repository.record_action(context,'delivery',payload,key)
        with psycopg.connect(scope.admin) as connection:
            after=tuple(connection.execute(f'SELECT count(*) FROM {table} WHERE tenant_id=%s',
                (context.tenant_id,)).fetchone()[0] for table in
                ('deliveries','idempotency_records','audit_events','notification_source_outbox','canon_state_transitions'))
            assert after==before
        result, replayed = repository.record_action(context,'delivery',payload,key)
        assert replayed is False and result['action']=='delivery'
        again, replayed = repository.record_action(context,'delivery',payload,key)
        assert replayed is True and again==result
        with psycopg.connect(scope.admin) as connection:
            origin = connection.execute("SELECT event_id FROM audit_events WHERE tenant_id=%s AND workspace_id=%s AND actor_id=%s AND action='studio.delivery' AND target_id=%s",
                (context.tenant_id,context.workspace_id,context.actor_id,result['record_id'])).fetchall()
            assert len(origin)==1
            sources = connection.execute("""SELECT origin_event_id,recipient_id,source_store,action,target_type,target_id,
                notebook_id,trace_id,policy_version,state FROM notification_source_outbox
                WHERE tenant_id=%s AND workspace_id=%s ORDER BY source_event_id,recipient_id""",
                (context.tenant_id,context.workspace_id)).fetchall()
            assert sources == [(origin[0][0],context.actor_id,'studio','studio.delivery','deliveries',
                result['record_id'],context.notebook_id,context.trace_id,context.policy_version,'pending')]
            assert connection.execute('SELECT count(*) FROM notifications WHERE tenant_id=%s',(context.tenant_id,)).fetchone()[0]==0
            assert connection.execute('SELECT canonical_text,digest_sha256 FROM output_versions WHERE tenant_id=%s AND workspace_id=%s AND record_id=%s',
                (context.tenant_id,context.workspace_id,version)).fetchone()==original
        scope.version, scope.delivery_id, scope.delivery_origin = version,result['record_id'],origin[0][0]
    finally:
        store.close()


def _existing_0069_studio_case(*, pending=False, structured_content=None, workspace_kind='personal'):
    """현재 main 소유 QA0069에 새 합성 scope만 만든다. migration/기존 scope를 재실행하지 않는다."""
    import psycopg
    from psycopg.conninfo import conninfo_to_dict, make_conninfo
    from daon_user_api.studio_workspace import StudioContext
    from test_studio_approval_lifecycle_migration import seed_delivery
    dsn=os.environ.get('DAON_NOTIFICATION_TEST_DSN')
    if not dsn:
        pytest.skip('DAON_NOTIFICATION_TEST_DSN 미제공: 실제 PG 미실행')
    dsn=_PrivateDsn(dsn)
    if conninfo_to_dict(dsn).get('dbname')!='daon_user_s42_qa_20261017_l2':
        pytest.fail('승인된 L2a 전용 DB가 아님')
    with psycopg.connect(dsn) as connection:
        assert connection.execute('SELECT current_database(),(SELECT version_num FROM alembic_version)').fetchone()==(
            'daon_user_s42_qa_20261017_l2','0069')
        assert connection.execute('SELECT count(*) FROM canon_transition_rules').fetchone()==(84,)
    suffix=uuid4().hex
    context=StudioContext('s42l2a-tenant-'+suffix,'s42l2a-workspace-'+suffix,
        's42l2a-actor-'+suffix,'s42l2a-trace-'+suffix,'runtime-policy-v1','s42l2a-notebook-'+suffix)
    scope=SimpleNamespace(admin=dsn,app=_PrivateDsn(make_conninfo(dsn,options='-c role=daon_app')),
        context=context,suffix=suffix,revision='0069')
    scope.version,scope.approval,scope.original=seed_delivery(scope,pending=pending,workspace_kind=workspace_kind,
        **({'structured_content':structured_content} if structured_content is not None else {}))
    with psycopg.connect(dsn) as connection:
        connection.execute('INSERT INTO identity_tenants(tenant_id) VALUES(%s)',(context.tenant_id,))
        connection.execute("INSERT INTO identity_memberships(tenant_id,user_id,role) VALUES(%s,%s,%s)",
            (context.tenant_id,context.actor_id,'personal_owner' if workspace_kind=='personal' else 'organization_admin'))
    return scope


def _studio_case_member(scope, *, role):
    """기존 권한 표에 비민감 시험 사용자만 준비한다. 세션/Principal/공유 역할은 만들지 않는다."""
    import psycopg
    user='s42l2a-peer-'+uuid4().hex
    ctx=scope.context
    with psycopg.connect(scope.admin) as connection:
        connection.execute('INSERT INTO user_accounts(tenant_id,user_id) VALUES(%s,%s)',(ctx.tenant_id,user))
        connection.execute("INSERT INTO identity_users(user_id,issuer,subject,state) VALUES(%s,'s42l2a-fixture',%s,'active')",(user,user))
        connection.execute("INSERT INTO identity_memberships(tenant_id,user_id,role) VALUES(%s,%s,'member')",(ctx.tenant_id,user))
        connection.execute("INSERT INTO identity_auth_memberships(tenant_id,workspace_id,user_id,role,state,version,updated_at) "
            "VALUES(%s,%s,%s,%s,'active',1,now())",(ctx.tenant_id,ctx.workspace_id,user,role))
    return user


def _studio_case_counts(scope):
    import psycopg
    with psycopg.connect(scope.admin) as connection:
        return tuple(connection.execute(f'SELECT count(*) FROM public.{table} WHERE tenant_id=%s',
            (scope.context.tenant_id,)).fetchone()[0] for table in (
                'review_requests','approval_requests','approvals','audit_events','idempotency_records',
                'canon_state_transitions','notification_source_outbox'))


def test_l2a_existing_0069_direct_enqueue_rejects_current_access_bypass():
    """정상 업무/원감사의 같은 transaction에서도 직접 함수 호출로 수신 권한을 우회할 수 없다."""
    from dataclasses import replace
    from unittest.mock import patch
    import psycopg
    from daon_user_api.cloud_storage import PostgresCloudStore
    from daon_user_api.notification_events import make_studio_event, _can_receive_studio
    from daon_user_api.studio_workspace_postgres import PostgresStudioWorkspaceRepository
    accepted=[]
    # REVIEW 부재·APPROVE permission deny·잘못된 Source binding을 모두 따로 실행한다.
    # 이미 SQL에 있는 명시 Source ACL 거부도 양성 대조로 유지한다.
    for case in ('viewer','permission_deny','missing_source_binding','source_acl_deny'):
        scope=_existing_0069_studio_case()
        ctx=scope.context
        peer=_studio_case_member(scope,role='viewer' if case=='viewer' else 'approver' if case=='permission_deny' else 'reviewer')
        notebook=ctx.notebook_id
        with psycopg.connect(scope.admin) as connection:
            if case=='permission_deny':
                connection.execute("INSERT INTO identity_auth_workspace_permission_rules(tenant_id,workspace_id,permission,effect,locked) "
                    "VALUES(%s,%s,'final_approval_external_delivery','deny',0)",(ctx.tenant_id,ctx.workspace_id))
            elif case=='source_acl_deny':
                connection.execute("INSERT INTO identity_auth_source_access(tenant_id,workspace_id,user_id,source_version_id,allowed,version,updated_at) "
                    "VALUES(%s,%s,%s,%s,0,1,now())",(ctx.tenant_id,ctx.workspace_id,peer,scope.source_version_id))
            elif case=='missing_source_binding':
                notebook='s42l2a-unbound-'+scope.suffix
                connection.execute('INSERT INTO notebooks(tenant_id,workspace_id,notebook_id,created_by,created_at) VALUES(%s,%s,%s,%s,now())',
                    (ctx.tenant_id,ctx.workspace_id,notebook,peer))
                connection.execute("INSERT INTO notebook_bindings(tenant_id,workspace_id,notebook_id,binding_kind,record_id,created_by,created_at) "
                    "VALUES(%s,%s,%s,'output_version',%s,%s,now())",(ctx.tenant_id,ctx.workspace_id,notebook,scope.version,peer))
        store=PostgresCloudStore(scope.app,min_size=0,max_size=2,notification_inbox_enabled=True)
        before=_studio_case_counts(scope)
        def probe(connection,context,**event_args):
            event=make_studio_event(context,**event_args)
            recipient=replace(context,actor_id=peer,notebook_id=notebook)
            assert not _can_receive_studio(connection,recipient,event,scope.version,ctx.actor_id)
            original=connection.execute('SELECT xmin=pg_current_xact_id()::xid FROM audit_events WHERE tenant_id=%s AND event_id=%s',
                (ctx.tenant_id,event.origin_event_id)).fetchone()
            assert original==(True,)
            try:
                with connection.transaction(force_rollback=True):
                    connection.execute('SELECT public.enqueue_studio_notification(%s,%s,%s)',(event.origin_event_id,notebook,peer))
            except psycopg.Error as error:
                assert error.sqlstate in {'42501','23514'}
            else:
                accepted.append(case)
            # 반례가 허용되더라도 원업무/source는 전부 rollback하고 나머지 반례를 계속 확인한다.
            raise RuntimeError('S42 직접 함수 반례 종료')
        try:
            repo=PostgresStudioWorkspaceRepository(store)
            action='approval_request' if case=='permission_deny' else 'review'
            payload={'output_version_id':scope.version}
            if action=='approval_request':
                payload['review_request_id']='s24-review-'+scope.suffix
            with patch('daon_user_api.notification_events.enqueue_studio_event',side_effect=probe):
                with pytest.raises(RuntimeError,match='S42 직접 함수 반례 종료'):
                    repo.record_action(ctx,action,payload,'s42-l2a-direct-function-key')
            assert _studio_case_counts(scope)==before
        finally:
            store.close()
    assert accepted==[], f'현재 접근 거부를 직접 SQL 호출이 우회함: {accepted}'


def test_l2a_existing_0069_approval_and_nested_savepoint_preserve_source_intent():
    """실제 approval_transaction과 별도 중첩 savepoint 모두 같은 최상위 transaction으로 인정한다."""
    from daon_user_api.cloud_storage import PostgresCloudStore, CloudDatabaseError
    from daon_user_api.studio_workspace import StudioError
    from daon_user_api.studio_workspace_postgres import PostgresStudioWorkspaceRepository as Canon
    from daon_user_api.notification_events import enqueue_studio_event
    from unittest.mock import patch
    import psycopg
    scope=_existing_0069_studio_case(pending=True)
    ctx=scope.context
    store=PostgresCloudStore(scope.app,min_size=0,max_size=2,notification_inbox_enabled=True)
    errors=[]
    try:
        repo=Canon(store)
        try:
            result,replayed=repo.record_action(ctx,'approval',{'output_version_id':scope.version,
                'approval_request_id':scope.request_id,'decision':'approved'},'s42-l2a-real-approval-key')
        except StudioError as error:
            errors.append(('actual_approval',error.code))
        else:
            assert not replayed and result['action']=='approval'
            with psycopg.connect(scope.admin) as connection:
                assert connection.execute("SELECT state FROM approval_requests WHERE tenant_id=%s AND record_id=%s",
                    (ctx.tenant_id,scope.request_id)).fetchone()==('approved',)
                assert connection.execute("SELECT count(*) FROM notification_source_outbox WHERE tenant_id=%s AND action='studio.approval'",
                    (ctx.tenant_id,)).fetchone()==(1,)
        try:
            with store._transaction(repo._cloud(ctx,'studio.review')) as connection:
                connection.execute('SELECT pg_current_xact_id()')
                with connection.transaction(force_rollback=True):
                    # 실제 record_action과 같은 key/scope ID를 사용한다. 임의 ID로 보호 검사를 우회하지 않는다.
                    record=Canon._opaque('review',ctx.tenant_id,ctx.workspace_id,ctx.actor_id,'studio.review','s42-l2a-nested-key')
                    Canon._insert(connection,ctx,'review_requests',record,{'output_version_id':scope.version},
                        extra_columns=('output_version_id',),extra_values=(scope.version,))
                    inserted=[]
                    def checked_enqueue(*args,**kwargs):
                        result=enqueue_studio_event(*args,**kwargs)
                        inserted.append(result)
                        return result
                    with patch('daon_user_api.notification_events.enqueue_studio_event',side_effect=checked_enqueue):
                        repo._finish(connection,ctx,'studio.review','s42-l2a-nested-key','a'*64,
                            {'record_id':record},'review_requests',record)
                    # app의 source 직접 SELECT 권한은 넓히지 않고 실제 INSERT 함수 결과를 확인한다.
                    assert inserted==[1]
        except CloudDatabaseError as error:
            errors.append(('nested_savepoint',error.code))
        assert errors==[], f'같은 transaction의 정상 승인/중첩 의도 실패: {errors}'
    finally:
        store.close()


def test_l2a_existing_0069_approval_result_includes_original_view_only_requester():
    """승인처리자와 원요청자는 다르며 원요청자에게 APPROVE를 새로 요구하지 않는다."""
    from dataclasses import replace
    from daon_user_api.cloud_storage import PostgresCloudStore
    from daon_user_api.studio_workspace_postgres import PostgresStudioWorkspaceRepository as Canon
    from daon_user_api.studio_approval_lifecycle import require_pending
    from daon_user_api.open_law_source_access import _current_auth_grant, OpenLawAccessError
    from daon_user_api.authorization import Action
    from daon_user_api.notebook import NotebookContext
    from daon_user_api.notebook_postgres import PostgresNotebookRepository
    from datetime import datetime, timezone
    import psycopg
    scope=_existing_0069_studio_case(pending=True)
    requester=scope.context.actor_id
    approver=_studio_case_member(scope,role='approver')
    with psycopg.connect(scope.admin) as connection:
        connection.execute('DELETE FROM identity_auth_tenant_roles WHERE tenant_id=%s AND user_id=%s',
            (scope.context.tenant_id,requester))
        connection.execute("INSERT INTO identity_auth_memberships(tenant_id,workspace_id,user_id,role,state,version,updated_at) "
            "VALUES(%s,%s,%s,'viewer','active',1,now())",(scope.context.tenant_id,scope.context.workspace_id,requester))
    ctx=replace(scope.context,actor_id=approver)
    store=PostgresCloudStore(scope.app,min_size=0,max_size=2,notification_inbox_enabled=True)
    try:
        # 0066의 승인용 Notebook 본인 조건을 그대로 충족한다. 일반 공유 정책은 바꾸지 않는다.
        notebook_repository=PostgresNotebookRepository(store)
        notebook_context=NotebookContext(ctx.tenant_id,ctx.workspace_id,approver,ctx.trace_id,ctx.policy_version)
        now=datetime.now(timezone.utc)
        notebook,replayed=notebook_repository.create(notebook_context,title='S42 L2a 합성 승인자 Notebook',
            description=None,idempotency_key='s42-l2a-approver-notebook-key',request_fingerprint='c'*64,now=now)
        assert not replayed
        assert notebook_repository.bind_verified(notebook_context,notebook.notebook_id,binding_kind='output_version',
            record_id=scope.version,version_id=None,now=now) is False
        with psycopg.connect(scope.admin) as connection:
            sources=connection.execute('SELECT sv.source_id,er.source_version_id FROM evidence_references er JOIN source_versions sv '
                'ON sv.tenant_id=er.tenant_id AND sv.workspace_id=er.workspace_id AND sv.record_id=er.source_version_id '
                'WHERE er.tenant_id=%s AND er.workspace_id=%s AND er.output_version_id=%s',
                (ctx.tenant_id,ctx.workspace_id,scope.version)).fetchall()
        assert sources
        for source,source_version in sources:
            assert notebook_repository.bind_verified(notebook_context,notebook.notebook_id,binding_kind='source',
                record_id=source,version_id=source_version,now=now) is False
        ctx=replace(ctx,notebook_id=notebook.notebook_id)
        repo=Canon(store)
        with store._transaction(repo._cloud(ctx,'studio.approval')) as connection:
            _current_auth_grant(scope.context,connection,Action.VIEW)
            with pytest.raises(OpenLawAccessError):
                _current_auth_grant(scope.context,connection,Action.APPROVE)
            _current_auth_grant(ctx,connection,Action.APPROVE)
            request_version=require_pending(connection,ctx,scope.request_id,scope.version)
            output_version=connection.execute('SELECT version FROM output_versions WHERE tenant_id=%s AND workspace_id=%s AND record_id=%s',
                (ctx.tenant_id,ctx.workspace_id,scope.version)).fetchone()[0]
            record='s42l2a-decision-'+scope.suffix
            Canon._insert(connection,ctx,'approvals',record,{'output_version_id':scope.version,
                'approval_request_id':scope.request_id,'decision':'approved'},extra_columns=('output_version_id','approval_request_id','decision'),
                extra_values=(scope.version,scope.request_id,'approved'))
            Canon._transition(connection,ctx,'ApprovalRequest',scope.request_id,request_version,'approved','s42l2a-request-approved-'+scope.suffix)
            Canon._transition(connection,ctx,'OutputVersion',scope.version,output_version,'approved','s42l2a-output-approved-'+scope.suffix)
            # I2가 I3를 가리지 않도록 실제 원장+_finish를 최상위 connection에서 실행한다.
            repo._finish(connection,ctx,'studio.approval','s42-l2a-requester-key','b'*64,
                {'record_id':record},'approvals',record)
        with psycopg.connect(scope.admin) as connection:
            original=connection.execute("SELECT a.actor_id,r.created_by FROM audit_events a JOIN approvals d "
                "ON d.tenant_id=a.tenant_id AND d.workspace_id=a.workspace_id AND d.record_id=a.target_id "
                "JOIN approval_requests r ON r.tenant_id=d.tenant_id AND r.workspace_id=d.workspace_id AND r.record_id=d.approval_request_id "
                "WHERE a.tenant_id=%s AND a.action='studio.approval' AND a.target_id=%s",(ctx.tenant_id,record)).fetchall()
            assert original==[(approver,requester)]
            recipients=connection.execute('SELECT recipient_id FROM notification_source_outbox WHERE tenant_id=%s AND target_id=%s ORDER BY recipient_id',
                (ctx.tenant_id,record)).fetchall()
            assert recipients==sorted([(approver,),(requester,)])
    finally:
        store.close()


def test_l2a_existing_0069_rejects_committed_and_invalid_origin():
    """subtransaction 지원이 기존에 commit된 원감사/없는 사건/부정한 action을 허용하지 않는다."""
    import psycopg
    from psycopg.types.json import Jsonb
    scope=_existing_0069_studio_case()
    ctx=scope.context
    with psycopg.connect(scope.admin) as connection:
        origin='s42l2a-committed-origin-'+scope.suffix
        target='s24-review-'+scope.suffix
        connection.execute("INSERT INTO audit_events(event_id,tenant_id,workspace_id,actor_id,action,target_type,target_id,outcome,trace_id,policy_version) "
            "VALUES(%s,%s,%s,%s,'studio.review','review_requests',%s,'succeeded',%s,%s)",
            (origin,ctx.tenant_id,ctx.workspace_id,ctx.actor_id,target,ctx.trace_id,ctx.policy_version))
    for case in ('committed','missing','wrong_action'):
        with psycopg.connect(scope.app) as connection:
            with connection.transaction(force_rollback=True):
                for name,value in (('tenant_id',ctx.tenant_id),('workspace_id',ctx.workspace_id),('actor_id',ctx.actor_id)):
                    connection.execute('SELECT set_config(%s,%s,true)',('app.'+name,value))
                attempted=origin if case=='committed' else 's42l2a-invalid-origin-'+scope.suffix
                if case=='wrong_action':
                    connection.execute("INSERT INTO audit_events(event_id,tenant_id,workspace_id,actor_id,action,target_type,target_id,outcome,trace_id,policy_version,metadata) "
                        "VALUES(%s,%s,%s,%s,'studio.unregistered','review_requests',%s,'succeeded',%s,%s,%s)",
                        (attempted,ctx.tenant_id,ctx.workspace_id,ctx.actor_id,target,ctx.trace_id,ctx.policy_version,Jsonb({})))
                with pytest.raises(psycopg.Error) as rejected:
                    connection.execute('SELECT public.enqueue_studio_notification(%s,%s,%s)',(attempted,ctx.notebook_id,ctx.actor_id))
                assert rejected.value.sqlstate in {'42501','23514'}
    with psycopg.connect(scope.admin) as connection:
        assert connection.execute('SELECT count(*) FROM notification_source_outbox WHERE tenant_id=%s',(ctx.tenant_id,)).fetchone()==(0,)


def _studio_case_egress(connection,scope,scope_type,payload,version):
    """합성 QA scope의 실제 불변 정책 version과 현재 binding만 준비한다."""
    import hashlib
    from psycopg.types.json import Jsonb
    from daon_user_api.data_canon import canonical_json_bytes
    ctx=scope.context
    workspace=None if scope_type=='organization' else ctx.workspace_id
    policy_id='s42l2a-policy-'+scope.suffix+'-'+scope_type+'-'+str(version)
    binding_id='s42l2a-binding-'+scope.suffix+'-'+scope_type+'-'+str(version)
    canonical=canonical_json_bytes(payload).decode()
    connection.execute('INSERT INTO egress_policy_versions(tenant_id,organization_id,workspace_id,policy_version_id,scope_type,'
        "policy_version,state,canonical_json,canonical_text,digest_sha256,created_by,trace_id) VALUES(%s,%s,%s,%s,%s,%s,'active',%s,%s,%s,%s,%s)",
        (ctx.tenant_id,ctx.tenant_id,workspace,policy_id,scope_type,version,Jsonb(payload),canonical,
         hashlib.sha256(canonical.encode()).hexdigest(),ctx.actor_id,ctx.trace_id))
    connection.execute('INSERT INTO egress_policy_bindings(tenant_id,organization_id,workspace_id,binding_id,scope_type,'
        'policy_version_id,binding_version,active,current,created_by,trace_id) VALUES(%s,%s,%s,%s,%s,%s,%s,true,true,%s,%s)',
        (ctx.tenant_id,ctx.tenant_id,workspace,binding_id,scope_type,policy_id,version,ctx.actor_id,ctx.trace_id))


def _studio_case_special_source(scope,kind):
    """기존 import 저장 경로로 실제 snapshot을 준비한다. HTTP/Object 전송 증거는 아니다."""
    import psycopg
    from daon_user_api.internet_connector import WebsiteImportContext
    from daon_user_api.studio_workspace_postgres import PostgresStudioWorkspaceRepository as Canon
    ctx=scope.context
    source_context=WebsiteImportContext(ctx.tenant_id,ctx.workspace_id,ctx.notebook_id,ctx.actor_id,ctx.trace_id,ctx.policy_version)
    if kind=='law':
        from psycopg.types.json import Jsonb
        from daon_user_api.open_law_mcp_server import PROTOCOL,SCHEMA_DIGEST
        from daon_user_api.open_law_source_postgres import PostgresOpenLawSourceRepository
        from test_open_law_source_import import document,selection
        from test_open_law_source_postgres import promoted
        policy={'mode':'allow_approved_external','allowed_provider_kinds':['external_api'],'allowed_destinations':['www.law.go.kr'],
            'classification':'public','max_bytes':2097152,'masking_required':True,'redaction_required':True,'required_approver':'organization_admin'}
        with psycopg.connect(scope.admin) as connection:
            for scope_type in ('organization','workspace'):
                _studio_case_egress(connection,scope,scope_type,policy,1)
            connection.execute("INSERT INTO workspace_connectors(tenant_id,workspace_id,connector_id,kind,name,endpoint_label,status,source_count,sources_json,verification_json,created_at,updated_at) "
                "VALUES(%s,%s,'mcp-open-law-go-kr','mcp','S42 합성 법령','open.law.go.kr','connected',0,'[]',%s,now(),now())",
                (ctx.tenant_id,ctx.workspace_id,Jsonb({'protocol_version':PROTOCOL,'tool_schema_digest':SCHEMA_DIGEST,'checked_at':'2026-10-17T00:00:00Z'})))
        repo=PostgresOpenLawSourceRepository(scope.app,policy_fingerprint=lambda _:'s42l2a-synthetic-policy')
        try:
            doc=document()
            claim=repo.claim(source_context,selection(),doc.raw_sha256,doc.text_sha256,'s42-l2a-special-source-key')
            result=repo.complete(source_context,claim,doc,*promoted(scope.admin,source_context,claim))
            assert repo.get_snapshot(source_context,result.source_version_id).source_id==result.source_id
        finally:
            repo.close()
    else:
        from daon_user_api.website_source_postgres import PostgresWebsiteSourceRepository
        from test_website_source_import import page
        from test_website_source_postgres import promoted_records
        repo=PostgresWebsiteSourceRepository(scope.app,policy_fingerprint=lambda _:'s42l2a-synthetic-policy')
        try:
            fetched=page()
            claim=repo.claim(source_context,fetched.requested_url,'s42-l2a-special-source-key')
            result=repo.complete(source_context,claim,fetched,*promoted_records(scope.admin,source_context,claim,fetched))
            assert repo.get_snapshot(source_context,result.source_version_id).source_id==result.source_id
        finally:
            repo.close()
    with psycopg.connect(scope.admin) as connection:
        for name,value in (('tenant_id',ctx.tenant_id),('workspace_id',ctx.workspace_id),('actor_id',ctx.actor_id)):
            connection.execute('SELECT set_config(%s,%s,true)',('app.'+name,value))
        span=connection.execute('SELECT record_id FROM evidence_spans WHERE tenant_id=%s AND workspace_id=%s AND source_version_id=%s ORDER BY record_id LIMIT 1',
            (ctx.tenant_id,ctx.workspace_id,result.source_version_id)).fetchone()
        assert span is not None
        Canon._insert(connection,ctx,'evidence_references','s42l2a-special-evidence-'+scope.suffix,
            {'source_version_id':result.source_version_id,'evidence_span_id':span[0]},
            extra_columns=('output_version_id','source_version_id','evidence_span_id'),
            extra_values=(scope.version,result.source_version_id,span[0]))
    return result


@pytest.mark.parametrize('case',('law_owner','law_connector','law_policy','website_owner'))
def test_l2a_existing_0069_special_source_direct_enqueue_rejects_current_access(case):
    """유효 snapshot의 특정 보호만 바꿔 Python과 SQL의 독립 거부를 확인한다."""
    from dataclasses import replace
    from unittest.mock import patch
    import psycopg
    from daon_user_api.cloud_storage import PostgresCloudStore
    from daon_user_api.studio_workspace_postgres import PostgresStudioWorkspaceRepository
    from daon_user_api.notification_events import make_studio_event,_can_receive_studio
    from daon_user_api.egress_policy import EgressPolicyPayload
    scope=_existing_0069_studio_case()
    ctx=scope.context
    accepted=[]
    with pytest.MonkeyPatch.context() as environment:
        # 현재 시험 프로세스에서만 기존 gate를 유효하게 둔다. 정식 설정/DB 신뢰 bit는 바꾸지 않는다.
        if case.startswith('law_'):
            environment.setenv('DAON_OPEN_LAW_ENABLED','true')
            environment.setenv('DAON_OPEN_LAW_QA_WORKSPACE_ID',ctx.workspace_id)
        special=_studio_case_special_source(scope,'law' if case.startswith('law_') else 'website')
        peer=ctx.actor_id
        if case.endswith('_owner'):
            peer=_studio_case_member(scope,role='reviewer')
            # 법령 egress의 기존 required_approver 기준을 통과시켜 owner 하나의 실패를 분리한다.
            with psycopg.connect(scope.admin) as connection:
                connection.execute("INSERT INTO identity_auth_tenant_roles(tenant_id,user_id,role,state,version,updated_at) "
                    "VALUES(%s,%s,'personal_owner','active',1,now())",(ctx.tenant_id,peer))
            from daon_user_api.internet_connector import WebsiteImportContext
            if case=='law_owner':
                from daon_user_api.open_law_source_postgres import PostgresOpenLawSourceRepository
                from daon_user_api.open_law_source_import import OpenLawImportError
                snapshot_reader=PostgresOpenLawSourceRepository(scope.app,policy_fingerprint=lambda _:'s42l2a-synthetic-policy')
                denied_error=OpenLawImportError
                denied_code='OPEN_LAW_ACCESS_DENIED'
            else:
                from daon_user_api.website_source_postgres import PostgresWebsiteSourceRepository
                from daon_user_api.website_source_import import WebsiteImportError
                snapshot_reader=PostgresWebsiteSourceRepository(scope.app,policy_fingerprint=lambda _:'s42l2a-synthetic-policy')
                denied_error=WebsiteImportError
                denied_code='WEBSITE_ACCESS_DENIED'
            try:
                with pytest.raises(denied_error) as denied:
                    snapshot_reader.get_snapshot(WebsiteImportContext(ctx.tenant_id,ctx.workspace_id,ctx.notebook_id,peer,ctx.trace_id,ctx.policy_version),
                        special.source_version_id)
                assert denied.value.code==denied_code
            finally:
                snapshot_reader.close()
        before=_studio_case_counts(scope)
        store=PostgresCloudStore(scope.app,min_size=0,max_size=2,notification_inbox_enabled=True)
        def probe(connection,context,**event_args):
            event=make_studio_event(context,**event_args)
            if case=='website_owner':
                from daon_user_api.notification_events import _website_snapshot_current
                other_notebook='s42l2a-other-notebook-'+scope.suffix
                connection.execute('INSERT INTO notebooks(tenant_id,workspace_id,notebook_id,created_by,created_at) VALUES(%s,%s,%s,%s,now())',
                    (ctx.tenant_id,ctx.workspace_id,other_notebook,ctx.actor_id))
                connection.execute("SELECT set_config('app.actor_id',%s,true),set_config('app.notebook_id',%s,true)",(peer,other_notebook))
                previous_scope=(peer,other_notebook)
            assert _can_receive_studio(connection,context,event,scope.version,ctx.actor_id)
            if case=='website_owner':
                assert connection.execute("SELECT current_setting('app.actor_id',true),current_setting('app.notebook_id',true)").fetchone()==previous_scope
                assert not _website_snapshot_current(connection,replace(context,notebook_id=other_notebook),special.source_version_id)
                assert connection.execute("SELECT current_setting('app.actor_id',true),current_setting('app.notebook_id',true)").fetchone()==previous_scope
                # SQL 원감사의 caller 검증은 원업무 actor 그대로 둔다. Notebook 우연값에는 의존하지 않는다.
                connection.execute("SELECT set_config('app.actor_id',%s,true)",(ctx.actor_id,))
                previous_scope=(ctx.actor_id,other_notebook)
            # 정상 owner/설정에서는 SQL도 실제 저장 가능해야 한다. 시험 INSERT는 rollback한다.
            with connection.transaction(force_rollback=True):
                assert connection.execute('SELECT public.enqueue_studio_notification(%s,%s,%s)',
                    (event.origin_event_id,ctx.notebook_id,ctx.actor_id)).fetchone()==(True,)
                if case=='website_owner':
                    assert connection.execute("SELECT current_setting('app.actor_id',true),current_setting('app.notebook_id',true)").fetchone()==previous_scope
            if case=='law_connector':
                connection.execute("UPDATE workspace_connectors SET status='unavailable' WHERE tenant_id=%s AND workspace_id=%s AND connector_id='mcp-open-law-go-kr'",
                    (ctx.tenant_id,ctx.workspace_id))
            elif case=='law_policy':
                from daon_user_api.egress_policy import EgressPolicyContext
                from daon_user_api.egress_policy_postgres import PostgresEgressPolicyRepository
                class SameConnectionStore:
                    @contextmanager
                    def _transaction(self,_context):
                        # 기존 정책 저장 코드를 실제 원업무 connection에 조립한다. BEGIN/COMMIT/close는 없다.
                        yield connection
                policies=PostgresEgressPolicyRepository(SameConnectionStore())
                policy_context=EgressPolicyContext(ctx.tenant_id,ctx.tenant_id,ctx.workspace_id,ctx.actor_id,ctx.trace_id,ctx.policy_version)
                previous=policies.current(policy_context,'workspace')
                blocked=policies.create_and_activate(policy_context,'workspace',EgressPolicyPayload.deny_external(),
                    previous.etag,'s42-l2a-policy-block-key')
                assert blocked.payload.mode=='deny_external' and blocked.policy_version==previous.policy_version+1
            recipient=replace(context,actor_id=peer)
            if _can_receive_studio(connection,recipient,event,scope.version,ctx.actor_id):
                accepted.append('python')
            if case=='website_owner':
                assert connection.execute("SELECT current_setting('app.actor_id',true),current_setting('app.notebook_id',true)").fetchone()==previous_scope
            try:
                with connection.transaction(force_rollback=True):
                    connection.execute('SELECT public.enqueue_studio_notification(%s,%s,%s)',(event.origin_event_id,ctx.notebook_id,peer))
            except psycopg.Error as error:
                assert error.sqlstate in {'42501','23514'}
            else:
                accepted.append('sql')
            if case=='website_owner':
                assert connection.execute("SELECT current_setting('app.actor_id',true),current_setting('app.notebook_id',true)").fetchone()==previous_scope
            raise RuntimeError('S42 특별 Source 반례 종료')
        try:
            repo=PostgresStudioWorkspaceRepository(store)
            with patch('daon_user_api.notification_events.enqueue_studio_event',side_effect=probe):
                with pytest.raises(RuntimeError,match='S42 특별 Source 반례 종료'):
                    repo.record_action(ctx,'review',{'output_version_id':scope.version},'s42-l2a-special-review-key')
            assert _studio_case_counts(scope)==before
            assert accepted==[], f'{case} 현재 자료 접근을 허용함: {accepted}'
        finally:
            store.close()


def _pending_l2a_delivery(scope=None):
    """0069 새 합성 scope에서 정상 실제 producer로 pending source 한 건만 준비한다."""
    import psycopg
    from daon_user_api.cloud_storage import PostgresCloudStore
    from daon_user_api.studio_workspace_postgres import PostgresStudioWorkspaceRepository
    scope=scope if scope is not None else _existing_0069_studio_case()
    store=PostgresCloudStore(scope.app,min_size=0,max_size=2,notification_inbox_enabled=True)
    try:
        result,replayed=PostgresStudioWorkspaceRepository(store).record_action(scope.context,'delivery',
            {'output_version_id':scope.version,'approval_id':scope.approval,'recipient':'workspace_recipient'},'s42-l2a-consumer-delivery-key')
        assert not replayed
        scope.delivery_id=result['record_id']
    finally:
        store.close()
    with psycopg.connect(scope.admin) as connection:
        source=connection.execute('SELECT source_event_id,origin_event_id,state,recipient_id FROM notification_source_outbox '
            'WHERE tenant_id=%s AND workspace_id=%s',(scope.context.tenant_id,scope.context.workspace_id)).fetchall()
        assert len(source)==1 and source[0][2:] == ('pending',scope.context.actor_id)
        scope.source_event_id,scope.origin_event_id=source[0][:2]
        assert connection.execute('SELECT action,target_id FROM audit_events WHERE tenant_id=%s AND event_id=%s',
            (scope.context.tenant_id,scope.origin_event_id)).fetchone()==('studio.delivery',scope.delivery_id)
        assert connection.execute('SELECT count(*) FROM notifications WHERE tenant_id=%s',(scope.context.tenant_id,)).fetchone()==(0,)
    return scope


def _l2_current_read_materialized(domain, *, scope=None):
    """실제 원업무/원감사/source를 소비해 만든 알림만 사용한다. L1 합성 detail은 사용하지 않는다."""
    import psycopg
    import daon_user_api.notification_events as events
    from daon_user_api.cloud_storage import CloudAccessContext
    from daon_user_api.notification_postgres import PostgresNotificationRepository
    assert domain in ('document','question','studio')
    scope=_pending_l2a_delivery(scope) if domain=='studio' else _pending_l2b_completed(domain,scope=scope)
    ctx=scope.context
    materialize=events.materialize_pending_studio if domain=='studio' else events.materialize_pending_completed
    assert materialize(scope.app,context=ctx,limit=100)==1
    with psycopg.connect(scope.admin) as connection:
        rows=connection.execute('SELECT n.notification_id,n.source_event_id,d.source_store,d.origin_event_id,'
            'd.resource_type,d.resource_id,d.notebook_id,s.action,s.target_type,s.target_id,s.state,'
            'a.event_id,a.actor_id,a.outcome FROM notifications n JOIN notification_details d '
            'ON d.tenant_id=n.tenant_id AND d.notification_id=n.notification_id '
            'JOIN notification_source_outbox s ON s.tenant_id=n.tenant_id AND s.workspace_id=n.workspace_id '
            'AND s.source_event_id=n.source_event_id AND s.recipient_id=n.recipient_id '
            'JOIN audit_events a ON a.tenant_id=s.tenant_id AND a.workspace_id=s.workspace_id AND a.event_id=s.origin_event_id '
            'WHERE n.tenant_id=%s AND n.workspace_id=%s AND n.recipient_id=%s',
            (ctx.tenant_id,ctx.workspace_id,ctx.actor_id)).fetchall()
        assert len(rows)==1
        item=rows[0]
        action,target_type,target_id=(('studio.delivery','deliveries',scope.delivery_id) if domain=='studio' else
            ('canon.transition','ProcessingRun',scope.completed_run) if domain=='document' else
            ('question.answer','Run',scope.completed_run))
        assert item[1:]==(scope.source_event_id,domain,scope.origin_event_id,target_type,target_id,ctx.notebook_id,
            action,target_type,target_id,'delivered',scope.origin_event_id,ctx.actor_id,'succeeded')
        scope.current_notification_id=item[0]
    scope.current_source_version=scope.source_version_id if domain=='studio' else scope.completed_source_version
    repository=PostgresNotificationRepository(scope.app,
        context=CloudAccessContext(ctx.tenant_id,ctx.workspace_id,ctx.actor_id,'view'))
    before=_l2b_completion_snapshot(scope)
    items=repository.notifications_for(tenant_id=ctx.tenant_id,recipient_id=ctx.actor_id)
    assert len(items)==1 and items[0].notification_id==scope.current_notification_id
    original=repository.notification_for(tenant_id=ctx.tenant_id,recipient_id=ctx.actor_id,
        notification_id=scope.current_notification_id)
    assert original==items[0] and original.read_at is None and original.version==1
    assert (original.resource_type,original.resource_id)==(target_type,target_id)
    assert _l2b_completion_snapshot(scope)==before,'CURRENT_NOTIFICATION_GET_WROTE_ROWS'
    return scope,repository,original


def _l2_current_read_request(scope,repository,original):
    """실제 읽음 port의 영수증/의도를 사용한다. 중앙 append나 원자료 접근을 대체하지 않는다."""
    import hashlib
    from datetime import datetime,timezone
    from daon_user_api.audit import ActorType,AuditEventDraft,AuditOutcome
    ctx=scope.context
    def draft(before,after):
        return AuditEventDraft(event_id='s42-current-read-audit-'+scope.suffix,occurred_at=after.read_at,
            actor_id=ctx.actor_id,actor_type=ActorType.USER,tenant_id=ctx.tenant_id,workspace_id=ctx.workspace_id,
            action='notification.read',target_type='notification',target_id=before.notification_id,
            outcome=AuditOutcome.SUCCEEDED,trace_id=ctx.trace_id,policy_version=ctx.policy_version,
            before={'state':'unread','version':before.version},after={'state':'read','version':after.version},
            metadata={'source_event_id':before.source_event_id})
    return repository.transition_read(notification_id=original.notification_id,tenant_id=ctx.tenant_id,
        recipient_id=ctx.actor_id,expected_etag=original.etag,idempotency_key='s42-current-read-'+scope.suffix,
        fingerprint=hashlib.sha256(f'{ctx.actor_id}|{original.notification_id}|read|{original.etag}'.encode()).hexdigest(),
        read_at=datetime.now(timezone.utc),audit_draft_factory=draft,
        on_first_transition=lambda *_:pytest.fail('읽음 commit 전에 중앙 감사 side effect 호출'))


@pytest.mark.parametrize('domain',('document','question','studio'))
def test_l2_current_read_materialized_source_acl_denies_list_detail_and_first_read(domain):
    """실제 발행된 알림도 원자료 ACL 철회 뒤 목록/단건/첫 읽음에서 숨기고 영수증을 만들지 않는다."""
    from daon_user_api.notification import NotificationError
    scope,repository,original=_l2_current_read_materialized(domain)
    ctx=scope.context
    _l2b_deny_current_source(scope,scope.current_source_version)
    before=_l2b_completion_snapshot(scope)
    items=repository.notifications_for(tenant_id=ctx.tenant_id,recipient_id=ctx.actor_id)
    assert _l2b_completion_snapshot(scope)==before,'DENIED_NOTIFICATION_LIST_WROTE_ROWS'
    assert items==(),'MATERIALIZED_NOTIFICATION_LIST_EXPOSED_REVOKED_SOURCE'
    item=repository.notification_for(tenant_id=ctx.tenant_id,recipient_id=ctx.actor_id,
        notification_id=original.notification_id)
    assert _l2b_completion_snapshot(scope)==before,'DENIED_NOTIFICATION_GET_WROTE_ROWS'
    denial=None
    try:
        _l2_current_read_request(scope,repository,original)
    except NotificationError as error:
        denial=(error.code,error.http_status)
    # 기존 결함으로 쓰기가 진행돼도 본문/예외 원문 없이 세 경계와 부분 저장을 모두 관측한다.
    assert {'listed':bool(items),'detail':item is not None,'read_denial':denial,
        'rows_unchanged':_l2b_completion_snapshot(scope)==before}=={
        'listed':False,'detail':False,'read_denial':('RESOURCE_UNAVAILABLE',404),'rows_unchanged':True}


def test_l2_current_read_materialized_receipt_replay_rechecks_source_before_returning_read_state():
    """권한 있을 때 확정한 실제 읽음 영수증도 철회 뒤 replay에서 본문/옛 읽음 상태를 반환하지 않는다."""
    from daon_user_api.cloud_storage import CloudAccessContext
    from daon_user_api.notification import NotificationError
    from daon_user_api.notification_postgres import PostgresNotificationRepository
    scope,repository,original=_l2_current_read_materialized('question')
    ctx=scope.context
    changed=_l2_current_read_request(scope,repository,original)
    assert changed.first_transition is True and changed.notification.version==2 and changed.notification.read_at is not None
    before=_l2b_completion_snapshot(scope)
    replay=_l2_current_read_request(scope,repository,original)
    assert replay.first_transition is False and replay.notification==changed.notification
    assert _l2b_completion_snapshot(scope)==before
    _l2b_deny_current_source(scope,scope.current_source_version)
    before=_l2b_completion_snapshot(scope)
    reopened=PostgresNotificationRepository(scope.app,
        context=CloudAccessContext(ctx.tenant_id,ctx.workspace_id,ctx.actor_id,'view'))
    denial=None
    try:
        _l2_current_read_request(scope,reopened,original)
    except NotificationError as error:
        denial=(error.code,error.http_status)
    assert {'denial':denial,'rows_unchanged':_l2b_completion_snapshot(scope)==before}=={
        'denial':('RESOURCE_UNAVAILABLE',404),'rows_unchanged':True}


def _l2_current_read_boundary_snapshot(scope):
    """조회 대상의 원사건뿐 아니라 현재 결속/삭제/조직 상태도 조회 중 변경되지 않았는지 확인한다."""
    import psycopg
    snapshot=_l2b_completion_snapshot(scope)
    with psycopg.connect(scope.admin) as connection:
        for table in ('notebooks','notebook_metadata_versions','notebook_deletion_requests',
            'notebook_source_unbindings','notebook_source_unbinding_idempotency','identity_org_tenant_memberships'):
            snapshot[table]=connection.execute(f'SELECT to_jsonb(r) FROM public.{table} r '
                'WHERE tenant_id=%s ORDER BY to_jsonb(r)::text',(scope.context.tenant_id,)).fetchall()
    return snapshot


@pytest.mark.parametrize('case',('source_unbound','notebook_deletion','organization_suspended'))
def test_l2_current_read_boundaries_reject_current_binding_deletion_and_suspension(case):
    """실제 발행 뒤 현재 자료 결속/삭제 요청/조직 중지는 본문·읽음 접근을 차단한다. 원사건은 보존한다."""
    from datetime import datetime,timezone
    import psycopg
    from daon_user_api.cloud_storage import PostgresCloudStore
    from daon_user_api.notebook import NotebookContext
    from daon_user_api.notebook_postgres import PostgresNotebookRepository
    from daon_user_api.notification import NotificationError
    domain={'source_unbound':'studio','notebook_deletion':'document','organization_suspended':'question'}[case]
    prepared_scope=_existing_0069_studio_case(workspace_kind='organization') if case=='organization_suspended' else None
    scope,repository,original=_l2_current_read_materialized(domain,scope=prepared_scope)
    ctx=scope.context
    store=PostgresCloudStore(scope.app,min_size=0,max_size=2,notification_inbox_enabled=True)
    try:
        notebooks=PostgresNotebookRepository(store)
        notebook_context=NotebookContext(ctx.tenant_id,ctx.workspace_id,ctx.actor_id,ctx.trace_id,ctx.policy_version)
        if case=='source_unbound':
            with psycopg.connect(scope.admin) as connection:
                source=connection.execute('SELECT source_id FROM source_versions WHERE tenant_id=%s '
                    'AND workspace_id=%s AND record_id=%s',
                    (ctx.tenant_id,ctx.workspace_id,scope.current_source_version)).fetchone()[0]
                assert connection.execute('SELECT count(*) FROM notebook_source_unbindings WHERE tenant_id=%s '
                    'AND workspace_id=%s AND notebook_id=%s',(ctx.tenant_id,ctx.workspace_id,ctx.notebook_id)).fetchone()==(0,)
            changed,replayed=notebooks.unbind_source(notebook_context,ctx.notebook_id,source_id=source,
                source_version_id=scope.current_source_version,expected_etag='"notebook-binding:1"',
                idempotency_key='s42-current-unbind-'+scope.suffix,request_fingerprint='e'*64,now=datetime.now(timezone.utc))
            assert replayed is False and changed.status=='unbound'
            with psycopg.connect(scope.admin) as connection:
                assert connection.execute('SELECT source_id,source_version_id,actor_id FROM notebook_source_unbindings '
                    'WHERE tenant_id=%s AND workspace_id=%s AND notebook_id=%s',
                    (ctx.tenant_id,ctx.workspace_id,ctx.notebook_id)).fetchall()==[(source,scope.current_source_version,ctx.actor_id)]
                assert connection.execute('SELECT count(*) FROM notebook_bindings WHERE tenant_id=%s AND workspace_id=%s '
                    "AND notebook_id=%s AND binding_kind='source' AND record_id=%s AND version_id=%s",
                    (ctx.tenant_id,ctx.workspace_id,ctx.notebook_id,source,scope.current_source_version)).fetchone()==(1,)
        elif case=='notebook_deletion':
            title='S42 현재 알림 삭제 요청 경계'
            with psycopg.connect(scope.admin) as connection:
                assert connection.execute('SELECT count(*) FROM notebook_metadata_versions WHERE tenant_id=%s '
                    'AND workspace_id=%s AND notebook_id=%s',(ctx.tenant_id,ctx.workspace_id,ctx.notebook_id)).fetchone()==(0,)
                connection.execute('INSERT INTO notebook_metadata_versions '
                    '(tenant_id,workspace_id,notebook_id,version,title,description,is_current,updated_by,updated_at) '
                    'VALUES(%s,%s,%s,1,%s,NULL,true,%s,now())',
                    (ctx.tenant_id,ctx.workspace_id,ctx.notebook_id,title,ctx.actor_id))
            deletion,replayed=notebooks.request_deletion(notebook_context,ctx.notebook_id,title_confirmation=title,
                expected_etag='"notebook:1"',idempotency_key='s42-current-deletion-'+scope.suffix,
                request_fingerprint='f'*64,now=datetime.now(timezone.utc))
            assert replayed is False and (deletion.state,deletion.current_step,deletion.attempts,deletion.completed_at)==(
                'accepted','accepted',0,None)
            with psycopg.connect(scope.admin) as connection:
                assert connection.execute('SELECT 1 FROM notebooks WHERE tenant_id=%s AND workspace_id=%s AND notebook_id=%s',
                    (ctx.tenant_id,ctx.workspace_id,ctx.notebook_id)).fetchone()==(1,)
        else:
            # 해당 합성 사용자에게만 기존 workflow 행을 준비한다. identity/auth 역할·공유 role은 바꾸지 않는다.
            with psycopg.connect(scope.admin) as connection:
                assert connection.execute('SELECT w.workspace_kind,r.role FROM identity_auth_workspaces w '
                    'JOIN identity_auth_tenant_roles r ON r.tenant_id=w.tenant_id '
                    'WHERE w.tenant_id=%s AND w.workspace_id=%s AND r.user_id=%s',
                    (ctx.tenant_id,ctx.workspace_id,ctx.actor_id)).fetchone()==('organization','organization_admin')
                assert connection.execute('INSERT INTO identity_org_tenant_memberships '
                    '(tenant_id,user_id,role,state,version,updated_at,join_workspace_id) '
                    "VALUES(%s,%s,'member','active',1,now(),%s) RETURNING state,version",
                    (ctx.tenant_id,ctx.actor_id,ctx.workspace_id)).fetchone()==('active',1)
            before_active=_l2_current_read_boundary_snapshot(scope)
            assert repository.notification_for(tenant_id=ctx.tenant_id,recipient_id=ctx.actor_id,
                notification_id=original.notification_id)==original
            assert _l2_current_read_boundary_snapshot(scope)==before_active
            with psycopg.connect(scope.admin) as connection:
                assert connection.execute("UPDATE identity_org_tenant_memberships SET state='suspended',version=version+1,updated_at=now() "
                    "WHERE tenant_id=%s AND user_id=%s AND state='active' AND version=1 RETURNING state,version",
                    (ctx.tenant_id,ctx.actor_id)).fetchone()==('suspended',2)
        before=_l2_current_read_boundary_snapshot(scope)
        assert repository.notifications_for(tenant_id=ctx.tenant_id,recipient_id=ctx.actor_id)==()
        assert repository.notification_for(tenant_id=ctx.tenant_id,recipient_id=ctx.actor_id,
            notification_id=original.notification_id) is None
        with pytest.raises(NotificationError) as denied:
            _l2_current_read_request(scope,repository,original)
        assert (denied.value.code,denied.value.http_status)==('RESOURCE_UNAVAILABLE',404)
        assert _l2_current_read_boundary_snapshot(scope)==before
    finally:
        store.close()


def test_l2_current_read_boundaries_sql_failure_is_503_not_hidden_or_empty():
    """현재 원자료 검사 중 실제 SQL 실패는 목록/단건/읽음에서 503이며 원장·영수증을 변경하지 않는다."""
    from unittest.mock import patch
    import psycopg
    import daon_user_api.notification_events as events
    from daon_user_api.notification import NotificationError
    scope,repository,original=_l2_current_read_materialized('question')
    ctx=scope.context
    actual_access=events._completed_event_access
    phases=[]
    def sql_failure(connection,context,**kwargs):
        recipient=actual_access(connection,context,**kwargs)
        assert recipient is not None and recipient.actor_id==ctx.actor_id
        try:
            connection.execute('SELECT 1/0')
        except psycopg.Error as error:
            phases.append((error.sqlstate,connection.info.transaction_status.name))
            raise
    before=_l2_current_read_boundary_snapshot(scope)
    calls=(lambda:repository.notifications_for(tenant_id=ctx.tenant_id,recipient_id=ctx.actor_id),
        lambda:repository.notification_for(tenant_id=ctx.tenant_id,recipient_id=ctx.actor_id,notification_id=original.notification_id),
        lambda:_l2_current_read_request(scope,repository,original))
    with patch.object(events,'_completed_event_access',new=sql_failure):
        for invoke in calls:
            with pytest.raises(NotificationError) as failed:
                invoke()
            assert (failed.value.code,failed.value.http_status)==('DATABASE_UNAVAILABLE',503)
            assert _l2_current_read_boundary_snapshot(scope)==before
    assert phases==[('22012','INERROR')]*3
    # 실패 뒤 새 transaction의 정상 조회가 가능하며 장애를 빈 목록으로 확정하지 않았다.
    assert repository.notification_for(tenant_id=ctx.tenant_id,recipient_id=ctx.actor_id,
        notification_id=original.notification_id)==original
    assert _l2_current_read_boundary_snapshot(scope)==before


def test_l2_current_read_boundaries_private_provenance_cannot_substitute_source_or_notebook():
    """불변행을 변조하지 않고 같은 실제 원사건의 private 입력만 바꾸어 source/Notebook 대체를 거부한다."""
    from datetime import datetime,timezone
    import psycopg
    import daon_user_api.notification_events as events
    from daon_user_api.cloud_storage import PostgresCloudStore
    from daon_user_api.notebook import NotebookContext
    from daon_user_api.notebook_postgres import PostgresNotebookRepository
    from daon_user_api.notification_postgres import _SELECT
    scope,repository,original=_l2_current_read_materialized('question')
    ctx=scope.context
    store=PostgresCloudStore(scope.app,min_size=0,max_size=2,notification_inbox_enabled=True)
    try:
        other,replayed=PostgresNotebookRepository(store).create(
            NotebookContext(ctx.tenant_id,ctx.workspace_id,ctx.actor_id,ctx.trace_id,ctx.policy_version),
            title='S42 다른 결속의 QA Notebook',description=None,idempotency_key='s42-current-other-'+scope.suffix,
            request_fingerprint='a'*64,now=datetime.now(timezone.utc))
        assert replayed is False and other.notebook_id!=ctx.notebook_id
        with psycopg.connect(scope.admin) as connection:
            other_origin=connection.execute("SELECT event_id FROM audit_events WHERE tenant_id=%s AND workspace_id=%s "
                "AND action='notebook.created' AND target_id=%s AND actor_id=%s",
                (ctx.tenant_id,ctx.workspace_id,other.notebook_id,ctx.actor_id)).fetchone()[0]
        before=_l2_current_read_boundary_snapshot(scope)
        with repository.transaction() as connection:
            row=connection.execute(_SELECT+' AND n.notification_id=%s',
                (ctx.tenant_id,ctx.workspace_id,ctx.actor_id,original.notification_id)).fetchone()
            assert row is not None and row['_source_store']=='question' and row['_notebook_id']==ctx.notebook_id
            settings="SELECT current_setting('app.actor_id',true) AS actor,NULLIF(current_setting('app.notebook_id',true),'') AS notebook"
            scope_before=connection.execute(settings).fetchone()
            resolved=events.resolve_notification_target(connection,repository.context,original,provenance=row)
            assert resolved is not None and (resolved.notebook_id,resolved.resource_type,resolved.resource_id,resolved.deep_link)==(
                ctx.notebook_id,original.resource_type,original.resource_id,original.deep_link)
            for changed in ({'_source_store':'recovery'},{'_origin_event_id':other_origin},{'_notebook_id':other.notebook_id}):
                assert events.resolve_notification_target(connection,repository.context,original,provenance={**row,**changed}) is None
                assert connection.execute(settings).fetchone()==scope_before
            assert events.resolve_notification_target(connection,repository.context,original,provenance=row)==resolved
        assert repository.notification_for(tenant_id=ctx.tenant_id,recipient_id=ctx.actor_id,
            notification_id=original.notification_id)==original
        assert _l2_current_read_boundary_snapshot(scope)==before
    finally:
        store.close()


def test_l2a_existing_0069_materializes_registered_source_and_reopens_without_duplicate():
    """실제 source를 읽어 지속 알림/상세/created 의도/ack를 만들고 재연결 시 중복하지 않는다."""
    import psycopg
    import daon_user_api.notification_events as events
    from daon_user_api.notification_postgres import PostgresNotificationRepository
    from daon_user_api.cloud_storage import CloudAccessContext
    scope=_pending_l2a_delivery()
    ctx=scope.context
    assert events.materialize_pending_studio(scope.app,context=ctx,limit=100)==1
    with psycopg.connect(scope.admin) as connection:
        rows=connection.execute('SELECT n.notification_id,n.recipient_id,n.source_event_id,n.version,n.read_at,'
            'd.source_store,d.origin_event_id,d.resource_type,d.resource_id,d.notebook_id,d.deep_link,d.trace_id,d.policy_version,d.delivery_state '
            'FROM notifications n JOIN notification_details d ON d.tenant_id=n.tenant_id AND d.notification_id=n.notification_id '
            'WHERE n.tenant_id=%s AND n.workspace_id=%s',(ctx.tenant_id,ctx.workspace_id)).fetchall()
        assert len(rows)==1
        item=rows[0]
        assert item[1:]==(ctx.actor_id,scope.source_event_id,1,None,'studio',scope.origin_event_id,'deliveries',scope.delivery_id,
            ctx.notebook_id,f'/notebooks/{ctx.notebook_id}?output_version_id={scope.version}',ctx.trace_id,ctx.policy_version,'delivered')
        assert connection.execute('SELECT state,version,delivered_at IS NOT NULL FROM notification_source_outbox '
            'WHERE tenant_id=%s AND source_event_id=%s',(ctx.tenant_id,scope.source_event_id)).fetchall()==[('delivered',2,True)]
        audits=connection.execute('SELECT action,state,draft FROM notification_audit_outbox WHERE tenant_id=%s',(ctx.tenant_id,)).fetchall()
        assert len(audits)==1 and audits[0][:2]==('notification.created','pending')
        draft=audits[0][2]
        assert (draft['tenant_id'],draft['workspace_id'],draft['target_id'],draft['trace_id'],draft['policy_version'],draft['metadata'])==(
            ctx.tenant_id,ctx.workspace_id,item[0],ctx.trace_id,ctx.policy_version,{'source_event_id':scope.source_event_id})
        times=connection.execute('SELECT n.created_at,s.delivered_at,(a.draft->>\'occurred_at\')::timestamptz,'
            's.occurred_at,o.occurred_at FROM notifications n JOIN notification_details d '
            'ON d.tenant_id=n.tenant_id AND d.notification_id=n.notification_id '
            'JOIN notification_source_outbox s ON s.tenant_id=n.tenant_id AND s.workspace_id=n.workspace_id '
            'AND s.recipient_id=n.recipient_id AND s.source_event_id=n.source_event_id '
            'JOIN notification_audit_outbox a ON a.tenant_id=n.tenant_id AND a.notification_id=n.notification_id '
            "AND a.action='notification.created' JOIN audit_events o ON o.tenant_id=s.tenant_id "
            'AND o.workspace_id=s.workspace_id AND o.event_id=s.origin_event_id '
            'WHERE n.tenant_id=%s AND n.notification_id=%s',(ctx.tenant_id,item[0])).fetchall()
        assert len(times)==1
        created_at,source_delivered_at,created_audit_at,source_occurred_at,origin_occurred_at=times[0]
        assert created_at>=source_delivered_at
        assert created_audit_at==created_at
        assert source_occurred_at==origin_occurred_at
        # 중앙 전달은 소비 commit 이후 별도 단계다. 원감사와 security 감사 저장을 혼동하지 않는다.
        assert connection.execute('SELECT count(*) FROM security_audit_events WHERE tenant_id=%s',(ctx.tenant_id,)).fetchone()==(0,)
    # 새 저장소 객체/새 connection에서 현재 scope의 같은 알림을 다시 읽는다. 새 process 증거는 별도 시험이다.
    repository=PostgresNotificationRepository(scope.app,context=CloudAccessContext(ctx.tenant_id,ctx.workspace_id,ctx.actor_id,'view'))
    reopened=repository.notifications_for(tenant_id=ctx.tenant_id,recipient_id=ctx.actor_id)
    assert len(reopened)==1 and reopened[0].notification_id==item[0] and reopened[0].resource_id==scope.delivery_id
    assert events.materialize_pending_studio(scope.app,context=ctx,limit=100)==0
    with psycopg.connect(scope.admin) as connection:
        assert connection.execute('SELECT count(*) FROM notifications WHERE tenant_id=%s',(ctx.tenant_id,)).fetchone()==(1,)
        assert connection.execute('SELECT count(*) FROM notification_audit_outbox WHERE tenant_id=%s',(ctx.tenant_id,)).fetchone()==(1,)


def test_l2a_existing_0069_consumer_policy_sql_failure_keeps_pending():
    """공통 law helper가 감싼 실제 SQL 실패도 권한 철회/suppressed로 확정하면 안 된다."""
    from unittest.mock import patch
    import psycopg
    import daon_user_api.notification_events as events
    from daon_user_api.notification import NotificationError
    scope=_existing_0069_studio_case()
    ctx=scope.context
    with pytest.MonkeyPatch.context() as environment:
        environment.setenv('DAON_OPEN_LAW_ENABLED','true')
        environment.setenv('DAON_OPEN_LAW_QA_WORKSPACE_ID',ctx.workspace_id)
        _studio_case_special_source(scope,'law')
        _pending_l2a_delivery(scope)
        before=_studio_case_counts(scope)
        def fail_policy_select(connection,*_args,**_kwargs):
            # 대역 예외가 아니라 실제 PG SQLSTATE 22012/INERROR를 만든다. 표/role/자료를 변조하지 않는다.
            connection.execute('SELECT 1/0')
        with patch('daon_user_api.egress_policy_postgres.PostgresEgressPolicyRepository._select_current',side_effect=fail_policy_select):
            with pytest.raises(NotificationError) as failed:
                events.materialize_pending_studio(scope.app,context=ctx,limit=100)
            assert (failed.value.code,failed.value.http_status)==('DATABASE_UNAVAILABLE',503)
        assert _studio_case_counts(scope)==before
        with psycopg.connect(scope.admin) as connection:
            assert connection.execute('SELECT state,version,delivered_at FROM notification_source_outbox WHERE tenant_id=%s',
                (ctx.tenant_id,)).fetchall()==[('pending',1,None)]
            for table in ('notifications','notification_details','notification_audit_outbox','security_audit_events'):
                assert connection.execute(f'SELECT count(*) FROM public.{table} WHERE tenant_id=%s',(ctx.tenant_id,)).fetchone()==(0,)


@pytest.mark.parametrize('denied,requested,expected', [
    (False,False,'pending'),(True,False,'suppressed'),(True,True,'suppressed')])
def test_l2a_existing_0069_materialize_intent_cannot_override_current_access(denied,requested,expected):
    """의도 bool은 승인 증명이 아니다. 허용된 의도의 임의 억제와 철회 후 발행을 모두 막는다."""
    import psycopg
    from daon_user_api.notification_events import make_studio_event,_can_receive_studio
    scope=_pending_l2a_delivery()
    ctx=scope.context
    event=make_studio_event(ctx,operation='studio.delivery',target_type='deliveries',
        target_id=scope.delivery_id,origin_event_id=scope.origin_event_id)
    with psycopg.connect(scope.admin) as connection:
        with connection.transaction(force_rollback=True):
            if denied:
                connection.execute('INSERT INTO identity_auth_source_access(tenant_id,workspace_id,user_id,'
                    'source_version_id,allowed,version,updated_at) VALUES(%s,%s,%s,%s,0,1,now())',
                    (ctx.tenant_id,ctx.workspace_id,ctx.actor_id,scope.source_version_id))
            connection.execute('SET LOCAL ROLE daon_app')
            for name,value in (('tenant_id',ctx.tenant_id),('workspace_id',ctx.workspace_id),('actor_id',ctx.actor_id)):
                connection.execute('SELECT set_config(%s,%s,true)',('app.'+name,value))
            assert connection.execute('SELECT current_user').fetchone()==('daon_app',)
            assert _can_receive_studio(connection,ctx,event,scope.version,ctx.actor_id) is (not denied)
            result=connection.execute('SELECT public.materialize_studio_notification(%s,%s)',
                (scope.source_event_id,requested)).fetchone()[0]
            assert result==(None if expected=='pending' else {'state':'suppressed'})
            if expected=='pending':
                # 같은 False 재호출도 ack하지 않고, 이후 정상 소비를 방해하지 않는다.
                assert connection.execute('SELECT public.materialize_studio_notification(%s,false)',
                    (scope.source_event_id,)).fetchone()==(None,)
            # 호출의 app 권한을 확인한 뒤 fixture 소유자로 미commit 상태만 검사한다.
            connection.execute('RESET ROLE')
            assert connection.execute('SELECT state,version,delivered_at IS NOT NULL FROM notification_source_outbox '
                'WHERE tenant_id=%s AND source_event_id=%s',(ctx.tenant_id,scope.source_event_id)).fetchall()==[
                    (expected,1 if expected=='pending' else 2,expected!='pending')]
            for table in ('notifications','notification_details','notification_audit_outbox'):
                assert connection.execute(f'SELECT count(*) FROM public.{table} WHERE tenant_id=%s',(ctx.tenant_id,)).fetchone()==(0,)
    with psycopg.connect(scope.admin) as connection:
        assert connection.execute('SELECT state,version,delivered_at FROM notification_source_outbox '
            'WHERE tenant_id=%s AND source_event_id=%s',(ctx.tenant_id,scope.source_event_id)).fetchall()==[('pending',1,None)]


def test_l2a_existing_0069_security_audit_delivery_defers_failures_and_retries_without_duplicate():
    """실제 중앙 PG 감사의 commit과 별도 ack 실패를 구분하고 안정 ID/내용으로 재전달한다."""
    from dataclasses import fields
    from unittest.mock import patch
    import psycopg
    import daon_user_api.notification_events as events
    from daon_user_api.audit import AuditEventDraft,PostgresSecurityAuditStore
    from daon_user_api.notification_postgres import _time,serialize_notification_audit
    scope=_pending_l2a_delivery()
    ctx=scope.context
    assert events.materialize_pending_studio(scope.app,context=ctx,limit=100)==1

    def state():
        with psycopg.connect(scope.admin) as connection:
            intents=connection.execute('SELECT event_id,state,version,draft,fingerprint,delivered_at IS NOT NULL '
                'FROM notification_audit_outbox WHERE tenant_id=%s',(ctx.tenant_id,)).fetchall()
            central=connection.execute('SELECT event_id,event_hash FROM security_audit_events WHERE tenant_id=%s',
                (ctx.tenant_id,)).fetchall()
            durable=tuple(connection.execute(f'SELECT count(*) FROM public.{table} WHERE tenant_id=%s',
                (ctx.tenant_id,)).fetchone()[0] for table in ('notifications','notification_details'))
            source=connection.execute('SELECT state,version FROM notification_source_outbox WHERE tenant_id=%s',
                (ctx.tenant_id,)).fetchall()
        assert durable==(1,1) and source==[('delivered',2)]
        assert len(intents)==1
        return intents[0],central

    original,central=state()
    assert original[1:3]==('pending',1) and not original[5] and central==[]
    store=PostgresSecurityAuditStore(scope.app,min_size=0,max_size=2)
    faults=[]
    def sink_unavailable(_draft):
        # 중앙 저장소의 실제 connection에 SQL 실패를 주입한다. 원업무/알림 Tx는 이미 commit됐다.
        with store._pool.connection(timeout=2.0) as connection:
            try:
                connection.execute('SELECT 1/0')
            except psycopg.Error as error:
                faults.append(error.sqlstate)
                raise
    def ack_unavailable(*_args,**_kwargs):
        # append가 commit된 뒤, ack 단계에서만 실제 PG 오류를 만든다.
        with psycopg.connect(scope.app) as connection:
            try:
                connection.execute('SELECT 1/0')
            except psycopg.Error as error:
                faults.append(error.sqlstate)
                raise
    try:
        store._ensure_open()
        with patch.object(store,'_append_admin_role',side_effect=sink_unavailable):
            assert events.dispatch_pending_notification_audits(scope.app,context=ctx,audit_store=store)==0
        assert faults==['22012'] and state()==(original,[])
        with patch.object(events,'_ack_notification_audit',side_effect=ack_unavailable):
            assert events.dispatch_pending_notification_audits(scope.app,context=ctx,audit_store=store)==0
        pending,central=state()
        assert faults==['22012','22012'] and pending==original
        assert len(central)==1 and central[0][0]==original[0]
        delivered=store.read(original[0],tenant_id=ctx.tenant_id)
        assert delivered is not None
        draft=AuditEventDraft(**{field.name:getattr(delivered,field.name) for field in fields(AuditEventDraft)})
        expected_draft=dict(original[3],occurred_at=_time(original[3]['occurred_at']).isoformat())
        assert serialize_notification_audit(draft)==expected_draft
    finally:
        store.close()
    # 새 중앙 저장소 객체/새 연결에서도 기전달 ID의 내용 일치를 확인하고 ack만 수행한다.
    reopened=PostgresSecurityAuditStore(scope.app,min_size=0,max_size=2)
    try:
        assert events.dispatch_pending_notification_audits(scope.app,context=ctx,audit_store=reopened)==1
        after,central_after=state()
        assert (after[0],after[1],after[2],after[3],after[4],after[5])==(
            original[0],'delivered',2,original[3],original[4],True)
        assert central_after==central
        assert events.dispatch_pending_notification_audits(scope.app,context=ctx,audit_store=reopened)==0
        assert state()==(after,central_after)
    finally:
        reopened.close()


def test_l2a_existing_0069_two_connections_materialize_and_deliver_audit_once():
    """두 실제 backend에서 같은 의도를 동시에 소비/전달해도 알림과 중앙 감사는 각각 하나다."""
    from concurrent.futures import ThreadPoolExecutor
    from contextlib import ExitStack
    from threading import Barrier,Lock
    from unittest.mock import patch
    import psycopg
    import daon_user_api.notification_events as events
    from daon_user_api.audit import PostgresSecurityAuditStore
    scope=_pending_l2a_delivery()
    ctx=scope.context
    barrier=Barrier(2)
    guard=Lock()
    backend_ids=[]
    original_connect=psycopg.connect
    def simultaneous_connect(*args,**kwargs):
        connection=original_connect(*args,**kwargs)
        try:
            with guard:
                backend_ids.append(connection.info.backend_pid)
            barrier.wait(timeout=5)
            return connection
        except BaseException:
            connection.close()
            raise
    with patch('psycopg.connect',side_effect=simultaneous_connect):
        with ThreadPoolExecutor(max_workers=2) as executor:
            futures=[executor.submit(events.materialize_pending_studio,scope.app,context=ctx,limit=100) for _ in range(2)]
            results=[future.result(timeout=15) for future in futures]
    assert len(backend_ids)==2 and len(set(backend_ids))==2
    assert sorted(results)==[0,1]

    stores=[PostgresSecurityAuditStore(scope.app,min_size=0,max_size=2) for _ in range(2)]
    append_barrier=Barrier(2)
    audit_backend_ids=[]
    def competing_append(store,append):
        def run(draft):
            # 두 전달자의 미저장 판정 후 별도 실제 PG 연결을 확인하고 원래 append를 경합시킨다.
            with store._pool.connection(timeout=2.0) as connection:
                pid,role=connection.execute('SELECT pg_backend_pid(),current_user').fetchone()
                assert role=='daon_app'
                with guard:
                    audit_backend_ids.append(pid)
            append_barrier.wait(timeout=5)
            return append(draft)
        return run
    try:
        with ExitStack() as patches:
            for store in stores:
                patches.enter_context(patch.object(store,'_append_admin_role',
                    side_effect=competing_append(store,store._append_admin_role)))
            with ThreadPoolExecutor(max_workers=2) as executor:
                futures=[executor.submit(events.dispatch_pending_notification_audits,scope.app,
                    context=ctx,audit_store=store) for store in stores]
                deliveries=[future.result(timeout=15) for future in futures]
        assert len(audit_backend_ids)==2 and len(set(audit_backend_ids))==2
        assert sorted(deliveries)==[0,1]
    finally:
        for store in stores:
            store.close()
    with psycopg.connect(scope.admin) as connection:
        for table in ('notifications','notification_details','notification_audit_outbox','security_audit_events'):
            assert connection.execute(f'SELECT count(*) FROM public.{table} WHERE tenant_id=%s',(ctx.tenant_id,)).fetchone()==(1,)
        assert connection.execute('SELECT state,version FROM notification_source_outbox WHERE tenant_id=%s',
            (ctx.tenant_id,)).fetchall()==[('delivered',2)]
        assert connection.execute('SELECT state,version FROM notification_audit_outbox WHERE tenant_id=%s',
            (ctx.tenant_id,)).fetchall()==[('delivered',2)]


def test_l2a_existing_0069_new_python_process_reads_persisted_notification_without_audit_duplicate():
    """새 Python process의 실제 저장 조회/재소비/재전달이다. 브라우저나 Provider 증거는 아니다."""
    from dataclasses import asdict
    from unittest.mock import patch
    import json
    import subprocess
    import sys
    import psycopg
    import daon_user_api.notification_events as events
    from daon_user_api.audit import PostgresSecurityAuditStore
    from daon_user_api.notification_postgres import PostgresNotificationRepository
    scope=_pending_l2a_delivery()
    ctx=scope.context
    assert events.materialize_pending_studio(scope.app,context=ctx)==1
    store=PostgresSecurityAuditStore(scope.app,min_size=0,max_size=2)
    diagnostic={'stage':'PARENT_READY','failures':[],'in_ack':False}
    original_transaction=PostgresNotificationRepository.transaction
    original_ack=events._ack_notification_audit
    def failure(stage,error):
        # 메시지/args/connection/params는 절대 직렬화하지 않는다. 감싼 SQLSTATE만 안전 추출한다.
        cause=error
        seen=set()
        sqlstate='-'
        while cause is not None and id(cause) not in seen:
            seen.add(id(cause))
            value=getattr(cause,'sqlstate',None)
            if isinstance(value,str) and len(value)==5 and all(c in '0123456789ABCDEFGHIJKLMNOPQRSTUVWXYZ' for c in value):
                sqlstate=value
                break
            cause=cause.__cause__ or cause.__context__
        name=type(error).__name__
        safe_class=name if len(name)<=80 and all(c.isascii() and (c.isalnum() or c=='_') for c in name) else 'Exception'
        diagnostic['stage']=stage+'_EXCEPTION'
        diagnostic['failures'].append((stage,safe_class,sqlstate))
    def observe(stage,call):
        def run(*args,**kwargs):
            diagnostic['stage']=stage+'_ENTER'
            try:
                result=call(*args,**kwargs)
            except Exception as error:
                failure(stage,error)
                raise
            diagnostic['stage']=stage+('_EMPTY' if result is None else '_FALSE' if result is False else '_RETURNED')
            return result
        return run
    @contextmanager
    def observed_transaction(repository):
        stage='ACK_TX' if diagnostic['in_ack'] else 'PENDING_TX'
        diagnostic['stage']=stage+'_ENTER'
        try:
            with original_transaction(repository) as connection:
                yield connection
        except Exception as error:
            failure(stage,error)
            raise
        else:
            diagnostic['stage']=stage+'_COMMITTED'
    def observed_ack(*args,**kwargs):
        diagnostic['in_ack']=True
        try:
            return observe('ACK',original_ack)(*args,**kwargs)
        finally:
            diagnostic['in_ack']=False
    try:
        with patch.object(PostgresNotificationRepository,'transaction',new=observed_transaction), \
             patch.object(store,'read',new=observe('CENTRAL_READ',store.read)), \
             patch.object(store,'_append_admin_role',new=observe('CENTRAL_APPEND',store._append_admin_role)), \
             patch.object(events,'_ack_notification_audit',new=observed_ack):
            try:
                dispatched=events.dispatch_pending_notification_audits(scope.app,context=ctx,audit_store=store)
            except Exception as error:
                failure('PARENT_DISPATCH',error)
                dispatched=None
            if dispatched!=1:
                pytest.fail('PARENT_DISPATCH_NOT_ONE; CHILD_NOT_STARTED; stage='+diagnostic['stage']+
                    '; failures='+json.dumps(diagnostic['failures'],ensure_ascii=True),pytrace=False)
    finally:
        store.close()
    with psycopg.connect(scope.admin) as connection:
        item=connection.execute('SELECT notification_id,version,read_at FROM notifications WHERE tenant_id=%s',
            (ctx.tenant_id,)).fetchall()
        central=connection.execute('SELECT event_id,event_hash FROM security_audit_events WHERE tenant_id=%s',
            (ctx.tenant_id,)).fetchall()
        assert len(item)==1 and len(central)==1
    code="""import json,os,sys
from psycopg.conninfo import conninfo_to_dict
from daon_user_api.audit import PostgresSecurityAuditStore
from daon_user_api.cloud_storage import CloudAccessContext
from daon_user_api.notification_postgres import PostgresNotificationRepository
from daon_user_api.notification_events import materialize_pending_studio,dispatch_pending_notification_audits
from daon_user_api.studio_workspace import StudioContext
dsn=os.environ['DAON_NOTIFICATION_TEST_DSN']
assert conninfo_to_dict(dsn).get('dbname')=='daon_user_s42_qa_20261017_l2'
ctx=StudioContext(**json.loads(sys.stdin.read()))
repo=PostgresNotificationRepository(dsn,context=CloudAccessContext(ctx.tenant_id,ctx.workspace_id,ctx.actor_id,'view'))
items=repo.notifications_for(tenant_id=ctx.tenant_id,recipient_id=ctx.actor_id)
assert len(items)==1
item=items[0]
assert materialize_pending_studio(dsn,context=ctx)==0
store=PostgresSecurityAuditStore(dsn,min_size=0,max_size=2)
try:
    assert dispatch_pending_notification_audits(dsn,context=ctx,audit_store=store)==0
    event=store.read(item.audit_event_id,tenant_id=ctx.tenant_id)
    assert event is not None and event.target_id==item.notification_id
    print(json.dumps([os.getpid(),item.notification_id,item.version,item.read_at,item.resource_id,item.deep_link,event.event_id,event.event_hash]))
finally:
    store.close()
"""
    environment=dict(os.environ,DAON_NOTIFICATION_TEST_DSN=scope.app,
        PYTHONPATH=str(Path(__file__).resolve().parents[1]/'src'),PYTHONIOENCODING='utf-8')
    process=subprocess.run([sys.executable,'-B','-c',code],input=json.dumps(asdict(ctx)),
        env=environment,capture_output=True,text=True,encoding='utf-8',timeout=15)
    if process.returncode!=0:
        pytest.fail('CHILD_PROCESS_FAILED; exit='+str(process.returncode)+' (접속 원문 출력 금지)',pytrace=False)
    readback=json.loads(process.stdout)
    assert readback[0]!=os.getpid()
    assert readback[1:]==[item[0][0],1,None,scope.delivery_id,
        f'/notebooks/{ctx.notebook_id}?output_version_id={scope.version}',central[0][0],central[0][1]]
    with psycopg.connect(scope.admin) as connection:
        assert connection.execute('SELECT event_id,event_hash FROM security_audit_events WHERE tenant_id=%s',
            (ctx.tenant_id,)).fetchall()==central
        assert connection.execute('SELECT count(*) FROM notifications WHERE tenant_id=%s',(ctx.tenant_id,)).fetchone()==(1,)
        assert connection.execute('SELECT state,version FROM notification_audit_outbox WHERE tenant_id=%s',
            (ctx.tenant_id,)).fetchall()==[('delivered',2)]


def test_l2a_existing_0069_different_central_content_keeps_intent_pending():
    """같은 event ID의 실제 중앙 내용이 다르면 덮어쓰기/ack하지 않는다."""
    from dataclasses import replace
    import psycopg
    import daon_user_api.notification_events as events
    from daon_user_api.audit import ActorType,AuditEventDraft,AuditOutcome,PostgresSecurityAuditStore
    from daon_user_api.notification_postgres import _time
    scope=_pending_l2a_delivery()
    ctx=scope.context
    assert events.materialize_pending_studio(scope.app,context=ctx)==1
    with psycopg.connect(scope.admin) as connection:
        before=connection.execute('SELECT event_id,draft,fingerprint,state,version,delivered_at FROM notification_audit_outbox '
            'WHERE tenant_id=%s',(ctx.tenant_id,)).fetchall()
        assert len(before)==1 and before[0][3:]==('pending',1,None)
    payload=dict(before[0][1])
    payload['occurred_at']=_time(payload['occurred_at'])
    payload['actor_type']=ActorType(payload['actor_type'])
    payload['outcome']=AuditOutcome(payload['outcome'])
    mismatched=replace(AuditEventDraft(**payload),policy_version='s42-l2a-conflicting-policy')
    store=PostgresSecurityAuditStore(scope.app,min_size=0,max_size=2)
    try:
        committed=store.append(mismatched)
        for _ in range(2):
            assert events.dispatch_pending_notification_audits(scope.app,context=ctx,audit_store=store)==0
        assert store.read(committed.event_id,tenant_id=ctx.tenant_id)==committed
    finally:
        store.close()
    with psycopg.connect(scope.admin) as connection:
        assert connection.execute('SELECT event_id,draft,fingerprint,state,version,delivered_at FROM notification_audit_outbox '
            'WHERE tenant_id=%s',(ctx.tenant_id,)).fetchall()==before
        assert connection.execute('SELECT event_id,event_hash,policy_version FROM security_audit_events WHERE tenant_id=%s',
            (ctx.tenant_id,)).fetchall()==[(committed.event_id,committed.event_hash,'s42-l2a-conflicting-policy')]
        assert connection.execute('SELECT count(*) FROM notifications WHERE tenant_id=%s',(ctx.tenant_id,)).fetchone()==(1,)
        assert connection.execute('SELECT state,version FROM notification_source_outbox WHERE tenant_id=%s',
            (ctx.tenant_id,)).fetchall()==[('delivered',2)]


class _L2bNoObjectRead:
    def get(self,*_args,**_kwargs):
        raise AssertionError('L2b 저장 경계 시험은 Object/모델 외부 호출을 하지 않는다')


def _l2b_bound_pdf(scope,store,*,expected_activity_count=1):
    """기존 PG Notebook/Source 준비 방식으로 비민감 메타데이터만 만든다. 원업무 메서드는 대체하지 않는다."""
    import hashlib
    from datetime import datetime,timezone
    from daon_user_api.cloud_storage import CloudAccessContext
    from daon_user_api.notebook import NotebookContext
    from daon_user_api.notebook_postgres import PostgresNotebookRepository
    from daon_user_api.studio_workspace_postgres import PostgresStudioWorkspaceRepository as Canon
    ctx=scope.context
    source='s42l2b-source-'+scope.suffix
    version='s42l2b-version-'+scope.suffix
    object_id='s42l2b-object-'+scope.suffix
    content=b'%PDF-1.4\nS42 synthetic stored-result fixture\n%%EOF\n'
    digest=hashlib.sha256(content).hexdigest()
    with store._transaction(CloudAccessContext(ctx.tenant_id,ctx.workspace_id,ctx.actor_id,'source.create')) as connection:
        # 기존 실제 PG import fixture와 같은 completed Object 메타데이터 형식이며 Object 송수신은 없다.
        connection.execute('INSERT INTO object_records(tenant_id,workspace_id,object_id,area,staging_key,object_key,'
            'digest_sha256,byte_size,content_type,status,created_by,trace_id,idempotency_key,request_fingerprint,created_at) '
            "VALUES(%s,%s,%s,'source',%s,%s,%s,%s,'application/pdf','completed',%s,%s,%s,%s,now())",
            (ctx.tenant_id,ctx.workspace_id,object_id,'staging/'+object_id,'source/'+object_id,digest,len(content),
             ctx.actor_id,ctx.trace_id,object_id,digest))
        Canon._insert(connection,ctx,'sources',source,{'filename':'S42 synthetic.pdf'},state='registered')
        Canon._insert(connection,ctx,'source_versions',version,{'filename':'S42 synthetic.pdf','origin_kind':'native_pdf'},
            extra_columns=('source_id','object_id'),extra_values=(source,object_id))
    notebook_context=NotebookContext(ctx.tenant_id,ctx.workspace_id,ctx.actor_id,ctx.trace_id,ctx.policy_version)
    replayed=PostgresNotebookRepository(store).bind_verified(notebook_context,ctx.notebook_id,
        binding_kind='source',record_id=source,version_id=version,now=datetime.now(timezone.utc))
    # 기존 반환값은 성공 여부가 아니라 replay 여부다. 새 결속은 False이며 실제 저장행도 검사한다.
    assert replayed is False
    with store._transaction(CloudAccessContext(ctx.tenant_id,ctx.workspace_id,ctx.actor_id,'view')) as connection:
        bound=connection.execute('SELECT b.record_id,b.version_id,b.created_by FROM notebook_bindings b '
            'JOIN source_versions sv ON sv.tenant_id=b.tenant_id AND sv.workspace_id=b.workspace_id '
            'AND sv.record_id=b.version_id AND sv.source_id=b.record_id '
            'WHERE b.tenant_id=%s AND b.workspace_id=%s AND b.notebook_id=%s '
            "AND b.binding_kind='source' AND b.record_id=%s AND b.version_id=%s",
            (ctx.tenant_id,ctx.workspace_id,ctx.notebook_id,source,version)).fetchall()
        assert bound==[(source,version,ctx.actor_id)]
        activities=connection.execute('SELECT activity_kind,actor_id FROM notebook_activities '
            'WHERE tenant_id=%s AND workspace_id=%s AND notebook_id=%s',
            (ctx.tenant_id,ctx.workspace_id,ctx.notebook_id)).fetchall()
        assert activities==list(getattr(scope,'expected_notebook_activity_prefix',()))+[('context_bound',ctx.actor_id)]*expected_activity_count
    return source,version


def _l2b_assert_completed_source(connection,scope,*,origin,store_name,action,target_type,target_id):
    """실제 원감사/원장 결속과 동일 저장 Tx를 검사한다. source 미구현이면 빈 목록만 반환한다."""
    ctx=scope.context
    rows=connection.execute('SELECT s.recipient_id,s.source_store,s.action,s.target_type,s.target_id,s.kind,s.notebook_id,'
        's.trace_id,s.policy_version,s.state,s.version,s.occurred_at=a.occurred_at,s.xmin=a.xmin '
        'FROM notification_source_outbox s JOIN audit_events a ON a.tenant_id=s.tenant_id '
        'AND a.workspace_id=s.workspace_id AND a.event_id=s.origin_event_id '
        'WHERE s.tenant_id=%s AND s.workspace_id=%s AND s.origin_event_id=%s',
        (ctx.tenant_id,ctx.workspace_id,origin)).fetchall()
    if rows:
        assert rows==[(ctx.actor_id,store_name,action,target_type,target_id,'run_state',ctx.notebook_id,
            ctx.trace_id,ctx.policy_version,'pending',1,True,True)]
    return rows


def test_l2b_existing_0069_document_completed_records_source_on_original_transaction():
    """실제 start/complete의 ready·needs_review 완료와 원감사까지 저장한 뒤 source 의도를 요구한다."""
    from dataclasses import replace
    import psycopg
    from daon_user_api.cloud_storage import PostgresCloudStore
    from daon_user_api.document_processing import DocumentProcessingContext
    from daon_user_api.document_processing_postgres import PostgresDocumentProcessingRepository
    from test_document_index_postgres import ready_result
    missing=[]
    for result_status in ('ready','needs_review'):
        scope=_existing_0069_studio_case()
        ctx=scope.context
        store=PostgresCloudStore(scope.app,min_size=0,max_size=2,notification_inbox_enabled=True)
        try:
            source,version=_l2b_bound_pdf(scope,store)
            context=DocumentProcessingContext(ctx.tenant_id,ctx.workspace_id,ctx.actor_id,ctx.trace_id,ctx.policy_version)
            repository=PostgresDocumentProcessingRepository(store,_L2bNoObjectRead())
            run=repository.start(context,version,notebook_id=ctx.notebook_id)
            result=replace(ready_result(),source_id=source,source_version_id=version,status=result_status,
                conflict='UNDERSTANDING_PARSER_CONFLICT' if result_status=='needs_review' else None)
            repository.complete(context,run,result)
            with psycopg.connect(scope.admin) as connection:
                assert connection.execute('SELECT state,created_by,source_version_id,canonical_json->>\'notebook_id\' '
                    'FROM processing_runs WHERE tenant_id=%s AND workspace_id=%s AND record_id=%s',
                    (ctx.tenant_id,ctx.workspace_id,run)).fetchone()==('completed',ctx.actor_id,version,ctx.notebook_id)
                assert connection.execute('SELECT canonical_json->>\'status\' FROM understanding_results '
                    'WHERE tenant_id=%s AND workspace_id=%s AND processing_run_id=%s',
                    (ctx.tenant_id,ctx.workspace_id,run)).fetchall()==[(result_status,)]
                original=connection.execute("SELECT a.event_id,a.actor_id FROM canon_state_transitions t JOIN audit_events a "
                    'ON a.tenant_id=t.tenant_id AND a.workspace_id=t.workspace_id AND a.event_id=t.transition_id '
                    "WHERE t.tenant_id=%s AND t.workspace_id=%s AND t.entity_type='ProcessingRun' AND t.record_id=%s "
                    "AND t.target_state='completed' AND a.action='canon.transition' AND a.outcome='succeeded' "
                    "AND a.target_type='ProcessingRun' AND a.target_id=t.record_id",
                    (ctx.tenant_id,ctx.workspace_id,run)).fetchall()
                assert len(original)==1 and original[0][1]==ctx.actor_id
                assert connection.execute('SELECT 1 FROM notebook_bindings WHERE tenant_id=%s AND workspace_id=%s '
                    "AND notebook_id=%s AND binding_kind='source' AND record_id=%s AND version_id=%s",
                    (ctx.tenant_id,ctx.workspace_id,ctx.notebook_id,source,version)).fetchone()==(1,)
                if not _l2b_assert_completed_source(connection,scope,origin=original[0][0],store_name='document',
                        action='canon.transition',target_type='ProcessingRun',target_id=run):
                    missing.append(result_status)
        finally:
            store.close()
    if missing:
        pytest.fail('DOCUMENT_COMPLETED_SOURCE_INTENT_MISSING; actual_business_and_origin_saved; results='+','.join(missing),pytrace=False)


def test_l2b_existing_0069_document_failed_records_source_once_on_original_transaction():
    """실제 fail의 전이 성공 감사와 업무 실패를 구분하고, 같은 실패의 late 재호출은 불변이다."""
    import hashlib
    import json
    import psycopg
    from daon_user_api.cloud_storage import PostgresCloudStore
    from daon_user_api.document_processing import DocumentProcessingContext
    from daon_user_api.document_processing_postgres import PostgresDocumentProcessingRepository
    scope=_existing_0069_studio_case()
    ctx=scope.context
    store=PostgresCloudStore(scope.app,min_size=0,max_size=2,notification_inbox_enabled=True)
    try:
        source,version=_l2b_bound_pdf(scope,store)
        context=DocumentProcessingContext(ctx.tenant_id,ctx.workspace_id,ctx.actor_id,ctx.trace_id,ctx.policy_version)
        repository=PostgresDocumentProcessingRepository(store,_L2bNoObjectRead())
        run=repository.start(context,version,notebook_id=ctx.notebook_id)
        with psycopg.connect(scope.admin) as connection:
            previous=connection.execute('SELECT pr.state,pr.version,s.state,s.version FROM processing_runs pr '
                'JOIN source_versions sv ON sv.tenant_id=pr.tenant_id AND sv.workspace_id=pr.workspace_id AND sv.record_id=pr.source_version_id '
                'JOIN sources s ON s.tenant_id=sv.tenant_id AND s.workspace_id=sv.workspace_id AND s.record_id=sv.source_id '
                'WHERE pr.tenant_id=%s AND pr.workspace_id=%s AND pr.record_id=%s',
                (ctx.tenant_id,ctx.workspace_id,run)).fetchone()
            assert previous is not None and previous[0]=='vision_llm_understanding' and previous[2]=='processing'
            assert connection.execute('SELECT count(*) FROM notification_source_outbox WHERE tenant_id=%s',(ctx.tenant_id,)).fetchone()==(0,)
        failure_code='S42_SYNTHETIC_DOCUMENT_FAILURE'
        expected_origin='tr-'+hashlib.sha256('|'.join(('ProcessingRun',run,'failed',str(previous[1]))).encode()).hexdigest()[:32]
        repository.fail(context,run,failure_code,retryable=False)
        with psycopg.connect(scope.admin) as connection:
            failed=connection.execute('SELECT state,version,created_by,source_version_id,canonical_json->>\'notebook_id\' '
                'FROM processing_runs WHERE tenant_id=%s AND workspace_id=%s AND record_id=%s',
                (ctx.tenant_id,ctx.workspace_id,run)).fetchone()
            assert failed==('failed',previous[1]+1,ctx.actor_id,version,ctx.notebook_id)
            assert connection.execute('SELECT state,version FROM sources WHERE tenant_id=%s AND workspace_id=%s AND record_id=%s',
                (ctx.tenant_id,ctx.workspace_id,source)).fetchone()==('failed',previous[3]+1)
            originals=connection.execute('SELECT a.event_id,a.actor_id,a.action,a.target_type,a.target_id,a.outcome,'
                "a.after_value->>'state',t.target_state,t.transition_version,a.occurred_at FROM canon_state_transitions t "
                'JOIN audit_events a ON a.tenant_id=t.tenant_id AND a.workspace_id=t.workspace_id AND a.event_id=t.transition_id '
                "WHERE t.tenant_id=%s AND t.workspace_id=%s AND t.entity_type='ProcessingRun' AND t.record_id=%s "
                "AND t.target_state='failed'",(ctx.tenant_id,ctx.workspace_id,run)).fetchall()
            assert len(originals)==1 and originals[0][:9]==(expected_origin,ctx.actor_id,'canon.transition',
                'ProcessingRun',run,'succeeded','failed','failed',previous[1]+1)
            assert connection.execute('SELECT 1 FROM notebook_bindings WHERE tenant_id=%s AND workspace_id=%s '
                "AND notebook_id=%s AND binding_kind='source' AND record_id=%s AND version_id=%s",
                (ctx.tenant_id,ctx.workspace_id,ctx.notebook_id,source,version)).fetchone()==(1,)
        after_first_failure=_l2b_completion_snapshot(scope)
        # terminal failed 상태는 추가 전이/감사/source/알림을 만들거나 기존 상태를 바꾸지 않는다.
        repository.fail(context,run,failure_code,retryable=False)
        assert _l2b_completion_snapshot(scope)==after_first_failure
        with psycopg.connect(scope.admin) as connection:
            sources=connection.execute('SELECT s.source_event_id,s.recipient_id,s.source_store,s.origin_event_id,s.action,'
                's.target_type,s.target_id,s.kind,s.severity,s.notebook_id,s.trace_id,s.policy_version,s.state,s.version,s.delivered_at,'
                's.occurred_at=a.occurred_at,s.xmin=a.xmin FROM notification_source_outbox s JOIN audit_events a '
                'ON a.tenant_id=s.tenant_id AND a.workspace_id=s.workspace_id AND a.event_id=s.origin_event_id '
                'WHERE s.tenant_id=%s AND s.workspace_id=%s',(ctx.tenant_id,ctx.workspace_id)).fetchall()
            for table in ('notifications','notification_details','notification_audit_outbox'):
                assert connection.execute(f'SELECT count(*) FROM {table} WHERE tenant_id=%s',(ctx.tenant_id,)).fetchone()==(0,)
        if not sources:
            pytest.fail('DOCUMENT_FAILED_SOURCE_INTENT_MISSING; actual_failed_run_and_successful_transition_audit_saved; late_fail_unchanged',pytrace=False)
        event='document-event-'+hashlib.sha256((expected_origin+'|'+ctx.workspace_id).encode()).hexdigest()[:32]
        assert sources==[(event,ctx.actor_id,'document',expected_origin,'canon.transition','ProcessingRun',run,
            'run_failed','warning',ctx.notebook_id,ctx.trace_id,ctx.policy_version,'pending',1,None,True,True)]
        assert failure_code not in json.dumps(sources,ensure_ascii=False,default=str)
    finally:
        store.close()


def _sg_assert_terminal_job_materialized(terminal_state):
    """실제 실패 Job 사건만 소비한다. Provider/worker 실행·공개 Job 화면 성공 증거는 아니다."""
    import hashlib
    import psycopg
    import daon_user_api.notification_events as events
    from daon_user_api.cloud_storage import PostgresCloudStore,CloudAccessContext
    from daon_user_api.notification_postgres import PostgresNotificationRepository
    scope=_existing_0069_studio_case()
    ctx=scope.context
    store=PostgresCloudStore(scope.app,min_size=0,max_size=2,notification_inbox_enabled=True)
    try:
        queue,job=_l2b_start_owned_studio_job(scope,store)
        queue.finish(job,state=terminal_state,error_code='S42_SYNTHETIC_STUDIO_FAILURE')
        origin='audit-studio-job-finish-'+hashlib.sha256(
            f'{ctx.tenant_id}|{ctx.workspace_id}|{job.job_id}|{job.version+1}|{terminal_state}'.encode()).hexdigest()[:32]
        source_id='studio-event-'+hashlib.sha256(f'{origin}|{ctx.workspace_id}'.encode()).hexdigest()[:32]
        with psycopg.connect(scope.admin) as connection:
            job_before=connection.execute('SELECT to_jsonb(j) FROM studio_generation_jobs j WHERE tenant_id=%s '
                'AND workspace_id=%s AND job_id=%s',(ctx.tenant_id,ctx.workspace_id,job.job_id)).fetchone()
            assert job_before is not None
            assert (job_before[0]['state'],job_before[0]['version'],job_before[0]['actor_id'])==(terminal_state,job.version+1,ctx.actor_id)
            sources=connection.execute('SELECT s.source_event_id,s.origin_event_id,s.source_store,s.recipient_id,'
                's.action,s.target_type,s.target_id,s.kind,s.severity,s.notebook_id,s.trace_id,s.policy_version,'
                's.state,s.version,s.delivered_at,s.occurred_at,a.occurred_at,'
                'a.xmin=j.xmin AND j.xmin=s.xmin,a.after_value '
                'FROM notification_source_outbox s JOIN audit_events a ON a.tenant_id=s.tenant_id '
                'AND a.workspace_id=s.workspace_id AND a.event_id=s.origin_event_id '
                'JOIN studio_generation_jobs j ON j.tenant_id=s.tenant_id AND j.workspace_id=s.workspace_id AND j.job_id=s.target_id '
                "WHERE s.tenant_id=%s AND s.workspace_id=%s AND a.actor_id=%s AND a.outcome='succeeded'",
                (ctx.tenant_id,ctx.workspace_id,ctx.actor_id)).fetchall()
            assert len(sources)==1
            source=sources[0]
            assert source[:15]==(source_id,origin,'studio',ctx.actor_id,'studio.generation.finish','StudioGenerationJob',
                job.job_id,'run_failed','warning',ctx.notebook_id,ctx.trace_id,ctx.policy_version,'pending',1,None)
            assert source[15]==source[16] and source[17] is True
            assert source[18]=={'state':terminal_state,'version':job.version+1}
            original_audit=connection.execute('SELECT to_jsonb(a) FROM audit_events a WHERE tenant_id=%s '
                'AND workspace_id=%s AND event_id=%s',(ctx.tenant_id,ctx.workspace_id,origin)).fetchone()
            for table in ('notifications','notification_details','notification_audit_outbox','security_audit_events'):
                assert connection.execute(f'SELECT count(*) FROM {table} WHERE tenant_id=%s',(ctx.tenant_id,)).fetchone()==(0,)
    finally:
        try:
            _l2b_cleanup_owned_active_studio_job(scope)
        finally:
            store.close()
    # 실제 producer의 확정 Job/원감사/pending source를 준비한 다음에만 소비 누락을 판정한다.
    assert events.materialize_pending_completed(scope.app,context=ctx,limit=100)==1,'STUDIO_FAILED_JOB_SOURCE_NOT_MATERIALIZED'
    link=f'/notebooks/{ctx.notebook_id}?studio_job_id={job.job_id}'
    title='Studio 생성 사용 불가' if terminal_state=='unavailable' else 'Studio 생성 실패'
    summary=('Studio 생성 Job을 사용할 수 없습니다. 현재 처리 상태를 확인하세요.' if terminal_state=='unavailable'
        else 'Studio 생성 Job이 실패했습니다. 현재 처리 상태를 확인하세요.')
    with psycopg.connect(scope.admin) as connection:
        rows=connection.execute('SELECT n.notification_id,n.recipient_id,n.source_event_id,n.version,n.read_at,'
            'd.source_store,d.origin_event_id,d.resource_type,d.resource_id,d.notebook_id,d.deep_link,d.title,d.summary,'
            'd.kind,d.severity,d.trace_id,d.policy_version,d.delivery_state '
            'FROM notifications n JOIN notification_details d ON d.tenant_id=n.tenant_id AND d.notification_id=n.notification_id '
            'WHERE n.tenant_id=%s AND n.workspace_id=%s',(ctx.tenant_id,ctx.workspace_id)).fetchall()
        assert len(rows)==1
        item=rows[0]
        assert item[1:]==(ctx.actor_id,source_id,1,None,'studio',origin,'StudioGenerationJob',job.job_id,ctx.notebook_id,
            link,title,summary,'run_failed','warning',ctx.trace_id,ctx.policy_version,'delivered')
        audits=connection.execute('SELECT event_id,action,state,version,draft FROM notification_audit_outbox '
            'WHERE tenant_id=%s AND workspace_id=%s',(ctx.tenant_id,ctx.workspace_id)).fetchall()
        assert len(audits)==1 and audits[0][1:4]==('notification.created','pending',1)
        draft=audits[0][4]
        assert (draft['tenant_id'],draft['workspace_id'],draft['target_type'],draft['target_id'],draft['trace_id'],
            draft['policy_version'],draft['metadata'])==(ctx.tenant_id,ctx.workspace_id,'notification',item[0],
                ctx.trace_id,ctx.policy_version,{'source_event_id':source_id})
        lineage=connection.execute('SELECT s.state,s.version,n.created_at,s.delivered_at,'
            "(a.draft->>'occurred_at')::timestamptz,s.occurred_at,o.occurred_at,"
            'n.xmin=d.xmin AND d.xmin=s.xmin AND s.xmin=a.xmin '
            'FROM notifications n JOIN notification_details d ON d.tenant_id=n.tenant_id AND d.notification_id=n.notification_id '
            'JOIN notification_source_outbox s ON s.tenant_id=n.tenant_id AND s.workspace_id=n.workspace_id '
            'AND s.source_event_id=n.source_event_id AND s.recipient_id=n.recipient_id '
            'JOIN notification_audit_outbox a ON a.tenant_id=n.tenant_id AND a.notification_id=n.notification_id '
            'JOIN audit_events o ON o.tenant_id=s.tenant_id AND o.workspace_id=s.workspace_id AND o.event_id=s.origin_event_id '
            'WHERE n.tenant_id=%s AND n.notification_id=%s',(ctx.tenant_id,item[0])).fetchall()
        assert len(lineage)==1
        state,version,created,delivered,audit_time,source_time,origin_time,same_tx=lineage[0]
        assert (state,version,same_tx)==('delivered',2,True)
        assert created>=delivered and created>source_time and audit_time==created
        assert source_time==origin_time==source[15]
        assert job.request_json['purpose'] not in str((item,draft))
        assert 'S42_SYNTHETIC_STUDIO_FAILURE' not in str((item,draft))
        assert connection.execute('SELECT count(*) FROM security_audit_events WHERE tenant_id=%s',(ctx.tenant_id,)).fetchone()==(0,)
        assert connection.execute('SELECT to_jsonb(j) FROM studio_generation_jobs j WHERE tenant_id=%s '
            'AND workspace_id=%s AND job_id=%s',(ctx.tenant_id,ctx.workspace_id,job.job_id)).fetchone()==job_before
        assert connection.execute('SELECT to_jsonb(a) FROM audit_events a WHERE tenant_id=%s '
            'AND workspace_id=%s AND event_id=%s',(ctx.tenant_id,ctx.workspace_id,origin)).fetchone()==original_audit
    before_reads=_l2b_completion_snapshot(scope)
    repository=PostgresNotificationRepository(scope.app,
        context=CloudAccessContext(ctx.tenant_id,ctx.workspace_id,ctx.actor_id,'view'))
    reopened=repository.notifications_for(tenant_id=ctx.tenant_id,recipient_id=ctx.actor_id)
    assert len(reopened)==1
    notification=reopened[0]
    assert (notification.notification_id,notification.recipient_id,notification.source_event_id,notification.resource_type,
        notification.resource_id,notification.deep_link,notification.title,notification.summary,notification.kind,
        notification.severity,notification.audit_event_id,notification.created_at,notification.version,notification.read_at)==(
            item[0],ctx.actor_id,source_id,'StudioGenerationJob',job.job_id,link,title,summary,'run_failed','warning',audits[0][0],created,1,None)
    assert repository.notification_for(tenant_id=ctx.tenant_id,recipient_id=ctx.actor_id,notification_id=item[0])==notification
    assert events.materialize_pending_completed(scope.app,context=ctx,limit=100)==0
    assert _l2b_completion_snapshot(scope)==before_reads


def test_sg_existing_0069_completed_job_materializes_actual_generation_and_reopens_without_duplicate():
    """실제 Source-only 저장 메서드의 산출물만 쓴다. Provider/worker/PDF 열기·Object 송수신 증거는 아니다."""
    import hashlib
    import psycopg
    import daon_user_api.notification_events as events
    from daon_user_api.cloud_storage import PostgresCloudStore,CloudAccessContext
    from daon_user_api.data_canon import canonical_json_bytes
    from daon_user_api.egress_policy import EgressPolicyPayload
    from daon_user_api.notification_postgres import PostgresNotificationRepository
    from daon_user_api.studio_workspace import StudioGenerationRequest
    from daon_user_api.studio_workspace_postgres import PostgresStudioWorkspaceRepository
    scope=_existing_0069_studio_case()
    ctx=scope.context
    prep_store=PostgresCloudStore(scope.app,min_size=0,max_size=2,notification_inbox_enabled=False)
    try:
        processing_run,complete=_l2b_prepared_completion(scope,prep_store,'document',document_ready=True)
        complete()
    finally:
        prep_store.close()
    with psycopg.connect(scope.admin) as connection:
        # 기존 실제 정책 fixture를 새 QA scope에만 적용한다. 외부 허용/검사 대역은 사용하지 않는다.
        for policy_scope in ('organization','workspace'):
            _studio_case_egress(connection,scope,policy_scope,EgressPolicyPayload.deny_external().as_dict(),1)
        prepared=connection.execute('SELECT sv.source_id,sv.record_id,s.state,r.state,u.canonical_json->>\'status\' '
            'FROM processing_runs r JOIN source_versions sv ON sv.tenant_id=r.tenant_id AND sv.workspace_id=r.workspace_id '
            'AND sv.record_id=r.source_version_id JOIN sources s ON s.tenant_id=sv.tenant_id AND s.workspace_id=sv.workspace_id '
            'AND s.record_id=sv.source_id JOIN understanding_results u ON u.tenant_id=r.tenant_id AND u.workspace_id=r.workspace_id '
            'AND u.processing_run_id=r.record_id AND u.source_version_id=sv.record_id '
            'WHERE r.tenant_id=%s AND r.workspace_id=%s AND r.record_id=%s',
            (ctx.tenant_id,ctx.workspace_id,processing_run)).fetchone()
        assert prepared is not None and prepared[2:]==('ready','completed','ready')
        source,source_version=prepared[:2]
        assert connection.execute('SELECT count(*) FROM evidence_spans WHERE tenant_id=%s AND workspace_id=%s AND source_version_id=%s',
            (ctx.tenant_id,ctx.workspace_id,source_version)).fetchone()[0]>0
        assert connection.execute('SELECT count(*) FROM index_versions WHERE tenant_id=%s AND workspace_id=%s AND source_version_id=%s',
            (ctx.tenant_id,ctx.workspace_id,source_version)).fetchone()==(1,)
        assert connection.execute('SELECT count(*) FROM notification_source_outbox WHERE tenant_id=%s',(ctx.tenant_id,)).fetchone()==(0,)
        assert connection.execute("SELECT count(*) FROM studio_generation_jobs WHERE state IN ('queued','leased','generating')").fetchone()==(0,)
    store=PostgresCloudStore(scope.app,min_size=0,max_size=2,notification_inbox_enabled=True)
    try:
        repository=PostgresStudioWorkspaceRepository(store)
        queue=repository._generation_queue
        assert queue is not None
        scope.studio_queue=queue
        with store._transaction(CloudAccessContext(ctx.tenant_id,ctx.workspace_id,ctx.actor_id,'view')) as connection:
            projection=repository._policy_projection(connection,ctx)
            assert len(projection['locks'])==6
            assert projection['authoritative_values']['egress_policy']['effective_policy']['mode']=='deny_external'
        request=StudioGenerationRequest('evidence_report',source,(source_version,),None,None,
            'S42 합성 Source-only 완료 저장 시험','reviewer',None,'short','summary','pdf','review_required',source_only=True)
        key='s42-job-completed-'+scope.suffix
        queued,replayed=repository.enqueue_generation(ctx,request,key)
        assert replayed is False and queued['status']=='queued'
        job=queue.claim('s42-completed-worker-'+scope.suffix,lease_seconds=600)
        if job is None or (job.tenant_id,job.workspace_id,job.actor_id,job.job_id)!=(
                ctx.tenant_id,ctx.workspace_id,ctx.actor_id,queued['job_id']):
            pytest.fail('STUDIO_JOB_CLAIM_NOT_OWN_TARGET; 생성/finish 미실행',pytrace=False)
        scope.own_studio_job=job
        assert job.idempotency_key==key
        assert (job.request_json['notebook_id'],job.request_json['source_id'],job.request_json['source_version_ids'])==(
            ctx.notebook_id,source,[source_version])
        queue.start(job)
        generated,generation_replayed=repository.create_generation(ctx,request,job.idempotency_key)
        assert generation_replayed is False
        generation_hash=hashlib.sha256('|'.join((ctx.tenant_id,ctx.workspace_id,ctx.actor_id,'studio.generation.create',key)).encode()).hexdigest()[:32]
        output_id,version_id=generated['studio_output_id'],generated['output_version_id']
        assert (output_id,version_id)==('output-'+generation_hash,'output-version-'+generation_hash)
        assert generated['status']=='draft' and isinstance(generated['content'],dict)
        with psycopg.connect(scope.admin) as connection:
            generation=connection.execute('SELECT so.record_id,g.record_id,g.state,g.canonical_json->>\'notebook_id\','
                'g.canonical_json->\'source_version_ids\',ov.state,ov.canonical_json->\'content\' '
                'FROM studio_outputs so JOIN generation_requests g ON g.tenant_id=so.tenant_id AND g.workspace_id=so.workspace_id '
                'AND g.record_id=so.generation_request_id JOIN output_versions ov ON ov.tenant_id=so.tenant_id '
                'AND ov.workspace_id=so.workspace_id AND ov.studio_output_id=so.record_id '
                'JOIN notebook_bindings b ON b.tenant_id=ov.tenant_id AND b.workspace_id=ov.workspace_id '
                "AND b.binding_kind='output_version' AND b.record_id=ov.record_id AND b.notebook_id=%s "
                'WHERE so.tenant_id=%s AND so.workspace_id=%s AND so.record_id=%s AND ov.record_id=%s',
                (ctx.notebook_id,ctx.tenant_id,ctx.workspace_id,output_id,version_id)).fetchall()
            assert len(generation)==1,'SG_GENERATION_ROW_COUNT_MISMATCH'
            stored=generation[0]
            assert stored[0]==output_id,'SG_GENERATION_OUTPUT_ID_MISMATCH'
            assert stored[1]=='generation-'+generation_hash,'SG_GENERATION_REQUEST_ID_MISMATCH'
            assert stored[2]=='submitted','SG_GENERATION_REQUEST_STATE_MISMATCH'
            assert stored[3]==ctx.notebook_id,'SG_GENERATION_NOTEBOOK_MISMATCH'
            assert stored[4]==[source_version],'SG_GENERATION_SOURCE_VERSIONS_MISMATCH'
            assert stored[5]=='draft','SG_GENERATION_OUTPUT_STATE_MISMATCH'
            # asdict 반환의 tuple은 실제 Canon JSON 저장 시 배열이 된다. 내용 전체의 정식 bytes를 대조한다.
            assert isinstance(stored[6],dict),'SG_GENERATION_CONTENT_SHAPE_MISMATCH'
            assert canonical_json_bytes(stored[6])==canonical_json_bytes(generated['content']),'SG_GENERATION_CONTENT_CANONICAL_MISMATCH'
            evidence=connection.execute('SELECT er.source_version_id,es.source_version_id FROM evidence_references er '
                'JOIN evidence_spans es ON es.tenant_id=er.tenant_id AND es.workspace_id=er.workspace_id AND es.record_id=er.evidence_span_id '
                'WHERE er.tenant_id=%s AND er.workspace_id=%s AND er.output_version_id=%s',
                (ctx.tenant_id,ctx.workspace_id,version_id)).fetchall()
            assert evidence and all(row==(source_version,source_version) for row in evidence)
        queue.finish(job,state='completed',studio_output_id=output_id,output_version_id=version_id)
        origin='audit-studio-job-finish-'+hashlib.sha256(
            f'{ctx.tenant_id}|{ctx.workspace_id}|{job.job_id}|{job.version+1}|completed'.encode()).hexdigest()[:32]
        source_id='studio-event-'+hashlib.sha256(f'{origin}|{ctx.workspace_id}'.encode()).hexdigest()[:32]
        with psycopg.connect(scope.admin) as connection:
            sources=connection.execute('SELECT s.source_event_id,s.origin_event_id,s.source_store,s.recipient_id,s.action,s.target_type,'
                's.target_id,s.kind,s.severity,s.notebook_id,s.state,s.version,s.delivered_at,s.occurred_at,a.occurred_at,'
                'a.after_value,j.state,j.version,j.studio_output_id,j.output_version_id,s.xmin=a.xmin AND a.xmin=j.xmin '
                'FROM notification_source_outbox s JOIN audit_events a ON a.tenant_id=s.tenant_id AND a.workspace_id=s.workspace_id '
                'AND a.event_id=s.origin_event_id JOIN studio_generation_jobs j ON j.tenant_id=s.tenant_id '
                'AND j.workspace_id=s.workspace_id AND j.job_id=s.target_id '
                'WHERE s.tenant_id=%s AND s.workspace_id=%s',(ctx.tenant_id,ctx.workspace_id)).fetchall()
            assert len(sources)==1
            event=sources[0]
            assert event[:13]==(source_id,origin,'studio',ctx.actor_id,'studio.generation.finish','StudioGenerationJob',
                job.job_id,'run_state','info',ctx.notebook_id,'pending',1,None)
            assert event[13]==event[14]
            assert event[15:]==({'state':'completed','version':job.version+1},'completed',job.version+1,output_id,version_id,True)
            for table in ('notifications','notification_details','notification_audit_outbox','security_audit_events'):
                assert connection.execute(f'SELECT count(*) FROM {table} WHERE tenant_id=%s',(ctx.tenant_id,)).fetchone()==(0,)
            original_job=connection.execute('SELECT to_jsonb(j) FROM studio_generation_jobs j WHERE tenant_id=%s AND workspace_id=%s AND job_id=%s',
                (ctx.tenant_id,ctx.workspace_id,job.job_id)).fetchone()
            original_audit=connection.execute('SELECT to_jsonb(a) FROM audit_events a WHERE tenant_id=%s AND workspace_id=%s AND event_id=%s',
                (ctx.tenant_id,ctx.workspace_id,origin)).fetchone()
    finally:
        try:
            _l2b_cleanup_owned_active_studio_job(scope)
        finally:
            store.close()
    assert events.materialize_pending_completed(scope.app,context=ctx,limit=100)==1,'STUDIO_COMPLETED_JOB_SOURCE_NOT_MATERIALIZED'
    link=f'/notebooks/{ctx.notebook_id}?output_version_id={version_id}'
    title,summary='Studio 생성 완료','Studio 생성 Job이 완료되었습니다.'
    with psycopg.connect(scope.admin) as connection:
        rows=connection.execute('SELECT n.notification_id,n.recipient_id,n.source_event_id,d.source_store,d.origin_event_id,d.resource_type,'
            'd.resource_id,d.notebook_id,d.deep_link,d.title,d.summary,d.kind,d.severity,d.trace_id,d.policy_version,d.delivery_state '
            'FROM notifications n JOIN notification_details d ON d.tenant_id=n.tenant_id AND d.notification_id=n.notification_id '
            'WHERE n.tenant_id=%s AND n.workspace_id=%s',(ctx.tenant_id,ctx.workspace_id)).fetchall()
        assert len(rows)==1
        item=rows[0]
        assert item[1:]==(ctx.actor_id,source_id,'studio',origin,'StudioGenerationJob',job.job_id,ctx.notebook_id,link,
            title,summary,'run_state','info',ctx.trace_id,ctx.policy_version,'delivered')
        audits=connection.execute('SELECT event_id,action,state,draft FROM notification_audit_outbox '
            'WHERE tenant_id=%s AND workspace_id=%s',(ctx.tenant_id,ctx.workspace_id)).fetchall()
        assert len(audits)==1 and audits[0][1:3]==('notification.created','pending')
        assert audits[0][3]['metadata']=={'source_event_id':source_id}
        lineage=connection.execute('SELECT s.state,s.version,n.created_at,s.delivered_at,(a.draft->>\'occurred_at\')::timestamptz,'
            's.occurred_at,o.occurred_at,n.xmin=d.xmin AND d.xmin=s.xmin AND s.xmin=a.xmin '
            'FROM notifications n JOIN notification_details d ON d.tenant_id=n.tenant_id AND d.notification_id=n.notification_id '
            'JOIN notification_source_outbox s ON s.tenant_id=n.tenant_id AND s.workspace_id=n.workspace_id '
            'AND s.source_event_id=n.source_event_id AND s.recipient_id=n.recipient_id '
            'JOIN notification_audit_outbox a ON a.tenant_id=n.tenant_id AND a.notification_id=n.notification_id '
            'JOIN audit_events o ON o.tenant_id=s.tenant_id AND o.workspace_id=s.workspace_id AND o.event_id=s.origin_event_id '
            'WHERE n.tenant_id=%s AND n.notification_id=%s',(ctx.tenant_id,item[0])).fetchone()
        state,version,created,delivered,audit_time,source_time,origin_time,same_tx=lineage
        assert (state,version,same_tx)==('delivered',2,True)
        assert created>=delivered and created>source_time and audit_time==created
        assert source_time==origin_time==event[13]
        assert connection.execute('SELECT to_jsonb(j) FROM studio_generation_jobs j WHERE tenant_id=%s AND workspace_id=%s AND job_id=%s',
            (ctx.tenant_id,ctx.workspace_id,job.job_id)).fetchone()==original_job
        assert connection.execute('SELECT to_jsonb(a) FROM audit_events a WHERE tenant_id=%s AND workspace_id=%s AND event_id=%s',
            (ctx.tenant_id,ctx.workspace_id,origin)).fetchone()==original_audit
    before_reads=_l2b_completion_snapshot(scope)
    reopened=PostgresNotificationRepository(scope.app,context=CloudAccessContext(ctx.tenant_id,ctx.workspace_id,ctx.actor_id,'view'))
    notifications=reopened.notifications_for(tenant_id=ctx.tenant_id,recipient_id=ctx.actor_id)
    assert len(notifications)==1
    notification=notifications[0]
    assert (notification.notification_id,notification.resource_type,notification.resource_id,notification.deep_link,
        notification.title,notification.summary,notification.audit_event_id,notification.version,notification.read_at)==(
            item[0],'StudioGenerationJob',job.job_id,link,title,summary,audits[0][0],1,None)
    assert reopened.notification_for(tenant_id=ctx.tenant_id,recipient_id=ctx.actor_id,notification_id=item[0])==notification
    assert events.materialize_pending_completed(scope.app,context=ctx,limit=100)==0
    assert _l2b_completion_snapshot(scope)==before_reads


def test_sg_existing_0069_failed_job_materializes_and_reopens_without_duplicate():
    _sg_assert_terminal_job_materialized('failed')


def test_sg_existing_0069_unavailable_job_materializes_and_reopens_without_duplicate():
    _sg_assert_terminal_job_materialized('unavailable')


def _sg_pending_failed_job():
    """실제 owner Job/원감사/source만 준비한다. 원업무 저장·queue 호출을 대역으로 바꾸지 않는다."""
    import hashlib
    import psycopg
    from daon_user_api.cloud_storage import PostgresCloudStore
    scope=_existing_0069_studio_case()
    ctx=scope.context
    store=PostgresCloudStore(scope.app,min_size=0,max_size=2,notification_inbox_enabled=True)
    try:
        queue,job=_l2b_start_owned_studio_job(scope,store)
        queue.finish(job,state='failed',error_code='S42_SYNTHETIC_STUDIO_FAILURE')
        origin='audit-studio-job-finish-'+hashlib.sha256(
            f'{ctx.tenant_id}|{ctx.workspace_id}|{job.job_id}|{job.version+1}|failed'.encode()).hexdigest()[:32]
        source_id='studio-event-'+hashlib.sha256(f'{origin}|{ctx.workspace_id}'.encode()).hexdigest()[:32]
        with psycopg.connect(scope.admin) as connection:
            sources=connection.execute('SELECT to_jsonb(s),a.after_value,s.xmin=a.xmin AND a.xmin=j.xmin '
                'FROM notification_source_outbox s JOIN audit_events a ON a.tenant_id=s.tenant_id '
                'AND a.workspace_id=s.workspace_id AND a.event_id=s.origin_event_id '
                'JOIN studio_generation_jobs j ON j.tenant_id=s.tenant_id AND j.workspace_id=s.workspace_id AND j.job_id=s.target_id '
                'WHERE s.tenant_id=%s AND s.workspace_id=%s',(ctx.tenant_id,ctx.workspace_id)).fetchall()
            assert len(sources)==1
            source,after,same_tx=sources[0]
            assert (source['source_event_id'],source['origin_event_id'],source['source_store'],source['action'],
                source['target_type'],source['target_id'],source['recipient_id'],source['state'],source['version'],source['delivered_at'])==(
                    source_id,origin,'studio','studio.generation.finish','StudioGenerationJob',job.job_id,ctx.actor_id,'pending',1,None)
            assert after=={'state':'failed','version':job.version+1} and same_tx is True
        return SimpleNamespace(scope=scope,job=job,source=source)
    finally:
        try:
            _l2b_cleanup_owned_active_studio_job(scope)
        finally:
            store.close()


def test_sg_existing_0069_current_source_deny_suppresses_and_sql_true_cannot_bypass():
    """현재 Source ACL 철회는 Python/직접 SQL 모두 본문 발행0이며 source 상태만 suppressed로 바꾼다."""
    import psycopg
    import daon_user_api.notification_events as events
    prepared=_sg_pending_failed_job()
    scope=prepared.scope
    ctx=scope.context
    source=prepared.source
    with psycopg.connect(scope.admin) as connection:
        connection.execute('INSERT INTO identity_auth_source_access '
            '(tenant_id,workspace_id,user_id,source_version_id,allowed,version,updated_at) '
            'VALUES(%s,%s,%s,%s,0,1,now())',
            (ctx.tenant_id,ctx.workspace_id,ctx.actor_id,prepared.job.request_json['source_version_ids'][0]))
    before=_l2b_completion_snapshot(scope)
    with psycopg.connect(scope.app) as connection:
        with connection.transaction(force_rollback=True):
            for name,value in (('tenant_id',ctx.tenant_id),('workspace_id',ctx.workspace_id),
                    ('actor_id',ctx.actor_id),('notebook_id',ctx.notebook_id),('capability','view')):
                connection.execute('SELECT set_config(%s,%s,true)',('app.'+name,value))
            assert connection.execute('SELECT current_user').fetchone()==('daon_app',)
            assert events._completed_event_access(connection,ctx,origin_event_id=source['origin_event_id'],
                notebook_id=ctx.notebook_id,origin_actor_id=ctx.actor_id) is None
            assert connection.execute('SELECT public.materialize_completed_notification(%s,true)',
                (source['source_event_id'],)).fetchone()==({'state':'suppressed'},)
    assert _l2b_completion_snapshot(scope)==before
    assert events.materialize_pending_completed(scope.app,context=ctx,limit=100)==0
    after=_l2b_completion_snapshot(scope)
    assert {key:value for key,value in after.items() if key!='notification_source_outbox'}=={
        key:value for key,value in before.items() if key!='notification_source_outbox'}
    with psycopg.connect(scope.admin) as connection:
        source_after=connection.execute('SELECT to_jsonb(s) FROM notification_source_outbox s WHERE tenant_id=%s '
            'AND workspace_id=%s AND source_event_id=%s',(ctx.tenant_id,ctx.workspace_id,source['source_event_id'])).fetchone()[0]
        assert (source_after['state'],source_after['version'])==('suppressed',2) and source_after['delivered_at'] is not None
        assert {key:value for key,value in source_after.items() if key not in {'state','version','delivered_at'}}=={
            key:value for key,value in source.items() if key not in {'state','version','delivered_at'}}
        for table in ('notifications','notification_details','notification_audit_outbox','security_audit_events'):
            assert connection.execute(f'SELECT count(*) FROM {table} WHERE tenant_id=%s',(ctx.tenant_id,)).fetchone()==(0,)
    assert events.materialize_pending_completed(scope.app,context=ctx,limit=100)==0
    assert _l2b_completion_snapshot(scope)==after


def test_sg_existing_0069_materialize_sql_failure_after_detail_keeps_pending():
    """실제 materialize 후 detail/감사의도/ack 확인 직후 SQL22012를 내어 전체 소비 rollback을 검증한다."""
    from unittest.mock import patch
    import psycopg
    import daon_user_api.notification_events as events
    from daon_user_api.notification import NotificationError
    prepared=_sg_pending_failed_job()
    scope=prepared.scope
    ctx=scope.context
    before=_l2b_completion_snapshot(scope)
    real_connect=psycopg.connect
    phases=[]
    faults=[]
    @contextmanager
    def observed_connect(*args,**kwargs):
        with real_connect(*args,**kwargs) as actual:
            class ObservedConnection:
                def __getattr__(self,name):
                    return getattr(actual,name)
                def execute(self,query,params=None):
                    cursor=actual.execute(query,params)
                    if query=='SELECT public.materialize_completed_notification(%s,%s)':
                        assert params==(prepared.source['source_event_id'],True)
                        parents=actual.execute('SELECT n.notification_id,d.resource_type,d.resource_id,d.kind,d.severity,n.xmin=d.xmin '
                            'FROM notifications n JOIN notification_details d ON d.tenant_id=n.tenant_id AND d.notification_id=n.notification_id '
                            'WHERE n.tenant_id=%s AND n.workspace_id=%s AND n.recipient_id=%s',
                            (ctx.tenant_id,ctx.workspace_id,ctx.actor_id)).fetchall()
                        assert len(parents)==1 and parents[0][1:]==('StudioGenerationJob',prepared.job.job_id,'run_failed','warning',True)
                        assert actual.execute("SELECT draft->>'action',draft->>'target_id' FROM public.pending_notification_audits(100)").fetchall()==[
                            ('notification.created',parents[0][0])]
                        assert actual.execute('SELECT source_event_id FROM public.pending_completed_notification_sources(100)').fetchall()==[]
                        phases.append('ACTUAL_SG_DETAIL_CREATED_INTENT_AND_ACK')
                        try:
                            actual.execute('SELECT 1/0')
                        except psycopg.Error as error:
                            faults.append(error.sqlstate)
                            raise
                    return cursor
            yield ObservedConnection()
    with patch('psycopg.connect',new=observed_connect):
        with pytest.raises(NotificationError) as caught:
            events.materialize_pending_completed(scope.app,context=ctx,limit=100)
    assert (caught.value.code,caught.value.http_status)==('DATABASE_UNAVAILABLE',503)
    assert phases==['ACTUAL_SG_DETAIL_CREATED_INTENT_AND_ACK'] and faults==['22012']
    assert _l2b_completion_snapshot(scope)==before
    with psycopg.connect(scope.admin) as connection:
        assert connection.execute('SELECT state,version,delivered_at FROM notification_source_outbox WHERE tenant_id=%s '
            'AND workspace_id=%s AND source_event_id=%s',
            (ctx.tenant_id,ctx.workspace_id,prepared.source['source_event_id'])).fetchone()==('pending',1,None)
        for table in ('notifications','notification_details','notification_audit_outbox','security_audit_events'):
            assert connection.execute(f'SELECT count(*) FROM {table} WHERE tenant_id=%s',(ctx.tenant_id,)).fetchone()==(0,)


def test_l2b_existing_0069_studio_job_failed_records_origin_and_source_once():
    """실제 owner enqueue/claim/start/finish의 실패 원감사·source 동일 Tx와 lease lost 불변을 요구한다."""
    import hashlib
    import psycopg
    from daon_user_api.cloud_storage import PostgresCloudStore
    from daon_user_api.studio_workspace import StudioGenerationRequest
    from daon_user_api.studio_workspace_postgres import PostgresStudioWorkspaceRepository
    from daon_user_api.studio_generation_queue import StudioGenerationQueueError
    scope=_existing_0069_studio_case()
    ctx=scope.context
    store=PostgresCloudStore(scope.app,min_size=0,max_size=2,notification_inbox_enabled=True)
    repository=PostgresStudioWorkspaceRepository(store)
    queue=repository._generation_queue
    assert queue is not None
    try:
        source,source_version=_l2b_bound_pdf(scope,store)
        # claim은 기존 전역 큐 계약이다. 다른 대기/임대 Job이 있으면 건드리지 않고 준비 오류로 중단한다.
        with psycopg.connect(scope.admin) as connection:
            assert connection.execute("SELECT count(*) FROM studio_generation_jobs WHERE state IN ('queued','leased','generating')").fetchone()==(0,)
        request=StudioGenerationRequest('evidence_report',source,(source_version,),None,None,
            'S42 합성 최종 Job 저장 시험','reviewer',None,'short','summary','pdf','review_required',source_only=True)
        queued,replayed=repository.enqueue_generation(ctx,request,'s42-job-terminal-'+scope.suffix)
        assert replayed is False and queued['status']=='queued'
        job=queue.claim('s42-job-worker-'+scope.suffix,lease_seconds=600)
        if job is None or (job.tenant_id,job.workspace_id,job.actor_id,job.job_id)!=(
                ctx.tenant_id,ctx.workspace_id,ctx.actor_id,queued['job_id']):
            pytest.fail('STUDIO_JOB_CLAIM_NOT_OWN_TARGET; finish 미실행; main 확인 필요',pytrace=False)
        assert (job.state,job.attempt,job.version)==('leased',1,2)
        assert job.request_json['notebook_id']==ctx.notebook_id
        assert job.request_json['source_id']==source and job.request_json['source_version_ids']==[source_version]
        queue.start(job)
        with psycopg.connect(scope.admin) as connection:
            assert connection.execute('SELECT state,version,lease_owner FROM studio_generation_jobs '
                'WHERE tenant_id=%s AND workspace_id=%s AND job_id=%s',
                (ctx.tenant_id,ctx.workspace_id,job.job_id)).fetchone()==('generating',job.version,'s42-job-worker-'+scope.suffix)
            assert connection.execute('SELECT 1 FROM notebook_bindings WHERE tenant_id=%s AND workspace_id=%s '
                "AND notebook_id=%s AND binding_kind='source' AND record_id=%s AND version_id=%s",
                (ctx.tenant_id,ctx.workspace_id,ctx.notebook_id,source,source_version)).fetchone()==(1,)
            assert connection.execute('SELECT count(*) FROM audit_events WHERE tenant_id=%s AND workspace_id=%s '
                "AND action='studio.generation.finish'",(ctx.tenant_id,ctx.workspace_id)).fetchone()==(0,)
            assert connection.execute('SELECT count(*) FROM notification_source_outbox WHERE tenant_id=%s',
                (ctx.tenant_id,)).fetchone()==(0,)
        safe_error='S42_SYNTHETIC_STUDIO_FAILURE'
        expected_origin='audit-studio-job-finish-'+hashlib.sha256(
            f'{ctx.tenant_id}|{ctx.workspace_id}|{job.job_id}|{job.version+1}|failed'.encode()).hexdigest()[:32]
        queue.finish(job,state='failed',error_code=safe_error)
        with psycopg.connect(scope.admin) as connection:
            failed=connection.execute('SELECT state,version,attempt,lease_owner,lease_until,safe_error_code,'
                'studio_output_id,output_version_id,completed_at,actor_id,request_json FROM studio_generation_jobs '
                'WHERE tenant_id=%s AND workspace_id=%s AND job_id=%s',
                (ctx.tenant_id,ctx.workspace_id,job.job_id)).fetchone()
            assert failed is not None and failed[:8]==('failed',job.version+1,1,None,None,safe_error,None,None)
            assert failed[8] is not None and failed[9:]==(ctx.actor_id,dict(job.request_json))
        # 재호출은 기존 lease/version 조건에 의해 거부되며 실제 최종 Job·원감사·의도를 바꾸지 않아야 한다.
        def terminal_snapshot():
            with psycopg.connect(scope.admin) as connection:
                jobs=connection.execute('SELECT to_jsonb(j) FROM studio_generation_jobs j WHERE tenant_id=%s '
                    'ORDER BY job_id',(ctx.tenant_id,)).fetchall()
            return jobs,_l2b_completion_snapshot(scope)
        before_repeated_finish=terminal_snapshot()
        with pytest.raises(StudioGenerationQueueError) as lost:
            queue.finish(job,state='failed',error_code=safe_error)
        assert (lost.value.code,lost.value.retryable)==('STUDIO_JOB_LEASE_LOST',True)
        assert terminal_snapshot()==before_repeated_finish
        with psycopg.connect(scope.admin) as connection:
            audits=connection.execute('SELECT a.event_id,a.actor_id,a.target_type,a.target_id,a.outcome,'
                "a.after_value->>'state',a.after_value->>'version',a.trace_id,a.policy_version,a.xmin=j.xmin "
                'FROM audit_events a JOIN studio_generation_jobs j ON j.tenant_id=a.tenant_id '
                'AND j.workspace_id=a.workspace_id AND j.job_id=a.target_id '
                "WHERE a.tenant_id=%s AND a.workspace_id=%s AND a.action='studio.generation.finish'",
                (ctx.tenant_id,ctx.workspace_id)).fetchall()
            sources=connection.execute('SELECT s.origin_event_id,s.source_store,s.recipient_id,s.action,s.target_type,s.target_id,'
                's.kind,s.severity,s.notebook_id,s.trace_id,s.policy_version,s.state,s.version,s.delivered_at,'
                's.occurred_at=a.occurred_at,s.xmin=a.xmin AND a.xmin=j.xmin '
                'FROM notification_source_outbox s JOIN audit_events a ON a.tenant_id=s.tenant_id '
                'AND a.workspace_id=s.workspace_id AND a.event_id=s.origin_event_id '
                'JOIN studio_generation_jobs j ON j.tenant_id=s.tenant_id AND j.workspace_id=s.workspace_id AND j.job_id=s.target_id '
                'WHERE s.tenant_id=%s AND s.workspace_id=%s',(ctx.tenant_id,ctx.workspace_id)).fetchall()
            for table in ('notifications','notification_details','notification_audit_outbox'):
                assert connection.execute(f'SELECT count(*) FROM {table} WHERE tenant_id=%s',(ctx.tenant_id,)).fetchone()==(0,)
        if not audits:
            pytest.fail('STUDIO_JOB_TERMINAL_ORIGIN_AUDIT_MISSING; actual_failed_job_saved; late_finish_unchanged',pytrace=False)
        assert len(audits)==1 and audits[0][1:]==(ctx.actor_id,'StudioGenerationJob',job.job_id,'succeeded',
            'failed',str(job.version+1),ctx.trace_id,ctx.policy_version,True)
        assert audits[0][0]==expected_origin
        if not sources:
            pytest.fail('STUDIO_JOB_TERMINAL_SOURCE_INTENT_MISSING; actual_failed_job_and_origin_audit_saved',pytrace=False)
        assert sources==[(audits[0][0],'studio',ctx.actor_id,'studio.generation.finish','StudioGenerationJob',job.job_id,
            'run_failed','warning',ctx.notebook_id,ctx.trace_id,ctx.policy_version,'pending',1,None,True,True)]
        # Job 의도는 기존 Studio 4-action 소비 목록에 섞거나 억제/ack하지 않는다.
        with psycopg.connect(scope.app) as connection:
            for name,value in (('tenant_id',ctx.tenant_id),('workspace_id',ctx.workspace_id),('actor_id',ctx.actor_id)):
                connection.execute('SELECT set_config(%s,%s,true)',('app.'+name,value))
            assert connection.execute('SELECT source_event_id FROM public.pending_studio_notification_sources(100)').fetchall()==[]
            expected_source='studio-event-'+hashlib.sha256(f'{expected_origin}|{ctx.workspace_id}'.encode()).hexdigest()[:32]
            for deliver in (False,True):
                assert connection.execute('SELECT public.materialize_studio_notification(%s,%s)',
                    (expected_source,deliver)).fetchone()==(None,)
        assert terminal_snapshot()==before_repeated_finish
        # 실패 Job 선택 query는 승인 대기다. 없는 Run/Version 링크나 대역 알림을 만들지 않는다.
    finally:
        queue.close()
        store.close()


def _l2b_start_owned_studio_job(scope,store):
    """기존 실제 owner enqueue/claim/start를 사용하고 다른 Job을 claim하면 즉시 중단한다."""
    import psycopg
    from daon_user_api.studio_workspace import StudioGenerationRequest
    from daon_user_api.studio_workspace_postgres import PostgresStudioWorkspaceRepository
    ctx=scope.context
    source,version=_l2b_bound_pdf(scope,store)
    with psycopg.connect(scope.admin) as connection:
        assert connection.execute("SELECT count(*) FROM studio_generation_jobs WHERE state IN ('queued','leased','generating')").fetchone()==(0,)
    request=StudioGenerationRequest('evidence_report',source,(version,),None,None,
        'S42 합성 Job 경계 시험','reviewer',None,'short','summary','pdf','review_required',source_only=True)
    repository=PostgresStudioWorkspaceRepository(store)
    queue=repository._generation_queue
    assert queue is not None
    scope.studio_queue=queue
    queued,replayed=repository.enqueue_generation(ctx,request,'s42-job-boundary-'+scope.suffix)
    assert replayed is False and queued['status']=='queued'
    job=queue.claim('s42-job-worker-'+scope.suffix,lease_seconds=600)
    if job is None or (job.tenant_id,job.workspace_id,job.actor_id,job.job_id)!=(
            ctx.tenant_id,ctx.workspace_id,ctx.actor_id,queued['job_id']):
        pytest.fail('STUDIO_JOB_CLAIM_NOT_OWN_TARGET; start/finish 미실행; main 확인 필요',pytrace=False)
    scope.own_studio_job=job
    queue.start(job)
    return queue,job


def _l2b_cleanup_owned_active_studio_job(scope):
    """시험 단언 이후 본인 활성 Job만 기존 OFF finish로 정리한다. 검증 결과와 별도다."""
    from daon_user_api.cloud_storage import PostgresCloudStore,CloudAccessContext
    from daon_user_api.studio_generation_queue import PostgresStudioGenerationQueue
    queue=getattr(scope,'studio_queue',None)
    job=getattr(scope,'own_studio_job',None)
    try:
        if queue is not None and job is not None:
            ctx=scope.context
            current=queue.get(CloudAccessContext(ctx.tenant_id,ctx.workspace_id,ctx.actor_id,'studio.read'),job.job_id)
            assert (current.tenant_id,current.workspace_id,current.actor_id,current.job_id)==(
                ctx.tenant_id,ctx.workspace_id,ctx.actor_id,job.job_id)
            if current.state in {'leased','generating'}:
                cleanup_store=PostgresCloudStore(scope.app,min_size=0,max_size=1,notification_inbox_enabled=False)
                cleanup_queue=PostgresStudioGenerationQueue(scope.app,cleanup_store)
                try:
                    cleanup_queue.finish(current,state='failed',error_code='S42_FIXTURE_CLEANUP')
                finally:
                    cleanup_queue.close()
                    cleanup_store.close()
    finally:
        if queue is not None:
            queue.close()


def test_l2b_existing_0069_studio_job_source_insert_failure_rolls_back_finish():
    """실제 source INSERT 후 SQL22012는 Job 최종 UPDATE·정식 감사·의도를 전부 rollback한다."""
    from unittest.mock import patch
    import psycopg
    from daon_user_api.cloud_storage import PostgresCloudStore,CloudDatabaseError
    from daon_user_api.notification_events import enqueue_completed_event
    scope=_existing_0069_studio_case()
    ctx=scope.context
    store=PostgresCloudStore(scope.app,min_size=0,max_size=2,notification_inbox_enabled=True)
    phases=[]
    faults=[]
    try:
        queue,job=_l2b_start_owned_studio_job(scope,store)
        def snapshot():
            with psycopg.connect(scope.admin) as connection:
                jobs=connection.execute('SELECT to_jsonb(j) FROM studio_generation_jobs j WHERE tenant_id=%s '
                    'AND workspace_id=%s AND job_id=%s',(ctx.tenant_id,ctx.workspace_id,job.job_id)).fetchall()
            return jobs,_l2b_completion_snapshot(scope)
        before=snapshot()
        assert len(before[0])==1 and before[0][0][0]['state']=='generating'
        assert before[1]['notification_source_outbox']==[]
        def actual_enqueue_then_sql_failure(connection,context,**kwargs):
            assert connection.execute('SELECT j.state,j.version,a.actor_id,a.action,a.target_type,a.target_id,a.outcome,'
                "a.after_value->>'state',a.after_value->>'version',j.xmin=a.xmin "
                'FROM studio_generation_jobs j JOIN audit_events a ON a.tenant_id=j.tenant_id '
                'AND a.workspace_id=j.workspace_id AND a.target_id=j.job_id '
                'WHERE j.tenant_id=%s AND j.workspace_id=%s AND j.job_id=%s AND a.event_id=%s',
                (ctx.tenant_id,ctx.workspace_id,job.job_id,kwargs['origin_event_id'])).fetchone()==(
                    'failed',job.version+1,ctx.actor_id,'studio.generation.finish','StudioGenerationJob',job.job_id,
                    'succeeded','failed',str(job.version+1),True)
            assert enqueue_completed_event(connection,context,**kwargs)==1
            phases.append('ACTUAL_JOB_UPDATE_AUDIT_AND_SOURCE_INSERT')
            try:
                connection.execute('SELECT 1/0')
            except psycopg.Error as error:
                faults.append(error.sqlstate)
                raise
        with patch('daon_user_api.notification_events.enqueue_completed_event',side_effect=actual_enqueue_then_sql_failure):
            with pytest.raises(CloudDatabaseError) as caught:
                queue.finish(job,state='failed',error_code='S42_SYNTHETIC_STUDIO_FAILURE')
        assert (caught.value.code,caught.value.retryable)==('DATABASE_UNAVAILABLE',True)
        assert phases==['ACTUAL_JOB_UPDATE_AUDIT_AND_SOURCE_INSERT'] and faults==['22012']
        assert snapshot()==before
    finally:
        # generating 복귀는 위 전체행 비교로 검증한다. 이후 정리는 그 증거에 포함하지 않는다.
        try:
            _l2b_cleanup_owned_active_studio_job(scope)
        finally:
            store.close()


def test_l2b_existing_0069_committed_studio_job_rejects_new_official_origin():
    """OFF에서 실제 저장한 최종 Job에 현재 Tx의 정식형식 감사를 붙여도 source 재발행을 거부한다."""
    import hashlib
    import psycopg
    from psycopg.types.json import Jsonb
    from daon_user_api.cloud_storage import PostgresCloudStore
    scope=_existing_0069_studio_case()
    ctx=scope.context
    # 과거 업무를 재현하는 준비만 OFF다. 실제 enqueue/claim/start/finish는 대체하지 않는다.
    store=PostgresCloudStore(scope.app,min_size=0,max_size=2,notification_inbox_enabled=False)
    try:
        queue,job=_l2b_start_owned_studio_job(scope,store)
        queue.finish(job,state='failed',error_code='S42_SYNTHETIC_STUDIO_FAILURE')
        with psycopg.connect(scope.admin) as connection:
            committed=connection.execute('SELECT to_jsonb(j) FROM studio_generation_jobs j WHERE tenant_id=%s '
                'AND workspace_id=%s AND job_id=%s',(ctx.tenant_id,ctx.workspace_id,job.job_id)).fetchall()
            assert len(committed)==1 and (committed[0][0]['state'],committed[0][0]['version'])==('failed',job.version+1)
            assert connection.execute('SELECT count(*) FROM audit_events WHERE tenant_id=%s '
                "AND action='studio.generation.finish'",(ctx.tenant_id,)).fetchone()==(0,)
            assert connection.execute('SELECT count(*) FROM notification_source_outbox WHERE tenant_id=%s',
                (ctx.tenant_id,)).fetchone()==(0,)
        before=_l2b_completion_snapshot(scope)
        origin='audit-studio-job-finish-'+hashlib.sha256(
            f'{ctx.tenant_id}|{ctx.workspace_id}|{job.job_id}|{job.version+1}|failed'.encode()).hexdigest()[:32]
        with psycopg.connect(scope.app) as connection:
            with connection.transaction(force_rollback=True):
                for name,value in (('tenant_id',ctx.tenant_id),('workspace_id',ctx.workspace_id),
                        ('actor_id',ctx.actor_id),('notebook_id',ctx.notebook_id),('capability','studio.create')):
                    connection.execute('SELECT set_config(%s,%s,true)',('app.'+name,value))
                context_before=connection.execute("SELECT current_user,current_setting('app.actor_id'),current_setting('app.notebook_id')").fetchone()
                assert context_before==('daon_app',ctx.actor_id,ctx.notebook_id)
                # 부정 호출 반례만 현재 Tx에서 삽입하고 전체 forceRollback한다. 기존 원장을 고치지 않는다.
                connection.execute('INSERT INTO audit_events '
                    '(event_id,tenant_id,workspace_id,actor_id,action,target_type,target_id,outcome,trace_id,policy_version,after_value) '
                    "VALUES(%s,%s,%s,%s,'studio.generation.finish','StudioGenerationJob',%s,'succeeded',%s,%s,%s)",
                    (origin,ctx.tenant_id,ctx.workspace_id,ctx.actor_id,job.job_id,ctx.trace_id,ctx.policy_version,
                     Jsonb({'state':'failed','version':job.version+1})))
                assert connection.execute('SELECT j.xmin<>a.xmin FROM studio_generation_jobs j JOIN audit_events a '
                    'ON a.tenant_id=j.tenant_id AND a.workspace_id=j.workspace_id AND a.target_id=j.job_id '
                    'WHERE j.tenant_id=%s AND j.workspace_id=%s AND j.job_id=%s AND a.event_id=%s',
                    (ctx.tenant_id,ctx.workspace_id,job.job_id,origin)).fetchone()==(True,)
                with pytest.raises(psycopg.Error) as rejected:
                    with connection.transaction(force_rollback=True):
                        connection.execute('SELECT public.enqueue_completed_notification(%s,%s)',(origin,ctx.notebook_id))
                assert rejected.value.sqlstate=='23514'
                assert connection.execute("SELECT current_user,current_setting('app.actor_id'),current_setting('app.notebook_id')").fetchone()==context_before
        assert _l2b_completion_snapshot(scope)==before
        with psycopg.connect(scope.admin) as connection:
            assert connection.execute('SELECT to_jsonb(j) FROM studio_generation_jobs j WHERE tenant_id=%s '
                'AND workspace_id=%s AND job_id=%s',(ctx.tenant_id,ctx.workspace_id,job.job_id)).fetchall()==committed
            assert connection.execute('SELECT count(*) FROM audit_events WHERE tenant_id=%s AND event_id=%s',
                (ctx.tenant_id,origin)).fetchone()==(0,)
            assert connection.execute('SELECT count(*) FROM notification_source_outbox WHERE tenant_id=%s',
                (ctx.tenant_id,)).fetchone()==(0,)
    finally:
        try:
            _l2b_cleanup_owned_active_studio_job(scope)
        finally:
            store.close()


@pytest.mark.parametrize('terminal_state',['completed','failed','unavailable'])
def test_sg_guard_existing_0069_preserves_queue_lifecycle_and_immutable_inputs(terminal_state):
    """실제 queue의 재시도/최초 finish와 보호를 검증한다. OFF finish는 completed producer 증거가 아니다."""
    import psycopg
    from psycopg.types.json import Jsonb
    from daon_user_api.cloud_storage import PostgresCloudStore,CloudAccessContext
    from daon_user_api.studio_generation_queue import StudioGenerationQueueError
    scope=_existing_0069_studio_case()
    ctx=scope.context
    store=PostgresCloudStore(scope.app,min_size=0,max_size=2,notification_inbox_enabled=False)
    key=(ctx.tenant_id,ctx.workspace_id)
    def snapshot(job):
        with psycopg.connect(scope.admin) as connection:
            return connection.execute('SELECT to_jsonb(j) FROM studio_generation_jobs j '
                'WHERE tenant_id=%s AND workspace_id=%s AND job_id=%s',(*key,job.job_id)).fetchone()[0]
    try:
        queue,job=_l2b_start_owned_studio_job(scope,store)
        before=snapshot(job)
        assert (before['state'],before['attempt'],before['version'])==('generating',1,2)
        with pytest.raises(psycopg.Error) as caught:
            with psycopg.connect(scope.app) as connection:
                with connection.transaction(force_rollback=True):
                    connection.execute("UPDATE studio_generation_jobs SET state='failed',version=version+1,lease_owner=NULL,lease_until=NULL,"
                        "completed_at='2000-01-01T00:00:00+00:00' WHERE tenant_id=%s AND workspace_id=%s AND job_id=%s",
                        (*key,job.job_id))
        assert caught.value.sqlstate=='23514'
        assert snapshot(job)==before
        for patch in ({'request_json':{}},{'actor_id':'s42-other-actor'},{'tenant_id':'s42-other-tenant'},
                {'workspace_id':'s42-other-workspace'},{'trace_id':'s42-other-trace'},
                {'policy_version':'s42-other-policy'},{'idempotency_key':'s42-other-key'},
                {'created_at':'2000-01-01T00:00:00+00:00'},{'max_attempts':4}):
            with pytest.raises(psycopg.Error) as caught:
                with psycopg.connect(scope.app) as connection:
                    with connection.transaction(force_rollback=True):
                        # state만 정상 start/retry로 바꾸어도 입력 변경을 함께 끼울 수 없다.
                        connection.execute('UPDATE studio_generation_jobs SET '
                            '(tenant_id,workspace_id,actor_id,trace_id,policy_version,idempotency_key,request_json,created_at,max_attempts)='
                            '(SELECT r.tenant_id,r.workspace_id,r.actor_id,r.trace_id,r.policy_version,r.idempotency_key,r.request_json,r.created_at,r.max_attempts '
                            'FROM jsonb_populate_record(NULL::studio_generation_jobs,to_jsonb(studio_generation_jobs)||%s) r) '
                            'WHERE tenant_id=%s AND workspace_id=%s AND job_id=%s',(Jsonb(patch),*key,job.job_id))
            assert caught.value.sqlstate=='23514'
            assert snapshot(job)==before
        # 삽입 시 기존 행을 복사하더라도 queued/v1/attempt0·빈 결과 외의 상태는 거부한다.
        initial={**before,'job_id':'s42-initial-'+scope.suffix,'idempotency_key':'s42-initial-'+scope.suffix,
            'state':'queued','version':1,'attempt':0,'lease_owner':None,'lease_until':None,
            'safe_error_code':None,'studio_output_id':None,'output_version_id':None,'completed_at':None}
        for patch in ({'state':'failed'},{'version':2},{'attempt':1},{'safe_error_code':'S42_SYNTHETIC'},
                {'studio_output_id':'s42-output'},{'output_version_id':'s42-version'},
                {'completed_at':'2000-01-01T00:00:00+00:00'}):
            with pytest.raises(psycopg.Error) as caught:
                with psycopg.connect(scope.app) as connection:
                    with connection.transaction(force_rollback=True):
                        connection.execute('INSERT INTO studio_generation_jobs SELECT '
                            '(jsonb_populate_record(NULL::studio_generation_jobs,%s)).*',(Jsonb({**initial,**patch}),))
            assert caught.value.sqlstate=='23514'
        assert queue.retry(job,error_code='S42_SYNTHETIC_RETRY',delay_seconds=0) is True
        retried=snapshot(job)
        assert (retried['state'],retried['attempt'],retried['version'],retried['lease_owner'],retried['lease_until'])==('queued',1,3,None,None)
        claimed=queue.claim('s42-retry-worker-'+scope.suffix,lease_seconds=600)
        assert claimed is not None and claimed.job_id==job.job_id
        assert (claimed.attempt,claimed.version)==(2,4)
        scope.own_studio_job=claimed
        queue.start(claimed)
        assert snapshot(claimed)['version']==4
        queue.finish(claimed,state=terminal_state)
        terminal=snapshot(claimed)
        assert (terminal['state'],terminal['attempt'],terminal['version'])==(terminal_state,2,5)
        assert terminal['completed_at'] is not None
        for patch in ({},{'version':6},{'state':'queued'},{'request_json':{}},{'actor_id':'s42-other-actor'},
                {'safe_error_code':'S42_CHANGED'},{'studio_output_id':'s42-output'},
                {'output_version_id':'s42-version'},{'completed_at':None},{'attempt':3}):
            with pytest.raises(psycopg.Error) as caught:
                with psycopg.connect(scope.app) as connection:
                    with connection.transaction(force_rollback=True):
                        connection.execute('UPDATE studio_generation_jobs SET '
                            '(state,version,request_json,actor_id,safe_error_code,studio_output_id,output_version_id,completed_at,attempt)='
                            '(SELECT r.state,r.version,r.request_json,r.actor_id,r.safe_error_code,r.studio_output_id,r.output_version_id,r.completed_at,r.attempt '
                            'FROM jsonb_populate_record(NULL::studio_generation_jobs,to_jsonb(studio_generation_jobs)||%s) r) '
                            'WHERE tenant_id=%s AND workspace_id=%s AND job_id=%s',(Jsonb(patch),*key,job.job_id))
            assert caught.value.sqlstate=='23514'
            assert snapshot(claimed)==terminal
        with pytest.raises(StudioGenerationQueueError) as caught:
            queue.finish(claimed,state=terminal_state)
        assert caught.value.code=='STUDIO_JOB_LEASE_LOST'
        assert snapshot(claimed)==terminal
        with psycopg.connect(scope.app) as connection:
            assert connection.execute("SELECT current_user,has_table_privilege(current_user,'studio_generation_jobs','SELECT'),"
                "has_table_privilege(current_user,'studio_generation_jobs','INSERT'),has_table_privilege(current_user,'studio_generation_jobs','UPDATE'),"
                "has_table_privilege(current_user,'studio_generation_jobs','DELETE,TRUNCATE,REFERENCES,TRIGGER')").fetchone()==('daon_app',True,True,True,False)
        with pytest.raises(psycopg.Error) as caught:
            with psycopg.connect(scope.app) as connection:
                connection.execute('SET SESSION AUTHORIZATION daon_app')
                assert connection.execute('SELECT session_user,current_user').fetchone()==('daon_app','daon_app')
                with connection.transaction(force_rollback=True):
                    connection.execute('SET LOCAL ROLE daon_notebook_delete')
        assert caught.value.sqlstate=='42501'
    finally:
        try:
            _l2b_cleanup_owned_active_studio_job(scope)
        finally:
            store.close()


@pytest.mark.parametrize('drift',['when_false','update_of','other_function_schema','other_table_schema',
    'function_body','owner','delete_grant','truncate_grant'])
def test_sg_guard_existing_0069_probe_rejects_exact_protection_drift(drift):
    """SG 필수 보호만 QA transaction에서 변조/rollback한다. 전역 역할과 기존 행은 변경하지 않는다."""
    import psycopg
    from psycopg import sql
    from psycopg.conninfo import conninfo_to_dict
    from daon_user_api.notification_postgres import notification_storage_is_protected
    dsn=_PrivateDsn(os.environ.get('DAON_NOTIFICATION_TEST_DSN') or '')
    if not dsn:
        pytest.skip('DAON_NOTIFICATION_TEST_DSN 미제공: 실제 SG probe 미실행')
    if conninfo_to_dict(dsn).get('dbname')!='daon_user_s42_qa_20261017_l2':
        pytest.fail('승인된 L2 전용 DB가 아님',pytrace=False)
    with psycopg.connect(dsn) as connection:
        assert connection.execute('SELECT version_num FROM alembic_version').fetchall()==[('0069',)]
        assert notification_storage_is_protected(connection)
        jobs=connection.execute('SELECT job_id,to_jsonb(j) FROM studio_generation_jobs j ORDER BY tenant_id,workspace_id,job_id').fetchall()
        with connection.transaction(force_rollback=True):
            if drift in {'when_false','update_of','other_function_schema','other_table_schema'}:
                connection.execute('DROP TRIGGER studio_generation_jobs_guard ON public.studio_generation_jobs')
                if drift=='when_false':
                    connection.execute('CREATE TRIGGER studio_generation_jobs_guard BEFORE INSERT OR UPDATE ON public.studio_generation_jobs '
                        'FOR EACH ROW WHEN(false) EXECUTE FUNCTION public.guard_studio_generation_job()')
                elif drift=='update_of':
                    connection.execute('CREATE TRIGGER studio_generation_jobs_guard BEFORE INSERT OR UPDATE OF version ON public.studio_generation_jobs '
                        'FOR EACH ROW EXECUTE FUNCTION public.guard_studio_generation_job()')
                else:
                    namespace='s42_sg_drift_'+uuid4().hex
                    connection.execute(sql.SQL('CREATE SCHEMA {}').format(sql.Identifier(namespace)))
                    if drift=='other_function_schema':
                        connection.execute(sql.SQL('CREATE FUNCTION {}.guard_studio_generation_job() RETURNS trigger LANGUAGE plpgsql AS '
                            '$body$ BEGIN RETURN NEW; END $body$').format(sql.Identifier(namespace)))
                        connection.execute(sql.SQL('CREATE TRIGGER studio_generation_jobs_guard BEFORE INSERT OR UPDATE ON public.studio_generation_jobs '
                            'FOR EACH ROW EXECUTE FUNCTION {}.guard_studio_generation_job()').format(sql.Identifier(namespace)))
                    else:
                        connection.execute(sql.SQL('CREATE TABLE {}.studio_generation_jobs (LIKE public.studio_generation_jobs)').format(sql.Identifier(namespace)))
                        connection.execute(sql.SQL('CREATE TRIGGER studio_generation_jobs_guard BEFORE INSERT OR UPDATE ON {}.studio_generation_jobs '
                            'FOR EACH ROW EXECUTE FUNCTION public.guard_studio_generation_job()').format(sql.Identifier(namespace)))
            elif drift=='function_body':
                connection.execute('CREATE OR REPLACE FUNCTION public.guard_studio_generation_job() RETURNS trigger LANGUAGE plpgsql '
                    'SET search_path=pg_catalog,pg_temp AS $body$ BEGIN RETURN NEW; END $body$')
            elif drift=='owner':
                connection.execute('ALTER TABLE public.studio_generation_jobs OWNER TO daon_app')
            else:
                privilege='DELETE' if drift=='delete_grant' else 'TRUNCATE'
                connection.execute(sql.SQL('GRANT {} ON public.studio_generation_jobs TO daon_app').format(sql.SQL(privilege)))
            assert not notification_storage_is_protected(connection)
        assert notification_storage_is_protected(connection)
        assert connection.execute('SELECT job_id,to_jsonb(j) FROM studio_generation_jobs j ORDER BY tenant_id,workspace_id,job_id').fetchall()==jobs


def test_l2b_existing_0069_terminal_job_version_bump_cannot_republish_source():
    """실제 최종 Job의 version만 갱신해 xmin/정식 감사 hash를 맞춰도 재발행할 수 없어야 한다."""
    import hashlib
    import psycopg
    from psycopg.types.json import Jsonb
    from daon_user_api.cloud_storage import PostgresCloudStore
    scope=_existing_0069_studio_case()
    ctx=scope.context
    store=PostgresCloudStore(scope.app,min_size=0,max_size=2,notification_inbox_enabled=True)
    accepted=[]
    rejected=[]
    rejection_boundary=[]
    try:
        queue,job=_l2b_start_owned_studio_job(scope,store)
        queue.finish(job,state='failed',error_code='S42_SYNTHETIC_STUDIO_FAILURE')
        with psycopg.connect(scope.admin) as connection:
            original_jobs=connection.execute('SELECT to_jsonb(j) FROM studio_generation_jobs j WHERE tenant_id=%s '
                'AND workspace_id=%s AND job_id=%s',(ctx.tenant_id,ctx.workspace_id,job.job_id)).fetchall()
            assert len(original_jobs)==1
            original_job=original_jobs[0][0]
            assert (original_job['state'],original_job['version'],original_job['actor_id'])==('failed',job.version+1,ctx.actor_id)
            originals=connection.execute('SELECT a.event_id,a.after_value,a.actor_id,a.trace_id,a.policy_version,'
                's.origin_event_id,s.recipient_id,s.state,s.version,a.xmin=j.xmin AND j.xmin=s.xmin '
                'FROM audit_events a JOIN studio_generation_jobs j ON j.tenant_id=a.tenant_id '
                'AND j.workspace_id=a.workspace_id AND j.job_id=a.target_id '
                'JOIN notification_source_outbox s ON s.tenant_id=a.tenant_id AND s.workspace_id=a.workspace_id '
                'AND s.origin_event_id=a.event_id WHERE a.tenant_id=%s AND a.workspace_id=%s '
                "AND a.target_id=%s AND a.action='studio.generation.finish' AND a.target_type='StudioGenerationJob'",
                (ctx.tenant_id,ctx.workspace_id,job.job_id)).fetchall()
            assert len(originals)==1
            original=originals[0]
            assert original[1:]==({'state':'failed','version':job.version+1},ctx.actor_id,ctx.trace_id,ctx.policy_version,
                original[0],ctx.actor_id,'pending',1,True)
        before=_l2b_completion_snapshot(scope)
        new_version=original_job['version']+1
        cloned_origin='audit-studio-job-finish-'+hashlib.sha256(
            f'{ctx.tenant_id}|{ctx.workspace_id}|{job.job_id}|{new_version}|failed'.encode()).hexdigest()[:32]
        assert cloned_origin!=original[0]
        phase='scope'
        try:
            with psycopg.connect(scope.app) as connection:
                with connection.transaction(force_rollback=True):
                    for name,value in (('tenant_id',ctx.tenant_id),('workspace_id',ctx.workspace_id),
                            ('actor_id',ctx.actor_id),('notebook_id',ctx.notebook_id),('capability','studio.create')):
                        connection.execute('SELECT set_config(%s,%s,true)',('app.'+name,value))
                    original_context=connection.execute("SELECT current_user,current_setting('app.actor_id'),current_setting('app.notebook_id')").fetchone()
                    assert original_context==('daon_app',ctx.actor_id,ctx.notebook_id)
                    phase='terminal_update'
                    bumped=connection.execute('UPDATE studio_generation_jobs j SET version=j.version+1 '
                            'WHERE j.tenant_id=%s AND j.workspace_id=%s AND j.job_id=%s AND j.actor_id=%s '
                            "AND j.state='failed' AND j.version=%s RETURNING to_jsonb(j)",
                            (ctx.tenant_id,ctx.workspace_id,job.job_id,ctx.actor_id,original_job['version'])).fetchone()
                    assert bumped is not None and bumped[0]=={**original_job,'version':new_version}
                    # 미보호 후보는 이전 실제 변형까지 그대로 진행해 재발행 허용을 검출한다.
                    phase='clone'
                    clone=connection.execute('INSERT INTO audit_events '
                        '(event_id,tenant_id,workspace_id,actor_id,action,target_type,target_id,outcome,trace_id,policy_version,'
                        'before_value,after_value,metadata) SELECT %s,a.tenant_id,a.workspace_id,a.actor_id,a.action,a.target_type,'
                        'a.target_id,a.outcome,a.trace_id,a.policy_version,a.before_value,a.after_value||%s,a.metadata '
                        'FROM audit_events a WHERE a.tenant_id=%s AND a.workspace_id=%s AND a.event_id=%s '
                        'RETURNING event_id,actor_id,target_id,after_value',
                        (cloned_origin,Jsonb({'version':new_version}),ctx.tenant_id,ctx.workspace_id,original[0])).fetchone()
                    assert clone==(cloned_origin,ctx.actor_id,job.job_id,{'state':'failed','version':new_version})
                    assert connection.execute('SELECT j.xmin=a.xmin FROM studio_generation_jobs j JOIN audit_events a '
                        'ON a.tenant_id=j.tenant_id AND a.workspace_id=j.workspace_id AND a.target_id=j.job_id '
                        'WHERE j.tenant_id=%s AND j.workspace_id=%s AND j.job_id=%s AND a.event_id=%s',
                        (ctx.tenant_id,ctx.workspace_id,job.job_id,cloned_origin)).fetchone()==(True,)
                    phase='enqueue'
                    result=connection.execute('SELECT public.enqueue_completed_notification(%s,%s)',
                        (cloned_origin,ctx.notebook_id)).fetchone()
                    accepted.append(result)
                    assert connection.execute("SELECT current_user,current_setting('app.actor_id'),current_setting('app.notebook_id')").fetchone()==original_context
        except psycopg.Error as error:
            # 오류를 낸 transaction 전체가 종료된 뒤에만 판정한다. bump/clone의 XID를 분리하지 않는다.
            rejected.append(error.sqlstate)
            rejection_boundary.append(phase)
        # 허용되는 결함이 있어도 먼저 반례 전체 rollback과 실제 원행 불변을 확인한다.
        assert _l2b_completion_snapshot(scope)==before
        with psycopg.connect(scope.admin) as connection:
            assert connection.execute('SELECT to_jsonb(j) FROM studio_generation_jobs j WHERE tenant_id=%s '
                'AND workspace_id=%s AND job_id=%s',(ctx.tenant_id,ctx.workspace_id,job.job_id)).fetchall()==original_jobs
            assert connection.execute('SELECT count(*) FROM audit_events WHERE tenant_id=%s AND event_id=%s',
                (ctx.tenant_id,cloned_origin)).fetchone()==(0,)
            assert connection.execute('SELECT count(*) FROM notification_source_outbox WHERE tenant_id=%s AND origin_event_id=%s',
                (ctx.tenant_id,cloned_origin)).fetchone()==(0,)
            assert connection.execute('SELECT count(*) FROM notification_source_outbox WHERE tenant_id=%s',
                (ctx.tenant_id,)).fetchone()==(1,)
        if accepted or rejected!=['23514'] or rejection_boundary!=['terminal_update']:
            pytest.fail('TERMINAL_STUDIO_JOB_VERSION_ONLY_REPUBLISH_NOT_REJECTED; actual_function_boundary; rollback_unchanged',pytrace=False)
    finally:
        try:
            _l2b_cleanup_owned_active_studio_job(scope)
        finally:
            store.close()


def _l2b_qd_history_case():
    """합성 답변만 주입하고 정상 완료/실패 저장은 실제 repository로 수행한다. Provider/Object 호출은 없다."""
    import psycopg
    from daon_user_api.cloud_storage import PostgresCloudStore
    from daon_user_api.notebook import NotebookContext
    from daon_user_api.notebook_postgres import PostgresNotebookRepository
    from daon_user_api.question_answering import GroundedTextResult
    from daon_user_api.question_answering_postgres import PostgresQuestionAnsweringRepository
    prepared=_l2b_question_failed_case(prepare_only=True)
    scope=prepared.scope
    ctx=scope.context
    notebook_context=NotebookContext(ctx.tenant_id,ctx.workspace_id,ctx.actor_id,ctx.trace_id,ctx.policy_version)
    store=PostgresCloudStore(scope.app,min_size=0,max_size=2,notification_inbox_enabled=True)
    try:
        repository=PostgresQuestionAnsweringRepository(store,_L2bNoObjectRead())
        old=repository.persist_completed(prepared.context,run_id='s42-qd-old-'+scope.suffix,
            source_id=None,source_version_id=None,question='S42 합성 일반 대화',selection=prepared.selection,
            evidence=(),result=GroundedTextResult('S42 보존할 정상 답변',(),False,{}),provider_called=False,
            context_mode='general_ungrounded',context_sources=())
        selected=PostgresNotebookRepository(store).read_selected_context(notebook_context,ctx.notebook_id)
        assert selected.conversation is not None
        assert (selected.conversation.run_id,selected.conversation.run_result_id,selected.conversation.answer)==(
            old.run_id,old.run_result_id,'S42 보존할 정상 답변')
        assert selected.conversation_thread_ids==(selected.conversation.conversation_thread_id,)
        repository.persist_failed(prepared.context,run_id=prepared.run,request_fingerprint=prepared.fingerprint,
            context_sources=prepared.items,selection=prepared.selection,egress_authorization=prepared.authorization,
            request_payload=prepared.request_payload,idempotency_key=prepared.run)
        with psycopg.connect(scope.admin) as connection:
            # 시각을 조작하지 않고 실제 두 저장 호출의 순서와 별도 대화 binding을 확인한다.
            assert connection.execute('SELECT r.state,r.conversation_id,b.created_at>old_b.created_at '
                'FROM runs r JOIN notebook_bindings b ON b.tenant_id=r.tenant_id AND b.workspace_id=r.workspace_id '
                "AND b.record_id=r.conversation_id AND b.binding_kind='conversation_thread' "
                'JOIN notebook_bindings old_b ON old_b.tenant_id=b.tenant_id AND old_b.workspace_id=b.workspace_id '
                "AND old_b.notebook_id=b.notebook_id AND old_b.binding_kind='conversation_thread' "
                'AND old_b.record_id=%s WHERE r.tenant_id=%s AND r.workspace_id=%s AND r.record_id=%s '
                'AND b.notebook_id=%s',(selected.conversation.conversation_thread_id,ctx.tenant_id,ctx.workspace_id,
                    prepared.run,ctx.notebook_id)).fetchone()==('failed',prepared.accepted[3],True)
            assert prepared.accepted[3]!=selected.conversation.conversation_thread_id
            assert connection.execute('SELECT count(*) FROM run_results WHERE tenant_id=%s AND workspace_id=%s '
                'AND run_id=%s',(ctx.tenant_id,ctx.workspace_id,prepared.run)).fetchone()==(0,)
        return prepared,old,selected.conversation.conversation_thread_id
    finally:
        store.close()


def test_l2b_existing_0069_failed_binding_preserves_last_completed_notebook_conversation():
    """최신 failed binding이 정상 답변/run/result와 정상 대화의 단일 thread 선택을 지우면 실패한다."""
    from daon_user_api.cloud_storage import PostgresCloudStore
    from daon_user_api.notebook import NotebookContext
    from daon_user_api.notebook_postgres import PostgresNotebookRepository
    prepared,old,thread=_l2b_qd_history_case()
    scope=prepared.scope
    ctx=scope.context
    store=PostgresCloudStore(scope.app,min_size=0,max_size=2,notification_inbox_enabled=True)
    try:
        before=_l2b_completion_snapshot(scope)
        selected=PostgresNotebookRepository(store).read_selected_context(
            NotebookContext(ctx.tenant_id,ctx.workspace_id,ctx.actor_id,ctx.trace_id,ctx.policy_version),ctx.notebook_id)
        assert _l2b_completion_snapshot(scope)==before
        assert selected.conversation is not None,'FAILED_BINDING_HID_LAST_COMPLETED_CONVERSATION'
        assert (selected.conversation.answer,selected.conversation.run_id,selected.conversation.run_result_id,
            selected.conversation.conversation_thread_id)==('S42 보존할 정상 답변',old.run_id,old.run_result_id,thread)
        assert selected.conversation_thread_ids==(thread,)
    finally:
        store.close()


@pytest.mark.parametrize('case',('missing_result','source_unbound','source_acl_denied'))
def test_l2b_existing_0069_newer_completed_never_falls_back_to_old_notebook_answer(case):
    """최신 completed의 결과 부재/자료 해제/명시 ACL 거부는 옛 정상 답변 fallback을 허용하지 않는다."""
    from datetime import datetime,timezone
    import psycopg
    from daon_user_api.cloud_storage import PostgresCloudStore,CloudAccessContext
    from daon_user_api.document_index_postgres import IndexedEvidenceChunk
    from daon_user_api.notebook import NotebookContext
    from daon_user_api.notebook_postgres import PostgresNotebookRepository
    from daon_user_api.question_answering import GroundedTextResult
    from daon_user_api.question_answering_postgres import PostgresQuestionAnsweringRepository
    from daon_user_api.studio_workspace_postgres import PostgresStudioWorkspaceRepository as Canon
    prepared,old,old_thread=_l2b_qd_history_case()
    scope=prepared.scope
    ctx=scope.context
    context=prepared.context
    notebook_context=NotebookContext(ctx.tenant_id,ctx.workspace_id,ctx.actor_id,ctx.trace_id,ctx.policy_version)
    store=PostgresCloudStore(scope.app,min_size=0,max_size=2,notification_inbox_enabled=True)
    try:
        repository=PostgresQuestionAnsweringRepository(store,_L2bNoObjectRead())
        notebooks=PostgresNotebookRepository(store)
        run='s42-qd-new-'+scope.suffix
        if case=='missing_result':
            # 결과를 삭제하지 않는다. 정상 owner INSERT/Canon 전이로 결과 없는 합성 completed 원장만 만든다.
            thread=repository._opaque_id('conversation',run)
            with store._transaction(CloudAccessContext(ctx.tenant_id,ctx.workspace_id,ctx.actor_id,'question.execute')) as connection:
                repository._insert_canon(connection,context,'conversations',thread,
                    {'current_run_id':run,'current_run_result_id':repository._opaque_id('run-result',run)})
                repository._insert_canon(connection,context,'runs',run,repository._run_canonical_payload(
                    question='S42 결과 없는 합성 완료 원장',source_id=None,source_version_id=None,
                    context_mode='general_ungrounded',request_fingerprint=None,selection=prepared.selection,
                    frozen_scope=None),state='accepted',extra_columns=('conversation_id',),extra_values=(thread,))
                version=1
                for state in ('planning','retrieving','generating','validating','completed'):
                    version=repository._transition(connection,context,run,version,state)
            assert notebooks.bind_verified(notebook_context,ctx.notebook_id,binding_kind='conversation_thread',
                record_id=thread,version_id=None,now=datetime.now(timezone.utc)) is False
        else:
            span='s42-qd-span-'+scope.suffix
            with store._transaction(CloudAccessContext(ctx.tenant_id,ctx.workspace_id,ctx.actor_id,'source.create')) as connection:
                Canon._insert(connection,ctx,'evidence_spans',span,{'page':1,'text':'S42 합성 인용 근거'},
                    extra_columns=('source_version_id',),extra_values=(prepared.version,))
            evidence=IndexedEvidenceChunk('s42-qd-chunk-'+scope.suffix,prepared.source,prepared.version,
                1,'S42 합성 인용 근거',span,1.0)
            latest=repository.persist_completed(context,run_id=run,source_id=prepared.source,
                source_version_id=prepared.version,question='S42 최신 합성 자료 질문',selection=prepared.selection,
                evidence=(evidence,),result=GroundedTextResult('S42 최신 자료 답변',(evidence.chunk_id,),False,{}),
                provider_called=False,context_sources=prepared.items)
            allowed=notebooks.read_selected_context(notebook_context,ctx.notebook_id)
            assert allowed.conversation is not None
            assert (allowed.conversation.run_id,allowed.conversation.run_result_id,allowed.conversation.answer)==(
                run,latest.run_result_id,'S42 최신 자료 답변')
            assert len(allowed.conversation.citations)==1
            assert (prepared.source,prepared.version) in allowed.sources
            if case=='source_unbound':
                changed,replayed=notebooks.unbind_source(notebook_context,ctx.notebook_id,source_id=prepared.source,
                    source_version_id=prepared.version,expected_etag=allowed.etag,
                    idempotency_key='s42-qd-unbind-'+scope.suffix,request_fingerprint='d'*64,now=datetime.now(timezone.utc))
                assert replayed is False and changed.status=='unbound'
            else:
                # 실제 현재 자료 ACL만 변경한다. unbind/삭제를 ACL 거부의 대역으로 쓰지 않는다.
                with psycopg.connect(scope.admin) as connection:
                    connection.execute('INSERT INTO identity_auth_source_access '
                        '(tenant_id,workspace_id,user_id,source_version_id,allowed,version,updated_at) '
                        'VALUES(%s,%s,%s,%s,0,1,now())',(ctx.tenant_id,ctx.workspace_id,ctx.actor_id,prepared.version))
        with psycopg.connect(scope.admin) as connection:
            assert connection.execute('SELECT r.state,b.created_at>failed_b.created_at FROM runs r '
                'JOIN notebook_bindings b ON b.tenant_id=r.tenant_id AND b.workspace_id=r.workspace_id '
                "AND b.record_id=r.conversation_id AND b.binding_kind='conversation_thread' "
                'JOIN notebook_bindings failed_b ON failed_b.tenant_id=b.tenant_id AND failed_b.workspace_id=b.workspace_id '
                "AND failed_b.notebook_id=b.notebook_id AND failed_b.binding_kind='conversation_thread' AND failed_b.record_id=%s "
                'WHERE r.tenant_id=%s AND r.workspace_id=%s AND r.record_id=%s AND b.notebook_id=%s',
                (prepared.accepted[3],ctx.tenant_id,ctx.workspace_id,run,ctx.notebook_id)).fetchone()==('completed',True)
            if case=='missing_result':
                assert connection.execute('SELECT count(*) FROM run_results WHERE tenant_id=%s AND workspace_id=%s '
                    'AND run_id=%s',(ctx.tenant_id,ctx.workspace_id,run)).fetchone()==(0,)
            if case=='source_acl_denied':
                assert connection.execute('SELECT allowed FROM identity_auth_source_access WHERE tenant_id=%s '
                    'AND workspace_id=%s AND user_id=%s AND source_version_id=%s',
                    (ctx.tenant_id,ctx.workspace_id,ctx.actor_id,prepared.version)).fetchone()==(0,)
                assert connection.execute('SELECT count(*) FROM notebook_source_unbindings WHERE tenant_id=%s '
                    'AND workspace_id=%s AND notebook_id=%s AND source_id=%s AND source_version_id=%s',
                    (ctx.tenant_id,ctx.workspace_id,ctx.notebook_id,prepared.source,prepared.version)).fetchone()==(0,)
        before=_l2b_completion_snapshot(scope)
        selected=notebooks.read_selected_context(notebook_context,ctx.notebook_id)
        assert _l2b_completion_snapshot(scope)==before
        if case in ('source_unbound','source_acl_denied'):
            assert (prepared.source,prepared.version) not in selected.sources,'CURRENT_DENIED_SOURCE_METADATA_EXPOSED'
        assert selected.conversation is None,'NEWER_COMPLETED_ANSWER_MUST_BE_HIDDEN_WITHOUT_OLD_FALLBACK: '+case
        assert len(selected.conversation_thread_ids)==1
        # 옛 정상 행은 삭제하지 않고 현재 응답에서만 비노출되는지 확인한다.
        with psycopg.connect(scope.admin) as connection:
            assert connection.execute('SELECT r.state,rr.canonical_json->>\'answer\' FROM runs r JOIN run_results rr '
                'ON rr.tenant_id=r.tenant_id AND rr.workspace_id=r.workspace_id AND rr.run_id=r.record_id '
                'WHERE r.tenant_id=%s AND r.workspace_id=%s AND r.record_id=%s AND rr.record_id=%s',
                (ctx.tenant_id,ctx.workspace_id,old.run_id,old.run_result_id)).fetchone()==('completed','S42 보존할 정상 답변')
    finally:
        store.close()


def _l2b_question_failed_case(*,prepare_only=False,raw_question=None,logical_question=None,return_case=False,http_resource_count=None,
    filtered_first_unavailable=False):
    """실제 accepted owner 준비를 재사용한다. 기본 경로의 기존 성공/원자성 단언은 그대로 유지한다."""
    import hashlib
    import psycopg
    from daon_user_api.authorization import Action
    from daon_user_api.cloud_storage import PostgresCloudStore,CloudAccessContext
    from daon_user_api.egress_policy import EgressPolicyContext,EgressPolicyPayload,EgressPolicyService
    from daon_user_api.egress_policy_postgres import PostgresEgressPolicyRepository
    from daon_user_api.open_law_source_access import _current_auth_grant
    from daon_user_api.question_answering import TextModelSelection
    from daon_user_api.question_answering_postgres import PostgresQuestionAnsweringRepository,QuestionContext
    from daon_user_api.question_answering_service import QuestionInputSource,question_request_fingerprint
    from daon_user_api.question_egress import PostgresQuestionEgressAuthorizer
    from daon_user_api.studio_workspace_postgres import PostgresStudioWorkspaceRepository as Canon
    scope=_existing_0069_studio_case()
    ctx=scope.context
    store=PostgresCloudStore(scope.app,min_size=0,max_size=2,notification_inbox_enabled=True)
    try:
        assert http_resource_count is None or type(http_resource_count) is int and http_resource_count in (1,2)
        assert not filtered_first_unavailable or http_resource_count==2
        source,version=_l2b_bound_pdf(scope,store)
        source_scopes=[(scope,source,version)]
        if http_resource_count==2 and not filtered_first_unavailable:
            # 같은 tenant/Workspace/Notebook에 별도 ID만 사용한다. 두 번째 ID가 정렬상 앞서도 요청 순서는 유지한다.
            second_scope=SimpleNamespace(context=ctx,suffix='0-'+scope.suffix)
            second_source,second_version=_l2b_bound_pdf(second_scope,store,expected_activity_count=2)
            source_scopes.append((second_scope,second_source,second_version))
        pairs=[(item_source,item_version) for _,item_source,item_version in source_scopes]
        resolved_pairs=list(pairs)
        unavailable=None
        if filtered_first_unavailable:
            # 실제 기존 seed의 registered 자료를 원 선택 첫 항목으로 둔다. ready로 바꾸거나 필터를 대역으로 만들지 않는다.
            with store._transaction(CloudAccessContext(ctx.tenant_id,ctx.workspace_id,ctx.actor_id,'question.read')) as connection:
                unavailable=connection.execute('SELECT sv.source_id,sv.record_id,s.state,s.version FROM source_versions sv '
                    'JOIN sources s ON s.tenant_id=sv.tenant_id AND s.workspace_id=sv.workspace_id AND s.record_id=sv.source_id '
                    'JOIN notebook_bindings b ON b.tenant_id=sv.tenant_id AND b.workspace_id=sv.workspace_id '
                    "AND b.binding_kind='source' AND b.record_id=sv.source_id AND b.version_id=sv.record_id "
                    'WHERE sv.tenant_id=%s AND sv.workspace_id=%s AND sv.record_id=%s AND b.notebook_id=%s',
                    (ctx.tenant_id,ctx.workspace_id,scope.source_version_id,ctx.notebook_id)).fetchone()
                assert unavailable is not None and unavailable[2]=='registered' and unavailable[:2]!=(source,version)
                assert connection.execute('SELECT count(*) FROM index_versions WHERE tenant_id=%s AND workspace_id=%s '
                    'AND source_version_id=%s',(ctx.tenant_id,ctx.workspace_id,unavailable[1])).fetchone()==(0,)
            resolved_pairs=[unavailable[:2],(source,version)]
        for item_scope,item_source,item_version in source_scopes:
            with store._transaction(CloudAccessContext(ctx.tenant_id,ctx.workspace_id,ctx.actor_id,'source.create')) as connection:
                source_state_version=1
                for state in ('security_check','processing','indexing','ready'):
                    source_state_version=Canon._transition(connection,ctx,'Source',item_source,source_state_version,state,
                        's42-q-failed-source-'+state+'-'+item_scope.suffix)
        # 실제 불변 정책/현재 binding을 기존 owner fixture로 준비하고 실제 서비스가 교집합을 읽는다.
        with psycopg.connect(scope.admin) as connection:
            for policy_scope in ('organization','workspace'):
                _studio_case_egress(connection,scope,policy_scope,EgressPolicyPayload.deny_external().as_dict(),1)
        context=QuestionContext(ctx.tenant_id,ctx.workspace_id,ctx.actor_id,ctx.trace_id,ctx.policy_version,ctx.notebook_id)
        policy_service=EgressPolicyService(PostgresEgressPolicyRepository(store))
        effective=policy_service.get_effective(EgressPolicyContext(ctx.tenant_id,ctx.tenant_id,ctx.workspace_id,
            ctx.actor_id,ctx.trace_id,ctx.policy_version))
        assert effective.mode=='deny_external'
        selection=TextModelSelection('OLLAMA','https://s42-model.invalid','s42-q-failed-profile-'+scope.suffix,
            's42-q-failed-deployment-'+scope.suffix,'s42-synthetic-model',1,provider_kind='server_internal')
        run='s42-q-failed-run-'+scope.suffix
        question='S42 합성 최종 실패 저장 시험' if raw_question is None else raw_question
        logical=question if logical_question is None else logical_question
        request_payload={'question':logical,'source_id':source,'source_version_id':version,'context_mode':'raw_only'}
        if http_resource_count is not None:
            request_payload={'notebook_id':ctx.notebook_id,'question':logical,'knowledge_context':{
                'mode':'raw_only','resources':[{'resource_kind':'source','resource_id':item_source,'version_id':item_version}
                    for item_source,item_version in resolved_pairs]}}
        fingerprint=question_request_fingerprint(context,run_id=run,idempotency_key=run,request_payload=request_payload)
        items=tuple(QuestionInputSource('raw_source',item_source,item_source,item_version) for item_source,item_version in pairs)
        resolved_items=tuple(QuestionInputSource('raw_source',item_source,item_source,item_version)
            for item_source,item_version in resolved_pairs)
        # 실제 라우팅/egress 원장만 저장한다. Provider/모델/transport/Object를 호출하지 않는다.
        authorization=PostgresQuestionEgressAuthorizer(store,policy_service).authorize(context,run_id=run,
            source_id=source,source_version_id=version,selection=selection,
            provider_payload=b'{"messages":[{"role":"user","content":"S42 synthetic question"}]}',
            question=question,context_mode='raw_only',request_fingerprint=fingerprint)
        assert authorization['provider_owner'] is True and authorization['policy_fingerprint']==effective.fingerprint
        frozen=authorization['frozen_routing_context']
        assert frozen['provider_kind']=='server_internal' and frozen['mode']=='deny_external'
        with store._transaction(CloudAccessContext(ctx.tenant_id,ctx.workspace_id,ctx.actor_id,'question.read')) as connection:
            _current_auth_grant(context,connection,Action.QUERY)
            assert connection.execute('SELECT 1 FROM identity_memberships m JOIN identity_users u ON u.user_id=m.user_id '
                'JOIN user_accounts a ON a.tenant_id=m.tenant_id AND a.user_id=m.user_id '
                "WHERE m.tenant_id=%s AND m.user_id=%s AND u.state='active'",
                (ctx.tenant_id,ctx.actor_id)).fetchone()==(1,)
            for item_source,item_version in pairs:
                assert connection.execute('SELECT 1 FROM notebook_bindings b JOIN source_versions sv '
                    'ON sv.tenant_id=b.tenant_id AND sv.workspace_id=b.workspace_id AND sv.record_id=b.version_id '
                    'AND sv.source_id=b.record_id JOIN sources s ON s.tenant_id=sv.tenant_id AND s.workspace_id=sv.workspace_id '
                    "AND s.record_id=sv.source_id WHERE b.tenant_id=%s AND b.workspace_id=%s AND b.notebook_id=%s "
                    "AND b.binding_kind='source' AND b.record_id=%s AND b.version_id=%s AND s.state='ready' "
                    'AND NOT EXISTS(SELECT 1 FROM identity_auth_source_access a WHERE a.tenant_id=b.tenant_id '
                    'AND a.workspace_id=b.workspace_id AND a.user_id=%s AND a.source_version_id=b.version_id AND a.allowed=0) '
                    'AND NOT EXISTS(SELECT 1 FROM notebook_source_unbindings u WHERE u.tenant_id=b.tenant_id '
                    'AND u.workspace_id=b.workspace_id AND u.notebook_id=b.notebook_id AND u.source_id=b.record_id AND u.source_version_id=b.version_id) '
                    'AND NOT EXISTS(SELECT 1 FROM deletion_requests d WHERE d.tenant_id=s.tenant_id AND d.workspace_id=s.workspace_id '
                    'AND d.source_id=s.record_id AND d.source_active=false)',
                    (ctx.tenant_id,ctx.workspace_id,ctx.notebook_id,item_source,item_version,ctx.actor_id)).fetchone()==(1,)
        with psycopg.connect(scope.admin) as connection:
            accepted=connection.execute('SELECT state,version,created_by,conversation_id,canonical_json FROM runs '
                'WHERE tenant_id=%s AND workspace_id=%s AND record_id=%s',(ctx.tenant_id,ctx.workspace_id,run)).fetchone()
            assert accepted is not None and accepted[:3]==('accepted',1,ctx.actor_id)
            assert accepted[4]['question']==question
            assert (accepted[4]['request_fingerprint'],accepted[4]['source_id'],accepted[4]['source_version_id'],
                accepted[4]['context_mode'],accepted[4]['frozen_routing_context'])==(
                    fingerprint,source,version,'raw_only',frozen)
            assert connection.execute('SELECT canonical_json->\'allowed\',canonical_json->\'frozen_routing_context\' '
                'FROM egress_decisions WHERE tenant_id=%s AND workspace_id=%s AND record_id=%s AND run_id=%s',
                (ctx.tenant_id,ctx.workspace_id,authorization['egress_decision_id'],run)).fetchone()==(True,frozen)
            assert connection.execute("SELECT canonical_json->>'routing_status',canonical_json->>'selected_deployment_id' "
                'FROM routing_decisions WHERE tenant_id=%s AND workspace_id=%s AND record_id=%s',
                (ctx.tenant_id,ctx.workspace_id,authorization['routing_decision_id'])).fetchone()==('selected',selection.deployment_id)
            for table in ('run_results','citations','model_attempts','run_snapshots','notification_source_outbox'):
                assert connection.execute(f'SELECT count(*) FROM {table} WHERE tenant_id=%s',(ctx.tenant_id,)).fetchone()==(0,)
        prepared=SimpleNamespace(scope=scope,context=context,source=source,version=version,run=run,
            request_payload=request_payload,fingerprint=fingerprint,items=items,selection=selection,
            authorization=authorization,accepted=accepted,frozen=frozen,resolved_items=resolved_items,unavailable=unavailable)
        if prepare_only:
            return prepared
        repository=PostgresQuestionAnsweringRepository(store,_L2bNoObjectRead())
        # 메서드 부재 RED는 실제 accepted Run·원소속·자료·frozen routing을 확인한 뒤에만 회수한다.
        repository.persist_failed(context,run_id=run,request_fingerprint=fingerprint,context_sources=items,
            selection=selection,egress_authorization=authorization,request_payload=request_payload,idempotency_key=run,
            question=question,**({'resolved_context_sources':resolved_items} if filtered_first_unavailable else {}))
        with psycopg.connect(scope.admin) as connection:
            failed=connection.execute('SELECT state,version,created_by,conversation_id,canonical_json FROM runs '
                'WHERE tenant_id=%s AND workspace_id=%s AND record_id=%s',(ctx.tenant_id,ctx.workspace_id,run)).fetchone()
            assert failed==('failed',accepted[1]+1,ctx.actor_id,accepted[3],accepted[4])
            origin='tr-'+hashlib.sha256(f'{run}|failed|{accepted[1]}'.encode()).hexdigest()[:32]
            receipts=connection.execute('SELECT t.transition_id,t.source_state,t.target_state,t.transition_version,t.reason_code,'
                'a.actor_id,a.outcome,a.after_value,r.expected_version,r.current_version,r.safe_error_code,r.outcome,r.result_state,r.result_version '
                'FROM canon_state_transitions t JOIN audit_events a ON a.tenant_id=t.tenant_id AND a.workspace_id=t.workspace_id '
                'AND a.event_id=t.transition_id JOIN canon_transition_attempts r ON r.tenant_id=t.tenant_id '
                'AND r.workspace_id=t.workspace_id AND r.attempt_id=t.transition_id '
                "WHERE t.tenant_id=%s AND t.workspace_id=%s AND t.entity_type='Run' AND t.record_id=%s AND t.target_state='failed'",
                (ctx.tenant_id,ctx.workspace_id,run)).fetchall()
            assert receipts==[(origin,'accepted','failed',2,'QUESTION_FAILED',ctx.actor_id,'succeeded',
                {'state':'failed','version':2},1,1,None,'succeeded','failed',2)]
            snapshot=connection.execute('SELECT ks.canonical_json,ss.canonical_json,rs.canonical_json '
                'FROM run_snapshots rs JOIN scope_snapshots ss ON ss.tenant_id=rs.tenant_id AND ss.workspace_id=rs.workspace_id '
                'AND ss.record_id=rs.scope_snapshot_id JOIN knowledge_scopes ks ON ks.tenant_id=ss.tenant_id '
                'AND ks.workspace_id=ss.workspace_id AND ks.record_id=ss.knowledge_scope_id '
                'WHERE rs.tenant_id=%s AND rs.workspace_id=%s AND rs.run_id=%s',(ctx.tenant_id,ctx.workspace_id,run)).fetchall()
            assert len(snapshot)==1
            expected_items=[{'origin':'raw_source','context_item_id':item_source,'source_id':item_source,
                'source_version_id':item_version,'digest_sha256':None} for item_source,item_version in pairs]
            for scope_payload in snapshot[0][:2]:
                assert (scope_payload['mode'],scope_payload['items'],scope_payload['source_version_ids'])==(
                    'raw_only',expected_items,[item_version for _,item_version in pairs])
            assert snapshot[0][2]['frozen_routing_context']==frozen
            assert snapshot[0][2]['egress_decision_id']==authorization['egress_decision_id']
            assert snapshot[0][2]['source_version_ids']==[item_version for _,item_version in pairs]
            assert snapshot[0][2]['authority']==['source']
            assert snapshot[0][2]['prompt_version']=='cp3-grounded-question-v1'
            assert snapshot[0][2]['tool_version']=='document-index-v1'
            assert connection.execute('SELECT count(*) FROM notebook_bindings WHERE tenant_id=%s AND workspace_id=%s '
                "AND notebook_id=%s AND binding_kind='conversation_thread' AND record_id=%s AND created_by=%s",
                (ctx.tenant_id,ctx.workspace_id,ctx.notebook_id,accepted[3],ctx.actor_id)).fetchone()==(1,)
            sources=connection.execute('SELECT s.recipient_id,s.source_store,s.origin_event_id,s.action,s.target_type,s.target_id,'
                's.kind,s.severity,s.notebook_id,s.trace_id,s.policy_version,s.state,s.version,s.occurred_at=a.occurred_at,'
                's.xmin=a.xmin AND a.xmin=r.xmin AND r.xmin=t.xmin AND t.xmin=receipt.xmin AND receipt.xmin=rs.xmin '
                'FROM notification_source_outbox s JOIN audit_events a ON a.tenant_id=s.tenant_id AND a.workspace_id=s.workspace_id '
                'AND a.event_id=s.origin_event_id JOIN runs r ON r.tenant_id=s.tenant_id AND r.workspace_id=s.workspace_id AND r.record_id=s.target_id '
                'JOIN canon_state_transitions t ON t.tenant_id=s.tenant_id AND t.workspace_id=s.workspace_id AND t.transition_id=a.event_id '
                'JOIN canon_transition_attempts receipt ON receipt.tenant_id=t.tenant_id AND receipt.workspace_id=t.workspace_id '
                'AND receipt.attempt_id=t.transition_id JOIN run_snapshots rs ON rs.tenant_id=r.tenant_id AND rs.workspace_id=r.workspace_id '
                'AND rs.run_id=r.record_id WHERE s.tenant_id=%s AND s.workspace_id=%s',(ctx.tenant_id,ctx.workspace_id)).fetchall()
            assert sources==[(ctx.actor_id,'question',origin,'canon.transition','Run',run,'run_failed','warning',
                ctx.notebook_id,ctx.trace_id,ctx.policy_version,'pending',1,True,True)]
            for table in ('run_results','citations','model_attempts','notifications','notification_details','notification_audit_outbox'):
                assert connection.execute(f'SELECT count(*) FROM {table} WHERE tenant_id=%s',(ctx.tenant_id,)).fetchone()==(0,)
        before_pending_read=_l2b_completion_snapshot(scope)
        with psycopg.connect(scope.app) as connection:
            for name,value in (('tenant_id',ctx.tenant_id),('workspace_id',ctx.workspace_id),('actor_id',ctx.actor_id)):
                connection.execute('SELECT set_config(%s,%s,true)',('app.'+name,value))
            # 실제 실패 source만 정확한 원사건/본인 범위로 조회한다. 조회 자체는 ack/suppress하지 않는다.
            assert connection.execute('SELECT source_event_id,tenant_id,workspace_id,recipient_id,source_store,'
                'origin_event_id,action,target_type,target_id,kind,severity,notebook_id,trace_id,policy_version,state,version '
                'FROM public.pending_completed_notification_sources(100)').fetchall()==[(
                    'question-event-'+hashlib.sha256(f'{origin}|{ctx.workspace_id}'.encode()).hexdigest()[:32],
                    ctx.tenant_id,ctx.workspace_id,ctx.actor_id,'question',origin,'canon.transition','Run',run,
                    'run_failed','warning',ctx.notebook_id,ctx.trace_id,ctx.policy_version,'pending',1)]
        assert _l2b_completion_snapshot(scope)==before_pending_read
        if return_case:
            return prepared
    finally:
        store.close()


def test_l2b_existing_0069_question_failed_persists_from_actual_accepted_owner_run():
    """실제 정책/egress owner의 accepted Run 이후 실패 저장·원감사·source 동일 Tx를 요구한다."""
    _l2b_question_failed_case()


@pytest.mark.parametrize('resource_count',(1,2))
def test_l2b_existing_0069_question_failed_persists_http_raw_resources_in_original_order(resource_count):
    """실제 HTTP형 원 body로 egress accepted를 만든 뒤 원 선택 순서의 failed snapshot/source를 요구한다."""
    prepared=_l2b_question_failed_case(http_resource_count=resource_count,return_case=True)
    resources=prepared.request_payload['knowledge_context']['resources']
    assert len(resources)==resource_count and len(prepared.items)==resource_count
    assert prepared.request_payload=={'notebook_id':prepared.context.notebook_id,
        'question':prepared.accepted[4]['question'],'knowledge_context':{'mode':'raw_only','resources':[
            {'resource_kind':'source','resource_id':item.source_id,'version_id':item.source_version_id} for item in prepared.items]}}
    assert (prepared.accepted[4]['source_id'],prepared.accepted[4]['source_version_id'])==(
        resources[0]['resource_id'],resources[0]['version_id'])


def _l2b_registered_knowledge_case(*,registration_count):
    """실제 승인 Output과 기존 등록 저장 메서드로 적격 metadata를 최초 INSERT한다. 외부 Daon 호출은 없다."""
    import hashlib
    import json
    from dataclasses import asdict
    from datetime import timedelta
    import psycopg
    from daon_user_api.authorization import Action
    from daon_user_api.cloud_storage import PostgresCloudStore,CloudAccessContext
    from daon_user_api.comparison_table import ComparisonTable
    from daon_user_api.data_canon import canonical_json_bytes
    from daon_user_api.open_law_source_access import _current_auth_grant
    from daon_user_api.studio_workspace_postgres import PostgresStudioWorkspaceRepository
    assert registration_count in (1,2)
    # 등록 시나리오만 기존 comparison builder의 정상 Mapping을 최초 INSERT한다. 기본 scalar seed는 보존한다.
    structured=json.loads(canonical_json_bytes(asdict(ComparisonTable().compare(
        {'근거':'S42 원문'},{'근거':'S42 승인 합성 지식'},
        'evidence','s42-registration-result',{'근거':('S42 합성 근거','S42 합성 근거')}))))
    scope=_existing_0069_studio_case(structured_content=structured)
    ctx=scope.context
    store=PostgresCloudStore(scope.app,min_size=0,max_size=2,notification_inbox_enabled=True)
    try:
        with store._transaction(CloudAccessContext(ctx.tenant_id,ctx.workspace_id,ctx.actor_id,'studio.read')) as connection:
            assert _current_auth_grant(ctx,connection,Action.KNOWLEDGE_REGISTER).allowed is True
            output=connection.execute('SELECT state,canonical_json,canonical_text,digest_sha256 FROM output_versions '
                'WHERE tenant_id=%s AND workspace_id=%s AND record_id=%s',
                (ctx.tenant_id,ctx.workspace_id,scope.version)).fetchone()
            assert output is not None and output[0]=='approved' and 'content' in output[1]
            assert output[1]['content']==structured
            now=connection.execute('SELECT CURRENT_TIMESTAMP').fetchone()[0]
        content=canonical_json_bytes(output[1]['content'])
        digest=hashlib.sha256(content).hexdigest()
        package='s42-registration-package-'+scope.suffix
        # 기존 package metadata 필드만 내부 owner 등록 입력에 사용한다. HTTP 입력 확장/과거 불변행 UPDATE는 없다.
        payload={'output_version_id':scope.version,'explicit':True,'package_id':package,
            'producer':'daon3','producer_version':'3.0','authority':'approved','package_digest':digest,
            'byte_size':len(content),'content_type':'application/json',
            'effective_at':(now-timedelta(days=1)).isoformat(),'expires_at':(now+timedelta(days=1)).isoformat()}
        results=[]
        for index in range(registration_count):
            result,replayed=PostgresStudioWorkspaceRepository(store).record_action(ctx,'knowledge_registration',payload,
                's42-registration-'+str(index)+'-'+scope.suffix)
            assert replayed is False and (result['status'],result['searchable'])==('registered',True)
            results.append(result)
        with psycopg.connect(scope.admin) as connection:
            rows=connection.execute('SELECT kr.record_id,kr.registered_source_version_id,sv.source_id,kr.state,ov.state,'
                's.state,kr.canonical_json FROM knowledge_registrations kr JOIN output_versions ov '
                'ON ov.tenant_id=kr.tenant_id AND ov.workspace_id=kr.workspace_id AND ov.record_id=kr.output_version_id '
                'JOIN source_versions sv ON sv.tenant_id=kr.tenant_id AND sv.workspace_id=kr.workspace_id '
                'AND sv.record_id=kr.registered_source_version_id JOIN sources s ON s.tenant_id=sv.tenant_id '
                'AND s.workspace_id=sv.workspace_id AND s.record_id=sv.source_id JOIN notebook_bindings b '
                'ON b.tenant_id=sv.tenant_id AND b.workspace_id=sv.workspace_id AND b.record_id=sv.source_id '
                "AND b.version_id=sv.record_id AND b.binding_kind='source' AND b.notebook_id=%s "
                'WHERE kr.tenant_id=%s AND kr.workspace_id=%s ORDER BY kr.record_id',
                (ctx.notebook_id,ctx.tenant_id,ctx.workspace_id)).fetchall()
            assert len(rows)==registration_count
            assert {row[0] for row in rows}=={result['record_id'] for result in results}
            assert {row[1] for row in rows}=={result['registered_source_version_id'] for result in results}
            assert len({row[1] for row in rows})==registration_count
            assert all(row[3:6]==('registered','approved','ready') and row[6]==payload for row in rows)
            assert connection.execute('SELECT state,canonical_json,canonical_text,digest_sha256 FROM output_versions '
                'WHERE tenant_id=%s AND workspace_id=%s AND record_id=%s',
                (ctx.tenant_id,ctx.workspace_id,scope.version)).fetchone()==output
            for result in results:
                assert connection.execute('SELECT action,target_type,target_id,outcome FROM audit_events '
                    "WHERE tenant_id=%s AND workspace_id=%s AND actor_id=%s AND action='studio.knowledge_registration' "
                    'AND target_id=%s',(ctx.tenant_id,ctx.workspace_id,ctx.actor_id,result['record_id'])).fetchall()==[
                        ('studio.knowledge_registration','knowledge_registrations',result['record_id'],'succeeded')]
        return SimpleNamespace(scope=scope,package=package,digest=digest,now=now,registrations=rows)
    finally:
        store.close()


def test_registered_package_access_selection_binds_real_snapshot_with_accepted_owner():
    """실제 등록/해석/accepted 저장만 검증한다. 합성 내부 모델 설정이며 Provider/Object/외부 package I/O는 없다."""
    import hashlib
    import inspect
    import json
    from dataclasses import replace
    import psycopg
    from daon_user_api.authorization import Action
    from daon_user_api.cloud_storage import PostgresCloudStore,CloudAccessContext
    from daon_user_api.data_canon import canonical_json_bytes
    from daon_user_api.egress_policy import EgressPolicyContext,EgressPolicyPayload,EgressPolicyService
    from daon_user_api.egress_policy_postgres import PostgresEgressPolicyRepository
    from daon_user_api.knowledge_package import KnowledgePackageContext
    from daon_user_api.knowledge_package_postgres import PostgresKnowledgePackageService
    from daon_user_api.open_law_source_access import _current_auth_grant
    from daon_user_api.question_answering import TextModelSelection
    from daon_user_api.question_answering_postgres import QuestionContext,PostgresQuestionAnsweringRepository
    from daon_user_api.question_answering_service import QuestionInputSource,question_request_fingerprint
    from daon_user_api.question_egress import PostgresQuestionEgressAuthorizer
    prepared=_l2b_registered_knowledge_case(registration_count=1)
    scope=prepared.scope
    ctx=scope.context
    registration,version,source=prepared.registrations[0][:3]
    class NoPackageIO:
        def read_package(self,*args,**kwargs):
            raise AssertionError('K1 metadata 선택은 외부 package를 읽지 않아야 한다')
    store=PostgresCloudStore(scope.app,min_size=0,max_size=2,notification_inbox_enabled=True)
    try:
        package_context=KnowledgePackageContext(ctx.tenant_id,ctx.workspace_id,ctx.actor_id,ctx.trace_id,ctx.policy_version,
            's42-k-selection-device-'+scope.suffix)
        service=PostgresKnowledgePackageService(store,NoPackageIO(),clock=lambda:prepared.now)
        resolved=service.resolve_question_sources(package_context,(prepared.package,))
        assert len(resolved)==1
        selected=resolved[0]
        assert (selected.package_id,selected.source_id,selected.source_version_id,selected.digest_sha256,
            selected.knowledge_registration_id)==(prepared.package,source,version,prepared.digest,registration)
        item={'origin':'daon_knowledge','context_item_id':prepared.package,'source_id':source,
            'source_version_id':version,'digest_sha256':prepared.digest}
        items=(QuestionInputSource(item['origin'],item['context_item_id'],source,version,prepared.digest,
            knowledge_registration_id=registration),)
        # 설계의 canonical JSON 배열을 UTF-8로 만든다. Mapping 전용 helper에 배열을 넘기거나 기존 5필드를 바꾸지 않는다.
        identity_bytes=json.dumps([ctx.tenant_id,ctx.workspace_id,ctx.notebook_id,ctx.actor_id,registration,item],
            ensure_ascii=False,sort_keys=True,separators=(',',':'),allow_nan=False).encode('utf-8')
        identity_hash=hashlib.sha256(identity_bytes).hexdigest()[:32]
        snapshot_id='scope-snapshot-knowledge-'+identity_hash
        scope_id='scope-knowledge-'+identity_hash
        assert prepared.package!=snapshot_id
        immutable_tables=('sources','source_versions','studio_outputs','output_versions','generation_requests','knowledge_registrations')
        def immutable_rows(connection):
            return {table:connection.execute(f'SELECT to_jsonb(t) FROM public.{table} t WHERE tenant_id=%s '
                'AND workspace_id=%s ORDER BY to_jsonb(t)::text',(ctx.tenant_id,ctx.workspace_id)).fetchall() for table in immutable_tables}
        with psycopg.connect(scope.admin) as connection:
            assert connection.execute('SELECT record_id FROM scope_snapshots WHERE tenant_id=%s AND workspace_id=%s AND record_id=%s',
                (ctx.tenant_id,ctx.workspace_id,prepared.package)).fetchall()==[]
            assert connection.execute('SELECT record_id FROM scope_snapshots WHERE tenant_id=%s AND workspace_id=%s AND record_id=%s',
                (ctx.tenant_id,ctx.workspace_id,snapshot_id)).fetchall()==[]
            assert connection.execute("SELECT record_id FROM notebook_bindings WHERE tenant_id=%s AND workspace_id=%s "
                "AND notebook_id=%s AND binding_kind='knowledge_context'",(ctx.tenant_id,ctx.workspace_id,ctx.notebook_id)).fetchall()==[]
            original_rows=immutable_rows(connection)
            raw_bindings=connection.execute("SELECT to_jsonb(b) FROM notebook_bindings b WHERE tenant_id=%s AND workspace_id=%s "
                "AND notebook_id=%s AND binding_kind='source' ORDER BY record_id,version_id",(ctx.tenant_id,ctx.workspace_id,ctx.notebook_id)).fetchall()
            activities_before=connection.execute('SELECT sequence,activity_kind,actor_id FROM notebook_activities '
                'WHERE tenant_id=%s AND workspace_id=%s AND notebook_id=%s ORDER BY sequence',
                (ctx.tenant_id,ctx.workspace_id,ctx.notebook_id)).fetchall()
            for policy_scope in ('organization','workspace'):
                _studio_case_egress(connection,scope,policy_scope,EgressPolicyPayload.deny_external().as_dict(),1)
        context=QuestionContext(ctx.tenant_id,ctx.workspace_id,ctx.actor_id,ctx.trace_id,ctx.policy_version,ctx.notebook_id)
        with store._transaction(CloudAccessContext(ctx.tenant_id,ctx.workspace_id,ctx.actor_id,'question.read')) as connection:
            assert _current_auth_grant(context,connection,Action.QUERY).allowed is True
            assert _current_auth_grant(context,connection,Action.VIEW).allowed is True
        repository=PostgresQuestionAnsweringRepository(store,_L2bNoObjectRead())
        assert repository.load_ready_source(context,source,version) is not None
        policies=EgressPolicyService(PostgresEgressPolicyRepository(store))
        effective=policies.get_effective(EgressPolicyContext(ctx.tenant_id,ctx.tenant_id,ctx.workspace_id,
            ctx.actor_id,ctx.trace_id,ctx.policy_version))
        assert effective.mode=='deny_external'
        selection=TextModelSelection('OLLAMA','https://s42-model.invalid','s42-k-profile-'+scope.suffix,
            's42-k-deployment-'+scope.suffix,'s42-synthetic-model',1,provider_kind='server_internal')
        run='s42-k-accepted-'+scope.suffix
        key='s42-k-key-'+scope.suffix
        question='S42 합성 등록 지식 선택 시험'
        logical_body={'notebook_id':ctx.notebook_id,'question':question,'knowledge_context':{
            'mode':'daon_priority','resources':[{'resource_kind':'knowledge_package','resource_id':prepared.package}]}}
        fingerprint=question_request_fingerprint(context,run_id=run,idempotency_key=key,request_payload=logical_body)
        expected_selection={'schema_version':1,'request_fingerprint':fingerprint,'requested_mode':'daon_priority',
            'resources':[{'resource_kind':'knowledge_package','resource_id':prepared.package}],
            'resolved_items':[{**item,'knowledge_registration_id':registration,'notebook_scope_snapshot_id':snapshot_id}]}
        authorizer=PostgresQuestionEgressAuthorizer(store,policies)
        carry={'request_payload':logical_body,'idempotency_key':key,'context_sources':items}
        # 기존 포트에서도 accepted까지 실행해 실제 저장 누락 RED를 얻는다. 새 포트는 세 원값 전달을 모두 요구한다.
        parameters=inspect.signature(authorizer.authorize).parameters
        forwarded={name:value for name,value in carry.items() if name in parameters}
        def authorize(current):
            return authorizer.authorize(current,run_id=run,source_id=source,source_version_id=version,selection=selection,
                provider_payload=b'{"messages":[{"role":"user","content":"S42 internal selection fixture"}]}',
                no_external_payload=True,question=question,context_mode='daon_priority',request_fingerprint=fingerprint,**forwarded)
        authorization=authorize(context)
        assert authorization['provider_owner'] is True
        assert authorization['policy_fingerprint']==effective.fingerprint
        with psycopg.connect(scope.admin) as connection:
            accepted=connection.execute('SELECT state,version,created_by,trace_id,canonical_json FROM runs '
                'WHERE tenant_id=%s AND workspace_id=%s AND record_id=%s',(ctx.tenant_id,ctx.workspace_id,run)).fetchone()
            assert accepted is not None and accepted[:4]==('accepted',1,ctx.actor_id,ctx.trace_id)
            assert (accepted[4]['question'],accepted[4]['request_fingerprint'],accepted[4]['context_mode'],
                accepted[4]['source_id'],accepted[4]['source_version_id'])==(question,fingerprint,'daon_priority',source,version)
            assert accepted[4]['frozen_routing_context']==authorization['frozen_routing_context']
            egress=connection.execute('SELECT canonical_json FROM egress_decisions WHERE tenant_id=%s AND workspace_id=%s '
                'AND record_id=%s AND run_id=%s',(ctx.tenant_id,ctx.workspace_id,authorization['egress_decision_id'],run)).fetchone()
            assert egress is not None and egress[0]['allowed'] is True
            assert egress[0]['run_id']==run and egress[0]['frozen_routing_context']==authorization['frozen_routing_context']
            assert egress[0].get('access_selection')==expected_selection,'REGISTERED_PACKAGE_ACCESS_SELECTION_NOT_SAVED'
            assert type(egress[0]['access_selection']['schema_version']) is int
            assert set(forwarded)==set(carry),'ACCEPTED_SELECTION_INTERNAL_INPUTS_NOT_SUPPORTED'
            scope_payload={'mode':'daon_priority','source_version_ids':[version],'items':[item]}
            snapshot_payload={**scope_payload,'knowledge_scope_id':scope_id}
            for table,record,payload in (('knowledge_scopes',scope_id,scope_payload),('scope_snapshots',snapshot_id,snapshot_payload)):
                stored=connection.execute(f'SELECT version,created_by,trace_id,canonical_json,canonical_text,digest_sha256 FROM {table} '
                    'WHERE tenant_id=%s AND workspace_id=%s AND record_id=%s',(ctx.tenant_id,ctx.workspace_id,record)).fetchall()
                encoded=canonical_json_bytes(payload)
                assert stored==[(1,ctx.actor_id,ctx.trace_id,payload,encoded.decode('utf-8'),hashlib.sha256(encoded).hexdigest())]
            assert connection.execute('SELECT knowledge_scope_id FROM scope_snapshots WHERE tenant_id=%s AND workspace_id=%s AND record_id=%s',
                (ctx.tenant_id,ctx.workspace_id,snapshot_id)).fetchone()==(scope_id,)
            assert connection.execute("SELECT record_id,version_id,created_by FROM notebook_bindings WHERE tenant_id=%s AND workspace_id=%s "
                "AND notebook_id=%s AND binding_kind='knowledge_context'",(ctx.tenant_id,ctx.workspace_id,ctx.notebook_id)).fetchall()==[
                    (snapshot_id,None,ctx.actor_id)]
            assert connection.execute("SELECT to_jsonb(b) FROM notebook_bindings b WHERE tenant_id=%s AND workspace_id=%s "
                "AND notebook_id=%s AND binding_kind='source' ORDER BY record_id,version_id",(ctx.tenant_id,ctx.workspace_id,ctx.notebook_id)).fetchall()==raw_bindings
            activity=connection.execute('SELECT sequence,activity_kind,actor_id FROM notebook_activities '
                'WHERE tenant_id=%s AND workspace_id=%s AND notebook_id=%s ORDER BY sequence',
                (ctx.tenant_id,ctx.workspace_id,ctx.notebook_id)).fetchall()
            assert len(activity)==len(activities_before)+1
            assert activity[:-1]==activities_before and activity[-1][1:]==('context_bound',ctx.actor_id)
            atomic=connection.execute('SELECT r.xmin=e.xmin AND e.xmin=a.xmin AND a.xmin=k.xmin AND k.xmin=s.xmin '
                'AND s.xmin=b.xmin AND b.xmin=n.xmin FROM runs r JOIN egress_decisions e ON e.tenant_id=r.tenant_id '
                'AND e.workspace_id=r.workspace_id AND e.run_id=r.record_id AND e.record_id=%s '
                'JOIN audit_events a ON a.tenant_id=e.tenant_id AND a.workspace_id=e.workspace_id '
                "AND a.event_id=%s AND a.actor_id=r.created_by AND a.target_id=r.record_id AND a.action='question.egress.authorize' "
                "AND a.outcome='succeeded' AND a.trace_id=r.trace_id AND a.policy_version=%s "
                'JOIN scope_snapshots s ON s.tenant_id=r.tenant_id AND s.workspace_id=r.workspace_id AND s.record_id=%s '
                'JOIN knowledge_scopes k ON k.tenant_id=s.tenant_id AND k.workspace_id=s.workspace_id AND k.record_id=s.knowledge_scope_id '
                'JOIN notebook_bindings b ON b.tenant_id=s.tenant_id AND b.workspace_id=s.workspace_id AND b.record_id=s.record_id '
                "AND b.binding_kind='knowledge_context' AND b.notebook_id=%s JOIN notebook_activities n ON n.tenant_id=b.tenant_id "
                'AND n.workspace_id=b.workspace_id AND n.notebook_id=b.notebook_id AND n.sequence=%s '
                'WHERE r.tenant_id=%s AND r.workspace_id=%s AND r.record_id=%s',
                (authorization['egress_decision_id'],'audit-egress-'+hashlib.sha256(
                    (run+'|'+authorization['egress_decision_id']).encode()).hexdigest()[:32],ctx.policy_version,snapshot_id,ctx.notebook_id,
                 activity[-1][0],ctx.tenant_id,ctx.workspace_id,run)).fetchall()
            assert atomic==[(True,)],'ACCEPTED_RUN_SELECTION_BINDING_NOT_ATOMIC'
            assert immutable_rows(connection)==original_rows
            for table in ('run_results','run_snapshots','citations','model_attempts','notification_source_outbox','notifications'):
                assert connection.execute(f'SELECT count(*) FROM {table} WHERE tenant_id=%s',(ctx.tenant_id,)).fetchone()==(0,)
            egress_before=connection.execute('SELECT to_jsonb(e) FROM egress_decisions e WHERE tenant_id=%s ORDER BY record_id',(ctx.tenant_id,)).fetchall()
        before_replay=_l2b_completion_snapshot(scope)
        replay=authorize(replace(context,trace_id='s42-k-replay-'+scope.suffix))
        assert replay['provider_owner'] is False and replay['egress_decision_id']==authorization['egress_decision_id']
        assert _l2b_completion_snapshot(scope)==before_replay
        with psycopg.connect(scope.admin) as connection:
            assert immutable_rows(connection)==original_rows
            assert connection.execute('SELECT to_jsonb(e) FROM egress_decisions e WHERE tenant_id=%s ORDER BY record_id',(ctx.tenant_id,)).fetchall()==egress_before
        assert items[0].context_item_id==prepared.package and logical_body['knowledge_context']['resources']==expected_selection['resources']
        assert question_request_fingerprint(context,run_id=run,idempotency_key=key,request_payload=logical_body)==fingerprint
    finally:
        store.close()


def test_l2b_existing_0069_knowledge_registration_resolver_carries_exact_stored_registration_id():
    """실제 등록 Source/승인/원감사 저장 뒤 exact 등록 ID를 반환해야 한다. SQL 대역/외부 package 증거가 아니다."""
    from daon_user_api.cloud_storage import PostgresCloudStore
    from daon_user_api.knowledge_package import KnowledgePackageContext
    from daon_user_api.knowledge_package_postgres import PostgresKnowledgePackageService
    prepared=_l2b_registered_knowledge_case(registration_count=1)
    scope=prepared.scope
    ctx=scope.context
    class NoPackageIO:
        def read_package(self,*args,**kwargs):
            raise AssertionError('S42 등록 resolver는 외부 content를 읽지 않아야 한다')
    store=PostgresCloudStore(scope.app,min_size=0,max_size=2,notification_inbox_enabled=True)
    try:
        context=KnowledgePackageContext(ctx.tenant_id,ctx.workspace_id,ctx.actor_id,ctx.trace_id,ctx.policy_version,
            's42-registration-device-'+scope.suffix)
        service=PostgresKnowledgePackageService(store,NoPackageIO(),clock=lambda:prepared.now)
        before=_l2b_completion_snapshot(scope)
        registration,version,source=prepared.registrations[0][:3]
        try:
            packages=service.list_packages(context)
            assert len(packages)==1 and packages[0].knowledge_registration_id==registration
            resolved=service.resolve_question_sources(context,(prepared.package,))
            assert len(resolved)==1
            assert (resolved[0].package_id,resolved[0].source_id,resolved[0].source_version_id,resolved[0].digest_sha256)==(
                prepared.package,source,version,prepared.digest)
            assert getattr(resolved[0],'knowledge_registration_id',None)==registration,'EXACT_KNOWLEDGE_REGISTRATION_ID_NOT_CARRIED'
        finally:
            assert _l2b_completion_snapshot(scope)==before
    finally:
        store.close()


def _l2b_source_acl_control_rows(scope, denied_version, allowed_version):
    """본인 allow와 다른 실제 사용자의 deny를 별도 자료에 최초 INSERT한다. 기존 정책행은 수정하지 않는다."""
    import psycopg
    ctx=scope.context
    peer=_studio_case_member(scope,role='viewer')
    with psycopg.connect(scope.admin) as connection:
        connection.execute('INSERT INTO identity_auth_source_access '
            '(tenant_id,workspace_id,user_id,source_version_id,allowed,version,updated_at) '
            'VALUES(%s,%s,%s,%s,0,1,now()),(%s,%s,%s,%s,1,1,now())',
            (ctx.tenant_id,ctx.workspace_id,peer,denied_version,
             ctx.tenant_id,ctx.workspace_id,ctx.actor_id,allowed_version))
        assert connection.execute('SELECT user_id,source_version_id,allowed FROM identity_auth_source_access '
            'WHERE tenant_id=%s AND workspace_id=%s ORDER BY user_id,source_version_id',
            (ctx.tenant_id,ctx.workspace_id)).fetchall()==sorted([
                (peer,denied_version,0),(ctx.actor_id,allowed_version,1)])
    return peer


def _l2b_deny_current_source(scope, version):
    """해당 QA scope/사용자/자료의 명시 deny만 최초 저장한다."""
    import psycopg
    ctx=scope.context
    with psycopg.connect(scope.admin) as connection:
        assert connection.execute('INSERT INTO identity_auth_source_access '
            '(tenant_id,workspace_id,user_id,source_version_id,allowed,version,updated_at) '
            'VALUES(%s,%s,%s,%s,0,1,now()) RETURNING tenant_id,workspace_id,user_id,source_version_id,allowed',
            (ctx.tenant_id,ctx.workspace_id,ctx.actor_id,version)).fetchone()==(
                ctx.tenant_id,ctx.workspace_id,ctx.actor_id,version,0)


def test_l2b_existing_0069_source_acl_revalidate_rejects_current_actor_deny():
    """일반 등록 자료도 직접 재검사에서 본인 deny를 거부하며 행 없음/allow/다른 사용자 deny는 허용한다."""
    from daon_user_api.cloud_storage import PostgresCloudStore
    from daon_user_api.question_answering_postgres import PostgresQuestionAnsweringRepository,QuestionContext,QuestionRepositoryError
    prepared=_l2b_registered_knowledge_case(registration_count=2)
    scope=prepared.scope
    ctx=scope.context
    denied_version,allowed_version=(row[1] for row in prepared.registrations)
    context=QuestionContext(ctx.tenant_id,ctx.workspace_id,ctx.actor_id,ctx.trace_id,ctx.policy_version,ctx.notebook_id)
    store=PostgresCloudStore(scope.app,min_size=0,max_size=2,notification_inbox_enabled=True)
    try:
        repository=PostgresQuestionAnsweringRepository(store,_L2bNoObjectRead())
        before=_l2b_completion_snapshot(scope)
        assert repository.revalidate_open_law_sources(context,(denied_version,allowed_version)) is None
        assert _l2b_completion_snapshot(scope)==before
        _l2b_source_acl_control_rows(scope,denied_version,allowed_version)
        before=_l2b_completion_snapshot(scope)
        assert repository.revalidate_open_law_sources(context,(denied_version,allowed_version)) is None
        assert _l2b_completion_snapshot(scope)==before
        _l2b_deny_current_source(scope,denied_version)
        before=_l2b_completion_snapshot(scope)
        try:
            with pytest.raises(QuestionRepositoryError) as caught:
                repository.revalidate_open_law_sources(context,(allowed_version,denied_version))
            assert (caught.value.code,caught.value.status)==('QUESTION_SOURCE_UNAVAILABLE',404)
        finally:
            assert _l2b_completion_snapshot(scope)==before
    finally:
        store.close()


def test_l2b_existing_0069_source_acl_index_search_rejects_current_actor_deny():
    """실제 등록 index의 양성 검색 뒤 본인 deny는 텍스트 반환 없이 기존 안전 오류로 거부해야 한다."""
    from daon_user_api.cloud_storage import PostgresCloudStore
    from daon_user_api.document_index_postgres import PostgresDocumentIndex
    from daon_user_api.document_processing import DocumentProcessingContext
    from daon_user_api.document_understanding_adapter import DocumentUnderstandingError
    prepared=_l2b_registered_knowledge_case(registration_count=2)
    scope=prepared.scope
    ctx=scope.context
    denied_version,allowed_version=(row[1] for row in prepared.registrations)
    source_by_version={row[1]:row[2] for row in prepared.registrations}
    context=DocumentProcessingContext(ctx.tenant_id,ctx.workspace_id,ctx.actor_id,ctx.trace_id,ctx.policy_version)
    store=PostgresCloudStore(scope.app,min_size=0,max_size=2,notification_inbox_enabled=True)
    try:
        index=PostgresDocumentIndex(store)
        def search(version):
            return index.search(context,source_id=source_by_version[version],source_version_id=version,query='S42')
        before=_l2b_completion_snapshot(scope)
        normal={version:search(version) for version in (denied_version,allowed_version)}
        for version,chunks in normal.items():
            assert len(chunks)==1 and chunks[0].text and 'S42' in chunks[0].text
            assert (chunks[0].source_id,chunks[0].source_version_id)==(source_by_version[version],version)
        assert _l2b_completion_snapshot(scope)==before
        _l2b_source_acl_control_rows(scope,denied_version,allowed_version)
        before=_l2b_completion_snapshot(scope)
        assert search(denied_version)==normal[denied_version]
        assert search(allowed_version)==normal[allowed_version]
        assert _l2b_completion_snapshot(scope)==before
        _l2b_deny_current_source(scope,denied_version)
        before=_l2b_completion_snapshot(scope)
        exposed=()
        try:
            with pytest.raises(DocumentUnderstandingError) as caught:
                exposed=search(denied_version)
            assert (caught.value.code,caught.value.status)==('SOURCE_NOT_FOUND',404)
            assert exposed==()
        finally:
            assert _l2b_completion_snapshot(scope)==before
    finally:
        store.close()


def test_l2b_existing_0069_source_acl_persist_completed_rejects_current_actor_deny_atomically():
    """실제 완료 저장의 행 없음/allow 대조 후 본인 deny 자료의 새 Run은 부분 저장 없이 거부한다."""
    from daon_user_api.cloud_storage import PostgresCloudStore
    from daon_user_api.question_answering import GroundedTextResult,TextModelSelection
    from daon_user_api.question_answering_postgres import PostgresQuestionAnsweringRepository,QuestionContext,QuestionRepositoryError
    prepared=_l2b_registered_knowledge_case(registration_count=2)
    scope=prepared.scope
    ctx=scope.context
    denied_version,allowed_version=(row[1] for row in prepared.registrations)
    source_by_version={row[1]:row[2] for row in prepared.registrations}
    context=QuestionContext(ctx.tenant_id,ctx.workspace_id,ctx.actor_id,ctx.trace_id,ctx.policy_version,ctx.notebook_id)
    selection=TextModelSelection('OLLAMA','https://s42-model.invalid','s42-acl-profile-'+scope.suffix,
        's42-acl-deployment-'+scope.suffix,'s42-synthetic-model',1,provider_kind='server_internal')
    store=PostgresCloudStore(scope.app,min_size=0,max_size=2,notification_inbox_enabled=True)
    try:
        repository=PostgresQuestionAnsweringRepository(store,_L2bNoObjectRead())
        def complete(version,label):
            run='s42-acl-'+label+'-'+scope.suffix
            answer=repository.persist_completed(context,run_id=run,source_id=source_by_version[version],
                source_version_id=version,question='S42 합성 권한 경계 질문',selection=selection,
                evidence=(),result=GroundedTextResult('',(),True,{}),provider_called=False)
            assert answer.run_id==run and answer.insufficient is True and answer.citations==()
            return answer
        # 자료 ACL 행이 없는 실제 완료 저장도 유지한다. 모델/Provider 호출은 없다.
        complete(denied_version,'missing')
        _l2b_source_acl_control_rows(scope,denied_version,allowed_version)
        complete(denied_version,'peer-deny')
        complete(allowed_version,'allow')
        _l2b_deny_current_source(scope,denied_version)
        before=_l2b_completion_snapshot(scope)
        try:
            with pytest.raises(QuestionRepositoryError) as caught:
                complete(denied_version,'deny')
            assert (caught.value.code,caught.value.status)==('QUESTION_SOURCE_UNAVAILABLE',404)
        finally:
            assert _l2b_completion_snapshot(scope)==before
    finally:
        store.close()


@pytest.mark.parametrize('allowed',(1,0))
def test_l2b_existing_0069_source_acl_pdf_row_requires_current_access(allowed):
    """실제 PDF/Object 메타데이터·완료/index 양성 row를 만든 뒤 본인 ACL만 검사한다. Object I/O는 없다."""
    from dataclasses import replace
    import psycopg
    from daon_user_api.cloud_storage import PostgresCloudStore
    from daon_user_api.document_index_postgres import PostgresDocumentIndex
    from daon_user_api.document_processing import DocumentProcessingContext
    from daon_user_api.document_processing_postgres import PostgresDocumentProcessingRepository
    from daon_user_api.question_answering_postgres import PostgresQuestionAnsweringRepository,QuestionContext,QuestionRepositoryError
    from test_document_index_postgres import ready_result
    scope=_existing_0069_studio_case()
    ctx=scope.context
    store=PostgresCloudStore(scope.app,min_size=0,max_size=2,notification_inbox_enabled=True)
    try:
        source,version=_l2b_bound_pdf(scope,store)
        document_context=DocumentProcessingContext(ctx.tenant_id,ctx.workspace_id,ctx.actor_id,ctx.trace_id,ctx.policy_version)
        documents=PostgresDocumentProcessingRepository(store,_L2bNoObjectRead())
        run=documents.start(document_context,version,notebook_id=ctx.notebook_id)
        result=replace(ready_result(),source_id=source,source_version_id=version)
        documents.complete(document_context,run,result)
        assert PostgresDocumentIndex(store).index_result(document_context,result)
        repository=PostgresQuestionAnsweringRepository(store,_L2bNoObjectRead())
        context=QuestionContext(ctx.tenant_id,ctx.workspace_id,ctx.actor_id,ctx.trace_id,ctx.policy_version,ctx.notebook_id)
        before=_l2b_completion_snapshot(scope)
        normal=repository._row(context,source,version,'question.read',require_notebook_binding=True)
        assert normal[:4]==(source,version,'ready','S42 synthetic.pdf')
        assert normal[4] is not None and normal[8:]==('application/pdf','completed')
        assert _l2b_completion_snapshot(scope)==before
        with psycopg.connect(scope.admin) as connection:
            assert connection.execute('INSERT INTO identity_auth_source_access '
                '(tenant_id,workspace_id,user_id,source_version_id,allowed,version,updated_at) '
                'VALUES(%s,%s,%s,%s,%s,1,now()) RETURNING allowed',
                (ctx.tenant_id,ctx.workspace_id,ctx.actor_id,version,allowed)).fetchone()==(allowed,)
        before=_l2b_completion_snapshot(scope)
        try:
            if allowed:
                assert repository._row(context,source,version,'question.read',require_notebook_binding=True)==normal
            else:
                with pytest.raises(QuestionRepositoryError) as caught:
                    repository._row(context,source,version,'question.read',require_notebook_binding=True)
                assert (caught.value.code,caught.value.status)==('QUESTION_SOURCE_UNAVAILABLE',404)
        finally:
            assert _l2b_completion_snapshot(scope)==before
    finally:
        store.close()


def test_l2b_existing_0069_registered_source_loader_rejects_current_actor_acl_deny():
    """resolver와 무관하게 실제 QuestionRepository의 현재 SourceACL 거부를 직접 요구한다."""
    import psycopg
    from daon_user_api.cloud_storage import PostgresCloudStore
    from daon_user_api.question_answering_postgres import PostgresQuestionAnsweringRepository,QuestionContext,QuestionRepositoryError
    prepared=_l2b_registered_knowledge_case(registration_count=1)
    scope=prepared.scope
    ctx=scope.context
    registration,version,source=prepared.registrations[0][:3]
    assert registration and prepared.registrations[0][3:6]==('registered','approved','ready')
    context=QuestionContext(ctx.tenant_id,ctx.workspace_id,ctx.actor_id,ctx.trace_id,ctx.policy_version,ctx.notebook_id)
    store=PostgresCloudStore(scope.app,min_size=0,max_size=2,notification_inbox_enabled=True)
    try:
        repository=PostgresQuestionAnsweringRepository(store,_L2bNoObjectRead())
        normal_before=_l2b_completion_snapshot(scope)
        normal=repository.load_ready_source(context,source,version)
        assert (normal.source_id,normal.source_version_id)==(source,version)
        assert _l2b_completion_snapshot(scope)==normal_before
        with psycopg.connect(scope.admin) as connection:
            assert connection.execute('INSERT INTO identity_auth_source_access '
                '(tenant_id,workspace_id,user_id,source_version_id,allowed,version,updated_at) '
                'VALUES(%s,%s,%s,%s,0,1,now()) RETURNING user_id,source_version_id,allowed',
                (ctx.tenant_id,ctx.workspace_id,ctx.actor_id,version)).fetchone()==(ctx.actor_id,version,0)
        before=_l2b_completion_snapshot(scope)
        try:
            with pytest.raises(QuestionRepositoryError) as caught:
                repository.load_ready_source(context,source,version)
            assert (caught.value.code,caught.value.status)==('QUESTION_SOURCE_UNAVAILABLE',404)
        finally:
            assert _l2b_completion_snapshot(scope)==before
            with psycopg.connect(scope.admin) as connection:
                assert connection.execute('SELECT allowed,version FROM identity_auth_source_access WHERE tenant_id=%s '
                    'AND workspace_id=%s AND user_id=%s AND source_version_id=%s',
                    (ctx.tenant_id,ctx.workspace_id,ctx.actor_id,version)).fetchone()==(0,1)
    finally:
        store.close()


@pytest.mark.parametrize('registration_count',(1,2))
def test_l2b_existing_0069_knowledge_registration_metadata_does_not_preempt_current_source_ready_filter(registration_count):
    """등록 적격 metadata 조회와 실제 자료 사용 거부를 분리한다. ACL deny를 자료 사용 허용으로 바꾸지 않는다."""
    import psycopg
    from daon_user_api.cloud_storage import PostgresCloudStore
    from daon_user_api.knowledge_package import KnowledgePackageContext,KnowledgePackageError
    from daon_user_api.knowledge_package_postgres import PostgresKnowledgePackageService
    from daon_user_api.question_answering_postgres import PostgresQuestionAnsweringRepository,QuestionContext,QuestionRepositoryError
    prepared=_l2b_registered_knowledge_case(registration_count=registration_count)
    scope=prepared.scope
    ctx=scope.context
    registration,version,source=prepared.registrations[0][:3]
    class NoPackageIO:
        def read_package(self,*args,**kwargs):
            raise AssertionError('S42 등록 resolver는 외부 content를 읽지 않아야 한다')
    store=PostgresCloudStore(scope.app,min_size=0,max_size=2,notification_inbox_enabled=True)
    try:
        context=KnowledgePackageContext(ctx.tenant_id,ctx.workspace_id,ctx.actor_id,ctx.trace_id,ctx.policy_version,
            's42-registration-device-'+scope.suffix)
        question_context=QuestionContext(ctx.tenant_id,ctx.workspace_id,ctx.actor_id,ctx.trace_id,ctx.policy_version,ctx.notebook_id)
        repository=PostgresQuestionAnsweringRepository(store,_L2bNoObjectRead())
        normal=repository.load_ready_source(question_context,source,version)
        assert (normal.source_id,normal.source_version_id)==(source,version)
        with psycopg.connect(scope.admin) as connection:
            assert connection.execute('INSERT INTO identity_auth_source_access '
                '(tenant_id,workspace_id,user_id,source_version_id,allowed,version,updated_at) '
                'VALUES(%s,%s,%s,%s,0,1,now()) RETURNING allowed',
                (ctx.tenant_id,ctx.workspace_id,ctx.actor_id,version)).fetchone()==(0,)
        before=_l2b_completion_snapshot(scope)
        try:
            service=PostgresKnowledgePackageService(store,NoPackageIO(),clock=lambda:prepared.now)
            eligible=service.list_packages(context)
            assert len(eligible)==registration_count
            assert {item.knowledge_registration_id for item in eligible}=={row[0] for row in prepared.registrations}
            # 양쪽 결과를 각각 관측한다. resolver의 선행 404를 loader 권한 거부 증거로 오인하지 않는다.
            loader_denial=None
            try:
                repository.load_ready_source(question_context,source,version)
            except QuestionRepositoryError as error:
                loader_denial=(error.code,error.status)
            resolver_denial=None
            resolved=()
            try:
                resolved=service.resolve_question_sources(context,(prepared.package,))
            except KnowledgePackageError as error:
                resolver_denial=(error.code,error.status)
            if registration_count==1:
                assert resolver_denial is None,('REGISTERED_METADATA_PREEMPTED_READY_FILTER',resolver_denial,
                    'ACTUAL_LOADER_DENIAL',loader_denial)
                assert len(resolved)==1
                assert (resolved[0].package_id,resolved[0].source_id,resolved[0].source_version_id,
                    resolved[0].digest_sha256,getattr(resolved[0],'knowledge_registration_id',None))==(
                        prepared.package,source,version,prepared.digest,registration)
                assert loader_denial==('QUESTION_SOURCE_UNAVAILABLE',404),'CURRENT_SOURCE_ACL_DENIAL_NOT_ENFORCED_BY_LOADER'
            else:
                # ACL 가시성으로 적격 등록 하나를 숨겨 복수 등록을 단일 등록처럼 선택하지 않는다.
                assert resolver_denial==('KNOWLEDGE_PACKAGE_UNAVAILABLE',404),'SOURCE_ACL_FILTER_HID_AMBIGUOUS_REGISTRATION'
                assert resolved==()
        finally:
            assert _l2b_completion_snapshot(scope)==before
            with psycopg.connect(scope.admin) as connection:
                assert connection.execute('SELECT allowed,version FROM identity_auth_source_access WHERE tenant_id=%s '
                    'AND workspace_id=%s AND user_id=%s AND source_version_id=%s',
                    (ctx.tenant_id,ctx.workspace_id,ctx.actor_id,version)).fetchone()==(0,1)
    finally:
        store.close()


def test_l2b_existing_0069_knowledge_registration_resolver_rejects_ambiguous_eligible_package():
    """동일 package를 정상 owner 등록 두 번으로 만든다. 마지막 dict 항목을 임의 선택하면 안 된다."""
    from daon_user_api.cloud_storage import PostgresCloudStore
    from daon_user_api.knowledge_package import KnowledgePackageContext,KnowledgePackageError
    from daon_user_api.knowledge_package_postgres import PostgresKnowledgePackageService
    prepared=_l2b_registered_knowledge_case(registration_count=2)
    scope=prepared.scope
    ctx=scope.context
    class NoPackageIO:
        def read_package(self,*args,**kwargs):
            raise AssertionError('S42 등록 resolver는 외부 content를 읽지 않아야 한다')
    store=PostgresCloudStore(scope.app,min_size=0,max_size=2,notification_inbox_enabled=True)
    try:
        context=KnowledgePackageContext(ctx.tenant_id,ctx.workspace_id,ctx.actor_id,ctx.trace_id,ctx.policy_version,
            's42-registration-device-'+scope.suffix)
        service=PostgresKnowledgePackageService(store,NoPackageIO(),clock=lambda:prepared.now)
        before=_l2b_completion_snapshot(scope)
        try:
            eligible=service.list_packages(context)
            assert len(eligible)==2 and {item.package_id for item in eligible}=={prepared.package}
            assert {item.knowledge_registration_id for item in eligible}=={row[0] for row in prepared.registrations}
            assert len({item.knowledge_registration_id for item in eligible})==2
            with pytest.raises(KnowledgePackageError) as caught:
                service.resolve_question_sources(context,(prepared.package,))
            assert (caught.value.code,caught.value.status)==('KNOWLEDGE_PACKAGE_UNAVAILABLE',404)
        finally:
            assert _l2b_completion_snapshot(scope)==before
    finally:
        store.close()


def test_l2b_existing_0069_question_raw_filtered_failure_preserves_original_selection_and_used_snapshot():
    """실제 registered 첫 선택/ready 둘째 선택으로 accepted를 만들고 used만 실패 snapshot에 저장한다."""
    import psycopg
    prepared=_l2b_question_failed_case(http_resource_count=2,filtered_first_unavailable=True,return_case=True)
    assert len(prepared.resolved_items)==2 and len(prepared.items)==1
    assert prepared.items==(prepared.resolved_items[1],)
    assert prepared.request_payload=={'notebook_id':prepared.context.notebook_id,
        'question':prepared.accepted[4]['question'],'knowledge_context':{'mode':'raw_only','resources':[
            {'resource_kind':'source','resource_id':item.source_id,'version_id':item.source_version_id}
            for item in prepared.resolved_items]}}
    assert (prepared.accepted[4]['source_id'],prepared.accepted[4]['source_version_id'])==(
        prepared.items[0].source_id,prepared.items[0].source_version_id)
    assert prepared.request_payload['knowledge_context']['resources'][0]['version_id']!=prepared.accepted[4]['source_version_id']
    with psycopg.connect(prepared.scope.admin) as connection:
        assert connection.execute('SELECT state,version FROM sources WHERE tenant_id=%s AND workspace_id=%s AND record_id=%s',
            (prepared.context.tenant_id,prepared.context.workspace_id,prepared.unavailable[0])).fetchone()==prepared.unavailable[2:]


def test_l2b_existing_0069_question_raw_filtered_replay_and_materialize_reopen_without_duplicates():
    """실제 registered 첫 선택을 제외한 failed 원장을 새 trace로 읽고 used snapshot만 소비한다."""
    from dataclasses import replace
    import psycopg
    from daon_user_api.cloud_storage import PostgresCloudStore
    from daon_user_api.question_answering_postgres import PostgresQuestionAnsweringRepository
    prepared=_l2b_question_failed_case(http_resource_count=2,filtered_first_unavailable=True,return_case=True)
    scope=prepared.scope
    assert len(prepared.resolved_items)==2 and prepared.items==(prepared.resolved_items[1],)
    assert (prepared.accepted[4]['source_id'],prepared.accepted[4]['source_version_id'])==(
        prepared.items[0].source_id,prepared.items[0].source_version_id)
    read_context=replace(prepared.context,trace_id='s42-filtered-replay-'+scope.suffix)
    assert read_context.trace_id!=prepared.context.trace_id
    before=_l2b_completion_snapshot(scope)
    store=PostgresCloudStore(scope.app,min_size=0,max_size=2,notification_inbox_enabled=True)
    try:
        try:
            assert PostgresQuestionAnsweringRepository(store,_L2bNoObjectRead()).load_failed_for_replay(
                read_context,prepared.run,prepared.fingerprint) is True
        finally:
            assert _l2b_completion_snapshot(scope)==before
    finally:
        store.close()
    _l2b_assert_failed_question_materialized(prepared)
    with psycopg.connect(scope.admin) as connection:
        assert connection.execute('SELECT state,version FROM sources WHERE tenant_id=%s AND workspace_id=%s AND record_id=%s',
            (prepared.context.tenant_id,prepared.context.workspace_id,prepared.unavailable[0])).fetchone()==prepared.unavailable[2:]


@pytest.mark.parametrize('case',('resolved_missing','used_duplicate','used_foreign','used_reversed','used_empty'))
def test_l2b_existing_0069_question_raw_filtered_invalid_used_selection_never_writes(case):
    """정상 ready2 accepted 원장에서도 partial 원선택 부재/잘못된 used는 저장 전 거부하며 전체 원장은 불변이다."""
    import psycopg
    from daon_user_api.cloud_storage import PostgresCloudStore
    from daon_user_api.question_answering_service import QuestionInputSource
    from daon_user_api.question_answering_postgres import PostgresQuestionAnsweringRepository,QuestionRepositoryError
    prepared=_l2b_question_failed_case(http_resource_count=2,prepare_only=True)
    scope=prepared.scope
    ctx=scope.context
    assert len(prepared.items)==2 and prepared.resolved_items==prepared.items
    first,second=prepared.items
    assert (prepared.accepted[4]['source_id'],prepared.accepted[4]['source_version_id'])==(
        first.source_id,first.source_version_id)
    used=prepared.items
    resolved=prepared.resolved_items
    if case=='resolved_missing':
        used=(first,)
        resolved=None
    elif case=='used_duplicate':
        used=(first,first)
    elif case=='used_foreign':
        # 같은 실제 Notebook의 기존 다른 자료를 사용한다. 원 body에 없는 원소라는 이유로 거부해야 한다.
        with psycopg.connect(scope.admin) as connection:
            other=connection.execute('SELECT sv.source_id,sv.record_id FROM source_versions sv JOIN notebook_bindings b '
                'ON b.tenant_id=sv.tenant_id AND b.workspace_id=sv.workspace_id AND b.record_id=sv.source_id '
                "AND b.version_id=sv.record_id AND b.binding_kind='source' WHERE sv.tenant_id=%s AND sv.workspace_id=%s "
                'AND sv.record_id=%s AND b.notebook_id=%s',
                (ctx.tenant_id,ctx.workspace_id,scope.source_version_id,ctx.notebook_id)).fetchone()
            assert other is not None and other[1] not in {first.source_version_id,second.source_version_id}
        used=(first,QuestionInputSource('raw_source',other[0],other[0],other[1]))
    elif case=='used_reversed':
        used=(second,first)
    else:
        assert case=='used_empty'
        used=()
    before=_l2b_completion_snapshot(scope)
    store=PostgresCloudStore(scope.app,min_size=0,max_size=2,notification_inbox_enabled=True)
    try:
        try:
            with pytest.raises(QuestionRepositoryError) as caught:
                PostgresQuestionAnsweringRepository(store,_L2bNoObjectRead()).persist_failed(
                    prepared.context,run_id=prepared.run,request_fingerprint=prepared.fingerprint,
                    request_payload=prepared.request_payload,idempotency_key=prepared.run,context_sources=used,
                    selection=prepared.selection,egress_authorization=prepared.authorization,
                    question=prepared.accepted[4]['question'],
                    **({'resolved_context_sources':resolved} if resolved is not None else {}))
            assert (caught.value.code,caught.value.status)==('QUESTION_RESULT_INVALID',400),case
        finally:
            assert _l2b_completion_snapshot(scope)==before,case
        with psycopg.connect(scope.admin) as connection:
            assert connection.execute('SELECT state,version,created_by,conversation_id,canonical_json FROM runs '
                'WHERE tenant_id=%s AND workspace_id=%s AND record_id=%s',
                (ctx.tenant_id,ctx.workspace_id,prepared.run)).fetchone()==prepared.accepted
    finally:
        store.close()


def test_l2b_existing_0069_question_raw_resources_replay_and_materialize_preserve_order_without_duplicates():
    """실제 복수 자료 failed를 새 연결로 읽고 기존 강한 소비/시각/재조회 단언을 그대로 적용한다."""
    from dataclasses import replace
    from daon_user_api.cloud_storage import PostgresCloudStore
    from daon_user_api.question_answering_postgres import PostgresQuestionAnsweringRepository
    prepared=_l2b_question_failed_case(http_resource_count=2,return_case=True)
    scope=prepared.scope
    read_context=replace(prepared.context,trace_id='s42-resources-replay-'+scope.suffix)
    assert read_context.trace_id!=prepared.context.trace_id and len(prepared.items)==2
    before=_l2b_completion_snapshot(scope)
    store=PostgresCloudStore(scope.app,min_size=0,max_size=2,notification_inbox_enabled=True)
    try:
        assert PostgresQuestionAnsweringRepository(store,_L2bNoObjectRead()).load_failed_for_replay(
            read_context,prepared.run,prepared.fingerprint) is True
        assert _l2b_completion_snapshot(scope)==before
    finally:
        store.close()
    _l2b_assert_failed_question_materialized(prepared)


def test_l2b_existing_0069_question_raw_resources_nonprimary_acl_denies_replay_and_materialization():
    """첫 자료의 접근은 남아도 두 번째 실제 Source ACL 철회는 SQL/Python 양쪽 발행을 막는다."""
    from dataclasses import replace
    import psycopg
    import daon_user_api.notification_events as events
    from daon_user_api.authorization import Action
    from daon_user_api.cloud_storage import PostgresCloudStore
    from daon_user_api.open_law_source_access import _current_auth_grant
    from daon_user_api.question_answering_postgres import PostgresQuestionAnsweringRepository,QuestionRepositoryError
    prepared=_l2b_question_failed_case(http_resource_count=2,return_case=True)
    scope=prepared.scope
    ctx=scope.context
    primary,secondary=prepared.items
    assert (primary.source_id,primary.source_version_id)==(prepared.source,prepared.version)
    assert secondary.source_version_id!=primary.source_version_id
    with psycopg.connect(scope.admin) as connection:
        assert connection.execute('INSERT INTO identity_auth_source_access '
            '(tenant_id,workspace_id,user_id,source_version_id,allowed,version,updated_at) '
            'VALUES(%s,%s,%s,%s,0,1,now()) RETURNING source_version_id,allowed',
            (ctx.tenant_id,ctx.workspace_id,ctx.actor_id,secondary.source_version_id)).fetchone()==(
                secondary.source_version_id,0)
        assert connection.execute('SELECT count(*) FROM identity_auth_source_access WHERE tenant_id=%s '
            'AND workspace_id=%s AND user_id=%s AND source_version_id=%s AND allowed=0',
            (ctx.tenant_id,ctx.workspace_id,ctx.actor_id,primary.source_version_id)).fetchone()==(0,)
        source_before=connection.execute('SELECT to_jsonb(s) FROM notification_source_outbox s '
            'WHERE tenant_id=%s AND workspace_id=%s AND target_id=%s',
            (ctx.tenant_id,ctx.workspace_id,prepared.run)).fetchall()
        assert len(source_before)==1
        source_before=source_before[0][0]
        assert (source_before['state'],source_before['version'])==('pending',1)
    before=_l2b_completion_snapshot(scope)
    store=PostgresCloudStore(scope.app,min_size=0,max_size=2,notification_inbox_enabled=True)
    try:
        try:
            with pytest.raises(QuestionRepositoryError) as caught:
                PostgresQuestionAnsweringRepository(store,_L2bNoObjectRead()).load_failed_for_replay(
                    replace(prepared.context,trace_id='s42-resources-acl-'+scope.suffix),prepared.run,prepared.fingerprint)
            assert (caught.value.code,caught.value.status)==('QUESTION_SOURCE_UNAVAILABLE',404)
        finally:
            assert _l2b_completion_snapshot(scope)==before
    finally:
        store.close()
    with psycopg.connect(scope.app,connect_timeout=3) as connection:
        with connection.transaction(force_rollback=True):
            for name,value in (('tenant_id',ctx.tenant_id),('workspace_id',ctx.workspace_id),
                ('actor_id',ctx.actor_id),('capability','view')):
                connection.execute('SELECT set_config(%s,%s,true)',('app.'+name,value))
            assert connection.execute('SELECT current_user').fetchone()==('daon_app',)
            assert _current_auth_grant(prepared.context,connection,Action.VIEW).allowed is True
            assert _current_auth_grant(prepared.context,connection,Action.QUERY).allowed is True
            assert connection.execute('SELECT public.materialize_completed_notification(%s,true)',
                (source_before['source_event_id'],)).fetchone()==({'state':'suppressed'},)
    assert _l2b_completion_snapshot(scope)==before
    assert events.materialize_pending_completed(scope.app,context=ctx,limit=100)==0
    after=_l2b_completion_snapshot(scope)
    assert {key:value for key,value in after.items() if key!='notification_source_outbox'}=={
        key:value for key,value in before.items() if key!='notification_source_outbox'}
    with psycopg.connect(scope.admin) as connection:
        source_after=connection.execute('SELECT to_jsonb(s) FROM notification_source_outbox s '
            'WHERE tenant_id=%s AND workspace_id=%s AND source_event_id=%s',
            (ctx.tenant_id,ctx.workspace_id,source_before['source_event_id'])).fetchone()[0]
        assert (source_after['state'],source_after['version'])==('suppressed',2) and source_after['delivered_at'] is not None
        assert {key:value for key,value in source_after.items() if key not in {'state','version','delivered_at'}}=={
            key:value for key,value in source_before.items() if key not in {'state','version','delivered_at'}}
        for table in ('notifications','notification_details','notification_audit_outbox','security_audit_events'):
            assert connection.execute(f'SELECT count(*) FROM {table} WHERE tenant_id=%s',(ctx.tenant_id,)).fetchone()==(0,)
    assert events.materialize_pending_completed(scope.app,context=ctx,limit=100)==0
    assert _l2b_completion_snapshot(scope)==after


def test_l2b_existing_0069_question_raw_resources_reject_accessible_replacement_of_original_selection():
    """현재 접근되는 별도 자료라도 원 body/fp/resolved/used 선택을 바꿀 수 없으며 전체 원장은 불변이다."""
    import psycopg
    from daon_user_api.authorization import Action
    from daon_user_api.cloud_storage import PostgresCloudStore,CloudAccessContext
    from daon_user_api.open_law_source_access import _current_auth_grant
    from daon_user_api.question_answering_service import QuestionInputSource
    from daon_user_api.question_answering_postgres import PostgresQuestionAnsweringRepository,QuestionRepositoryError
    from daon_user_api.studio_workspace_postgres import PostgresStudioWorkspaceRepository as Canon
    prepared=_l2b_question_failed_case(http_resource_count=2,prepare_only=True)
    scope=prepared.scope
    ctx=scope.context
    store=PostgresCloudStore(scope.app,min_size=0,max_size=2,notification_inbox_enabled=True)
    try:
        # 기존 seed의 다른 실제 결속 자료를 기존 Canon 전이로 ready 처리한다. 가상 Source/권한은 만들지 않는다.
        with store._transaction(CloudAccessContext(ctx.tenant_id,ctx.workspace_id,ctx.actor_id,'source.create')) as connection:
            other=connection.execute('SELECT sv.source_id,sv.record_id,s.state,s.version FROM source_versions sv '
                'JOIN sources s ON s.tenant_id=sv.tenant_id AND s.workspace_id=sv.workspace_id AND s.record_id=sv.source_id '
                'JOIN notebook_bindings b ON b.tenant_id=sv.tenant_id AND b.workspace_id=sv.workspace_id '
                "AND b.binding_kind='source' AND b.record_id=sv.source_id AND b.version_id=sv.record_id "
                'WHERE sv.tenant_id=%s AND sv.workspace_id=%s AND sv.record_id=%s AND b.notebook_id=%s',
                (ctx.tenant_id,ctx.workspace_id,scope.source_version_id,ctx.notebook_id)).fetchone()
            assert other is not None and other[2]=='registered'
            assert other[1] not in {item.source_version_id for item in prepared.items}
            current_version=other[3]
            for state in ('security_check','processing','indexing','ready'):
                current_version=Canon._transition(connection,ctx,'Source',other[0],current_version,state,
                    's42-resources-replacement-'+state+'-'+scope.suffix)
            assert _current_auth_grant(prepared.context,connection,Action.QUERY).allowed is True
            assert connection.execute('SELECT 1 FROM sources s JOIN source_versions sv ON sv.tenant_id=s.tenant_id '
                'AND sv.workspace_id=s.workspace_id AND sv.source_id=s.record_id '
                "WHERE s.tenant_id=%s AND s.workspace_id=%s AND s.record_id=%s AND sv.record_id=%s AND s.state='ready' "
                'AND NOT EXISTS(SELECT 1 FROM identity_auth_source_access a WHERE a.tenant_id=s.tenant_id '
                'AND a.workspace_id=s.workspace_id AND a.user_id=%s AND a.source_version_id=sv.record_id AND a.allowed=0)',
                (ctx.tenant_id,ctx.workspace_id,other[0],other[1],ctx.actor_id)).fetchone()==(1,)
        changed_items=(prepared.items[0],QuestionInputSource('raw_source',other[0],other[0],other[1]))
        changed_body={'notebook_id':ctx.notebook_id,'question':prepared.request_payload['question'],
            'knowledge_context':{'mode':'raw_only','resources':[
                {'resource_kind':'source','resource_id':item.source_id,'version_id':item.source_version_id}
                for item in changed_items]}}
        before=_l2b_completion_snapshot(scope)
        repository=PostgresQuestionAnsweringRepository(store,_L2bNoObjectRead())
        cases=(('used_items',prepared.request_payload,changed_items,prepared.items,'QUESTION_RESULT_INVALID',400),
            ('resolved_items',prepared.request_payload,prepared.items,changed_items,'QUESTION_RESULT_INVALID',400),
            ('body_and_items',changed_body,changed_items,changed_items,'IDEMPOTENCY_KEY_REUSED',409))
        observed=[]
        for name,payload,used,resolved,code,status in cases:
            try:
                with pytest.raises(QuestionRepositoryError) as caught:
                    repository.persist_failed(prepared.context,run_id=prepared.run,request_fingerprint=prepared.fingerprint,
                        context_sources=used,resolved_context_sources=resolved,selection=prepared.selection,
                        egress_authorization=prepared.authorization,request_payload=payload,idempotency_key=prepared.run,
                        question=prepared.accepted[4]['question'])
                assert (caught.value.code,caught.value.status)==(code,status),name
                observed.append(name)
            finally:
                assert _l2b_completion_snapshot(scope)==before,name
        assert observed==['used_items','resolved_items','body_and_items']
        with psycopg.connect(scope.admin) as connection:
            assert connection.execute('SELECT state,version,created_by,conversation_id,canonical_json FROM runs '
                'WHERE tenant_id=%s AND workspace_id=%s AND record_id=%s',
                (ctx.tenant_id,ctx.workspace_id,prepared.run)).fetchone()==prepared.accepted
    finally:
        store.close()


def test_l2b_existing_0069_question_failed_replay_reopens_with_new_trace_without_writes():
    """실제 failed 전이/receipt/원감사/snapshot/source를 모두 검증한 뒤 새 연결의 읽기만 요구한다."""
    from dataclasses import replace
    import psycopg
    from daon_user_api.cloud_storage import PostgresCloudStore
    from daon_user_api.question_answering_postgres import PostgresQuestionAnsweringRepository
    prepared=_l2b_question_failed_case(return_case=True)
    scope=prepared.scope
    ctx=scope.context
    replay_context=replace(prepared.context,trace_id='s42-failed-replay-read-'+scope.suffix)
    assert replay_context.trace_id!=prepared.context.trace_id
    # 준비 helper의 store/원업무 연결은 이미 닫혔다. 과거 사건에 현재 xmin/원 요청 trace를 요구하지 않는다.
    store=PostgresCloudStore(scope.app,min_size=0,max_size=2,notification_inbox_enabled=True)
    try:
        before=_l2b_completion_snapshot(scope)
        with psycopg.connect(scope.admin) as connection:
            assert connection.execute('SELECT state,version,created_by,conversation_id,canonical_json FROM runs '
                'WHERE tenant_id=%s AND workspace_id=%s AND record_id=%s',
                (ctx.tenant_id,ctx.workspace_id,prepared.run)).fetchone()==(
                    'failed',prepared.accepted[1]+1,ctx.actor_id,prepared.accepted[3],prepared.accepted[4])
        try:
            repository=PostgresQuestionAnsweringRepository(store,_L2bNoObjectRead())
            assert repository.load_failed_for_replay(replay_context,prepared.run,prepared.fingerprint) is True
        finally:
            assert _l2b_completion_snapshot(scope)==before
    finally:
        store.close()


@pytest.mark.parametrize('case',('fingerprint','source_acl','sql_failure'))
def test_l2b_existing_0069_question_failed_replay_fp_acl_sql_errors_never_write_or_return_false(case):
    """실제 확정 실패의 fp/현재 ACL 거부와 실제 읽기 SQL 장애는 False/재실행으로 숨기지 않는다."""
    from dataclasses import replace
    from unittest.mock import patch
    import psycopg
    from daon_user_api.cloud_storage import PostgresCloudStore
    from daon_user_api.question_answering_postgres import PostgresQuestionAnsweringRepository,QuestionRepositoryError
    prepared=_l2b_question_failed_case(return_case=True)
    scope=prepared.scope
    ctx=scope.context
    read_context=replace(prepared.context,trace_id='s42-failed-replay-negative-'+scope.suffix)
    store=PostgresCloudStore(scope.app,min_size=0,max_size=2,notification_inbox_enabled=True)
    try:
        repository=PostgresQuestionAnsweringRepository(store,_L2bNoObjectRead())
        normal_before=_l2b_completion_snapshot(scope)
        assert repository.load_failed_for_replay(read_context,prepared.run,prepared.fingerprint) is True
        assert _l2b_completion_snapshot(scope)==normal_before
        fingerprint=prepared.fingerprint
        expected=('IDEMPOTENCY_KEY_REUSED',409)
        if case=='fingerprint':
            fingerprint='sha256:'+'0'*64
            if fingerprint==prepared.fingerprint:
                fingerprint='sha256:'+'1'*64
        elif case=='source_acl':
            with psycopg.connect(scope.admin) as connection:
                assert connection.execute('INSERT INTO identity_auth_source_access '
                    '(tenant_id,workspace_id,user_id,source_version_id,allowed,version,updated_at) '
                    'VALUES(%s,%s,%s,%s,0,1,now()) RETURNING allowed',
                    (ctx.tenant_id,ctx.workspace_id,ctx.actor_id,prepared.version)).fetchone()==(0,)
            expected=('QUESTION_SOURCE_UNAVAILABLE',404)
        else:
            expected=('QUESTION_DATABASE_UNAVAILABLE',503)
        before=_l2b_completion_snapshot(scope)
        phases=[]
        faults=[]
        real_transaction=store._transaction

        @contextmanager
        def observed_transaction(context):
            # 실제 pool/transaction의 예외 변환을 유지하고 같은 connection에서 읽기 SQL만 실패시킨다.
            with real_transaction(context) as actual:
                class ObservedConnection:
                    def __getattr__(self,name):
                        return getattr(actual,name)
                    def execute(self,query,params=None):
                        cursor=actual.execute(query,params)
                        if query.startswith('SELECT state FROM identity_users '):
                            assert params==(ctx.actor_id,)
                            phases.append('ACTUAL_FAILED_REPLAY_CURRENT_QUERY_ACCESS_READ')
                            try:
                                actual.execute('SELECT 1/0')
                            except psycopg.Error as error:
                                faults.append(error.sqlstate)
                                raise
                        return cursor
                yield ObservedConnection()

        try:
            with pytest.raises(QuestionRepositoryError) as caught:
                if case=='sql_failure':
                    with patch.object(store,'_transaction',new=observed_transaction):
                        repository.load_failed_for_replay(read_context,prepared.run,fingerprint)
                else:
                    repository.load_failed_for_replay(read_context,prepared.run,fingerprint)
        finally:
            assert _l2b_completion_snapshot(scope)==before
        assert (caught.value.code,caught.value.status)==expected
        if case=='sql_failure':
            assert phases==['ACTUAL_FAILED_REPLAY_CURRENT_QUERY_ACCESS_READ'] and faults==['22012']
        with psycopg.connect(scope.admin) as connection:
            assert connection.execute('SELECT state,version,created_by,conversation_id,canonical_json FROM runs '
                'WHERE tenant_id=%s AND workspace_id=%s AND record_id=%s',
                (ctx.tenant_id,ctx.workspace_id,prepared.run)).fetchone()==(
                    'failed',prepared.accepted[1]+1,ctx.actor_id,prepared.accepted[3],prepared.accepted[4])
    finally:
        store.close()


@pytest.mark.parametrize('case',('query_revoked','source_unbound'))
def test_l2b_existing_0069_question_failed_replay_query_revocation_and_unbind_never_write_or_return_false(case):
    """현재 VIEW만 남거나 실제 Source가 해제되면 확정 실패 읽기도 거부하고 원장은 보존한다."""
    from dataclasses import replace
    from datetime import datetime,timezone
    import psycopg
    from daon_user_api.authorization import Action
    from daon_user_api.cloud_storage import PostgresCloudStore
    from daon_user_api.notebook import NotebookContext
    from daon_user_api.notebook_postgres import PostgresNotebookRepository
    from daon_user_api.open_law_source_access import _current_auth_grant,OpenLawAccessError
    from daon_user_api.question_answering_postgres import PostgresQuestionAnsweringRepository,QuestionRepositoryError
    prepared=_l2b_question_failed_case(return_case=True)
    scope=prepared.scope
    ctx=scope.context
    read_context=replace(prepared.context,trace_id='s42-failed-replay-access-'+scope.suffix)
    store=PostgresCloudStore(scope.app,min_size=0,max_size=2,notification_inbox_enabled=True)
    try:
        repository=PostgresQuestionAnsweringRepository(store,_L2bNoObjectRead())
        assert repository.load_failed_for_replay(read_context,prepared.run,prepared.fingerprint) is True
        if case=='query_revoked':
            with psycopg.connect(scope.admin) as connection:
                assert connection.execute('SELECT role,state FROM identity_auth_tenant_roles '
                    'WHERE tenant_id=%s AND user_id=%s',(ctx.tenant_id,ctx.actor_id)).fetchone()==('personal_owner','active')
                assert connection.execute('INSERT INTO identity_auth_memberships '
                    '(tenant_id,workspace_id,user_id,role,state,version,updated_at) '
                    "VALUES(%s,%s,%s,'viewer','active',1,now()) RETURNING role,state",
                    (ctx.tenant_id,ctx.workspace_id,ctx.actor_id)).fetchone()==('viewer','active')
            # Workspace viewer만으로 QUERY가 사라진다고 가정하지 않는다. 활성 tenant 역할의 우선 판정을 먼저 확인한다.
            with store._transaction(repository._cloud_context(read_context,'question.read')) as connection:
                for action in (Action.VIEW,Action.QUERY):
                    grant=_current_auth_grant(read_context,connection,action)
                    assert (grant.role.value,grant.role_scope.value)==('personal_owner','tenant')
            with psycopg.connect(scope.admin) as connection:
                assert connection.execute("UPDATE identity_auth_tenant_roles SET state='inactive',version=version+1,updated_at=now() "
                    "WHERE tenant_id=%s AND user_id=%s AND role='personal_owner' AND state='active' RETURNING role,state,version",
                    (ctx.tenant_id,ctx.actor_id)).fetchone()==('personal_owner','inactive',2)
            with store._transaction(repository._cloud_context(read_context,'question.read')) as connection:
                grant=_current_auth_grant(read_context,connection,Action.VIEW)
                assert (grant.role.value,grant.role_scope.value)==('viewer','workspace')
                with pytest.raises(OpenLawAccessError):
                    _current_auth_grant(read_context,connection,Action.QUERY)
                assert connection.execute('SELECT allowed FROM identity_auth_source_access '
                    'WHERE tenant_id=%s AND workspace_id=%s AND user_id=%s AND source_version_id=%s',
                    (ctx.tenant_id,ctx.workspace_id,ctx.actor_id,prepared.version)).fetchone() is None
        else:
            notebook_context=NotebookContext(ctx.tenant_id,ctx.workspace_id,ctx.actor_id,
                read_context.trace_id,ctx.policy_version)
            notebooks=PostgresNotebookRepository(store)
            selected=notebooks.read_selected_context(notebook_context,ctx.notebook_id)
            assert (prepared.source,prepared.version) in selected.sources
            changed,replayed=notebooks.unbind_source(notebook_context,ctx.notebook_id,source_id=prepared.source,
                source_version_id=prepared.version,expected_etag=selected.etag,
                idempotency_key='s42-failed-replay-unbind-'+scope.suffix,request_fingerprint='e'*64,
                now=datetime.now(timezone.utc))
            assert replayed is False and changed.status=='unbound'
            with psycopg.connect(scope.admin) as connection:
                assert connection.execute('SELECT source_id,source_version_id,actor_id FROM notebook_source_unbindings '
                    'WHERE tenant_id=%s AND workspace_id=%s AND notebook_id=%s',
                    (ctx.tenant_id,ctx.workspace_id,ctx.notebook_id)).fetchall()==[(prepared.source,prepared.version,ctx.actor_id)]
                assert connection.execute('SELECT count(*) FROM notebook_bindings WHERE tenant_id=%s AND workspace_id=%s '
                    "AND notebook_id=%s AND binding_kind='source' AND record_id=%s AND version_id=%s",
                    (ctx.tenant_id,ctx.workspace_id,ctx.notebook_id,prepared.source,prepared.version)).fetchone()==(1,)
            with store._transaction(repository._cloud_context(read_context,'question.read')) as connection:
                assert _current_auth_grant(read_context,connection,Action.QUERY).allowed is True

        def read_snapshot():
            snapshot=_l2b_completion_snapshot(scope)
            with psycopg.connect(scope.admin) as connection:
                for table in ('identity_auth_tenant_roles','identity_auth_memberships',
                    'notebook_source_unbindings','notebook_source_unbinding_idempotency'):
                    snapshot[table]=connection.execute(f'SELECT to_jsonb(r) FROM {table} r '
                        'WHERE tenant_id=%s ORDER BY to_jsonb(r)::text',(ctx.tenant_id,)).fetchall()
            return snapshot

        before=read_snapshot()
        try:
            with pytest.raises(QuestionRepositoryError) as caught:
                repository.load_failed_for_replay(read_context,prepared.run,prepared.fingerprint)
        finally:
            assert read_snapshot()==before
        assert (caught.value.code,caught.value.status)==('QUESTION_SOURCE_UNAVAILABLE',404)
        with psycopg.connect(scope.admin) as connection:
            assert connection.execute('SELECT state,version,created_by,conversation_id,canonical_json FROM runs '
                'WHERE tenant_id=%s AND workspace_id=%s AND record_id=%s',
                (ctx.tenant_id,ctx.workspace_id,prepared.run)).fetchone()==(
                    'failed',prepared.accepted[1]+1,ctx.actor_id,prepared.accepted[3],prepared.accepted[4])
    finally:
        store.close()


def _l2b_assert_failed_question_materialized(prepared):
    """실제 확정 실패 source 소비의 기존 저장/계보/재조회 단언을 raw/general에 동일 적용한다."""
    import psycopg
    import daon_user_api.notification_events as events
    from daon_user_api.cloud_storage import CloudAccessContext
    from daon_user_api.notification_postgres import PostgresNotificationRepository
    scope=prepared.scope
    ctx=scope.context
    with psycopg.connect(scope.admin) as connection:
        sources=connection.execute('SELECT s.source_event_id,s.origin_event_id,s.recipient_id,s.action,s.target_type,'
            's.target_id,s.kind,s.severity,s.state,s.version,s.delivered_at,s.occurred_at,a.occurred_at '
            'FROM notification_source_outbox s JOIN audit_events a ON a.tenant_id=s.tenant_id '
            'AND a.workspace_id=s.workspace_id AND a.event_id=s.origin_event_id '
            "WHERE s.tenant_id=%s AND s.workspace_id=%s AND s.source_store='question' "
            "AND a.action='canon.transition' AND a.target_type='Run' AND a.target_id=%s "
            "AND a.actor_id=%s AND a.outcome='succeeded' AND a.after_value=%s::jsonb",
            (ctx.tenant_id,ctx.workspace_id,prepared.run,ctx.actor_id,'{"state":"failed","version":2}')).fetchall()
        assert len(sources)==1
        source=sources[0]
        assert source[2:11]==(ctx.actor_id,'canon.transition','Run',prepared.run,'run_failed','warning','pending',1,None)
        assert source[11]==source[12]
        for table in ('notifications','notification_details','notification_audit_outbox','security_audit_events',
            'run_results','citations','model_attempts'):
            assert connection.execute(f'SELECT count(*) FROM {table} WHERE tenant_id=%s',(ctx.tenant_id,)).fetchone()==(0,)
    # 실제 실패 원장/원감사/source 준비 이후에만 소비 미연결 RED를 낸다. 합성 publish는 사용하지 않는다.
    assert events.materialize_pending_completed(scope.app,context=ctx,limit=100)==1,'QUESTION_FAILED_SOURCE_NOT_MATERIALIZED'
    title='질문 처리 실패'
    summary='질문 처리 Run이 실패했습니다. 현재 처리 상태를 확인하세요.'
    link=f'/notebooks/{ctx.notebook_id}?run_id={prepared.run}'
    with psycopg.connect(scope.admin) as connection:
        rows=connection.execute('SELECT n.notification_id,n.recipient_id,n.source_event_id,n.version,n.read_at,'
            'd.source_store,d.origin_event_id,d.resource_type,d.resource_id,d.notebook_id,d.deep_link,d.title,d.summary,'
            'd.kind,d.severity,d.trace_id,d.policy_version,d.delivery_state '
            'FROM notifications n JOIN notification_details d ON d.tenant_id=n.tenant_id AND d.notification_id=n.notification_id '
            'WHERE n.tenant_id=%s AND n.workspace_id=%s',(ctx.tenant_id,ctx.workspace_id)).fetchall()
        assert len(rows)==1
        item=rows[0]
        assert item[1:]==(ctx.actor_id,source[0],1,None,'question',source[1],'Run',prepared.run,ctx.notebook_id,link,
            title,summary,'run_failed','warning',ctx.trace_id,ctx.policy_version,'delivered')
        audits=connection.execute('SELECT event_id,action,state,version,draft FROM notification_audit_outbox '
            'WHERE tenant_id=%s AND workspace_id=%s',(ctx.tenant_id,ctx.workspace_id)).fetchall()
        assert len(audits)==1 and audits[0][1:4]==('notification.created','pending',1)
        draft=audits[0][4]
        assert (draft['tenant_id'],draft['workspace_id'],draft['target_type'],draft['target_id'],draft['trace_id'],
            draft['policy_version'],draft['metadata'])==(
                ctx.tenant_id,ctx.workspace_id,'notification',item[0],ctx.trace_id,ctx.policy_version,{'source_event_id':source[0]})
        lineage=connection.execute('SELECT s.state,s.version,n.created_at,s.delivered_at,'
            "(a.draft->>'occurred_at')::timestamptz,s.occurred_at,o.occurred_at,"
            'n.xmin=d.xmin AND d.xmin=s.xmin AND s.xmin=a.xmin '
            'FROM notifications n JOIN notification_details d ON d.tenant_id=n.tenant_id AND d.notification_id=n.notification_id '
            'JOIN notification_source_outbox s ON s.tenant_id=n.tenant_id AND s.workspace_id=n.workspace_id '
            'AND s.source_event_id=n.source_event_id AND s.recipient_id=n.recipient_id '
            'JOIN notification_audit_outbox a ON a.tenant_id=n.tenant_id AND a.notification_id=n.notification_id '
            'JOIN audit_events o ON o.tenant_id=s.tenant_id AND o.workspace_id=s.workspace_id AND o.event_id=s.origin_event_id '
            'WHERE n.tenant_id=%s AND n.notification_id=%s',(ctx.tenant_id,item[0])).fetchall()
        assert len(lineage)==1
        state,version,created,delivered,audit_time,source_time,origin_time,same_tx=lineage[0]
        assert (state,version,same_tx)==('delivered',2,True)
        assert created>=delivered and audit_time==created and created>source_time
        assert source_time==origin_time==source[11]
        # 원문/예외/성공 결과를 실패 알림으로 복제하지 않는다. 중앙 감사 전달은 별도 commit 경계다.
        assert prepared.accepted[4]['question'] not in str((item,draft))
        assert 'QUESTION_FAILED' not in str((item,draft))
        for table in ('run_results','citations','model_attempts','security_audit_events'):
            assert connection.execute(f'SELECT count(*) FROM {table} WHERE tenant_id=%s',(ctx.tenant_id,)).fetchone()==(0,)
        assert connection.execute('SELECT state,version,created_by,conversation_id,canonical_json FROM runs '
            'WHERE tenant_id=%s AND workspace_id=%s AND record_id=%s',
            (ctx.tenant_id,ctx.workspace_id,prepared.run)).fetchone()==(
                'failed',prepared.accepted[1]+1,ctx.actor_id,prepared.accepted[3],prepared.accepted[4])
    before_replay=_l2b_completion_snapshot(scope)
    reopened_repository=PostgresNotificationRepository(scope.app,
        context=CloudAccessContext(ctx.tenant_id,ctx.workspace_id,ctx.actor_id,'view'))
    reopened=reopened_repository.notifications_for(tenant_id=ctx.tenant_id,recipient_id=ctx.actor_id)
    assert len(reopened)==1
    notification=reopened[0]
    assert (notification.notification_id,notification.recipient_id,notification.source_event_id,notification.resource_type,
        notification.resource_id,notification.deep_link,notification.title,notification.summary,notification.kind,
        notification.severity,notification.audit_event_id,notification.created_at,notification.version,notification.read_at)==(
            item[0],ctx.actor_id,source[0],'Run',prepared.run,link,title,summary,'run_failed','warning',audits[0][0],created,1,None)
    assert reopened_repository.notification_for(tenant_id=ctx.tenant_id,recipient_id=ctx.actor_id,notification_id=item[0])==notification
    assert events.materialize_pending_completed(scope.app,context=ctx,limit=100)==0
    assert _l2b_completion_snapshot(scope)==before_replay


def test_l2b_existing_0069_question_failed_materializes_and_reopens_without_duplicate():
    """실제 확정 실패 source를 소비하여 본인 알림을 원자 저장하고 새 연결 재조회·중복0을 요구한다."""
    _l2b_assert_failed_question_materialized(_l2b_question_failed_case(return_case=True))


def test_l2b_existing_0069_question_failed_consumer_sql_failure_after_detail_keeps_pending():
    """실제 실패 알림의 detail/created 의도 직후 SQL 오류는 알림과 source ack를 모두 되돌린다."""
    from unittest.mock import patch
    import psycopg
    import daon_user_api.notification_events as events
    from daon_user_api.notification import NotificationError
    prepared=_l2b_question_failed_case(return_case=True)
    scope=prepared.scope
    ctx=scope.context
    before=_l2b_completion_snapshot(scope)
    real_connect=psycopg.connect
    phases=[]
    faults=[]

    @contextmanager
    def observed_connect(*args,**kwargs):
        with real_connect(*args,**kwargs) as actual:
            class ObservedConnection:
                def __getattr__(self,name):
                    return getattr(actual,name)
                def execute(self,query,params=None):
                    cursor=actual.execute(query,params)
                    if query=='SELECT public.materialize_completed_notification(%s,%s)':
                        assert params[1] is True
                        parents=actual.execute('SELECT n.notification_id,d.resource_id,d.kind,d.severity,n.xmin=d.xmin '
                            'FROM notifications n JOIN notification_details d '
                            'ON d.tenant_id=n.tenant_id AND d.notification_id=n.notification_id '
                            'WHERE n.tenant_id=%s AND n.workspace_id=%s AND n.recipient_id=%s',
                            (ctx.tenant_id,ctx.workspace_id,ctx.actor_id)).fetchall()
                        assert len(parents)==1 and parents[0][1:]==(prepared.run,'run_failed','warning',True)
                        audits=actual.execute("SELECT draft->>'action',draft->>'target_id' "
                            'FROM public.pending_notification_audits(100)').fetchall()
                        assert audits==[('notification.created',parents[0][0])]
                        phases.append('ACTUAL_QUESTION_FAILED_DETAIL_AND_CREATED_INTENT')
                        try:
                            actual.execute('SELECT 1/0')
                        except psycopg.Error as error:
                            faults.append(error.sqlstate)
                            raise
                    return cursor
            yield ObservedConnection()

    try:
        with patch('psycopg.connect',new=observed_connect):
            with pytest.raises(NotificationError) as caught:
                events.materialize_pending_completed(scope.app,context=ctx,limit=100)
    finally:
        assert phases==['ACTUAL_QUESTION_FAILED_DETAIL_AND_CREATED_INTENT'] and faults==['22012']
        assert _l2b_completion_snapshot(scope)==before
    assert (caught.value.code,caught.value.http_status)==('DATABASE_UNAVAILABLE',503)
    with psycopg.connect(scope.admin) as connection:
        assert connection.execute('SELECT state,version,delivered_at FROM notification_source_outbox '
            'WHERE tenant_id=%s AND workspace_id=%s AND target_id=%s',
            (ctx.tenant_id,ctx.workspace_id,prepared.run)).fetchall()==[('pending',1,None)]
        for table in ('notifications','notification_details','notification_audit_outbox','security_audit_events'):
            assert connection.execute(f'SELECT count(*) FROM {table} WHERE tenant_id=%s',(ctx.tenant_id,)).fetchone()==(0,)


def test_l2b_existing_0069_question_failed_consumer_query_revocation_suppresses_and_sql_true_cannot_bypass():
    """VIEW만 남은 실제 viewer는 Python 소비와 app 직접 True 호출 어느 쪽에서도 실패 본문을 받지 못한다."""
    import psycopg
    import daon_user_api.notification_events as events
    from daon_user_api.authorization import Action
    from daon_user_api.open_law_source_access import _current_auth_grant,OpenLawAccessError
    prepared=_l2b_question_failed_case(return_case=True)
    scope=prepared.scope
    ctx=scope.context
    with psycopg.connect(scope.admin) as connection:
        assert connection.execute('SELECT role,state FROM identity_auth_tenant_roles WHERE tenant_id=%s AND user_id=%s',
            (ctx.tenant_id,ctx.actor_id)).fetchone()==('personal_owner','active')
        assert connection.execute('INSERT INTO identity_auth_memberships '
            '(tenant_id,workspace_id,user_id,role,state,version,updated_at) '
            "VALUES(%s,%s,%s,'viewer','active',1,now()) RETURNING role,state",
            (ctx.tenant_id,ctx.workspace_id,ctx.actor_id)).fetchone()==('viewer','active')
        assert connection.execute("UPDATE identity_auth_tenant_roles SET state='inactive',version=version+1,updated_at=now() "
            "WHERE tenant_id=%s AND user_id=%s AND role='personal_owner' AND state='active' RETURNING role,state,version",
            (ctx.tenant_id,ctx.actor_id)).fetchone()==('personal_owner','inactive',2)
        source_before=connection.execute('SELECT to_jsonb(s) FROM notification_source_outbox s '
            'WHERE tenant_id=%s AND workspace_id=%s AND target_id=%s',
            (ctx.tenant_id,ctx.workspace_id,prepared.run)).fetchall()
        assert len(source_before)==1
        source_before=source_before[0][0]
        assert (source_before['state'],source_before['version'],source_before['action'],source_before['kind'])==(
            'pending',1,'canon.transition','run_failed')
    before=_l2b_completion_snapshot(scope)
    with psycopg.connect(scope.app,connect_timeout=3) as connection:
        # 먼저 직접 SQL 경계만 강제 rollback으로 검사한다. Python의 사전 판정/소비가 SQL 거부를 대신하지 않는다.
        with connection.transaction(force_rollback=True):
            for name,value in (('tenant_id',ctx.tenant_id),('workspace_id',ctx.workspace_id),
                ('actor_id',ctx.actor_id),('capability','view')):
                connection.execute('SELECT set_config(%s,%s,true)',('app.'+name,value))
            assert connection.execute('SELECT current_user').fetchone()==('daon_app',)
            grant=_current_auth_grant(prepared.context,connection,Action.VIEW)
            assert grant.allowed is True and (grant.role.value,grant.role_scope.value)==('viewer','workspace')
            with pytest.raises(OpenLawAccessError):
                _current_auth_grant(prepared.context,connection,Action.QUERY)
            assert connection.execute('SELECT public.materialize_completed_notification(%s,true)',
                (source_before['source_event_id'],)).fetchone()==({'state':'suppressed'},)
    assert _l2b_completion_snapshot(scope)==before
    # 같은 현재 권한에서 실제 Python 소비도 발행0/suppressed여야 한다. 정상 source를 합성/재발행하지 않는다.
    assert events.materialize_pending_completed(scope.app,context=ctx,limit=100)==0
    after=_l2b_completion_snapshot(scope)
    assert {key:value for key,value in after.items() if key!='notification_source_outbox'}=={
        key:value for key,value in before.items() if key!='notification_source_outbox'}
    with psycopg.connect(scope.admin) as connection:
        source_after=connection.execute('SELECT to_jsonb(s) FROM notification_source_outbox s '
            'WHERE tenant_id=%s AND workspace_id=%s AND source_event_id=%s',
            (ctx.tenant_id,ctx.workspace_id,source_before['source_event_id'])).fetchone()[0]
        assert (source_after['state'],source_after['version'])==('suppressed',2) and source_after['delivered_at'] is not None
        assert {key:value for key,value in source_after.items() if key not in {'state','version','delivered_at'}}=={
            key:value for key,value in source_before.items() if key not in {'state','version','delivered_at'}}
        for table in ('notifications','notification_details','notification_audit_outbox','security_audit_events'):
            assert connection.execute(f'SELECT count(*) FROM {table} WHERE tenant_id=%s',(ctx.tenant_id,)).fetchone()==(0,)
        assert connection.execute('SELECT role,state,version FROM identity_auth_tenant_roles WHERE tenant_id=%s AND user_id=%s',
            (ctx.tenant_id,ctx.actor_id)).fetchone()==('personal_owner','inactive',2)
        assert connection.execute('SELECT role,state,version FROM identity_auth_memberships WHERE tenant_id=%s '
            'AND workspace_id=%s AND user_id=%s',(ctx.tenant_id,ctx.workspace_id,ctx.actor_id)).fetchone()==('viewer','active',1)
    assert events.materialize_pending_completed(scope.app,context=ctx,limit=100)==0
    assert _l2b_completion_snapshot(scope)==after


def _l2b_question_general_failed_case(*,return_case=False):
    """실제 무자료 general accepted owner의 최종 실패를 빈 선택 계보와 함께 같은 Tx에 저장한다."""
    import hashlib
    import psycopg
    from daon_user_api.authorization import Action
    from daon_user_api.cloud_storage import PostgresCloudStore,CloudAccessContext
    from daon_user_api.egress_policy import EgressPolicyContext,EgressPolicyPayload,EgressPolicyService
    from daon_user_api.egress_policy_postgres import PostgresEgressPolicyRepository
    from daon_user_api.open_law_source_access import _current_auth_grant
    from daon_user_api.question_answering import TextModelSelection
    from daon_user_api.question_answering_postgres import PostgresQuestionAnsweringRepository,QuestionContext
    from daon_user_api.question_answering_service import question_request_fingerprint
    from daon_user_api.question_egress import PostgresQuestionEgressAuthorizer
    scope=_existing_0069_studio_case()
    ctx=scope.context
    context=QuestionContext(ctx.tenant_id,ctx.workspace_id,ctx.actor_id,ctx.trace_id,ctx.policy_version,ctx.notebook_id)
    store=PostgresCloudStore(scope.app,min_size=0,max_size=2,notification_inbox_enabled=True)
    question='안녕하세요!'
    run,key='s42-general-failed-run-'+scope.suffix,'s42-general-failed-key-'+scope.suffix
    payload={'notebook_id':ctx.notebook_id,'question':question}
    fingerprint=question_request_fingerprint(context,run_id=run,idempotency_key=key,request_payload=payload)
    selection=TextModelSelection('OLLAMA','https://s42-model.invalid','s42-general-profile-'+scope.suffix,
        's42-general-deployment-'+scope.suffix,'s42-synthetic-model',1,provider_kind='server_internal')

    def unrelated_source_rows():
        # QA의 기존 Studio Source는 선택 자료가 아니다. general 계보에 복제하거나 변경하지 않는다.
        with psycopg.connect(scope.admin) as connection:
            return tuple(connection.execute(f'SELECT to_jsonb(r) FROM {table} r WHERE tenant_id=%s '
                'ORDER BY to_jsonb(r)::text',(ctx.tenant_id,)).fetchall() for table in ('sources','source_versions'))

    unrelated_before=unrelated_source_rows()
    assert all(unrelated_before)
    try:
        with psycopg.connect(scope.admin) as connection:
            for policy_scope in ('organization','workspace'):
                _studio_case_egress(connection,scope,policy_scope,EgressPolicyPayload.deny_external().as_dict(),1)
        policy_service=EgressPolicyService(PostgresEgressPolicyRepository(store))
        effective=policy_service.get_effective(EgressPolicyContext(ctx.tenant_id,ctx.tenant_id,ctx.workspace_id,
            ctx.actor_id,ctx.trace_id,ctx.policy_version))
        assert effective.mode=='deny_external'
        with store._transaction(CloudAccessContext(ctx.tenant_id,ctx.workspace_id,ctx.actor_id,'question.read')) as connection:
            for action in (Action.VIEW,Action.QUERY):
                assert _current_auth_grant(context,connection,action).allowed is True
            assert connection.execute('SELECT 1 FROM notebooks WHERE tenant_id=%s AND workspace_id=%s AND notebook_id=%s',
                (ctx.tenant_id,ctx.workspace_id,ctx.notebook_id)).fetchone()==(1,)
        authorization=PostgresQuestionEgressAuthorizer(store,policy_service).authorize(context,run_id=run,
            source_id=None,source_version_id=None,selection=selection,
            provider_payload=b'{"messages":[{"role":"user","content":"S42 synthetic general"}]}',
            question=question,context_mode='general_ungrounded',request_fingerprint=fingerprint)
        assert authorization['provider_owner'] is True and authorization['policy_fingerprint']==effective.fingerprint
        with psycopg.connect(scope.admin) as connection:
            accepted=connection.execute('SELECT state,version,created_by,conversation_id,canonical_json FROM runs '
                'WHERE tenant_id=%s AND workspace_id=%s AND record_id=%s',(ctx.tenant_id,ctx.workspace_id,run)).fetchone()
            assert accepted is not None and accepted[:3]==('accepted',1,ctx.actor_id)
            assert {k:accepted[4][k] for k in ('question','source_id','source_version_id','context_mode','request_fingerprint',
                'frozen_routing_context')}=={'question':question,'source_id':None,'source_version_id':None,
                    'context_mode':'general_ungrounded','request_fingerprint':fingerprint,
                    'frozen_routing_context':authorization['frozen_routing_context']}
            assert connection.execute("SELECT canonical_json->'allowed',canonical_json->'frozen_routing_context' "
                'FROM egress_decisions WHERE tenant_id=%s AND workspace_id=%s AND record_id=%s AND run_id=%s',
                (ctx.tenant_id,ctx.workspace_id,authorization['egress_decision_id'],run)).fetchone()==(
                    True,authorization['frozen_routing_context'])
            for table in ('run_snapshots','run_results','citations','model_attempts','notification_source_outbox'):
                assert connection.execute(f'SELECT count(*) FROM {table} WHERE tenant_id=%s',(ctx.tenant_id,)).fetchone()==(0,)
        assert unrelated_source_rows()==unrelated_before
        # 원 body/key/지문은 그대로 두고 실제 egress와 일치하는 실효 mode만 내부 인자로 전달한다.
        PostgresQuestionAnsweringRepository(store,_L2bNoObjectRead()).persist_failed(context,run_id=run,
            request_fingerprint=fingerprint,context_sources=(),selection=selection,egress_authorization=authorization,
            request_payload=payload,idempotency_key=key,question=question,actual_context_mode='general_ungrounded')
        origin='tr-'+hashlib.sha256(f'{run}|failed|1'.encode()).hexdigest()[:32]
        with psycopg.connect(scope.admin) as connection:
            assert connection.execute('SELECT state,version,created_by,conversation_id,canonical_json FROM runs '
                'WHERE tenant_id=%s AND workspace_id=%s AND record_id=%s',(ctx.tenant_id,ctx.workspace_id,run)).fetchone()==(
                    'failed',2,ctx.actor_id,accepted[3],accepted[4])
            snapshots=connection.execute('SELECT k.canonical_json,s.canonical_json,rs.canonical_json '
                'FROM run_snapshots rs JOIN scope_snapshots s ON s.tenant_id=rs.tenant_id AND s.workspace_id=rs.workspace_id '
                'AND s.record_id=rs.scope_snapshot_id JOIN knowledge_scopes k ON k.tenant_id=s.tenant_id '
                'AND k.workspace_id=s.workspace_id AND k.record_id=s.knowledge_scope_id '
                'WHERE rs.tenant_id=%s AND rs.workspace_id=%s AND rs.run_id=%s',(ctx.tenant_id,ctx.workspace_id,run)).fetchall()
            assert len(snapshots)==1
            assert snapshots[0][0]=={'mode':'general_ungrounded','items':[],'source_version_ids':[]}
            assert {k:snapshots[0][1][k] for k in ('mode','items','source_version_ids')}==snapshots[0][0]
            assert snapshots[0][2]['source_version_ids']==[] and snapshots[0][2]['authority']==[]
            assert snapshots[0][2]['frozen_routing_context']==authorization['frozen_routing_context']
            assert snapshots[0][2]['prompt_version']=='general-conversation-v1'
            assert snapshots[0][2]['tool_version']=='provider-chat-v1'
            assert connection.execute('SELECT count(*) FROM notebook_bindings WHERE tenant_id=%s AND workspace_id=%s '
                "AND notebook_id=%s AND binding_kind='conversation_thread' AND record_id=%s AND created_by=%s",
                (ctx.tenant_id,ctx.workspace_id,ctx.notebook_id,accepted[3],ctx.actor_id)).fetchone()==(1,)
            receipts=connection.execute('SELECT t.transition_id,t.source_state,t.target_state,t.transition_version,t.reason_code,'
                'p.outcome,p.result_state,p.result_version,p.safe_error_code,a.actor_id,a.action,a.outcome,a.after_value '
                'FROM canon_state_transitions t JOIN canon_transition_attempts p ON p.tenant_id=t.tenant_id '
                'AND p.workspace_id=t.workspace_id AND p.attempt_id=t.transition_id JOIN audit_events a '
                'ON a.tenant_id=t.tenant_id AND a.workspace_id=t.workspace_id AND a.event_id=t.transition_id '
                'WHERE t.tenant_id=%s AND t.workspace_id=%s AND t.transition_id=%s',
                (ctx.tenant_id,ctx.workspace_id,origin)).fetchall()
            assert receipts==[(origin,'accepted','failed',2,'QUESTION_FAILED','succeeded','failed',2,None,
                ctx.actor_id,'canon.transition','succeeded',{'state':'failed','version':2})]
            sources=connection.execute('SELECT o.recipient_id,o.source_store,o.origin_event_id,o.action,o.target_type,o.target_id,'
                'o.kind,o.severity,o.notebook_id,o.trace_id,o.policy_version,o.state,o.version,o.occurred_at=a.occurred_at,'
                'o.xmin=r.xmin AND r.xmin=t.xmin AND t.xmin=p.xmin AND p.xmin=a.xmin AND a.xmin=rs.xmin '
                'FROM notification_source_outbox o JOIN runs r ON r.tenant_id=o.tenant_id AND r.workspace_id=o.workspace_id '
                'AND r.record_id=o.target_id JOIN canon_state_transitions t ON t.tenant_id=o.tenant_id '
                'AND t.workspace_id=o.workspace_id AND t.transition_id=o.origin_event_id JOIN canon_transition_attempts p '
                'ON p.tenant_id=t.tenant_id AND p.workspace_id=t.workspace_id AND p.attempt_id=t.transition_id '
                'JOIN audit_events a ON a.tenant_id=t.tenant_id AND a.workspace_id=t.workspace_id AND a.event_id=t.transition_id '
                'JOIN run_snapshots rs ON rs.tenant_id=r.tenant_id AND rs.workspace_id=r.workspace_id AND rs.run_id=r.record_id '
                'WHERE o.tenant_id=%s AND o.workspace_id=%s',(ctx.tenant_id,ctx.workspace_id)).fetchall()
            assert sources==[(ctx.actor_id,'question',origin,'canon.transition','Run',run,'run_failed','warning',
                ctx.notebook_id,ctx.trace_id,ctx.policy_version,'pending',1,True,True)]
            for table in ('run_results','citations','model_attempts','notifications','notification_details','notification_audit_outbox'):
                assert connection.execute(f'SELECT count(*) FROM {table} WHERE tenant_id=%s',(ctx.tenant_id,)).fetchone()==(0,)
        assert unrelated_source_rows()==unrelated_before
        if return_case:
            return SimpleNamespace(scope=scope,context=context,run=run,request_payload=payload,idempotency_key=key,
                fingerprint=fingerprint,selection=selection,authorization=authorization,accepted=accepted,origin=origin)
    finally:
        store.close()


def test_l2b_existing_0069_question_general_failed_persists_actual_owner_without_selected_source():
    """실제 general accepted owner 저장의 기존 모든 계보/원자성 단언을 유지한다."""
    _l2b_question_general_failed_case()


def test_l2b_existing_0069_question_general_failed_replay_reopens_with_new_trace_without_writes():
    """실제 빈 자료 failed 원장의 새 trace replay를 읽기만으로 확인한다."""
    from dataclasses import replace
    from daon_user_api.cloud_storage import PostgresCloudStore
    from daon_user_api.question_answering_postgres import PostgresQuestionAnsweringRepository
    prepared=_l2b_question_general_failed_case(return_case=True)
    scope=prepared.scope
    replay_context=replace(prepared.context,trace_id='s42-general-failed-replay-'+scope.suffix)
    assert replay_context.trace_id!=prepared.context.trace_id
    store=PostgresCloudStore(scope.app,min_size=0,max_size=2,notification_inbox_enabled=True)
    try:
        before=_l2b_completion_snapshot(scope)
        try:
            assert PostgresQuestionAnsweringRepository(store,_L2bNoObjectRead()).load_failed_for_replay(
                replay_context,prepared.run,prepared.fingerprint) is True
        finally:
            assert _l2b_completion_snapshot(scope)==before
    finally:
        store.close()


def test_l2b_existing_0069_question_general_failed_materializes_and_reopens_without_selected_source():
    """실제 general 실패 source를 소비하며 빈 선택 계보·Run 링크·새 연결 재조회·중복0을 요구한다."""
    prepared=_l2b_question_general_failed_case(return_case=True)
    scope=prepared.scope
    before=_l2b_completion_snapshot(scope)
    assert len(before['run_snapshots'])==1 and before['run_snapshots'][0][0]['canonical_json']['authority']==[]
    assert before['scope_snapshots'][0][0]['canonical_json']['items']==[]
    assert before['scope_snapshots'][0][0]['canonical_json']['source_version_ids']==[]
    assert prepared.accepted[4]['source_id'] is None and prepared.accepted[4]['source_version_id'] is None
    _l2b_assert_failed_question_materialized(prepared)
    after=_l2b_completion_snapshot(scope)
    for table in ('runs','run_snapshots','scope_snapshots','knowledge_scopes','sources','source_versions',
        'notebook_bindings','run_results','citations','model_attempts'):
        assert after[table]==before[table]


@pytest.mark.parametrize('case',('query_revoked','notebook_deletion_accepted'))
def test_l2b_existing_0069_question_general_failed_current_access_denies_replay_and_materialization(case):
    """빈 선택도 현재 QUERY/Notebook 폐기를 검사한다. 삭제 요청 원장만 만들며 실제 삭제는 실행하지 않는다."""
    from dataclasses import replace
    from datetime import datetime,timezone
    import psycopg
    import daon_user_api.notification_events as events
    from daon_user_api.authorization import Action
    from daon_user_api.cloud_storage import PostgresCloudStore
    from daon_user_api.notebook import NotebookContext
    from daon_user_api.notebook_postgres import PostgresNotebookRepository
    from daon_user_api.open_law_source_access import _current_auth_grant,OpenLawAccessError
    from daon_user_api.question_answering_postgres import PostgresQuestionAnsweringRepository,QuestionRepositoryError
    prepared=_l2b_question_general_failed_case(return_case=True)
    scope=prepared.scope
    ctx=scope.context
    read_context=replace(prepared.context,trace_id='s42-general-access-'+scope.suffix)
    store=PostgresCloudStore(scope.app,min_size=0,max_size=2,notification_inbox_enabled=True)

    def snapshot():
        rows=_l2b_completion_snapshot(scope)
        with psycopg.connect(scope.admin) as connection:
            for table in ('identity_auth_tenant_roles','identity_auth_memberships','notebooks',
                'notebook_metadata_versions','notebook_deletion_requests'):
                rows[table]=connection.execute(f'SELECT to_jsonb(r) FROM {table} r '
                    'WHERE tenant_id=%s ORDER BY to_jsonb(r)::text',(ctx.tenant_id,)).fetchall()
        return rows

    try:
        repository=PostgresQuestionAnsweringRepository(store,_L2bNoObjectRead())
        normal=snapshot()
        assert repository.load_failed_for_replay(read_context,prepared.run,prepared.fingerprint) is True
        with store._transaction(repository._cloud_context(read_context,'question.read')) as connection:
            recipient=events._completed_event_access(connection,read_context,
                origin_event_id=prepared.origin,notebook_id=ctx.notebook_id)
            assert recipient is not None and (recipient.actor_id,recipient.notebook_id)==(ctx.actor_id,ctx.notebook_id)
        assert snapshot()==normal
        with psycopg.connect(scope.admin) as connection:
            sources=connection.execute('SELECT to_jsonb(s) FROM notification_source_outbox s '
                'WHERE tenant_id=%s AND workspace_id=%s AND target_id=%s',
                (ctx.tenant_id,ctx.workspace_id,prepared.run)).fetchall()
            assert len(sources)==1
            source_before=sources[0][0]
            assert (source_before['recipient_id'],source_before['origin_event_id'],source_before['state'],
                source_before['version'],source_before['kind'])==(ctx.actor_id,prepared.origin,'pending',1,'run_failed')
        if case=='query_revoked':
            with psycopg.connect(scope.admin) as connection:
                assert connection.execute('SELECT role,state FROM identity_auth_tenant_roles WHERE tenant_id=%s AND user_id=%s',
                    (ctx.tenant_id,ctx.actor_id)).fetchone()==('personal_owner','active')
                assert connection.execute('INSERT INTO identity_auth_memberships '
                    '(tenant_id,workspace_id,user_id,role,state,version,updated_at) '
                    "VALUES(%s,%s,%s,'viewer','active',1,now()) RETURNING role,state",
                    (ctx.tenant_id,ctx.workspace_id,ctx.actor_id)).fetchone()==('viewer','active')
            # Workspace viewer보다 활성 tenant 역할이 우선함을 실제 기존 판정으로 확인한 뒤 철회한다.
            with store._transaction(repository._cloud_context(read_context,'question.read')) as connection:
                for action in (Action.VIEW,Action.QUERY):
                    grant=_current_auth_grant(read_context,connection,action)
                    assert (grant.role.value,grant.role_scope.value)==('personal_owner','tenant')
            with psycopg.connect(scope.admin) as connection:
                assert connection.execute("UPDATE identity_auth_tenant_roles SET state='inactive',version=version+1,updated_at=now() "
                    "WHERE tenant_id=%s AND user_id=%s AND role='personal_owner' AND state='active' RETURNING role,state,version",
                    (ctx.tenant_id,ctx.actor_id)).fetchone()==('personal_owner','inactive',2)
        else:
            title='S42 general 실패 삭제 요청 경계'
            # 기존 Studio fixture Notebook에는 metadata가 없다. 본인 QA에 현재 metadata만 정상 필드로 준비한다.
            with psycopg.connect(scope.admin) as connection:
                assert connection.execute('SELECT count(*) FROM notebook_metadata_versions WHERE tenant_id=%s '
                    'AND workspace_id=%s AND notebook_id=%s',(ctx.tenant_id,ctx.workspace_id,ctx.notebook_id)).fetchone()==(0,)
                assert connection.execute('INSERT INTO notebook_metadata_versions '
                    '(tenant_id,workspace_id,notebook_id,version,title,description,is_current,updated_by,updated_at) '
                    'VALUES(%s,%s,%s,1,%s,NULL,true,%s,now()) RETURNING version,title,is_current',
                    (ctx.tenant_id,ctx.workspace_id,ctx.notebook_id,title,ctx.actor_id)).fetchone()==(1,title,True)
            notebooks=PostgresNotebookRepository(store)
            notebook_context=NotebookContext(ctx.tenant_id,ctx.workspace_id,ctx.actor_id,read_context.trace_id,ctx.policy_version)
            deletion,replayed=notebooks.request_deletion(notebook_context,ctx.notebook_id,title_confirmation=title,
                expected_etag='"notebook:1"',idempotency_key='s42-general-deletion-'+scope.suffix,
                request_fingerprint='f'*64,now=datetime.now(timezone.utc))
            assert replayed is False and (deletion.notebook_id,deletion.state,deletion.current_step,
                deletion.attempts,deletion.completed_at)==(ctx.notebook_id,'accepted','accepted',0,None)
            with psycopg.connect(scope.admin) as connection:
                assert connection.execute('SELECT state,current_step,attempts,completed_at FROM notebook_deletion_requests '
                    'WHERE tenant_id=%s AND workspace_id=%s AND notebook_id=%s AND request_id=%s',
                    (ctx.tenant_id,ctx.workspace_id,ctx.notebook_id,deletion.request_id)).fetchone()==('accepted','accepted',0,None)
                assert connection.execute('SELECT 1 FROM notebooks WHERE tenant_id=%s AND workspace_id=%s AND notebook_id=%s',
                    (ctx.tenant_id,ctx.workspace_id,ctx.notebook_id)).fetchone()==(1,)
        before=snapshot()
        try:
            with store._transaction(repository._cloud_context(read_context,'question.read')) as connection:
                grant=_current_auth_grant(read_context,connection,Action.VIEW)
                assert grant.allowed is True
                if case=='query_revoked':
                    assert (grant.role.value,grant.role_scope.value)==('viewer','workspace')
                    with pytest.raises(OpenLawAccessError):
                        _current_auth_grant(read_context,connection,Action.QUERY)
                else:
                    assert _current_auth_grant(read_context,connection,Action.QUERY).allowed is True
                assert events._completed_event_access(connection,read_context,
                    origin_event_id=prepared.origin,notebook_id=ctx.notebook_id) is None
            with pytest.raises(QuestionRepositoryError) as caught:
                repository.load_failed_for_replay(read_context,prepared.run,prepared.fingerprint)
        finally:
            assert snapshot()==before
        assert (caught.value.code,caught.value.status)==('QUESTION_SOURCE_UNAVAILABLE',404)
        # Python 결과를 SQL 권한 증명으로 쓰지 않는다. app 직접 True 호출도 별도 Tx에서 억제하고 rollback한다.
        with psycopg.connect(scope.app,connect_timeout=3) as connection:
            with connection.transaction(force_rollback=True):
                for name,value in (('tenant_id',ctx.tenant_id),('workspace_id',ctx.workspace_id),
                    ('actor_id',ctx.actor_id),('capability','view')):
                    connection.execute('SELECT set_config(%s,%s,true)',('app.'+name,value))
                assert connection.execute('SELECT current_user').fetchone()==('daon_app',)
                assert connection.execute('SELECT public.materialize_completed_notification(%s,true)',
                    (source_before['source_event_id'],)).fetchone()==({'state':'suppressed'},)
        assert snapshot()==before
        assert events.materialize_pending_completed(scope.app,context=ctx,limit=100)==0
        after=snapshot()
        assert {key:value for key,value in after.items() if key!='notification_source_outbox'}=={
            key:value for key,value in before.items() if key!='notification_source_outbox'}
        with psycopg.connect(scope.admin) as connection:
            source_after=connection.execute('SELECT to_jsonb(s) FROM notification_source_outbox s '
                'WHERE tenant_id=%s AND workspace_id=%s AND source_event_id=%s',
                (ctx.tenant_id,ctx.workspace_id,source_before['source_event_id'])).fetchone()[0]
            assert (source_after['state'],source_after['version'])==('suppressed',2) and source_after['delivered_at'] is not None
            assert {key:value for key,value in source_after.items() if key not in {'state','version','delivered_at'}}=={
                key:value for key,value in source_before.items() if key not in {'state','version','delivered_at'}}
            for table in ('notifications','notification_details','notification_audit_outbox','security_audit_events'):
                assert connection.execute(f'SELECT count(*) FROM {table} WHERE tenant_id=%s',(ctx.tenant_id,)).fetchone()==(0,)
        assert events.materialize_pending_completed(scope.app,context=ctx,limit=100)==0
        assert snapshot()==after
    finally:
        store.close()


def test_l2b_existing_0069_question_failed_legacy_strip_fingerprint_preserves_raw_accepted_question():
    """실제 egress의 raw 질문과 legacy strip 지문을 보존한 실패 저장을 요구한다. Provider 호출은 없다."""
    # 실제 accepted JSON/current auth/frozen 대조 뒤 원 raw 질문을 별도 내부 인자로 전달한다.
    # fp/body/key 및 failed 저장 후 원 accepted JSON 전체 불변 단언은 기존과 동일하다.
    _l2b_question_failed_case(raw_question=' S42 공백 질문 실패 저장 시험 ',logical_question='S42 공백 질문 실패 저장 시험')


@pytest.mark.parametrize('case',('different_question','empty_question','reverse_normalization','raw_substitution'))
def test_l2b_existing_0069_question_failed_rejects_question_normalization_or_raw_substitution(case):
    """실제 accepted 원문/지문을 다른 질문이나 반대 정규화·다른 raw 표현으로 바꿔 저장하지 못한다."""
    import psycopg
    from daon_user_api.cloud_storage import PostgresCloudStore
    from daon_user_api.question_answering_postgres import PostgresQuestionAnsweringRepository,QuestionRepositoryError
    raw=' S42 질문 원문 대조 시험 '
    logical=raw if case=='reverse_normalization' else 'S42 질문 원문 대조 시험'
    prepared=_l2b_question_failed_case(prepare_only=True,raw_question=raw,logical_question=logical)
    scope=prepared.scope
    ctx=scope.context
    assert prepared.accepted[4]['question']==raw and prepared.request_payload['question']==logical
    actual_question={
        'different_question':'S42 완전히 다른 질문',
        'empty_question':'',
        'reverse_normalization':'S42 질문 원문 대조 시험',
        'raw_substitution':'\tS42 질문 원문 대조 시험\t',
    }[case]
    expected=('IDEMPOTENCY_KEY_REUSED',409) if case=='raw_substitution' else ('QUESTION_RESULT_INVALID',400)
    store=PostgresCloudStore(scope.app,min_size=0,max_size=2,notification_inbox_enabled=True)
    try:
        before=_l2b_completion_snapshot(scope)
        try:
            with pytest.raises(QuestionRepositoryError) as caught:
                PostgresQuestionAnsweringRepository(store,_L2bNoObjectRead()).persist_failed(prepared.context,
                    run_id=prepared.run,request_fingerprint=prepared.fingerprint,context_sources=prepared.items,
                    selection=prepared.selection,egress_authorization=prepared.authorization,
                    request_payload=prepared.request_payload,idempotency_key=prepared.run,question=actual_question)
        finally:
            # 잘못 허용되어도 먼저 원장/전이/receipt/감사/source의 전체 불변 여부를 회수한다.
            assert _l2b_completion_snapshot(scope)==before
        assert (caught.value.code,caught.value.status)==expected
        with psycopg.connect(scope.admin) as connection:
            assert connection.execute('SELECT state,version,created_by,conversation_id,canonical_json FROM runs '
                'WHERE tenant_id=%s AND workspace_id=%s AND record_id=%s',
                (ctx.tenant_id,ctx.workspace_id,prepared.run)).fetchone()==prepared.accepted
    finally:
        store.close()


def test_l2b_existing_0069_question_failed_source_insert_failure_rolls_back_storage():
    """실제 실패 source INSERT 뒤 SQL22012가 accepted Run·JSON·snapshot/전이/원감사를 보존한다."""
    from unittest.mock import patch
    import psycopg
    from daon_user_api.cloud_storage import PostgresCloudStore
    from daon_user_api.question_answering_postgres import PostgresQuestionAnsweringRepository,QuestionRepositoryError
    from daon_user_api.notification_events import enqueue_completed_event
    prepared=_l2b_question_failed_case(prepare_only=True)
    scope=prepared.scope
    ctx=scope.context
    store=PostgresCloudStore(scope.app,min_size=0,max_size=2,notification_inbox_enabled=True)
    phases=[]
    faults=[]
    try:
        before=_l2b_completion_snapshot(scope)
        assert before['run_snapshots']==[] and before['notification_source_outbox']==[]
        def actual_enqueue_then_sql_failure(connection,context,**kwargs):
            run=connection.execute('SELECT r.state,r.version,r.canonical_json,a.actor_id,a.action,a.target_type,a.target_id,'
                "a.outcome,a.after_value->>'state',r.xmin=a.xmin "
                'FROM runs r JOIN audit_events a ON a.tenant_id=r.tenant_id AND a.workspace_id=r.workspace_id '
                'AND a.target_id=r.record_id WHERE r.tenant_id=%s AND r.workspace_id=%s AND r.record_id=%s AND a.event_id=%s',
                (ctx.tenant_id,ctx.workspace_id,prepared.run,kwargs['origin_event_id'])).fetchone()
            assert run==('failed',2,prepared.accepted[4],ctx.actor_id,'canon.transition','Run',prepared.run,'succeeded','failed',True)
            assert connection.execute('SELECT count(*) FROM run_snapshots WHERE tenant_id=%s AND workspace_id=%s AND run_id=%s',
                (ctx.tenant_id,ctx.workspace_id,prepared.run)).fetchone()==(1,)
            assert connection.execute('SELECT count(*) FROM notebook_bindings WHERE tenant_id=%s AND workspace_id=%s '
                "AND notebook_id=%s AND binding_kind='conversation_thread' AND record_id=%s",
                (ctx.tenant_id,ctx.workspace_id,ctx.notebook_id,prepared.accepted[3])).fetchone()==(1,)
            # 실제 제한 INSERT 반환값을 확인한다. 앱에 outbox SELECT 권한을 추가하지 않는다.
            assert enqueue_completed_event(connection,context,**kwargs)==1
            phases.append('ACTUAL_FAILED_RUN_RECEIPT_AND_SOURCE_INSERT')
            try:
                connection.execute('SELECT 1/0')
            except psycopg.Error as error:
                faults.append(error.sqlstate)
                raise
        repository=PostgresQuestionAnsweringRepository(store,_L2bNoObjectRead())
        with patch('daon_user_api.notification_events.enqueue_completed_event',side_effect=actual_enqueue_then_sql_failure):
            with pytest.raises(QuestionRepositoryError) as caught:
                repository.persist_failed(prepared.context,run_id=prepared.run,request_fingerprint=prepared.fingerprint,
                    context_sources=prepared.items,selection=prepared.selection,egress_authorization=prepared.authorization,
                    request_payload=prepared.request_payload,idempotency_key=prepared.run)
        assert (caught.value.code,caught.value.status,caught.value.retryable)==('QUESTION_DATABASE_UNAVAILABLE',503,True)
        assert phases==['ACTUAL_FAILED_RUN_RECEIPT_AND_SOURCE_INSERT'] and faults==['22012']
        assert _l2b_completion_snapshot(scope)==before
        with psycopg.connect(scope.admin) as connection:
            assert connection.execute('SELECT state,version,created_by,conversation_id,canonical_json FROM runs '
                'WHERE tenant_id=%s AND workspace_id=%s AND record_id=%s',
                (ctx.tenant_id,ctx.workspace_id,prepared.run)).fetchone()==prepared.accepted
    finally:
        store.close()


def test_l2b_existing_0069_question_failed_rejects_changed_scope_items_and_waiter():
    """실제 접근 가능한 다른 Notebook/primary 자료라도 원 fingerprint·owner를 대체하지 못한다."""
    from dataclasses import replace
    from datetime import datetime,timezone
    import hashlib
    import psycopg
    from daon_user_api.authorization import Action
    from daon_user_api.cloud_storage import PostgresCloudStore,CloudAccessContext
    from daon_user_api.notebook import NotebookContext
    from daon_user_api.notebook_postgres import PostgresNotebookRepository
    from daon_user_api.open_law_source_access import _current_auth_grant
    from daon_user_api.question_answering_service import QuestionInputSource
    from daon_user_api.question_answering_postgres import PostgresQuestionAnsweringRepository,QuestionRepositoryError
    from daon_user_api.studio_workspace_postgres import PostgresStudioWorkspaceRepository as Canon
    prepared=_l2b_question_failed_case(prepare_only=True)
    scope=prepared.scope
    ctx=scope.context
    store=PostgresCloudStore(scope.app,min_size=0,max_size=2,notification_inbox_enabled=True)
    try:
        notebook_context=NotebookContext(ctx.tenant_id,ctx.workspace_id,ctx.actor_id,ctx.trace_id,ctx.policy_version)
        notebooks=PostgresNotebookRepository(store)
        title='S42 합성 실패 입력 경계 Notebook'
        other,replayed=notebooks.create(notebook_context,title=title,description=None,
            idempotency_key='s42-q-failed-other-'+scope.suffix,request_fingerprint=hashlib.sha256(title.encode()).hexdigest(),
            now=datetime.now(timezone.utc))
        assert replayed is False and other.notebook_id!=ctx.notebook_id
        assert notebooks.bind_verified(notebook_context,other.notebook_id,binding_kind='source',record_id=prepared.source,
            version_id=prepared.version,now=datetime.now(timezone.utc)) is False
        other_context=replace(prepared.context,notebook_id=other.notebook_id)
        with store._transaction(CloudAccessContext(ctx.tenant_id,ctx.workspace_id,ctx.actor_id,'question.read')) as connection:
            _current_auth_grant(other_context,connection,Action.QUERY)
            assert connection.execute('SELECT 1 FROM notebooks n JOIN notebook_bindings b ON b.tenant_id=n.tenant_id '
                'AND b.workspace_id=n.workspace_id AND b.notebook_id=n.notebook_id '
                'WHERE n.tenant_id=%s AND n.workspace_id=%s AND n.notebook_id=%s AND n.created_by=%s '
                "AND b.binding_kind='source' AND b.record_id=%s AND b.version_id=%s",
                (ctx.tenant_id,ctx.workspace_id,other.notebook_id,ctx.actor_id,prepared.source,prepared.version)).fetchone()==(1,)
        # 기존 owner fixture가 같은 Notebook에 결속한 다른 실제 Source/Version을 사용한다.
        with store._transaction(CloudAccessContext(ctx.tenant_id,ctx.workspace_id,ctx.actor_id,'source.create')) as connection:
            secondary_row=connection.execute('SELECT sv.source_id,sv.record_id,s.state,s.version FROM source_versions sv '
                'JOIN sources s ON s.tenant_id=sv.tenant_id AND s.workspace_id=sv.workspace_id AND s.record_id=sv.source_id '
                'JOIN notebook_bindings b ON b.tenant_id=sv.tenant_id AND b.workspace_id=sv.workspace_id '
                "AND b.binding_kind='source' AND b.record_id=sv.source_id AND b.version_id=sv.record_id "
                'WHERE sv.tenant_id=%s AND sv.workspace_id=%s AND sv.record_id=%s AND b.notebook_id=%s',
                (ctx.tenant_id,ctx.workspace_id,scope.source_version_id,ctx.notebook_id)).fetchone()
            assert secondary_row is not None and secondary_row[0]!=prepared.source and secondary_row[2]=='registered'
            secondary,secondary_version,_,source_state_version=secondary_row
            for state in ('security_check','processing','indexing','ready'):
                source_state_version=Canon._transition(connection,ctx,'Source',secondary,source_state_version,state,
                    's42-q-failed-secondary-'+state+'-'+scope.suffix)
        alternate_items=(QuestionInputSource('raw_source',secondary,secondary,secondary_version),)
        cases=(
            ('other_notebook',other_context,prepared.request_payload,prepared.items,prepared.authorization,'IDEMPOTENCY_KEY_REUSED',409),
            ('primary_items',prepared.context,prepared.request_payload,alternate_items,prepared.authorization,'QUESTION_RESULT_INVALID',400),
            ('primary_body',prepared.context,{**prepared.request_payload,'source_id':secondary,'source_version_id':secondary_version},
                alternate_items,prepared.authorization,'IDEMPOTENCY_KEY_REUSED',409),
            ('provider_waiter',prepared.context,prepared.request_payload,prepared.items,
                {**prepared.authorization,'provider_owner':False},'QUESTION_NEW_RUN_REQUIRED',409),
        )
        before=_l2b_completion_snapshot(scope)
        repository=PostgresQuestionAnsweringRepository(store,_L2bNoObjectRead())
        observed=[]
        for name,context,payload,items,authorization,code,status in cases:
            with pytest.raises(QuestionRepositoryError) as caught:
                repository.persist_failed(context,run_id=prepared.run,request_fingerprint=prepared.fingerprint,
                    context_sources=items,selection=prepared.selection,egress_authorization=authorization,
                    request_payload=payload,idempotency_key=prepared.run)
            assert (caught.value.code,caught.value.status)==(code,status),name
            assert _l2b_completion_snapshot(scope)==before,name
            observed.append(name)
        assert observed==['other_notebook','primary_items','primary_body','provider_waiter']
        with psycopg.connect(scope.admin) as connection:
            assert connection.execute('SELECT state,version,created_by,conversation_id,canonical_json FROM runs '
                'WHERE tenant_id=%s AND workspace_id=%s AND record_id=%s',
                (ctx.tenant_id,ctx.workspace_id,prepared.run)).fetchone()==prepared.accepted
    finally:
        store.close()


def test_l2b_existing_0069_document_failed_materializes_and_reopens_without_duplicate():
    """실제 failed Run 의도를 소비하여 실패 알림을 저장하고 새 연결 조회·재소비 중복0을 확인한다."""
    import hashlib
    import psycopg
    import daon_user_api.notification_events as events
    from daon_user_api.cloud_storage import PostgresCloudStore,CloudAccessContext
    from daon_user_api.document_processing import DocumentProcessingContext
    from daon_user_api.document_processing_postgres import PostgresDocumentProcessingRepository
    from daon_user_api.notification_postgres import PostgresNotificationRepository
    scope=_existing_0069_studio_case()
    ctx=scope.context
    store=PostgresCloudStore(scope.app,min_size=0,max_size=2,notification_inbox_enabled=True)
    failure_code='S42_SYNTHETIC_DOCUMENT_FAILURE'
    try:
        _,source_version=_l2b_bound_pdf(scope,store)
        context=DocumentProcessingContext(ctx.tenant_id,ctx.workspace_id,ctx.actor_id,ctx.trace_id,ctx.policy_version)
        repository=PostgresDocumentProcessingRepository(store,_L2bNoObjectRead())
        run=repository.start(context,source_version,notebook_id=ctx.notebook_id)
        with psycopg.connect(scope.admin) as connection:
            previous=connection.execute('SELECT state,version FROM processing_runs WHERE tenant_id=%s '
                'AND workspace_id=%s AND record_id=%s',(ctx.tenant_id,ctx.workspace_id,run)).fetchone()
            assert previous is not None and previous[0]=='vision_llm_understanding'
        origin='tr-'+hashlib.sha256('|'.join(('ProcessingRun',run,'failed',str(previous[1]))).encode()).hexdigest()[:32]
        repository.fail(context,run,failure_code,retryable=False)
    finally:
        store.close()
    with psycopg.connect(scope.admin) as connection:
        sources=connection.execute('SELECT s.source_event_id,s.origin_event_id,s.recipient_id,s.target_type,s.target_id,'
            's.kind,s.severity,s.state,s.version,s.delivered_at,s.occurred_at,a.occurred_at '
            'FROM notification_source_outbox s JOIN audit_events a ON a.tenant_id=s.tenant_id '
            'AND a.workspace_id=s.workspace_id AND a.event_id=s.origin_event_id '
            'WHERE s.tenant_id=%s AND s.workspace_id=%s',(ctx.tenant_id,ctx.workspace_id)).fetchall()
        assert len(sources)==1
        source=sources[0]
        assert source[1:10]==(origin,ctx.actor_id,'ProcessingRun',run,'run_failed','warning','pending',1,None)
        assert source[10]==source[11]
        for table in ('notifications','notification_details','notification_audit_outbox','security_audit_events'):
            assert connection.execute(f'SELECT count(*) FROM {table} WHERE tenant_id=%s',(ctx.tenant_id,)).fetchone()==(0,)
    assert events.materialize_pending_completed(scope.app,context=ctx,limit=100)==1
    title='문서 처리 실패'
    summary='문서 처리 Run이 실패했습니다. 현재 처리 상태를 확인하세요.'
    link=f'/notebooks/{ctx.notebook_id}?source_version_id={source_version}'
    with psycopg.connect(scope.admin) as connection:
        rows=connection.execute('SELECT n.notification_id,n.recipient_id,n.source_event_id,n.version,n.read_at,'
            'd.source_store,d.origin_event_id,d.resource_type,d.resource_id,d.notebook_id,d.deep_link,d.title,d.summary,'
            'd.kind,d.severity,d.trace_id,d.policy_version,d.delivery_state '
            'FROM notifications n JOIN notification_details d ON d.tenant_id=n.tenant_id AND d.notification_id=n.notification_id '
            'WHERE n.tenant_id=%s AND n.workspace_id=%s',(ctx.tenant_id,ctx.workspace_id)).fetchall()
        assert len(rows)==1
        item=rows[0]
        assert item[1:]==(ctx.actor_id,source[0],1,None,'document',origin,'ProcessingRun',run,ctx.notebook_id,link,
            title,summary,'run_failed','warning',ctx.trace_id,ctx.policy_version,'delivered')
        audits=connection.execute('SELECT event_id,action,state,version,draft FROM notification_audit_outbox '
            'WHERE tenant_id=%s AND workspace_id=%s',(ctx.tenant_id,ctx.workspace_id)).fetchall()
        assert len(audits)==1 and audits[0][1:4]==('notification.created','pending',1)
        draft=audits[0][4]
        assert (draft['tenant_id'],draft['workspace_id'],draft['target_type'],draft['target_id'],draft['trace_id'],
            draft['policy_version'],draft['metadata'])==(
                ctx.tenant_id,ctx.workspace_id,'notification',item[0],ctx.trace_id,ctx.policy_version,{'source_event_id':source[0]})
        lineage=connection.execute('SELECT s.state,s.version,n.created_at,s.delivered_at,'
            "(a.draft->>'occurred_at')::timestamptz,s.occurred_at,o.occurred_at,"
            'n.xmin=d.xmin AND d.xmin=s.xmin AND s.xmin=a.xmin '
            'FROM notifications n JOIN notification_details d ON d.tenant_id=n.tenant_id AND d.notification_id=n.notification_id '
            'JOIN notification_source_outbox s ON s.tenant_id=n.tenant_id AND s.workspace_id=n.workspace_id '
            'AND s.source_event_id=n.source_event_id AND s.recipient_id=n.recipient_id '
            'JOIN notification_audit_outbox a ON a.tenant_id=n.tenant_id AND a.notification_id=n.notification_id '
            'JOIN audit_events o ON o.tenant_id=s.tenant_id AND o.workspace_id=s.workspace_id AND o.event_id=s.origin_event_id '
            'WHERE n.tenant_id=%s AND n.notification_id=%s',(ctx.tenant_id,item[0])).fetchall()
        assert len(lineage)==1
        state,version,created,delivered,audit_time,source_time,origin_time,same_tx=lineage[0]
        assert (state,version,same_tx)==('delivered',2,True)
        assert created>=delivered and audit_time==created and source_time==origin_time==source[10]
        assert connection.execute('SELECT count(*) FROM security_audit_events WHERE tenant_id=%s',(ctx.tenant_id,)).fetchone()==(0,)
        assert failure_code not in str((item,draft))
    before_replay=_l2b_completion_snapshot(scope)
    reopened_repository=PostgresNotificationRepository(scope.app,
        context=CloudAccessContext(ctx.tenant_id,ctx.workspace_id,ctx.actor_id,'view'))
    reopened=reopened_repository.notifications_for(tenant_id=ctx.tenant_id,recipient_id=ctx.actor_id)
    assert len(reopened)==1
    notification=reopened[0]
    assert (notification.notification_id,notification.recipient_id,notification.source_event_id,notification.resource_type,
        notification.resource_id,notification.deep_link,notification.title,notification.summary,notification.kind,
        notification.severity,notification.audit_event_id,notification.created_at,notification.version,notification.read_at)==(
            item[0],ctx.actor_id,source[0],'ProcessingRun',run,link,title,summary,'run_failed','warning',audits[0][0],created,1,None)
    assert reopened_repository.notification_for(tenant_id=ctx.tenant_id,recipient_id=ctx.actor_id,notification_id=item[0])==notification
    assert events.materialize_pending_completed(scope.app,context=ctx,limit=100)==0
    assert _l2b_completion_snapshot(scope)==before_replay


def test_l2b_existing_0069_document_failed_source_insert_failure_rolls_back_failure():
    """실제 fail의 source INSERT 뒤 SQL 오류는 Run·Source 전이·원감사·의도를 전부 되돌린다."""
    from unittest.mock import patch
    import psycopg
    from daon_user_api.cloud_storage import PostgresCloudStore
    from daon_user_api.document_processing import DocumentProcessingContext
    from daon_user_api.document_processing_postgres import PostgresDocumentProcessingRepository
    from daon_user_api.document_understanding_adapter import DocumentUnderstandingError
    from daon_user_api.notification_events import enqueue_completed_event
    scope=_existing_0069_studio_case()
    ctx=scope.context
    store=PostgresCloudStore(scope.app,min_size=0,max_size=2,notification_inbox_enabled=True)
    faults=[]
    phases=[]
    try:
        source,source_version=_l2b_bound_pdf(scope,store)
        context=DocumentProcessingContext(ctx.tenant_id,ctx.workspace_id,ctx.actor_id,ctx.trace_id,ctx.policy_version)
        repository=PostgresDocumentProcessingRepository(store,_L2bNoObjectRead())
        run=repository.start(context,source_version,notebook_id=ctx.notebook_id)
        before=_l2b_completion_snapshot(scope)
        assert before['notification_source_outbox']==[]
        def actual_enqueue_then_sql_failure(connection,event_context,**kwargs):
            assert connection.execute('SELECT state FROM processing_runs WHERE tenant_id=%s AND workspace_id=%s '
                'AND record_id=%s',(ctx.tenant_id,ctx.workspace_id,run)).fetchone()==('failed',)
            assert connection.execute('SELECT state FROM sources WHERE tenant_id=%s AND workspace_id=%s '
                'AND record_id=%s',(ctx.tenant_id,ctx.workspace_id,source)).fetchone()==('failed',)
            assert connection.execute("SELECT actor_id,action,target_type,target_id,outcome,after_value->>'state' "
                'FROM audit_events WHERE tenant_id=%s AND workspace_id=%s AND event_id=%s',
                (ctx.tenant_id,ctx.workspace_id,kwargs['origin_event_id'])).fetchone()==(
                    ctx.actor_id,'canon.transition','ProcessingRun',run,'succeeded','failed')
            # 실제 제한 함수의 INSERT 반환값을 확인한다. outbox SELECT 권한을 추가하지 않는다.
            assert enqueue_completed_event(connection,event_context,**kwargs)==1
            phases.append('ACTUAL_FAILED_RUN_AUDIT_AND_SOURCE_INSERT')
            try:
                connection.execute('SELECT 1/0')
            except psycopg.Error as error:
                faults.append(error.sqlstate)
                raise
        with patch('daon_user_api.notification_events.enqueue_completed_event',side_effect=actual_enqueue_then_sql_failure):
            with pytest.raises(DocumentUnderstandingError) as caught:
                repository.fail(context,run,'S42_SYNTHETIC_DOCUMENT_FAILURE',retryable=False)
        assert (caught.value.code,caught.value.status)==('DOCUMENT_PROCESSING_DATABASE_UNAVAILABLE',503)
        assert phases==['ACTUAL_FAILED_RUN_AUDIT_AND_SOURCE_INSERT'] and faults==['22012']
        assert _l2b_completion_snapshot(scope)==before
    finally:
        store.close()


def test_l2b_existing_0069_question_completed_records_source_on_original_transaction():
    """실제 persist_completed의 일반·insufficient 완료 및 Notebook/원감사 후 source 의도를 요구한다."""
    import psycopg
    from daon_user_api.cloud_storage import PostgresCloudStore,CloudAccessContext
    from daon_user_api.question_answering import TextModelSelection,GroundedTextResult
    from daon_user_api.question_answering_postgres import PostgresQuestionAnsweringRepository,QuestionContext
    from daon_user_api.document_index_postgres import IndexedEvidenceChunk
    from daon_user_api.studio_workspace_postgres import PostgresStudioWorkspaceRepository as Canon
    missing=[]
    for insufficient in (False,True):
        scope=_existing_0069_studio_case()
        ctx=scope.context
        store=PostgresCloudStore(scope.app,min_size=0,max_size=2,notification_inbox_enabled=True)
        try:
            source,version=_l2b_bound_pdf(scope,store)
            span='s42l2b-span-'+scope.suffix
            with store._transaction(CloudAccessContext(ctx.tenant_id,ctx.workspace_id,ctx.actor_id,'source.create')) as connection:
                source_state_version=1
                for state in ('security_check','processing','indexing','ready'):
                    source_state_version=Canon._transition(connection,ctx,'Source',source,source_state_version,
                        state,'s42l2b-source-'+state+'-'+scope.suffix)
                Canon._insert(connection,ctx,'evidence_spans',span,{'page':1,'text':'S42 synthetic evidence'},
                    extra_columns=('source_version_id',),extra_values=(version,))
            context=QuestionContext(ctx.tenant_id,ctx.workspace_id,ctx.actor_id,ctx.trace_id,ctx.policy_version,
                notebook_id=ctx.notebook_id)
            run='s42l2b-run-'+scope.suffix
            evidence=() if insufficient else (IndexedEvidenceChunk('s42l2b-chunk-'+scope.suffix,
                source,version,1,'S42 synthetic evidence',span,1.0),)
            repository=PostgresQuestionAnsweringRepository(store,_L2bNoObjectRead())
            stored=repository.persist_completed(context,run_id=run,source_id=source,source_version_id=version,
                question='S42 synthetic stored question',selection=TextModelSelection('OLLAMA','https://s42-model.invalid',
                    's42l2b-profile-'+scope.suffix,'s42l2b-deployment-'+scope.suffix,'s42-synthetic-model',1,
                    provider_kind='server_internal'),evidence=evidence,
                result=GroundedTextResult('' if insufficient else 'S42 synthetic evidence',
                    () if insufficient else (evidence[0].chunk_id,),insufficient,{}),provider_called=not insufficient)
            assert stored.run_id==run and stored.insufficient is insufficient
            with psycopg.connect(scope.admin) as connection:
                row=connection.execute('SELECT r.state,r.created_by,r.conversation_id,rr.canonical_json->\'insufficient\' '
                    'FROM runs r JOIN run_results rr ON rr.tenant_id=r.tenant_id AND rr.workspace_id=r.workspace_id '
                    'AND rr.run_id=r.record_id WHERE r.tenant_id=%s AND r.workspace_id=%s AND r.record_id=%s',
                    (ctx.tenant_id,ctx.workspace_id,run)).fetchone()
                assert row is not None and (row[0],row[1],row[3])==('completed',ctx.actor_id,insufficient)
                assert connection.execute('SELECT 1 FROM notebook_bindings WHERE tenant_id=%s AND workspace_id=%s '
                    "AND notebook_id=%s AND binding_kind='conversation_thread' AND record_id=%s",
                    (ctx.tenant_id,ctx.workspace_id,ctx.notebook_id,row[2])).fetchone()==(1,)
                original=connection.execute('SELECT event_id,actor_id FROM audit_events WHERE tenant_id=%s AND workspace_id=%s '
                    "AND action='question.answer' AND target_type='Run' AND target_id=%s AND outcome='succeeded'",
                    (ctx.tenant_id,ctx.workspace_id,run)).fetchall()
                assert len(original)==1 and original[0][1]==ctx.actor_id
                if not _l2b_assert_completed_source(connection,scope,origin=original[0][0],store_name='question',
                        action='question.answer',target_type='Run',target_id=run):
                    missing.append('insufficient' if insufficient else 'sufficient')
        finally:
            store.close()
    if missing:
        pytest.fail('QUESTION_COMPLETED_SOURCE_INTENT_MISSING; actual_business_and_origin_saved; results='+','.join(missing),pytrace=False)


def _l2b_prepared_completion(scope,store,domain,*,document_ready=False):
    """실제 원장 메서드 실행 직전까지만 준비한다. 완료/감사/알림 저장은 대체하지 않는다."""
    from dataclasses import replace
    ctx=scope.context
    source,version=_l2b_bound_pdf(scope,store)
    if domain=='document':
        from daon_user_api.document_processing import DocumentProcessingContext
        from daon_user_api.document_processing_postgres import PostgresDocumentProcessingRepository
        from test_document_index_postgres import ready_result
        context=DocumentProcessingContext(ctx.tenant_id,ctx.workspace_id,ctx.actor_id,ctx.trace_id,ctx.policy_version)
        repository=PostgresDocumentProcessingRepository(store,_L2bNoObjectRead())
        run=repository.start(context,version,notebook_id=ctx.notebook_id)
        # 검토 필요 Source 전이도 완료 transaction과 함께 rollback되어야 한다.
        result=replace(ready_result(),source_id=source,source_version_id=version)
        if not document_ready:
            result=replace(result,status='needs_review',conflict='UNDERSTANDING_PARSER_CONFLICT')
        else:
            def complete_ready():
                from daon_user_api.document_index_postgres import PostgresDocumentIndex
                repository.complete(context,run,result)
                PostgresDocumentIndex(store).index_result(context,result)
            return run,complete_ready
        return run,lambda:repository.complete(context,run,result)
    assert domain=='question'
    from daon_user_api.cloud_storage import CloudAccessContext
    from daon_user_api.question_answering import TextModelSelection,GroundedTextResult
    from daon_user_api.question_answering_postgres import PostgresQuestionAnsweringRepository,QuestionContext
    from daon_user_api.studio_workspace_postgres import PostgresStudioWorkspaceRepository as Canon
    with store._transaction(CloudAccessContext(ctx.tenant_id,ctx.workspace_id,ctx.actor_id,'source.create')) as connection:
        state_version=1
        for state in ('security_check','processing','indexing','ready'):
            state_version=Canon._transition(connection,ctx,'Source',source,state_version,state,
                's42l2b-fault-source-'+state+'-'+scope.suffix)
    context=QuestionContext(ctx.tenant_id,ctx.workspace_id,ctx.actor_id,ctx.trace_id,ctx.policy_version,
        notebook_id=ctx.notebook_id)
    run='s42l2b-fault-run-'+scope.suffix
    repository=PostgresQuestionAnsweringRepository(store,_L2bNoObjectRead())
    return run,lambda:repository.persist_completed(context,run_id=run,source_id=source,source_version_id=version,
        question='S42 synthetic stored question',selection=TextModelSelection('OLLAMA','https://s42-model.invalid',
            's42l2b-profile-'+scope.suffix,'s42l2b-deployment-'+scope.suffix,'s42-synthetic-model',1,
            provider_kind='server_internal'),evidence=(),result=GroundedTextResult('',(),True,{}),provider_called=False)


def _l2b_completion_snapshot(scope):
    """해당 합성 scope의 저장행 전체를 비교하여 건수가 같은 부분 변경도 놓치지 않는다."""
    import psycopg
    tables=('processing_runs','sources','source_versions','understanding_results','extraction_evidence',
        'runs','run_results','run_snapshots','knowledge_scopes','scope_snapshots','conversations',
        'provider_profiles','model_artifacts','model_deployments','routing_policy_versions','routing_decisions',
        'model_attempts','citations','deliveries','output_versions','notebook_bindings','notebook_activities','canon_state_transitions',
        'canon_transition_attempts','audit_events','idempotency_records','notification_source_outbox',
        'notifications','notification_details','notification_audit_outbox')
    with psycopg.connect(scope.admin) as connection:
        return {table:connection.execute(f'SELECT to_jsonb(t) FROM public.{table} t '
            'WHERE tenant_id=%s ORDER BY to_jsonb(t)::text',(scope.context.tenant_id,)).fetchall() for table in tables}


def _l2b_repository_error(domain):
    from daon_user_api.document_understanding_adapter import DocumentUnderstandingError
    from daon_user_api.question_answering_postgres import QuestionRepositoryError
    return (DocumentUnderstandingError,'DOCUMENT_PROCESSING_DATABASE_UNAVAILABLE') if domain=='document' else (
        QuestionRepositoryError,'QUESTION_DATABASE_UNAVAILABLE')


def _l2b_assert_uncommitted_completion(connection,scope,domain,run,origin):
    table='processing_runs' if domain=='document' else 'runs'
    action,target=('canon.transition','ProcessingRun') if domain=='document' else ('question.answer','Run')
    assert connection.execute(f'SELECT state FROM public.{table} WHERE tenant_id=%s AND workspace_id=%s AND record_id=%s',
        (scope.context.tenant_id,scope.context.workspace_id,run)).fetchone()==('completed',)
    assert connection.execute('SELECT actor_id,action,target_type,target_id,outcome FROM public.audit_events '
        'WHERE tenant_id=%s AND workspace_id=%s AND event_id=%s',
        (scope.context.tenant_id,scope.context.workspace_id,origin)).fetchone()==(
            scope.context.actor_id,action,target,run,'succeeded')


def _l2b_run_enqueue_rollback(domain):
    from unittest.mock import patch
    import psycopg
    from daon_user_api.cloud_storage import PostgresCloudStore
    from daon_user_api.notification_events import enqueue_completed_event
    scope=_existing_0069_studio_case()
    store=PostgresCloudStore(scope.app,min_size=0,max_size=2,notification_inbox_enabled=True)
    injected=[]
    try:
        run,complete=_l2b_prepared_completion(scope,store,domain)
        before=_l2b_completion_snapshot(scope)
        assert before['notification_source_outbox']==[]
        def actual_enqueue_then_fail(connection,context,**kwargs):
            _l2b_assert_uncommitted_completion(connection,scope,domain,run,kwargs['origin_event_id'])
            # 앱의 outbox SELECT 권한을 넓히지 않는다. 실제 제한 INSERT 함수의 삽입 반환값 1을 확인한다.
            assert enqueue_completed_event(connection,context,**kwargs)==1
            try:
                connection.execute('SELECT 1/0')
            except psycopg.Error as error:
                injected.append(error.sqlstate)
                raise
        error_type,code=_l2b_repository_error(domain)
        with patch('daon_user_api.notification_events.enqueue_completed_event',side_effect=actual_enqueue_then_fail):
            with pytest.raises(error_type) as caught:
                complete()
        assert (caught.value.code,caught.value.status)==(code,503)
        assert injected==['22012']
        assert _l2b_completion_snapshot(scope)==before
    finally:
        store.close()


def test_l2b_existing_0069_document_source_insert_failure_rolls_back_completion():
    """실제 source INSERT 직후 오류가 Run/Source 상태·이해결과·전이·원감사를 전부 되돌린다."""
    _l2b_run_enqueue_rollback('document')


def test_l2b_existing_0069_question_source_insert_failure_rolls_back_completion():
    """실제 source INSERT 직후 오류가 Run/결과/대화 binding/감사 일부 저장을 남기지 않는다."""
    _l2b_run_enqueue_rollback('question')


@pytest.mark.parametrize('domain',('document','question'))
@pytest.mark.parametrize('case',('other_actor','wrong_notebook','committed_origin'))
def test_l2b_existing_0069_direct_completed_enqueue_rejects_unbound_origin(domain,case):
    """직접 함수도 실제 caller·정확 Notebook·현재 transaction의 원감사를 우회할 수 없다."""
    from unittest.mock import patch
    from datetime import datetime,timezone
    import psycopg
    from daon_user_api.cloud_storage import PostgresCloudStore,CloudAccessContext,CloudDatabaseError
    from daon_user_api.notebook import NotebookContext
    from daon_user_api.notebook_postgres import PostgresNotebookRepository
    scope=_existing_0069_studio_case()
    ctx=scope.context
    # 이미 commit된 실제 원장을 준비할 때만 기존 기본 OFF 동작을 사용한다. 원업무 메서드는 그대로다.
    store=PostgresCloudStore(scope.app,min_size=0,max_size=2,notification_inbox_enabled=case!='committed_origin')
    denied=[]
    try:
        run,complete=_l2b_prepared_completion(scope,store,domain)
        peer=_studio_case_member(scope,role='viewer') if case=='other_actor' else None
        notebook=ctx.notebook_id
        if case=='wrong_notebook':
            other,replayed=PostgresNotebookRepository(store).create(
                NotebookContext(ctx.tenant_id,ctx.workspace_id,ctx.actor_id,ctx.trace_id,ctx.policy_version),
                title='S42 synthetic unbound Notebook',description=None,idempotency_key='s42l2b-unbound-'+scope.suffix,
                request_fingerprint='b'*64,now=datetime.now(timezone.utc))
            assert replayed is False and other.notebook_id!=ctx.notebook_id
            notebook=other.notebook_id
        def direct_probe(connection,context,**kwargs):
            _l2b_assert_uncommitted_completion(connection,scope,domain,run,kwargs['origin_event_id'])
            with connection.transaction(force_rollback=True):
                if peer is not None:
                    connection.execute("SELECT set_config('app.actor_id',%s,true)",(peer,))
                try:
                    connection.execute('SELECT public.enqueue_completed_notification(%s,%s)',
                        (kwargs['origin_event_id'],notebook))
                except psycopg.Error as error:
                    assert error.sqlstate in {'42501','23514'}
                    denied.append(error.sqlstate)
            # 허용되는 결함이 있어도 업무/source를 commit하지 않는다. 최종 denied 단언은 별도로 남긴다.
            raise CloudDatabaseError('DATABASE_UNAVAILABLE',retryable=True)
        if case=='committed_origin':
            complete()
            with psycopg.connect(scope.admin) as connection:
                action,target=('canon.transition','ProcessingRun') if domain=='document' else ('question.answer','Run')
                originals=connection.execute('SELECT event_id FROM audit_events WHERE tenant_id=%s AND workspace_id=%s '
                    "AND target_id=%s AND action=%s AND target_type=%s AND outcome='succeeded' "
                    "AND (action<>'canon.transition' OR after_value->>'state'='completed')",
                    (ctx.tenant_id,ctx.workspace_id,run,action,target)).fetchall()
                assert len(originals)==1
            before=_l2b_completion_snapshot(scope)
            with store._transaction(CloudAccessContext(ctx.tenant_id,ctx.workspace_id,ctx.actor_id,'view')) as connection:
                with connection.transaction(force_rollback=True):
                    try:
                        connection.execute('SELECT public.enqueue_completed_notification(%s,%s)',
                            (originals[0][0],ctx.notebook_id))
                    except psycopg.Error as error:
                        assert error.sqlstate in {'42501','23514'}
                        denied.append(error.sqlstate)
        else:
            before=_l2b_completion_snapshot(scope)
            error_type,code=_l2b_repository_error(domain)
            with patch('daon_user_api.notification_events.enqueue_completed_event',side_effect=direct_probe):
                with pytest.raises(error_type) as caught:
                    complete()
            assert (caught.value.code,caught.value.status)==(code,503)
        assert before['notification_source_outbox']==[]
        assert len(denied)==1
        assert _l2b_completion_snapshot(scope)==before
    finally:
        store.close()


@pytest.mark.parametrize('mode',('daon_priority','mixed','general_ungrounded'))
def test_l2b_existing_0069_question_context_modes_preserve_completed_source(mode):
    """typed knowledge/general 입력 fixture와 실제 완료 저장을 구분한다. 외부 Daon/모델 호출은 없다."""
    from dataclasses import replace
    from datetime import datetime,timezone
    import psycopg
    from daon_user_api.cloud_storage import PostgresCloudStore
    from daon_user_api.notebook import NotebookContext
    from daon_user_api.notebook_postgres import PostgresNotebookRepository
    from daon_user_api.question_answering import TextModelSelection,GroundedTextResult
    from daon_user_api.question_answering_service import QuestionInputSource
    from daon_user_api.question_answering_postgres import PostgresQuestionAnsweringRepository,QuestionContext
    from daon_user_api.studio_workspace_postgres import PostgresStudioWorkspaceRepository as Canon
    scope=_existing_0069_studio_case()
    ctx=scope.context
    store=PostgresCloudStore(scope.app,min_size=0,max_size=2,notification_inbox_enabled=True)
    try:
        raw_pair=_l2b_bound_pdf(scope,store) if mode=='mixed' else None
        notebook_repository=PostgresNotebookRepository(store)
        notebook_context=NotebookContext(ctx.tenant_id,ctx.workspace_id,ctx.actor_id,ctx.trace_id,ctx.policy_version)
        notebook,replayed=notebook_repository.create(notebook_context,title='S42 synthetic question context',
            description=None,idempotency_key='s42l2b-context-notebook-'+scope.suffix,request_fingerprint='c'*64,
            now=datetime.now(timezone.utc))
        assert replayed is False
        scope.context=ctx=replace(ctx,notebook_id=notebook.notebook_id)
        items=[]
        if raw_pair is not None:
            raw_source,raw_version=raw_pair
            assert notebook_repository.bind_verified(notebook_context,ctx.notebook_id,binding_kind='source',
                record_id=raw_source,version_id=raw_version,now=datetime.now(timezone.utc)) is False
            items.append(QuestionInputSource('raw_source',raw_source,raw_source,raw_version))
        knowledge_snapshot=None
        knowledge_item=None
        if mode!='general_ungrounded':
            knowledge_scope='s42l2b-knowledge-scope-'+scope.suffix
            knowledge_snapshot='s42l2b-knowledge-snapshot-'+scope.suffix
            # 기존 QA SourceVersion을 참조만 한다. knowledge를 통과시키기 위한 Source/가짜 raw binding은 생성하지 않는다.
            with psycopg.connect(scope.admin) as connection:
                for name,value in (('tenant_id',ctx.tenant_id),('workspace_id',ctx.workspace_id),('actor_id',ctx.actor_id)):
                    connection.execute('SELECT set_config(%s,%s,true)',('app.'+name,value))
                knowledge_rows=connection.execute('SELECT source_id,record_id,digest_sha256 FROM source_versions '
                    'WHERE tenant_id=%s AND workspace_id=%s AND record_id=%s',
                    (ctx.tenant_id,ctx.workspace_id,'s24-source-version-'+scope.suffix)).fetchall()
                assert len(knowledge_rows)==1
                knowledge_source,knowledge_version,knowledge_digest=knowledge_rows[0]
                knowledge_item={'origin':'daon_knowledge','context_item_id':knowledge_snapshot,
                    'source_id':knowledge_source,'source_version_id':knowledge_version,'digest_sha256':knowledge_digest}
                payload={'mode':'daon_priority','source_version_ids':[knowledge_version],'items':[knowledge_item]}
                Canon._insert(connection,ctx,'knowledge_scopes',knowledge_scope,payload)
                Canon._insert(connection,ctx,'scope_snapshots',knowledge_snapshot,
                    {**payload,'knowledge_scope_id':knowledge_scope},extra_columns=('knowledge_scope_id',),extra_values=(knowledge_scope,))
            assert notebook_repository.bind_verified(notebook_context,ctx.notebook_id,binding_kind='knowledge_context',
                record_id=knowledge_snapshot,version_id=None,now=datetime.now(timezone.utc)) is False
            items.append(QuestionInputSource(**knowledge_item))
        context=QuestionContext(ctx.tenant_id,ctx.workspace_id,ctx.actor_id,ctx.trace_id,ctx.policy_version,
            notebook_id=ctx.notebook_id)
        run='s42l2b-context-run-'+scope.suffix
        selected=tuple(items)
        source=selected[0].source_id if selected else None
        version=selected[0].source_version_id if selected else None
        stored=PostgresQuestionAnsweringRepository(store,_L2bNoObjectRead()).persist_completed(context,
            run_id=run,source_id=source,source_version_id=version,question='S42 synthetic context question',
            selection=TextModelSelection('OLLAMA','https://s42-model.invalid','s42l2b-profile-'+scope.suffix,
                's42l2b-deployment-'+scope.suffix,'s42-synthetic-model',1,provider_kind='server_internal'),
            evidence=(),result=GroundedTextResult('',(),True,{}),provider_called=False,context_mode=mode,context_sources=selected)
        assert stored.run_id==run and stored.insufficient is True
        with psycopg.connect(scope.admin) as connection:
            rows=connection.execute('SELECT r.state,r.created_by,r.canonical_json->>\'context_mode\',s.canonical_json '
                'FROM runs r JOIN run_snapshots rs ON rs.tenant_id=r.tenant_id AND rs.workspace_id=r.workspace_id AND rs.run_id=r.record_id '
                'JOIN scope_snapshots s ON s.tenant_id=rs.tenant_id AND s.workspace_id=rs.workspace_id AND s.record_id=rs.scope_snapshot_id '
                'WHERE r.tenant_id=%s AND r.workspace_id=%s AND r.record_id=%s',(ctx.tenant_id,ctx.workspace_id,run)).fetchall()
            assert len(rows)==1 and rows[0][:3]==('completed',ctx.actor_id,mode)
            frozen=rows[0][3]
            expected_items=[{'origin':item.origin,'context_item_id':item.context_item_id,'source_id':item.source_id,
                'source_version_id':item.source_version_id,'digest_sha256':item.digest_sha256} for item in selected]
            assert frozen['items']==expected_items
            assert frozen['source_version_ids']==[item.source_version_id for item in selected]
            bindings=connection.execute('SELECT binding_kind,record_id,version_id FROM notebook_bindings '
                'WHERE tenant_id=%s AND workspace_id=%s AND notebook_id=%s',
                (ctx.tenant_id,ctx.workspace_id,ctx.notebook_id)).fetchall()
            if knowledge_snapshot is not None:
                assert ('knowledge_context',knowledge_snapshot,None) in bindings
                assert ('source',knowledge_item['source_id'],knowledge_item['source_version_id']) not in bindings
            assert sum(item[0]=='source' for item in bindings)==(1 if mode=='mixed' else 0)
            originals=connection.execute('SELECT event_id,actor_id FROM audit_events WHERE tenant_id=%s AND workspace_id=%s '
                "AND action='question.answer' AND target_type='Run' AND target_id=%s AND outcome='succeeded'",
                (ctx.tenant_id,ctx.workspace_id,run)).fetchall()
            assert len(originals)==1 and originals[0][1]==ctx.actor_id
            if not _l2b_assert_completed_source(connection,scope,origin=originals[0][0],store_name='question',
                    action='question.answer',target_type='Run',target_id=run):
                pytest.fail('QUESTION_CONTEXT_MODE_SOURCE_INTENT_MISSING; actual_business_and_origin_saved; mode='+mode,pytrace=False)
    finally:
        store.close()


@pytest.mark.parametrize('case',('source_acl_deny','wrong_notebook','snapshot_item_mismatch'))
def test_l2b_existing_0069_knowledge_notification_boundary_rejects_current_mismatch(case):
    """실제 Q 완료 뒤 알림 경계의 knowledge/raw deny·Notebook·불변 snapshot 항목을 따로 검사한다."""
    from dataclasses import replace
    from datetime import datetime,timezone
    from unittest.mock import patch
    import psycopg
    from daon_user_api.cloud_storage import PostgresCloudStore
    from daon_user_api.notebook import NotebookContext
    from daon_user_api.notebook_postgres import PostgresNotebookRepository
    from daon_user_api.question_answering import TextModelSelection,GroundedTextResult
    from daon_user_api.question_answering_service import QuestionInputSource
    from daon_user_api.question_answering_postgres import PostgresQuestionAnsweringRepository,QuestionContext
    from daon_user_api.studio_workspace_postgres import PostgresStudioWorkspaceRepository as Canon
    from daon_user_api.notification_events import enqueue_completed_event
    scope=_existing_0069_studio_case()
    ctx=scope.context
    store=PostgresCloudStore(scope.app,min_size=0,max_size=2,notification_inbox_enabled=True)
    checked=[]
    accepted=[]
    try:
        raw_source,raw_version=_l2b_bound_pdf(scope,store)
        notebooks=PostgresNotebookRepository(store)
        notebook_context=NotebookContext(ctx.tenant_id,ctx.workspace_id,ctx.actor_id,ctx.trace_id,ctx.policy_version)
        notebook,replayed=notebooks.create(notebook_context,title='S42 synthetic knowledge boundary',description=None,
            idempotency_key='s42l2b-knowledge-boundary-'+scope.suffix,request_fingerprint='d'*64,now=datetime.now(timezone.utc))
        assert replayed is False
        scope.context=ctx=replace(ctx,notebook_id=notebook.notebook_id)
        assert notebooks.bind_verified(notebook_context,ctx.notebook_id,binding_kind='source',record_id=raw_source,
            version_id=raw_version,now=datetime.now(timezone.utc)) is False
        knowledge_scope='s42l2b-negative-scope-'+scope.suffix
        knowledge_snapshot='s42l2b-negative-snapshot-'+scope.suffix
        with psycopg.connect(scope.admin) as connection:
            for name,value in (('tenant_id',ctx.tenant_id),('workspace_id',ctx.workspace_id),('actor_id',ctx.actor_id)):
                connection.execute('SELECT set_config(%s,%s,true)',('app.'+name,value))
            knowledge_rows=connection.execute('SELECT source_id,record_id,digest_sha256 FROM source_versions '
                'WHERE tenant_id=%s AND workspace_id=%s AND record_id=%s',
                (ctx.tenant_id,ctx.workspace_id,'s24-source-version-'+scope.suffix)).fetchall()
            assert len(knowledge_rows)==1
            knowledge_source,knowledge_version,digest=knowledge_rows[0]
            item={'origin':'daon_knowledge','context_item_id':knowledge_snapshot,'source_id':knowledge_source,
                'source_version_id':knowledge_version,'digest_sha256':digest}
            reference_item=dict(item)
            if case=='snapshot_item_mismatch':
                # 원행을 UPDATE하지 않는다. 처음부터 다른 불변 항목을 가진 실제 snapshot을 준비한다.
                reference_item['digest_sha256']=('0' if digest[0]!='0' else '1')+digest[1:]
            payload={'mode':'daon_priority','source_version_ids':[knowledge_version],'items':[reference_item]}
            Canon._insert(connection,ctx,'knowledge_scopes',knowledge_scope,payload)
            Canon._insert(connection,ctx,'scope_snapshots',knowledge_snapshot,{**payload,'knowledge_scope_id':knowledge_scope},
                extra_columns=('knowledge_scope_id',),extra_values=(knowledge_scope,))
        assert notebooks.bind_verified(notebook_context,ctx.notebook_id,binding_kind='knowledge_context',
            record_id=knowledge_snapshot,version_id=None,now=datetime.now(timezone.utc)) is False
        target_notebook=ctx.notebook_id
        if case=='wrong_notebook':
            other,replayed=notebooks.create(notebook_context,title='S42 synthetic other Notebook',description=None,
                idempotency_key='s42l2b-other-knowledge-'+scope.suffix,request_fingerprint='e'*64,now=datetime.now(timezone.utc))
            assert replayed is False and other.notebook_id!=ctx.notebook_id
            target_notebook=other.notebook_id
        run='s42l2b-knowledge-negative-run-'+scope.suffix
        def inspect_after_real_completion(connection,context,**kwargs):
            _l2b_assert_uncommitted_completion(connection,scope,'question',run,kwargs['origin_event_id'])
            # 실제 완료·정식 원감사·원 Notebook 결속이 먼저 성공한 상태에서만 알림 접근을 검사한다.
            targets=(knowledge_version,raw_version) if case=='source_acl_deny' else (None,)
            for denied_version in targets:
                with connection.transaction(force_rollback=True):
                    if denied_version is not None:
                        connection.execute('INSERT INTO identity_auth_source_access '
                            '(tenant_id,workspace_id,user_id,source_version_id,allowed,version,updated_at) '
                            'VALUES(%s,%s,%s,%s,0,1,now())',(ctx.tenant_id,ctx.workspace_id,ctx.actor_id,denied_version))
                    result=enqueue_completed_event(connection,context,origin_event_id=kwargs['origin_event_id'],notebook_id=target_notebook)
                    assert result==0
                    with connection.transaction(force_rollback=True):
                        try:
                            connection.execute('SELECT public.enqueue_completed_notification(%s,%s)',
                                (kwargs['origin_event_id'],target_notebook))
                        except psycopg.Error as error:
                            assert error.sqlstate in {'42501','23514'}
                            checked.append(denied_version or case)
                        else:
                            accepted.append(denied_version or case)
            # 시험용 정책 주입은 savepoint로 복구한다. 업무/원감사 저장은 대체하지 않는다.
            return 0
        with patch('daon_user_api.notification_events.enqueue_completed_event',side_effect=inspect_after_real_completion):
            stored=PostgresQuestionAnsweringRepository(store,_L2bNoObjectRead()).persist_completed(
                QuestionContext(ctx.tenant_id,ctx.workspace_id,ctx.actor_id,ctx.trace_id,ctx.policy_version,notebook_id=ctx.notebook_id),
                run_id=run,source_id=raw_source,source_version_id=raw_version,question='S42 synthetic knowledge boundary',
                selection=TextModelSelection('OLLAMA','https://s42-model.invalid','s42l2b-profile-'+scope.suffix,
                    's42l2b-deployment-'+scope.suffix,'s42-synthetic-model',1,provider_kind='server_internal'),
                evidence=(),result=GroundedTextResult('',(),True,{}),provider_called=False,context_mode='mixed',
                context_sources=(QuestionInputSource('raw_source',raw_source,raw_source,raw_version),QuestionInputSource(**item)))
        assert stored.run_id==run
        assert len(checked)==(2 if case=='source_acl_deny' else 1) and accepted==[]
        with psycopg.connect(scope.admin) as connection:
            assert connection.execute('SELECT r.state FROM runs r JOIN run_results rr ON rr.tenant_id=r.tenant_id '
                'AND rr.workspace_id=r.workspace_id AND rr.run_id=r.record_id WHERE r.tenant_id=%s AND r.workspace_id=%s AND r.record_id=%s',
                (ctx.tenant_id,ctx.workspace_id,run)).fetchall()==[('completed',)]
            assert connection.execute('SELECT count(*) FROM audit_events WHERE tenant_id=%s AND workspace_id=%s '
                "AND target_id=%s AND action='question.answer' AND outcome='succeeded'",
                (ctx.tenant_id,ctx.workspace_id,run)).fetchone()==(1,)
            assert connection.execute('SELECT 1 FROM notebook_bindings WHERE tenant_id=%s AND workspace_id=%s '
                "AND notebook_id=%s AND binding_kind='knowledge_context' AND record_id=%s",
                (ctx.tenant_id,ctx.workspace_id,ctx.notebook_id,knowledge_snapshot)).fetchone()==(1,)
            assert connection.execute('SELECT canonical_json->\'items\' FROM scope_snapshots WHERE tenant_id=%s '
                'AND workspace_id=%s AND record_id=%s',(ctx.tenant_id,ctx.workspace_id,knowledge_snapshot)).fetchone()==([reference_item],)
            for table in ('notification_source_outbox','notifications','notification_details','notification_audit_outbox'):
                assert connection.execute(f'SELECT count(*) FROM public.{table} WHERE tenant_id=%s',(ctx.tenant_id,)).fetchone()==(0,)
    finally:
        store.close()


def test_l2b_existing_0069_new_audit_cannot_republish_committed_question_run():
    """commit된 실제 완료 Run에 새 감사 ID만 붙여 현재 사건으로 재발행할 수 없다."""
    import hashlib
    import psycopg
    from daon_user_api.cloud_storage import PostgresCloudStore,CloudAccessContext
    scope=_existing_0069_studio_case()
    ctx=scope.context
    store=PostgresCloudStore(scope.app,min_size=0,max_size=2,notification_inbox_enabled=True)
    try:
        run,complete=_l2b_prepared_completion(scope,store,'question')
        complete()
        with psycopg.connect(scope.admin) as connection:
            originals=connection.execute('SELECT event_id FROM audit_events WHERE tenant_id=%s AND workspace_id=%s '
                "AND action='question.answer' AND target_type='Run' AND target_id=%s AND outcome='succeeded'",
                (ctx.tenant_id,ctx.workspace_id,run)).fetchall()
            assert len(originals)==1
            origin=originals[0][0]
            assert origin=='audit-'+hashlib.sha256(run.encode('utf-8')).hexdigest()[:32]
            assert connection.execute('SELECT origin_event_id,source_store,recipient_id,state FROM notification_source_outbox '
                'WHERE tenant_id=%s AND workspace_id=%s',(ctx.tenant_id,ctx.workspace_id)).fetchall()==[
                    (origin,'question',ctx.actor_id,'pending')]
            assert connection.execute('SELECT state FROM runs WHERE tenant_id=%s AND workspace_id=%s AND record_id=%s',
                (ctx.tenant_id,ctx.workspace_id,run)).fetchone()==('completed',)
        before=_l2b_completion_snapshot(scope)
        copied='s42l2b-copied-audit-'+scope.suffix
        assert copied!=origin
        denied=[]
        accepted=[]
        try:
            with store._transaction(CloudAccessContext(ctx.tenant_id,ctx.workspace_id,ctx.actor_id,'view')) as connection:
                with connection.transaction(force_rollback=True):
                    assert connection.execute('SELECT current_user').fetchone()==('daon_app',)
                    inserted=connection.execute('INSERT INTO audit_events(event_id,tenant_id,workspace_id,actor_id,action,'
                        'target_type,target_id,outcome,trace_id,policy_version,before_value,after_value,metadata,occurred_at) '
                        'SELECT %s,tenant_id,workspace_id,actor_id,action,target_type,target_id,outcome,trace_id,policy_version,'
                        'before_value,after_value,metadata,occurred_at FROM audit_events '
                        'WHERE tenant_id=%s AND workspace_id=%s AND event_id=%s RETURNING event_id',
                        (copied,ctx.tenant_id,ctx.workspace_id,origin)).fetchall()
                    assert inserted==[(copied,)]
                    # 원업무는 이미 commit됐다. 새 감사행의 xmin만 현재 transaction에 속한다.
                    assert connection.execute('SELECT r.xmin<>a.xmin FROM runs r JOIN audit_events a '
                        'ON a.tenant_id=r.tenant_id AND a.workspace_id=r.workspace_id AND a.target_id=r.record_id '
                        'WHERE r.tenant_id=%s AND r.workspace_id=%s AND r.record_id=%s AND a.event_id=%s',
                        (ctx.tenant_id,ctx.workspace_id,run,copied)).fetchone()==(True,)
                    try:
                        result=connection.execute('SELECT public.enqueue_completed_notification(%s,%s)',
                            (copied,ctx.notebook_id)).fetchone()[0]
                    except psycopg.Error as error:
                        assert error.sqlstate in {'42501','23514'}
                        denied.append(error.sqlstate)
                    else:
                        accepted.append(result)
        finally:
            # 반례가 허용되는 RED여도 복사 감사와 추가 source는 전부 rollback해야 한다.
            assert _l2b_completion_snapshot(scope)==before
        if not denied or accepted:
            pytest.fail('COMMITTED_QUESTION_RUN_REPUBLISHED_WITH_NEW_AUDIT_ID; direct_app_function_not_rejected',pytrace=False)
    finally:
        store.close()


def test_l2a_existing_0069_new_audit_cannot_republish_committed_studio_record():
    """commit된 실제 Studio 전달 기록도 새 감사 ID만 붙여 다시 발행할 수 없다."""
    import psycopg
    from daon_user_api.cloud_storage import PostgresCloudStore,CloudAccessContext
    scope=_pending_l2a_delivery()
    ctx=scope.context
    with psycopg.connect(scope.admin) as connection:
        assert connection.execute('SELECT a.actor_id,a.action,a.target_type,a.target_id FROM audit_events a '
            'JOIN deliveries d ON d.tenant_id=a.tenant_id AND d.workspace_id=a.workspace_id AND d.record_id=a.target_id '
            "WHERE a.tenant_id=%s AND a.workspace_id=%s AND a.event_id=%s AND a.outcome='succeeded'",
            (ctx.tenant_id,ctx.workspace_id,scope.origin_event_id)).fetchall()==[
                (ctx.actor_id,'studio.delivery','deliveries',scope.delivery_id)]
        assert connection.execute('SELECT origin_event_id,source_store,recipient_id,state FROM notification_source_outbox '
            'WHERE tenant_id=%s AND workspace_id=%s',(ctx.tenant_id,ctx.workspace_id)).fetchall()==[
                (scope.origin_event_id,'studio',ctx.actor_id,'pending')]
    before=_l2b_completion_snapshot(scope)
    copied='s42l2a-copied-audit-'+scope.suffix
    assert copied!=scope.origin_event_id
    denied=[]
    accepted=[]
    store=PostgresCloudStore(scope.app,min_size=0,max_size=2,notification_inbox_enabled=True)
    try:
        try:
            with store._transaction(CloudAccessContext(ctx.tenant_id,ctx.workspace_id,ctx.actor_id,'view')) as connection:
                with connection.transaction(force_rollback=True):
                    assert connection.execute('SELECT current_user').fetchone()==('daon_app',)
                    inserted=connection.execute('INSERT INTO audit_events(event_id,tenant_id,workspace_id,actor_id,action,'
                        'target_type,target_id,outcome,trace_id,policy_version,before_value,after_value,metadata,occurred_at) '
                        'SELECT %s,tenant_id,workspace_id,actor_id,action,target_type,target_id,outcome,trace_id,policy_version,'
                        'before_value,after_value,metadata,occurred_at FROM audit_events '
                        'WHERE tenant_id=%s AND workspace_id=%s AND event_id=%s RETURNING event_id',
                        (copied,ctx.tenant_id,ctx.workspace_id,scope.origin_event_id)).fetchall()
                    assert inserted==[(copied,)]
                    assert connection.execute('SELECT d.xmin<>a.xmin FROM deliveries d JOIN audit_events a '
                        'ON a.tenant_id=d.tenant_id AND a.workspace_id=d.workspace_id AND a.target_id=d.record_id '
                        'WHERE d.tenant_id=%s AND d.workspace_id=%s AND d.record_id=%s AND a.event_id=%s',
                        (ctx.tenant_id,ctx.workspace_id,scope.delivery_id,copied)).fetchone()==(True,)
                    try:
                        result=connection.execute('SELECT public.enqueue_studio_notification(%s,%s,%s)',
                            (copied,ctx.notebook_id,ctx.actor_id)).fetchone()[0]
                    except psycopg.Error as error:
                        assert error.sqlstate in {'42501','23514'}
                        denied.append(error.sqlstate)
                    else:
                        accepted.append(result)
        finally:
            assert _l2b_completion_snapshot(scope)==before
        if not denied or accepted:
            pytest.fail('COMMITTED_STUDIO_RECORD_REPUBLISHED_WITH_NEW_AUDIT_ID; direct_app_function_not_rejected',pytrace=False)
    finally:
        store.close()


def test_l2a_existing_0069_copied_receipt_key_cannot_republish_current_studio_record():
    """현재 Tx의 진짜 기록도 다른 receipt key와 정식 감사 hash를 복사해 중복 발행할 수 없다."""
    import hashlib
    from unittest.mock import patch
    import psycopg
    from daon_user_api.cloud_storage import PostgresCloudStore
    from daon_user_api.studio_workspace_postgres import PostgresStudioWorkspaceRepository
    from daon_user_api.notification_events import enqueue_studio_event
    scope=_existing_0069_studio_case()
    ctx=scope.context
    key='s42l2a-real-record-key'
    copied_key='s42l2a-copied-receipt-key'
    operation='studio.delivery'
    copied_hash=hashlib.sha256('|'.join((ctx.tenant_id,ctx.workspace_id,ctx.actor_id,operation,copied_key)).encode()).hexdigest()[:32]
    copied_audit='audit-'+copied_hash
    denied=[]
    accepted=[]
    reached=[]
    before=_l2b_completion_snapshot(scope)
    store=PostgresCloudStore(scope.app,min_size=0,max_size=2,notification_inbox_enabled=True)
    def actual_enqueue_then_probe(connection,context,**kwargs):
        assert enqueue_studio_event(connection,context,**kwargs)==1
        reached.append('real_source_inserted')
        origin=kwargs['origin_event_id']
        target=kwargs['target_id']
        assert target!='delivery-'+copied_hash and origin!=copied_audit
        with connection.transaction(force_rollback=True):
            receipts=connection.execute('INSERT INTO idempotency_records(tenant_id,workspace_id,actor_id,operation,'
                'idempotency_key,request_fingerprint,result,status,expires_at) '
                'SELECT tenant_id,workspace_id,actor_id,operation,%s,request_fingerprint,result,status,expires_at '
                'FROM idempotency_records WHERE tenant_id=%s AND workspace_id=%s AND actor_id=%s '
                "AND operation='studio.delivery' AND idempotency_key=%s RETURNING result->>'record_id'",
                (copied_key,ctx.tenant_id,ctx.workspace_id,ctx.actor_id,key)).fetchall()
            assert receipts==[(target,)]
            audits=connection.execute('INSERT INTO audit_events(event_id,tenant_id,workspace_id,actor_id,action,'
                'target_type,target_id,outcome,trace_id,policy_version,before_value,after_value,metadata,occurred_at) '
                'SELECT %s,tenant_id,workspace_id,actor_id,action,target_type,target_id,outcome,trace_id,policy_version,'
                'before_value,after_value,metadata,occurred_at FROM audit_events '
                'WHERE tenant_id=%s AND workspace_id=%s AND event_id=%s RETURNING event_id',
                (copied_audit,ctx.tenant_id,ctx.workspace_id,origin)).fetchall()
            assert audits==[(copied_audit,)]
            assert connection.execute('SELECT current_user').fetchone()==('daon_app',)
            try:
                result=connection.execute('SELECT public.enqueue_studio_notification(%s,%s,%s)',
                    (copied_audit,ctx.notebook_id,ctx.actor_id)).fetchone()[0]
            except psycopg.Error as error:
                assert error.sqlstate in {'42501','23514'}
                denied.append(error.sqlstate)
            else:
                accepted.append(result)
        # 직접 호출이 허용되는 결함이어도 정상 업무/source를 포함하여 이 시험 scope의 쓰기를 되돌린다.
        raise RuntimeError('S42 복사 receipt 반례 종료')
    try:
        with patch('daon_user_api.notification_events.enqueue_studio_event',side_effect=actual_enqueue_then_probe):
            with pytest.raises(RuntimeError,match='S42 복사 receipt 반례 종료'):
                PostgresStudioWorkspaceRepository(store).record_action(ctx,'delivery',
                    {'output_version_id':scope.version,'approval_id':scope.approval,'recipient':'workspace_recipient'},key)
        assert _l2b_completion_snapshot(scope)==before
        assert reached==['real_source_inserted']
        if not denied or accepted:
            pytest.fail('CURRENT_STUDIO_RECORD_REPUBLISHED_WITH_COPIED_RECEIPT_KEY; target_scope_hash_not_bound',pytrace=False)
    finally:
        store.close()


def _l2b_assert_materialized_completion(domain):
    """실제 완료 의도를 실제 소비한 후 새 저장소에서 읽는다. Studio 사건으로 치환하지 않는다."""
    import psycopg
    import daon_user_api.notification_events as events
    from daon_user_api.cloud_storage import PostgresCloudStore,CloudAccessContext
    from daon_user_api.notification_postgres import PostgresNotificationRepository
    scope=_existing_0069_studio_case()
    ctx=scope.context
    store=PostgresCloudStore(scope.app,min_size=0,max_size=2,notification_inbox_enabled=True)
    try:
        run,complete=_l2b_prepared_completion(scope,store,domain)
        complete()
    finally:
        store.close()
    with psycopg.connect(scope.admin) as connection:
        sources=connection.execute('SELECT s.source_event_id,s.origin_event_id,s.source_store,s.recipient_id,'
            's.action,s.target_type,s.target_id,s.kind,s.notebook_id,s.state,s.version,s.delivered_at,s.occurred_at '
            'FROM notification_source_outbox s JOIN audit_events a ON a.tenant_id=s.tenant_id '
            'AND a.workspace_id=s.workspace_id AND a.event_id=s.origin_event_id '
            "WHERE s.tenant_id=%s AND s.workspace_id=%s AND a.outcome='succeeded'",
            (ctx.tenant_id,ctx.workspace_id)).fetchall()
        assert len(sources)==1
        source=sources[0]
        action,target_type=('canon.transition','ProcessingRun') if domain=='document' else ('question.answer','Run')
        assert source[2:12]==(domain,ctx.actor_id,action,target_type,run,'run_state',ctx.notebook_id,'pending',1,None)
        for table in ('notifications','notification_details','notification_audit_outbox','security_audit_events'):
            assert connection.execute(f'SELECT count(*) FROM public.{table} WHERE tenant_id=%s',(ctx.tenant_id,)).fetchone()==(0,)
        if domain=='document':
            version=connection.execute('SELECT source_version_id FROM processing_runs WHERE tenant_id=%s '
                'AND workspace_id=%s AND record_id=%s',(ctx.tenant_id,ctx.workspace_id,run)).fetchone()[0]
            link=f'/notebooks/{ctx.notebook_id}?source_version_id={version}'
            title,summary='문서 처리 검토 필요','문서 처리 Run이 완료되었으며 검토가 필요합니다.'
        else:
            link=f'/notebooks/{ctx.notebook_id}?run_id={run}'
            title,summary='질문 처리 완료','질문 처리 Run이 완료되었으며 근거가 부족합니다.'
    # 부재/미구현이면 이 실제 source 준비 뒤에만 실패한다. 대역 publish나 Studio 소비를 사용하지 않는다.
    assert events.materialize_pending_completed(scope.app,context=ctx,limit=100)==1
    with psycopg.connect(scope.admin) as connection:
        rows=connection.execute('SELECT n.notification_id,n.recipient_id,n.source_event_id,n.version,n.read_at,'
            'd.source_store,d.origin_event_id,d.resource_type,d.resource_id,d.notebook_id,d.deep_link,d.title,d.summary,'
            'd.kind,d.severity,d.trace_id,d.policy_version,d.delivery_state '
            'FROM notifications n JOIN notification_details d ON d.tenant_id=n.tenant_id AND d.notification_id=n.notification_id '
            'WHERE n.tenant_id=%s AND n.workspace_id=%s',(ctx.tenant_id,ctx.workspace_id)).fetchall()
        assert len(rows)==1
        item=rows[0]
        assert item[1:]==(ctx.actor_id,source[0],1,None,domain,source[1],target_type,run,ctx.notebook_id,link,
            title,summary,'run_state','info',ctx.trace_id,ctx.policy_version,'delivered')
        audits=connection.execute('SELECT event_id,action,state,version,draft FROM notification_audit_outbox '
            'WHERE tenant_id=%s AND workspace_id=%s',(ctx.tenant_id,ctx.workspace_id)).fetchall()
        assert len(audits)==1 and audits[0][1:4]==('notification.created','pending',1)
        draft=audits[0][4]
        assert (draft['tenant_id'],draft['workspace_id'],draft['target_type'],draft['target_id'],draft['trace_id'],
            draft['policy_version'],draft['metadata'])==(
                ctx.tenant_id,ctx.workspace_id,'notification',item[0],ctx.trace_id,ctx.policy_version,{'source_event_id':source[0]})
        lineage=connection.execute('SELECT s.state,s.version,n.created_at,s.delivered_at,'
            "(a.draft->>'occurred_at')::timestamptz,s.occurred_at,o.occurred_at,"
            'n.xmin=d.xmin AND d.xmin=s.xmin AND s.xmin=a.xmin '
            'FROM notifications n JOIN notification_details d ON d.tenant_id=n.tenant_id AND d.notification_id=n.notification_id '
            'JOIN notification_source_outbox s ON s.tenant_id=n.tenant_id AND s.workspace_id=n.workspace_id '
            'AND s.source_event_id=n.source_event_id AND s.recipient_id=n.recipient_id '
            'JOIN notification_audit_outbox a ON a.tenant_id=n.tenant_id AND a.notification_id=n.notification_id '
            'JOIN audit_events o ON o.tenant_id=s.tenant_id AND o.workspace_id=s.workspace_id AND o.event_id=s.origin_event_id '
            'WHERE n.tenant_id=%s AND n.notification_id=%s',(ctx.tenant_id,item[0])).fetchall()
        assert len(lineage)==1
        state,version,created,delivered,audit_time,source_time,origin_time,same_tx=lineage[0]
        assert (state,version,same_tx)==('delivered',2,True)
        assert created>=delivered and audit_time==created
        assert source_time==origin_time==source[12]
        # 알림 감사의도는 소비 transaction 안에, 실제 중앙 전달은 commit 후 별도 경계다.
        assert connection.execute('SELECT count(*) FROM security_audit_events WHERE tenant_id=%s',(ctx.tenant_id,)).fetchone()==(0,)
    before_replay=_l2b_completion_snapshot(scope)
    # 새 저장소 객체가 여는 실제 새 connection에서 같은 지속 알림/상세를 읽는다. 새 process 증거는 아니다.
    repository=PostgresNotificationRepository(scope.app,context=CloudAccessContext(ctx.tenant_id,ctx.workspace_id,ctx.actor_id,'view'))
    reopened=repository.notifications_for(tenant_id=ctx.tenant_id,recipient_id=ctx.actor_id)
    assert len(reopened)==1
    notification=reopened[0]
    assert (notification.notification_id,notification.recipient_id,notification.resource_type,notification.resource_id,
        notification.deep_link,notification.title,notification.summary,notification.audit_event_id,notification.version,
        notification.read_at)==(item[0],ctx.actor_id,target_type,run,link,title,summary,audits[0][0],1,None)
    assert repository.notification_for(tenant_id=ctx.tenant_id,recipient_id=ctx.actor_id,notification_id=item[0])==notification
    assert events.materialize_pending_completed(scope.app,context=ctx,limit=100)==0
    assert _l2b_completion_snapshot(scope)==before_replay


def test_l2b_existing_0069_document_completed_materializes_and_reopens_without_duplicate():
    """문서 검토 필요 완료의 실제 pending 의도→자료 결속 알림→새 연결 재조회·중복0을 요구한다."""
    _l2b_assert_materialized_completion('document')


def test_l2b_existing_0069_question_completed_materializes_and_reopens_without_duplicate():
    """질문 insufficient 완료의 실제 pending 의도→Run 결속 알림→새 연결 재조회·중복0을 요구한다."""
    _l2b_assert_materialized_completion('question')


def _pending_l2b_completed(domain, *, scope=None):
    """실제 완료 원장/정식 감사/source를 먼저 commit한다. 소비나 알림 발행을 대체하지 않는다."""
    import psycopg
    from daon_user_api.cloud_storage import PostgresCloudStore
    scope=scope if scope is not None else _existing_0069_studio_case()
    ctx=scope.context
    store=PostgresCloudStore(scope.app,min_size=0,max_size=2,notification_inbox_enabled=True)
    try:
        scope.completed_run,complete=_l2b_prepared_completion(scope,store,domain)
        complete()
    finally:
        store.close()
    with psycopg.connect(scope.admin) as connection:
        rows=connection.execute('SELECT source_event_id,origin_event_id,source_store,recipient_id,state,version,delivered_at '
            'FROM notification_source_outbox WHERE tenant_id=%s AND workspace_id=%s',
            (ctx.tenant_id,ctx.workspace_id)).fetchall()
        assert len(rows)==1 and rows[0][2:]==(domain,ctx.actor_id,'pending',1,None)
        scope.source_event_id,scope.origin_event_id=rows[0][:2]
        if domain=='document':
            query='SELECT source_version_id FROM processing_runs WHERE tenant_id=%s AND workspace_id=%s AND record_id=%s'
        else:
            query="SELECT canonical_json->>'source_version_id' FROM runs WHERE tenant_id=%s AND workspace_id=%s AND record_id=%s"
        scope.completed_source_version=connection.execute(query,(ctx.tenant_id,ctx.workspace_id,scope.completed_run)).fetchone()[0]
        assert scope.completed_source_version is not None
        for table in ('notifications','notification_details','notification_audit_outbox'):
            assert connection.execute(f'SELECT count(*) FROM {table} WHERE tenant_id=%s',(ctx.tenant_id,)).fetchone()==(0,)
    return scope


def test_l2b_existing_0069_completed_consumer_sql_failure_after_detail_keeps_pending():
    """실제 detail/created 의도가 생긴 뒤 같은 소비 connection의 SQL 오류는 모두 rollback한다."""
    from unittest.mock import patch
    import psycopg
    import daon_user_api.notification_events as events
    from daon_user_api.notification import NotificationError
    scope=_pending_l2b_completed('document')
    ctx=scope.context
    before=_l2b_completion_snapshot(scope)
    real_connect=psycopg.connect
    phases=[]
    faults=[]
    @contextmanager
    def observed_connect(*args,**kwargs):
        with real_connect(*args,**kwargs) as actual:
            class ObservedConnection:
                def __getattr__(self,name):
                    return getattr(actual,name)
                def execute(self,query,params=None):
                    cursor=actual.execute(query,params)
                    if query=='SELECT public.materialize_completed_notification(%s,%s)':
                        parents=actual.execute('SELECT n.notification_id,d.resource_id FROM notifications n JOIN notification_details d '
                            'ON d.tenant_id=n.tenant_id AND d.notification_id=n.notification_id '
                            'WHERE n.tenant_id=%s AND n.workspace_id=%s AND n.recipient_id=%s',
                            (ctx.tenant_id,ctx.workspace_id,ctx.actor_id)).fetchall()
                        assert len(parents)==1 and parents[0][1]==scope.completed_run
                        audits=actual.execute("SELECT draft->>'action',draft->>'target_id' FROM public.pending_notification_audits(100)").fetchall()
                        assert audits==[('notification.created',parents[0][0])]
                        phases.append('ACTUAL_DETAIL_AND_CREATED_INTENT')
                        try:
                            actual.execute('SELECT 1/0')
                        except psycopg.Error as error:
                            faults.append(error.sqlstate)
                            raise
                    return cursor
            yield ObservedConnection()
    with patch('psycopg.connect',new=observed_connect):
        with pytest.raises(NotificationError) as caught:
            events.materialize_pending_completed(scope.app,context=ctx,limit=100)
    assert (caught.value.code,caught.value.http_status)==('DATABASE_UNAVAILABLE',503)
    assert phases==['ACTUAL_DETAIL_AND_CREATED_INTENT'] and faults==['22012']
    assert _l2b_completion_snapshot(scope)==before


def test_l2b_existing_0069_completed_consumer_current_source_deny_suppresses():
    """원업무 확정 후 실제 Source ACL 철회는 현재 소비에서 suppressed이며 알림 본문을 만들지 않는다."""
    import psycopg
    import daon_user_api.notification_events as events
    scope=_pending_l2b_completed('question')
    ctx=scope.context
    with psycopg.connect(scope.admin) as connection:
        connection.execute('INSERT INTO identity_auth_source_access '
            '(tenant_id,workspace_id,user_id,source_version_id,allowed,version,updated_at) '
            'VALUES(%s,%s,%s,%s,0,1,now())',(ctx.tenant_id,ctx.workspace_id,ctx.actor_id,scope.completed_source_version))
        source_before=connection.execute('SELECT to_jsonb(s) FROM notification_source_outbox s WHERE tenant_id=%s '
            'AND workspace_id=%s AND source_event_id=%s',(ctx.tenant_id,ctx.workspace_id,scope.source_event_id)).fetchone()[0]
    before=_l2b_completion_snapshot(scope)
    assert events.materialize_pending_completed(scope.app,context=ctx,limit=100)==0
    after=_l2b_completion_snapshot(scope)
    assert {key:value for key,value in after.items() if key!='notification_source_outbox'}=={
        key:value for key,value in before.items() if key!='notification_source_outbox'}
    with psycopg.connect(scope.admin) as connection:
        source_after=connection.execute('SELECT to_jsonb(s) FROM notification_source_outbox s WHERE tenant_id=%s '
            'AND workspace_id=%s AND source_event_id=%s',(ctx.tenant_id,ctx.workspace_id,scope.source_event_id)).fetchone()[0]
        assert (source_after['state'],source_after['version'])==('suppressed',2) and source_after['delivered_at'] is not None
        assert {key:value for key,value in source_before.items() if key not in {'state','version','delivered_at'}}=={
            key:value for key,value in source_after.items() if key not in {'state','version','delivered_at'}}
        for table in ('notifications','notification_details','notification_audit_outbox'):
            assert connection.execute(f'SELECT count(*) FROM {table} WHERE tenant_id=%s',(ctx.tenant_id,)).fetchone()==(0,)
    assert events.materialize_pending_completed(scope.app,context=ctx,limit=100)==0
    assert _l2b_completion_snapshot(scope)==after


def test_l2b_existing_0069_completed_direct_false_preserves_pending_and_private_acl():
    """현재 허용 source의 False 호출은 억제/ack하지 않고 private resolver는 app이 직접 실행할 수 없다."""
    import psycopg
    import daon_user_api.notification_events as events
    scope=_pending_l2b_completed('document')
    ctx=scope.context
    before=_l2b_completion_snapshot(scope)
    with psycopg.connect(scope.app,connect_timeout=3) as connection:
        for name,value in (('tenant_id',ctx.tenant_id),('workspace_id',ctx.workspace_id),('actor_id',ctx.actor_id),('capability','view')):
            connection.execute('SELECT set_config(%s,%s,true)',('app.'+name,value))
        assert connection.execute('SELECT current_user').fetchone()==('daon_app',)
        for _ in range(2):
            assert connection.execute('SELECT public.materialize_completed_notification(%s,false)',
                (scope.source_event_id,)).fetchone()==(None,)
        with connection.transaction(force_rollback=True):
            with pytest.raises(psycopg.Error) as denied:
                connection.execute('SELECT public.current_completed_notification_target(%s)',(scope.source_event_id,))
            assert denied.value.sqlstate=='42501'
        assert connection.execute('SELECT current_user').fetchone()==('daon_app',)
    assert _l2b_completion_snapshot(scope)==before
    # False가 정상 source를 영구 억제하지 않아 이후 실제 소비가 가능해야 한다.
    assert events.materialize_pending_completed(scope.app,context=ctx,limit=100)==1


def test_l2a_existing_0069_last_recipient_policy_sql_failure_cannot_report_success():
    """마지막 수신자의 enqueue 조회 실패가 권한 거부/성공 응답으로 바뀌면 안 된다."""
    from unittest.mock import patch
    import psycopg
    from daon_user_api.cloud_storage import PostgresCloudStore
    from daon_user_api.studio_workspace import StudioError
    from daon_user_api.studio_workspace_postgres import PostgresStudioWorkspaceRepository
    from daon_user_api.notification import NotificationError
    from daon_user_api.notification_events import enqueue_studio_event,make_studio_event,_can_receive_studio
    scope=_existing_0069_studio_case()
    ctx=scope.context
    faults=[]
    with pytest.MonkeyPatch.context() as environment:
        environment.setenv('DAON_OPEN_LAW_ENABLED','true')
        environment.setenv('DAON_OPEN_LAW_QA_WORKSPACE_ID',ctx.workspace_id)
        _studio_case_special_source(scope,'law')
        with psycopg.connect(scope.admin) as connection:
            assert connection.execute("SELECT m.user_id FROM identity_memberships m JOIN identity_users u ON u.user_id=m.user_id "
                "JOIN user_accounts a ON a.tenant_id=m.tenant_id AND a.user_id=m.user_id WHERE m.tenant_id=%s AND u.state='active'",
                (ctx.tenant_id,)).fetchall()==[(ctx.actor_id,)]
        before=_studio_case_counts(scope)
        store=PostgresCloudStore(scope.app,min_size=0,max_size=2,notification_inbox_enabled=True)
        def enqueue_with_sql_failure(connection,context,**event_args):
            event=make_studio_event(context,**event_args)
            # 기존 실제 자료/egress/권한 검사가 유효한 원업무 이후, enqueue 안에서만 오류를 주입한다.
            assert _can_receive_studio(connection,context,event,scope.version,ctx.actor_id)
            assert connection.execute('SELECT action,target_id FROM audit_events WHERE tenant_id=%s AND event_id=%s',
                (ctx.tenant_id,event.origin_event_id)).fetchone()==('studio.delivery',event.target_id)
            assert connection.execute("SELECT count(*) FROM idempotency_records WHERE tenant_id=%s AND workspace_id=%s "
                "AND actor_id=%s AND operation='studio.delivery' AND idempotency_key='s42-l2a-last-recipient-fault-key'",
                (ctx.tenant_id,ctx.workspace_id,ctx.actor_id)).fetchone()==(1,)
            def sql_failure(policy_connection,*_args,**_kwargs):
                assert policy_connection is connection
                try:
                    policy_connection.execute('SELECT 1/0')
                except psycopg.Error as error:
                    faults.append((error.sqlstate,policy_connection.info.transaction_status.name))
                    raise
            with patch('daon_user_api.egress_policy_postgres.PostgresEgressPolicyRepository._select_current',side_effect=sql_failure):
                return enqueue_studio_event(connection,context,**event_args)
        try:
            repo=PostgresStudioWorkspaceRepository(store)
            try:
                with patch('daon_user_api.notification_events.enqueue_studio_event',side_effect=enqueue_with_sql_failure):
                    with pytest.raises((StudioError,NotificationError)) as failed:
                        repo.record_action(ctx,'delivery',{'output_version_id':scope.version,'approval_id':scope.approval,
                            'recipient':'workspace_recipient'},'s42-l2a-last-recipient-fault-key')
                    assert (failed.value.code,failed.value.status) in {
                        ('STUDIO_DATABASE_UNAVAILABLE',503),('DATABASE_UNAVAILABLE',503)}
            finally:
                # 성공 반환/DID NOT RAISE여도 실제 SQL 실패 지점과 전체 rollback 여부를 먼저 회수한다.
                assert faults==[('22012','INERROR')]
                assert _studio_case_counts(scope)==before
                with psycopg.connect(scope.admin) as connection:
                    assert connection.execute('SELECT state FROM output_versions WHERE tenant_id=%s AND workspace_id=%s AND record_id=%s',
                        (ctx.tenant_id,ctx.workspace_id,scope.version)).fetchone()==('approved',)
        finally:
            store.close()
