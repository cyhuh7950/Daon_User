"""명시 Studio 실행 범위와 정상 인증에 결속된 내부 서비스."""
from __future__ import annotations
from dataclasses import dataclass
import hashlib
import re
from .data_canon import canonical_json_bytes
from .studio_workspace import StudioContext,StudioError


def digest(value):
    return hashlib.sha256(canonical_json_bytes({"studio_execution":value})).hexdigest()


def safe_id(value):
    if type(value) is not str or re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._:-]{0,127}",value) is None:
        raise StudioError("STUDIO_INPUT_INVALID")
    return value


@dataclass(frozen=True,slots=True)
class StudioExecutionContext:
    tenant_id:str
    workspace_id:str
    actor_id:str
    trace_id:str
    policy_version:str
    notebook_id:str
    session_id:str
    device_id:str

    def __post_init__(self):
        for name in self.__dataclass_fields__:safe_id(getattr(self,name))

    def scope(self):
        return {name:getattr(self,name) for name in ("tenant_id","workspace_id","actor_id","notebook_id","session_id","device_id")}

    def studio(self):
        return StudioContext(self.tenant_id,self.workspace_id,self.actor_id,self.trace_id,self.policy_version,self.notebook_id)
