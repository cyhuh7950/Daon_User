const GENERAL_CONVERSATION_INTENTS = new Set([
  "안녕", "안녕하세요", "반가워", "반갑습니다", "고마워", "고마워요", "감사합니다",
  "도움말", "daon 사용법 알려줘", "daon 사용법을 알려줘", "다온 사용법 알려줘",
  "다온 사용법을 알려줘", "이 제품 사용법 알려줘", "이 제품 사용법을 알려줘",
]);

const SOURCE_LOOKUP_PATTERN = /(?:선택한|이|해당)\s*(?:source|문서|자료).*(?:있어|찾아|알려|확인)/iu;
const SOURCE_ACTION_PATTERN = /(?:이\s*자료|선택한\s*(?:source|문서)).*(?:보고서|표|체크리스트|정리|작성|만들)/iu;
const WEB_RESEARCH_PATTERN = /(?:최신|웹|인터넷|검색).*(?:찾아|알려|조사|검색)/iu;

export function classifyConversationIntent(value) {
  const normalized = typeof value === "string" ? value.normalize("NFKC").trim() : "";
  if (!normalized || normalized !== value) return "work_support";
  if (WEB_RESEARCH_PATTERN.test(normalized)) return "approved_web_research";
  if (SOURCE_ACTION_PATTERN.test(normalized)) return "source_backed_action";
  if (SOURCE_LOOKUP_PATTERN.test(normalized)) return "explicit_source_lookup";
  return "work_support";
}

export function buildSourceScopeMismatch({ sourceScopeSummary, mismatch } = {}) {
  if (typeof sourceScopeSummary !== "string" || !sourceScopeSummary.trim()
      || typeof mismatch !== "string" || !mismatch.trim()) {
    throw new Error("CONVERSATION_SCOPE_INVALID");
  }
  return {
    source_scope_summary: sourceScopeSummary.trim(),
    mismatch: mismatch.trim(),
    next_actions: ["다른 Source 선택", "Source 추가", "승인된 웹 조사 요청"],
  };
}

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
