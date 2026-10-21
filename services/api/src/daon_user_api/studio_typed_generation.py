"""Studio 유형별 순수 응답 검증. 모델/DB/권한/자격을 만들거나 호출하지 않는다."""
from __future__ import annotations
from dataclasses import dataclass
import json
import re
from .studio_workspace import StudioError,StudioGenerationRequest
from .document_understanding_adapter import _evidence_anchors

GENERATOR_VERSION="studio-selected-evidence-v1"
SUPPORTED_TYPES=frozenset({"evidence_report","compliance_checklist","comparison_table","knowledge_map","business_draft","slides","infographic","flashcards","quiz"})


def _invalid():
    raise StudioError("STUDIO_OUTPUT_UNAVAILABLE",409)


def _exact(value,keys):
    if type(value) is not dict or set(value)!=set(keys):_invalid()


def _text(value,maximum=8000):
    if type(value) is not str or not 1<=len(value.strip())<=maximum or any(ord(c)<32 and c not in "\t\n\r" for c in value):_invalid()
    return value


def _items(value,minimum=1,maximum=32):
    if type(value) is not list or not minimum<=len(value)<=maximum:_invalid()
    return value


def _pairs(items):
    result={}
    for key,value in items:
        if key in result:_invalid()
        result[key]=value
    return result


def _load(raw):
    try:
        if type(raw) is bytes:
            if len(raw)>65536:_invalid()
            raw=raw.decode("utf-8","strict")
        if type(raw) is str:
            if len(raw.encode("utf-8"))>65536:_invalid()
            return json.loads(raw,object_pairs_hook=_pairs,parse_constant=lambda _: _invalid())
        if type(raw) is dict:
            return _load(json.dumps(raw,ensure_ascii=False,allow_nan=False,separators=(",",":")))
    except (ValueError,TypeError,UnicodeError,RecursionError):
        _invalid()
    _invalid()


@dataclass(frozen=True,slots=True)
class TypedStudioResult:
    # Serialize to prevent callers mutating a validated nested dict after admission.
    content_json:str|None
    cited_chunk_ids:tuple[str,...]
    insufficient:bool
    next_actions:tuple[str,...]
    generator_version:str=GENERATOR_VERSION

    @property
    def content(self):
        return None if self.content_json is None else json.loads(self.content_json)


