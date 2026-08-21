from __future__ import annotations

import json
import unittest

from daon_user_api.document_index_postgres import IndexedEvidenceChunk
from daon_user_api.provider_settings import (
    ModelDeploymentView,
    ProviderProfileView,
    ProviderSettingsSnapshot,
)
from daon_user_api.question_answering import (
    GroundedQuestionRequest,
    OpenAICompatibleTextGenerationAdapter,
    UpstageTextGenerationAdapter,
    classify_question_intent,
    resolve_text_model_selection,
)


def text_snapshot() -> ProviderSettingsSnapshot:
    return ProviderSettingsSnapshot(
        workspace_id="workspace-cp3",
        profiles=(ProviderProfileView(
            "provider-upstage", "UPSTAGE", "external_api",
            "https://api.upstage.ai/v1", True, True, 2,
        ),),
        deployments=(ModelDeploymentView(
            "deployment-text", "provider-upstage", "UPSTAGE",
            "solar-pro4", ("text",), True, True, 3,
        ),),
        role_bindings={"text": "deployment-text"},
        binding_version=5,
    )


def external_text_snapshot(provider_code: str, base_url: str) -> ProviderSettingsSnapshot:
    return ProviderSettingsSnapshot(
        workspace_id="workspace-cp3",
        profiles=(ProviderProfileView(
            f"provider-{provider_code.lower()}", provider_code, "external_api",
            base_url, True, True, 2,
        ),),
        deployments=(ModelDeploymentView(
            "deployment-text", f"provider-{provider_code.lower()}", provider_code,
            "selected-model", ("text",), True, True, 3,
        ),),
        role_bindings={"text": "deployment-text"}, binding_version=5,
    )


class RecordingTransport:
    def __init__(self) -> None:
        self.calls: list[tuple[str, str, dict[str, object]]] = []

    def post_json(self, *, url: str, api_key: str, payload: dict[str, object], timeout_seconds: float) -> dict[str, object]:
        self.calls.append((url, api_key, payload))
        return {
            "choices": [{"message": {"content": json.dumps({
                "answer": "The phrase is ORANGE-COMPASS-42.",
                "cited_chunk_ids": ["chunk-page-2"],
                "insufficient": False,
            })}}],
            "usage": {"prompt_tokens": 20, "completion_tokens": 8, "total_tokens": 28},
        }


