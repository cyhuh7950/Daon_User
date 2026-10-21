"""Immutable Notebook question admissions and scoped, read-only history.

This module does not create Runs or dispatch models. Cursors are selectors, never
access grants; every operation checks current Notebook permissions in its Tx.
"""
from __future__ import annotations

import base64
import hashlib
import json
import re
from datetime import datetime, timezone
from types import SimpleNamespace

from psycopg.types.json import Jsonb
from .cloud_storage import CloudDatabaseError
from .notebook import NotebookError, _SAFE_ID
from .question_answering_postgres import PostgresQuestionAnsweringRepository, QuestionContext, QuestionRepositoryError

_EVENTS = {
    'awaiting_authorization': '추가 인증을 기다리고 있습니다.',
    'failed': '질문을 완료하지 못했습니다.',
    'uncertain': '처리 결과를 확인해 주세요.',
    'no_knowledge': '질문을 확인할 자료가 선택되지 않았습니다. 자료를 선택한 뒤 다시 질문해 주세요.',
    'no_relevant_evidence': '현재 선택한 자료에서 질문을 확인할 근거를 찾지 못했습니다. 자료의 처리 상태와 질문 범위를 확인해 주세요.',
}
_FP = re.compile(r'sha256:[0-9a-f]{64}')


def grounding_notice_content(status):
    if status not in {"no_knowledge", "no_relevant_evidence"}:
        raise NotebookError("NOTEBOOK_TURN_INVALID")
    return _EVENTS[status], ("질문의 대상·기간·조건을 확인할 수 있는 자료를 선택해 주세요.",)


_DISPOSITIONS = frozenset({"execution_prepared","no_knowledge","no_relevant_evidence"})


def question_grounding_disposition(connection,context,notebook_id,*,run_id,request_fingerprint):
    """Exact read; caller must own the shared request lock before using as a claim."""
    turn_id=_turn_id(context,notebook_id,run_id)
    row=connection.execute('SELECT m.canonical_json,m.canonical_text,m.digest_sha256,m.created_by,m.conversation_id FROM messages m WHERE m.tenant_id=%s AND m.workspace_id=%s AND m.record_id=%s',
        (context.tenant_id,context.workspace_id,turn_id+'-grounding')).fetchone()
    if row is None:
        return None
    payload,text,digest,actor,parent=row
    disposition=payload.get('disposition')
    expected={'kind':'notebook_grounding_disposition','turn_id':turn_id,'run_id':run_id,
        'request_fingerprint':request_fingerprint,'disposition':disposition}
    if (actor!=context.actor_id or parent!=turn_id or disposition not in _DISPOSITIONS or payload!=expected
        or text!=_canonical(payload) or digest!=hashlib.sha256(text.encode('utf-8')).hexdigest()):
        raise NotebookError('NOTEBOOK_TURN_CONFLICT',409)
    return disposition


def _canonical(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(',', ':'), allow_nan=False)


def _id(value):
    if not isinstance(value, str) or _SAFE_ID.fullmatch(value) is None:
        raise NotebookError('NOTEBOOK_TURN_INVALID')
    return value


def _time(value):
    if not isinstance(value, datetime) or value.tzinfo is None or value.utcoffset() is None:
        raise NotebookError('NOTEBOOK_CURSOR_INVALID')
    return value.astimezone(timezone.utc)


def _scope(context, notebook_id):
    return [_id(context.tenant_id), _id(context.workspace_id), _id(notebook_id), _id(context.actor_id)]


def encode_cursor(context, notebook_id, submitted_at, turn_id):
    value = [1, _scope(context, notebook_id), _time(submitted_at).isoformat(), _id(turn_id)]
    return base64.urlsafe_b64encode(_canonical(value).encode()).rstrip(b'=').decode('ascii')


