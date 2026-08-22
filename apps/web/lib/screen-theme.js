export const SCREEN_THEMES = Object.freeze(["system", "light", "dark"]);
export const SCREEN_THEME_STORAGE_KEY = "daon.screen-preference.v1";

export function isScreenTheme(value) {
  return SCREEN_THEMES.includes(value);
}

export function resolveScreenTheme(preference, prefersDark) {
  if (!isScreenTheme(preference)) return "light";
  return preference === "system" ? (prefersDark ? "dark" : "light") : preference;
}

export function applyScreenTheme(preference, documentRef = globalThis.document, prefersDark = globalThis.matchMedia?.("(prefers-color-scheme: dark)").matches === true) {
  const effective = resolveScreenTheme(preference, prefersDark);
  const root = documentRef?.documentElement;
  if (root) {
    root.dataset.theme = effective;
    root.style.colorScheme = effective;
  }
  return effective;
}

export function readCachedScreenTheme(storage = globalThis.localStorage) {
  try {
    const value = storage?.getItem(SCREEN_THEME_STORAGE_KEY);
    return isScreenTheme(value) ? value : "system";
  } catch {
    return "system";
  }
}

export function cacheScreenTheme(preference, storage = globalThis.localStorage) {
  if (!isScreenTheme(preference)) return false;
  try {
    storage?.setItem(SCREEN_THEME_STORAGE_KEY, preference);
    return true;
  } catch {
    return false;
  }
}

export function clearCachedScreenTheme(storage = globalThis.localStorage) {
  try { storage?.removeItem(SCREEN_THEME_STORAGE_KEY); } catch { /* storage is optional */ }
}

export function watchSystemScreenTheme(preference, onChange, media = globalThis.matchMedia?.("(prefers-color-scheme: dark)")) {
  if (preference !== "system" || !media || typeof onChange !== "function") return () => {};
  const listener = (event) => onChange(resolveScreenTheme("system", event.matches));
  media.addEventListener?.("change", listener);
  return () => media.removeEventListener?.("change", listener);
}