class QuestionAnsweringContractTests(unittest.TestCase):
    def test_work_support_and_source_modes_are_classified_without_refusal(self) -> None:
        self.assertEqual(classify_question_intent("다음 작업을 어떻게 진행할까?"), "work_support")
        self.assertEqual(classify_question_intent("선택한 문서에서 보존 기간을 찾아줘"), "explicit_source_lookup")
        self.assertEqual(classify_question_intent("이 자료로 보고서를 만들어줘"), "source_backed_action")
        self.assertEqual(classify_question_intent("최신 정책을 웹에서 찾아줘"), "approved_web_research")

    def test_groq_mistral_and_upstage_share_grounded_openai_compatible_contract(self) -> None:
        evidence = (IndexedEvidenceChunk(
            "chunk-page-2", "source-cp3", "source-version-cp3", 2,
            "Citation verification phrase: ORANGE-COMPASS-42.", "span-page-2", 1.0,
        ),)
        for provider_code, base_url in (
            ("GROQ", "https://api.groq.com/openai/v1"),
            ("MISTRAL", "https://api.mistral.ai/v1"),
            ("UPSTAGE", "https://api.upstage.ai/v1"),
        ):
            with self.subTest(provider_code=provider_code):
                transport = RecordingTransport()
                selection = resolve_text_model_selection(
                    external_text_snapshot(provider_code, base_url)
                )
                result = OpenAICompatibleTextGenerationAdapter(
                    transport=transport, api_key="server-secret",
                ).generate(
                    GroundedQuestionRequest("What is the phrase?", evidence, "trace-cp3"),
                    selection,
                )
                self.assertEqual(transport.calls[0][0], f"{base_url}/chat/completions")
                self.assertEqual(result.cited_chunk_ids, ("chunk-page-2",))

    def test_openai_compatible_adapter_rejects_unapproved_provider_endpoint_pair(self) -> None:
        selection = resolve_text_model_selection(
            external_text_snapshot("GROQ", "https://api.mistral.ai/v1")
        )
        with self.assertRaisesRegex(ValueError, "TEXT_PROVIDER_ENDPOINT_INVALID"):
            OpenAICompatibleTextGenerationAdapter(
                transport=RecordingTransport(), api_key="server-secret",
            ).generate(
                GroundedQuestionRequest(
                    "phrase?", (IndexedEvidenceChunk(
                        "chunk-page-2", "source-cp3", "source-version-cp3", 2,
                        "ORANGE-COMPASS-42", "span-page-2", 1.0,
                    ),), "trace-cp3",
                ),
                selection,
            )
    def test_text_role_is_frozen_to_selected_solar_pro4_deployment(self) -> None:
        selected = resolve_text_model_selection(text_snapshot())

        self.assertEqual(selected.provider_code, "UPSTAGE")
        self.assertEqual(selected.deployment_id, "deployment-text")
        self.assertEqual(selected.model_id, "solar-pro4")
        self.assertEqual(selected.binding_version, 5)

    def test_upstage_uses_exact_model_and_only_supplied_evidence(self) -> None:
        transport = RecordingTransport()
        adapter = UpstageTextGenerationAdapter(transport=transport, api_key="server-secret")
        evidence = (IndexedEvidenceChunk(
            "chunk-page-2", "source-cp3", "source-version-cp3", 2,
            "Citation verification phrase: ORANGE-COMPASS-42.", "span-page-2", 1.0,
        ),)

        result = adapter.generate(
            GroundedQuestionRequest(
                question="What is the citation verification phrase?",
                evidence=evidence,
                trace_id="trace-cp3",
            ),
            resolve_text_model_selection(text_snapshot()),
        )

        url, _secret, payload = transport.calls[0]
        self.assertEqual(url, "https://api.upstage.ai/v1/chat/completions")
        self.assertEqual(payload["model"], "solar-pro4")
        self.assertNotIn("source-version-cp3", result.answer)
        self.assertEqual(result.cited_chunk_ids, ("chunk-page-2",))
        self.assertEqual(result.answer, "The phrase is ORANGE-COMPASS-42.")

    def test_grounded_prompt_explains_source_scope_and_handles_out_of_scope_without_refusal(self) -> None:
        evidence = (IndexedEvidenceChunk(
            "chunk-page-2", "source-cp3", "source-version-cp3", 2,
            "Daon 보존 정책은 30일입니다.", "span-page-2", 1.0,
        ),)

        payload = UpstageTextGenerationAdapter.provider_payload(
            GroundedQuestionRequest("최신 주식 가격은?", evidence, "trace-cp3"),
            resolve_text_model_selection(text_snapshot()),
        )
        system_prompt = payload["messages"][0]["content"]
        self.assertIn("source scope", system_prompt.lower())
        self.assertIn("outside", system_prompt.lower())
        self.assertIn("web search", system_prompt.lower())
        self.assertIn("approval", system_prompt.lower())

    def test_adapter_rejects_provider_citation_outside_retrieved_chunks(self) -> None:
        transport = RecordingTransport()
        response = transport.post_json

        def fake_response(**kwargs):
            payload = response(**kwargs)
            payload["choices"][0]["message"]["content"] = json.dumps({
                "answer": "Invented", "cited_chunk_ids": ["fake"], "insufficient": False,
            })
            return payload

        transport.post_json = fake_response
        adapter = UpstageTextGenerationAdapter(transport=transport, api_key="server-secret")
        evidence = (IndexedEvidenceChunk(
            "chunk-page-2", "source-cp3", "source-version-cp3", 2,
            "ORANGE-COMPASS-42", "span-page-2", 1.0,
        ),)

        with self.assertRaisesRegex(ValueError, "TEXT_GENERATION_GROUNDING_INVALID"):
            adapter.generate(
                GroundedQuestionRequest("phrase?", evidence, "trace-cp3"),
                resolve_text_model_selection(text_snapshot()),
            )

    def test_adapter_rejects_material_answer_anchor_absent_from_cited_chunk(self) -> None:
        transport = RecordingTransport()

        def fake_response(**kwargs):
            transport.calls.append((kwargs["url"], kwargs["api_key"], kwargs["payload"]))
            return {"choices": [{"message": {"content": json.dumps({
                "answer": "The code is ALPHA-999.",
                "cited_chunk_ids": ["chunk-page-2"], "insufficient": False,
            })}}]}

        transport.post_json = fake_response
        evidence = (IndexedEvidenceChunk(
            "chunk-page-2", "source-cp3", "source-version-cp3", 2,
            "The verified code is ORANGE-COMPASS-42.", "span-page-2", 1.0,
        ),)
        with self.assertRaisesRegex(ValueError, "TEXT_GENERATION_GROUNDING_INVALID"):
            UpstageTextGenerationAdapter(
                transport=transport, api_key="server-secret",
            ).generate(
                GroundedQuestionRequest("What is the code?", evidence, "trace-cp3"),
                resolve_text_model_selection(text_snapshot()),
            )


if __name__ == "__main__":
    unittest.main()