def decode_cursor(context, notebook_id, before):
    if before is None:
        return None
    try:
        if not isinstance(before, str) or not 1 <= len(before) <= 2048 or re.fullmatch(r'[A-Za-z0-9_-]+', before) is None:
            raise ValueError
        raw = base64.b64decode(before + '=' * (-len(before) % 4), altchars=b'-_', validate=True)
        value = json.loads(raw.decode('utf-8', errors='strict'))
        if (not isinstance(value, list) or len(value) != 4 or type(value[0]) is not int or value[0] != 1
                or value[1] != _scope(context, notebook_id) or not isinstance(value[2], str)):
            raise ValueError
        timestamp = _time(datetime.fromisoformat(value[2]))
        if encode_cursor(context, notebook_id, timestamp, value[3]) != before:
            raise ValueError
        return timestamp, value[3]
    except (ValueError, TypeError, UnicodeError, NotebookError):
        raise NotebookError('NOTEBOOK_CURSOR_INVALID') from None


def admission_payload(context, notebook_id, run_id, question, request_fingerprint, context_identifiers, client_request_id=None):
    _scope(context, notebook_id)
    _id(run_id)
    if (not isinstance(question, str) or not question.strip() or len(question) > 32768
            or not isinstance(request_fingerprint, str) or _FP.fullmatch(request_fingerprint) is None
            or type(context_identifiers) is not dict
            or set(context_identifiers) - {'source_id', 'source_version_id', 'context_mode', 'context_item_ids'}):
        raise NotebookError('NOTEBOOK_TURN_INVALID')
    metadata = {}
    for key, value in context_identifiers.items():
        if key == 'context_mode':
            if value not in {'raw_only', 'general_ungrounded', 'mixed', 'daon_priority'}:
                raise NotebookError('NOTEBOOK_TURN_INVALID')
        elif key == 'context_item_ids':
            if type(value) not in (list, tuple) or len(value) > 8:
                raise NotebookError('NOTEBOOK_TURN_INVALID')
            value = [_id(item) for item in value]
            if len(set(value)) != len(value):
                raise NotebookError('NOTEBOOK_TURN_INVALID')
        else:
            value = _id(value)
        metadata[key] = value
    if client_request_id is not None and (not isinstance(client_request_id,str) or re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9._:-]{7,127}',client_request_id) is None):
        raise NotebookError('NOTEBOOK_TURN_INVALID')
    payload = {'kind': 'notebook_question_turn', 'schema_version': 1,
            'notebook_id': notebook_id, 'actor_id': context.actor_id, 'run_id': run_id,
            'request_fingerprint': request_fingerprint, 'question': question, 'context_identifiers': metadata}
    if client_request_id is not None:
        payload['client_request_id']=client_request_id
    return payload


def _turn_id(context, notebook_id, run_id):
    return 'question-turn-' + hashlib.sha256(_canonical([*_scope(context, notebook_id), run_id]).encode()).hexdigest()[:32]


