const SAFE_ID = /^[A-Za-z0-9][A-Za-z0-9._:-]{0,255}$/;

function requiredId(value) {
  if (typeof value !== "string" || !SAFE_ID.test(value)) {
    throw new Error("QUESTION_INPUT_INVALID");
  }
  return value;
}

async function responseData(response) {
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
  if (!payload?.data || typeof payload.data.answer !== "string") {
    throw new Error("QUESTION_RESPONSE_INVALID");
  }
  return payload.data;
}

export async function askGroundedQuestion(
  workspaceId,
  { sourceId, sourceVersionId, question },
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
      }),
    },
  );
  return responseData(response);
}

export function citationContentUrl(workspaceId, citation) {
  const workspace = requiredId(workspaceId);
  const citationId = requiredId(citation?.citation_id);
  const page = Number(citation?.page);
  if (!Number.isSafeInteger(page) || page < 1) throw new Error("CITATION_INPUT_INVALID");
  return `/bff/api/workspaces/${encodeURIComponent(workspace)}/citations/${encodeURIComponent(citationId)}/content#page=${page}`;
}
