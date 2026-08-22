"""Bounded representative Provider generation Gate for Foundation A5.

Run inside the production API environment. The credential and generated content
are consumed in memory and are never printed or persisted.
"""

from daon_user_api.document_index_postgres import IndexedEvidenceChunk
from daon_user_api.document_understanding_adapter import (
    ServerProviderCredentialResolver,
    UrlLibDocumentUnderstandingTransport,
)
from daon_user_api.question_answering import (
    GroundedQuestionRequest,
    OpenAICompatibleTextGenerationAdapter,
    TextModelSelection,
)


credential = ServerProviderCredentialResolver().resolve("UPSTAGE")
assert credential is not None
evidence = (
    IndexedEvidenceChunk(
        "chunk-a5-1",
        "source-a5",
        "source-version-a5",
        1,
        "검증 문구는 DAON-A5-ORANGE-42입니다.",
        "span-a5-1",
        1.0,
    ),
)
request = GroundedQuestionRequest(
    "검증 문구를 근거 그대로 답하고 해당 chunk_id만 인용하세요.",
    evidence,
    "trace-a5-upstage-1",
)
selection = TextModelSelection(
    "UPSTAGE",
    "https://api.upstage.ai/v1",
    "provider-upstage",
    "deployment-upstage-solar-pro4",
    "solar-pro4",
    1,
)
result = OpenAICompatibleTextGenerationAdapter(
    transport=UrlLibDocumentUnderstandingTransport(),
    api_key=credential,
    timeout_seconds=60.0,
).generate(request, selection)
assert result.insufficient is False
assert result.cited_chunk_ids == ("chunk-a5-1",)
assert result.answer
assert all(isinstance(value, int) and value >= 0 for value in result.usage.values())
print("A5_UPSTAGE_GROUNDED_GENERATION_PASS")
print("citation_count=1")
print(f"usage_fields={len(result.usage)}")
