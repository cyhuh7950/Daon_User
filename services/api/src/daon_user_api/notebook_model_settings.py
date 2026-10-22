"""본인 Notebook의 명시적 모델 선택. 자동 선택이나 실행 권한을 만들지 않는다."""
from __future__ import annotations
from dataclasses import dataclass
import re

_ID=re.compile(r"[A-Za-z0-9][A-Za-z0-9._:-]{0,127}")
_MODEL=re.compile(r"[^\x00-\x20\x7f]{1,255}")
_PROVIDERS=frozenset({"OLLAMA","GROQ","MISTRAL","UPSTAGE","OPENROUTER","OMNIROUTE","EOUL_GATEWAY","CUSTOM"})


class NotebookModelSettingsError(RuntimeError):
    def __init__(self,code,status=422):
        self.code,self.status,self.retryable=code,status,status>=500
        super().__init__(code)


def safe_id(value):
    if type(value) is not str or _ID.fullmatch(value) is None:
        raise NotebookModelSettingsError("NOTEBOOK_MODEL_SETTINGS_INVALID")
    return value


@dataclass(frozen=True,slots=True)
class NotebookModelSelection:
    connection_id: str
    model_id: str
    reasoning_effort: str|None=None
    def __post_init__(self):
        safe_id(self.connection_id)
        if (type(self.model_id) is not str or _MODEL.fullmatch(self.model_id) is None
                or self.reasoning_effort is not None and type(self.reasoning_effort) is not str):
            raise NotebookModelSettingsError("NOTEBOOK_MODEL_SETTINGS_INVALID")
    def as_dict(self):
        self.__post_init__()
        return {"connection_id":self.connection_id,"model_id":self.model_id,"reasoning_effort":self.reasoning_effort}


def reasoning_profile(provider_code,adapter_type,model_id):
    # 직접 Upstage Chat 공식 지원과 현재 비용 wire 검증 허용값의 교집합.
    if provider_code=="UPSTAGE" and adapter_type in ("UPSTAGE","openai_compatible","") and model_id == "solar-pro4":
        return {"status":"supported","values":[{"value":v,"label":label} for v,label in
            (("none","사용 안 함"),("low","낮음"),("medium","보통"),("high","높음"))],"profile_version":"upstage-solar-pro4-safe-wire-v1"}
    if provider_code=="UPSTAGE" and model_id=="solar-mini4":
        return {"status":"unsupported","values":[],"profile_version":None}
    return {"status":"unknown","values":[],"profile_version":None}


def validate_effort(provider_code,adapter_type,model_id,effort):
    profile=reasoning_profile(provider_code,adapter_type,model_id)
    if effort is not None and (type(effort) is not str or effort not in [v["value"] for v in profile["values"]]):
        raise NotebookModelSettingsError("NOTEBOOK_MODEL_SETTINGS_INVALID")
    return profile["profile_version"]


def project_candidate(row):
    # SQL 소유자는 현재 allowed/ready/public 또는 본인 verified 자격을 먼저 검사한다.
    if type(row) not in (tuple,list) or len(row)!=8:
        raise NotebookModelSettingsError("NOTEBOOK_MODEL_SETTINGS_UNAVAILABLE",503)
    connection,provider,label,model,connection_version,catalog_version,adapter,capabilities=row
    if provider not in _PROVIDERS or "text_generation" not in (capabilities or ()):
        return None
    if provider=="CUSTOM" and adapter not in ("","CUSTOM","openai_compatible","anthropic_compatible"):
        return None
    NotebookModelSelection(connection,model)
    if (type(label) is not str or not 1<=len(label)<=255 or
            any(type(v) is not int or v<1 for v in (connection_version,catalog_version))):
        raise NotebookModelSettingsError("NOTEBOOK_MODEL_SETTINGS_UNAVAILABLE",503)
    return {"connection_id":connection,"provider_code":provider,"display_name":label,"model_id":model,
        "connection_version":connection_version,"catalog_version":catalog_version,
        "reasoning":reasoning_profile(provider,adapter,model)}


@dataclass(frozen=True,slots=True)
class NotebookModelExecution:
    selection: NotebookModelSelection
    version: int
    profile_version: str|None
    def __post_init__(self):
        if type(self.selection) is not NotebookModelSelection or type(self.version) is not int or self.version<1:
            raise NotebookModelSettingsError("NOTEBOOK_MODEL_SETTINGS_INVALID")
        self.selection.__post_init__()
        if self.profile_version is not None: safe_id(self.profile_version)
    def as_dict(self):
        self.__post_init__()
        return {**self.selection.as_dict(),"model_settings_version":self.version,"reasoning_profile_version":self.profile_version}
