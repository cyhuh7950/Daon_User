import "@daon-user/design-tokens/tokens.css";
import "./globals.css";

export const metadata = {
  title: "Daon 사용자 Workspace Prototype",
  description: "Release 1 Production-bound 적응형 Workspace"
};

export default function RootLayout({ children }) {
  return <html lang="ko"><body>{children}</body></html>;
}
