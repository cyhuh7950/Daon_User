"""동일 connection에서 웹 출처·원문/텍스트 결속·색인을 확정하는 저장 경계."""
from __future__ import annotations

from contextlib import contextmanager
from dataclasses import asdict
from datetime import datetime, timezone
import hashlib
import json
import time
from uuid import uuid4

import psycopg
from psycopg.types.json import Jsonb

from .data_canon import CanonicalContext, PostgresDataCanonStore, canonical_json_bytes
from .safe_http_fetch import normalize_public_url
from .website_source_import import (WebsiteImportError, WebsiteImportClaim, WebsiteImportResult,
    WebsiteSnapshotView, object_id, validate_page, _store_deadline)


def website_text_chunks(source_id, source_version_id, text):
    chunks=[]
    # 원문 code point를 그대로 보존하는 bounded verbatim 구간. 모델 의미 이해로 표시하지 않는다.
    for start in range(0,len(text),1024):
        content=text[start:start+1024]
        if not content.strip():
            continue
        cid='web-chunk-'+hashlib.sha256(f'{source_version_id}|{start}'.encode()).hexdigest()[:32]
        chunks.append({'chunk_id':cid,'source_id':source_id,'source_version_id':source_version_id,
            'page':1,'text':content,'evidence_span_id':'web-span-'+cid[10:],
            'evidence_kind':'text_source_verbatim_fact'})
    return chunks


