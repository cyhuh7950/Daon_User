const THEMES = new Set(["system", "light", "dark"]);

function failure(code) {
  const error = new Error(code);
  error.code = code;
  return error;
}

function nativeInvoke() {
  if (typeof window === "undefined") return null;
  return window.__TAURI_INTERNALS__?.invoke ?? null;
}

function project(value) {
  if (!value || Array.isArray(value) || Object.keys(value).length !== 1 || !THEMES.has(value.theme)) throw failure("SCREEN_PREFERENCE_RESPONSE_INVALID");
  return { theme: value.theme };
}

export function createScreenPreferencesBridge({ invoke = nativeInvoke() } = {}) {
  if (typeof invoke !== "function") throw failure("SCREEN_PREFERENCE_UNAVAILABLE");
  const call = async (command, args) => {
    try { return project(await invoke(command, args)); }
    catch { throw failure("SCREEN_PREFERENCE_UNAVAILABLE"); }
  };
  return Object.freeze({
    get: () => call("screen_preferences_get"),
    save: (theme) => THEMES.has(theme) ? call("screen_preferences_save", { theme }) : Promise.reject(failure("SCREEN_PREFERENCE_INPUT_INVALID")),
    reset: () => call("screen_preferences_reset"),
  });
}
