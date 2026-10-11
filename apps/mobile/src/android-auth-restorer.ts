import type { AndroidAuthState } from "./android-auth-adapter.ts";

type NativeRestore = {
  getSessionState(): Promise<AndroidAuthState>;
  consumePendingAuthorization(): Promise<AndroidAuthState>;
};

export function createAndroidAuthRestorer(
  native: NativeRestore,
  onState: (state: AndroidAuthState) => void,
  onError: (failed: boolean) => void,
) {
  let generation = 0;
  let disposed = false;
  let inFlight: Promise<void> | null = null;

  const restore = (): Promise<void> => {
    if (disposed) return Promise.resolve();
    if (inFlight) return inFlight;
    const ownGeneration = generation;
    const current = () => !disposed && ownGeneration === generation;
    const task = (async () => {
      try {
        const state = await native.getSessionState();
        if (!current()) return;
        onState(state);
        if (state.status === "callback_ready") {
          try {
            const result = await native.consumePendingAuthorization();
            if (current()) { onState(result); onError(false); }
          } catch {
            if (current()) { onState({ status: "signed_out" }); onError(true); }
          }
        } else {
          onError(false);
        }
      } catch {
        if (current()) onError(true);
      }
    })();
    inFlight = task;
    void task.finally(() => { if (inFlight === task) inFlight = null; });
    return task;
  };

  return {
    restore,
    invalidate: () => { generation += 1; },
    dispose: () => { disposed = true; generation += 1; },
  };
}
