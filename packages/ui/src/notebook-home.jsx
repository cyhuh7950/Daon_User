"use client";

import { useEffect, useMemo, useRef, useState } from "react";
import { focusInitialModalControl, setBackgroundInert, trapModalTab } from "./workspace-interaction.js";
import "./notebook-home.css";

const STATUS_LABEL = Object.freeze({ empty: "비어 있음", active: "작업 중", attention: "확인 필요" });
const SAFE_CREATE_ERRORS = new Set([
  "NOTEBOOK_UNAVAILABLE", "NOTEBOOK_TITLE_INVALID", "IDEMPOTENCY_KEY_REUSED",
  "LICENSE_REQUIRED", "LICENSE_EXPIRED", "LICENSE_RESOURCE_LIMIT_REACHED",
]);
const SAFE_DELETE_ERRORS = new Set(["NOTEBOOK_TITLE_CONFIRMATION_MISMATCH", "NOTEBOOK_ETAG_MISMATCH", "NOTEBOOK_DELETION_IN_PROGRESS", "DELETE_SHARED_DATA_BLOCKED", "RETENTION_HOLD"]);
const safeText = (value) => typeof value === "string" ? value : "";

function SettingsMenu({ onOpenSetting, onLogout }) {
  const [open, setOpen] = useState(false);
  return <div className="notebook-settings-wrap">
    <button className="notebook-toolbar-button" type="button" aria-expanded={open} aria-haspopup="menu" onClick={() => setOpen((value) => !value)}>⚙ 설정</button>
    {open && <div className="notebook-settings-menu" role="menu" aria-label="공통 설정">
      {[['screen', '화면 설정'], ['license', '라이선스'], ['manual', '사용자 설명서']].map(([id, label]) =>
        <button key={id} role="menuitem" type="button" onClick={() => { setOpen(false); onOpenSetting?.(id); }}>{label}</button>)}
      <button role="menuitem" type="button" onClick={() => { setOpen(false); onLogout?.(); }}>로그아웃</button>
    </div>}
  </div>;
}

function CreateDialog({ onClose, onCreate }) {
  const dialogRef = useRef(null);
  const titleRef = useRef(null);
  const pendingRef = useRef(false);
  const [title, setTitle] = useState("");
  const [description, setDescription] = useState("");
  const [saving, setSaving] = useState(false);
  const [safeError, setSafeError] = useState(null);
  useEffect(() => { focusInitialModalControl(dialogRef.current); }, []);
  const submit = async (event) => {
    event.preventDefault();
    if (!title.trim() || pendingRef.current) return;
    pendingRef.current = true;
    setSaving(true);
    setSafeError(null);
    try {
      await onCreate?.({ title, description: description || null });
      onClose();
    } catch (error) {
      setSafeError(SAFE_CREATE_ERRORS.has(error?.message) ? error.message : "NOTEBOOK_CREATE_FAILED");
    } finally {
      pendingRef.current = false;
      setSaving(false);
    }
  };
  return <div className="notebook-dialog-backdrop" role="presentation">
    <form ref={dialogRef} className="notebook-dialog" role="dialog" aria-modal="true" aria-labelledby="new-notebook-title" onSubmit={submit} onKeyDown={(event) => {
      if (event.key === "Escape") { event.preventDefault(); onClose(); return; }
      trapModalTab(event.currentTarget, event);
    }}>
      <div className="notebook-dialog-heading"><h2 id="new-notebook-title">새 Notebook</h2><button type="button" aria-label="닫기" onClick={onClose}>×</button></div>
      <label>제목<input ref={titleRef} autoFocus data-modal-initial-focus maxLength={120} required value={title} onChange={(event) => setTitle(event.target.value)} /></label>
      <label>설명 <span>(선택)</span><textarea maxLength={500} value={description} onChange={(event) => setDescription(event.target.value)} /></label>
      {safeError && <p className="notebook-dialog-error" role="alert">{safeError}</p>}
      <div className="notebook-dialog-actions"><button type="button" onClick={onClose}>취소</button><button className="primary" disabled={!title.trim() || saving} type="submit">{saving ? "만드는 중…" : "만들기"}</button></div>
    </form>
  </div>;
}

