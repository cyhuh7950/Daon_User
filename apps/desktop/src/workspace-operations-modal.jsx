import { WorkspaceModal } from "./workspace-modal.jsx";

export function WorkspaceOperationsModal({ open, onClose, localService, offlineState, cloudState = "unknown" }) {
  const localOk = localService.state === "ready";
  const storageState = localService.storage_state ?? "unknown";
  const selectedModel = offlineState.models.find(
    (model) => model.deployment_id === offlineState.selectedModelDeploymentId,
  );
  const modelReady = offlineState.settingsConfirmed && selectedModel?.readiness === "ready";
  const modelStatus = modelReady
    ? "● 준비"
    : selectedModel?.readiness === "ready" ? "◐ 설정 확인 필요" : "○ 선택 필요";
  const cloudStatus = cloudState === "authenticated" ? "● 인증됨" : "○ unavailable";
  return (
    <WorkspaceModal open={open} onRequestClose={onClose} title="운영상태" titleId="operations-dialog-title">
      <dl className="operations-status-list">
        <div><dt>Local Service</dt><dd>{localOk ? "● 정상" : "▲ 주의"}</dd></div>
        <div><dt>Encrypted storage</dt><dd>{storageState === "unlocked" ? "◆ 연결됨" : storageState === "locked" ? "▲ unavailable" : "○ unknown"}</dd></div>
        <div><dt>Managed Local Model</dt><dd>{modelStatus}</dd></div>
        <div><dt>Cloud</dt><dd>{cloudStatus}</dd></div>
        <div><dt>Cloud Sync</dt><dd>{offlineState.sync.state ?? "unknown"}</dd></div>
        <div><dt>Pending jobs</dt><dd>{offlineState.sync.state === "awaiting_approval" ? 1 : 0}</dd></div>
        <div><dt>Last checked</dt><dd>현재 Session</dd></div>
      </dl>
      {!localOk ? <p className="modal-inline-alert" role="alert">LOCAL_SERVICE_ATTENTION</p> : null}
      <button type="button" onClick={onClose}>확인</button>
    </WorkspaceModal>
  );
}
