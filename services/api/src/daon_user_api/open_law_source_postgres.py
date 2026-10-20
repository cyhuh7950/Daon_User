"""법령 Source/Version/Notebook/index/snapshot을 동일 DB transaction으로 확정한다."""
from contextlib import contextmanager
from dataclasses import asdict
from datetime import datetime,timezone
import hashlib
import json
import time
from uuid import uuid4

import psycopg
from psycopg.types.json import Jsonb
from .authorization import Action

from .data_canon import CanonicalContext,PostgresDataCanonStore,canonical_json_bytes
from .open_law_client import OpenLawSelection
from .open_law_mcp_server import PROTOCOL,SCHEMA_DIGEST
from .open_law_source_access import OpenLawAccessError,require_open_law_source_access,current_open_law_policy
from .open_law_source_import import (OpenLawImportClaim,OpenLawImportError,OpenLawImportResult,
    OpenLawSnapshotView,checked_document,object_id,_SAFE_ERROR_STATUS)
from .website_source_import import _store_deadline

CONNECTOR='mcp-open-law-go-kr'
# API 25s + bounded child reap/DB shutdown margin. Not a renewable worker lease.
_IMPORT_STALE_SECONDS=45


def law_chunks(source,version,text):
    chunks=[]
    for start in range(0,len(text),1024):
        excerpt=text[start:start+1024]
        if not excerpt.strip():continue
        suffix=hashlib.sha256(f'{version}|{start}'.encode()).hexdigest()[:32]
        chunks.append({'chunk_id':'law-chunk-'+suffix,'source_id':source,'source_version_id':version,'page':1,
            'text':excerpt,'evidence_span_id':'law-span-'+suffix,'evidence_kind':'text_source_verbatim_fact',
            'start':start,'end':start+len(excerpt)})
    return chunks