function DeleteDialog({ notebook, onClose, onDelete }) {
  const [title, setTitle] = useState(""); const [error, setError] = useState(null); const [pending, setPending] = useState(false);
  const submit = async (event) => { event.preventDefault(); if (title !== notebook.title || pending) return; setPending(true); setError(null); try { await onDelete(notebook, title); onClose(); } catch (e) { setError(SAFE_DELETE_ERRORS.has(e?.message) ? e.message : "NOTEBOOK_DELETE_FAILED"); setPending(false); } };
  return <div className="notebook-dialog-backdrop"><form className="notebook-dialog" role="dialog" aria-modal="true" onSubmit={submit}><div className="notebook-dialog-heading"><h2>노트북 삭제</h2><button type="button" aria-label="닫기" onClick={onClose}>×</button></div><p>삭제할 노트북: <strong>{notebook.title}</strong></p><label>삭제를 확인하려면 제목을 입력하세요<input autoFocus value={title} onChange={(e) => setTitle(e.target.value)} /></label>{error && <p className="notebook-dialog-error" role="alert">{error}</p>}<div className="notebook-dialog-actions"><button type="button" onClick={onClose}>취소</button><button className="primary" disabled={title !== notebook.title || pending} type="submit">{pending ? "삭제 중…" : "영구 삭제"}</button></div></form></div>;
}

function NotebookCard({ notebook, viewMode, onOpenNotebook, onDelete }) {
  const [menu, setMenu] = useState(false); const [deleting, setDeleting] = useState(false);
  return <div className={`notebook-card notebook-card-${viewMode}`}>
    <button className="notebook-card-main" type="button" onClick={() => onOpenNotebook?.({ notebookId: notebook.notebook_id, mode: "existing" })} aria-label={`${safeText(notebook.title)} Notebook 열기`}>
    <span className="notebook-card-art" aria-hidden="true"><span>✦</span></span>
    <span className="notebook-card-body"><strong>{safeText(notebook.title)}</strong><span>Source {notebook.source_count} · 산출물 {notebook.output_count}</span><span>{new Date(notebook.updated_at).toLocaleString("ko-KR")} · {STATUS_LABEL[notebook.status] ?? "확인 필요"}</span></span>
    <span className="notebook-card-arrow" aria-hidden="true">›</span>
    </button><button type="button" className="notebook-card-menu-button" aria-label={`${safeText(notebook.title)} 메뉴`} aria-expanded={menu} onClick={() => setMenu((v) => !v)}>⋮</button>{menu && <div className="notebook-card-menu" role="menu"><button role="menuitem" type="button" onClick={() => { setMenu(false); setDeleting(true); }}>노트북 삭제</button></div>}{deleting && <DeleteDialog notebook={notebook} onClose={() => setDeleting(false)} onDelete={onDelete} />}</div>;
}

