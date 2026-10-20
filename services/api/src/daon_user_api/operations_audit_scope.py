"""실제 기존 인가를 확인한 서버 호출의 내부 관측 값. 이 형식 검사는 인가가 아니다."""
from __future__ import annotations

import json
import hashlib
import logging
import re
from dataclasses import dataclass

_LOG=logging.getLogger(__name__)
_ID=re.compile(r"[A-Za-z0-9][A-Za-z0-9._:-]{0,127}")
_KINDS={"workspace","backup","restore_request","source","notebook","output_version","run"}


def workspace_grant_metadata(workspace_id):
    """형식만 작성한다. 호출자는 기존 현재 인가를 별도로 확인해야 한다."""
    if type(workspace_id) is not str or _ID.fullmatch(workspace_id) is None:raise ValueError("OPERATIONS_SCOPE_INVALID")
    return dict(version=1,workspace_id=workspace_id,scope_basis="workspace_grant",resource_type="workspace",
        resource_id=workspace_id,notebook_id=None,source_version_ids=[])


def egress_denial_correlation(tenant_id,workspace_id,actor_id,run_id):
    values=(tenant_id,workspace_id,actor_id,run_id)
    if any(type(value) is not str or _ID.fullmatch(value) is None for value in values):raise ValueError("OPERATIONS_SCOPE_INVALID")
    wire=json.dumps(["operations-egress-denial-v1",*values],separators=(",",":"),ensure_ascii=True).encode()
    return "egress-denial-"+hashlib.sha256(wire).hexdigest()


@dataclass(frozen=True,slots=True,repr=False)
class VerifiedOperationsScope:
    tenant_id:str
    actor_id:str
    session_id:str
    device_id:str
    workspace_id:str
    scope_basis:str="workspace_grant"
    resource_type:str="workspace"
    resource_id:str=""
    notebook_id:str|None=None
    source_version_ids:tuple[str,...]=()
    version:int=1

    def __post_init__(self):
        def identity(value):return type(value) is str and _ID.fullmatch(value) is not None
        if (type(self.version) is not int or self.version!=1
            or not all(identity(v) for v in (self.tenant_id,self.actor_id,self.session_id,self.device_id,self.workspace_id,self.resource_id))
            or type(self.scope_basis) is not str or self.scope_basis not in {"workspace_grant","resource_access"}
            or type(self.resource_type) is not str or self.resource_type not in _KINDS
            or (self.notebook_id is not None and not identity(self.notebook_id))
            or type(self.source_version_ids) is not tuple or len(self.source_version_ids)>100
            or any(not identity(v) for v in self.source_version_ids) or len(set(self.source_version_ids))!=len(self.source_version_ids)):
            raise ValueError("OPERATIONS_SCOPE_INVALID")
        if self.scope_basis=="workspace_grant":
            if self.resource_type!="workspace" or self.resource_id!=self.workspace_id or self.notebook_id is not None or self.source_version_ids:
                raise ValueError("OPERATIONS_SCOPE_INVALID")
        elif self.resource_type=="workspace" or (self.resource_type in {"source","notebook","output_version","run"} and self.notebook_id is None):
            raise ValueError("OPERATIONS_SCOPE_INVALID")
        if self.resource_type=="notebook" and self.resource_id!=self.notebook_id:
            raise ValueError("OPERATIONS_SCOPE_INVALID")

    @classmethod
    def for_workspace(cls,tenant_id,actor_id,session_id,device_id,workspace_id):
        return cls(tenant_id,actor_id,session_id,device_id,workspace_id,resource_id=workspace_id)

    def _metadata(self):
        return dict(version=1,workspace_id=self.workspace_id,scope_basis=self.scope_basis,resource_type=self.resource_type,
            resource_id=self.resource_id,notebook_id=self.notebook_id,source_version_ids=sorted(self.source_version_ids))


def scope_metadata(scope,principal):
    """타인 토큰 행이 아니라 실제 소비 principal과 결속한다. 관측 오류는 사실을 만들지 않는다."""
    if scope is None:return None
    try:
        if type(scope) is not VerifiedOperationsScope:raise ValueError("OPERATIONS_SCOPE_INVALID")
        scope.__post_init__()
        if (scope.tenant_id,scope.actor_id,scope.session_id,scope.device_id)!=(principal["tenant_id"],principal["user_id"],principal["session_id"],principal["device_id"]):
            raise ValueError("OPERATIONS_SCOPE_INVALID")
        result=scope._metadata()
        if len(json.dumps(result,separators=(",",":"),ensure_ascii=True).encode())>15*1024:
            raise ValueError("OPERATIONS_SCOPE_INVALID")
        # 새 optional 항목만 기존 감사 JSON 계약으로 사전 검사한다.
        # 실제 감사 append 실패를 삼키거나 기록을 재시도하지 않는다.
        from .audit import _validate_projection
        _validate_projection({"operations_scope":result},"metadata")
        return result
    except (ValueError,TypeError,KeyError,IndexError):
        _LOG.warning("OPERATIONS_SCOPE_INVALID")
        return None
