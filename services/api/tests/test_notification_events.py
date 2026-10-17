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


def _l2b_bound_pdf(scope,store):
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
        assert activities==[('context_bound',ctx.actor_id)]
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


def _l2b_prepared_completion(scope,store,domain):
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
        result=replace(ready_result(),source_id=source,source_version_id=version,status='needs_review',
            conflict='UNDERSTANDING_PARSER_CONFLICT')
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


def _pending_l2b_completed(domain):
    """실제 완료 원장/정식 감사/source를 먼저 commit한다. 소비나 알림 발행을 대체하지 않는다."""
    import psycopg
    from daon_user_api.cloud_storage import PostgresCloudStore
    scope=_existing_0069_studio_case()
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
