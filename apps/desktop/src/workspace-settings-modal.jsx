import { useState } from "react";
import { WorkspaceModal } from "./workspace-modal.jsx";

export function WorkspaceSettingsModal({ open, onClose, offlineState, onSave }) {
  const [dirty, setDirty] = useState(false);
  const [confirmClose, setConfirmClose] = useState(false);
  const [deploymentId, setDeploymentId] = useState(offlineState.selectedModelDeploymentId ?? "");
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
          <fieldset disabled><legend>조직 정책</legend><p>🔒 RuleSet · Review · Egress</p></fieldset>
          <button type="submit">Save · 저장</button>
        </form>
      )}
    </WorkspaceModal>
  );
}
