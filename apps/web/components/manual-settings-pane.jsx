"use client";

import { useEffect, useMemo, useState } from "react";
import { downloadManualAsset, getManualManifest, readManualDocument } from "../lib/manual-api.js";

function saveDownload(result) {
  const href = URL.createObjectURL(result.blob);
  const link = document.createElement("a");
  link.href = href; link.download = result.filename; link.click();
  queueMicrotask(() => URL.revokeObjectURL(href));
}

export function ManualSettingsPane() {
  const [manifest, setManifest] = useState(null);
  const [selected, setSelected] = useState(null);
  const [search, setSearch] = useState("");
  const [pending, setPending] = useState(true);
  const [safeError, setSafeError] = useState(null);
  const visible = useMemo(() => manifest?.documents.filter((item) => `${item.title} ${item.summary}`.toLocaleLowerCase("ko-KR").includes(search.trim().toLocaleLowerCase("ko-KR"))) ?? [], [manifest, search]);
  const load = async () => { setPending(true); setSafeError(null); try { setManifest(await getManualManifest()); } catch { setSafeError("MANUAL_UNAVAILABLE"); } finally { setPending(false); } };
  useEffect(() => { void load(); }, []);
  const read = async (documentId) => { if (!manifest || pending) return; setPending(true); setSafeError(null); try { setSelected(await readManualDocument(documentId, { manifest })); } catch { setSafeError("MANUAL_CONTENT_INVALID"); } finally { setPending(false); } };
  const download = async (documentId, format) => { if (!manifest || pending) return; setPending(true); setSafeError(null); try { saveDownload(await downloadManualAsset(documentId, format, { manifest })); } catch { setSafeError("MANUAL_CONTENT_INVALID"); } finally { setPending(false); } };

  return <main className="common-settings-page" aria-labelledby="manual-settings-title">
    <header className="common-settings-header"><a href="/notebooks">← Notebook 홈</a><p>HELP CENTER</p><h1 id="manual-settings-title">사용자 설명서</h1><span>{manifest ? `Release ${manifest.release_version} · ${manifest.language}` : "검증된 Daon 문서를 불러옵니다."}</span></header>
    <label className="manual-page-search"><span className="sr-only">설명서 검색</span><input type="search" value={search} onChange={(event) => setSearch(event.target.value)} placeholder="문서 제목 또는 설명 검색" disabled={!manifest || pending} /></label>
    {pending && !manifest ? <p role="status" className="common-settings-state">사용자 설명서를 불러오고 있습니다.</p> : null}
    {safeError ? <div role="alert" className="common-settings-error"><strong>사용자 설명서를 처리하지 못했습니다.</strong><span>{safeError}</span><button type="button" onClick={() => void load()}>다시 시도</button></div> : null}
    <div className="manual-settings-layout"><nav className="manual-page-list" aria-label="Daon 설명서 목록">{visible.map((item) => <article key={item.document_id}><button type="button" disabled={pending} aria-pressed={selected?.document_id === item.document_id} onClick={() => void read(item.document_id)}><strong>{item.title}</strong><span>{item.summary}</span></button><div><button type="button" disabled={pending} onClick={() => void download(item.document_id, "docx")}>DOCX</button><button type="button" disabled={pending} onClick={() => void download(item.document_id, "pdf")}>PDF</button></div></article>)}</nav><article className="manual-page-reader" aria-label="선택한 설명서 본문">{selected ? <><header><strong>{selected.title}</strong><button type="button" onClick={() => setSelected(null)}>목록 보기</button></header><pre>{selected.text}</pre></> : <div><span aria-hidden="true">▤</span><strong>읽을 문서를 선택해 주세요.</strong><small>Web 읽기와 검증된 DOCX·PDF 다운로드를 제공합니다.</small></div>}</article></div>
  </main>;
}
