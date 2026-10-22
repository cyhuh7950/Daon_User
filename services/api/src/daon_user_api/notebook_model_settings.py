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


REASONING_CONTRACTS=frozenset({"unknown","unsupported","router_effort_v1"})


def _reasoning_protocol(provider_code,adapter_type):
    # Provider 코드가 아니라 실행 adapter/protocol의 wire 형식을 구분한다.
    if provider_code=="CUSTOM":
        return "chat" if adapter_type in ("","CUSTOM","openai_compatible") else None
    if provider_code in ("OMNIROUTE","OPENROUTER"):return "reasoning_object"
    if provider_code in ("GROQ","MISTRAL","UPSTAGE","EOUL_GATEWAY"):return "chat"
    return None


def reasoning_profile(provider_code,adapter_type,model_id=None,*,reasoning_contract="unknown",connection_version=1):
    if type(reasoning_contract) is not str or reasoning_contract not in REASONING_CONTRACTS or type(connection_version) is not int or connection_version<1:
        raise NotebookModelSettingsError("NOTEBOOK_MODEL_SETTINGS_INVALID")
    if reasoning_contract=="router_effort_v1":
        if _reasoning_protocol(provider_code,adapter_type) is None:
            raise NotebookModelSettingsError("NOTEBOOK_MODEL_SETTINGS_INVALID")
        return {"status":"supported","values":[{"value":v,"label":label} for v,label in
            (("low","낮음"),("medium","보통"),("high","높음"))],"profile_version":f"router-effort-v1-c{connection_version}"}
    return {"status":reasoning_contract,"values":[],"profile_version":None}


def validate_effort(provider_code,adapter_type,model_id,effort,*,reasoning_contract="unknown",connection_version=1):
    profile=reasoning_profile(provider_code,adapter_type,model_id,reasoning_contract=reasoning_contract,connection_version=connection_version)
    return _validate_profile_effort(profile,effort)


def _validate_profile_effort(profile,effort):
    if effort is not None and (type(effort) is not str or effort not in [v["value"] for v in profile["values"]]):
        raise NotebookModelSettingsError("NOTEBOOK_MODEL_SETTINGS_INVALID")
    return None if effort is None else profile["profile_version"]


def reasoning_wire_fields(provider_code,adapter_type,effort,*,reasoning_contract="unknown",connection_version=1):
    _validate_profile_effort(reasoning_profile(provider_code,adapter_type,reasoning_contract=reasoning_contract,connection_version=connection_version),effort)
    if effort is None:return {}
    return {"reasoning":{"effort":effort}} if _reasoning_protocol(provider_code,adapter_type)=="reasoning_object" else {"reasoning_effort":effort}


def project_candidate(row):
    # SQL 소유자는 현재 allowed/ready/public 또는 본인 verified 자격을 먼저 검사한다.
    if type(row) not in (tuple,list) or len(row)!=9:
        raise NotebookModelSettingsError("NOTEBOOK_MODEL_SETTINGS_UNAVAILABLE",503)
    connection,provider,label,model,connection_version,catalog_version,adapter,capabilities,contract=row
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
        "reasoning":reasoning_profile(provider,adapter,model,reasoning_contract=contract,connection_version=connection_version)}


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
