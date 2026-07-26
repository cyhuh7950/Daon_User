import React from "react";
import { MobileShell } from "./MobileShell.tsx";

export type MobileHostProps = { clientType?: unknown };

export default function App({ clientType }: MobileHostProps) {
  return <MobileShell clientType={clientType} />;
}
