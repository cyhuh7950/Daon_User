"""선택한 합성 원문에만 결속된 Studio 유형 규칙 시험. Provider/DB 호출 없음."""
from __future__ import annotations
import json
import unittest
from daon_user_api.studio_typed_generation import parse_typed_output
from daon_user_api.studio_workspace import StudioGenerationRequest,StudioError

TEXT1="갑은 계약기간을 2026년 1월부터 12월까지로 정한다."
TEXT2="을은 점검기록을 매월 보관해야 한다."
TEXT3="점검기록: 3건"
TEXT4="갑은 점검기록을 확인한 뒤 을에게 전달한다."
EVIDENCE=tuple({"chunk_id":f"chunk-{i}","text":text,"page":i} for i,text in enumerate((TEXT1,TEXT2,TEXT3,TEXT4),1))

def request(kind):
    fmt={"comparison_table":"pdf","compliance_checklist":"pdf","knowledge_map":"json","slides":"pdf","infographic":"svg","flashcards":"json","quiz":"json"}.get(kind,"pdf")
    return StudioGenerationRequest(kind,None,("source-version-1",),None,None,"선택 자료 정리","검토자",None,"short","summary",fmt,"review_required",source_only=True)

def envelope(content,ids=None):
    if ids is None:
        seen=[]
        def collect(value):
            if type(value) is str and value.startswith("chunk-") and value not in seen:seen.append(value)
            elif isinstance(value,dict):
                for item in value.values():collect(item)
            elif isinstance(value,list):
                for item in value:collect(item)
        collect(content);ids=seen
    return {"content":content,"cited_chunk_ids":list(ids),"insufficient":False,"next_actions":[]}

VALID={
 "evidence_report":{"summary":TEXT1,"body":TEXT2,"conclusion":TEXT4,"evidence":["chunk-1","chunk-2","chunk-4"]},
 "compliance_checklist":{"items":[{"criterion":TEXT2,"observation":TEXT3,"judgement":"needs_review","action":"원문 기준과 기록을 검토해 주세요.","criterion_chunk_id":"chunk-2","observation_chunk_id":"chunk-3"}]},
 "comparison_table":{"rows":[{"key":"계약기간","baseline":TEXT1,"current":TEXT2,"baseline_chunk_id":"chunk-1","current_chunk_id":"chunk-2"}]},
 "knowledge_map":{"nodes":[{"label":"갑","evidence":["chunk-4"]},{"label":"을","evidence":["chunk-4"]}],"edges":[{"source_index":0,"target_index":1,"condition":TEXT4,"evidence":["chunk-4"]}]},
 "business_draft":{"sections":[{"title":"본문","body":TEXT1,"evidence":["chunk-1"]}]},
 "slides":{"slides":[{"title":"계약기간","body":TEXT1,"evidence":["chunk-1"]},{"title":"점검기록","body":TEXT2,"evidence":["chunk-2"]}]},
 "infographic":{"summary":TEXT3,"metrics":[{"label":"점검기록","value":"3건","evidence":["chunk-3"]}]},
 "flashcards":{"cards":[{"question":"계약기간은?","answer":TEXT1,"evidence":["chunk-1"]},{"question":"점검기록 기준은?","answer":TEXT2,"evidence":["chunk-2"]}]},
 "quiz":{"questions":[{"question":"선택 자료의 점검기록 기준은?","options":[TEXT2,"교육용 오답: 점검기록을 즉시 폐기한다."],"answer_index":0,"explanation":TEXT2,"evidence":["chunk-2"]}]},
}

