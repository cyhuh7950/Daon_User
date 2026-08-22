import "@daon-user/design-tokens/tokens.css";
import "./globals.css";
import { ScreenThemeRuntime } from "../components/screen-theme-runtime.jsx";

export const metadata = {
  title: "Daon 사용자 Workspace",
  description: "Release 1 실행형 Web Shell"
};

const screenThemeEarlyPaint = `(()=>{try{const key="daon.screen-preference.v1";const value=localStorage.getItem(key);const preference=value==="light"||value==="dark"||value==="system"?value:"system";const dark=matchMedia("(prefers-color-scheme: dark)").matches;const theme=preference==="system"?(dark?"dark":"light"):preference;document.documentElement.setAttribute("data-theme",theme);document.documentElement.style.colorScheme=theme}catch(_){document.documentElement.setAttribute("data-theme","light");document.documentElement.style.colorScheme="light"}})();`;

export default function RootLayout({ children }) {
  return <html lang="ko"><body><script dangerouslySetInnerHTML={{ __html: screenThemeEarlyPaint }} /><ScreenThemeRuntime>{children}</ScreenThemeRuntime></body></html>;
}
