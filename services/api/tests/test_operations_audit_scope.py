from __future__ import annotations

import importlib
import importlib.util
from dataclasses import replace

import pytest


def scope_module():
    assert importlib.util.find_spec("daon_user_api.operations_audit_scope") is not None,"closed operations scope missing"
    return importlib.import_module("daon_user_api.operations_audit_scope")


def test_workspace_scope_is_closed_and_bound_to_the_actual_consuming_principal():
    module=scope_module()
    scope=module.VerifiedOperationsScope.for_workspace("tenant-a","actor-a","session-a","device-a","workspace-a")
    principal=dict(tenant_id="tenant-a",user_id="actor-a",session_id="session-a",device_id="device-a")
    expected={"version":1,"workspace_id":"workspace-a","scope_basis":"workspace_grant","resource_type":"workspace",
        "resource_id":"workspace-a","notebook_id":None,"source_version_ids":[]}
    assert module.scope_metadata(scope,principal)==expected
    for field in principal:
        assert module.scope_metadata(scope,{**principal,field:"other"}) is None
    assert module.scope_metadata(None,principal) is None
    assert module.scope_metadata({"workspace_id":"workspace-a","token":"PRIVATE"},principal) is None
    for changes in (dict(resource_id="workspace-b"),dict(notebook_id="notebook-a"),dict(source_version_ids=("source-a",)),dict(version=True)):
        with pytest.raises(ValueError):replace(scope,**changes)


def test_resource_scope_bounds_identifiers_sources_and_never_includes_internal_principal_fields():
    module=scope_module()
    scope=module.VerifiedOperationsScope("tenant-a","actor-a","session-a","device-a","workspace-a",
        "resource_access","source","source-a","notebook-a",tuple("v-"+str(i) for i in range(100)))
    metadata=module.scope_metadata(scope,dict(tenant_id="tenant-a",user_id="actor-a",session_id="session-a",device_id="device-a"))
    assert len(metadata["source_version_ids"])==100
    assert set(metadata)=={"version","workspace_id","scope_basis","resource_type","resource_id","notebook_id","source_version_ids"}
    for changes in (dict(source_version_ids=tuple("v-"+str(i) for i in range(101))),dict(source_version_ids=("v-a","v-a")),
            dict(resource_id="x"*129),dict(workspace_id="https://private.example.com"),dict(resource_type="credential"),dict(scope_basis="guessed")):
        with pytest.raises(ValueError):replace(scope,**changes)


def test_egress_correlation_is_stable_logical_event_not_a_secret_or_request_hash():
    module=scope_module()
    assert hasattr(module,"egress_denial_correlation")
    values=("tenant-a","workspace-a","actor-a","run-a")
    value=module.egress_denial_correlation(*values)
    assert value==module.egress_denial_correlation(*values)
    assert value.startswith("egress-denial-") and len(value)==78
    assert all(item not in value for item in values)
    for i in range(4):
        changed=list(values);changed[i]+="-other"
        assert module.egress_denial_correlation(*changed)!=value
    with pytest.raises(ValueError):module.egress_denial_correlation("tenant-a","workspace-a","actor-a","https://private")
