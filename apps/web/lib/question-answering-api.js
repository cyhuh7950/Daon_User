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

function isGroundedCitation(value) {
  return hasExactKeys(value, ["citation_id", "source_id", "source_version_id", "evidence_span_id", "page"])
    && isSafeId(value.citation_id)
    && isSafeId(value.source_id)
    && isSafeId(value.source_version_id)
    && isSafeId(value.evidence_span_id)
    && Number.isSafeInteger(value.page)
    && value.page >= 1;
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
  { sourceId, sourceVersionId, question, stepUpAuthorizationId },
  { fetchImpl = fetch, idempotencyKey = crypto.randomUUID() } = {},
) {
  const workspace = requiredId(workspaceId);
  const source = requiredId(sourceId);
  const version = requiredId(sourceVersionId);
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
        source_id: source, source_version_id: version, question: question.trim(),
        ...(stepUpAuthorizationId ? { step_up_authorization_id: requiredId(stepUpAuthorizationId) } : {}),
      }),
    },
  );
  return responseData(response);
}

export async function authorizeGroundedQuestion(
  workspaceId, { sourceId, sourceVersionId, question, password },
  { fetchImpl = fetch, idempotencyKey } = {},
) {
  const workspace = requiredId(workspaceId);
  if (!SAFE_ID.test(idempotencyKey || "") || typeof password !== "string" || !password) {
    throw new Error("QUESTION_AUTHORIZATION_INPUT_INVALID");
  }
  const response = await fetchImpl(
    `/bff/api/workspaces/${encodeURIComponent(workspace)}/questions/authorization`,
    { method: "POST", credentials: "same-origin", headers: {
      "Content-Type": "application/json", "Idempotency-Key": idempotencyKey,
    }, body: JSON.stringify({ source_id: requiredId(sourceId),
      source_version_id: requiredId(sourceVersionId), question: question.trim(), password }) },
  );
  const payload = await response.json();
  if (!response.ok) throw new Error(payload?.error?.code || "QUESTION_AUTHORIZATION_FAILED");
  if (!isSafeId(payload?.data?.step_up_authorization_id)) throw new Error("QUESTION_AUTHORIZATION_RESPONSE_INVALID");
  return payload.data;
}

export function citationContentUrl(workspaceId, citation) {
  const workspace = requiredId(workspaceId);
  const citationId = requiredId(citation?.citation_id);
  const page = Number(citation?.page);
  if (!Number.isSafeInteger(page) || page < 1) throw new Error("CITATION_INPUT_INVALID");
  return `/bff/api/workspaces/${encodeURIComponent(workspace)}/citations/${encodeURIComponent(citationId)}/content#page=${page}`;
}
