"use client";

import { createContext, useContext, useEffect, useMemo, useState } from "react";
import { getScreenPreferences, resetScreenPreferences, saveScreenPreferences } from "../lib/screen-preference-api.js";
import { applyScreenTheme, cacheScreenTheme, clearCachedScreenTheme, readCachedScreenTheme, watchSystemScreenTheme } from "../lib/screen-theme.js";

const ScreenThemeContext = createContext(null);

export function ScreenThemeRuntime({ children }) {
  const [theme, setTheme] = useState("system");
  const [status, setStatus] = useState("화면 설정을 불러오는 중입니다.");
  const [busy, setBusy] = useState(false);
  useEffect(() => {
    const cached = readCachedScreenTheme(); setTheme(cached); applyScreenTheme(cached);
    let active = true;
    void getScreenPreferences().then(({ theme: saved }) => {
      if (!active) return; setTheme(saved); cacheScreenTheme(saved); applyScreenTheme(saved); setStatus("화면 설정을 불러왔습니다.");
    }).catch(() => { if (active) setStatus("화면 설정을 불러오지 못했습니다. 현재 화면 설정을 유지합니다."); });
    return () => { active = false; };
  }, []);
  useEffect(() => { applyScreenTheme(theme); return watchSystemScreenTheme(theme, () => applyScreenTheme("system")); }, [theme]);
  const value = useMemo(() => ({ theme, status, busy, choose: async (nextTheme) => {
    if (busy || nextTheme === theme) return; const previous = theme;
    setTheme(nextTheme); applyScreenTheme(nextTheme); cacheScreenTheme(nextTheme); setBusy(true); setStatus("화면 설정을 저장하는 중입니다.");
    try { await saveScreenPreferences(nextTheme); setStatus("화면 설정을 저장했습니다."); }
    catch { setTheme(previous); applyScreenTheme(previous); cacheScreenTheme(previous); setStatus("화면 설정을 저장하지 못했습니다. 이전 설정을 유지합니다."); }
    finally { setBusy(false); }
  }, reset: async () => {
    if (busy) return; setBusy(true); setStatus("화면 설정을 초기화하는 중입니다.");
    try { const value = await resetScreenPreferences(); setTheme(value.theme); clearCachedScreenTheme(); applyScreenTheme(value.theme); setStatus("화면 설정만 초기화했습니다. Notebook 데이터는 변경하지 않았습니다."); }
    catch { setStatus("화면 설정을 초기화하지 못했습니다. 현재 설정을 유지합니다."); }
    finally { setBusy(false); }
  }}), [theme, status, busy]);
  return <ScreenThemeContext.Provider value={value}>{children}</ScreenThemeContext.Provider>;
}

export function useScreenThemeRuntime() { return useContext(ScreenThemeContext); }
