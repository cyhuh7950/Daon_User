const GENERAL_CONVERSATION_INTENTS = new Set([
  "안녕", "안녕하세요", "반가워", "반갑습니다", "고마워", "고마워요", "감사합니다",
  "도움말", "daon 사용법 알려줘", "daon 사용법을 알려줘", "다온 사용법 알려줘",
  "다온 사용법을 알려줘", "이 제품 사용법 알려줘", "이 제품 사용법을 알려줘",
]);

export function normalizeConversationIntent(value) {
  if (typeof value !== "string") return "";
  const normalized = value.normalize("NFKC");
  if (normalized !== value) return "";
  return normalized.trim().toLocaleLowerCase("ko-KR")
    .replace(/[.!?。！？]+$/u, "").trim().replace(/\s+/gu, " ");
}

export function isGeneralConversationIntent(value) {
  return GENERAL_CONVERSATION_INTENTS.has(normalizeConversationIntent(value));
}
