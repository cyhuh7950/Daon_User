import { isScreenTheme } from "./screen-theme.js";

function failure(code) {
  const error = new Error(code);
  error.code = code;
  return error;
}

function exactPreference(payload) {
  const value = payload?.data;
  if (!value || Array.isArray(value) || Object.keys(value).length !== 1 || !isScreenTheme(value.theme)) throw failure("SCREEN_PREFERENCE_RESPONSE_INVALID");
  return { theme: value.theme };
}

async function request(method, theme) {
  const options = { method, credentials: "same-origin", headers: {} };
  if (method === "PUT") {
    if (!isScreenTheme(theme)) throw failure("SCREEN_PREFERENCE_INPUT_INVALID");
    options.headers["Content-Type"] = "application/json";
    options.body = JSON.stringify({ theme });
  }
  let response;
  try { response = await fetch("/bff/api/preferences/screen", options); }
  catch { throw failure("SCREEN_PREFERENCE_UNAVAILABLE"); }
  if (!response.ok) throw failure("SCREEN_PREFERENCE_UNAVAILABLE");
  try { return exactPreference(await response.json()); }
  catch (error) { if (error?.code) throw error; throw failure("SCREEN_PREFERENCE_RESPONSE_INVALID"); }
}

export const getScreenPreferences = () => request("GET");
export const saveScreenPreferences = (theme) => request("PUT", theme);
export const resetScreenPreferences = () => request("PUT", "system");