class PostgresWebsiteSourceRepository:
    def __init__(self, dsn: str, *, canon_store=None, policy_fingerprint=None):
        self._dsn=dsn
        self._canon=canon_store or PostgresDataCanonStore(dsn)
        self._fingerprint=policy_fingerprint

    def close(self):
        self._canon.close()

    @contextmanager
    def _transaction(self, context, *, enforce_deadline=True):
        try:
            with psycopg.connect(self._dsn, connect_timeout=2) as conn:
                with conn.transaction():
                    conn.execute('SET LOCAL ROLE daon_app')
                    for name,value in [('tenant_id',context.tenant_id),('workspace_id',context.workspace_id),
                        ('actor_id',context.actor_id),('notebook_id',context.notebook_id),
                        ('capability','source.write'),('trace_id',context.trace_id)]:
                        conn.execute('SELECT set_config(%s,%s,true)',('app.'+name,value))
                    conn.execute("SET LOCAL statement_timeout='2000ms'")
                    yield conn
                    if enforce_deadline and _store_deadline.get() is not None and time.monotonic()>=_store_deadline.get():
                        raise WebsiteImportError('WEBSITE_FETCH_TIMEOUT',504)
        except WebsiteImportError:
            raise
        except psycopg.Error:
            raise WebsiteImportError('WEBSITE_STORAGE_UNAVAILABLE') from None

    @staticmethod
    def _scope(context):
        return (context.tenant_id,context.workspace_id,context.actor_id,context.notebook_id)

    def _policy_fingerprint(self,context):
        try:
            value=self._fingerprint(context) if callable(self._fingerprint) else None
            if not isinstance(value,str) or not value:
                raise ValueError
            return value
        except Exception:
            raise WebsiteImportError('WEBSITE_FETCH_BLOCKED',403) from None

    def _notebook(self, conn, context):
        row=conn.execute("SELECT n.notebook_id FROM notebooks n WHERE n.tenant_id=%s AND n.workspace_id=%s "
            "AND n.notebook_id=%s AND n.created_by=%s AND NOT EXISTS(SELECT 1 FROM notebook_deletion_requests d "
            "WHERE d.tenant_id=n.tenant_id AND d.workspace_id=n.workspace_id AND d.notebook_id=n.notebook_id "
            "AND d.state IN ('accepted','deleting','completed'))",
            (context.tenant_id,context.workspace_id,context.notebook_id,context.actor_id)).fetchone()
        if row is None:
            raise WebsiteImportError('WEBSITE_ACCESS_DENIED',403)

    def claim(self, context, url, idempotency_key):
        url=normalize_public_url(url)
        policy_fingerprint=self._policy_fingerprint(context)
        fingerprint=hashlib.sha256(canonical_json_bytes({'scope':list(self._scope(context)),
            'url':url,'key':idempotency_key})).hexdigest()
        with self._transaction(context) as conn:
            self._notebook(conn,context)
            lock=json.dumps([*self._scope(context),idempotency_key],separators=(',',':'))
            if not conn.execute('SELECT pg_try_advisory_xact_lock(hashtextextended(%s,0))',(lock,)).fetchone()[0]:
                raise WebsiteImportError('WEBSITE_IMPORT_IN_PROGRESS',409)
            prior=conn.execute('SELECT request_id,fingerprint,state,snapshot_id,raw_object_id,text_object_id,attempt,safe_error_code '
                'FROM website_import_requests WHERE tenant_id=%s AND workspace_id=%s AND actor_id=%s '
                'AND notebook_id=%s AND idempotency_key=%s FOR UPDATE',(*self._scope(context),idempotency_key)).fetchone()
            if prior:
                if str(prior[1])!=fingerprint:
                    raise WebsiteImportError('WEBSITE_IDEMPOTENCY_KEY_REUSED',409)
                if prior[2]=='completed':
                    row=conn.execute('SELECT snapshot_id,source_id,source_version_id FROM website_source_snapshots '
                        'WHERE tenant_id=%s AND workspace_id=%s AND snapshot_id=%s',
                        (context.tenant_id,context.workspace_id,prior[3])).fetchone()
                    if row is None:
                        raise WebsiteImportError('WEBSITE_STORAGE_UNAVAILABLE')
                    return WebsiteImportClaim(str(prior[0]),fingerprint,True,
                        WebsiteImportResult(str(row[0]),str(row[1]),str(row[2]),'ready',False))
                if prior[2]!='failed':
                    raise WebsiteImportError('WEBSITE_IMPORT_IN_PROGRESS',409)
            request_id='website-request-'+uuid4().hex
            claim=WebsiteImportClaim(request_id,fingerprint,False,None)
            raw,text=object_id(claim,'raw'),object_id(claim,'text')
            if prior:
                old={'request_id':prior[0],'raw_object_id':prior[4],'text_object_id':prior[5],
                     'attempt':prior[6],'safe_error_code':prior[7],'trace_id':context.trace_id}
                conn.execute("UPDATE website_import_requests SET request_id=%s,state='fetching',attempt=attempt+1,"
                    "history=history || %s::jsonb,raw_object_id=%s,text_object_id=%s,safe_error_code=NULL,"
                    "policy_version=%s,policy_fingerprint=%s,updated_at=now() WHERE tenant_id=%s AND workspace_id=%s AND request_id=%s",
                    (request_id,Jsonb([old]),raw,text,context.policy_version,policy_fingerprint,context.tenant_id,context.workspace_id,prior[0]))
            else:
                conn.execute("INSERT INTO website_import_requests(tenant_id,workspace_id,actor_id,notebook_id,"
                    "idempotency_key,request_id,fingerprint,requested_url,state,raw_object_id,text_object_id,policy_version,policy_fingerprint) "
                    "VALUES(%s,%s,%s,%s,%s,%s,%s,%s,'fetching',%s,%s,%s,%s)",
                    (*self._scope(context),idempotency_key,request_id,fingerprint,url,raw,text,context.policy_version,policy_fingerprint))
            return claim

    def mark_storing(self,context,claim):
        with self._transaction(context) as conn:
            row=conn.execute("UPDATE website_import_requests SET state='storing',updated_at=now() "
                "WHERE tenant_id=%s AND workspace_id=%s AND actor_id=%s AND notebook_id=%s AND request_id=%s "
                "AND fingerprint=%s AND state='fetching' RETURNING request_id",
                (*self._scope(context),claim.request_id,claim.fingerprint)).fetchone()
            if row is None:
                raise WebsiteImportError('WEBSITE_IMPORT_IN_PROGRESS',409)

    @staticmethod
    def _insert_canon(conn,context,table,record_id,payload,source_version_id):
        encoded=canonical_json_bytes(payload).decode('utf-8')
        conn.execute(f'INSERT INTO {table}(tenant_id,workspace_id,record_id,aggregate_id,version,schema_version,'
            'canonical_json,canonical_text,digest_sha256,created_by,trace_id,source_version_id) '
            'VALUES(%s,%s,%s,%s,1,1,%s,%s,%s,%s,%s,%s)',
            (context.tenant_id,context.workspace_id,record_id,record_id,Jsonb(payload),encoded,
             hashlib.sha256(encoded.encode()).hexdigest(),context.actor_id,context.trace_id,source_version_id))

    def complete(self,context,claim,page,raw_object_id,text_object_id):
        validate_page(page,page.requested_url)
        if raw_object_id!=object_id(claim,'raw') or text_object_id!=object_id(claim,'text'):
            raise WebsiteImportError('WEBSITE_STORAGE_UNAVAILABLE')
        # 현재 policy fingerprint 공급은 W3의 기존 effective-policy 읽기 경계에서 주입한다.
        policy_fingerprint=self._policy_fingerprint(context)
        deadline=_store_deadline.get() or time.monotonic()+12
        with self._transaction(context) as conn:
            self._notebook(conn,context)
            record=conn.execute('SELECT fingerprint,state,requested_url,policy_version,policy_fingerprint FROM website_import_requests '
                'WHERE tenant_id=%s AND workspace_id=%s AND actor_id=%s AND notebook_id=%s AND request_id=%s FOR UPDATE',
                (*self._scope(context),claim.request_id)).fetchone()
            if record is None or record[0]!=claim.fingerprint or record[1] not in ('fetching','storing'):
                raise WebsiteImportError('WEBSITE_IMPORT_IN_PROGRESS',409)
            if record[2]!=page.requested_url or record[3]!=context.policy_version or record[4]!=policy_fingerprint:
                raise WebsiteImportError('WEBSITE_FETCH_BLOCKED',403)
            for oid,digest,body in [(raw_object_id,page.raw_sha256,page.raw_content),(text_object_id,page.text_sha256,page.text_content)]:
                stored=conn.execute('SELECT status,digest_sha256,byte_size,created_by FROM object_records '
                    'WHERE tenant_id=%s AND workspace_id=%s AND object_id=%s FOR SHARE',
                    (context.tenant_id,context.workspace_id,oid)).fetchone()
                if stored is None or tuple(stored)!=('completed',digest,len(body),context.actor_id):
                    raise WebsiteImportError('WEBSITE_STORAGE_UNAVAILABLE')
            sid='web-source-'+claim.request_id[16:]; vid='web-version-'+claim.request_id[16:]
            snapshot_id='web-snapshot-'+claim.request_id[16:]
            canon=CanonicalContext(context.tenant_id,context.workspace_id,context.actor_id,'source.write',context.trace_id)
            created_at=datetime.now(timezone.utc)
            # 웹 identity는 request 기반이다. 일반 파일의 content_digest_sha256
            # dedup 필드를 공유하지 않고 실제 raw/text digest를 별도로 결속한다.
            payload={'filename':snapshot_id+'.txt','content_type':'text/plain',
                'byte_size':len(page.text_content),'object_id':text_object_id,
                'deletion_policy':'delete_with_notebook','origin_kind':'website',
                'snapshot_id':snapshot_id,'requested_url':page.requested_url,
                'raw_sha256':page.raw_sha256,'text_sha256':page.text_sha256}
            self._canon.create_source_in_transaction(conn,canon,sid)
            self._canon.append_source_version_in_transaction(conn,canon,source_id=sid,
                source_version_id=vid,version_number=1,previous_version_id=None,
                canonical_payload=payload,digest_sha256=hashlib.sha256(canonical_json_bytes(payload)).hexdigest(),
                created_at=created_at)
            conn.execute(
                "WITH inserted AS (INSERT INTO notebook_bindings "
                "(tenant_id,workspace_id,notebook_id,binding_kind,record_id,version_id,created_by,created_at) "
                "VALUES (%s,%s,%s,'source',%s,%s,%s,%s) ON CONFLICT DO NOTHING RETURNING 1) "
                "INSERT INTO notebook_activities "
                "(tenant_id,workspace_id,notebook_id,sequence,activity_kind,actor_id,occurred_at) "
                "SELECT %s,%s,%s,coalesce((SELECT max(sequence) FROM notebook_activities WHERE "
                "tenant_id=%s AND workspace_id=%s AND notebook_id=%s),0)+1,'context_bound',%s,%s FROM inserted",
                (context.tenant_id,context.workspace_id,context.notebook_id,sid,vid,context.actor_id,created_at,
                 context.tenant_id,context.workspace_id,context.notebook_id,
                 context.tenant_id,context.workspace_id,context.notebook_id,context.actor_id,created_at))
            chunks=website_text_chunks(sid,vid,page.text_content.decode('utf-8'))
            if not chunks:
                raise WebsiteImportError('WEBSITE_TEXT_UNAVAILABLE',422)
            for chunk in chunks:
                if time.monotonic()>=deadline:
                    raise WebsiteImportError('WEBSITE_FETCH_TIMEOUT',504)
                self._insert_canon(conn,context,'evidence_spans',chunk['evidence_span_id'],
                    {'source_id':sid,'source_version_id':vid,'page':1,'text':chunk['text'],
                     'kind':'text_source_verbatim_fact'},vid)
            self._insert_canon(conn,context,'index_versions','web-index-'+claim.request_id[16:],
                {'source_id':sid,'source_version_id':vid,'strategy':'website_text_snapshot','chunks':chunks,
                 'lineage':{'snapshot_id':snapshot_id,'text_sha256':page.text_sha256,'raw_sha256':page.raw_sha256}},vid)
            version=1
            for target in ('security_check','processing','indexing','ready'):
                row=conn.execute('SELECT state,version,outcome,error_code FROM transition_canon_state(%s,%s,%s,%s,%s,%s,%s,%s)',
                    ('Source',sid,version,target,'web-transition-'+hashlib.sha256((sid+target).encode()).hexdigest()[:32],
                     'WEBSITE_TEXT_INDEXED',context.trace_id,context.policy_version)).fetchone()
                if row is None or row[2]!='succeeded':
                    raise WebsiteImportError('WEBSITE_STORAGE_UNAVAILABLE')
                version=int(row[1])
            conn.execute('INSERT INTO website_source_snapshots(tenant_id,workspace_id,snapshot_id,source_id,source_version_id,'
                'notebook_id,request_id,raw_object_id,text_object_id,requested_url,final_url,redirect_urls,title,published_at,fetched_at,'
                'license_status,license_url,raw_sha256,text_sha256,extraction_version,policy_version,policy_fingerprint,created_by,trace_id) '
                'VALUES('+','.join(['%s']*24)+')',
                (context.tenant_id,context.workspace_id,snapshot_id,sid,vid,context.notebook_id,claim.request_id,raw_object_id,text_object_id,
                 page.requested_url,page.final_url,Jsonb(list(page.redirect_urls)),page.title,page.published_at,page.fetched_at,
                 page.license_status,page.license_url,page.raw_sha256,page.text_sha256,page.extraction_version,context.policy_version,
                 policy_fingerprint,context.actor_id,context.trace_id))
            if time.monotonic()>=deadline:
                raise WebsiteImportError('WEBSITE_FETCH_TIMEOUT',504)
            if self._policy_fingerprint(context)!=policy_fingerprint:
                raise WebsiteImportError('WEBSITE_FETCH_BLOCKED',403)
            conn.execute("UPDATE website_import_requests SET state='completed',snapshot_id=%s,updated_at=now() "
                'WHERE tenant_id=%s AND workspace_id=%s AND request_id=%s',
                (snapshot_id,context.tenant_id,context.workspace_id,claim.request_id))
            return WebsiteImportResult(snapshot_id,sid,vid,'ready',False)

    def fail(self,context,claim,safe_error_code):
        allowed={'WEBSITE_ACCESS_DENIED','WEBSITE_FETCH_BLOCKED','WEBSITE_FETCH_FAILED','WEBSITE_FETCH_TIMEOUT',
                 'WEBSITE_CONTENT_TOO_LARGE','WEBSITE_MEDIA_UNSUPPORTED','WEBSITE_TEXT_UNAVAILABLE','WEBSITE_URL_INVALID',
                 'WEBSITE_STORAGE_UNAVAILABLE'}
        if safe_error_code not in allowed:
            raise WebsiteImportError('WEBSITE_STORAGE_UNAVAILABLE')
        with self._transaction(context,enforce_deadline=False) as conn:
            conn.execute("UPDATE website_import_requests SET state='failed',safe_error_code=%s,updated_at=now() "
                "WHERE tenant_id=%s AND workspace_id=%s AND actor_id=%s AND notebook_id=%s AND request_id=%s "
                "AND fingerprint=%s AND state IN ('fetching','storing')",
                (safe_error_code,*self._scope(context),claim.request_id,claim.fingerprint))

    def get_snapshot(self,context,source_version_id):
        with self._transaction(context) as conn:
            self._notebook(conn,context)
            fields='snapshot_id,source_id,source_version_id,requested_url,final_url,title,published_at,fetched_at,license_status,license_url,raw_sha256,text_sha256,extraction_version'
            row=conn.execute('SELECT '+','.join('w.'+x for x in fields.split(','))+' FROM website_source_snapshots w '
                'JOIN sources s ON s.tenant_id=w.tenant_id AND s.workspace_id=w.workspace_id AND s.record_id=w.source_id '
                'WHERE w.tenant_id=%s AND w.workspace_id=%s AND w.created_by=%s AND w.notebook_id=%s AND w.source_version_id=%s '
                "AND s.state='ready' AND EXISTS(SELECT 1 FROM notebook_bindings b WHERE b.tenant_id=w.tenant_id "
                "AND b.workspace_id=w.workspace_id AND b.notebook_id=w.notebook_id AND b.binding_kind='source' "
                'AND b.record_id=w.source_id AND b.version_id=w.source_version_id) '
                'AND NOT EXISTS(SELECT 1 FROM notebook_source_unbindings u WHERE u.tenant_id=w.tenant_id '
                'AND u.workspace_id=w.workspace_id AND u.notebook_id=w.notebook_id AND u.source_id=w.source_id '
                'AND u.source_version_id=w.source_version_id) '
                'AND NOT EXISTS(SELECT 1 FROM source_versions v WHERE v.tenant_id=w.tenant_id AND v.workspace_id=w.workspace_id '
                'AND v.source_id=w.source_id AND v.version>(SELECT version FROM source_versions WHERE record_id=w.source_version_id)) '
                'AND NOT EXISTS(SELECT 1 FROM deletion_requests d WHERE d.tenant_id=w.tenant_id AND d.workspace_id=w.workspace_id '
                'AND d.source_id=w.source_id AND d.source_active=false)',
                (*self._scope(context),source_version_id)).fetchone()
            if row is None:
                raise WebsiteImportError('WEBSITE_ACCESS_DENIED',403)
            values=list(row)
            for index in (6,7):
                if values[index] is not None:
                    values[index]=values[index].isoformat()
            return WebsiteSnapshotView(*values)
