export const SESSION_COOKIE_NAME = process.env.DAON_BFF_PROFILE === "wsl_http_qa"
  ? "daon_session"
  : "__Host-daon_session";
