"use client";
import { egressPolicyModeLabel } from "./egress-policy-model.js";
import { parsePptxBlock, isPptxBlockContent, pptxBlockLabel } from "./shared-pptx-block.js";

import { useEffect, useMemo, useRef, useState } from "react";
import "@daon-user/design-tokens/tokens.css";
import { isNotebookModelSettings, isQuestionSystemNotice, isQuestionGroundingMetadata, canCreateGroundedReport, createProductWorkspaceState, normalizeProductWorkspaceState, mergeNotebookConversationTurns, projectQuestionFailureState, questionFailureMessage, parseImageRegion, parseSheetCell, isSheetCellContent } from "./product-workspace-model.js";
import { createProductStudioState } from "./product-studio-model.js";
import { ProductStudioPane } from "./product-studio-pane.jsx";
import { StudioApprovalPolicySettings } from "./studio-approval-policy-settings.jsx";
import { isGeneralConversationIntent, classifyConversationIntent } from "./conversation-intent.js";
import { secureUuid } from "./secure-uuid.js";
import "./workspace.css";
import { parseDocxBlock, isDocxBlockContent } from "./shared-docx-block.js";
import { parseAudioSegment, isAudioSegmentContent, audioSourceSha256 } from "./shared-audio-segment.js";

const audioTime = ms => `${String(Math.floor(ms/60000)).padStart(2,'0')}:${String(Math.floor(ms/1000)%60).padStart(2,'0')}.${String(ms%1000).padStart(3,'0')}`;

export function OpenLawSourceForm({adapter,connector,onImported,onConnected}) {
  const [query,setQuery]=useState(''),[confirmed,setConfirmed]=useState(false),[result,setResult]=useState(null);
  const [selection,setSelection]=useState(null),[article,setArticle]=useState(''),[preview,setPreview]=useState(null);
  const [pending,setPending]=useState(false),[error,setError]=useState(null);
  const request=useRef(null),identity=useRef(null);
  useEffect(()=>{setResult(null);setSelection(null);setPreview(null);setPending(false);identity.current=null;
    return()=>{request.current?.abort();request.current=null;};},[adapter]);
  const run=async operation=>{
    if(request.current)return;
    const controller=new AbortController();request.current=controller;setPending(true);setError(null);
    const current=()=>request.current===controller && !controller.signal.aborted;
    try{await operation(controller.signal,current);}catch(cause){if(current())setError(safeErrorCode(cause,'OPEN_LAW_REQUEST_FAILED'));}
    finally{if(current()){request.current=null;setPending(false);}}
  };
  const search=page=>run(async(signal,current)=>{
    if(!confirmed || !query.trim())return;
    setPreview(null);setSelection(null);identity.current=null;
    const value=await adapter.searchOpenLaw({query,page,public_query_confirmed:true},{signal});if(current()){setResult(value);onConnected?.();}
  });
  const selected=selection?{mst:selection.mst,effective_date:selection.effective_date,article_code:article || null}:null;
  return <div className="open-law-source-form">
    <strong>국가법령정보센터 MCP · {result?'공개 조회 확인됨':connector?.status==='connected'?'연결 확인됨':'공개 검색으로 연결 확인 필요'}</strong>
    <label>공개 법령명<input aria-label="공개 법령명" value={query} maxLength={100} disabled={pending} onChange={event=>{setQuery(event.target.value);setResult(null);setSelection(null);setPreview(null);identity.current=null;}} /></label>
    <label><input type="checkbox" aria-label="공개 법령명 전송 확인" checked={confirmed} disabled={pending} onChange={event=>{setConfirmed(event.target.checked);setPreview(null);}} />공개 법령명만 외부 전송하는 데 동의합니다. Source·질문·로그인 정보는 보내지 않습니다.</label>
    <button type="button" disabled={pending || !confirmed || !query.trim() || typeof adapter?.searchOpenLaw!=='function'} onClick={()=>void search(1)}>법령 검색</button>
    {result?<><ul>{result.items.map(item=><li key={`${item.mst}:${item.effective_date}`}><button type="button" disabled={pending} aria-pressed={selection?.mst===item.mst && selection?.effective_date===item.effective_date} onClick={()=>{setSelection(item);setArticle('');setPreview(null);identity.current=null;}}>{item.title} · 시행 {item.effective_date}</button><small>공포일 {item.promulgation_date ?? '미제공'} · 소관부처 {item.ministry ?? '미제공'}</small></li>)}</ul>
      <span>페이지 {result.page} · 총 {result.total_count}건</span><button type="button" disabled={pending || result.page<=1} onClick={()=>void search(result.page-1)}>이전 페이지</button><button type="button" disabled={pending || result.page>=100 || result.page*20>=result.total_count} onClick={()=>void search(result.page+1)}>다음 페이지</button></>:null}
    {selected?<><label>조문 6자리 (비우면 본문)<input aria-label="조문 6자리" value={article} maxLength={6} disabled={pending} onChange={event=>{setArticle(event.target.value);setPreview(null);identity.current=null;}} /></label>
      <button type="button" disabled={pending || !confirmed || (article!=='' && !/^[0-9]{6}$/u.test(article))} onClick={()=>void run(async(signal,current)=>{const value=await adapter.previewOpenLaw(selected,{signal});if(current())setPreview(value);})}>본문 미리보기</button></>:null}
    {preview?<><p>시행 {preview.law.effective_date} · 라이선스 미상<br /><small>공포일 {preview.law.promulgation_date ?? '미제공'} · 소관부처 {preview.law.ministry ?? '미제공'}</small></p><pre>{preview.text}</pre>
      <button type="button" disabled={pending || !confirmed} onClick={()=>void run(async(signal,current)=>{
        const input={...selected,expected_raw_sha256:preview.raw_sha256,expected_text_sha256:preview.text_sha256};
        const fingerprint=JSON.stringify(input);if(identity.current?.fingerprint!==fingerprint)identity.current={fingerprint,key:secureUuid()};
        const value=await adapter.importOpenLaw(input,{idempotencyKey:identity.current.key,signal});if(current()){setPreview(null);identity.current=null;onImported?.(value);}
      })}>Notebook에 가져오기</button></>:null}
    {pending?<p role="status">법령 요청 처리 중입니다.</p>:null}
    {error?<p role="alert">법령 요청을 완료하지 못했습니다. ({error}) 미리보기가 바뀌면 다시 확인하세요.</p>:null}
  </div>;
}

export function OpenLawOriginal({adapter,source}) {
  const [open,setOpen]=useState(true),[value,setValue]=useState(null),[error,setError]=useState(null);
  useEffect(()=>{const controller=new AbortController();setValue(null);setError(null);
    if(open){(async()=>{try{
      const data=await adapter.getOpenLawSnapshot(source.sourceVersionId,{signal:controller.signal});
      if(data.snapshot.source_id!==source.sourceId || data.snapshot.source_version_id!==source.sourceVersionId)throw Error('OPEN_LAW_RESPONSE_INVALID');
      if(!controller.signal.aborted)setValue(data);
    }catch(cause){if(!controller.signal.aborted)setError(safeErrorCode(cause,'OPEN_LAW_ACCESS_DENIED'));}})();}
    return()=>controller.abort();
  },[adapter,source.sourceId,source.sourceVersionId,open]);
  return <section className="website-original" aria-label="법령 Source 원문"><button type="button" onClick={()=>setOpen(current=>!current)}>{open?'법령 원문 닫기':'법령 원문 보기'}</button>
    {open && value?<><h4>{value.snapshot.title}</h4><p>시행 {value.snapshot.effective_date} · 수집 {value.snapshot.fetched_at} · 라이선스 미상</p><pre>{value.text}</pre></>:null}
    {open && error?<p role="alert">법령 원문 사용 불가 ({error})</p>:null}</section>;
}

export function WebsiteSourceForm({ adapter, onRegistered }) {
  const [url,setUrl]=useState(''), [confirmed,setConfirmed]=useState(false);
  const [pending,setPending]=useState(false), [error,setError]=useState(null);
  const request=useRef(null), identity=useRef(null);
  useEffect(()=>()=>{request.current?.abort();request.current=null;},[adapter]);
  const submit=async()=>{
    if(request.current || !confirmed || !adapter?.validWebsiteUrl?.(url) || typeof adapter.importWebsite!=='function')return;
    const fingerprint=JSON.stringify([url,confirmed]);
    if(identity.current?.fingerprint!==fingerprint)identity.current={fingerprint,key:secureUuid()};
    const controller=new AbortController();request.current=controller;setPending(true);setError(null);
    try{
      const result=await adapter.importWebsite({url,publicPageConfirmed:confirmed},{idempotencyKey:identity.current.key,signal:controller.signal});
      if(request.current===controller && !controller.signal.aborted)onRegistered?.(result);
    }catch(cause){
      if(request.current===controller && !controller.signal.aborted)setError(safeErrorCode(cause,'WEBSITE_FETCH_FAILED'));
    }finally{
      if(request.current===controller){request.current=null;setPending(false);}
    }
  };
  return <div className="website-source-form">
    <label>공개 HTTPS URL<input type="url" aria-label="공개 HTTPS URL" value={url} onChange={event=>setUrl(event.target.value)} disabled={pending} maxLength={2048} /></label>
    <small>입력한 공개 주소만 외부 사이트로 전송합니다. Source·질문·로그인 정보는 보내지 않습니다.</small>
    <label><input type="checkbox" checked={confirmed} onChange={event=>setConfirmed(event.target.checked)} disabled={pending} />로그인 없이 공개된 페이지이며 수집 권한을 확인했습니다.</label>
    <button type="button" className="primary-button" disabled={pending || !confirmed || !adapter?.validWebsiteUrl?.(url)} onClick={()=>void submit()}>{pending?'웹사이트 수집 중':'웹사이트 가져오기'}</button>
    {error?<p role="alert">웹사이트를 가져오지 못했습니다. ({error})</p>:null}
  </div>;
}

export function WebsiteOriginal({ adapter, source }) {
  const [open,setOpen]=useState(true), [value,setValue]=useState(null), [error,setError]=useState(null);
  const [pending,setPending]=useState(false);const epoch=useRef(0);
  useEffect(()=>{
    const current=++epoch.current;const controller=new AbortController();
    setValue(null);setError(null);setPending(open);
    if(open && typeof adapter?.getWebsiteSnapshot==='function' && typeof adapter?.listSources==='function'){
      (async()=>{
        try{
          const metadata=await adapter.getWebsiteSnapshot(source.sourceVersionId,{signal:controller.signal});
          const sources=await adapter.listSources({websiteSourceVersionId:source.sourceVersionId,signal:controller.signal});
          const matches=sources.filter(item=>item.source_id===source.sourceId && item.source_version_id===source.sourceVersionId);
          const website=matches.length===1?matches[0].website:null;
          if(!website || metadata.source_id!==source.sourceId || metadata.source_version_id!==source.sourceVersionId
              || Object.keys(metadata).some(key=>website[key]!==metadata[key]) || typeof website.text!=='string'
              || !website.text.trim() || new TextEncoder().encode(website.text).length>524288)throw Error('WEBSITE_RESPONSE_INVALID');
          if(current===epoch.current && !controller.signal.aborted)setValue(website);
        }catch(cause){if(current===epoch.current && !controller.signal.aborted)setError(safeErrorCode(cause,'WEBSITE_FETCH_FAILED'));}
        finally{if(current===epoch.current && !controller.signal.aborted)setPending(false);}
      })();
    }else if(open){setError('WEBSITE_STORAGE_UNAVAILABLE');setPending(false);}
    return()=>{++epoch.current;controller.abort();};
  },[adapter,source.sourceId,source.sourceVersionId,open]);
  return <section className="website-original" aria-label="웹 Source 원문">
    <button type="button" onClick={()=>setOpen(current=>!current)}>{open?'원문 닫기':'원문 보기'}</button>
    {pending?<p role="status">현재 접근 권한과 원문을 확인 중입니다.</p>:null}
    {open && error?<p role="alert">웹 원문을 불러오지 못했습니다. ({error})</p>:null}
    {open && value?<><h4>{value.title}</h4><dl>
      <dt>요청 URL</dt><dd>{value.requested_url}</dd><dt>최종 URL</dt><dd>{value.final_url}</dd>
      <dt>수집 시각</dt><dd>{value.fetched_at}</dd><dt>발행 시각</dt><dd>{value.published_at??'미상'}</dd>
      <dt>라이선스</dt><dd>{value.license_status==='unknown'?'라이선스 미상':value.license_url??'게시자 표시'}</dd>
    </dl><pre>{value.text}</pre></>:null}
  </section>;
}

function AudioOriginalSegments({ content, citation }) {
  const playerRef=useRef(null), resourceRef=useRef(null), requestRef=useRef(null);
  const stopTimerRef=useRef(null);
  const [url,setUrl]=useState(null), [pending,setPending]=useState(false), [error,setError]=useState(null);
  const locator=parseAudioSegment(citation.locator.value);
  useEffect(()=>{
    const player=playerRef.current;
    return()=>{player?.pause();};
  },[url]);
  useEffect(()=>()=>{
    requestRef.current?.abort();requestRef.current=null;
    clearTimeout(stopTimerRef.current);stopTimerRef.current=null;
    playerRef.current?.pause();
    if(resourceRef.current) URL.revokeObjectURL(resourceRef.current);
    resourceRef.current=null;
  },[content,citation]);
  const play=async()=>{
    if(pending || !locator) return;
    setError(null);
    if(url){
      const player=playerRef.current;
      if(!player || !Number.isFinite(player.duration) || Math.abs(player.duration*1000-content.duration_ms)>50){setError('AUDIO_DURATION_MISMATCH');return;}
      clearTimeout(stopTimerRef.current);stopTimerRef.current=null;
      player.currentTime=locator.start_ms/1000;
      try{
        await player.play();
        if(playerRef.current!==player || !resourceRef.current){player.pause();return;}
        stopTimerRef.current=globalThis.setTimeout(()=>{
          stopTimerRef.current=null;
          if(playerRef.current===player && resourceRef.current){player.pause();player.currentTime=locator.end_ms/1000;}
        },Math.max(0,locator.end_ms-Math.round(player.currentTime*1000)));
      }catch{setError('AUDIO_PLAYBACK_UNAVAILABLE');}
      return;
    }
    const controller=new AbortController();requestRef.current=controller;setPending(true);
    try{
      // The validated citation URL is same-origin and has exactly one Notebook binding.
      if(!/^\/bff\/api\/workspaces\/[A-Za-z0-9._:%-]+\/citations\/[A-Za-z0-9._:%-]+\/content\?notebook_id=[A-Za-z0-9._:%-]+$/u.test(citation.content_url))throw new Error();
      const response=await globalThis.fetch(citation.content_url+'&representation=audio',{credentials:'same-origin',cache:'no-store',signal:controller.signal});
      if(!response.ok || response.headers.get('content-type')?.split(';',1)[0].trim()!=='audio/wav')throw new Error();
      const reader=response.body?.getReader();if(!reader)throw new Error();
      const chunks=[];let total=0;
      try{while(true){const {done,value}=await reader.read();if(done)break;total+=value.byteLength;
        if(total>25*1024*1024){await reader.cancel();throw new Error();}chunks.push(value);}}
      finally{reader.releaseLock();}
      if(!total)throw new Error();
      const blob=new Blob(chunks,{type:'audio/wav'});
      const hash=audioSourceSha256(new Uint8Array(await blob.arrayBuffer()));
      if(hash!==content.source_sha256)throw new Error();
      if(controller.signal.aborted || requestRef.current!==controller)return;
      const objectUrl=URL.createObjectURL(blob);resourceRef.current=objectUrl;setUrl(objectUrl);
    }catch{if(!controller.signal.aborted && requestRef.current===controller)setError('AUDIO_CONTENT_UNAVAILABLE');}
    finally{if(!controller.signal.aborted && requestRef.current===controller)setPending(false);}
  };
  return <div>
    <div className="citation-docx-scroll"><p>음성 인식 전사입니다. 사람이 확인한 원문과 구분하여 확인하세요.</p>{content.segments.map(segment=>{
      const points=Array.from(segment.text),selected=segment.segment_id===content.selected_segment_id;
      return <article key={segment.segment_id}><small>{audioTime(segment.start_ms)}–{audioTime(segment.end_ms)}</small><p>{selected?<>{points.slice(0,content.selected_start).join('')}<mark style={{color:'#241c2d',backgroundColor:'#fff0a5'}}>{points.slice(content.selected_start,content.selected_end).join('')}</mark>{points.slice(content.selected_end).join('')}</>:segment.text}</p></article>;
    })}</div>
    <button type="button" onClick={()=>void play()} disabled={pending}>{pending?'원본 확인 중':url?'선택 구간 재생':'원본 불러오기'}</button>
    {url?<audio ref={playerRef} src={url} preload="metadata" onLoadedMetadata={event=>{
      const player=event.currentTarget;
      if(!Number.isFinite(player.duration)||Math.abs(player.duration*1000-content.duration_ms)>50){clearTimeout(stopTimerRef.current);stopTimerRef.current=null;player.pause();setError('AUDIO_DURATION_MISMATCH');return;}
      player.currentTime=locator.start_ms/1000;
    }} onTimeUpdate={event=>{const player=event.currentTarget;if(player.currentTime>=locator.end_ms/1000){clearTimeout(stopTimerRef.current);stopTimerRef.current=null;player.pause();player.currentTime=locator.end_ms/1000;}}} onError={()=>setError('AUDIO_PLAYBACK_UNAVAILABLE')}/>:null}
    {error?<p role="alert">원본 음성을 확인할 수 없습니다. ({error})</p>:null}
  </div>;
}

function PptxOriginalBlocks({ content }) {
  return <div className="citation-docx-scroll"><p>읽기 전용 슬라이드 텍스트·표 원문입니다. PowerPoint 배치는 재현하지 않습니다.</p>{content.blocks.map(block => {
    const points=Array.from(block.text), selected=block.block_id===content.selected_block_id;
    return <article key={block.block_id}><small>{pptxBlockLabel(JSON.stringify({block:block.block_id,start:0,end:Math.max(1,points.length)}))}</small><p>{selected?<>{points.slice(0,content.selected_start).join("")}<mark>{points.slice(content.selected_start,content.selected_end).join("")}</mark>{points.slice(content.selected_end).join("")}</>:block.text}</p></article>;
  })}</div>;
}

function DocxOriginalBlocks({ content }) {
  return <div className="citation-docx-scroll"><p>읽기 전용 문단·표 원문입니다. Word 페이지 배치는 재현하지 않습니다.</p>
    {content.blocks.map(block => {
      const points=Array.from(block.text), selected=block.block_id===content.selected_block_id;
      return <article key={block.block_id}><small>{block.table ? `표 ${block.table.body_index} · 행 ${block.table.row} · 열 ${block.table.column} · 문단 ${block.table.paragraph}` : `문단 ${block.block_id}`}</small>
        <p>{selected ? <>{points.slice(0,content.selected_start).join('')}<mark>{points.slice(content.selected_start,content.selected_end).join('')}</mark>{points.slice(content.selected_end).join('')}</> : block.text}</p>
      </article>;
    })}</div>;
}

const STATE_LABELS = Object.freeze({
  loading: "Workspace를 불러오는 중입니다.",
  empty: "표시할 Workspace 자료가 없습니다.",
  ready: "Workspace가 준비되었습니다.",
  error: "Workspace 처리 중 안전 오류가 발생했습니다.",
  forbidden: "현재 권한으로 Workspace를 열 수 없습니다.",
  unavailable: "실제 Workspace 연결이 아직 준비되지 않았습니다."
});

const PANE_ICONS = Object.freeze({
  "product-pane-sources": "source", "product-pane-conversation": "conversation", "product-pane-studio": "studio",
});