def parse_typed_output(request:StudioGenerationRequest,raw,evidence,*,request_id:str,model_id:str)->TypedStudioResult:
    if type(request) is not StudioGenerationRequest or request.output_type not in SUPPORTED_TYPES:_invalid()
    _text(request_id,128);_text(model_id,255)
    parsed=_load(raw);_exact(parsed,("content","cited_chunk_ids","insufficient","next_actions"))
    if type(parsed["insufficient"]) is not bool:_invalid()
    by_id={}
    for item in evidence:
        if type(item) is not dict:_invalid()
        chunk_id=_text(item.get("chunk_id"),128);text=_text(item.get("text"))
        if chunk_id in by_id or type(item.get("page")) is not int or item["page"]<1:_invalid()
        by_id[chunk_id]=item
    actions=_items(parsed["next_actions"],0,4)
    for action in actions:
        _text(action,120)
        if action not in {"선택한 기간을 확인할 자료를 추가해 주세요.","선택한 기준을 확인할 자료를 추가해 주세요.",
                          "선택한 비교 대상의 자료를 추가해 주세요.","선택한 항목의 수치와 단위를 확인할 자료를 추가해 주세요.",
                          "선택한 관계를 확인할 자료를 추가해 주세요.","선택한 자료를 보완해 주세요.","원문 기준과 기록을 검토해 주세요."}:_invalid()
    cited=_items(parsed["cited_chunk_ids"],0,10)
    if any(type(value) is not str or value not in by_id for value in cited) or len(set(cited))!=len(cited):_invalid()
    if parsed["insufficient"]:
        if parsed["content"] is not None or cited or not actions:_invalid()
        return TypedStudioResult(None,(),True,tuple(actions))
    if not cited:_invalid()
    used=set()
    def refs(value):
        values=_items(value,1,10)
        if any(type(v) is not str or v not in cited for v in values) or len(set(values))!=len(values):_invalid()
        used.update(values);return values
    def quote(text,ids):
        text=_text(text);parts=[by_id[v]["text"].strip() for v in ids]
        if text.strip() not in ("\n".join(parts),"\n\n".join(parts)):_invalid()
        return text
    def heading(text,ids):
        text=_text(text,255)
        if text not in {"요약","본문","결론","핵심 내용","검토","확인 필요"} and not any(text in by_id[v]["text"] for v in ids):_invalid()
        return text
    def question(text,ids):
        text=_text(text,500)
        # Neutral question frames may wrap an exact supplied term, never a free generated claim.
        stem=re.sub(r"^(?:선택 자료의 |선택한 자료의 )", "", text)
        stem=re.sub(r"(?: 관련 내용| 기준)?(?:은|는)?[?？]$", "", stem)
        if stem==text:heading(text,ids)
        else:heading(stem,ids)
        return text
    def action(text,ids):
        text=_text(text,120)
        if text not in {"원문 기준과 기록을 검토해 주세요.","선택한 기준과 근거를 확인해 주세요.","추가 자료를 확인해 주세요."}:
            quote(text,ids)
        return text
    content=parsed["content"];kind=request.output_type
    lineage={"request_id":request_id,"model_id":model_id}
    warnings=tuple(actions)
    if kind=="evidence_report":
        _exact(content,("summary","body","conclusion","evidence"));ids=refs(content["evidence"])
        # Each report part must preserve one or more complete cited source units.
        for key in ("summary","body","conclusion"):
            text=_text(content[key])
            if text.strip() not in {by_id[v]["text"].strip() for v in ids}:
                quote(text,ids)
        if len({content[key].strip() for key in ("summary","body","conclusion")})==1:_invalid()
        content={"format":request.output_format,"status":"generated","summary":content["summary"],"body":content["body"],"conclusion":content["conclusion"],
                 "citations":[[v,by_id[v]["page"],by_id[v]["page"]] for v in ids],"warnings":warnings,"lineage":lineage}
    elif kind=="compliance_checklist":
        _exact(content,("items",));items=[]
        for index,item in enumerate(_items(content["items"]),1):
            _exact(item,("criterion","observation","judgement","action","criterion_chunk_id","observation_chunk_id"))
            criterion=refs([item["criterion_chunk_id"]]);observation=refs([item["observation_chunk_id"]])
            quote(item["criterion"],criterion);quote(item["observation"],observation)
            # RuleSet execution remains held: never turn a name/ID into proven compliance.
            if item["judgement"]!="needs_review":_invalid()
            action(item["action"],criterion+observation)
            if re.search(r"https?://|비밀번호|API.?Key|토큰|정책.*해제",item["action"],re.I):_invalid()
            items.append({"item_id":f"check-{index}","criterion":item["criterion"],"observation":item["observation"],"judgement":"needs_review",
                          "evidence":"; ".join(criterion+observation),"ruleset_id":request.ruleset_version_id or "none","action":item["action"]})
        content={"items":items,"warnings":warnings+("규정과 기록의 최종 판정은 검토가 필요합니다.",),"lineage":lineage}
    elif kind=="comparison_table":
        _exact(content,("rows",));rows=[]
        for item in _items(content["rows"]):
            _exact(item,("key","baseline","current","baseline_chunk_id","current_chunk_id"))
            a=refs([item["baseline_chunk_id"]]);b=refs([item["current_chunk_id"]])
            if a==b:_invalid()
            quote(item["baseline"],a);quote(item["current"],b);heading(item["key"],a+b)
            same=item["baseline"]==item["current"]
            rows.append({"key":item["key"],"baseline":item["baseline"],"current":item["current"],"difference":None if same else [item["baseline"],item["current"]],
                         "state":"same" if same else "changed","evidence":[a[0],b[0]],"baseline_version":a[0],"current_version":b[0]})
        if len({row["key"] for row in rows})!=len(rows):_invalid()
        content={"rows":rows,"warnings":warnings}
    elif kind=="knowledge_map":
        _exact(content,("nodes","edges"));nodes=[];edges=[]
        for index,item in enumerate(_items(content["nodes"],2),1):
            _exact(item,("label","evidence"));ids=refs(item["evidence"]);heading(item["label"],ids)
            nodes.append({"id":f"node-{index}","label":item["label"],"confidence":"verified","evidence":"; ".join(ids)})
        if len({item["label"] for item in nodes})!=len(nodes):_invalid()
        for index,item in enumerate(_items(content["edges"]),1):
            _exact(item,("source_index","target_index","condition","evidence"))
            if any(type(item[k]) is not int or not 0<=item[k]<len(nodes) for k in ("source_index","target_index")) or item["source_index"]==item["target_index"]:_invalid()
            ids=refs(item["evidence"]);quote(item["condition"],ids)
            # A relation cannot cite unrelated text that never mentions either endpoint.
            if not all(nodes[item[k]]["label"] in "\n".join(by_id[v]["text"] for v in ids) for k in ("source_index","target_index")):_invalid()
            left,right=sorted((item["source_index"],item["target_index"]))
            edges.append({"id":f"edge-{index}","source":nodes[left]["id"],"target":nodes[right]["id"],"condition":item["condition"],"direction":"none"})
        if len({(item["source"],item["target"],item["condition"]) for item in edges})!=len(edges):_invalid()
        content={"nodes":nodes,"edges":edges,"warnings":warnings,"relation_mode":"quoted_context"}
    elif kind in {"business_draft","slides"}:
        collection="sections" if kind=="business_draft" else "slides";_exact(content,(collection,));items=[]
        for index,item in enumerate(_items(content[collection]),1):
            _exact(item,("title","body","evidence"));ids=refs(item["evidence"]);heading(item["title"],ids);quote(item["body"],ids)
            items.append({**item,**({"slide_number":index} if kind=="slides" else {})})
        if len({v["body"] for v in items})!=len(items):_invalid()
        content={collection:items,"warnings":warnings,**({"title":request.purpose} if kind=="slides" else {"template_id":request.structure,"review_state":"draft","lineage":lineage})}
    elif kind=="infographic":
        _exact(content,("summary","metrics"));metrics=[]
        for item in _items(content["metrics"]):
            _exact(item,("label","value","evidence"));ids=refs(item["evidence"]);heading(item["label"],ids);value=_text(item["value"],255)
            text="\n".join(by_id[v]["text"] for v in ids)
            if value in by_id or value not in text:_invalid()
            if not _evidence_anchors(value).issubset(_evidence_anchors(text)):_invalid()
            numbers=lambda body:{re.sub(r"\s+", "",token) for token in re.findall(r"(?<![0-9.])[+−-]?\s*[0-9]+(?:[.,][0-9]+)*",body)}
            if not numbers(value).issubset(numbers(text)):_invalid()
            if re.fullmatch(r"[+−-]?[0-9][0-9,.%\s가-힣A-Za-z/]*",value) is None:
                quote(value,ids)
            elif len(ids)!=1 or re.fullmatch(r"\s*"+re.escape(item["label"])+r"\s*[:=]\s*"+re.escape(value)+r"\s*",text) is None:
                _invalid()
            metrics.append(dict(item))
        quote(content["summary"],[v for v in cited if v in used])
        content={"title":request.purpose,"summary":content["summary"],"metrics":metrics,"warnings":warnings}
    elif kind=="flashcards":
        _exact(content,("cards",));cards=[]
        for index,item in enumerate(_items(content["cards"]),1):
            _exact(item,("question","answer","evidence"));ids=refs(item["evidence"]);question(item["question"],ids);quote(item["answer"],ids)
            cards.append({"id":f"card-{index}",**item})
        if len({v["question"] for v in cards})!=len(cards) or len({v["answer"] for v in cards})!=len(cards):_invalid()
        content={"title":request.purpose,"cards":cards,"warnings":warnings}
    elif kind=="quiz":
        _exact(content,("questions",));questions=[]
        for index,item in enumerate(_items(content["questions"]),1):
            _exact(item,("question","options","answer_index","explanation","evidence"));ids=refs(item["evidence"]);question(item["question"],ids)
            options=_items(item["options"],2,6);answer=item["answer_index"]
            if type(answer) is not int or not 0<=answer<len(options) or any(type(v) is not str for v in options) or len(set(options))!=len(options):_invalid()
            quote(options[answer],ids);quote(item["explanation"],ids)
            for i,option in enumerate(options):
                if i!=answer and (not _text(option,1000).startswith("교육용 오답:") or len(option)<=7):_invalid()
            questions.append({"id":f"quiz-{index}",**item})
        if len({v["question"] for v in questions})!=len(questions):_invalid()
        content={"title":request.purpose,"questions":questions,"warnings":warnings}
    if set(cited)!=used:_invalid()
    return TypedStudioResult(json.dumps(content,ensure_ascii=False,sort_keys=True,separators=(",",":")),tuple(v for v in cited if v in used),False,tuple(actions))
