import { useEffect, useMemo, useState } from "react";
import { WorkspaceModal } from "./workspace-modal.jsx";
import { createScreenPreferencesBridge } from "./screen-preferences-bridge.js";

export function WorkspaceSettingsModal({ open, onClose, offlineState, onSave, nativeInvoke, onScreenTheme }) {
  const [dirty, setDirty] = useState(false);
  const [confirmClose, setConfirmClose] = useState(false);
  const [deploymentId, setDeploymentId] = useState(offlineState.selectedModelDeploymentId ?? "");
  const [screenTheme, setScreenTheme] = useState("system");
  const [screenStatus, setScreenStatus] = useState("화면 설정을 불러오는 중입니다.");
  const bridge = useMemo(() => {
    try { return createScreenPreferencesBridge({ invoke: nativeInvoke }); }
    catch { return null; }
  }, [nativeInvoke]);
  useEffect(() => {
    if (!open || !bridge) return;
    let active = true;
    void bridge.get().then(({ theme }) => {
      if (active) { setScreenTheme(theme); onScreenTheme(theme); setScreenStatus("화면 설정을 불러왔습니다."); }
    }).catch(() => { if (active) setScreenStatus("화면 설정을 불러오지 못했습니다."); });
    return () => { active = false; };
  }, [open, bridge, onScreenTheme]);
  const saveScreenTheme = async (theme) => {
    if (!bridge) return;
    setScreenTheme(theme); onScreenTheme(theme); setScreenStatus("화면 설정을 저장하는 중입니다.");
    try { await bridge.save(theme); setScreenStatus("화면 설정을 저장했습니다."); }
    catch { setScreenStatus("화면 설정을 저장하지 못했습니다. 현재 화면을 유지합니다."); }
  };
  const resetScreenTheme = async () => {
    if (!bridge) return;
    setScreenStatus("화면 설정을 초기화하는 중입니다.");
    try {
      const { theme } = await bridge.reset();
      setScreenTheme(theme);
      onScreenTheme(theme);
      setScreenStatus("화면 설정을 초기화했습니다.");
    } catch { setScreenStatus("화면 설정을 초기화하지 못했습니다. 현재 화면을 유지합니다."); }
  };
  const save = () => {
    onSave(deploymentId);
    setDirty(false);
    setConfirmClose(false);
    onClose();
  };
  const requestClose = () => dirty ? setConfirmClose(true) : onClose();
  const discard = () => { setDirty(false); setConfirmClose(false); onClose(); };
  return (
    <WorkspaceModal open={open} onRequestClose={requestClose} title="설정" titleId="settings-dialog-title">
      {confirmClose ? (
        <div role="alert">
          <p>저장하지 않은 변경이 있습니다.</p>
          <button type="button" onClick={save}>Save · 저장</button>
          <button type="button" onClick={discard}>Discard · 버리기</button>
          <button type="button" onClick={() => setConfirmClose(false)}>Continue editing · 계속 편집</button>
        </div>
      ) : (
        <form onChange={() => setDirty(true)} onSubmit={(event) => { event.preventDefault(); save(); }}>
          <fieldset><legend>Local Model</legend><select value={deploymentId} onChange={(event) => setDeploymentId(event.currentTarget.value)}><option value="">unavailable · 선택 필요</option>{offlineState.models.map((model) => <option key={model.deployment_id} value={model.deployment_id} disabled={model.provider_code !== "OLLAMA" || model.provider_kind !== "server_internal" || model.readiness !== "ready"}>{model.label ?? model.deployment_id}</option>)}</select><p>선택은 다음 설정 확인 시 불변 Generation Settings에 저장됩니다.</p></fieldset>
          <fieldset><legend>현재 고정 정책</legend><dl><div><dt>Output</dt><dd>문서</dd></div><div><dt>Version save mode</dt><dd>새 Version</dd></div><div><dt>Sync approval mode</dt><dd>사용자 승인 후</dd></div></dl></fieldset>
          <fieldset><legend>화면 설정</legend><label>테마<select value={screenTheme} onChange={(event) => { void saveScreenTheme(event.currentTarget.value); }} disabled={!bridge}><option value="system">시스템 설정</option><option value="light">밝게</option><option value="dark">어둡게</option></select></label><button type="button" onClick={() => { void resetScreenTheme(); }} disabled={!bridge}>화면 설정 초기화</button><p role="status" aria-live="polite">{screenStatus}</p></fieldset>
          <fieldset disabled><legend>조직 정책</legend><p>🔒 RuleSet · Review · Egress</p></fieldset>
          <button type="submit">Save · 저장</button>
        </form>
      )}
    </WorkspaceModal>
  );
}