function readableManualLine(line) {
  return line
    .replace(/!\[([^\]]+)\]\([^)]*\)/gu, "$1")
    .replace(/\[([^\]]+)\]\([^)]*\)/gu, "$1")
    .replace(/\*\*([^*]+)\*\*/gu, "$1")
    .replace(/`([^`]+)`/gu, "$1");
}

function ManualMarkdown({ text }) {
  return <div className="manual-reader-body">
    {text.split("\n").map((line, index) => {
      const value = readableManualLine(line.trim());
      if (!value) return <span className="manual-reader-space" aria-hidden="true" key={index} />;
      if (/^!\[/u.test(line.trim())) return <p className="manual-reader-caption" key={index}>{value}</p>;
      const heading = /^(#{1,3})\s+(.+)$/u.exec(value);
      if (heading) return heading[1].length === 1 ? <h3 key={index}>{heading[2]}</h3> : <h4 key={index}>{heading[2]}</h4>;
      const ordered = /^\d+\.\s+(.+)$/u.exec(value);
      if (ordered) return <p className="manual-reader-list" key={index}><span>{value.slice(0, value.indexOf(" "))}</span>{ordered[1]}</p>;
      const bullet = /^-\s+(.+)$/u.exec(value);
      if (bullet) return <p className="manual-reader-list" key={index}><span>•</span>{bullet[1]}</p>;
      if (value.startsWith("> ")) return <aside key={index}>{value.slice(2)}</aside>;
      return <p key={index}>{value}</p>;
    })}
  </div>;
}

const SOURCE_STATE_LABELS = Object.freeze({
  registered: "등록됨",
  security_check: "보안 확인 중",
  processing: "처리 중",
  indexing: "색인 중",
  ready: "사용 가능",
  waiting_model: "모델 대기",
  partial_understanding: "부분 이해",
  needs_review: "검토 필요",
  failed: "처리 실패",
  expired: "만료됨",
  disabled: "사용 중지",
  unavailable: "사용 불가",
  deleting: "삭제 중",
  deleted: "삭제됨",
});

const KNOWLEDGE_PRODUCER_LABELS = Object.freeze({
  daon2: "Daon 2",
  daon2_5: "Daon 2.5",
  daon3: "Daon 3",
});

const OPERATIONS_LABELS = Object.freeze({
  provider: "Provider", api: "API", storage: "Storage", sync: "Sync", queue: "Queue",
});
const OPERATIONS_MESSAGES = Object.freeze({
  PROVIDER_READY: "LLM Provider와 선택 모델이 준비되었습니다.",
  PROVIDER_CONFIGURATION_REQUIRED: "LLM Provider 또는 선택 모델 설정이 필요합니다.",
  API_READY: "Workspace API가 정상입니다.", API_UNAVAILABLE: "Workspace API 상태를 확인할 수 없습니다.",
  STORAGE_READY: "Database와 Object Storage가 정상입니다.", STORAGE_UNAVAILABLE: "저장소 상태 확인이 필요합니다.",
  SYNC_READY: "대기 중인 동기화가 없습니다.", SYNC_PENDING: "승인 또는 전송을 기다리는 동기화가 있습니다.",
  QUEUE_READY: "처리 Queue가 정상입니다.", QUEUE_ATTENTION_REQUIRED: "대기 또는 실패한 처리 작업이 있습니다.",
});
const OUTPUT_SETTING_LABELS = Object.freeze({
  evidence_report: "근거 기반 보고서",
  compliance_checklist: "제약·준수 점검표",
  comparison_table: "비교·데이터 표",
  knowledge_map: "지식 구조도",
  business_draft: "업무 문서 초안",
});
const OUTPUT_SETTING_FORMATS = Object.freeze({
  evidence_report: Object.freeze(["pdf", "docx"]),
  compliance_checklist: Object.freeze(["xlsx", "csv", "pdf"]),
  comparison_table: Object.freeze(["xlsx", "csv", "pdf"]),
  knowledge_map: Object.freeze(["json", "svg", "png", "pdf"]),
  business_draft: Object.freeze(["docx", "pdf"]),
});

const EGRESS_APPROVER_LABELS = Object.freeze({ workspace_manager: "Workspace 관리자", organization_admin: "조직 관리자" });
const LICENSE_RESOURCE_LABELS = Object.freeze({
  users: "사용자", notebooks: "Notebook", storage_bytes: "저장공간",
  generation_runs: "생성 실행", source_versions: "Source Version", studio_outputs: "Studio 산출물",
});
const LICENSE_STATUS_LABELS = Object.freeze({
  not_configured: "미적용", active: "정상", expiring_soon: "30일 이내 만료",
  expired: "만료", limit_reached: "한도 도달",
});

function SafePane({ id, title, description, children }) {
  return (
    <div className="pane-slot">
      <section id={id} className="workspace-pane" aria-labelledby={`${id}-title`}>
        <div className="pane-heading">
          <h2 id={`${id}-title`}><span className={`pane-icon icon-${PANE_ICONS[id]}`} aria-hidden="true" />{title}</h2>
          <button className="info-button" type="button" title={description} aria-label={`${title} 설명`}>i</button>
        </div>
        {children}
      </section>
    </div>
  );
}

function trapDialogFocus(event, close) {
  if (event.key === "Escape") {
    event.preventDefault();
    close();
    return;
  }
  if (event.key !== "Tab") return;
  const focusable = [...event.currentTarget.querySelectorAll("button:not([disabled]), [href], input:not([disabled]), select:not([disabled]), textarea:not([disabled]), [tabindex]:not([tabindex='-1'])")];
  if (!focusable.length) return;
  const first = focusable[0];
  const last = focusable.at(-1);
  if (event.shiftKey && document.activeElement === first) { event.preventDefault(); last.focus(); }
  else if (!event.shiftKey && document.activeElement === last) { event.preventDefault(); first.focus(); }
}

function safeErrorCode(error, fallback) {
  return typeof error?.message === "string" && /^[A-Z][A-Z0-9_]{2,63}$/u.test(error.message)
    ? error.message
    : fallback;
}

const SAFE_DTO_ID = /^[A-Za-z0-9][A-Za-z0-9._:-]{0,255}$/u;
const PROCESSING_STATUS_KEYS = Object.freeze([
  "processing_run_id", "source_id", "source_version_id", "processing_state",
  "source_state", "job_state", "safe_error_code"
]);
const PENDING_SOURCE_STATES = new Set(["registered", "security_check", "processing", "indexing", "ready"]);
const PENDING_PROCESSING_STATES = new Set([
  "accepted", "queued", "processing", "vision_llm_understanding", "audio_llm_understanding",
  "speech_to_text", "llm_semantic_understanding", "parser_ocr_validation",
  "transcript_timecode_validation", "evidence_reconciliation", "completed"
]);
const PENDING_JOB_STATES = new Set(["pending", "queued", "leased", "processing", "retry_wait", "completed"]);
const TERMINAL_SOURCE_FAILURES = new Set(["waiting_model", "partial_understanding", "needs_review", "failed", "disabled", "expired", "deleting", "deleted"]);
const TERMINAL_PROCESSING_FAILURES = new Set(["failed", "policy_blocked"]);
const TERMINAL_JOB_FAILURES = new Set(["dead_letter"]);
const DEFAULT_PROCESSING_POLL_OPTIONS = Object.freeze({
  deadlineMs: 150_000,
  intervalMs: 1_000,
  statusRequestTimeoutMs: 10_000
});

function isSafeDtoId(value) {
  return typeof value === "string" && SAFE_DTO_ID.test(value);
}

function hasExactKeys(value, keys) {
  if (!value || typeof value !== "object" || Array.isArray(value)) return false;
  const actual = Object.keys(value).sort();
  const expected = [...keys].sort();
  return actual.length === expected.length && actual.every((key, index) => key === expected[index]);
}

function abortError() {
  return Object.assign(new Error("PROCESSING_POLL_ABORTED"), { name: "AbortError" });
}

function waitForProcessingPoll(intervalMs, signal) {
  return new Promise((resolve, reject) => {
    if (signal.aborted) {
      reject(abortError());
      return;
    }
    const onAbort = () => {
      clearTimeout(timeoutId);
      reject(abortError());
    };
    const timeoutId = setTimeout(() => {
      signal.removeEventListener("abort", onAbort);
      resolve();
    }, intervalMs);
    signal.addEventListener("abort", onAbort, { once: true });
  });
}

function monotonicNow() {
  return globalThis.performance?.now?.() ?? Date.now();
}

function linkedAbortSignal(signals) {
  const controller = new AbortController();
  const listeners = [];
  const abort = (signal) => {
    if (!controller.signal.aborted) controller.abort(signal.reason ?? abortError());
  };
  for (const signal of signals) {
    if (signal.aborted) {
      abort(signal);
      break;
    }
    const listener = () => abort(signal);
    signal.addEventListener("abort", listener, { once: true });
    listeners.push([signal, listener]);
  }
  return {
    signal: controller.signal,
    cleanup: () => {
      for (const [signal, listener] of listeners) signal.removeEventListener("abort", listener);
    }
  };
}

function awaitAbortable(promise, signal) {
  if (signal.aborted) return Promise.reject(abortError());
  return new Promise((resolve, reject) => {
    const onAbort = () => reject(abortError());
    signal.addEventListener("abort", onAbort, { once: true });
    Promise.resolve(promise).then(
      (value) => { signal.removeEventListener("abort", onAbort); resolve(value); },
      (error) => { signal.removeEventListener("abort", onAbort); reject(error); }
    );
  });
}

function isTransientSourceFetchError(error) {
  if (error?.name !== "TypeError" || typeof error.message !== "string") return false;
  return /^(?:Failed to fetch|NetworkError when attempting to fetch resource\.?|Load failed)$/u.test(error.message);
}

function isRetryableSourceLoadError(error) {
  if (error?.retryable === true || isTransientSourceFetchError(error)) return true;
  // Contract/list failures are surfaced immediately so the user can retry from
  // the UI; only transient transport failures are retried automatically.
  return new Set(["RESOURCE_UNAVAILABLE", "WORKSPACE_REQUEST_FAILED"]).has(error?.message);
}

function normalizeProcessingPollOptions(options) {
  return {
    deadlineMs: Number.isSafeInteger(options?.deadlineMs) && options.deadlineMs >= 1 && options.deadlineMs <= 150_000
      ? options.deadlineMs
      : DEFAULT_PROCESSING_POLL_OPTIONS.deadlineMs,
    intervalMs: Number.isSafeInteger(options?.intervalMs) && options.intervalMs >= 0 && options.intervalMs <= 60_000
      ? options.intervalMs
      : DEFAULT_PROCESSING_POLL_OPTIONS.intervalMs,
    statusRequestTimeoutMs: Number.isSafeInteger(options?.statusRequestTimeoutMs)
      && options.statusRequestTimeoutMs >= 1
      && options.statusRequestTimeoutMs <= 10_000
      ? options.statusRequestTimeoutMs
      : DEFAULT_PROCESSING_POLL_OPTIONS.statusRequestTimeoutMs,
    now: typeof options?.now === "function" ? options.now : monotonicNow,
    wait: typeof options?.wait === "function" ? options.wait : waitForProcessingPoll
  };
}

function processingDisposition(status, submission) {
  const validShape = hasExactKeys(status, PROCESSING_STATUS_KEYS)
    && isSafeDtoId(status.processing_run_id)
    && isSafeDtoId(status.source_id)
    && isSafeDtoId(status.source_version_id)
    && typeof status.processing_state === "string"
    && typeof status.source_state === "string"
    && (status.job_state === null || typeof status.job_state === "string")
    && (status.safe_error_code === null || (
      typeof status.safe_error_code === "string" && /^[A-Z][A-Z0-9_]{2,63}$/u.test(status.safe_error_code)
    ));
  if (!validShape) throw new Error("PROCESSING_STATUS_INVALID");
  if (
    status.processing_run_id !== submission.processing_run_id
    || status.source_id !== submission.source_id
    || status.source_version_id !== submission.source_version_id
  ) throw new Error("PROCESSING_LINEAGE_MISMATCH");
  if (
    status.source_state === "ready"
    && status.processing_state === "completed"
    && status.job_state === "completed"
    && status.safe_error_code === null
  ) return "ready";
  if (
    status.safe_error_code
    || TERMINAL_SOURCE_FAILURES.has(status.source_state)
    || TERMINAL_PROCESSING_FAILURES.has(status.processing_state)
    || TERMINAL_JOB_FAILURES.has(status.job_state)
  ) throw new Error(status.safe_error_code ?? "PROCESSING_STATUS_UNAVAILABLE");
  if (
    !PENDING_SOURCE_STATES.has(status.source_state)
    || !PENDING_PROCESSING_STATES.has(status.processing_state)
    || (status.job_state !== null && !PENDING_JOB_STATES.has(status.job_state))
  ) throw new Error("PROCESSING_STATUS_INVALID");
  return "pending";
}

export function projectSafeQuestionAnswer(answer, workspaceId, citationUrl, selectedSource, selectedKnowledgeId = null) {
  const baseKeys = ["run_id", "run_result_id", "answer", "insufficient", "citations"];
  const metadataKeys = ["mode", "grounding", "source_scope_summary", "mismatch", "next_actions"];
  const legacyShape = hasExactKeys(answer, baseKeys);
  const enrichedShape = hasExactKeys(answer, [...baseKeys, ...metadataKeys]);
  const freshShape=hasExactKeys(answer,[...baseKeys,...metadataKeys,"grounding_status"]);
  const validMetadata = !(enrichedShape||freshShape) || (
    ["work_support", "explicit_source_lookup", "source_backed_action", "approved_web_research"].includes(answer.mode)
    && ["source_backed", "ungrounded", "source_evidence_unavailable", "web_backed"].includes(answer.grounding)
    && (answer.source_scope_summary === null || (
      typeof answer.source_scope_summary === "string"
      && answer.source_scope_summary.length <= 1_000
      && !/[\u0000-\u001f\u007f]/u.test(answer.source_scope_summary)
    ))
    && (answer.mismatch === null || (
      hasExactKeys(answer.mismatch, ["code", "detail"])
      && answer.mismatch.code === "SOURCE_SCOPE_MISMATCH"
      && typeof answer.mismatch.detail === "string"
      && answer.mismatch.detail.length >= 1
      && answer.mismatch.detail.length <= 1_000
    ))
    && Array.isArray(answer.next_actions)
    && answer.next_actions.length <= 4
    && answer.next_actions.every((item) => typeof item === "string" && item.length >= 1 && item.length <= 120)
    && (answer.grounding !== "source_evidence_unavailable" || (
      (answer.mismatch !== null || freshShape&&answer.grounding_status==="no_relevant_evidence") && answer.next_actions.length > 0
    ))
  );
  const validAnswer = (legacyShape || enrichedShape || freshShape) && validMetadata
    && (!freshShape || isQuestionGroundingMetadata(answer))
    && isSafeDtoId(answer.run_id)
    && isSafeDtoId(answer.run_result_id)
    && typeof answer.answer === "string"
    && answer.answer.length >= 1
    && answer.answer.length <= 8_000
    && typeof answer.insufficient === "boolean"
    && Array.isArray(answer.citations)
    && answer.citations.length <= 10;
  if (!validAnswer) throw new Error("QUESTION_RESPONSE_INVALID");
  const citations = answer.citations.map((citation) => {
    const validLocator = hasExactKeys(citation?.locator, ["kind", "value"])
      && typeof citation.locator.kind === "string"
      && /^[a-z][a-z0-9_]{0,31}$/u.test(citation.locator.kind)
      && typeof citation.locator.value === "string"
      && citation.locator.value.length >= 1
      && citation.locator.value.length <= 255
      && !/[\u0000-\u001f\u007f]/u.test(citation.locator.value)
      && (citation.locator.kind !== "image_region" || parseImageRegion(citation.locator.value) !== null)
      && (citation.locator.kind !== "sheet_cell" || parseSheetCell(citation.locator.value) !== null)
      && (citation.locator.kind !== "docx_block" || parseDocxBlock(citation.locator.value) !== null)
      && (citation.locator.kind !== "pptx_block" || parsePptxBlock(citation.locator.value) !== null)
      && (citation.locator.kind !== "audio_segment" || parseAudioSegment(citation.locator.value) !== null);
    const validCitation = hasExactKeys(citation, ["citation_id", "source_id", "source_version_id", "evidence_span_id", "page", "origin", "context_item_id", "locator"])
      && isSafeDtoId(citation.citation_id)
      && isSafeDtoId(citation.source_id)
      && isSafeDtoId(citation.source_version_id)
      && isSafeDtoId(citation.evidence_span_id)
      && Number.isSafeInteger(citation.page)
      && citation.page >= 1
      && validLocator
      && (citation.locator.kind !== "page" || citation.locator.value === String(citation.page))
      && (
        citation.origin === "raw_source"
          ? citation.source_id === selectedSource?.sourceId
            && citation.source_version_id === selectedSource?.sourceVersionId
            && citation.context_item_id === selectedSource?.sourceId
          : citation.origin === "daon_knowledge"
            ? citation.context_item_id === selectedKnowledgeId
          : false
      );
    if (!validCitation) throw new Error("QUESTION_RESPONSE_INVALID");
    let contentUrl;
    try {
      contentUrl = citationUrl(citation);
    } catch {
      throw new Error("QUESTION_RESPONSE_INVALID");
    }
    // Citation content is notebook-scoped on the BFF contract. The adapter
    // already includes notebook_id in the generated URL; validate the exact
    // same URL here so a valid grounded response is not rejected client-side.
    const expectedBase = `${citationUrl(citation).split("#", 1)[0]}`;
    const expectedUrl = citation.locator.kind === "page" ? `${expectedBase}#page=${citation.page}` : expectedBase;
    const expectedNativeUrl = `#/citations/${encodeURIComponent(citation.citation_id)}?page=${citation.page}`;
    if (contentUrl !== expectedUrl && contentUrl !== expectedNativeUrl) throw new Error("QUESTION_RESPONSE_INVALID");
    return { ...citation, content_url: contentUrl };
  });
  return { ...answer, citations };
}

export function buildQuestionKnowledgeContext(selectedSource, selectedKnowledgeId) {
  if (!selectedSource && !selectedKnowledgeId) throw new Error("QUESTION_CONTEXT_INVALID");
  return {
    mode: selectedKnowledgeId && selectedSource
      ? "mixed"
      : selectedKnowledgeId ? "daon_priority" : "raw_only",
    resources: [
      ...(selectedKnowledgeId ? [{
        resourceKind: "knowledge_package", resourceId: selectedKnowledgeId,
      }] : []),
      ...(selectedSource ? [{
        resourceKind: "source", resourceId: selectedSource.sourceId,
        versionId: selectedSource.sourceVersionId,
      }] : []),
    ],
  };
}

export async function submitGroundedReport({ adapter, state, title, purpose, idempotencyKey, signal }) {
  const reportTitle = typeof title === "string" ? title.trim() : "";
  const reportPurpose = typeof purpose === "string" ? purpose.trim() : "";
  if (
    !adapter || typeof adapter.createReport !== "function" || typeof adapter.listStudioOutputs !== "function"
    || !canCreateGroundedReport(state) || !reportTitle || !reportPurpose
  ) return { submitted: false, outputs: state?.studioOutputs ?? [] };
  await adapter.createReport({
    source_id: state.selectedSource.sourceId,
    source_version_id: state.selectedSource.sourceVersionId,
    run_id: state.answer.run_id,
    run_result_id: state.answer.run_result_id,
    title: reportTitle,
    purpose: reportPurpose
  }, { idempotencyKey, signal });
  return { submitted: true, outputs: await adapter.listStudioOutputs({ signal }) };
}

export async function submitGroundedReportForm(event, {
  adapter, state, title, purpose, idempotencyRef, uuid = secureUuid, signal,
}) {
  event?.preventDefault();
  const normalized = {
    source_id: state?.selectedSource?.sourceId,
    source_version_id: state?.selectedSource?.sourceVersionId,
    run_id: state?.answer?.run_id,
    run_result_id: state?.answer?.run_result_id,
    title: typeof title === "string" ? title.trim() : "",
    purpose: typeof purpose === "string" ? purpose.trim() : ""
  };
  if (!canCreateGroundedReport(state) || !normalized.title || !normalized.purpose) {
    return { submitted: false, outputs: state?.studioOutputs ?? [] };
  }
  const fingerprint = JSON.stringify(normalized);
  let key = idempotencyRef?.current?.fingerprint === fingerprint
    ? idempotencyRef.current.key
    : `report-${uuid()}`;
  if (typeof key !== "string" || !/^[A-Za-z0-9][A-Za-z0-9._:-]{15,127}$/u.test(key)) {
    throw new Error("STUDIO_INPUT_INVALID");
  }
  if (idempotencyRef) idempotencyRef.current = { fingerprint, key };
  return submitGroundedReport({ adapter, state, title: normalized.title, purpose: normalized.purpose, idempotencyKey: key, signal });
}

export async function openNativeCitation(event, { adapter, citation, signal, registerObjectUrl, unregisterObjectUrl, openWindow = globalThis.open, objectUrl = globalThis.URL, BlobType = globalThis.Blob, schedule = globalThis.setTimeout }) {
  if (typeof adapter?.citationContent !== "function") return false;
  event?.preventDefault();
  const result = await adapter.citationContent(citation, { signal });
  if (signal?.aborted) return false;
  const bytes = result?.bytes;
  if (
    !hasExactKeys(result, ["content_type", "page", "bytes"])
    || result.content_type !== "application/pdf"
    || result.page !== citation?.page
    || !Array.isArray(bytes) || bytes.length < 5 || bytes.length > 25 * 1024 * 1024
    || bytes.slice(0, 5).some((byte, index) => byte !== [0x25, 0x50, 0x44, 0x46, 0x2d][index])
    || typeof objectUrl?.createObjectURL !== "function" || typeof objectUrl?.revokeObjectURL !== "function"
    || typeof BlobType !== "function" || typeof openWindow !== "function"
  ) throw new Error("CITATION_RESPONSE_INVALID");
  const url = objectUrl.createObjectURL(new BlobType([Uint8Array.from(bytes)], { type: "application/pdf" }));
  registerObjectUrl?.(url);
  if (signal?.aborted) {
    objectUrl.revokeObjectURL(url);
    unregisterObjectUrl?.(url);
    return false;
  }
  try {
    openWindow(`${url}#page=${result.page}`, "_blank", "noopener,noreferrer");
  } finally {
    schedule(() => {
      objectUrl.revokeObjectURL(url);
      unregisterObjectUrl?.(url);
    }, 60_000);
  }
  return true;
}

const WORKSPACE_ADAPTER_KEYS = new WeakMap();
let workspaceAdapterSequence = 0;
function workspaceAdapterKey(adapter) {
  if (!adapter || (typeof adapter !== "object" && typeof adapter !== "function")) return "none";
  if (!WORKSPACE_ADAPTER_KEYS.has(adapter)) WORKSPACE_ADAPTER_KEYS.set(adapter, `adapter-${++workspaceAdapterSequence}`);
  return WORKSPACE_ADAPTER_KEYS.get(adapter);
}

function SheetOriginalTable({ content }) {
  const position = address => {
    const [, letters, row] = /^([A-Z]+)([0-9]+)$/u.exec(address);
    return [[...letters].reduce((total, letter) => total * 26 + letter.charCodeAt(0) - 64, 0), Number(row)];
  };
  const selectedRange = content.selected_range.split(":");
  const selected = [...position(selectedRange[0]), ...position(selectedRange.at(-1))];
  const rows = new Map();
  for (const cell of content.cells) {
    const [col, row] = position(cell.address);
    if (!rows.has(row)) rows.set(row, []);
    rows.get(row).push({ cell, col, row });
  }
  return <><p>{content.sheet_name} · {content.selected_range} · {content.sheet_visibility === "visible" ? "표시 시트" : "숨김 시트"}</p>
    <small>원본에 저장된 cached 값이며 재계산하지 않습니다. 수식은 실행하지 않습니다.</small>
    <div className="citation-sheet-scroll" tabIndex="0" aria-label="원문 셀 표 스크롤"><table aria-label="원문 셀 표"><tbody>{[...rows].sort(([a], [b]) => a - b).map(([row, cells]) => <tr key={row}>{cells.sort((a, b) => a.col - b.col).map(({ cell, col }) => <td key={cell.address} data-address={cell.address} aria-selected={col >= selected[0] && col <= selected[2] && row >= selected[1] && row <= selected[3]}>
      <strong>{cell.address}</strong><span>{cell.display_value ?? "—"}</span>
      {cell.formula !== null ? <small>수식 {cell.formula} · cached {cell.cached_value ?? "없음"}</small> : null}
      {cell.hidden_row || cell.hidden_column ? <small>{cell.hidden_row ? "숨김 행 " : ""}{cell.hidden_column ? "숨김 열" : ""}</small> : null}
    </td>)}</tr>)}</tbody></table></div>
    {content.merged_ranges.length ? <small>병합 영역: {content.merged_ranges.join(", ")}</small> : null}
  </>;
}