export function NotebookHome({ state = "ready", notebooks = [], errorCode = null, onReload, onCreate, onDelete, onOpenNotebook, onOpenSetting, onLogout }) {
  const surfaceRef = useRef(null);
  const createOpenerRef = useRef(null);
  const [search, setSearch] = useState("");
  const [sortMode, setSortMode] = useState("updated");
  const [viewMode, setViewMode] = useState("grid");
  const [creating, setCreating] = useState(false);
  useEffect(() => {
    setBackgroundInert(surfaceRef.current, creating);
    return () => setBackgroundInert(surfaceRef.current, false);
  }, [creating]);
  const visible = useMemo(() => notebooks
    .filter((item) => safeText(item.title).toLocaleLowerCase("ko-KR").includes(search.trim().toLocaleLowerCase("ko-KR")))
    .sort((a, b) => sortMode === "title"
      ? safeText(a.title).localeCompare(safeText(b.title), "ko-KR")
      : safeText(b.updated_at).localeCompare(safeText(a.updated_at))), [notebooks, search, sortMode]);
  const create = async (input) => {
    const notebook = await onCreate?.(input);
    if (notebook?.notebook_id) onOpenNotebook?.({ notebookId: notebook.notebook_id, mode: "empty" });
  };

  const closeCreate = () => {
    setCreating(false);
    queueMicrotask(() => createOpenerRef.current?.focus?.());
  };

  return <main className="notebook-home" aria-busy={state === "loading"}>
    <div ref={surfaceRef} className="notebook-home-surface">
    <header className="notebook-home-header"><a className="notebook-brand" href="#notebook-home" aria-label="Daon Notebook 홈"><span aria-hidden="true">◒</span>Daon Notebook</a><SettingsMenu onOpenSetting={onOpenSetting} onLogout={onLogout} /></header>
    <section id="notebook-home" className="notebook-home-content" aria-labelledby="notebook-home-title">
      <div className="notebook-home-intro"><div><p>MY NOTEBOOKS</p><h1 id="notebook-home-title">지식에서 결과까지, 하나의 Notebook에서</h1></div><button ref={createOpenerRef} className="notebook-create-button" type="button" onClick={() => setCreating(true)}>＋ 새 Notebook</button></div>
      <div className="notebook-home-tools">
        <label className="notebook-search" htmlFor="notebook-search"><span aria-hidden="true">⌕</span><input id="notebook-search" type="search" placeholder="Notebook 제목 검색" value={search} onChange={(event) => setSearch(event.target.value)} /></label>
        <label className="sr-only" htmlFor="notebook-sort">정렬</label><select id="notebook-sort" value={sortMode} onChange={(event) => setSortMode(event.target.value)}><option value="updated">최근 수정</option><option value="title">제목</option></select>
        <div className="notebook-view-toggle" role="group" aria-label="보기 방식"><button className={viewMode === "grid" ? "selected" : ""} aria-pressed={viewMode === "grid"} type="button" onClick={() => setViewMode("grid")}>▦</button><button className={viewMode === "list" ? "selected" : ""} aria-pressed={viewMode === "list"} type="button" onClick={() => setViewMode("list")}>☷</button></div>
      </div>
      {state === "loading" && <div id="notebook-home-loading" className="notebook-state" role="status"><span className="notebook-spinner" />Notebook을 불러오는 중입니다.</div>}
      {state === "error" && <div id="notebook-home-error" className="notebook-state notebook-error" role="alert"><strong>Notebook을 불러오지 못했습니다.</strong><span>{safeText(errorCode) || "NOTEBOOK_UNAVAILABLE"}</span><button type="button" onClick={onReload}>다시 시도</button></div>}
      {state === "ready" && notebooks.length === 0 && <div id="notebook-home-empty" className="notebook-state"><span className="notebook-empty-symbol" aria-hidden="true">＋</span><strong>첫 Notebook을 만들어 보세요</strong><span>제목과 선택 설명만 입력하면 빈 작업 공간이 열립니다.</span><button type="button" onClick={(event) => { createOpenerRef.current = event.currentTarget; setCreating(true); }}>새 Notebook</button></div>}
      {state === "ready" && notebooks.length > 0 && <><p className="notebook-result-count" aria-live="polite">Notebook {visible.length}개</p><div className={`notebook-collection ${viewMode}`}>
        {visible.map((notebook) => <NotebookCard key={notebook.notebook_id} notebook={notebook} viewMode={viewMode} onOpenNotebook={onOpenNotebook} onDelete={onDelete} />)}
        {visible.length === 0 && <div className="notebook-state"><strong>검색 결과가 없습니다.</strong><span>다른 제목으로 검색해 보세요.</span></div>}
      </div></>}
    </section></div>
    {creating && <CreateDialog onClose={closeCreate} onCreate={create} />}
  </main>;
}
