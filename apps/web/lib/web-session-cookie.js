import { browserSessionCookieName } from "./bff-api-proxy.js";

export function webSessionCookieName() {
  return browserSessionCookieName(process.env.DAON_BFF_PROFILE ?? "production");
}
