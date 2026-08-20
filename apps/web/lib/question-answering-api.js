const SAFE_ID = /^[A-Za-z0-9][A-Za-z0-9._:-]{0,255}$/;

function requiredId(value) {
  if (typeof value !== "string" || !SAFE_ID.test(value)) {
    throw new Error("QUESTION_INPUT_INVALID");
  }
  return value;
}

function isRecord(value) {
  return value !== null && typeof value === "object" && !Array.isArray(value);
}

function hasExactKeys(value, keys) {
  if (!isRecord(value)) return false;
  const actual = Object.keys(value).sort();
  const expected = [...keys].sort();
  return actual.length === expected.length && actual.every((key, index) => key === expected[index]);
}

function isSafeId(value) {
  return typeof value === "string" && SAFE_ID.test(value);
}

function isCitationLocator(value) {
  return hasExactKeys(value, ["kind", "value"])
    && typeof value.kind === "string"
    && /^[a-z][a-z0-9_]{0,31}$/u.test(value.kind)
    && typeof value.value === "string"
    && value.value.length >= 1
    && value.value.length <= 255
    && !/[\u0000-\u001f\u007f]/u.test(value.value);
}

function isGroundedCitation(value) {
  return hasExactKeys(value, ["citation_id", "source_id", "source_version_id", "evidence_span_id", "page", "origin", "context_item_id", "locator"])
    && isSafeId(value.citation_id)
    && isSafeId(value.source_id)
    && isSafeId(value.source_version_id)
    && isSafeId(value.evidence_span_id)
    && ["raw_source", "daon_knowledge"].includes(value.origin)
    && isSafeId(value.context_item_id)
    && isCitationLocator(value.locator)
    && Number.isSafeInteger(value.page)
    && value.page >= 1
    && (value.locator.kind !== "page" || value.locator.value === String(value.page));
}

function knowledgeContextBody(value) {
  if (!isRecord(value) || !["raw_only", "daon_priority", "mixed"].includes(value.mode)) {
    throw new Error("QUESTION_INPUT_INVALID");
  }
  const inputResources = Array.isArray(value.resources) ? value.resources : [];
  if (inputResources.length < 1 || inputResources.length > 8) {
    throw new Error("QUESTION_INPUT_INVALID");
  }
  const resources = inputResources.map((item) => {
    if (!isRecord(item) || !/^[a-z][a-z0-9_]{0,63}$/u.test(item.resourceKind || "")) {
      throw new Error("QUESTION_INPUT_INVALID");
    }
    return {
      resource_kind: item.resourceKind,
      resource_id: requiredId(item.resourceId),
      ...(item.versionId ? { version_id: requiredId(item.versionId) } : {}),
    };
  });
  const knowledgeCount = resources.filter((item) => item.resource_kind === "knowledge_package").length;
  const otherCount = resources.length - knowledgeCount;
  const valid = value.mode === "raw_only" && otherCount && !knowledgeCount
    || value.mode === "daon_priority" && knowledgeCount
    || value.mode === "mixed" && otherCount && knowledgeCount;
  if (!valid) throw new Error("QUESTION_INPUT_INVALID");
  return { mode: value.mode, resources };
}

function questionSourceBody({ sourceId, sourceVersionId, knowledgeContext, question }) {
  if (knowledgeContext) {
    if (sourceId || sourceVersionId) throw new Error("QUESTION_INPUT_INVALID");
    return { knowledge_context: knowledgeContextBody(knowledgeContext) };
  }
  if (sourceId || sourceVersionId) {
    return { source_id: requiredId(sourceId), source_version_id: requiredId(sourceVersionId) };
  }
  if (isGeneralConversationIntent(question)) return {};
  throw new Error("QUESTION_INPUT_INVALID");
}

function isGroundedAnswer(value) {
  return hasExactKeys(value, ["run_id", "run_result_id", "answer", "insufficient", "citations"])
    && isSafeId(value.run_id)
    && isSafeId(value.run_result_id)
    && typeof value.answer === "string"
    && value.answer.length >= 1
    && value.answer.length <= 8_000
    && typeof value.insufficient === "boolean"
    && Array.isArray(value.citations)
    && value.citations.length <= 10
    && value.citations.every(isGroundedCitation);
}

