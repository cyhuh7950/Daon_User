const STATUS_COMMAND = "local_service_status";
const RETRY_COMMAND = "local_service_retry";
const DEFAULT_POLL_INTERVAL_MS = 1000;

const unavailable = Object.freeze({
  state: "unavailable",
  retryable: false,
  error_code: "NOT_TAURI_RUNTIME"
});

function nativeInvoke() {
  if (typeof window === "undefined") return null;
  return window.__TAURI_INTERNALS__?.invoke ?? null;
}

export async function readLocalServiceStatus(invoke = nativeInvoke()) {
  if (!invoke) return unavailable;
  try {
    return await invoke(STATUS_COMMAND);
  } catch {
    return {
      state: "unavailable",
      retryable: true,
      error_code: "LOCAL_SERVICE_STATUS_FAILED"
    };
  }
}

export async function retryLocalService(invoke = nativeInvoke()) {
  if (!invoke) return unavailable;
  try {
    return await invoke(RETRY_COMMAND);
  } catch {
    return {
      state: "unavailable",
      retryable: true,
      error_code: "LOCAL_SERVICE_RETRY_FAILED"
    };
  }
}

export function watchLocalServiceStatus(
  onStatus,
  {
    invoke = nativeInvoke(),
    intervalMs = DEFAULT_POLL_INTERVAL_MS,
    schedule = setTimeout,
    cancel = clearTimeout
  } = {}
) {
  let active = true;
  let timer = null;
  const poll = async () => {
    const status = await readLocalServiceStatus(invoke);
    if (!active) return;
    onStatus(status);
    timer = schedule(poll, intervalMs);
  };
  void poll();
  return () => {
    active = false;
    if (timer !== null) cancel(timer);
  };
}

export function describeLocalServiceState(status) {
  if (status.state === "starting") return "로컬 서비스를 시작하고 있습니다.";
  if (status.state === "ready") return "로컬 서비스가 준비되었습니다.";
  if (status.state === "retrying") return "로컬 서비스를 다시 시작하고 있습니다.";
  if (
    status.error_code === "LOCAL_HEALTH_FAILED"
    || status.error_code === "LOCAL_HEALTH_REJECTED"
    || status.error_code === "LOCAL_SERVICE_WAIT_FAILED"
  ) {
    return "로컬 서비스 상태를 확인할 수 없습니다.";
  }
  if (status.error_code === "LOCAL_SERVICE_BINARY_MISSING") {
    return "로컬 서비스 구성 요소를 찾을 수 없습니다.";
  }
  return "로컬 서비스를 사용할 수 없습니다.";
}