class StudioTypedGenerationTests(unittest.TestCase):
    def parse(self,kind,raw):
        return parse_typed_output(request(kind),raw,EVIDENCE,request_id="generation-qa",model_id="model-qa")
    def test_nine_types_preserve_distinct_real_content_and_server_lineage(self):
        for kind,content in VALID.items():
            with self.subTest(kind=kind):
                result=self.parse(kind,envelope(content))
                self.assertFalse(result.insufficient);self.assertIsNotNone(result.content)
                self.assertNotIn("교육용 오답",json.dumps(result.content,ensure_ascii=False) if kind!="quiz" else "")
                self.assertEqual(result.next_actions,())
    def test_missing_evidence_is_material_request_without_valid_artifact(self):
        result=self.parse("slides",{"content":None,"cited_chunk_ids":[],"insufficient":True,"next_actions":["선택한 기간을 확인할 자료를 추가해 주세요."]})
        self.assertTrue(result.insufficient);self.assertIsNone(result.content)
    def test_forged_reference_number_relation_and_generated_facts_fail_closed(self):
        from copy import deepcopy
        cases=[]
        value=deepcopy(VALID["infographic"]);value["metrics"][0]["value"]="999건";cases.append(("infographic",value))
        value=deepcopy(VALID["slides"]);value["slides"][0]["body"]="계약은 자동으로 2030년까지 갱신된다.";cases.append(("slides",value))
        value=deepcopy(VALID["knowledge_map"]);value["edges"][0]["condition"]="갑이 을을 소유한다.";cases.append(("knowledge_map",value))
        value=deepcopy(VALID["comparison_table"]);value["rows"][0]["baseline_chunk_id"]="unknown";cases.append(("comparison_table",value))
        value=deepcopy(VALID["compliance_checklist"]);value["items"][0]["judgement"]="compliant";cases.append(("compliance_checklist",value))
        for kind,content in cases:
            with self.subTest(kind=kind),self.assertRaises(StudioError):self.parse(kind,envelope(content))
    def test_strict_envelope_rejects_extra_bool_index_duplicate_json_and_legacy_template(self):
        from copy import deepcopy
        raw=envelope(VALID["quiz"]);raw["content"]=deepcopy(raw["content"]);raw["content"]["questions"][0]["answer_index"]=True
        for value in (raw,{**envelope(VALID["slides"]),"password":"not-stored"},
                      '{"content":null,"content":null,"cited_chunk_ids":[],"insufficient":true,"next_actions":["자료"]}'):
            with self.subTest(value_type=type(value).__name__),self.assertRaises(StudioError):self.parse("quiz",value)
        old={"summary":"요약","metrics":[{"label":"근거 1","value":"chunk-1","evidence":["chunk-1"]}]}
        with self.assertRaises(StudioError):self.parse("infographic",envelope(old))

    def test_top_level_references_exactly_equal_item_references(self):
        value=envelope(VALID["slides"]);value["cited_chunk_ids"].append("chunk-4")
        with self.assertRaises(StudioError):self.parse("slides",value)

    def test_numeric_substring_sign_and_date_fragments_are_not_evidence(self):
        for source,value in (("확인한 점검기록은 13건이다.","3건"),("확인한 점검기록은 -3건이다.","3건"),("기록일은 2026-03-01이다.","03")):
            with self.subTest(value=value):
                evidence=({"chunk_id":"chunk-3","text":source,"page":1},)
                raw=envelope({"summary":source,"metrics":[{"label":"점검기록" if "점검기록" in source else "기록일","value":value,"evidence":["chunk-3"]}]})
                with self.assertRaises(StudioError):parse_typed_output(request("infographic"),raw,evidence,request_id="generation-qa",model_id="model-qa")
    def test_hidden_fact_in_titles_questions_or_actions_is_rejected(self):
        from copy import deepcopy
        for kind,collection,field in (("comparison_table","rows","key"),("flashcards","cards","question"),("quiz","questions","question"),("compliance_checklist","items","action")):
            with self.subTest(kind=kind):
                value=deepcopy(VALID[kind]);value[collection][0][field]="근거에 없는 인물이 999억원을 횡령했다."
                with self.assertRaises(StudioError):self.parse(kind,envelope(value))
    def test_repeated_report_nodes_and_relations_are_not_new_content(self):
        from copy import deepcopy
        report={"summary":TEXT1,"body":TEXT1,"conclusion":TEXT1,"evidence":["chunk-1"]}
        with self.subTest(kind="report"),self.assertRaises(StudioError):self.parse("evidence_report",envelope(report))
        for collection in ("nodes","edges"):
            with self.subTest(collection=collection):
                value=deepcopy(VALID["knowledge_map"]);value[collection].append(deepcopy(value[collection][0]))
                with self.assertRaises(StudioError):self.parse("knowledge_map",envelope(value))

    def test_top_level_actions_cannot_smuggle_factual_or_destructive_instructions(self):
        for content,cited,insufficient in ((None,[],True),(VALID["slides"],["chunk-1","chunk-2"],False)):
            with self.subTest(insufficient=insufficient),self.assertRaises(StudioError):
                self.parse("slides",{"content":content,"cited_chunk_ids":cited,"insufficient":insufficient,"next_actions":["계약은 무효이므로 즉시 해지하세요."]})
    def test_numeric_metric_requires_a_complete_single_label_value_source_unit(self):
        for text,label,value in (("서울 지점 매출은 3억원이며 부산 지점 매출은 9억원이다.","서울","9억원"),("점검기록: 3건이 아니다.","점검기록","3건"),("점검기록: 3건. 단 조건을 확인해야 한다.","점검기록","3건")):
            evidence=({"chunk_id":"chunk-3","text":text,"page":1},)
            raw=envelope({"summary":text,"metrics":[{"label":label,"value":value,"evidence":["chunk-3"]}]})
            with self.subTest(text=text),self.assertRaises(StudioError):parse_typed_output(request("infographic"),raw,evidence,request_id="generation-qa",model_id="model-qa")
    def test_graph_direction_is_never_inferred_from_generated_endpoint_order(self):
        from copy import deepcopy
        forward=self.parse("knowledge_map",envelope(VALID["knowledge_map"])).content
        reversed_value=deepcopy(VALID["knowledge_map"]);reversed_value["edges"][0].update(source_index=1,target_index=0)
        backward=self.parse("knowledge_map",envelope(reversed_value)).content
        self.assertEqual(forward["relation_mode"],"quoted_context")
        self.assertEqual(forward["edges"],backward["edges"])
        self.assertTrue(all(edge["direction"]=="none" for edge in forward["edges"]))
