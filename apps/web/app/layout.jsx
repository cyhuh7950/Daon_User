import "@daon-user/design-tokens/tokens.css";
import { WebShellRuntimeStatus } from "@daon-user/ui";
import "./globals.css";

export const metadata = {
  title: "Daon 사용자 Workspace",
  description: "Release 1 실행형 Web Shell"
};

export default function RootLayout({ children }) {
  return <html lang="ko"><body><WebShellRuntimeStatus />{children}</body></html>;
}
