"use client";

import { SCREEN_THEMES } from "../lib/screen-theme.js";
import { useScreenThemeRuntime } from "./screen-theme-runtime.jsx";

const LABELS = { system: "시스템 설정", light: "밝게", dark: "어둡게" };

export function ScreenPreferencesPane() {
  const runtime = useScreenThemeRuntime();
  const { theme, status, busy, choose, reset } = runtime ?? { theme: "system", status: "화면 설정을 사용할 수 없습니다.", busy: true, choose: async () => {}, reset: async () => {} };
  return <main className="screen-preference-page" aria-labelledby="screen-preference-title">
    <header><p className="section-kicker">DISPLAY</p><h1 id="screen-preference-title">화면 설정</h1><p>Notebook과 Source·대화·산출물은 변경하지 않습니다.</p></header>
    <p className="screen-preference-status" role="status" aria-live="polite">{status}</p>
    <fieldset disabled={busy}><legend>테마</legend><div className="screen-theme-options">{SCREEN_THEMES.map((value) => <label key={value}><input type="radio" name="screen-theme" value={value} checked={theme === value} onChange={() => { void choose(value); }} />{LABELS[value]}</label>)}</div></fieldset>
    <button className="secondary-button" type="button" onClick={() => { void reset(); }} disabled={busy}>화면 설정 초기화</button>
  </main>;
}
