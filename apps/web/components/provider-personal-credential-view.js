export function projectPersonalCredentialView(connection, userCredential = null) {
  const verified = connection?.enabled === true
    && connection?.routeReady === true
    && userCredential?.verification_status === "verified";
  const needsReentry = userCredential != null
    && userCredential.configured === false
    && userCredential.verification_status === "unverified";
  return {
    label: needsReentry
      ? `비공용 · Key 재입력 필요 · ${verified ? "사용 가능" : "사용 대기"}`
      : `비공용 · 개인 Key 필요 · ${verified ? "사용 가능" : "사용 대기"}`,
    verified,
    ...(needsReentry ? { needsReentry: true } : {}),
  };
}