class PostgresNotebookConversationRepository:
    def __init__(self, owner):
        self.owner = owner
        self.store = owner._store

    @staticmethod
    def _guard(connection, context, notebook_id):
        scope = QuestionContext(context.tenant_id, context.workspace_id, context.actor_id,
                                context.trace_id, context.policy_version, notebook_id)
        try:
            PostgresQuestionAnsweringRepository._knowledge_selections(connection, scope, (), write=False)
        except QuestionRepositoryError as error:
            raise NotebookError('NOTEBOOK_UNAVAILABLE' if error.status >= 500 else 'NOTEBOOK_NOT_FOUND',
                                503 if error.status >= 500 else 404) from None

    @staticmethod
    def _insert_exact(connection, context, table, record_id, payload, *, now, conversation_id=None):
        if table not in {'conversations', 'messages'}:
            raise NotebookError('NOTEBOOK_TURN_INVALID')
        text = _canonical(payload)
        digest = hashlib.sha256(text.encode()).hexdigest()
        columns = 'tenant_id,workspace_id,record_id,aggregate_id,version,schema_version,canonical_json,canonical_text,digest_sha256,created_by,trace_id,created_at'
        values = [context.tenant_id, context.workspace_id, record_id, record_id, 1, 1,
                  Jsonb(payload), text, digest, context.actor_id, context.trace_id, now]
        if table == 'messages':
            columns += ',conversation_id'
            values.append(conversation_id)
        connection.execute(f'INSERT INTO {table} ({columns}) VALUES ({",".join(["%s"]*len(values))}) '
                           'ON CONFLICT (tenant_id,workspace_id,record_id) DO NOTHING', tuple(values))
        # Re-read after INSERT, including the conflicting committed row. Trace/time
        # belong to the first admission and deliberately differ across HTTP retries.
        row = connection.execute(f'SELECT canonical_json,canonical_text,digest_sha256,created_by,aggregate_id,version,schema_version'
                                 + (',conversation_id' if table == 'messages' else '')
                                 + f' FROM {table} WHERE tenant_id=%s AND workspace_id=%s AND record_id=%s',
                                 (context.tenant_id, context.workspace_id, record_id)).fetchone()
        expected = (payload, text, digest, context.actor_id, record_id, 1, 1)
        if row is None or tuple(row[:7]) != expected or (table == 'messages' and row[7] != conversation_id):
            raise NotebookError('NOTEBOOK_TURN_CONFLICT', 409)

    def admit_question_turn(self, context, notebook_id, *, run_id, question, request_fingerprint, context_identifiers, now, client_request_id=None):
        payload = admission_payload(context, notebook_id, run_id, question, request_fingerprint, context_identifiers, client_request_id)
        turn_id = _turn_id(context, notebook_id, run_id)
        try:
            with self.store._transaction(self.owner._context(context)) as connection:
                self._guard(connection, context, notebook_id)
                existing = connection.execute('SELECT r.created_by,r.canonical_json,r.conversation_id,EXISTS('
                    'SELECT 1 FROM notebook_bindings b WHERE b.tenant_id=r.tenant_id AND b.workspace_id=r.workspace_id '
                    "AND b.notebook_id=%s AND b.binding_kind='conversation_thread' AND b.record_id=r.conversation_id AND b.created_by=r.created_by) "
                    'FROM runs r WHERE r.tenant_id=%s AND r.workspace_id=%s AND r.record_id=%s',
                    (notebook_id,context.tenant_id,context.workspace_id,run_id)).fetchone()
                if existing is not None and (existing[0]!=context.actor_id or not existing[3]
                        or existing[1].get('request_fingerprint')!=request_fingerprint or existing[1].get('question')!=question):
                    raise NotebookError('NOTEBOOK_TURN_CONFLICT',409)
                self._insert_exact(connection, context, 'conversations', turn_id, payload, now=now)
                self._insert_exact(connection, context, 'messages', turn_id + '-user',
                    {'kind': 'notebook_question_user', 'turn_id': turn_id, 'run_id': run_id,
                     'request_fingerprint': request_fingerprint, 'text': question}, now=now, conversation_id=turn_id)
                connection.execute('INSERT INTO notebook_bindings (tenant_id,workspace_id,notebook_id,binding_kind,record_id,version_id,created_by,created_at) '
                    "VALUES (%s,%s,%s,'conversation_thread',%s,NULL,%s,%s) ON CONFLICT DO NOTHING",
                    (context.tenant_id, context.workspace_id, notebook_id, turn_id, context.actor_id, now))
                bound = connection.execute('SELECT created_by,version_id FROM notebook_bindings WHERE tenant_id=%s AND workspace_id=%s '
                    "AND notebook_id=%s AND binding_kind='conversation_thread' AND record_id=%s",
                    (context.tenant_id, context.workspace_id, notebook_id, turn_id)).fetchone()
                if bound != (context.actor_id, None):
                    raise NotebookError('NOTEBOOK_TURN_CONFLICT', 409)
            return turn_id
        except CloudDatabaseError:
            raise NotebookError('NOTEBOOK_UNAVAILABLE', 503) from None

    def load_question_turn_notice(self, context, notebook_id, *, run_id, request_fingerprint):
        turn_id = _turn_id(context,notebook_id,run_id)
        try:
            with self.store._transaction(self.owner._context(context)) as connection:
                self._guard(connection,context,notebook_id)
                parent = connection.execute('SELECT canonical_json,created_by FROM conversations WHERE tenant_id=%s AND workspace_id=%s AND record_id=%s',
                    (context.tenant_id,context.workspace_id,turn_id)).fetchone()
                if parent is None or parent[1]!=context.actor_id or parent[0].get('notebook_id')!=notebook_id or parent[0].get('run_id')!=run_id or parent[0].get('request_fingerprint')!=request_fingerprint:
                    raise NotebookError('NOTEBOOK_TURN_CONFLICT',409)
                status=question_grounding_disposition(connection,context,notebook_id,run_id=run_id,request_fingerprint=request_fingerprint)
                return status if status in {'no_knowledge','no_relevant_evidence'} else None
        except CloudDatabaseError:
            raise NotebookError('NOTEBOOK_UNAVAILABLE',503) from None

    def claim_question_grounding(self,context,notebook_id,*,run_id,request_fingerprint,disposition,now):
        if disposition not in _DISPOSITIONS:
            raise NotebookError('NOTEBOOK_TURN_INVALID')
        turn_id=_turn_id(context,notebook_id,run_id)
        try:
            with self.store._transaction(self.owner._context(context)) as connection:
                # Same order as Run admission: request lock before Notebook/source locks.
                connection.execute('SELECT pg_advisory_xact_lock(hashtextextended(%s,0))',
                    ('question-egress|'+context.tenant_id+'|'+context.workspace_id+'|'+run_id,))
                self._guard(connection,context,notebook_id)
                parent=connection.execute('SELECT canonical_json,created_by FROM conversations WHERE tenant_id=%s AND workspace_id=%s AND record_id=%s',
                    (context.tenant_id,context.workspace_id,turn_id)).fetchone()
                if parent is None or parent[1]!=context.actor_id or parent[0].get('notebook_id')!=notebook_id or parent[0].get('run_id')!=run_id or parent[0].get('request_fingerprint')!=request_fingerprint:
                    raise NotebookError('NOTEBOOK_TURN_CONFLICT',409)
                prior=question_grounding_disposition(connection,context,notebook_id,run_id=run_id,request_fingerprint=request_fingerprint)
                if prior is not None:
                    if prior=='execution_prepared' and disposition!='execution_prepared':
                        raise NotebookError('NOTEBOOK_TURN_CONFLICT',409)
                    return prior
                # A legacy/current real Run must never be overlaid with a notice.
                actual=connection.execute('SELECT canonical_json FROM runs WHERE tenant_id=%s AND workspace_id=%s AND record_id=%s',
                    (context.tenant_id,context.workspace_id,run_id)).fetchone()
                if actual is not None and disposition!='execution_prepared':
                    raise NotebookError('NOTEBOOK_TURN_CONFLICT',409)
                payload={'kind':'notebook_grounding_disposition','turn_id':turn_id,'run_id':run_id,
                    'request_fingerprint':request_fingerprint,'disposition':disposition}
                self._insert_exact(connection,context,'messages',turn_id+'-grounding',payload,now=now,conversation_id=turn_id)
                if disposition!='execution_prepared':
                    event={'kind':'notebook_question_event','turn_id':turn_id,'run_id':run_id,
                        'request_fingerprint':request_fingerprint,'event_kind':disposition,
                        'text':_EVENTS[disposition],'safe_error_code':None}
                    event_id=turn_id+'-'+hashlib.sha256(_canonical(event).encode()).hexdigest()[:16]
                    self._insert_exact(connection,context,'messages',event_id,event,now=now,conversation_id=turn_id)
                return disposition
        except CloudDatabaseError:
            raise NotebookError('NOTEBOOK_UNAVAILABLE',503) from None

    def append_question_turn_event(self, context, notebook_id, *, run_id, request_fingerprint, event_kind, text=None, safe_error_code=None, now):
        if event_kind not in _EVENTS or (text is not None and text != _EVENTS[event_kind]):
            raise NotebookError('NOTEBOOK_TURN_INVALID')
        if safe_error_code is not None and (not isinstance(safe_error_code, str) or re.fullmatch('[A-Z][A-Z0-9_]{0,95}', safe_error_code) is None):
            raise NotebookError('NOTEBOOK_TURN_INVALID')
        _id(run_id)
        if not isinstance(request_fingerprint, str) or _FP.fullmatch(request_fingerprint) is None:
            raise NotebookError('NOTEBOOK_TURN_INVALID')
        turn_id = _turn_id(context, notebook_id, run_id)
        payload = {'kind': 'notebook_question_event', 'turn_id': turn_id, 'run_id': run_id,
                   'request_fingerprint': request_fingerprint, 'event_kind': event_kind,
                   'text': _EVENTS[event_kind], 'safe_error_code': safe_error_code}
        event_id = turn_id + '-' + hashlib.sha256(_canonical(payload).encode()).hexdigest()[:16]
        try:
            with self.store._transaction(self.owner._context(context)) as connection:
                self._guard(connection, context, notebook_id)
                parent = connection.execute('SELECT canonical_json,created_by FROM conversations WHERE tenant_id=%s AND workspace_id=%s AND record_id=%s',
                    (context.tenant_id, context.workspace_id, turn_id)).fetchone()
                if (parent is None or parent[1] != context.actor_id or parent[0].get('run_id') != run_id
                        or parent[0].get('notebook_id') != notebook_id or parent[0].get('request_fingerprint') != request_fingerprint):
                    raise NotebookError('NOTEBOOK_TURN_CONFLICT', 409)
                self._insert_exact(connection, context, 'messages', event_id, payload, now=now, conversation_id=turn_id)
        except CloudDatabaseError:
            raise NotebookError('NOTEBOOK_UNAVAILABLE', 503) from None

    @staticmethod
    def _completed_response(connection, context, notebook_id, run_id):
        from .notification_events import _completed_event_access, _snapshot_read_scope
        from .notification import NotificationError
        scope=SimpleNamespace(tenant_id=context.tenant_id,workspace_id=context.workspace_id,actor_id=context.actor_id,
                              trace_id=context.trace_id,policy_version=context.policy_version,notebook_id=notebook_id)
        try:
            with _snapshot_read_scope(connection,scope):
                target=_completed_event_access(connection,scope,
                    origin_event_id=PostgresQuestionAnsweringRepository._opaque_id('audit',run_id),notebook_id=notebook_id)
            if target is None or target.actor_id!=context.actor_id:
                raise NotebookError('NOTEBOOK_NOT_FOUND',404)
        except NotificationError as error:
            raise NotebookError('NOTEBOOK_UNAVAILABLE' if error.http_status>=500 else 'NOTEBOOK_NOT_FOUND',
                                503 if error.http_status>=500 else 404) from None
        rows=connection.execute('SELECT rr.canonical_json,c.record_id,c.canonical_json FROM run_results rr '
            'LEFT JOIN citations c ON c.tenant_id=rr.tenant_id AND c.workspace_id=rr.workspace_id AND c.run_result_id=rr.record_id '
            'WHERE rr.tenant_id=%s AND rr.workspace_id=%s AND rr.run_id=%s ORDER BY c.record_id',
            (context.tenant_id,context.workspace_id,run_id)).fetchall()
        if not rows:
            raise NotebookError('NOTEBOOK_NOT_FOUND',404)
        payload=rows[0][0]
        citations=[]
        for _,citation_id,item in rows:
            if citation_id is None:
                continue
            citations.append({'citation_id':citation_id,'source_id':item['source_id'],'source_version_id':item['source_version_id'],
                'evidence_span_id':item['evidence_span_id'],'page':item['page'],'origin':item.get('origin','raw_source'),
                'context_item_id':item.get('context_item_id',item['source_version_id']),
                'locator':item.get('locator',{'kind':'page','value':str(item['page'])})})
        if (not isinstance(payload.get('answer'),str) or not 1<=len(payload['answer'])<=8000
                or type(payload.get('insufficient')) is not bool or len(citations)>10):
            raise NotebookError('NOTEBOOK_UNAVAILABLE',503)
        from .question_answering import stored_grounding_metadata
        try:
            _, actions = stored_grounding_metadata(payload,citation_count=len(citations))
        except ValueError:
            raise NotebookError('NOTEBOOK_UNAVAILABLE',503) from None
        return {'kind':'answer','text':payload['answer'],'insufficient':payload['insufficient'],'next_actions':list(actions),'citations':citations}

    def list_conversation_turns(self, context, notebook_id, *, limit=50, before=None):
        if type(limit) is not int or not 1 <= limit <= 100:
            raise NotebookError('NOTEBOOK_PAGE_INVALID')
        edge = decode_cursor(context, notebook_id, before)
        params = dict(tenant=context.tenant_id, workspace=context.workspace_id,
                      notebook=notebook_id, actor=context.actor_id, size=limit+1,
                      edge_time=None if edge is None else edge[0], edge_id=None if edge is None else edge[1])
        try:
            with self.store._transaction(self.owner._context(context)) as connection:
                self._guard(connection, context, notebook_id)
                # Page distinct turns before fetching any citations. Actual Runs
                # must have their own canonical conversation and Notebook binding.
                rows = connection.execute("""WITH actual_runs AS (
                  SELECT r.record_id,r.state,r.canonical_json,r.created_at,r.conversation_id
                  FROM runs r JOIN conversations c ON c.tenant_id=r.tenant_id AND c.workspace_id=r.workspace_id
                    AND c.record_id=r.conversation_id AND c.created_by=r.created_by
                  JOIN notebook_bindings b ON b.tenant_id=r.tenant_id AND b.workspace_id=r.workspace_id
                    AND b.notebook_id=%(notebook)s AND b.binding_kind='conversation_thread'
                    AND b.record_id=c.record_id AND b.created_by=r.created_by
                  WHERE r.tenant_id=%(tenant)s AND r.workspace_id=%(workspace)s AND r.created_by=%(actor)s
                    AND c.canonical_json->>'current_run_id'=r.record_id
                    AND r.canonical_json->>'request_fingerprint' ~ '^sha256:[0-9a-f]{64}$'
                ), admitted AS (
                  SELECT c.record_id AS turn_id,c.created_at,c.canonical_json AS admission,
                    r.record_id AS actual_run_id,r.state AS actual_state,r.canonical_json AS run_payload
                  FROM conversations c JOIN notebook_bindings b ON b.tenant_id=c.tenant_id AND b.workspace_id=c.workspace_id
                    AND b.notebook_id=%(notebook)s AND b.binding_kind='conversation_thread'
                    AND b.record_id=c.record_id AND b.created_by=c.created_by
                  JOIN messages m ON m.tenant_id=c.tenant_id AND m.workspace_id=c.workspace_id
                    AND m.conversation_id=c.record_id AND m.record_id=c.record_id||'-user' AND m.created_by=c.created_by
                    AND m.canonical_json->>'kind'='notebook_question_user'
                    AND m.canonical_json->>'text'=c.canonical_json->>'question'
                    AND m.canonical_json->>'request_fingerprint'=c.canonical_json->>'request_fingerprint'
                  LEFT JOIN actual_runs r ON r.record_id=c.canonical_json->>'run_id'
                    AND r.canonical_json->>'request_fingerprint'=c.canonical_json->>'request_fingerprint'
                    AND r.canonical_json->>'question'=c.canonical_json->>'question'
                  WHERE c.tenant_id=%(tenant)s AND c.workspace_id=%(workspace)s AND c.created_by=%(actor)s
                    AND c.canonical_json->>'kind'='notebook_question_turn'
                    AND c.canonical_json->>'notebook_id'=%(notebook)s AND c.canonical_json->>'actor_id'=%(actor)s
                ), turns AS (
                  SELECT * FROM admitted UNION ALL
                  SELECT r.record_id,r.created_at,NULL::jsonb,r.record_id,r.state,r.canonical_json FROM actual_runs r
                  WHERE r.state IN ('completed','failed') AND NOT EXISTS(
                    SELECT 1 FROM admitted a WHERE a.actual_run_id=r.record_id)
                ) SELECT turn_id,created_at,admission,actual_run_id,actual_state,run_payload FROM turns
                  WHERE (%(edge_time)s::timestamptz IS NULL OR (created_at,turn_id)<(%(edge_time)s::timestamptz,%(edge_id)s::text))
                  ORDER BY created_at DESC,turn_id DESC LIMIT %(size)s""", params).fetchall()
                items = []
                for turn_id, submitted_at, admission, actual_run_id, actual_state, run_payload in rows[:limit]:
                    if admission is not None:
                        expected = admission_payload(context, notebook_id, admission.get('run_id'), admission.get('question'),
                            admission.get('request_fingerprint'), admission.get('context_identifiers'), admission.get('client_request_id'))
                        if admission != expected or turn_id != _turn_id(context, notebook_id, admission['run_id']):
                            raise NotebookError('NOTEBOOK_UNAVAILABLE', 503)
                        question = admission['question']
                        event = connection.execute('SELECT canonical_json FROM messages WHERE tenant_id=%s AND workspace_id=%s '
                            "AND conversation_id=%s AND created_by=%s AND canonical_json->>'kind'='notebook_question_event' "
                            "ORDER BY created_at DESC,CASE canonical_json->>'event_kind' WHEN 'no_knowledge' THEN 4 WHEN 'no_relevant_evidence' THEN 4 WHEN 'uncertain' THEN 3 WHEN 'failed' THEN 2 ELSE 1 END DESC,record_id DESC LIMIT 1",
                            (context.tenant_id,context.workspace_id,turn_id,context.actor_id)).fetchone()
                        state = 'pending'
                        if event is not None:
                            data = event[0]
                            if (data.get('turn_id') != turn_id or data.get('run_id') != admission['run_id']
                                    or data.get('request_fingerprint') != admission['request_fingerprint']
                                    or data.get('event_kind') not in _EVENTS or data.get('text') != _EVENTS[data['event_kind']]):
                                raise NotebookError('NOTEBOOK_UNAVAILABLE',503)
                            state = data['event_kind']
                    else:
                        question = run_payload.get('question')
                        if not isinstance(question,str) or not question.strip() or len(question)>32768:
                            raise NotebookError('NOTEBOOK_UNAVAILABLE',503)
                        state = 'pending'
                    response = None
                    model = None
                    if actual_state == 'failed':
                        state = 'failed'
                    if actual_state == 'completed':
                        # Reuse the existing current Source/retention/Notebook
                        # access reader, never merely the stored answer payload.
                        try:
                            response = self._completed_response(connection,context,notebook_id,actual_run_id)
                            state = 'completed'
                            recorded = connection.execute("""SELECT a.canonical_json->>'provider_code',a.canonical_json->>'model_id'
                              FROM run_results rr JOIN model_attempts ma ON ma.tenant_id=rr.tenant_id AND ma.workspace_id=rr.workspace_id
                                AND ma.record_id=rr.selected_model_attempt_id AND ma.routing_decision_id=rr.routing_decision_id
                              JOIN routing_decisions d ON d.tenant_id=ma.tenant_id AND d.workspace_id=ma.workspace_id
                                AND d.record_id=ma.routing_decision_id AND d.run_id=rr.run_id
                              JOIN model_artifacts a ON a.tenant_id=ma.tenant_id AND a.workspace_id=ma.workspace_id AND a.record_id=ma.model_artifact_id
                              JOIN model_deployments md ON md.tenant_id=ma.tenant_id AND md.workspace_id=ma.workspace_id
                                AND md.record_id=ma.model_deployment_id AND md.model_artifact_id=a.record_id
                              WHERE rr.tenant_id=%s AND rr.workspace_id=%s AND rr.run_id=%s
                                AND ma.canonical_json->>'run_id'=rr.run_id AND ma.canonical_json->>'outcome'='succeeded'
                              ORDER BY rr.record_id LIMIT 2""", (context.tenant_id,context.workspace_id,actual_run_id)).fetchall()
                            if len(recorded)==1 and all(isinstance(x,str) and 1<=len(x)<=255 and not re.search(r'[\x00-\x20\x7f]',x) for x in recorded[0]):
                                model = {'provider_code':recorded[0][0],'model_id':recorded[0][1]}
                        except NotebookError as error:
                            if error.status != 404:
                                raise
                            state = 'unavailable'
                    if response is None and state != 'pending':
                        text = '답변에 현재 접근할 수 없습니다.' if state=='unavailable' else _EVENTS[state]
                        response = {'kind':'system','text':text,'insufficient':True,'next_actions':list(grounding_notice_content(state)[1]) if state in {'no_knowledge','no_relevant_evidence'} else [],'citations':[]}
                    items.append({'turn_id':turn_id,'question':question,'response':response,'state':state,
                                  'submitted_at':_time(submitted_at).isoformat().replace('+00:00','Z'),'model':model,
                                  'client_request_id':admission.get('client_request_id') if admission is not None else None})
                next_cursor = None
                if len(rows)>limit:
                    next_cursor=encode_cursor(context,notebook_id,rows[limit-1][1],rows[limit-1][0])
                return {'items':items,'next_cursor':next_cursor}
        except (CloudDatabaseError, QuestionRepositoryError):
            raise NotebookError('NOTEBOOK_UNAVAILABLE',503) from None