class PostgresOpenLawSourceRepository:
    def __init__(self,dsn,*,canon_store=None,policy_fingerprint=None):
        self._dsn=dsn;self._canon=canon_store or PostgresDataCanonStore(dsn);self._fingerprint=policy_fingerprint
    def close(self):self._canon.close()
    @staticmethod
    def _scope(ctx):return (ctx.tenant_id,ctx.workspace_id,ctx.actor_id,ctx.notebook_id)
    def _policy(self,ctx):
        try:
            value=self._fingerprint(ctx) if callable(self._fingerprint) else None
            if not isinstance(value,str) or not value:raise ValueError
            return value
        except Exception:raise OpenLawImportError('OPEN_LAW_EGRESS_BLOCKED',403) from None

    @contextmanager
    def _transaction(self,ctx,*,enforce_deadline=True):
        try:
            with psycopg.connect(self._dsn,connect_timeout=2) as conn:
                with conn.transaction():
                    conn.execute('SET LOCAL ROLE daon_app')
                    for key,value in [('tenant_id',ctx.tenant_id),('workspace_id',ctx.workspace_id),('actor_id',ctx.actor_id),
                        ('notebook_id',ctx.notebook_id),('capability','source.write'),('trace_id',ctx.trace_id)]:
                        conn.execute('SELECT set_config(%s,%s,true)',('app.'+key,value))
                    conn.execute("SET LOCAL statement_timeout='2000ms'")
                    yield conn
                    if enforce_deadline and _store_deadline.get() is not None and time.monotonic()>=_store_deadline.get():
                        raise OpenLawImportError('OPEN_LAW_TIMEOUT',504)
        except OpenLawImportError:raise
        except OpenLawAccessError:raise OpenLawImportError('OPEN_LAW_ACCESS_DENIED',403) from None
        except psycopg.Error:raise OpenLawImportError('OPEN_LAW_STORAGE_UNAVAILABLE') from None

    def _notebook(self,conn,ctx):
        row=conn.execute("SELECT n.notebook_id FROM notebooks n WHERE n.tenant_id=%s AND n.workspace_id=%s "
            "AND n.notebook_id=%s AND n.created_by=%s AND NOT EXISTS(SELECT 1 FROM notebook_deletion_requests d "
            "WHERE d.tenant_id=n.tenant_id AND d.workspace_id=n.workspace_id AND d.notebook_id=n.notebook_id "
            "AND d.state IN ('accepted','deleting','completed'))",(ctx.tenant_id,ctx.workspace_id,ctx.notebook_id,ctx.actor_id)).fetchone()
        if row is None:raise OpenLawImportError('OPEN_LAW_ACCESS_DENIED',403)

    def _connector(self,conn,ctx):
        from .open_law_source_access import open_law_enabled
        if not open_law_enabled(ctx):raise OpenLawImportError('OPEN_LAW_ACCESS_DENIED',403)
        current_open_law_policy(ctx,conn,action=Action.EDIT)
        row=conn.execute("SELECT verification_json FROM workspace_connectors WHERE tenant_id=%s AND workspace_id=%s "
            "AND connector_id=%s AND status='connected' FOR SHARE",(ctx.tenant_id,ctx.workspace_id,CONNECTOR)).fetchone()
        if (row is None or not isinstance(row[0],dict) or set(row[0])!={'protocol_version','tool_schema_digest','checked_at'}
                or row[0]['protocol_version']!=PROTOCOL or row[0]['tool_schema_digest']!=SCHEMA_DIGEST or not row[0]['checked_at']):
            raise OpenLawImportError('OPEN_LAW_UNAVAILABLE')

    def claim(self,ctx,selection,raw_hash,text_hash,key):
        policy=self._policy(ctx)
        fingerprint=hashlib.sha256(canonical_json_bytes({'scope':list(self._scope(ctx)),'selection':asdict(selection),
            'expected_raw_sha256':raw_hash,'expected_text_sha256':text_hash})).hexdigest()
        with self._transaction(ctx) as conn:
            self._notebook(conn,ctx);self._connector(conn,ctx)
            lock=json.dumps([*self._scope(ctx),key])
            if not conn.execute('SELECT pg_try_advisory_xact_lock(hashtextextended(%s,0))',(lock,)).fetchone()[0]:
                raise OpenLawImportError('OPEN_LAW_IMPORT_IN_PROGRESS',409)
            prior=conn.execute("SELECT request_id,fingerprint,state,snapshot_id,source_id,source_version_id,raw_object_id,text_object_id,attempt,safe_error_code,updated_at<clock_timestamp()-(%s*interval '1 second') "
                'FROM open_law_import_requests WHERE tenant_id=%s AND workspace_id=%s AND actor_id=%s AND notebook_id=%s AND idempotency_key=%s FOR UPDATE',
                (_IMPORT_STALE_SECONDS,*self._scope(ctx),key)).fetchone()
            if prior:
                if prior[1]!=fingerprint:raise OpenLawImportError('OPEN_LAW_IMPORT_CONFLICT',409)
                if prior[2]=='completed':
                    require_open_law_source_access(ctx,str(prior[5]),conn)
                    return OpenLawImportClaim(str(prior[0]),fingerprint,True,OpenLawImportResult(str(prior[3]),str(prior[4]),str(prior[5]),'ready',False))
                if prior[2] in ('fetching','storing') and prior[10] is True:
                    conn.execute("UPDATE open_law_import_requests SET state='failed',safe_error_code='OPEN_LAW_TIMEOUT',updated_at=now() "
                        "WHERE tenant_id=%s AND workspace_id=%s AND request_id=%s AND state IN ('fetching','storing')",
                        (ctx.tenant_id,ctx.workspace_id,prior[0]))
                    prior=(*prior[:2],'failed',*prior[3:9],'OPEN_LAW_TIMEOUT',prior[10])
                if prior[2]!='failed':raise OpenLawImportError('OPEN_LAW_IMPORT_IN_PROGRESS',409)
            request_id='law-request-'+uuid4().hex;claimed=OpenLawImportClaim(request_id,fingerprint,False,None)
            raw,text=object_id(claimed,'raw'),object_id(claimed,'text')
            if prior:
                history={'request_id':prior[0],'raw_object_id':prior[6],'text_object_id':prior[7],'attempt':prior[8],'safe_error_code':prior[9]}
                conn.execute("UPDATE open_law_import_requests SET request_id=%s,state='fetching',attempt=attempt+1,history=history||%s::jsonb,"
                    "raw_object_id=%s,text_object_id=%s,safe_error_code=NULL,policy_version=%s,policy_fingerprint=%s,updated_at=now() "
                    'WHERE tenant_id=%s AND workspace_id=%s AND request_id=%s',
                    (request_id,Jsonb([history]),raw,text,ctx.policy_version,policy,ctx.tenant_id,ctx.workspace_id,prior[0]))
            else:
                conn.execute('INSERT INTO open_law_import_requests(tenant_id,workspace_id,actor_id,notebook_id,idempotency_key,request_id,fingerprint,'
                    'mst,effective_date,article_code,expected_raw_sha256,expected_text_sha256,state,raw_object_id,text_object_id,policy_version,policy_fingerprint) '
                    "VALUES("+','.join(['%s']*12)+",'fetching',%s,%s,%s,%s)",
                    (*self._scope(ctx),key,request_id,fingerprint,selection.mst,selection.effective_date,selection.article_code,raw_hash,text_hash,raw,text,ctx.policy_version,policy))
            return claimed

    def mark_storing(self,ctx,claim):
        with self._transaction(ctx) as conn:
            row=conn.execute("UPDATE open_law_import_requests SET state='storing',updated_at=now() WHERE tenant_id=%s AND workspace_id=%s "
                "AND actor_id=%s AND notebook_id=%s AND request_id=%s AND fingerprint=%s AND state='fetching' RETURNING request_id",
                (*self._scope(ctx),claim.request_id,claim.fingerprint)).fetchone()
            if row is None:raise OpenLawImportError('OPEN_LAW_IMPORT_IN_PROGRESS',409)

    @staticmethod
    def _insert(conn,ctx,table,ident,payload,version):
        text=canonical_json_bytes(payload).decode()
        conn.execute(f'INSERT INTO {table}(tenant_id,workspace_id,record_id,aggregate_id,version,schema_version,canonical_json,canonical_text,digest_sha256,created_by,trace_id,source_version_id) '
            'VALUES(%s,%s,%s,%s,1,1,%s,%s,%s,%s,%s,%s)',(ctx.tenant_id,ctx.workspace_id,ident,ident,Jsonb(payload),text,hashlib.sha256(text.encode()).hexdigest(),ctx.actor_id,ctx.trace_id,version))

    def complete(self,ctx,claim,document,raw_id,text_id):
        selection=OpenLawSelection(document.law['mst'],document.law['effective_date'],document.law['article_code'])
        document=checked_document(document,selection);policy=self._policy(ctx);deadline=_store_deadline.get() or time.monotonic()+12
        if (raw_id,text_id)!=(object_id(claim,'raw'),object_id(claim,'text')):raise OpenLawImportError('OPEN_LAW_STORAGE_UNAVAILABLE')
        with self._transaction(ctx) as conn:
            self._notebook(conn,ctx);self._connector(conn,ctx)
            row=conn.execute('SELECT fingerprint,state,mst,effective_date,article_code,expected_raw_sha256,expected_text_sha256,policy_version,policy_fingerprint '
                'FROM open_law_import_requests WHERE tenant_id=%s AND workspace_id=%s AND actor_id=%s AND notebook_id=%s AND request_id=%s FOR UPDATE',
                (*self._scope(ctx),claim.request_id)).fetchone()
            if row is None or row[0]!=claim.fingerprint or row[1] not in ('fetching','storing'):raise OpenLawImportError('OPEN_LAW_IMPORT_IN_PROGRESS',409)
            if tuple(row[2:7])!=(selection.mst,selection.effective_date,selection.article_code,document.raw_sha256,document.text_sha256):raise OpenLawImportError('OPEN_LAW_PREVIEW_CHANGED',409)
            if tuple(row[7:])!=(ctx.policy_version,policy):raise OpenLawImportError('OPEN_LAW_EGRESS_BLOCKED',403)
            for oid,digest,content in [(raw_id,document.raw_sha256,document.raw_content),(text_id,document.text_sha256,document.text_content)]:
                stored=conn.execute('SELECT status,digest_sha256,byte_size,created_by FROM object_records WHERE tenant_id=%s AND workspace_id=%s AND object_id=%s FOR SHARE',
                    (ctx.tenant_id,ctx.workspace_id,oid)).fetchone()
                if stored is None or tuple(stored)!=('completed',digest,len(content),ctx.actor_id):raise OpenLawImportError('OPEN_LAW_STORAGE_UNAVAILABLE')
            # Use the same typed identity and lock as the snapshot INSERT guard.
            identity=(*self._scope(ctx),CONNECTOR,selection.mst,selection.effective_date,
                selection.article_code or '',document.raw_sha256,document.text_sha256)
            conn.execute('SELECT pg_advisory_xact_lock(hashtextextended(jsonb_build_array('
                +','.join(['%s::text']*10)+')::text,0))',identity)
            duplicate=conn.execute("SELECT r.snapshot_id,r.source_id,r.source_version_id FROM open_law_import_requests r "
                "JOIN notebook_bindings b ON b.tenant_id=r.tenant_id AND b.workspace_id=r.workspace_id AND b.notebook_id=r.notebook_id "
                "AND b.binding_kind='source' AND b.record_id=r.source_id AND b.version_id=r.source_version_id "
                "WHERE r.tenant_id=%s AND r.workspace_id=%s AND r.actor_id=%s AND r.notebook_id=%s AND r.fingerprint=%s AND r.state='completed' "
                'AND NOT EXISTS(SELECT 1 FROM notebook_source_unbindings u WHERE u.tenant_id=b.tenant_id AND u.workspace_id=b.workspace_id '
                'AND u.notebook_id=b.notebook_id AND u.source_id=b.record_id AND u.source_version_id=b.version_id) '
                'ORDER BY r.created_at DESC LIMIT 1',(*self._scope(ctx),claim.fingerprint)).fetchone()
            if duplicate:
                # Reuse only an active binding. An explicit new import after
                # unbinding creates new immutable records, never revives old ones.
                try:require_open_law_source_access(ctx,str(duplicate[2]),conn)
                except OpenLawAccessError:raise OpenLawImportError('OPEN_LAW_BINDING_CONFLICT',409) from None
                result=OpenLawImportResult(str(duplicate[0]),str(duplicate[1]),str(duplicate[2]),'ready',False)
            else:
                suffix=claim.request_id.removeprefix('law-request-');sid='law-source-'+suffix;vid='law-version-'+suffix;snapshot='law-snapshot-'+suffix
                payload={'filename':snapshot+'.txt','content_type':'text/plain','byte_size':len(document.text_content),'object_id':text_id,
                    'deletion_policy':'delete_with_notebook','origin_kind':'mcp_open_law','connector_id':CONNECTOR,'snapshot_id':snapshot,
                    'raw_sha256':document.raw_sha256,'text_sha256':document.text_sha256}
                canon=CanonicalContext(ctx.tenant_id,ctx.workspace_id,ctx.actor_id,'source.write',ctx.trace_id)
                self._canon.create_source_in_transaction(conn,canon,sid)
                self._canon.append_source_version_in_transaction(conn,canon,source_id=sid,source_version_id=vid,version_number=1,previous_version_id=None,
                    canonical_payload=payload,digest_sha256=hashlib.sha256(canonical_json_bytes(payload)).hexdigest(),created_at=datetime.now(timezone.utc))
                conn.execute("INSERT INTO notebook_bindings(tenant_id,workspace_id,notebook_id,binding_kind,record_id,version_id,created_by,created_at) "
                    "VALUES(%s,%s,%s,'source',%s,%s,%s,now())",(ctx.tenant_id,ctx.workspace_id,ctx.notebook_id,sid,vid,ctx.actor_id))
                chunks=law_chunks(sid,vid,document.text_content.decode())
                if not chunks:raise OpenLawImportError('OPEN_LAW_TEXT_UNAVAILABLE',422)
                for chunk in chunks:
                    if time.monotonic()>=deadline:raise OpenLawImportError('OPEN_LAW_TIMEOUT',504)
                    self._insert(conn,ctx,'evidence_spans',chunk['evidence_span_id'],{'source_id':sid,'source_version_id':vid,
                        'page':1,'text':chunk['text'],'start':chunk['start'],'end':chunk['end'],'kind':'text_source_verbatim_fact'},vid)
                self._insert(conn,ctx,'index_versions','law-index-'+suffix,{'source_id':sid,'source_version_id':vid,
                    'strategy':'open_law_verbatim_snapshot','chunks':chunks,'lineage':{'snapshot_id':snapshot,'text_sha256':document.text_sha256,'raw_sha256':document.raw_sha256}},vid)
                version=1
                for target in ('security_check','processing','indexing','ready'):
                    transition=conn.execute('SELECT state,version,outcome,error_code FROM transition_canon_state(%s,%s,%s,%s,%s,%s,%s,%s)',
                        ('Source',sid,version,target,'law-transition-'+hashlib.sha256((sid+target).encode()).hexdigest()[:32],'OPEN_LAW_TEXT_INDEXED',ctx.trace_id,ctx.policy_version)).fetchone()
                    if transition is None or transition[2]!='succeeded':raise OpenLawImportError('OPEN_LAW_STORAGE_UNAVAILABLE')
                    version=int(transition[1])
                law=document.law;origin='https://www.law.go.kr/LSW/lsInfoP.do?lsiSeq='+law['mst']+'&efYd='+law['effective_date']
                columns='tenant_id,workspace_id,snapshot_id,connector_id,source_id,source_version_id,notebook_id,request_id,raw_object_id,text_object_id,mst,law_id,title,effective_date,promulgation_date,ministry,article_code,origin_uri,fetched_at,raw_sha256,text_sha256,extraction_version,license_status,policy_version,policy_fingerprint,created_by,trace_id'
                values=(ctx.tenant_id,ctx.workspace_id,snapshot,CONNECTOR,sid,vid,ctx.notebook_id,claim.request_id,raw_id,text_id,
                    law['mst'],law['law_id'],law['title'],law['effective_date'],law['promulgation_date'],law['ministry'],law['article_code'],origin,document.fetched_at,
                    document.raw_sha256,document.text_sha256,document.extraction_version,'unknown',ctx.policy_version,policy,ctx.actor_id,ctx.trace_id)
                conn.execute('INSERT INTO open_law_source_snapshots('+columns+') VALUES('+','.join(['%s']*len(values))+')',values)
                result=OpenLawImportResult(snapshot,sid,vid,'ready',False)
            if time.monotonic()>=deadline:raise OpenLawImportError('OPEN_LAW_TIMEOUT',504)
            if self._policy(ctx)!=policy:raise OpenLawImportError('OPEN_LAW_EGRESS_BLOCKED',403)
            conn.execute("UPDATE open_law_import_requests SET state='completed',snapshot_id=%s,source_id=%s,source_version_id=%s,updated_at=now() "
                'WHERE tenant_id=%s AND workspace_id=%s AND request_id=%s',(result.snapshot_id,result.source_id,result.source_version_id,ctx.tenant_id,ctx.workspace_id,claim.request_id))
            return result

    def fail(self,ctx,claim,code):
        if not isinstance(code,str) or code not in _SAFE_ERROR_STATUS:raise OpenLawImportError('OPEN_LAW_STORAGE_UNAVAILABLE')
        with self._transaction(ctx,enforce_deadline=False) as conn:
            conn.execute("UPDATE open_law_import_requests SET state='failed',safe_error_code=%s,updated_at=now() "
                "WHERE tenant_id=%s AND workspace_id=%s AND actor_id=%s AND notebook_id=%s AND request_id=%s AND fingerprint=%s AND state IN ('fetching','storing')",
                (code,*self._scope(ctx),claim.request_id,claim.fingerprint))

    def get_snapshot(self,ctx,version):
        with self._transaction(ctx) as conn:
            self._notebook(conn,ctx);require_open_law_source_access(ctx,version,conn)
            fields=list(OpenLawSnapshotView.__dataclass_fields__)
            row=conn.execute('SELECT '+','.join(fields)+' FROM open_law_source_snapshots WHERE tenant_id=%s AND workspace_id=%s AND created_by=%s AND notebook_id=%s AND source_version_id=%s',
                (*self._scope(ctx),version)).fetchone()
            if row is None:raise OpenLawImportError('OPEN_LAW_ACCESS_DENIED',403)
            values=list(row);values[12]=values[12].isoformat()
            return OpenLawSnapshotView(*values)
