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


def _existing_0069_studio_case(*, pending=False):
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
    scope.version,scope.approval,scope.original=seed_delivery(scope,pending=pending)
    with psycopg.connect(dsn) as connection:
        connection.execute('INSERT INTO identity_tenants(tenant_id) VALUES(%s)',(context.tenant_id,))
        connection.execute("INSERT INTO identity_memberships(tenant_id,user_id,role) VALUES(%s,%s,'personal_owner')",
            (context.tenant_id,context.actor_id))
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
                    record='s42l2a-savepoint-'+scope.suffix
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