function ProductWorkspaceShellInner({ workspaceId, notebookTitle = null, state = createProductWorkspaceState(), adapter = null, processingPollOptions = null, desktopOfflineStudio = null, providerSettings = null, providerSettingsDirty = false, providerSettingsSaving = false, onLogout = null,
  initialSourceVersionId = null, initialStudioOutput = null }) {
  const [viewState, setViewState] = useState(() => normalizeProductWorkspaceState(state));
  const notificationOutputScope = useRef({adapter, output:initialStudioOutput, consumed:false});
  if (notificationOutputScope.current.adapter !== adapter || notificationOutputScope.current.output !== initialStudioOutput) {
    notificationOutputScope.current = {adapter, output:initialStudioOutput, consumed:false};
  }
  const notificationOutput = notificationOutputScope.current.consumed ? null : initialStudioOutput;
  useEffect(() => {
    // 최초 자료/산출물 로딩까지만 알림 대상을 유지한다. 이후 명시적인 사용자 선택을 강제 복원하지 않는다.
    if (viewState.status !== "loading" && viewState.studioStatus !== "loading") notificationOutputScope.current.consumed = true;
  }, [adapter, initialStudioOutput, viewState.status, viewState.studioStatus]);
  const [processing, setProcessing] = useState(null);
  const [question, setQuestion] = useState("");
  const [questionPending, setQuestionPending] = useState(false);
  const [notebookModel,setNotebookModel]=useState({status:"loading",view:null});
  const modelScope=useRef({adapter,workspaceId});modelScope.current={adapter,workspaceId};
  const modelLoadedScope=useRef(null);
  const modelRequest=useRef(null);const modelSaveRetry=useRef(null);
  const sameModelScope=owner=>modelScope.current.adapter===owner.adapter&&modelScope.current.workspaceId===owner.workspaceId;
  const loadNotebookModel=async()=>{
    const owner={adapter,workspaceId};
    modelRequest.current?.abort();const controller=new AbortController();modelRequest.current=controller;
    setNotebookModel({status:"loading",view:null});
    if(typeof adapter?.getModelSettings!=="function"){
      setNotebookModel({status:"unsupported",view:null});return;
    }
    try{
      const view=await adapter.getModelSettings({signal:controller.signal});
      if(controller.signal.aborted||!sameModelScope(owner))return;
      if(!isNotebookModelSettings(view))throw new Error("MODEL_SETTINGS_RESPONSE_INVALID");
      modelLoadedScope.current=owner;
      setNotebookModel({status:"ready",view});modelSaveRetry.current=null;
    }catch(error){if(!controller.signal.aborted&&sameModelScope(owner))setNotebookModel({status:"error",view:null,code:error?.message});}
  };
  useEffect(()=>{modelSaveRetry.current=null;loadNotebookModel();return()=>modelRequest.current?.abort();},[adapter,workspaceId]);
  const saveNotebookModel=async(selection,{retry=false,executionRequest=null}={})=>{
    if((questionRequestRef.current&&questionRequestRef.current!==executionRequest)||modelRequest.current?.saving)return null;
    const view=notebookModel.view;if(!view||typeof adapter?.saveModelSettings!=="function")return;
    const owner={adapter,workspaceId};
    const pending=retry?modelSaveRetry.current:{input:{connection_id:selection.connection_id,model_id:selection.model_id,
      reasoning_effort:selection.reasoning_effort,expected_version:view.version},key:secureUuid()};
    if(!pending)return;
    modelSaveRetry.current=pending;
    modelRequest.current?.abort();const controller=new AbortController();controller.saving=true;modelRequest.current=controller;
    setNotebookModel({status:"saving",view,draft:selection});
    try{
      const receipt=await adapter.saveModelSettings(pending.input,{signal:controller.signal,idempotencyKey:pending.key});
      if(controller.signal.aborted||!sameModelScope(owner))return;
      if(!isNotebookModelSettings(receipt,{saved:true}))throw new Error("MODEL_SETTINGS_RESPONSE_INVALID");
      // PATCH의 과거 멱등 결과를 최신 설정으로 표시하지 않고 현재 GET을 다시 확인한다.
      const current=await adapter.getModelSettings({signal:controller.signal});
      if(controller.signal.aborted||!sameModelScope(owner))return;
      if(!isNotebookModelSettings(current)||current.notebook_id!==receipt.notebook_id||current.version!==receipt.version
        ||["connection_id","model_id","reasoning_effort"].some(k=>current.selection?.[k]!==receipt.selection[k]))throw new Error("NOTEBOOK_MODEL_SETTINGS_CHANGED");
      modelSaveRetry.current=null;modelExecutionRef.current=current.version;setNotebookModel({status:"ready",view:current});return current;
    }catch(error){if(!controller.signal.aborted&&sameModelScope(owner))setNotebookModel({status:"error",view,draft:selection,code:error?.message});}
    finally{controller.saving=false;}
  };
  const modelScopeReady=modelLoadedScope.current?.adapter===adapter&&modelLoadedScope.current?.workspaceId===workspaceId&&notebookModel.status==="ready";
  const modelReady=modelScopeReady&&notebookModel.view?.version>0&&notebookModel.view.effective_selection?.origin==="notebook";
  const workspaceDefault=modelScopeReady&&notebookModel.view?.version===0&&notebookModel.view.selection===null
    &&notebookModel.view.effective_selection?.origin==="workspace_default"?notebookModel.view.effective_selection:null;
  const displayedSelection=notebookModel.view?.selection??workspaceDefault;
  const canSubmitQuestion=()=>modelReady||Boolean(workspaceDefault)||(modelScopeReady
    &&notebookModel.view?.version===0&&!viewState.selectedSource&&!selectedKnowledgeId&&!isGeneralConversationIntent(question));
  const modelExecutionRef=useRef(null);modelExecutionRef.current=modelReady?notebookModel.view.version:null;
  const confirmModelRef=useRef(null);
  confirmModelRef.current=async(executionRequest=null)=>{
    if(modelReady)return notebookModel.view.version;
    if(!workspaceDefault)throw new Error("NOTEBOOK_MODEL_SELECTION_REQUIRED");
    const selection={connection_id:workspaceDefault.connection_id,model_id:workspaceDefault.model_id,reasoning_effort:workspaceDefault.reasoning_effort};
    const saved=await saveNotebookModel(selection,{executionRequest});
    if(!saved)throw new Error("NOTEBOOK_MODEL_SETTINGS_CHANGED");
    return saved.version;
  };
  const studioModelAdapter=useMemo(()=>{
    if(!adapter)return null;
    const selectedInput=input=>{
      const version=modelExecutionRef.current;
      if(version===null||input.model_settings_version!==version)throw new Error("NOTEBOOK_MODEL_SETTINGS_CHANGED");
      return input;
    };
    return {...adapter,getStudioModelSettingsVersion:()=>modelExecutionRef.current,
      ensureStudioModelSettingsVersion:()=>confirmModelRef.current(),
      authorizeGeneration:typeof adapter.authorizeGeneration==="function"?(input,options)=>adapter.authorizeGeneration(selectedInput(input),options):undefined,
      createGeneration:typeof adapter.createGeneration==="function"?(input,options)=>adapter.createGeneration(selectedInput(input),options):undefined,
      createStudioVersion:typeof adapter.createStudioVersion==="function"?(id,input,options)=>adapter.createStudioVersion(id,input.revision_type==="user_edit"?input:selectedInput(input),options):undefined};
  },[adapter]);
  const [conversationTurns,setConversationTurns]=useState([]);
  const conversationTurnsRef=useRef([]);
  const conversationOwnerRef=useRef({adapter,workspaceId});
  const commitConversationTurns=update=>{
    const next=typeof update==="function"?update(conversationTurnsRef.current):update;
    conversationTurnsRef.current=next;setConversationTurns(next);
  };
  const visibleConversationTurns=conversationOwnerRef.current.adapter===adapter&&conversationOwnerRef.current.workspaceId===workspaceId?conversationTurns:[];
  const [conversationCursor,setConversationCursor]=useState(null);
  const [conversationHistoryError,setConversationHistoryError]=useState(null);
  const [conversationHistoryPending,setConversationHistoryPending]=useState(false);
  const conversationScopeRef=useRef(null);
  const conversationReadRef=useRef(null);
  const conversationGenerationRef=useRef(0);
  conversationScopeRef.current={adapter,workspaceId};
  const readConversationTurns=async ({before,append=false}={})=>{
    if(typeof adapter?.listConversationTurns!=="function") return;
    conversationReadRef.current?.abort();
    const controller=new AbortController();conversationReadRef.current=controller;
    const generation=conversationGenerationRef.current;
    const current=()=>!controller.signal.aborted&&conversationGenerationRef.current===generation
      &&conversationScopeRef.current.adapter===adapter&&conversationScopeRef.current.workspaceId===workspaceId;
    setConversationHistoryPending(true);
    try {
      const page=await adapter.listConversationTurns({before,limit:50,signal:controller.signal});
      if(!current())return;
      if(!hasExactKeys(page,["items","next_cursor"])||!Array.isArray(page.items)||page.items.length>50
        ||!(page.next_cursor===null||typeof page.next_cursor==='string'&&/^[A-Za-z0-9_-]{1,2048}$/u.test(page.next_cursor)))throw new Error('NOTEBOOK_CONVERSATION_INVALID');
      const projected=mergeNotebookConversationTurns([],page.items);
      commitConversationTurns(previous=>mergeNotebookConversationTurns(append?previous:
        previous.filter(turn=>turn.turn_id.startsWith("pending-")&&turn.state!=="completed"),projected));
      if(append||before===undefined)setConversationCursor(page.next_cursor);
      setConversationHistoryError(null);
    }catch(error){if(current()){
      commitConversationTurns([]);
      setConversationCursor(null);
      setConversationHistoryError(safeErrorCode(error,'NOTEBOOK_CONVERSATION_UNAVAILABLE'));
      setViewState(previous=>({...previous,answer:null,answerIntent:null}));
    }}
    finally{if(current())setConversationHistoryPending(false);}
  };
  useEffect(()=>{
    conversationGenerationRef.current++;
    conversationOwnerRef.current={adapter,workspaceId};
    commitConversationTurns([]);setConversationCursor(null);setConversationHistoryError(null);setConversationHistoryPending(false);
    void readConversationTurns();
    return()=>{conversationGenerationRef.current++;conversationReadRef.current?.abort();};
  },[adapter,workspaceId]);
  const historyCitationHref=citation=>{
    if(typeof adapter?.citationUrl!=="function")return null;
    try{
      const href=adapter.citationUrl(citation);
      const prefix=`/bff/api/workspaces/${encodeURIComponent(workspaceId)}/citations/${encodeURIComponent(citation.citation_id)}/content?notebook_id=`;
      if(typeof href!=="string")return null;
      if(href===`#/citations/${encodeURIComponent(citation.citation_id)}?page=${citation.page}`)return href;
      if(!href.startsWith(prefix))return null;
      const tail=href.slice(prefix.length).split("#");
      if(tail.length>2||!isSafeDtoId(tail[0])||(tail[1]!==undefined&&tail[1]!==`page=${citation.page}`))return null;
      if(adapter.notebookContext?.notebook_id&&tail[0]!==adapter.notebookContext.notebook_id)return null;
      return href;
    }catch{return null;}
  };
  const updateConversationRequest=(token,values)=>{
    if(!token.clientRequestId)return;
    commitConversationTurns(previous=>previous.map(turn=>{
      if(turn.client_request_id!==token.clientRequestId)return turn;
      if(!turn.turn_id.startsWith("pending-")&&["completed","failed","unavailable","no_knowledge","no_relevant_evidence"].includes(turn.state))return turn;
      return {...turn,...values,model:values.state==="completed"?turn.model:null};
    }));
  };
  const [questionAuthPending, setQuestionAuthPending] = useState(false);
  const [questionAuthError, setQuestionAuthError] = useState(null);
  const [reportTitle, setReportTitle] = useState("");
  const [reportPurpose, setReportPurpose] = useState("");
  const [reportPending, setReportPending] = useState(false);
  const [settingsMenuOpen, setSettingsMenuOpen] = useState(false);
  const [modalView, setModalView] = useState(null);
  const [providerCloseGuard,setProviderCloseGuard]=useState(false);
  const [loadRevision, setLoadRevision] = useState(0);
  useEffect(()=>{if(loadRevision>0)void readConversationTurns();},[loadRevision]);
  const [knowledgePackages, setKnowledgePackages] = useState([]);
  const [connectors, setConnectors] = useState([]);
  const [lawSourcesOnly,setLawSourcesOnly]=useState(false);
  const [connectorSafeError, setConnectorSafeError] = useState(null);
  const [connectorPending,setConnectorPending]=useState(false);
  const reconnectRequestRef=useRef(null);
  useEffect(()=>{
    reconnectRequestRef.current?.controller.abort();reconnectRequestRef.current=null;setConnectorPending(false);
    return()=>{reconnectRequestRef.current?.controller.abort();reconnectRequestRef.current=null;};
  },[workspaceId,adapter]);
  const [selectedKnowledgeId, setSelectedKnowledgeId] = useState(null);
  const [knowledgeSafeError, setKnowledgeSafeError] = useState(null);
  const [operationsStatus, setOperationsStatus] = useState(null);
  const [operationsPending, setOperationsPending] = useState(false);
  const [operationsSafeError, setOperationsSafeError] = useState(null);
  const [outputSettings, setOutputSettings] = useState(null);
  const [outputDraft, setOutputDraft] = useState(null);
  const [outputSettingsPending, setOutputSettingsPending] = useState(false);
  const [outputSettingsSafeError, setOutputSettingsSafeError] = useState(null);
  const [outputSettingsDirty, setOutputSettingsDirty] = useState(false);
  const [outputCloseGuard, setOutputCloseGuard] = useState(false);
  const outputSessionRef = useRef(null);
  const outputRequestRef = useRef(null);
  const outputScopeRef = useRef(null);
  outputScopeRef.current = { adapter, workspaceId };
  const [syncOperations, setSyncOperations] = useState([]);
  const [syncPending, setSyncPending] = useState(false);
  const [syncSafeError, setSyncSafeError] = useState(null);
  const [syncSelections, setSyncSelections] = useState({});
  const syncSessionRef = useRef(null);
  const syncRequestRef = useRef(null);
  const syncScopeRef = useRef(null);
  syncScopeRef.current = { adapter, workspaceId };
  const [syncFailurePhase, setSyncFailurePhase] = useState(null);
  const [egressPolicy, setEgressPolicy] = useState(null);
  const [egressPolicyPending, setEgressPolicyPending] = useState(false);
  const [egressPolicySafeError, setEgressPolicySafeError] = useState(null);
  const [licenseView, setLicenseView] = useState(null);
  const [licensePending, setLicensePending] = useState(false);
  const [licenseSafeError, setLicenseSafeError] = useState(null);
  const [manualManifest, setManualManifest] = useState(null);
  const [manualDocument, setManualDocument] = useState(null);
  const [manualSearch, setManualSearch] = useState("");
  const [manualPending, setManualPending] = useState(false);
  const [manualSafeError, setManualSafeError] = useState(null);
  const [sourceAction, setSourceAction] = useState(null);
  const [sourceActionKind, setSourceActionKind] = useState("unbind");
  const [sourceActionPending, setSourceActionPending] = useState(false);
  const [sourceActionSafeError, setSourceActionSafeError] = useState(null);
  const [sourceDeletionRequests, setSourceDeletionRequests] = useState([]);
  const sourceDeletionPasswordRef = useRef(null);
  const sourceDeletionRequestRef = useRef(null);
  const [sourceAddOpen, setSourceAddOpen] = useState(false);
  const [sourceAddMode, setSourceAddMode] = useState("file");
  const [sourceAddText, setSourceAddText] = useState("");
  const [sourceAddError, setSourceAddError] = useState(null);
  const [sourceAddPending, setSourceAddPending] = useState(false);
  const [driveFile, setDriveFile] = useState(null);
  const driveInputRef = useRef(null);
  const driveSubmitRef = useRef(false);
  const [sourceRetryError, setSourceRetryError] = useState(null);
  const [sourceRetryTarget,setSourceRetryTarget]=useState(null);
  const [sourceRetryMode,setSourceRetryMode]=useState("");
  const [sourceRetrySaved,setSourceRetrySaved]=useState(null);
  const sourceRetrySubmitting=useRef(false);
  const sourceRetryGeneration=useRef(0);
  const [imageCitation, setImageCitation] = useState(null);
  const [imageCitationLoaded, setImageCitationLoaded] = useState(false);
  const [imageCitationError, setImageCitationError] = useState(null);
  const [sheetCitation, setSheetCitation] = useState(null);
  const sheetRequestRef = useRef(null);
  const [docxCitation, setDocxCitation] = useState(null);
  const [lawCitation,setLawCitation]=useState(null);
  const lawCitationRequest=useRef(null);
  useEffect(()=>{setLawCitation(null);return()=>{lawCitationRequest.current?.abort();lawCitationRequest.current=null;};},[adapter,workspaceId]);
  const docxRequestRef = useRef(null);
  const sourceRetryKeysRef = useRef(new Map());
  useEffect(()=>{
    sourceRetryGeneration.current++;
    sourceRetryKeysRef.current.clear();sourceRetrySubmitting.current=false;
    setSourceRetryTarget(null);setSourceRetryMode("");setSourceRetrySaved(null);setSourceRetryError(null);
    return()=>{sourceRetryGeneration.current++;};
  },[adapter,workspaceId]);
  const pollControllerRef = useRef(null);
  const reportIdempotencyRef = useRef(null);
  const lifetimeControllerRef = useRef(null);
  const workspaceMountedRef = useRef(false);
  const citationUrlsRef = useRef(new Set());
  const questionEpochRef = useRef(0);
  const questionRequestRef = useRef(null);
  const questionPasswordRef = useRef(null);
  const workspaceLoadEpochRef = useRef(0);
  const modalRef = useRef(null);
  const modalOpenerRef = useRef(null);
  const settingsButtonRef = useRef(null);
  const syncPasswordRef = useRef(null);
  const licenseFileRef = useRef(null);
  const licensePasswordRef = useRef(null);

  const cancelQuestionRequest = () => {
    questionEpochRef.current += 1;
    const request = questionRequestRef.current;
    questionRequestRef.current = null;
    if(request)updateConversationRequest(request,{state:"uncertain",response:{kind:"system",text:"처리 결과를 확인해 주세요. 요청 취소가 서버 처리를 중단했음을 보장하지 않습니다.",insufficient:true,next_actions:[],citations:[]}});
    request?.controller.abort();
    request?.resolveAuthorization?.(null);
    if (questionPasswordRef.current) questionPasswordRef.current.value = "";
    setModalView(current => current === "question-step-up" ? null : current);
    setQuestionAuthPending(false);
    setQuestionAuthError(null);
    setQuestionPending(false);
  };

  useEffect(() => {
    if (!modalView) return undefined;
    const first = modalRef.current?.querySelector?.("button:not([disabled]), [href], input:not([disabled]), select:not([disabled]), textarea:not([disabled])");
    first?.focus();
    return undefined;
  }, [modalView]);

  useEffect(() => {
    if (!adapter || typeof adapter.listSources !== "function" || !workspaceId) return undefined;
    cancelQuestionRequest();
    const loadQuestionEpoch = questionEpochRef.current;
    const loadEpoch = workspaceLoadEpochRef.current + 1;
    workspaceLoadEpochRef.current = loadEpoch;
    const controller = new AbortController();
    const isCurrent = () => !controller.signal.aborted && workspaceLoadEpochRef.current === loadEpoch;
    setLawSourcesOnly(false);
    const sourceContextEmpty = Array.isArray(adapter.notebookContext?.sources)
      && adapter.notebookContext.sources.length === 0;
    if (sourceContextEmpty) {
      setViewState(createProductWorkspaceState({ status: "empty" }));
    }
    const listSources = async () => {
      const retryDelays = [250, 750, 1500];
      for (let attempt = 0; attempt <= retryDelays.length; attempt += 1) {
        try {
          return await adapter.listSources({ signal: controller.signal });
        } catch (error) {
          if (!isRetryableSourceLoadError(error) || controller.signal.aborted || attempt === retryDelays.length) {
            throw error;
          }
          await new Promise((resolve, reject) => {
            const timer = setTimeout(resolve, retryDelays[attempt]);
            controller.signal.addEventListener("abort", () => {
              clearTimeout(timer);
              reject(new Error("REQUEST_ABORTED"));
            }, { once: true });
          });
        }
      }
      throw new Error("SOURCE_LIST_FAILED");
    };
    Promise.allSettled([
      // The notebook context is a separate snapshot and may still be empty
      // immediately after an upload. Always reload the canonical Source list
      // so newly registered Sources are not hidden by that stale snapshot.
      listSources(),
      (typeof adapter.listKnowledgePackages === "function"
        ? adapter.listKnowledgePackages({ signal: controller.signal })
        : Promise.resolve([])),
      (typeof adapter.listConnectors === "function"
        ? adapter.listConnectors({ signal: controller.signal })
        : Promise.resolve([])),
      (typeof adapter.listStudioOutputs === "function"
        ? (typeof adapter.listProductStudioOutputs === "function"
          ? adapter.listProductStudioOutputs({ signal: controller.signal })
          : adapter.listStudioOutputs({ signal: controller.signal }))
        : Promise.resolve([])),
      (typeof adapter.loadNotebookConversation === "function"
        ? adapter.loadNotebookConversation({ signal: controller.signal })
        : Promise.resolve(null)),
      (typeof adapter.listSourceDeletionRequests === "function"
        ? adapter.listSourceDeletionRequests({ signal: controller.signal })
        : Promise.resolve([])),
    ]).then(async ([sourceResult, knowledgeResult, connectorResult, studioResult, conversationResult, deletionResult]) => {
      if (!isCurrent()) return;

      let projected = [];
      let sourceSafeError = null;
      if (sourceResult.status === "fulfilled") {
        try {
          if (!Array.isArray(sourceResult.value)) throw new Error("SOURCE_LIST_RESPONSE_INVALID");
          projected = await Promise.all(sourceResult.value.map(async (source) => {
            // Namespace는 후보 구분만 한다. 실제 sealed snapshot/currentVersion
            // 접근 GET 성공으로만 웹 availability를 확정한다.
            let websiteSnapshot=null,lawSnapshot=null;
            const lawCandidate=source.source_id.startsWith('law-source-') || source.source_version_id.startsWith('law-version-');
            if(lawCandidate && source.source_state==='ready' && typeof adapter.getOpenLawSnapshot==='function'){
              try{const data=await adapter.getOpenLawSnapshot(source.source_version_id,{signal:controller.signal});
                if(data.snapshot.source_id===source.source_id && data.snapshot.source_version_id===source.source_version_id)lawSnapshot=data.snapshot;
              }catch{/* 현재 서버 접근·snapshot 검증 성공 전에는 사용 불가. */}
            }
            if(source.source_state==='ready' && source.source_id.startsWith('web-source-') && source.source_version_id.startsWith('web-version-') && typeof adapter.getWebsiteSnapshot==='function'){
              try{
                const snapshot=await adapter.getWebsiteSnapshot(source.source_version_id,{signal:controller.signal});
                if(snapshot.source_id===source.source_id && snapshot.source_version_id===source.source_version_id)websiteSnapshot=snapshot;
              }catch{/* fail closed. Native 처리 상태는 바꾸지 않는다. */}
            }
            return {
            sourceId: source.source_id,
            sourceVersionId: source.source_version_id,
            filename: source.filename,
            sourceState: source.source_state,
            processingState: source.processing_state,
            jobState: source.job_state,
            websiteSnapshot,
            lawSnapshot,lawCandidate,
            ready: source.source_state === "ready"
              && (lawCandidate ? lawSnapshot!==null : websiteSnapshot!==null || (source.processing_state === "completed"
              && source.job_state === "completed"))
          }; }));
        } catch (error) {
          sourceSafeError = safeErrorCode(error, "SOURCE_LIST_FAILED");
        }
      } else {
        sourceSafeError = safeErrorCode(sourceResult.reason, "SOURCE_LIST_FAILED");
      }
      if (!isCurrent()) return;
      if (initialSourceVersionId && !projected.some(source=>source.sourceVersionId===initialSourceVersionId)) sourceSafeError = "SOURCE_UNAVAILABLE";
      setViewState((current) => ({
        ...current,
        status: sourceSafeError ? "error" : projected.length ? "ready" : "empty",
        safeError: sourceSafeError,
        sources: sourceSafeError ? current.sources : projected,
        selectedSource: sourceSafeError
          ? current.selectedSource
          : projected.find((source) => source.ready
            && source.sourceId === current.selectedSource?.sourceId
            && source.sourceVersionId === current.selectedSource?.sourceVersionId)
            ?? (initialSourceVersionId ? projected.find(source=>source.sourceVersionId===initialSourceVersionId) : null)
            ?? (current.selectedSource == null && conversationResult.status === "fulfilled"
              && Array.isArray(conversationResult.value?.citations)
              ? projected.find((source) => source.ready && conversationResult.value.citations.some((citation) =>
                citation?.source_id === source.sourceId && citation?.source_version_id === source.sourceVersionId))
              : null)
            ?? projected.find((source) => source.ready) ?? null,
      }));

      let projectedKnowledge = [];
      let nextKnowledgeError = null;
      if (knowledgeResult.status === "fulfilled") {
        try {
          if (!Array.isArray(knowledgeResult.value)) throw new Error("KNOWLEDGE_PACKAGE_LIST_INVALID");
          projectedKnowledge = knowledgeResult.value.map((item) => ({
            packageId: item.package_id,
            producerLabel: KNOWLEDGE_PRODUCER_LABELS[item.producer] ?? "Daon",
            producerVersion: item.producer_version,
            authorityLabel: item.authority === "approved" ? "승인" : "검증 필요",
            registrationLabel: item.registration_state === "registered" ? "등록됨" : "등록 확인 필요",
          }));
        } catch (error) {
          nextKnowledgeError = safeErrorCode(error, "KNOWLEDGE_PACKAGE_LIST_FAILED");
        }
      } else {
        nextKnowledgeError = safeErrorCode(knowledgeResult.reason, "KNOWLEDGE_PACKAGE_LIST_FAILED");
      }
      setKnowledgePackages(projectedKnowledge);
      setSelectedKnowledgeId((current) => projectedKnowledge.some((item) => item.packageId === current) ? current : null);
      setKnowledgeSafeError(nextKnowledgeError);
      if (connectorResult.status === "fulfilled" && Array.isArray(connectorResult.value)) {
        setConnectors(connectorResult.value);
        setConnectorSafeError(null);
      } else {
        setConnectors([]);
        setConnectorSafeError(safeErrorCode(connectorResult.reason, "CONNECTOR_LIST_FAILED"));
      }

      if (deletionResult.status === "fulfilled" && Array.isArray(deletionResult.value)) {
        setSourceDeletionRequests(deletionResult.value);
      } else {
        setSourceDeletionRequests([]);
      }

      let studioOutputs = [];
      let studioLocks = [];
      let studioSafeError = null;
      if (studioResult.status === "fulfilled") {
        try {
          const studioValue = studioResult.value;
          studioOutputs = Array.isArray(studioValue) ? studioValue : studioValue?.outputs ?? [];
          studioLocks = Array.isArray(studioValue?.studioLocks) ? studioValue.studioLocks : [];
          if (!Array.isArray(studioOutputs)) throw new Error("STUDIO_LIST_RESPONSE_INVALID");
        } catch (error) {
          studioOutputs = [];
          studioLocks = [];
          studioSafeError = safeErrorCode(error, "STUDIO_LIST_FAILED");
        }
      } else {
        studioSafeError = safeErrorCode(studioResult.reason, "STUDIO_LIST_FAILED");
      }
      setViewState((current) => ({
        ...current,
        studioOutputs,
        studioLocks,
        studioStatus: studioSafeError ? "unavailable" : "ready",
        studioSafeError,
        // Previous conversation history is optional.  A stale or unavailable
        // history record must never block a new question in the selected
        // Notebook/Source context; the current question will create its own
        // grounded run.  Keep the failure out of the blocking alert path.
        answer: questionEpochRef.current === loadQuestionEpoch
          ? (conversationResult.status === "fulfilled" ? conversationResult.value : null) : current.answer,
        conversationSafeError: questionEpochRef.current === loadQuestionEpoch ? null : current.conversationSafeError,
      }));
    });
    return () => {
      controller.abort();
      if (workspaceLoadEpochRef.current === loadEpoch) workspaceLoadEpochRef.current += 1;
    };
  }, [adapter, workspaceId, loadRevision, initialSourceVersionId]);

  useEffect(() => {
    const controller = new AbortController();
    workspaceMountedRef.current = true;
    lifetimeControllerRef.current = controller;
    return () => {
      questionEpochRef.current += 1;
      questionRequestRef.current?.controller.abort();
      questionRequestRef.current?.resolveAuthorization?.(null);
      if (questionPasswordRef.current) questionPasswordRef.current.value = "";
      questionRequestRef.current = null;
      outputRequestRef.current?.controller.abort();
      outputRequestRef.current = null;
      outputSessionRef.current = null;
      syncRequestRef.current?.controller.abort();
      if (syncRequestRef.current?.passwordInput) syncRequestRef.current.passwordInput.value = "";
      if (syncPasswordRef.current) syncPasswordRef.current.value = "";
      syncRequestRef.current = null;
      syncSessionRef.current = null;
      pollControllerRef.current?.abort();
      controller.abort();
      if (lifetimeControllerRef.current === controller) {
        lifetimeControllerRef.current = null;
        workspaceMountedRef.current = false;
      }
      for (const url of citationUrlsRef.current) globalThis.URL?.revokeObjectURL?.(url);
      citationUrlsRef.current.clear();
    };
  }, []);

  const resetDriveSource = () => {
    setDriveFile(null);
    if (driveInputRef.current) driveInputRef.current.value = "";
  };
  useEffect(() => { resetDriveSource(); }, [workspaceId, adapter]);

  const driveFileError = (file) => {
    if (!file) return null;
    const types = {
      pdf: "application/pdf", xlsx: "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
      docx: "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
      pptx: "application/vnd.openxmlformats-officedocument.presentationml.presentation",
      csv: "text/csv", txt: "text/plain", md: "text/markdown", png: "image/png",
      jpg: "image/jpeg", jpeg: "image/jpeg", wav: "audio/wav",
    };
    const name = file.name;
    const extension = typeof name === "string" ? name.split(".").pop().toLowerCase() : "";
    if (!name || name.length > 255 || /[/\\\u0000-\u001f]/u.test(name)
        || !Object.hasOwn(types, extension)) return "지원하지 않는 파일입니다. Google 바로가기는 지원 형식으로 내보낸 뒤 선택하세요.";
    if (file.type !== types[extension]) return "지원하지 않는 MIME 형식입니다. 파일의 실제 형식을 확인하세요.";
    if (!Number.isSafeInteger(file.size) || file.size < 1) return "빈 파일은 가져올 수 없습니다.";
    if (file.size > 25 * 1024 * 1024) return "25MiB 이하 파일만 가져올 수 있습니다.";
    return null;
  };
  const submitDriveSource = async () => {
    if (driveSubmitRef.current || sourceAddPending || !driveFile || driveFileError(driveFile)) return;
    driveSubmitRef.current = true;
    try { await uploadSource(driveFile); }
    finally { driveSubmitRef.current = false; }
  };

  const uploadSource = async (eventOrFile) => {
    const fileInput = eventOrFile?.currentTarget;
    const file = fileInput?.files?.[0] ?? eventOrFile;
    if (fileInput) fileInput.value = "";
    if (!adapter || !file) return;
    return runSourceSubmission((signal) => typeof adapter.uploadSource === "function"
      ? adapter.uploadSource(file, { signal }) : adapter.uploadPdf(file, { signal }));
  };

  const runSourceSubmission = async (submit, onSubmissionFailure = null) => {
    setSourceAddPending(true);
    setSourceAddError(null);
    pollControllerRef.current?.abort();
    const controller = new AbortController();
    pollControllerRef.current = controller;
    const pollOptions = normalizeProcessingPollOptions(processingPollOptions);
    const deadlineAt = pollOptions.now() + pollOptions.deadlineMs;
    let deadlineExpired = false;
    const deadlineTimer = setTimeout(() => {
      deadlineExpired = true;
      controller.abort(abortError());
    }, pollOptions.deadlineMs);
    const remainingMs = () => deadlineAt - pollOptions.now();
    setViewState(createProductWorkspaceState({ status: "loading" }));
    setProcessing(null);
    // The upload endpoint registers the Source before asynchronous document
    // processing starts. Keep that registration visible even if the optional
    // processing/provider step later reaches a terminal failure.
    let acceptedSubmission = false;
    try {
      const submission = await awaitAbortable(
        submit(controller.signal),
        controller.signal
      );
      if (!isSafeDtoId(submission?.processing_run_id) || !isSafeDtoId(submission?.source_id) || !isSafeDtoId(submission?.source_version_id)) {
        throw new Error("SOURCE_UPLOAD_RESPONSE_INVALID");
      }
      acceptedSubmission = true;
      while (!controller.signal.aborted) {
        const remainingBeforeRequest = remainingMs();
        if (remainingBeforeRequest <= 0) {
          deadlineExpired = true;
          controller.abort(abortError());
          throw new Error("PROCESSING_TIMEOUT");
        }
        const requestController = new AbortController();
        const requestTimeout = setTimeout(
          () => requestController.abort(abortError()),
          Math.min(pollOptions.statusRequestTimeoutMs, remainingBeforeRequest)
        );
        const linkedRequest = linkedAbortSignal([controller.signal, requestController.signal]);
        let processingState;
        try {
          processingState = await awaitAbortable(
            adapter.getProcessingStatus(submission.processing_run_id, { signal: linkedRequest.signal }),
            linkedRequest.signal
          );
        } catch (error) {
          if (controller.signal.aborted) throw error;
          if (!requestController.signal.aborted) throw error;
          const remainingAfterTimeout = remainingMs();
          if (remainingAfterTimeout <= 0) {
            deadlineExpired = true;
            controller.abort(abortError());
            throw new Error("PROCESSING_TIMEOUT");
          }
          await pollOptions.wait(Math.min(pollOptions.intervalMs, remainingAfterTimeout), controller.signal);
          continue;
        } finally {
          clearTimeout(requestTimeout);
          linkedRequest.cleanup();
        }
        if (controller.signal.aborted) return;
        setProcessing(processingState);
        if (processingDisposition(processingState, submission) === "ready") {
          setProcessing(null);
          setLoadRevision((current) => current + 1);
          return;
        }
        setViewState(createProductWorkspaceState({ status: "loading" }));
        const remainingBeforeWait = remainingMs();
        if (remainingBeforeWait <= 0) {
          deadlineExpired = true;
          controller.abort(abortError());
          throw new Error("PROCESSING_TIMEOUT");
        }
        await pollOptions.wait(Math.min(pollOptions.intervalMs, remainingBeforeWait), controller.signal);
      }
    } catch (error) {
      if (controller.signal.aborted && !deadlineExpired) return;
      if (acceptedSubmission) {
        // Registration succeeded; reload the canonical Source list so the
        // user can see the Source and its processing state instead of being
        // told that the upload itself failed.
        setProcessing(null);
        setViewState(createProductWorkspaceState({ status: "loading" }));
        setLoadRevision((current) => current + 1);
        return;
      }
      const errorCode = deadlineExpired ? "PROCESSING_TIMEOUT" : safeErrorCode(error, "SOURCE_UPLOAD_FAILED");
      if (onSubmissionFailure) {
        onSubmissionFailure(errorCode);
        setLoadRevision((current) => current + 1);
        return;
      }
      setSourceAddError(errorCode);
      // 등록 실패와 기존 자료의 조회 권한은 별개다. 정상 목록을 재조회한다.
      setViewState(createProductWorkspaceState({ status: "loading" }));
      setLoadRevision((current) => current + 1);
    } finally {
      clearTimeout(deadlineTimer);
      if (pollControllerRef.current === controller) pollControllerRef.current = null;
      setSourceAddPending(false);
    }
  };

  const retrySource = async (source, mode) => {
    if (sourceAddPending || sourceRetrySubmitting.current || typeof adapter?.reprocessSource !== "function") return;
    if(!["","auto","local_only","pinned"].includes(mode)){setSourceRetryError("SOURCE_REPROCESS_INPUT_INVALID");return;}
    setSourceRetryError(null);
    const retryIdentity = `${source.sourceId}:${source.sourceVersionId}`;
    const pending=sourceRetryKeysRef.current.get(retryIdentity);
    if(pending&&pending.mode!==mode){setSourceRetryError("SOURCE_REPROCESS_MODE_PENDING");return;}
    sourceRetrySubmitting.current=true;
    const generation=sourceRetryGeneration.current;
    try {
      if (!pending) sourceRetryKeysRef.current.set(retryIdentity,{key:`source-retry-${secureUuid()}`,mode});
      await runSourceSubmission(async (signal) => {
        const result = await adapter.reprocessSource(source, { signal, idempotencyKey: sourceRetryKeysRef.current.get(retryIdentity).key,
          ...(mode?{processingMode:mode}:{}) });
        if(generation!==sourceRetryGeneration.current||signal.aborted)throw abortError();
        if(mode&&(result?.processing_mode!==mode||typeof result.auto_requeue_configured!=="boolean"
          ||(mode==="pinned"&&result.auto_requeue_configured!==false)))throw new Error("SOURCE_REPROCESS_RESPONSE_INVALID");
        sourceRetryKeysRef.current.delete(retryIdentity);
        setSourceRetryTarget(null);
        setSourceRetrySaved(mode?`Mode ${result.processing_mode} 저장 · ${result.auto_requeue_configured
          ?"자동 재큐 설정 활성 (실행 성공을 뜻하지 않음)":"자동 재큐는 현재 비활성"}`:"재처리 요청 접수 · 자동 정책 미지정");
        return result;
      }, (code) => {if(generation===sourceRetryGeneration.current)setSourceRetryError(code);});
    } catch (error) { if(generation===sourceRetryGeneration.current)setSourceRetryError(safeErrorCode(error, "SOURCE_REPROCESS_FAILED")); }
    finally{if(generation===sourceRetryGeneration.current)sourceRetrySubmitting.current=false;}
  };

  const submitPastedText = async () => {
    const value = sourceAddText.trim();
    if (!value) { setSourceAddError("EMPTY_SOURCE"); return; }
    const file = new File([value], `pasted-text-${Date.now()}.txt`, { type: "text/plain" });
    await uploadSource(file);
    setSourceAddText("");
  };

  const registerMcpConnector = async () => {
    if (typeof adapter?.registerConnector !== "function" || sourceAddPending) return;
    setSourceAddPending(true);
    setSourceAddError(null);
    try {
      await adapter.registerConnector({ kind: "mcp", name: "국가법령정보센터" }, {
        signal: lifetimeControllerRef.current?.signal,
      });
      setSourceAddOpen(false);
      setLoadRevision((current) => current + 1);
    } catch (error) {
      setSourceAddError(safeErrorCode(error, "CONNECTOR_REGISTER_FAILED"));
    } finally {
      setSourceAddPending(false);
    }
  };

  const unregisterMcpConnector = async (connector) => {
    if (connector?.kind !== "mcp" || typeof adapter?.unregisterConnector !== "function") return;
    try {
      let scope='';
      if(connector.connector_id==='mcp-open-law-go-kr'){
        if(typeof adapter.listConnectorSources!=='function')throw Error('OPEN_LAW_ACCESS_DENIED');
        const sources=await adapter.listConnectorSources(connector.connector_id,{signal:lifetimeControllerRef.current?.signal});
        scope=`본인 접근 가능 Notebook의 활성 binding ${sources.length}개를 해제합니다. `;
      }
      if (!globalThis.confirm?.(`${connector.name} 연결을 즉시 삭제할까요? ${scope}외부 서버의 원본 데이터는 삭제되지 않습니다.`)) return;
      await adapter.unregisterConnector(connector.connector_id, { signal: lifetimeControllerRef.current?.signal });
      setLoadRevision((current) => current + 1);
    } catch (error) {
      setConnectorSafeError(safeErrorCode(error, "CONNECTOR_DELETE_FAILED"));
    }
  };

  const refreshLawConnector=async()=>{
    if(typeof adapter?.listConnectors!=='function')return;
    const epoch=workspaceLoadEpochRef.current,signal=lifetimeControllerRef.current?.signal;
    try{const values=await adapter.listConnectors({signal});
      if(!signal?.aborted && epoch===workspaceLoadEpochRef.current){
        setConnectors(values);
        setLoadRevision(current=>current+1);
      }
    }catch(error){if(!signal?.aborted && epoch===workspaceLoadEpochRef.current)setConnectorSafeError(safeErrorCode(error,'CONNECTOR_LIST_FAILED'));}
  };
  const reconnectLawConnector=async connector=>{
    if(reconnectRequestRef.current || typeof adapter?.reconnectConnector!=='function')return;
    const token={controller:new AbortController()},epoch=workspaceLoadEpochRef.current,signal=token.controller.signal;
    reconnectRequestRef.current=token;setConnectorPending(true);setConnectorSafeError(null);
    const current=()=>!signal?.aborted && epoch===workspaceLoadEpochRef.current && reconnectRequestRef.current===token;
    try{const value=await adapter.reconnectConnector(connector.connector_id,{signal});
      if(current()){
        setConnectors(items=>items.map(item=>item.connector_id===value.connector_id?value:item));
        setSourceAddMode('mcp');setSourceAddOpen(true);
      }
    }catch(error){if(current())setConnectorSafeError(safeErrorCode(error,'CONNECTOR_RECONNECT_FAILED'));}
    finally{if(reconnectRequestRef.current===token){reconnectRequestRef.current=null;setConnectorPending(false);}}
  };

  const reconnectNonLawConnector = async (connector) => {
    if (reconnectRequestRef.current || typeof adapter?.reconnectConnector !== "function") return;
    const token = { controller: new AbortController() }, epoch = workspaceLoadEpochRef.current;
    const signal = token.controller.signal;
    reconnectRequestRef.current = token;
    setConnectorPending(true);
    setConnectorSafeError(null);
    const current = () => !signal.aborted && epoch === workspaceLoadEpochRef.current
      && reconnectRequestRef.current === token;
    try {
      await adapter.reconnectConnector(connector.connector_id, { signal });
      if (current()) setLoadRevision(revision => revision + 1);
    } catch (error) {
      if (current()) setConnectorSafeError(safeErrorCode(error, "CONNECTOR_RECONNECT_FAILED"));
    } finally {
      if (reconnectRequestRef.current === token) {
        reconnectRequestRef.current = null;
        setConnectorPending(false);
      }
    }
  };

  const confirmSourceUnbind = async () => {
    if (!sourceAction || typeof adapter?.unbindSource !== "function" || sourceActionPending) return;
    setSourceActionPending(true);
    setSourceActionSafeError(null);
    try {
      await adapter.unbindSource(sourceAction, {
        bindingEtag: adapter.bindingEtag,
        idempotencyKey: secureUuid(),
        signal: lifetimeControllerRef.current?.signal,
      });
      setSourceAction(null);
      setLoadRevision((current) => current + 1);
    } catch (error) {
      setSourceActionSafeError(safeErrorCode(error, "SOURCE_UNBIND_FAILED"));
    } finally {
      setSourceActionPending(false);
    }
  };

  const confirmSourceDeletionRequest = async () => {
    if (!sourceAction || typeof adapter?.requestSourceDeletion !== "function"
        || typeof adapter?.issueSourceDeletionStepUp !== "function" || sourceDeletionRequestRef.current || sourceActionPending) return;
    const token = {}, epoch = workspaceLoadEpochRef.current, signal = lifetimeControllerRef.current?.signal;
    const password = sourceDeletionPasswordRef.current?.value ?? "";
    if (sourceDeletionPasswordRef.current) sourceDeletionPasswordRef.current.value = "";
    sourceDeletionRequestRef.current = token;
    const current = () => !signal?.aborted && workspaceMountedRef.current
      && epoch === workspaceLoadEpochRef.current && sourceDeletionRequestRef.current === token;
    setSourceActionPending(true);
    setSourceActionSafeError(null);
    try {
      const stepUpAuthorization = await adapter.issueSourceDeletionStepUp(sourceAction.sourceId, password,
        { idempotencyKey: secureUuid(), signal });
      if (!current()) return;
      // Canonical Source IDs are stable across reloads; no password or grant is
      // retained in UI state. The server also binds this key to actor/Notebook.
      const result = await adapter.requestSourceDeletion(sourceAction,
        { idempotencyKey: sourceAction.sourceId, stepUpAuthorization, signal });
      if (current()) {
        setSourceAction({ ...sourceAction, deletionRequest: result });
        setLoadRevision((value) => value + 1);
      }
    } catch (error) {
      if (current()) setSourceActionSafeError(safeErrorCode(error, "DELETION_REQUEST_FAILED"));
    } finally {
      if (sourceDeletionRequestRef.current === token) { sourceDeletionRequestRef.current = null; setSourceActionPending(false); }
    }
  };

  const confirmSourceDeletionCancel = async () => {
    const request = sourceAction?.deletionRequest;
    if (!request || typeof adapter?.cancelSourceDeletionRequest !== "function" || sourceActionPending) return;
    setSourceActionPending(true);
    setSourceActionSafeError(null);
    try {
      const result = await adapter.cancelSourceDeletionRequest(request.data.request_id, {
        etag: request.etag,
        idempotencyKey: secureUuid(),
        signal: lifetimeControllerRef.current?.signal,
      });
      setSourceAction({ ...sourceAction, deletionRequest: result });
      setLoadRevision((current) => current + 1);
    } catch (error) {
      setSourceActionSafeError(safeErrorCode(error, "DELETION_CANCEL_FAILED"));
    } finally {
      setSourceActionPending(false);
    }
  };

  const askQuestion = async (event) => {
    event.preventDefault();
    if (!adapter || !question.trim() || questionRequestRef.current || !workspaceMountedRef.current || !canSubmitQuestion()) return;
    const conversationMode = classifyConversationIntent(question);
    const questionEpoch = ++questionEpochRef.current;
    const token = { controller: new AbortController(), rawQuestion:question };
    const signal = token.controller.signal;
    questionRequestRef.current = token;
    setQuestionPending(true);
    setViewState(current => ({ ...current, conversationSafeError: null }));
    const current = () => !signal.aborted && workspaceMountedRef.current
      && questionEpoch === questionEpochRef.current && questionRequestRef.current === token;
    token.isCurrent = current;
    try {
      const idempotencyKey = secureUuid();
      token.clientRequestId=idempotencyKey;
      const localTurn={turn_id:`pending-${idempotencyKey}`,question:question.trim(),response:null,state:"pending",
        submitted_at:new Date().toISOString(),model:null,client_request_id:idempotencyKey};
      commitConversationTurns(previous=>mergeNotebookConversationTurns(previous,[localTurn]));
      const knowledgeContext = viewState.selectedSource || selectedKnowledgeId
        ? buildQuestionKnowledgeContext(viewState.selectedSource, selectedKnowledgeId)
        : null;
      if (!current()) return;
      // 무Source 사실 질문은 기존 C 사전판정을 유지하고 기본값 저장을 요구하지 않는다.
      const needsDefaultModel=Boolean(workspaceDefault)&&(Boolean(knowledgeContext)||isGeneralConversationIntent(token.rawQuestion));
      const executionVersion=modelReady?notebookModel.view.version:needsDefaultModel?await confirmModelRef.current(token):null;
      if(!current())return;
      const input = { knowledgeContext, question: token.rawQuestion.trim(),...(executionVersion?{modelSettingsVersion:executionVersion}:{}) };
      token.input = input;
      token.options = { signal, idempotencyKey };
      let answer;
      try {
        answer = await adapter.askQuestion(input, token.options);
      } catch (error) {
        if (!current() || error?.message !== "STEP_UP_REQUIRED" || typeof adapter.authorizeQuestion !== "function") throw error;
        updateConversationRequest(token,{state:"awaiting_authorization",response:{kind:"system",text:"추가 인증을 기다리고 있습니다.",insufficient:true,next_actions:[],citations:[]}});
        const authorization = await new Promise(resolve => {
          token.resolveAuthorization = resolve;
          setQuestionAuthError(null);
          modalOpenerRef.current = document.activeElement;
          setModalView("question-step-up");
        });
        token.resolveAuthorization = null;
        if (!current() || !authorization) return;
        answer = isQuestionSystemNotice(authorization) ? authorization
          : await adapter.askQuestion(input, { ...token.options, stepUpAuthorizationId: authorization });
      }
      if (!current()) return;
      if (isQuestionSystemNotice(answer)) {
        updateConversationRequest(token,{turn_id:answer.turn_id,state:answer.grounding_status,
          response:{kind:"system",text:answer.answer,insufficient:true,citations:[],next_actions:answer.next_actions}});
        setQuestion(value=>value===token.rawQuestion?"":value);
        // A notice never becomes a fabricated completed Run or the Studio source.
        setViewState(current=>({...current,conversationSafeError:null}));
        return;
      }
      const safeAnswer = projectSafeQuestionAnswer(
        answer, workspaceId, adapter.citationUrl, viewState.selectedSource, selectedKnowledgeId,
      );
      setViewState((current) => ({
        ...current, answer: safeAnswer,
        conversationSafeError: null,
        answerIntent: conversationMode === "work_support"
          ? (viewState.selectedSource || selectedKnowledgeId ? "work_support_source_backed" : "work_support_ungrounded")
          : conversationMode,
      }));
      updateConversationRequest(token,{state:"completed",response:{kind:"answer",text:safeAnswer.answer,insufficient:safeAnswer.insufficient,next_actions:safeAnswer.next_actions??[],
        citations:safeAnswer.citations.map(({content_url,...citation})=>citation)}});
      setQuestion(value=>value===token.rawQuestion?"":value);
      reportIdempotencyRef.current = null;
    } catch (error) {
      if (!current()) return;
      const failureCode=safeErrorCode(error,"QUESTION_FAILED");
      if(["NOTEBOOK_MODEL_SETTINGS_CHANGED","NOTEBOOK_MODEL_SELECTION_REQUIRED","NOTEBOOK_MODEL_UNAVAILABLE","NOTEBOOK_MODEL_SETTINGS_UNAVAILABLE"].includes(failureCode)){
        setNotebookModel(value=>({...value,status:"error",code:failureCode}));
      }
      updateConversationRequest(token,{state:"uncertain",response:{kind:"system",text:questionFailureMessage(safeErrorCode(error,"QUESTION_FAILED")),insufficient:true,next_actions:[],citations:[]}});
      setViewState((current) => projectQuestionFailureState(
        current,
        new Error(safeErrorCode(error, "QUESTION_FAILED")),
      ));
    } finally {
      if (questionRequestRef.current === token) {
        questionRequestRef.current = null;
        setQuestionPending(false);
        void readConversationTurns();
      }
    }
  };

  const authenticateQuestion = async () => {
    const token = questionRequestRef.current;
    if (!token?.isCurrent() || !token.resolveAuthorization || token.authPending) return;
    const password = questionPasswordRef.current?.value ?? "";
    if (questionPasswordRef.current) questionPasswordRef.current.value = "";
    if (!password) { setQuestionAuthError("추가 인증 비밀번호를 입력해 주세요."); return; }
    token.authPending = true;
    setQuestionAuthPending(true);
    setQuestionAuthError(null);
    try {
      const grant = await adapter.authorizeQuestion({ ...token.input, password }, token.options);
      if (!token.isCurrent()) return;
      if (isQuestionSystemNotice(grant)) {
        setModalView(current=>current==="question-step-up"?null:current);
        token.resolveAuthorization(grant);
        return;
      }
      if (typeof grant?.step_up_authorization_id !== "string" || !grant.step_up_authorization_id) {
        throw new Error("QUESTION_AUTHORIZATION_RESPONSE_INVALID");
      }
      setModalView(current => current === "question-step-up" ? null : current);
      token.resolveAuthorization(grant.step_up_authorization_id);
    } catch {
      if (token.isCurrent()) setQuestionAuthError("추가 인증을 완료하지 못했습니다. 입력과 현재 권한을 확인해 주세요.");
    } finally {
      token.authPending = false;
      if (token.isCurrent()) setQuestionAuthPending(false);
    }
  };

  const selectSource = (source) => {
    if (!source.ready) return;
    cancelQuestionRequest();
    reportIdempotencyRef.current = null;
    setViewState((current) => ({
      ...current,
      selectedSource: current.selectedSource?.sourceId === source.sourceId
        && current.selectedSource?.sourceVersionId === source.sourceVersionId ? null : source,
      answer: null, answerIntent: null,
    }));
  };

  const createReport = async (event) => {
    if (
      !adapter || typeof adapter.createReport !== "function" || reportPending
      || !canCreateGroundedReport(viewState) || !reportTitle.trim() || !reportPurpose.trim()
    ) {
      event.preventDefault();
      return;
    }
    setReportPending(true);
    try {
      const result = await submitGroundedReportForm(event, {
        adapter, state: viewState, title: reportTitle, purpose: reportPurpose,
        idempotencyRef: reportIdempotencyRef, signal: lifetimeControllerRef.current?.signal,
      });
      if (result.submitted) setViewState((current) => ({ ...current, studioOutputs: result.outputs }));
    } catch (error) {
      setViewState((current) => ({ ...current, safeError: safeErrorCode(error, "STUDIO_CREATE_FAILED") }));
    } finally {
      setReportPending(false);
    }
  };

  const reportReady = canCreateGroundedReport(viewState);
  const desktopEditor = typeof desktopOfflineStudio?.editor === "function"
    ? desktopOfflineStudio.editor(viewState)
    : desktopOfflineStudio?.editor;
  const desktopStudio = typeof desktopOfflineStudio?.studio === "function"
    ? desktopOfflineStudio.studio(viewState)
    : desktopOfflineStudio?.studio;
  const loadOperationsStatus = async () => {
    if (typeof adapter?.getOperationsStatus !== "function") {
      setOperationsStatus(null);
      setOperationsSafeError("OPERATIONS_STATUS_UNAVAILABLE");
      return;
    }
    setOperationsPending(true);
    setOperationsSafeError(null);
    try {
      const result = await adapter.getOperationsStatus({ signal: lifetimeControllerRef.current?.signal });
      setOperationsStatus(result);
    } catch (error) {
      setOperationsStatus(null);
      setOperationsSafeError(safeErrorCode(error, "OPERATIONS_STATUS_UNAVAILABLE"));
    } finally {
      setOperationsPending(false);
    }
  };
  const invalidateOutputSession = () => {
    outputRequestRef.current?.controller.abort();
    outputRequestRef.current = null;
    outputSessionRef.current = null;
  };
  const ownsOutputRequest = (request) => outputRequestRef.current === request
    && outputSessionRef.current === request.session && !request.controller.signal.aborted
    && workspaceMountedRef.current && outputScopeRef.current.adapter === request.session.adapter
    && outputScopeRef.current.workspaceId === request.session.workspaceId;
  const loadOutputVersionSettings = async () => {
    const session = outputSessionRef.current;
    if (!session || outputRequestRef.current?.kind === "save") return;
    outputRequestRef.current?.controller.abort();
    const request = { session, kind: "load", controller: new AbortController() };
    outputRequestRef.current = request;
    setOutputSettingsPending(true);
    setOutputSettingsSafeError(null);
    try {
      if (typeof session.adapter?.getOutputVersionSettings !== "function") throw new Error("OUTPUT_VERSION_SETTINGS_UNAVAILABLE");
      const result = await session.adapter.getOutputVersionSettings({ signal: request.controller.signal });
      if (!ownsOutputRequest(request)) return;
      setOutputSettings(result);
      setOutputDraft({ ...result.default_formats });
      setOutputSettingsDirty(false);
      setOutputCloseGuard(false);
    } catch (error) {
      if (ownsOutputRequest(request)) setOutputSettingsSafeError(safeErrorCode(error, "OUTPUT_VERSION_SETTINGS_UNAVAILABLE"));
    } finally {
      if (ownsOutputRequest(request)) {
        outputRequestRef.current = null;
        setOutputSettingsPending(false);
      }
    }
  };
  const saveOutputVersionSettings = async ({ closeAfter = false } = {}) => {
    const session = outputSessionRef.current;
    if (!session || outputRequestRef.current || !outputSettings || !outputDraft || !outputSettingsDirty || typeof session.adapter?.saveOutputVersionSettings !== "function") return;
    const request = { session, kind: "save", controller: new AbortController() };
    outputRequestRef.current = request;
    setOutputSettingsPending(true);
    setOutputSettingsSafeError(null);
    try {
      const saved = await session.adapter.saveOutputVersionSettings({
        default_formats: { ...outputDraft }, version: outputSettings.version, etag: outputSettings.etag,
      }, { signal: request.controller.signal });
      if (!ownsOutputRequest(request)) return;
      setOutputSettings(saved);
      setOutputDraft({ ...saved.default_formats });
      setOutputSettingsDirty(false);
      setOutputCloseGuard(false);
      if (closeAfter) {
        invalidateOutputSession();
        setOutputSettingsPending(false);
        setModalView(null);
        queueMicrotask(() => modalOpenerRef.current?.focus?.());
      }
    } catch (error) {
      if (ownsOutputRequest(request)) setOutputSettingsSafeError(safeErrorCode(error, "OUTPUT_VERSION_SETTINGS_UNAVAILABLE"));
    } finally {
      if (ownsOutputRequest(request)) {
        outputRequestRef.current = null;
        setOutputSettingsPending(false);
      }
    }
  };
  const invalidateSyncSession = () => {
    syncRequestRef.current?.controller.abort();
    if (syncRequestRef.current?.passwordInput) syncRequestRef.current.passwordInput.value = "";
    if (syncPasswordRef.current) syncPasswordRef.current.value = "";
    syncRequestRef.current = null;
    syncSessionRef.current = null;
  };
  const ownsSyncSession = (session) => session && syncSessionRef.current === session
    && workspaceMountedRef.current && syncScopeRef.current.adapter === session.adapter
    && syncScopeRef.current.workspaceId === session.workspaceId;
  const ownsSyncRequest = (request) => syncRequestRef.current === request
    && !request.controller.signal.aborted && ownsSyncSession(request.session);
  const loadSyncOperations = async () => {
    const session = syncSessionRef.current;
    if (!ownsSyncSession(session) || syncRequestRef.current?.kind === "approve") return;
    syncRequestRef.current?.controller.abort();
    const request = { session, kind: "load", controller: new AbortController() };
    syncRequestRef.current = request;
    setSyncPending(true);
    setSyncSafeError(null);
    setSyncFailurePhase(null);
    try {
      if (typeof session.adapter?.listSyncOperations !== "function") throw new Error("SYNC_SETTINGS_UNAVAILABLE");
      const result = await session.adapter.listSyncOperations({ signal: request.controller.signal });
      if (!ownsSyncRequest(request)) return;
      setSyncOperations(result);
      setSyncSelections(Object.fromEntries(result.map((operation) => [
        operation.operation_id, operation.state === "awaiting_approval" ? [...operation.item_ids] : [...operation.approved_item_ids],
      ])));
    } catch (error) {
      if (!ownsSyncRequest(request)) return;
      setSyncOperations([]);
      setSyncSafeError(safeErrorCode(error, "SYNC_SETTINGS_UNAVAILABLE"));
      setSyncFailurePhase("load");
    } finally {
      if (ownsSyncRequest(request)) {
        syncRequestRef.current = null;
        setSyncPending(false);
      }
    }
  };
  const approveSyncOperation = async (operation) => {
    const session = syncSessionRef.current;
    const approvedItemIds = [...(syncSelections[operation.operation_id] ?? [])];
    if (!ownsSyncSession(session) || syncRequestRef.current || operation.state !== "awaiting_approval"
        || !syncOperations.includes(operation) || !approvedItemIds.length
        || typeof session.adapter?.approveSyncOperation !== "function" || typeof session.adapter?.issueStudioStepUp !== "function") return;
    const passwordInput = syncPasswordRef.current;
    const password = passwordInput?.value ?? "";
    const request = { session, kind: "approve", controller: new AbortController(), passwordInput };
    syncRequestRef.current = request;
    if (passwordInput) passwordInput.value = "";
    let phase = "authorization";
    setSyncPending(true);
    setSyncSafeError(null);
    setSyncFailurePhase(null);
    try {
      const idempotencyKey = secureUuid();
      const stepUpAuthorizationId = await session.adapter.issueStudioStepUp(
        "data_area_move", operation.operation_id, password,
        { idempotencyKey, signal: request.controller.signal },
      );
      if (!ownsSyncRequest(request)) return;
      phase = "approval";
      const saved = await session.adapter.approveSyncOperation(
        operation,
        { approvedItemIds, stepUpAuthorizationId },
        { idempotencyKey, signal: request.controller.signal },
      );
      if (!ownsSyncRequest(request)) return;
      setSyncOperations((current) => current.map((item) => item.operation_id === saved.operation_id ? saved : item));
    } catch (error) {
      if (!ownsSyncRequest(request)) return;
      setSyncSafeError(safeErrorCode(error, "SYNC_SETTINGS_APPROVAL_FAILED"));
      setSyncFailurePhase(phase);
    } finally {
      if (ownsSyncRequest(request)) {
        if (passwordInput) passwordInput.value = "";
        syncRequestRef.current = null;
        setSyncPending(false);
      }
    }
  };
  const loadEgressPolicy = async () => {
    if (typeof adapter?.getEgressPolicy !== "function") {
      setEgressPolicy(null);
      setEgressPolicySafeError("EGRESS_POLICY_UNAVAILABLE");
      return;
    }
    setEgressPolicyPending(true);
    setEgressPolicySafeError(null);
    try {
      setEgressPolicy(await adapter.getEgressPolicy({ signal: lifetimeControllerRef.current?.signal }));
    } catch (error) {
      setEgressPolicy(null);
      setEgressPolicySafeError(safeErrorCode(error, "EGRESS_POLICY_UNAVAILABLE"));
    } finally {
      setEgressPolicyPending(false);
    }
  };
  const loadLicense = async () => {
    if (typeof adapter?.getLicense !== "function") {
      setLicenseView(null);
      setLicenseSafeError("LICENSE_UNAVAILABLE");
      return;
    }
    setLicensePending(true);
    setLicenseSafeError(null);
    try {
      setLicenseView(await adapter.getLicense({ signal: lifetimeControllerRef.current?.signal }));
    } catch (error) {
      setLicenseView(null);
      setLicenseSafeError(safeErrorCode(error, "LICENSE_UNAVAILABLE"));
    } finally {
      setLicensePending(false);
    }
  };
  const applyLicense = async () => {
    const file = licenseFileRef.current?.files?.[0];
    const password = licensePasswordRef.current?.value ?? "";
    if (licensePending || typeof adapter?.applyLicense !== "function" || !file || file.size > 65_536) {
      setLicenseSafeError("LICENSE_DOCUMENT_INVALID");
      return;
    }
    setLicensePending(true);
    setLicenseSafeError(null);
    try {
      const document = JSON.parse(await file.text());
      const saved = await adapter.applyLicense(document, password, {
        idempotencyKey: secureUuid(), signal: lifetimeControllerRef.current?.signal,
      });
      setLicenseView(saved);
    } catch (error) {
      setLicenseSafeError(safeErrorCode(error, "LICENSE_APPLY_FAILED"));
    } finally {
      if (licenseFileRef.current) licenseFileRef.current.value = "";
      if (licensePasswordRef.current) licensePasswordRef.current.value = "";
      setLicensePending(false);
    }
  };
  const loadManualHub = async () => {
    if (typeof adapter?.getManualManifest !== "function") {
      setManualManifest(null);
      setManualSafeError("MANUAL_HUB_UNAVAILABLE");
      return;
    }
    setManualPending(true);
    setManualSafeError(null);
    try {
      const result = await adapter.getManualManifest({ signal: lifetimeControllerRef.current?.signal });
      setManualManifest(result);
      setManualDocument(null);
      setManualSearch("");
    } catch (error) {
      setManualManifest(null);
      setManualSafeError(safeErrorCode(error, "MANUAL_HUB_UNAVAILABLE"));
    } finally {
      setManualPending(false);
    }
  };
  const readManual = async (documentId) => {
    if (manualPending || typeof adapter?.readManual !== "function") return;
    setManualPending(true);
    setManualSafeError(null);
    try {
      setManualDocument(await adapter.readManual(documentId, manualManifest, { signal: lifetimeControllerRef.current?.signal }));
    } catch (error) {
      setManualSafeError(safeErrorCode(error, "MANUAL_CONTENT_UNAVAILABLE"));
    } finally {
      setManualPending(false);
    }
  };
  const downloadManual = async (documentId, format) => {
    if (manualPending || typeof adapter?.downloadManual !== "function") return;
    setManualPending(true);
    setManualSafeError(null);
    try {
      const result = await adapter.downloadManual(documentId, format, manualManifest, { signal: lifetimeControllerRef.current?.signal });
      const url = globalThis.URL?.createObjectURL?.(result.blob);
      if (!url) throw new Error("MANUAL_DOWNLOAD_UNAVAILABLE");
      const anchor = document.createElement("a");
      anchor.href = url;
      anchor.download = result.filename;
      anchor.click();
      globalThis.URL.revokeObjectURL(url);
    } catch (error) {
      setManualSafeError(safeErrorCode(error, "MANUAL_DOWNLOAD_UNAVAILABLE"));
    } finally {
      setManualPending(false);
    }
  };
  const openModal = (view, opener) => {
    invalidateOutputSession();
    invalidateSyncSession();
    if (view === "sync") {
      syncSessionRef.current = { adapter, workspaceId };
      setSyncOperations([]); setSyncSelections({}); setSyncPending(false);
      setSyncSafeError(null); setSyncFailurePhase(null);
    }
    if (view === "output-version") {
      outputSessionRef.current = { adapter, workspaceId };
      setOutputSettings(null); setOutputDraft(null); setOutputSettingsDirty(false);
      setOutputCloseGuard(false); setOutputSettingsSafeError(null); setOutputSettingsPending(false);
    }
    modalOpenerRef.current = opener ?? document.activeElement;
    setSettingsMenuOpen(false);
    setModalView(view);
    if (view === "operations") void loadOperationsStatus();
    if (view === "output-version") void loadOutputVersionSettings();
    if (view === "sync") void loadSyncOperations();
    if (view === "organization-policy") void loadEgressPolicy();
    if (view === "license") void loadLicense();
    if (view === "manual") void loadManualHub();
  };
  const closeModal = (force = false) => {
    if(modalView==="llm" && providerSettingsSaving)return;
    if(modalView==="llm" && providerSettingsDirty && !force){setProviderCloseGuard(true);return;}
    setProviderCloseGuard(false);
    if (modalView === "output-version" && outputRequestRef.current?.kind === "save") return;
    if (modalView === "question-step-up") cancelQuestionRequest();
    if (!force && modalView === "output-version" && outputSettingsDirty) {
      setOutputCloseGuard(true);
      return;
    }
    invalidateOutputSession();
    invalidateSyncSession();
    setSyncPending(false);
    setOutputSettingsPending(false);
    setModalView(null);
    setOutputCloseGuard(false);
    queueMicrotask(() => modalOpenerRef.current?.focus?.());
  };
  const sourceCompactState = viewState.status === "ready" ? "Source 준비"
    : viewState.status === "loading" ? "Source 확인 중"
      : viewState.status === "error" ? "Source 확인 필요"
        : viewState.status === "empty" ? "Source 없음" : "Source 연결 필요";
  const cloudCompactState = operationsPending ? "Cloud 확인 중"
    : operationsSafeError ? "Cloud 확인 필요"
      : operationsStatus?.overall_status === "ready" ? "Cloud 정상"
        : operationsStatus?.overall_status === "warning" ? "Cloud 확인 필요"
          : operationsStatus?.overall_status === "error" ? "Cloud 주의" : "Cloud 미확인";

  return (
    <main className="adaptive-workspace" data-product-workspace-state={viewState.status} data-workspace-id={workspaceId ?? ""}>
      <div className="workspace-surface" inert={modalView ? true : undefined} aria-hidden={modalView ? "true" : undefined}>
      <header className="workspace-header workspace-app-bar">
        <div className="workspace-brand"><span className="daon-mark" aria-hidden="true">D</span><div><p className="eyebrow">DAON WORKSPACE · NOTEBOOK</p><h1>{notebookTitle || "Workspace"}</h1></div></div>
        <div className="workspace-app-actions">
          <span className={`workspace-status status-${viewState.status}`} role="status" data-cloud-status={operationsStatus?.overall_status ?? "unknown"}><span className="status-dot" aria-hidden="true" />{sourceCompactState}<span className="status-connection"> · {cloudCompactState}</span></span>
          <button type="button" onClick={(event) => openModal("operations", event.currentTarget)}>운영상태</button>
          <div className="settings-menu-anchor">
            <button ref={settingsButtonRef} type="button" aria-haspopup="menu" aria-expanded={settingsMenuOpen} onClick={() => setSettingsMenuOpen((open) => !open)}>설정</button>
            {settingsMenuOpen ? <div className="settings-menu" role="menu" aria-label="Workspace 설정">
              <button type="button" role="menuitem" onClick={() => openModal("llm", settingsButtonRef.current)}><span className="menu-icon icon-model" aria-hidden="true" /><span><strong>LLM 설정</strong><small>Provider와 모델 연결</small></span></button>
              <button type="button" role="menuitem" onClick={() => openModal("output-version", settingsButtonRef.current)}><span className="menu-icon icon-output" aria-hidden="true" /><span><strong>출력·버전</strong><small>기본 형식과 저장 정책</small></span></button>
              <button type="button" role="menuitem" onClick={() => openModal("sync", settingsButtonRef.current)}><span className="menu-icon icon-sync" aria-hidden="true" /><span><strong>동기화·승인</strong><small>Preview 확인과 명시 승인</small></span></button>
              <button type="button" role="menuitem" onClick={() => openModal("approval-policy", settingsButtonRef.current)}><span className="menu-icon icon-policy" aria-hidden="true" /><span><strong>승인 정책</strong><small>조직 승인 기한</small></span></button>
              <button type="button" role="menuitem" onClick={() => openModal("organization-policy", settingsButtonRef.current)}><span className="menu-icon icon-policy" aria-hidden="true" /><span><strong>조직 정책</strong><small>읽기 전용</small></span></button>
              <button type="button" role="menuitem" onClick={() => openModal("license", settingsButtonRef.current)}><span className="menu-icon icon-license" aria-hidden="true" /><span><strong>라이선스</strong><small>Edition과 사용 한도</small></span></button>
              <button type="button" role="menuitem" onClick={() => openModal("manual", settingsButtonRef.current)}><span className="menu-icon icon-manual" aria-hidden="true" /><span><strong>사용자 설명서</strong><small>가이드 검색·읽기·다운로드</small></span></button>
              <button type="button" role="menuitem" onClick={() => { setSettingsMenuOpen(false); onLogout?.(); }}><span className="menu-icon" aria-hidden="true" /><span><strong>로그아웃</strong><small>현재 세션만 종료</small></span></button>
            </div> : null}
          </div>
        </div>
      </header>
      <div className="workspace-panes" data-layout="source-conversation-studio" aria-label="Workspace 3면">
        <SafePane id="product-pane-sources" title="Source·지식·권위" description="NotebookLM처럼 파일·웹·Drive·복사 텍스트를 Source로 추가합니다.">
          <button className="source-add-button" type="button" onClick={() => { setSourceAddOpen(true); setSourceAddError(null); }} disabled={!adapter}><span aria-hidden="true">＋</span>Source 추가</button>
          <input className="source-legacy-input" type="file" accept="application/pdf" onChange={uploadSource} aria-hidden="true" tabIndex="-1" />
          {sourceAddOpen ? <section className="source-action-dialog source-add-dialog" role="dialog" aria-modal="true" aria-label="Source 추가">
            <header><strong>Source 추가</strong><button type="button" onClick={() => { resetDriveSource(); setSourceAddOpen(false); }} disabled={sourceAddPending} aria-label="Source 추가 닫기">×</button></header>
            <nav className="source-add-tabs" aria-label="Source 유형">
              {[['file', '파일 업로드'], ['website', '웹사이트'], ['drive', 'Drive'], ['text', '복사한 텍스트'], ['mcp', 'MCP 연결']].map(([mode, label]) => <button key={mode} type="button" aria-pressed={sourceAddMode === mode} onClick={() => { resetDriveSource(); setSourceAddMode(mode); setSourceAddError(null); }} disabled={sourceAddPending}>{label}</button>)}
            </nav>
            {sourceAddMode === "file" ? <label className="source-add-dropzone"><strong>파일을 선택하세요</strong><small>질문·Studio 처리 지원: PDF(문서 모델 필요), TXT/MD/CSV, XLSX(내부 모델·셀 검증 필요), DOCX(제한형 문단·병합 없는 단순 표, 내부 모델·구간 검증 필요), PNG/JPG/JPEG(내부 Vision 모델·OCR 필요). PPTX(제한형 텍스트·병합 없는 단순 표, 내부 모델·원문 검증 필요). WAV(PCM16·1~120초·25MiB, QA 내부 ASR와 의미 모델 필요). MP3/M4A는 미지원</small><input type="file" accept=".pdf,.xlsx,.docx,.pptx,.csv,.txt,.md,.png,.jpg,.jpeg,.wav" onChange={uploadSource} disabled={sourceAddPending} /></label> : null}
            {sourceAddMode === "text" ? <><textarea value={sourceAddText} onChange={(event) => setSourceAddText(event.target.value)} placeholder="복사한 텍스트를 붙여 넣으세요" disabled={sourceAddPending} /><button type="button" className="primary-button" onClick={() => void submitPastedText()} disabled={sourceAddPending || !sourceAddText.trim()}>{sourceAddPending ? "등록 중" : "텍스트 등록"}</button></> : null}
            {sourceAddMode === "mcp" ? (connectors.some(item=>item.connector_id==='mcp-open-law-go-kr')
              ? <OpenLawSourceForm adapter={adapter} connector={connectors.find(item=>item.connector_id==='mcp-open-law-go-kr')} onConnected={()=>void refreshLawConnector()} onImported={()=>{setSourceAddOpen(false);setLoadRevision(current=>current+1);}} />
              : <div className="source-add-unavailable"><strong>국가법령정보센터 MCP</strong><small>명시 등록 후 공개 법령 검색으로 연결을 확인합니다.</small><button type="button" className="primary-button" onClick={() => void registerMcpConnector()} disabled={sourceAddPending}>{sourceAddPending ? "등록 중" : "MCP 연결 등록"}</button></div>) : null}
            {sourceAddMode === "website" ? <WebsiteSourceForm key={workspaceId} adapter={adapter} onRegistered={()=>{setSourceAddOpen(false);setLoadRevision(current=>current+1);}} /> : null}
            {sourceAddMode === "drive" ? <div>
              <label className="source-add-dropzone"><strong>로컬·Google Drive 파일 선택</strong><small>PC에 연결된 Google Drive 또는 로컬 위치에서 파일 사본을 선택하세요. Google 웹 연결·자동 동기화는 지원하지 않습니다.</small>
                <input ref={driveInputRef} type="file" aria-label="로컬·Google Drive 파일 선택" accept=".pdf,.xlsx,.docx,.pptx,.csv,.txt,.md,.png,.jpg,.jpeg,.wav" disabled={sourceAddPending}
                  onClick={resetDriveSource} onCancel={resetDriveSource}
                  onChange={(event) => { setDriveFile(event.currentTarget.files?.[0] ?? null); setSourceAddError(null); }} />
              </label>
              {driveFile ? <p role="status">{driveFile.name.replace(/.*[/\\]/u, "")} · {driveFile.size} bytes · {driveFileError(driveFile) ?? "지원 형식 · 가져오기 전"}</p> : null}
              <button type="button" className="primary-button" onClick={() => void submitDriveSource()} disabled={sourceAddPending || !driveFile || Boolean(driveFileError(driveFile))}>Drive 파일 가져오기</button>
            </div> : null}
            {sourceAddPending ? <p className="source-inline-state" role="status">Source 등록·처리 중입니다.</p> : null}
            {sourceAddError ? <div className="inline-alert compact" role="alert">Source를 등록하지 못했습니다. ({sourceAddError})</div> : null}
          </section> : null}
          {processing ? <p className="source-inline-state" role="status">처리 중 · 잠시만 기다려 주세요.</p> : null}
          <section className="source-group" aria-labelledby="approved-knowledge-title">
            <div className="source-group-heading"><h3 id="approved-knowledge-title">Daon 승인 지식</h3><span>{knowledgePackages.length}</span></div>
            <ul className="source-list knowledge-list" aria-label="Daon 승인 지식 목록">
              {knowledgePackages.map((item) => (
                <li className="source-list-row knowledge-list-row" key={item.packageId}>
                  <button type="button" aria-pressed={selectedKnowledgeId === item.packageId} onClick={() => { cancelQuestionRequest(); reportIdempotencyRef.current = null; setViewState((current) => ({ ...current, answer: null, answerIntent: null })); setSelectedKnowledgeId((current) => current === item.packageId ? null : item.packageId); }}>
                    <span className="source-file-icon knowledge-icon" aria-hidden="true">K</span><span className="source-row-copy"><strong>{item.producerLabel} · {item.producerVersion}</strong><small>{item.authorityLabel} · {item.registrationLabel}</small></span><span className={`source-ready-dot ${selectedKnowledgeId === item.packageId ? "is-ready" : ""}`} aria-hidden="true" />
                  </button>
                </li>
              ))}
            </ul>
            {knowledgeSafeError ? <div className="inline-alert compact" role="alert">승인 지식을 불러오지 못했습니다.</div> : null}
          </section>
          <section className="source-group" aria-labelledby="connector-source-title">
            <div className="source-group-heading"><h3 id="connector-source-title">연결형 Source</h3><span>{connectors.length}</span></div>
            <ul className="source-list connector-list" aria-label="MCP 및 Daon 승인 지식 Connector 목록">
              {connectors.map((connector) => (
                <li className="source-list-row connector-list-row" key={connector.connector_id}>
                  <button type="button" disabled={connector.status !== "connected"} title={connector.status === "connected" ? "연결형 Source 선택" : "사용 불가"} onClick={()=>{if(connector.connector_id==='mcp-open-law-go-kr'){setLawSourcesOnly(true);const source=viewState.sources.find(item=>item.lawSnapshot && item.ready);if(source)selectSource(source);}}}>
                    <span className="source-file-icon knowledge-icon" aria-hidden="true">{connector.kind === "mcp" ? "M" : "K"}</span>
                    <span className="source-row-copy"><strong>{connector.name}</strong><small>{connector.status === "connected" ? `Source ${connector.source_count}개 · ${connector.endpoint_label}` : "사용 불가"}</small></span>
                    <span className={`source-ready-dot ${connector.status === "connected" ? "is-ready" : ""}`} aria-hidden="true" />
                  </button>
                  {connector.status !== "connected" && typeof adapter?.reconnectConnector === "function" ? <button type="button" disabled={connectorPending} className="source-action-trigger" title="Connector 재연결" aria-label={`${connector.name} 재연결`} onClick={() => {if(connector.connector_id==='mcp-open-law-go-kr')void reconnectLawConnector(connector);else void reconnectNonLawConnector(connector);}}>↻</button> : null}
                  {connector.kind === "mcp" && typeof adapter?.unregisterConnector === "function" ? <button type="button" className="source-action-trigger" title="MCP 연결 삭제" aria-label={`${connector.name} 연결 삭제`} onClick={() => void unregisterMcpConnector(connector)}>×</button> : null}
                </li>
              ))}
            </ul>
            {connectorSafeError ? <div className="inline-alert compact" role="alert">연결형 Source를 불러오지 못했습니다.</div> : null}
          </section>
          <section className="source-group" aria-labelledby="raw-source-title">
          <div className="source-group-heading"><h3 id="raw-source-title">Raw Source</h3><span>{viewState.sources.filter(source=>!lawSourcesOnly || source.lawSnapshot).length}</span></div>
          {lawSourcesOnly?<button type="button" onClick={()=>setLawSourcesOnly(false)}>모든 Source 보기</button>:null}
          <ul className="source-list" aria-label="Raw Source 목록">
            {viewState.sources.filter(source=>!lawSourcesOnly || source.lawSnapshot).map((source) => (
              <li className="source-list-row" key={`${source.sourceId}:${source.sourceVersionId}`}>
                <button type="button" aria-pressed={viewState.selectedSource?.sourceVersionId === source.sourceVersionId} onClick={() => selectSource(source)} disabled={!source.ready}>
                  <span className="source-file-icon" aria-hidden="true">{source.lawSnapshot?'LAW':source.websiteSnapshot?'WEB':'FILE'}</span><span className="source-row-copy"><strong>{source.lawSnapshot?.title ?? source.websiteSnapshot?.title ?? source.filename ?? source.sourceId}</strong><small>Version {source.sourceVersionId} · {source.lawCandidate?(source.lawSnapshot?'법령 수집·저장 완료':'법령 사용 불가'):source.websiteSnapshot?`웹 수집·저장 완료 · ${source.websiteSnapshot.fetched_at}`:SOURCE_STATE_LABELS[source.sourceState] ?? "상태 확인 필요"}</small></span><span className={`source-ready-dot ${source.ready ? "is-ready" : ""}`} aria-hidden="true" />
                </button>
                <button type="button" className="source-action-trigger" title="Source 작업" aria-label={`${source.filename ?? source.sourceId} 작업`} onClick={() => { setSourceAction(source); setSourceActionKind("unbind"); setSourceActionSafeError(null); }}>⋮</button>
                {(new Set(["failed", "waiting_model"]).has(source.sourceState)
                  || (typeof source.filename === "string" && /\.(docx|pptx)$/u.test(source.filename.toLowerCase())
                    && source.sourceState === "needs_review" && source.processingState === "completed" && source.jobState === "completed")) && typeof adapter?.reprocessSource === "function"
                  ? <button type="button" disabled={sourceAddPending} onClick={() => {
                    const prior=sourceRetryKeysRef.current.get(`${source.sourceId}:${source.sourceVersionId}`);
                    setSourceRetryTarget(source);setSourceRetryMode(prior?.mode??"");setSourceRetryError(null);setSourceRetrySaved(null);
                  }}>재처리</button> : null}
              </li>
            ))}
          </ul>
          {sourceRetryTarget?<section className="source-action-dialog" role="dialog" aria-modal="true" aria-label="Source 재처리 설정">
            <strong>{sourceRetryTarget.filename??sourceRetryTarget.sourceId}</strong>
            <label>처리 방식 <select aria-label="Source 재처리 Mode" value={sourceRetryMode}
              disabled={sourceAddPending||sourceRetryKeysRef.current.has(`${sourceRetryTarget.sourceId}:${sourceRetryTarget.sourceVersionId}`)}
              onChange={event=>setSourceRetryMode(event.target.value)}>
              <option value="">기존 방식 유지 (자동 정책 미지정)</option>
              <option value="auto">Auto · 조건 충족 시 재큐 허용</option>
              <option value="local_only">Local only · 로컬 조건만 재큐 허용</option>
              <option value="pinned">Pinned · 자동 재큐 금지</option>
            </select></label>
            <span title="선택한 Mode는 새 Run에 저장합니다. 자동 재큐는 서버의 승인 범위·현재 권한·모델·비용 조건을 모두 만족해야 하며 이 선택만으로 활성화되거나 성공하지 않습니다.">ⓘ</span>
            {sourceRetryKeysRef.current.has(`${sourceRetryTarget.sourceId}:${sourceRetryTarget.sourceVersionId}`)
              ?<p role="status">이전 요청의 저장 여부가 불확정합니다. 같은 설정·요청 키를 유지하며 자동 재전송하지 않습니다.</p>:null}
            <button type="button" disabled={sourceAddPending} onClick={()=>setSourceRetryTarget(null)}>취소</button>
            <button type="button" disabled={sourceAddPending} onClick={()=>void retrySource(sourceRetryTarget,sourceRetryMode)}>설정 확인·재처리</button>
          </section>:null}
          {sourceRetrySaved?<p role="status">{sourceRetrySaved}</p>:null}
          {viewState.selectedSource?.websiteSnapshot ? <WebsiteOriginal key={`${workspaceId}:${viewState.selectedSource.sourceVersionId}`} adapter={adapter} source={viewState.selectedSource} /> : null}
          {viewState.selectedSource?.lawSnapshot ? <OpenLawOriginal key={`${workspaceId}:${viewState.selectedSource.sourceVersionId}`} adapter={adapter} source={viewState.selectedSource} /> : null}
          {sourceRetryError ? <div className="inline-alert compact" role="alert">재처리를 요청하지 못했습니다. 연결과 처리 상태를 확인한 뒤 다시 시도하세요. ({sourceRetryError})</div> : null}
          <ul className="source-list" aria-label="삭제 요청 Source 목록">
            {sourceDeletionRequests.map((request) => (
              <li className="source-list-row" key={request.request_id}>
                <button type="button" disabled>
                  <span className="source-file-icon" aria-hidden="true">PDF</span><span className="source-row-copy"><strong>{request.source_id}</strong><small>삭제 요청 · {request.state} · {request.grace_until}</small></span>
                </button>
                <button type="button" className="source-action-trigger" title="삭제 요청 상태" aria-label={`${request.source_id} 삭제 요청 상태`} onClick={() => {
                  setSourceAction({ sourceId: request.source_id, filename: request.source_id, deletionRequest: {
                    data: { request_id: request.request_id, source_id: request.source_id, state: request.state },
                    etag: `"deletion:${request.request_id}:${request.version}"`,
                  } });
                  setSourceActionSafeError(null);
                }}>⋮</button>
              </li>
            ))}
          </ul>
          </section>
          {viewState.status === "loading" ? <div className="pane-empty" role="status"><span className="state-spinner" aria-hidden="true" /><strong>Source를 불러오고 있습니다.</strong><small>현재 Workspace의 안전한 목록을 확인하는 중입니다.</small></div> : null}
          {viewState.status === "empty" ? <div className="pane-empty"><span aria-hidden="true">◇</span><strong>Source를 추가해 주세요.</strong><small>파일·웹사이트·Drive·복사한 텍스트를 등록하면 질문과 Studio 생성에 사용할 수 있습니다.</small></div> : null}
          {viewState.status === "error" ? <div className="inline-alert" role="alert"><span>Source를 불러오지 못했습니다. 운영상태에서 연결을 확인해 주세요.</span><button type="button" onClick={() => { setViewState((current) => ({ ...current, status: "loading", safeError: null })); setLoadRevision((current) => current + 1); }}>다시 시도</button></div> : null}
          {sourceAction ? <section className="source-action-dialog" role="dialog" aria-modal="true" aria-label="Source 작업 확인">
            <strong>{sourceAction.filename ?? sourceAction.sourceId}</strong>
            {sourceAction.deletionRequest ? <p role="status">{sourceAction.deletionRequest.data.state === "purged"
              ? "Source 원문과 파생 자료의 실제 정리가 완료되었습니다."
              : sourceAction.deletionRequest.data.state === "cancelled"
                ? "삭제 요청이 취소되었습니다. 원본 Source는 보존됩니다."
                : "Source 사용은 차단되었으며 정리가 완료되지 않았습니다. 추가 인증 후 같은 요청을 재시도할 수 있습니다."}</p>
              : sourceActionKind === "unbind" ? <p>Notebook에서 제거하면 원본 Source는 보존되고 이 Notebook의 질문과 Studio에서만 제외됩니다.</p>
                : <p>Source 삭제는 원본과 이 Notebook의 연결을 즉시 제거합니다. 삭제 후 복구할 수 없습니다.</p>}
            {sourceActionSafeError ? <div role="alert">{sourceActionSafeError === "SOURCE_SHARED_BINDING"
              ? "다른 Notebook에서도 사용하는 자료입니다. Notebook에서 제거를 사용하세요."
              : sourceActionSafeError === "SOURCE_RETAINED_REFERENCE"
                ? "보존 중인 근거·동기화 참조가 있어 삭제할 수 없습니다."
                : "요청을 처리하지 못했습니다. 다시 확인해 주세요."}</div> : null}
            {(sourceActionKind === "deletion" || ["cleanup_pending", "failed"].includes(sourceAction.deletionRequest?.data.state))
              && !["purged", "cancelled"].includes(sourceAction.deletionRequest?.data.state)
              ? <label>추가 인증 비밀번호<input type="password" autoComplete="off" aria-label="Source 삭제 추가 인증 비밀번호" ref={sourceDeletionPasswordRef} disabled={sourceActionPending} /></label> : null}
            <div>{sourceAction.deletionRequest?.data.state !== "cancelled" && sourceAction.deletionRequest?.data.state !== "purged"
              ? sourceAction.deletionRequest
                ? ["cleanup_pending", "failed"].includes(sourceAction.deletionRequest.data.state)
                  ? <button type="button" disabled={sourceActionPending} onClick={() => void confirmSourceDeletionRequest()}>삭제 정리 재시도</button>
                  : <button type="button" disabled={sourceActionPending} onClick={() => void confirmSourceDeletionCancel()}>삭제 요청 취소</button>
                : sourceActionKind === "unbind"
              ? <><button type="button" disabled={sourceActionPending} onClick={() => void confirmSourceUnbind()}>Notebook에서 제거</button><button type="button" className="danger" disabled={sourceActionPending} onClick={() => setSourceActionKind("deletion")}>Source 삭제 요청</button></>
              : <button type="button" className="danger" disabled={sourceActionPending} onClick={() => void confirmSourceDeletionRequest()}>삭제 요청 확인</button>
              : null}
              <button type="button" disabled={sourceActionPending} onClick={() => {
                if (sourceDeletionPasswordRef.current) sourceDeletionPasswordRef.current.value = "";
                setSourceAction(null);
              }}>{sourceAction.deletionRequest ? "닫기" : "취소"}</button></div>
          </section> : null}
        </SafePane>
        <SafePane id="product-pane-conversation" title="대화·실행" description="ready Source 선택 후 실제 질문과 Citation을 사용합니다.">
          {desktopEditor ? desktopEditor : <div className="conversation-workspace"><div className="conversation-transcript" role="log" aria-label="질문과 답변 이력" aria-live="polite">
          <div className="context-selection-status" role="status">질문 컨텍스트 · {selectedKnowledgeId ? "Daon 승인 지식" : ""}{selectedKnowledgeId && viewState.selectedSource ? " + " : ""}{viewState.selectedSource ? "Raw Source" : !selectedKnowledgeId ? "일반 상담" : ""}</div>
          {questionPending ? <div className="context-selection-status" role="status">답변을 기다리고 있습니다.</div> : null}
          {typeof adapter?.listConversationTurns!=="function"?<p className="context-selection-status">이 환경에서는 저장된 전체 대화 조회를 지원하지 않습니다.</p>:null}
          {conversationHistoryPending ? <p role="status">저장된 대화를 확인하고 있습니다.</p> : null}
          {conversationHistoryError ? <p role="alert">대화 이력을 확인할 수 없습니다. ({conversationHistoryError})</p> : null}
          {typeof adapter?.listConversationTurns==="function" ? <div className="conversation-history-actions"><button type="button" disabled={conversationHistoryPending} onClick={()=>void readConversationTurns()}>대화 새로고침</button>{conversationCursor ? <button type="button" disabled={conversationHistoryPending} onClick={()=>void readConversationTurns({before:conversationCursor,append:true})}>이전 대화 더 보기</button>:null}</div>:null}
          {visibleConversationTurns.map((turn,index)=><section key={turn.turn_id} data-conversation-turn={turn.turn_id} className="conversation-turn">
            <article className="user-message"><span className="message-author">나</span><p>{turn.question}</p></article>
            {turn.response?<article className={turn.response.kind==="answer"?"assistant-message":"system-message"}><span className="message-author">{turn.response.kind==="answer"?"Daon":"안내"}</span><p>{turn.response.text}</p>
              {turn.response.next_actions.length?<ul>{turn.response.next_actions.map((action,i)=><li key={i}>{action}</li>)}</ul>:null}
              {!(index===visibleConversationTurns.length-1&&viewState.answer)&&turn.response.citations.length?<div className="citation-row">{turn.response.citations.map(citation=>{const href=historyCitationHref(citation);return href?<a key={citation.citation_id} href={href}>Citation · {citation.page}쪽</a>:<span key={citation.citation_id}>인용 원문 접근 확인 필요</span>;})}</div>:null}
            </article>:<p role="status">{turn.state==="pending"?"질문이 접수되었습니다. 처리 결과를 확인 중입니다.":"처리 결과를 확인해 주세요."}</p>}
            <small>{turn.turn_id.startsWith("pending-")?"서버 저장 확인 전":new Date(turn.submitted_at).toLocaleString("ko-KR")}{turn.model?` · ${turn.model.provider_code} / ${turn.model.model_id}`:""}</small>
          </section>)}
          {visibleConversationTurns.length || conversationHistoryError ? null : viewState.answer ? <article className="assistant-message"><div className="assistant-avatar" aria-hidden="true">D</div><div><span className="message-author">Daon</span><p>{viewState.answer.answer}</p></div></article> : <div className="conversation-empty"><span className="conversation-orbit" aria-hidden="true">✦</span><h3>{viewState.selectedSource || selectedKnowledgeId ? "Source에 대해 무엇이든 물어보세요." : "무엇이든 물어보세요."}</h3><p>{viewState.selectedSource || selectedKnowledgeId ? "선택한 Source의 근거와 Citation을 사용해 답합니다." : "작업 상담, 구현 방법 등 일반 질문에 답합니다."}</p></div>}
          {viewState.answerIntent === "general_ungrounded" || viewState.answerIntent === "work_support_ungrounded"
            ? <div className="context-selection-status" role="status">작업 상담 · 근거 미사용</div> : null}
          {viewState.answerIntent === "work_support_source_backed"
            ? <div className="context-selection-status" role="status">작업 상담 · Source 사용</div> : null}
          {viewState.answer?.grounding_status==="partial"?<div className="context-selection-status" role="status"><strong>부분 근거</strong><ul>{viewState.answer.next_actions.map((action,index)=><li key={index}>{action}</li>)}</ul></div>:null}
          {viewState.answer?.insufficient ? <div className="context-selection-status" role="status"><strong>근거 부족</strong>{viewState.answer.next_actions?.length ? <ul>{viewState.answer.next_actions.map((action, index) => <li key={index}>{action}</li>)}</ul> : null}</div> : null}
          {viewState.conversationSafeError ? <div className="inline-alert compact" role="alert">{questionFailureMessage(viewState.conversationSafeError)}</div> : null}
          <div className="citation-row">{viewState.answer?.citations?.map((citation) => (
            <a key={citation.citation_id} href={citation.content_url} onClick={(event) => {
              if(citation.origin==='raw_source' && citation.locator.kind==='section' && /^law-source-[0-9a-f]{32}$/u.test(citation.source_id)){
                event.preventDefault();lawCitationRequest.current?.abort();const controller=new AbortController();lawCitationRequest.current=controller;
                const epoch=questionEpochRef.current;setLawCitation({citation,epoch,content:null,error:null});
                const current=()=>lawCitationRequest.current===controller && !controller.signal.aborted && epoch===questionEpochRef.current && workspaceMountedRef.current;
                void(async()=>{try{
                  if(typeof adapter?.getOpenLawCitation!=='function' || !viewState.sources.some(source=>source.ready && source.lawSnapshot && source.sourceId===citation.source_id && source.sourceVersionId===citation.source_version_id))throw Error('CITATION_CONTENT_UNAVAILABLE');
                  const content=await adapter.getOpenLawCitation(citation,{signal:controller.signal});
                  if(!hasExactKeys(content,['source_version_id','text','start','end']) || content.source_version_id!==citation.source_version_id
                    || typeof content.text!=='string' || new TextEncoder().encode(content.text).length>524288
                    || !Number.isSafeInteger(content.start) || !Number.isSafeInteger(content.end) || content.start<0 || content.end<=content.start
                    || content.end-content.start>1024 || content.end>Array.from(content.text).length)throw Error('CITATION_CONTENT_UNAVAILABLE');
                  if(current())setLawCitation({citation,epoch,content,error:null});
                }catch(error){if(current())setLawCitation({citation,epoch,content:null,error:safeErrorCode(error,'CITATION_CONTENT_UNAVAILABLE')});}})();
                return;
              }
              if (["docx_block", "pptx_block", "audio_segment"].includes(citation.locator.kind)) {
                event.preventDefault();
                sheetRequestRef.current=null;setSheetCitation(null);setImageCitation(null);
                const requestId=Symbol("docx-citation"), requestEpoch=questionEpochRef.current;docxRequestRef.current=requestId;
                setDocxCitation({citation,requestId,requestEpoch,content:null,error:null});
                void(async()=>{
                  try {
                    const expected=`/bff/api/workspaces/${encodeURIComponent(workspaceId)}/citations/${encodeURIComponent(citation.citation_id)}/content?notebook_id=`;
                    const parse= citation.locator.kind === "audio_segment" ? parseAudioSegment : citation.locator.kind === "pptx_block" ? parsePptxBlock : parseDocxBlock;
                    if (!parse(citation.locator.value) || typeof citation.content_url !== "string"
                        || !citation.content_url.startsWith(expected) || !isSafeDtoId(citation.content_url.slice(expected.length))
                        || citation.content_url !== adapter.citationUrl(citation)) throw new Error("CITATION_CONTENT_UNAVAILABLE");
                    const response=await globalThis.fetch(citation.content_url,{credentials:"same-origin",cache:"no-store",signal:lifetimeControllerRef.current?.signal});
                    if (!response.ok || response.headers.get("content-type")?.split(";",1)[0].trim() !== "application/json") throw new Error("CITATION_CONTENT_UNAVAILABLE");
                    const text=await response.text();
                    if(new TextEncoder().encode(text).length>32768)throw new Error("CITATION_CONTENT_UNAVAILABLE");
                    const validate=citation.locator.kind === "audio_segment" ? isAudioSegmentContent : citation.locator.kind === "pptx_block" ? isPptxBlockContent : isDocxBlockContent;
                    if(!validate(text,citation.locator.value,citation.source_version_id))throw new Error("CITATION_CONTENT_UNAVAILABLE");
                    const content=JSON.parse(text);
                    if(docxRequestRef.current===requestId&&questionEpochRef.current===requestEpoch&&workspaceMountedRef.current)setDocxCitation(current=>current?.requestId===requestId?{...current,content}:current);
                  }catch{
                    if(docxRequestRef.current===requestId&&questionEpochRef.current===requestEpoch&&workspaceMountedRef.current)setDocxCitation(current=>current?.requestId===requestId?{...current,error:"CITATION_CONTENT_UNAVAILABLE"}:current);
                  }
                })();
                return;
              }
              docxRequestRef.current=null;setDocxCitation(null);
              if (citation.locator.kind === "sheet_cell") {
                event.preventDefault();
                setImageCitation(null);
                const requestId = Symbol("sheet-citation");
                sheetRequestRef.current = requestId;
                setSheetCitation({ citation, requestId, content: null, error: null });
                void (async () => {
                  try {
                    const expected = `/bff/api/workspaces/${encodeURIComponent(workspaceId)}/citations/${encodeURIComponent(citation.citation_id)}/content?notebook_id=`;
                    if (!parseSheetCell(citation.locator.value) || typeof citation.content_url !== "string"
                        || !citation.content_url.startsWith(expected)
                        || !isSafeDtoId(citation.content_url.slice(expected.length))
                        || citation.content_url !== adapter.citationUrl(citation)) throw new Error("CITATION_CONTENT_UNAVAILABLE");
                    const response = await globalThis.fetch(citation.content_url, { credentials: "same-origin", cache: "no-store", signal: lifetimeControllerRef.current?.signal });
                    if (!response.ok || response.headers.get("content-type")?.split(";", 1)[0].trim() !== "application/json") throw new Error("CITATION_CONTENT_UNAVAILABLE");
                    const text = await response.text();
                    if (new TextEncoder().encode(text).length > 262144) throw new Error("CITATION_CONTENT_UNAVAILABLE");
                    const content = JSON.parse(text);
                    if (!isSheetCellContent(content, citation.locator.value, citation.source_version_id)) throw new Error("CITATION_CONTENT_UNAVAILABLE");
                    if (sheetRequestRef.current === requestId && workspaceMountedRef.current) setSheetCitation(current => current?.requestId === requestId ? { ...current, content } : current);
                  } catch {
                    if (sheetRequestRef.current === requestId && workspaceMountedRef.current) setSheetCitation(current => current?.requestId === requestId ? { ...current, error: "CITATION_CONTENT_UNAVAILABLE" } : current);
                  }
                })();
                return;
              }
              if (citation.locator.kind === "image_region") {
                event.preventDefault();
                sheetRequestRef.current = null;
                setSheetCitation(null);
                setImageCitationLoaded(false);
                setImageCitationError(null);
                setImageCitation({ ...citation, region: parseImageRegion(citation.locator.value) });
                return;
              }
              void openNativeCitation(event, {
                adapter,
                citation,
                signal: lifetimeControllerRef.current?.signal,
                registerObjectUrl: (url) => citationUrlsRef.current.add(url),
                unregisterObjectUrl: (url) => citationUrlsRef.current.delete(url)
              }).catch((error) => {
                setViewState((current) => ({ ...current, status: "error", safeError: safeErrorCode(error, "CITATION_RESPONSE_INVALID") }));
              });
            }}>Citation · {citation.locator.kind === "audio_segment" ? `음성 ${audioTime(parseAudioSegment(citation.locator.value).start_ms)}–${audioTime(parseAudioSegment(citation.locator.value).end_ms)}` : citation.locator.kind === "pptx_block" ? pptxBlockLabel(citation.locator.value) : citation.locator.kind === "docx_block" ? `${parseDocxBlock(citation.locator.value)?.block} · ${parseDocxBlock(citation.locator.value)?.start}–${parseDocxBlock(citation.locator.value)?.end}` : citation.locator.kind === "sheet_cell" ? `${parseSheetCell(citation.locator.value)?.sheet} · ${parseSheetCell(citation.locator.value)?.range}` : citation.locator.kind === "page" ? `${citation.page}쪽` : citation.locator.kind === "image_region" ? "이미지 영역" : "지식 구간"}</a>
          ))}</div>
          {lawCitation && lawCitation.epoch===questionEpochRef.current && viewState.answer?.citations?.some(citation=>citation.citation_id===lawCitation.citation.citation_id && citation.source_version_id===lawCitation.citation.source_version_id && citation.evidence_span_id===lawCitation.citation.evidence_span_id)?<section className="website-original" aria-label="법령 인용 원문">
            <button type="button" onClick={()=>{lawCitationRequest.current?.abort();lawCitationRequest.current=null;setLawCitation(null);}}>법령 인용 원문 닫기</button>
            {lawCitation.error?<p role="alert">법령 인용 원문 사용 불가 ({lawCitation.error})</p>:lawCitation.content?<pre>{Array.from(lawCitation.content.text).slice(0,lawCitation.content.start).join('')}<mark>{Array.from(lawCitation.content.text).slice(lawCitation.content.start,lawCitation.content.end).join('')}</mark>{Array.from(lawCitation.content.text).slice(lawCitation.content.end).join('')}</pre>:<p role="status">현재 인용 권한과 법령 원문을 확인 중입니다.</p>}
          </section>:null}
          {docxCitation && docxCitation.requestEpoch===questionEpochRef.current && viewState.answer?.citations?.some(current=>current.citation_id===docxCitation.citation.citation_id&&current.source_id===docxCitation.citation.source_id&&current.source_version_id===docxCitation.citation.source_version_id&&["docx_block","pptx_block","audio_segment"].includes(current.locator.kind)&&current.locator.value===docxCitation.citation.locator.value)?<section className="citation-docx-view" aria-label={docxCitation.citation.locator.kind==="audio_segment"?"음성 전사 원문":docxCitation.citation.locator.kind==="pptx_block"?"PPTX 원문":"DOCX 원문"}>
            <button type="button" onClick={()=>{docxRequestRef.current=null;setDocxCitation(null);}}>원문 닫기</button>
            {docxCitation.error?<p role="alert">{docxCitation.citation.locator.kind==="audio_segment"?"음성":docxCitation.citation.locator.kind==="pptx_block"?"PPTX":"DOCX"} 원문을 확인할 수 없습니다. ({docxCitation.error})</p>:docxCitation.content?(docxCitation.citation.locator.kind==="audio_segment"?<AudioOriginalSegments key={`${docxCitation.citation.citation_id}:${docxCitation.requestEpoch}`} content={docxCitation.content} citation={docxCitation.citation}/>:docxCitation.citation.locator.kind==="pptx_block"?<PptxOriginalBlocks content={docxCitation.content}/>:<DocxOriginalBlocks content={docxCitation.content}/>):<p role="status">{docxCitation.citation.locator.kind==="audio_segment"?"음성":docxCitation.citation.locator.kind==="pptx_block"?"PPTX":"DOCX"} 원문을 불러오는 중입니다.</p>}
          </section>:null}
          {sheetCitation && viewState.answer?.citations?.some(current => current.citation_id === sheetCitation.citation.citation_id && current.source_id === sheetCitation.citation.source_id && current.source_version_id === sheetCitation.citation.source_version_id && current.locator.kind === "sheet_cell" && current.locator.value === sheetCitation.citation.locator.value) ? <section className="citation-sheet-view" aria-label="셀 원문">
            <button type="button" onClick={() => { sheetRequestRef.current = null; setSheetCitation(null); }}>원문 닫기</button>
            {sheetCitation.error ? <p role="alert">셀 원문을 확인할 수 없습니다. ({sheetCitation.error})</p> : sheetCitation.content ? <SheetOriginalTable content={sheetCitation.content} /> : <p role="status">셀 원문을 불러오는 중입니다.</p>}
          </section> : null}
          {imageCitation?.region && viewState.answer?.citations?.some((current) => current.citation_id === imageCitation.citation_id && current.source_version_id === imageCitation.source_version_id && current.locator.kind === "image_region" && current.locator.value === imageCitation.locator.value) ? <section className="citation-image-view" aria-label="이미지 원문">
            <button type="button" onClick={() => setImageCitation(null)}>원문 닫기</button>
            {imageCitationError ? <p role="alert">이미지 원문을 확인할 수 없습니다. ({imageCitationError})</p> : <div className="citation-image-frame">
              <img key={imageCitation.citation_id} src={imageCitation.content_url} alt="Citation 원문 이미지" onError={() => setImageCitationError("CITATION_CONTENT_UNAVAILABLE")} onLoad={(event) => {
                if (event.currentTarget.naturalWidth !== imageCitation.region.imageWidth || event.currentTarget.naturalHeight !== imageCitation.region.imageHeight) {
                  setImageCitationError("CITATION_REGION_MISMATCH");
                } else setImageCitationLoaded(true);
              }} />
              {imageCitationLoaded ? <span className="citation-image-region" aria-label="근거 영역" style={{ left: `${imageCitation.region.x / imageCitation.region.imageWidth * 100}%`, top: `${imageCitation.region.y / imageCitation.region.imageHeight * 100}%`, width: `${imageCitation.region.width / imageCitation.region.imageWidth * 100}%`, height: `${imageCitation.region.height / imageCitation.region.imageHeight * 100}%` }} /> : null}
            </div>}
          </section> : null}</div><form className="conversation-composer" onSubmit={askQuestion}>
            <label><span className="sr-only">질문</span><textarea rows="2" disabled={questionPending} placeholder={viewState.selectedSource || selectedKnowledgeId ? "선택한 지식과 작업에 대해 질문하세요" : "작업 내용이나 궁금한 점을 질문하세요"} value={question} onChange={(event) => setQuestion(event.currentTarget.value)} /></label>
            <div className="notebook-model-settings" aria-label="Notebook 모델 설정">
              <label>Provider · 모델<select aria-label="Provider · 모델" disabled={questionPending||notebookModel.status!=="ready"}
                value={displayedSelection?JSON.stringify([displayedSelection.connection_id,displayedSelection.model_id]):""}
                onChange={event=>{const row=notebookModel.view?.available_models.find(v=>JSON.stringify([v.connection_id,v.model_id])===event.target.value);
                  if(row)return saveNotebookModel({connection_id:row.connection_id,model_id:row.model_id,reasoning_effort:null});}}>
                <option value="">모델을 직접 선택해 주세요</option>
                {(notebookModel.view?.available_models??[]).map(row=><option key={JSON.stringify([row.connection_id,row.model_id])} value={JSON.stringify([row.connection_id,row.model_id])}>{row.display_name} · {row.provider_code} / {row.model_id}</option>)}
              </select></label>
              {(()=>{const selection=displayedSelection;const candidate=notebookModel.view?.available_models.find(r=>r.connection_id===selection?.connection_id&&r.model_id===selection?.model_id);
                const supported=candidate?.reasoning.status==="supported";
                if(candidate?.reasoning.status==="unsupported")return null;
                return <label>추론 단계<select aria-label="추론 단계" value={selection?.reasoning_effort??""}
                  disabled={questionPending||notebookModel.status!=="ready"||!supported}
                  onChange={event=>saveNotebookModel({...selection,reasoning_effort:event.target.value||null})}>
                  <option value="">{supported?"미지정":candidate?.reasoning.status==="unsupported"?"현재 실행 경로 미지원":"추론 설정 정보 없음"}</option>
                  {(candidate?.reasoning.values??[]).map(v=><option key={v.value} value={v.value}>{v.label}</option>)}
                </select></label>;})()}
              <small role="status">{notebookModel.status==="saving"?"모델 설정 저장 중":notebookModel.status==="loading"?"모델 설정 조회 중":notebookModel.status==="unsupported"?"모델 설정 조회를 지원하지 않는 연결입니다":notebookModel.status==="error"?"모델 설정을 확인하지 못했습니다. 재조회 후 다시 선택해 주세요.":modelReady?"모델 설정 저장됨":notebookModel.view?.selection?"저장한 모델을 사용할 수 없습니다. 다시 선택해 주세요.":workspaceDefault?"Workspace 기본값 · 실행할 때 현재 모델을 저장합니다.":"사용 가능한 모델을 직접 선택해 주세요."}</small>
              {notebookModel.status==="error"?<><button type="button" onClick={()=>loadNotebookModel()}>마지막 저장값 다시 조회</button>
                {modelSaveRetry.current?<button type="button" onClick={()=>saveNotebookModel(notebookModel.draft,{retry:true})}>같은 저장 요청 재시도</button>:null}</>:null}
            </div>
            <button className="composer-submit" type="submit" aria-label="질문 실행" disabled={questionPending || !question.trim() || !canSubmitQuestion()}>↑</button>
          </form></div>}
        </SafePane>
        <SafePane id="product-pane-studio" title="업무 Studio" description="근거가 확인된 답변으로 보고서를 생성하고 저장 결과를 확인합니다.">
          {desktopStudio ? desktopStudio : <><ProductStudioPane
            adapter={studioModelAdapter}
            initialOutput={notificationOutput}
            onKnowledgeRegistered={() => setLoadRevision((current) => current + 1)}
            state={createProductStudioState({
              workspaceId,
              sources: viewState.sources,
              selectedSourceVersionIds: viewState.selectedSource?.sourceVersionId
                ? [viewState.selectedSource.sourceVersionId]
                : viewState.sources.filter((source) => source.ready).map((source) => source.sourceVersionId),
              grounded: canCreateGroundedReport(viewState) ? {
                sourceId: viewState.selectedSource?.sourceId ?? viewState.answer.citations[0].source_id,
                sourceVersionId: viewState.selectedSource?.sourceVersionId ?? viewState.answer.citations[0].source_version_id,
                sourceVersionIds: [...new Set(viewState.answer.citations.map((citation) => citation.source_version_id))],
                runId: viewState.answer.run_id,
                runResultId: viewState.answer.run_result_id,
              } : null,
              locks: Array.isArray(viewState.studioLocks) ? viewState.studioLocks : [],
              outputs: viewState.studioOutputs,
              status: viewState.studioStatus === "unavailable"
                ? "unavailable"
                : reportReady || viewState.sources.some((source) => source.ready) ? "ready" : "unavailable",
              safeError: viewState.studioSafeError ?? null,
            })}
          />
          {reportReady ? <details className="grounded-report-legacy"><summary>빠른 근거 보고서</summary><form onSubmit={createReport}>
            <label>보고서 제목<input value={reportTitle} maxLength={200} onChange={(event) => setReportTitle(event.currentTarget.value)} /></label>
            <label>결과 목적<input value={reportPurpose} maxLength={500} onChange={(event) => setReportPurpose(event.currentTarget.value)} /></label>
            <button type="submit" disabled={!reportReady || !reportTitle.trim() || !reportPurpose.trim() || reportPending}>보고서 생성</button>
          </form></details> : null}
          <ul aria-label="저장된 보고서">
            {viewState.studioOutputs.map((output) => (
              <li key={output.studio_output_id}>
                <strong>{output.title}</strong>
                <span>{output.status}</span>
                <ul aria-label={`${output.title} Citation 계보`}>
                  {(Array.isArray(output.citations) ? output.citations : []).map((citation) => (
                    <li key={citation.citation_id}>{citation.locator?.kind === "audio_segment" ? `Citation 음성 ${audioTime(parseAudioSegment(citation.locator.value)?.start_ms??0)}–${audioTime(parseAudioSegment(citation.locator.value)?.end_ms??0)}` : citation.locator?.kind === "pptx_block" ? `Citation ${pptxBlockLabel(citation.locator.value)}` : citation.locator?.kind === "docx_block" ? `Citation 문단·표 구간 ${citation.locator.value}` : citation.locator?.kind === "sheet_cell" ? `Citation ${parseSheetCell(citation.locator.value)?.sheet} · ${parseSheetCell(citation.locator.value)?.range}` : citation.locator?.kind === "image_region" ? `Citation 이미지 영역 ${citation.locator.value}` : `Citation page ${citation.page}`}</li>
                  ))}
                </ul>
              </li>
            ))}
          </ul></>}
        </SafePane>
      </div>
      </div>
      {modalView ? <div className="workspace-modal-backdrop" role="presentation" onMouseDown={(event) => { if (event.target === event.currentTarget) closeModal(); }}>
        <section ref={modalRef} className={`workspace-modal workspace-modal-${modalView}`} role="dialog" aria-modal="true" aria-labelledby="workspace-modal-title" onKeyDown={(event) => trapDialogFocus(event, closeModal)}>
          <header className="workspace-modal-header"><div><span className="section-kicker">WORKSPACE SETTINGS</span><h2 id="workspace-modal-title">{modalView === "question-step-up" ? "외부 질문 추가 인증" : modalView === "llm" ? "LLM 설정" : modalView === "output-version" ? "출력·버전" : modalView === "sync" ? "동기화·승인" : modalView === "approval-policy" ? "승인 정책" : modalView === "organization-policy" ? "조직 정책" : modalView === "license" ? "라이선스" : modalView === "manual" ? "사용자 설명서" : "운영상태"}</h2></div><button className="modal-close" type="button" disabled={(modalView === "output-version" && outputRequestRef.current?.kind === "save") || (modalView==="llm" && providerSettingsSaving)} onClick={() => closeModal()} aria-label="닫기">×</button></header>
          {modalView === "question-step-up" ? <div className="workspace-modal-content">
            <p>현재 선택한 자료와 질문의 외부 전송에 추가 인증이 필요합니다.</p>
            <label>현재 비밀번호<input ref={questionPasswordRef} type="password" autoComplete="off" aria-label="외부 질문 추가 인증 비밀번호" disabled={questionAuthPending} /></label>
            {questionAuthError ? <div role="alert">{questionAuthError}</div> : null}
            {questionAuthPending ? <div role="status">추가 인증을 확인하고 있습니다.</div> : null}
            <button type="button" disabled={questionAuthPending} onClick={authenticateQuestion}>인증 후 질문 제출</button>
            <button type="button" onClick={() => closeModal()}>질문 인증 취소</button>
          </div> : modalView === "llm" ? <div className="workspace-modal-content">{providerSettings ?? <div className="modal-unavailable" role="status"><strong>Provider 설정 연결이 필요합니다.</strong><p>현재 Workspace에서는 설정 상태를 불러올 수 없습니다.</p><button type="button" disabled>연결 기능 준비 중</button></div>}{providerCloseGuard ? <div className="unsaved-settings-guard" role="group" aria-live="polite" aria-label="저장하지 않은 Provider 변경"><strong>저장하지 않은 변경이 있습니다.</strong><p>입력만 버리고 닫습니다. 서버 작업은 취소되지 않으며 서버 저장 여부가 불확실하면 재조회가 필요합니다.</p><button type="button" disabled={providerSettingsSaving} onClick={()=>setProviderCloseGuard(false)}>계속 편집</button><button type="button" disabled={providerSettingsSaving} onClick={()=>closeModal(true)}>변경 버리고 닫기</button></div> : null}{providerSettingsSaving ? <p role="status">Provider 저장 중에는 창을 닫을 수 없습니다.</p> : null}</div> : modalView === "output-version" ? <div className="workspace-modal-content output-version-settings">
            {outputSettingsPending && !outputSettings ? <div className="modal-unavailable" role="status">출력 설정을 불러오고 있습니다.</div> : null}
            {outputSettingsSafeError ? <div className="modal-unavailable" role="alert"><strong>출력 설정을 처리하지 못했습니다.</strong><p>현재 저장 상태를 확인할 수 없습니다. 다시 불러와 확인해 주세요.</p><button type="button" disabled={outputSettingsPending} onClick={() => void loadOutputVersionSettings()}>다시 불러오기</button></div> : null}
            {outputSettings && outputDraft ? <form onSubmit={(event) => { event.preventDefault(); void saveOutputVersionSettings(); }}>
              <div className="output-format-grid">
                {Object.entries(OUTPUT_SETTING_LABELS).map(([type, label]) => <label key={type}><span>{label}</span><select value={outputDraft[type]} disabled={outputSettingsPending} onChange={(event) => { if (outputRequestRef.current) return; setOutputDraft((current) => ({ ...current, [type]: event.target.value })); setOutputSettingsDirty(true); }}>
                  {OUTPUT_SETTING_FORMATS[type].map((format) => <option key={format} value={format}>{format.toUpperCase()}</option>)}
                </select></label>)}
              </div>
              <div className="version-policy-row"><div><strong>Version 저장 방식</strong><small>기존 Version은 보존하고 새 Version을 추가합니다.</small></div><span>Append-only</span></div>
              <div className="modal-form-actions"><button type="button" disabled={!outputSettingsDirty || outputSettingsPending} onClick={() => { if (outputRequestRef.current) return; setOutputDraft({ ...outputSettings.default_formats }); setOutputSettingsDirty(false); }}>변경 취소</button><button type="submit" disabled={!outputSettingsDirty || outputSettingsPending} onClick={(event) => { event.preventDefault(); void saveOutputVersionSettings(); }}>{outputSettingsPending ? "저장 중" : "저장"}</button></div>
            </form> : null}
            {outputCloseGuard ? <div className="unsaved-settings-guard" role="alertdialog" aria-label="저장하지 않은 변경"><strong>저장하지 않은 변경이 있습니다.</strong><p>저장하거나 변경을 버린 뒤 닫아 주세요.</p><div><button type="button" disabled={outputSettingsPending} onClick={() => void saveOutputVersionSettings({ closeAfter: true })}>저장</button><button type="button" disabled={outputSettingsPending} onClick={() => { if (outputRequestRef.current?.kind === "save") return; setOutputSettingsDirty(false); closeModal(true); }}>버리기</button><button type="button" disabled={outputSettingsPending} onClick={() => { if (outputRequestRef.current?.kind !== "save") setOutputCloseGuard(false); }}>계속 편집</button></div></div> : null}
          </div> : modalView === "sync" ? <div className="workspace-modal-content sync-approval-settings" aria-live="polite">
            <div className="sync-settings-toolbar"><div><strong>동기화 Preview</strong><small>자동 전송하지 않습니다. 승인할 항목을 확인해 명시적으로 선택하세요.</small></div><button type="button" disabled={syncPending} onClick={() => void loadSyncOperations()}>새로고침</button></div>
            {syncPending && !syncOperations.length ? <div className="modal-unavailable" role="status">동기화 상태를 불러오고 있습니다.</div> : null}
            {syncSafeError ? <div className="modal-unavailable" role="alert"><strong>동기화 설정을 처리하지 못했습니다.</strong><p>{syncFailurePhase === "approval" ? "승인 결과를 확인할 수 없습니다. 목록을 다시 불러와 확인해 주세요." : syncFailurePhase === "authorization" ? "추가 인증을 완료하지 못했습니다. 인증 정보를 확인해 주세요." : "동기화 상태를 불러오지 못했습니다. 다시 확인해 주세요."}</p><p>실제 전송 상태는 승인 상태와 별도로 확인해 주세요.</p><button type="button" disabled={syncPending} onClick={() => void loadSyncOperations()}>다시 불러오기</button></div> : null}
            {!syncPending && !syncSafeError && !syncOperations.length ? <div className="sync-empty-state"><strong>승인을 기다리는 동기화가 없습니다.</strong><small>Windows 앱에서 Preview를 만들면 이 화면에서 확인할 수 있습니다.</small></div> : null}
            <div className="sync-operation-list">
              {syncOperations.map((operation) => <article key={operation.operation_id} data-sync-state={operation.state}>
                <header><div><strong>{operation.state === "awaiting_approval" ? "승인 대기" : operation.state === "approved" ? "승인됨" : operation.state === "conflict" ? "충돌 확인 필요" : "처리 상태"}</strong><small>항목 {operation.item_ids.length}개 · Version {operation.version}</small></div><span>{operation.state}</span></header>
                <fieldset disabled={operation.state !== "awaiting_approval" || syncPending}><legend>승인 항목</legend>{operation.item_ids.map((itemId) => <label key={itemId}><input type="checkbox" checked={(syncSelections[operation.operation_id] ?? []).includes(itemId)} onChange={(event) => { if (syncRequestRef.current) return; const checked = event.target.checked; setSyncSelections((current) => ({ ...current, [operation.operation_id]: checked ? [...new Set([...(current[operation.operation_id] ?? []), itemId])] : (current[operation.operation_id] ?? []).filter((id) => id !== itemId) })); }} /><span>{itemId}</span></label>)}</fieldset>
                {operation.state === "awaiting_approval" ? <button type="button" disabled={syncPending || !(syncSelections[operation.operation_id] ?? []).length} onClick={() => void approveSyncOperation(operation)}>선택 항목 승인</button> : null}
              </article>)}
            </div>
            {syncOperations.some((operation) => operation.state === "awaiting_approval") ? <label className="sync-step-up-password"><span>현재 비밀번호</span><input ref={syncPasswordRef} type="password" autoComplete="current-password" disabled={syncPending} /><small>승인 시 Step-up 인증에만 사용하며 저장하지 않습니다.</small></label> : null}
          </div> : modalView === "approval-policy" ? <StudioApprovalPolicySettings key={workspaceId} adapter={adapter} workspaceId={workspaceId} /> : modalView === "organization-policy" ? <div className="workspace-modal-content organization-policy-settings" aria-live="polite">
            {egressPolicyPending ? <div className="modal-unavailable" role="status">조직 정책을 불러오고 있습니다.</div> : null}
            {!egressPolicyPending && egressPolicySafeError ? <div className="modal-unavailable" role="alert"><strong>조직 정책을 불러오지 못했습니다.</strong><p>정책은 변경되지 않았습니다.</p><button type="button" onClick={() => void loadEgressPolicy()}>다시 불러오기</button></div> : null}
            {!egressPolicyPending && egressPolicy ? <>
              <div className="policy-lock-banner"><span aria-hidden="true">⌾</span><div><strong>조직 강제 정책</strong><small>이 Workspace에서는 읽기 전용이며 조직 정책을 완화할 수 없습니다.</small></div></div>
              <dl className="organization-policy-grid">
                <div><dt>정책 모드</dt><dd>{egressPolicyModeLabel(egressPolicy.organization_policy)}</dd></div>
                <div><dt>허용 Provider 종류</dt><dd>{egressPolicy.organization_policy.allowed_provider_kinds.join(", ") || "허용 없음"}</dd></div>
                <div><dt>허용 목적지</dt><dd>{egressPolicy.organization_policy.allowed_destinations.join(", ") || "허용 없음"}</dd></div>
                <div><dt>분류</dt><dd>{egressPolicy.organization_policy.classification}</dd></div>
                <div><dt>최대 전송량</dt><dd>{egressPolicy.organization_policy.max_bytes.toLocaleString("ko-KR")} bytes</dd></div>
                <div><dt>마스킹</dt><dd>{egressPolicy.organization_policy.masking_required ? "마스킹 필수" : "마스킹 선택"}</dd></div>
                <div><dt>삭제 처리</dt><dd>{egressPolicy.organization_policy.redaction_required ? "삭제 처리 필수" : "삭제 처리 선택"}</dd></div>
                <div><dt>필수 승인자</dt><dd>{EGRESS_APPROVER_LABELS[egressPolicy.organization_policy.required_approver]}</dd></div>
              </dl>
              <div className="workspace-policy-effective"><div><strong>Workspace 적용 결과</strong><small>{egressPolicy.parent_locked ? "조직 차단이 우선 적용됩니다." : "조직과 Workspace 정책의 교집합입니다."}</small></div><span>{egressPolicyModeLabel(egressPolicy)}</span></div>
            </> : null}
          </div> : modalView === "license" ? <div className="workspace-modal-content license-settings" aria-live="polite">
            {licensePending && !licenseView ? <div className="modal-unavailable" role="status">라이선스 정보를 확인하고 있습니다.</div> : null}
            {licenseSafeError ? <div className="modal-unavailable" role="alert"><strong>라이선스 요청을 처리하지 못했습니다.</strong><p>{licenseSafeError}</p><button type="button" disabled={licensePending} onClick={() => void loadLicense()}>다시 불러오기</button></div> : null}
            {licenseView ? <>
              <section className="license-summary" aria-label="라이선스 요약">
                <div><span>제품</span><strong>{licenseView.product}</strong></div>
                <div><span>Edition</span><strong>{licenseView.edition ?? "미적용"}</strong></div>
                <div><span>License ID</span><strong>{licenseView.license_id_hint ?? "-"}</strong></div>
                <div><span>상태</span><strong data-license-status={licenseView.status}>{LICENSE_STATUS_LABELS[licenseView.status] ?? licenseView.status}</strong></div>
                <div><span>발급</span><strong>{licenseView.issued_at ? new Date(licenseView.issued_at).toLocaleDateString("ko-KR") : "-"}</strong></div>
                <div><span>만료</span><strong>{licenseView.expires_at ? new Date(licenseView.expires_at).toLocaleDateString("ko-KR") : "-"}</strong></div>
              </section>
              {licenseView.warning ? <div className="license-warning" role="status"><strong>{LICENSE_STATUS_LABELS[licenseView.status]}</strong><span>{licenseView.warning.action}</span></div> : null}
              <section className="license-features" aria-labelledby="license-feature-title"><h3 id="license-feature-title">허용 기능</h3><div>{licenseView.features.map((feature) => <span key={feature}>{feature}</span>)}</div></section>
              <section className="license-resources" aria-labelledby="license-resource-title"><h3 id="license-resource-title">사용 한도</h3>{licenseView.resources.map((resource) => <article key={resource.resource} data-license-resource-status={resource.status}>
                <div><strong>{LICENSE_RESOURCE_LABELS[resource.resource] ?? resource.resource}</strong><span>{resource.used.toLocaleString("ko-KR")} / {resource.limit.toLocaleString("ko-KR")}</span></div>
                <progress max={resource.limit} value={Math.min(resource.used, resource.limit)} aria-label={`${LICENSE_RESOURCE_LABELS[resource.resource] ?? resource.resource} 사용량`} />
                <small>잔여 {resource.remaining.toLocaleString("ko-KR")}</small>
              </article>)}</section>
              {!licenseView.can_apply ? <p className="license-readonly-note">일반 사용자는 라이선스 정보를 읽기 전용으로 확인합니다.</p> : <form className="license-apply-form" onSubmit={(event) => { event.preventDefault(); void applyLicense(); }}>
                <header><strong>서명 라이선스 적용</strong><button className="info-button" type="button" title="승인된 Public key로 서명·제품·조직·기간·Schema를 검증합니다. 원문은 저장하지 않습니다." aria-label="라이선스 적용 설명">i</button></header>
                <label><span>License document</span><input ref={licenseFileRef} type="file" accept="application/json,.json" disabled={licensePending} /></label>
                <label><span>현재 비밀번호</span><input ref={licensePasswordRef} type="password" minLength={12} maxLength={1024} autoComplete="current-password" disabled={licensePending} /></label>
                <button type="submit" disabled={licensePending}>{licensePending ? "검증·적용 중" : "Step-up 후 검증·적용"}</button>
              </form>}
            </> : null}
          </div> : modalView === "manual" ? <div className="workspace-modal-content manual-hub" aria-live="polite">
            <div className="manual-hub-toolbar">
              <div><strong>Daon 문서 Hub</strong><small>{manualManifest ? `Release ${manualManifest.release_version} · ${manualManifest.language}` : "Release 정보를 확인하고 있습니다."}</small></div>
              <label><span className="sr-only">설명서 검색</span><input type="search" value={manualSearch} onChange={(event) => setManualSearch(event.target.value)} placeholder="문서 제목 또는 설명 검색" disabled={!manualManifest || manualPending} /></label>
            </div>
            {manualPending && !manualManifest ? <div className="modal-unavailable" role="status">사용자 설명서를 불러오고 있습니다.</div> : null}
            {manualSafeError ? <div className="modal-unavailable" role="alert"><strong>사용자 설명서를 처리하지 못했습니다.</strong><p>허용된 Release 문서를 다시 확인해 주세요.</p><button type="button" disabled={manualPending} onClick={() => void loadManualHub()}>다시 불러오기</button></div> : null}
            <div className="manual-hub-layout">
              <nav className="manual-document-list" aria-label="Daon 설명서 목록">
                {manualManifest?.documents.filter((item) => `${item.title} ${item.summary}`.toLocaleLowerCase("ko-KR").includes(manualSearch.trim().toLocaleLowerCase("ko-KR"))).map((item) => <article key={item.document_id} data-manual-scope={item.auth_scope}>
                  <button type="button" aria-pressed={manualDocument?.document_id === item.document_id} disabled={manualPending} onClick={() => void readManual(item.document_id)}><strong>{item.title}</strong><small>{item.summary}</small><span>Web에서 읽기</span></button>
                  <div><button type="button" disabled={manualPending} onClick={() => void downloadManual(item.document_id, "docx")}>DOCX</button><button type="button" disabled={manualPending} onClick={() => void downloadManual(item.document_id, "pdf")}>PDF</button></div>
                </article>)}
              </nav>
              <article className="manual-reader" aria-label="선택한 설명서 본문">
                {manualDocument ? <><header><div><strong>{manualDocument.title}</strong><small>검증된 Release Markdown 정본</small></div><button type="button" onClick={() => setManualDocument(null)}>목록 보기</button></header><ManualMarkdown text={manualDocument.text} /></> : <div className="manual-reader-empty"><span aria-hidden="true">▤</span><strong>읽을 문서를 선택해 주세요.</strong><small>문서별 공개 범위와 로그인 후 조직 전용 절차를 구분해 제공합니다.</small></div>}
              </article>
            </div>
          </div> : <div className="operations-status-grid" aria-live="polite">
            {operationsPending ? <div className="modal-unavailable" role="status">운영상태를 확인하고 있습니다.</div> : null}
            {!operationsPending && operationsSafeError ? <div className="modal-unavailable" role="alert"><strong>운영상태를 불러오지 못했습니다.</strong><p>잠시 후 안전하게 다시 확인해 주세요.</p><button type="button" onClick={() => void loadOperationsStatus()}>상태 새로고침</button></div> : null}
            {!operationsPending && operationsStatus?.components?.map((item) => <article key={item.component_id} data-operation-status={item.status}>
              <span className={`status-dot status-${item.status}`} aria-hidden="true" />
              <div><strong>{OPERATIONS_LABELS[item.component_id]}</strong><small>{OPERATIONS_MESSAGES[item.safe_code] ?? "상태를 확인할 수 없습니다."}{item.pending_count ? ` · ${item.pending_count}건` : ""}</small></div>
              {item.recovery_action === "open_llm_settings" ? <button type="button" onClick={() => setModalView("llm")}>LLM 설정 열기</button> : null}
              {item.recovery_action === "open_sync_settings" ? <button type="button" onClick={() => { closeModal(); setSettingsMenuOpen(true); }}>동기화 설정 열기</button> : null}
              {item.recovery_action === "refresh_status" ? <button type="button" onClick={() => void loadOperationsStatus()}>상태 새로고침</button> : null}
            </article>)}
          </div>}
        </section>
      </div> : null}
    </main>
  );
}

export function ProductWorkspaceShell(props) {
  const identity = JSON.stringify([
    props.workspaceId ?? null,
    props.adapter?.notebookContext?.notebook_id ?? null,
    workspaceAdapterKey(props.adapter),
  ]);
  return <ProductWorkspaceShellInner key={identity} {...props} />;
}
