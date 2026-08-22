"use client";

import { useEffect, useRef, useState } from "react";
import { getCurrentNotebookSession } from "../lib/notebook-api.js";
import { applyCurrentOrganizationLicenseWithStepUp, getWorkspaceLicense } from "../lib/license-api.js";

const STATUS_LABEL = Object.freeze({ not_configured: "미설정", active: "정상", expiring_soon: "만료 예정", expired: "만료", limit_reached: "한도 도달" });

export function LicenseSettingsPane() {
  const fileRef = useRef(null);
  const passwordRef = useRef(null);
  const [view, setView] = useState(null);
  const [pending, setPending] = useState(true);
  const [safeError, setSafeError] = useState(null);

  const load = async () => {
    setPending(true); setSafeError(null);
    try {
      const session = await getCurrentNotebookSession();
      setView(await getWorkspaceLicense(session.workspace_id));
    } catch (error) {
      if (error?.message === "AUTHENTICATION_REQUIRED") { window.location.replace("/"); return; }
      setSafeError("LICENSE_UNAVAILABLE");
    } finally { setPending(false); }
  };
  useEffect(() => { void load(); }, []);

  const apply = async (event) => {
    event.preventDefault();
    const file = fileRef.current?.files?.[0];
    const password = passwordRef.current?.value ?? "";
    const validFile = file && file.size > 0 && file.size <= 65_536
      && file.name.toLocaleLowerCase("en-US").endsWith(".json")
      && (file.type === "application/json" || file.type === "");
    if (!validFile || password.length < 12 || pending) return;
    setPending(true); setSafeError(null);
    try {
      const document = JSON.parse(await file.text());
      setView(await applyCurrentOrganizationLicenseWithStepUp(document, password));
    } catch { setSafeError("LICENSE_APPLY_FAILED"); }
    finally {
      if (fileRef.current) fileRef.current.value = "";
      if (passwordRef.current) passwordRef.current.value = "";
      setPending(false);
    }
  };

  return <main className="common-settings-page" aria-labelledby="license-settings-title">
    <header className="common-settings-header"><a href="/notebooks">← Notebook 홈</a><p>WORKSPACE SETTINGS</p><h1 id="license-settings-title">라이선스</h1><span>Edition과 사용 한도를 확인하고 승인된 문서를 적용합니다.</span></header>
    {pending && !view ? <p role="status" className="common-settings-state">라이선스를 확인하고 있습니다.</p> : null}
    {safeError ? <div role="alert" className="common-settings-error"><strong>라이선스 요청을 처리하지 못했습니다.</strong><span>{safeError}</span><button type="button" onClick={() => void load()}>다시 시도</button></div> : null}
    {view ? <div className="license-settings-grid">
      <section className="settings-card license-overview"><h2>현재 라이선스</h2><dl><div><dt>제품</dt><dd>{view.product}</dd></div><div><dt>Edition</dt><dd>{view.edition ?? "미적용"}</dd></div><div><dt>상태</dt><dd>{STATUS_LABEL[view.status] ?? view.status}</dd></div><div><dt>만료</dt><dd>{view.expires_at ? new Date(view.expires_at).toLocaleDateString("ko-KR") : "-"}</dd></div></dl>{view.warning ? <p className="settings-warning" role="status">{view.warning.action}</p> : null}</section>
      <section className="settings-card"><h2>허용 기능</h2><div className="settings-tags">{view.features.map((feature) => <span key={feature}>{feature}</span>)}</div></section>
      <section className="settings-card license-resources"><h2>사용 한도</h2>{view.resources.map((resource) => <div key={resource.resource}><span>{resource.resource}</span><strong>{resource.used.toLocaleString()} / {resource.limit.toLocaleString()}</strong></div>)}</section>
      {view.can_apply ? <form className="settings-card license-apply" onSubmit={apply}><h2>라이선스 적용</h2><label>License document<input ref={fileRef} type="file" accept="application/json,.json" disabled={pending} required /></label><label>현재 비밀번호<input ref={passwordRef} type="password" minLength={12} maxLength={1024} autoComplete="current-password" disabled={pending} required /></label><button type="submit" disabled={pending}>{pending ? "검증 중…" : "Step-up 후 검증·적용"}</button></form> : <p className="settings-card">일반 사용자는 라이선스 정보를 읽기 전용으로 확인합니다.</p>}
    </div> : null}
  </main>;
}