async function responseData(response) {
  const contentType = response.headers?.get?.("content-type")?.split(";", 1)[0]?.trim().toLowerCase();
  if (contentType !== "application/json") {
    if (response.status === 504) throw new Error("GATEWAY_TIMEOUT");
    if (!response.ok) throw new Error("UPSTREAM_FAILURE");
    throw new Error("QUESTION_RESPONSE_INVALID");
  }
  let payload;
  try {
    payload = await response.json();
  } catch {
    throw new Error("QUESTION_RESPONSE_INVALID");
  }
  if (!response.ok) {
    const code = payload?.error?.code;
    throw new Error(typeof code === "string" ? code : "QUESTION_FAILED");
  }
  if (
    !hasExactKeys(payload, ["data", "meta"])
    || !isGroundedAnswer(payload.data)
    || !isRecord(payload.meta)
    || !isSafeId(payload.meta.trace_id)
    || !isSafeId(payload.meta.workspace_id)
  ) {
    throw new Error("QUESTION_RESPONSE_INVALID");
  }
  return payload.data;
}

export async function askGroundedQuestion(
  workspaceId,
  { notebookId, sourceId, sourceVersionId, knowledgeContext, question, stepUpAuthorizationId },
  { fetchImpl = fetch, idempotencyKey = crypto.randomUUID() } = {},
) {
  const workspace = requiredId(workspaceId);
  const notebook = requiredId(notebookId);
  const sourceBody = questionSourceBody({ sourceId, sourceVersionId, knowledgeContext, question });
  if (
    typeof question !== "string" || !question.trim()
    || question.length > 2_000 || !SAFE_ID.test(idempotencyKey)
  ) {
    throw new Error("QUESTION_INPUT_INVALID");
  }
  const response = await fetchImpl(
    `/bff/api/workspaces/${encodeURIComponent(workspace)}/questions`,
    {
      method: "POST",
      headers: {
        "Content-Type": "application/json",
        "Idempotency-Key": idempotencyKey,
      },
      body: JSON.stringify({
        notebook_id: notebook, ...sourceBody, question: question.trim(),
        ...(stepUpAuthorizationId ? { step_up_authorization_id: requiredId(stepUpAuthorizationId) } : {}),
      }),
    },
  );
  return responseData(response);
}

export async function authorizeGroundedQuestion(
  workspaceId, { notebookId, sourceId, sourceVersionId, knowledgeContext, question, password },
  { fetchImpl = fetch, idempotencyKey } = {},
) {
  const workspace = requiredId(workspaceId);
  const notebook = requiredId(notebookId);
  if (!SAFE_ID.test(idempotencyKey || "") || typeof password !== "string" || !password) {
    throw new Error("QUESTION_AUTHORIZATION_INPUT_INVALID");
  }
  const response = await fetchImpl(
    `/bff/api/workspaces/${encodeURIComponent(workspace)}/questions/authorization`,
    { method: "POST", credentials: "same-origin", headers: {
      "Content-Type": "application/json", "Idempotency-Key": idempotencyKey,
    }, body: JSON.stringify({
      notebook_id: notebook,
      ...questionSourceBody({ sourceId, sourceVersionId, knowledgeContext, question }),
      question: question.trim(), password,
    }) },
  );
  const payload = await response.json();
  if (!response.ok) throw new Error(payload?.error?.code || "QUESTION_AUTHORIZATION_FAILED");
  if (!isSafeId(payload?.data?.step_up_authorization_id)) throw new Error("QUESTION_AUTHORIZATION_RESPONSE_INVALID");
  return payload.data;
}

export function citationContentUrl(workspaceId, citation, { notebookId } = {}) {
  const workspace = requiredId(workspaceId);
  const citationId = requiredId(citation?.citation_id);
  const notebook = requiredId(notebookId);
  const page = Number(citation?.page);
  if (!Number.isSafeInteger(page) || page < 1) throw new Error("CITATION_INPUT_INVALID");
  const base = `/bff/api/workspaces/${encodeURIComponent(workspace)}/citations/${encodeURIComponent(citationId)}/content?notebook_id=${encodeURIComponent(notebook)}`;
  return citation?.locator?.kind === "page" ? `${base}#page=${page}` : base;
}
import { isGeneralConversationIntent } from "../../../packages/ui/src/conversation-intent.js";
