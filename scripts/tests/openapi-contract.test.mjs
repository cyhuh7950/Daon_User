import assert from "node:assert/strict";
import { readFile } from "node:fs/promises";
import path from "node:path";
import test from "node:test";

import {
  REQUIRED_PATHS,
  buildSummary,
  canonicalizeDocument,
  evidenceRelativePathForProfile,
  validateOpenApiDocument
} from "../verify-openapi-contract.mjs";

const root = path.resolve(import.meta.dirname, "../..");
const contractPath = path.join(root, "packages/contracts/openapi/v1/openapi.json");

async function loadContract() {
  return JSON.parse(await readFile(contractPath, "utf8"));
}

test('S44 capacity contracts keep default twelve and require exact independent opt-in branches',async()=>{
  const doc=await loadContract(),s=doc.components.schemas;
  const workspace=doc.paths['/api/v1/workspaces/{id}/operations/status'].get;
  const system=doc.paths['/api/v1/system/operations/status'].get;
  assert.deepEqual(workspace.parameters[3],{name:'include_capacity',in:'query',required:false,schema:{type:'string',enum:['1']}});
  assert.deepEqual(system.parameters,[{name:'include_capacity',in:'query',required:false,schema:{type:'string',enum:['1']}}]);
  assert.equal(s.OperationsStatus.properties.resource_samples.maxItems,12);
  assert.equal(s.SystemOperationsStatus.properties.resource_samples.maxItems,12);
  for(const name of ['OperationsStatusWithCapacity','OperationsStatusWithReadinessCapacity','SystemOperationsStatusWithCapacity']){
    const array=s[name].properties.resource_samples;
    assert.equal(array.minItems,16);assert.equal(array.maxItems,16);assert.equal(array.prefixItems.length,16);assert.equal(array.items,false);
  }
  assert.doesNotThrow(()=>validateOpenApiDocument(doc));
  for(const mutate of [
    d=>d.paths['/api/v1/system/operations/status'].get.parameters[0].schema.enum.push('0'),
    d=>{d.components.schemas.OperationsStatusWithCapacity.properties.resource_samples.maxItems=17;},
    d=>{d.components.schemas.WorkspaceHostCapacitySample.additionalProperties=true;},
    d=>{d.components.schemas.SystemHostCapacitySample.properties.boot_id={type:'string'};},
    d=>{d.components.schemas.SystemHostCapacitySample.properties.unit.enum.push('percent');},
    d=>{d.components.schemas.OperationsStatus.properties.resource_samples.maxItems=16;},
    d=>{d.components.schemas.OperationsStatusWithReadinessCapacity.properties.event_counts={$ref:'#/components/schemas/OperationsStatus/properties/event_counts'};},
  ]){const changed=structuredClone(doc);mutate(changed);assert.throws(()=>validateOpenApiDocument(changed));}
});

test("R4 readiness query and response branch stay exact while default9 remains closed",async()=>{
  const doc=await loadContract(),path="/api/v1/workspaces/{id}/operations/status",op=doc.paths[path].get,s=doc.components.schemas;
  assert.equal(op.parameters.length,4);
  assert.deepEqual(op.parameters[2],{name:"include_readiness",in:"query",required:false,schema:{type:"string",enum:["1"]}});
  assert.equal(s.OperationsStatus.properties.event_counts.maxItems,9);
  assert.equal(s.OperationsStatusWithReadiness.properties.event_counts.maxItems,15);
  assert.deepEqual(op.responses["200"].content["application/json"].schema.oneOf,
    [{$ref:"#/components/schemas/OperationsStatusEnvelope"},{$ref:"#/components/schemas/OperationsStatusReadinessEnvelope"},
      {$ref:'#/components/schemas/OperationsStatusCapacityEnvelope'},{$ref:'#/components/schemas/OperationsStatusReadinessCapacityEnvelope'}]);
  assert.doesNotThrow(()=>validateOpenApiDocument(doc));
  for(const mutate of [
    d=>d.paths[path].get.parameters.pop(),d=>d.paths[path].get.parameters.push({in:"query",name:"actor_id"}),
    d=>d.paths[path].get.parameters[2].schema.enum.push("0"),d=>{d.paths[path].get.parameters[2].required=true;},
    d=>{d.components.schemas.OperationsStatus.properties.event_counts.maxItems=15;},
    d=>{d.components.schemas.OperationsStatusWithReadiness.properties.event_counts.maxItems=16;},
    d=>{d.components.schemas.OperationsStatusWithReadiness.properties.event_counts.prefixItems[9].allOf[1].properties.window_start={type:["string","null"]};},
    d=>{d.components.schemas.WorkspaceReadinessEventCount.additionalProperties=true;},
    d=>{d.components.schemas.OperationsStatusWithReadiness.properties.event_counts.prefixItems[14]["x-window-seconds"]=3600;},
    d=>{d.paths[path].get.responses["200"].content["application/json"].schema={oneOf:[{$ref:"#/components/schemas/OperationsStatusEnvelope"}]};},
    d=>{d.components.schemas.OperationsStatusReadinessEnvelope.properties.data.$ref="#/components/schemas/OperationsStatus";},
    d=>{d.components.schemas.OperationsStatusWithReadiness.properties.event_counts.prefixItems[9].allOf[1].properties.metric_id.const="waiting_role_unknown";},
    d=>{d.components.schemas.WorkspaceReadinessEventCount.required.pop();},
    d=>{d.components.schemas.WorkspaceReadinessEventCount.properties.actor_id={type:"string"};},
    d=>{d.components.schemas.WorkspaceReadinessEventCount.allOf[0].$ref="#/components/schemas/WorkspaceEventCount/allOf/length";},
    d=>{d.components.schemas.WorkspaceReadinessEventCount.allOf[0].$ref="#/components/schemas/WorkspaceEventCount/allOf/00";},
    d=>{d.components.schemas.WorkspaceReadinessEventCount.allOf[0].$ref="#/components/schemas/WorkspaceEventCount/allOf/3";},
    d=>{d.components.schemas.WorkspaceReadinessEventCount.allOf[0].$ref="#/components/schemas/WorkspaceEventCount/__proto__";},
  ]){const changed=structuredClone(doc);mutate(changed);assert.throws(()=>validateOpenApiDocument(changed));}
});

test("R4 Source Mode is optional in the exact request and saved response fields stay paired",async()=>{
  const d=await loadContract(),s=d.components.schemas,p="/api/v1/sources/{id}/processing-runs";
  assert.equal(d.paths[p].post.requestBody.content["application/json"].schema.$ref,"#/components/schemas/SourceReprocessingMutation");
  assert.doesNotThrow(()=>validateOpenApiDocument(d));
  const attrs=s.SourceReprocessingMutation.properties.attributes;
  assert.deepEqual(s.SourceReprocessingMutation.properties.kind,{const:"processing_run"});
  assert.deepEqual(s.SourceReprocessingMutation.properties.step_up_authorization_id,{type:"null"});
  assert.deepEqual(attrs.required,["workspace_id","notebook_id","source_version_id"]);
  assert.deepEqual(attrs.properties.processing_mode,{type:"string",enum:["auto","local_only","pinned"]});
  for(const mutate of [
    d=>{d.paths[p].post.requestBody={$ref:"#/components/requestBodies/ResourceMutation"};},
    d=>{d.components.schemas.SourceReprocessingMutation.properties.attributes.required.push("processing_mode");},
    d=>{d.components.schemas.SourceReprocessingMutation.properties.attributes.properties.processing_mode.enum.push(null);},
    d=>{d.components.schemas.SourceReprocessingMutation.properties.attributes.additionalProperties=true;},
    d=>{d.components.schemas.SourceReprocessingMutation.properties.kind={type:"string"};},
    d=>{d.components.schemas.SourceReprocessingMutation.properties.step_up_authorization_id={type:["string","null"]};},
    d=>{d.components.schemas.SourceReprocessingResult.dependentRequired.processing_mode=[];},
    d=>{d.components.schemas.SourceReprocessingResult.properties.auto_requeue_configured.type="string";},
    d=>{d.paths[p].post.responses["201"]={$ref:"#/components/responses/CreatedResponse"};},
  ]){const changed=structuredClone(d);mutate(changed);assert.throws(()=>validateOpenApiDocument(changed));}
});

test("M2 workspace telemetry contract rejects weakened scope and missing typed groups",async()=>{
  const doc=await loadContract();
  assert.doesNotThrow(()=>validateOpenApiDocument(doc));
  for(const mutate of [
    d=>{d.components.schemas.OperationsStatus.required=d.components.schemas.OperationsStatus.required.filter(k=>k!=="worker_samples");},
    d=>{d.components.schemas.OperationsStatus.properties.resource_samples.maxItems=13;},
    d=>{d.components.schemas.WorkspaceCostSummary.properties.known_amounts.items.properties.amount.type="number";},
    d=>{d.paths["/api/v1/workspaces/{id}/operations/status"].get.parameters[1].schema.enum.push("all");},
    d=>{d.components.schemas.WorkspaceEventCount.additionalProperties=true;},
    d=>{d.components.schemas.WorkspaceResourceSample.properties.value.maximum=1e20;},
  ]){const changed=structuredClone(doc);mutate(changed);assert.throws(()=>validateOpenApiDocument(changed));}
});

test("M2-S exact system path and closed observation schemas reject mutations",async()=>{
  const doc=await loadContract();
  assert.doesNotThrow(()=>validateOpenApiDocument(doc));
  for(const mutate of [d=>delete d.paths["/api/v1/system/operations/status"],
    d=>{d.paths["/api/v1/system/operations/status"].post={};},
    d=>{d.paths["/api/v1/system/operations/status"].get.parameters=[{in:"query",name:"scope"}];},
    d=>{d.components.schemas.SystemOperationsStatus.additionalProperties=true;},
    d=>{d.components.schemas.SystemWorkerSample.properties.worker_id={type:"string"};},
    d=>{d.components.schemas.SystemOperationsStatus.properties.resource_samples.maxItems=13;},
    d=>{d.components.schemas.SystemOperationsStatus.properties.health.prefixItems[0].allOf[1].properties.component_id.const="provider";},
    d=>{d.components.schemas.SystemResourceSample.properties.value.maximum=1e20;},
    d=>{d.components.schemas.SystemWorkerSample.allOf=[];}]){
    const changed=structuredClone(doc);mutate(changed);assert.throws(()=>validateOpenApiDocument(changed));
  }
});

test("R4 closed registered destination Preview and unchanged Execute reject mutations",async()=>{
  const doc=await loadContract(),s=doc.components.schemas;
  assert.ok(doc.paths["/api/v1/backups/{id}/restore-destinations"]?.get);
  assert.equal(s.RestorePreviewRequest.oneOf.length,2);
  assert.deepEqual(s.RegisteredRestorePreviewRequest.required,["workspace_id","destination_id","destination_version","step_up_authorization_id"]);
  assert.equal(s.RegisteredRestorePreview.required.length,10);
  assert.equal(s.RestoreRequestView.properties.preview.oneOf.length,2);
  assert.doesNotThrow(()=>validateOpenApiDocument(doc));
  for(const mutate of [
    d=>delete d.paths["/api/v1/backups/{id}/restore-destinations"],
    d=>d.paths["/api/v1/backups/{id}/restore-destinations"].post=d.paths["/api/v1/backups/{id}/restore-previews"].post,
    d=>d.components.schemas.RegisteredDestinationSummary.required.pop(),
    d=>d.components.schemas.RegisteredDestinationSummary.properties.database_ref={type:"string"},
    d=>d.components.schemas.RegisteredRestorePreviewRequest.additionalProperties=true,
    d=>d.components.schemas.RegisteredRestorePreviewRequest.properties.destination_version.maximum++,
    d=>d.components.schemas.RegisteredRestorePreviewRequest.properties.step_up_authorization_id.maxLength++,
    d=>d.components.schemas.RegisteredRestorePreview.properties.current_retention_watermark={type:"string"},
    d=>d.components.schemas.RegisteredRestorePreview.properties.exclusion_counts.additionalProperties=true,
    d=>d.components.schemas.RestorePreviewRequest.oneOf.pop(),
    d=>d.components.schemas.RestoreRequestView.required.pop(),
    d=>d.components.schemas.RestoreExecuteRequest.properties.preview_version.minimum=0,
    d=>d.components.responses.RestoreResponse.content["application/json"].schema.$ref="#/components/schemas/RecoveryEnvelope",
  ]){const bad=structuredClone(doc);mutate(bad);assert.throws(()=>validateOpenApiDocument(bad));}
});

test("R1-C exact lineage preflight and three create branches reject contract mutations",async()=>{
  const doc=await loadContract(),s=doc.components.schemas;
  assert.ok(doc.paths["/api/v1/backups/selection-previews"]?.post);
  assert.equal(s.BackupCreateRequest.oneOf.length,3);
  assert.equal(s.LineageSelectionPreview.required.length,9);
  assert.doesNotThrow(()=>validateOpenApiDocument(doc));
  for(const mutate of [
    d=>delete d.paths["/api/v1/backups/selection-previews"],
    d=>d.components.schemas.LineageSelectionPreviewRequest.additionalProperties=true,
    d=>d.components.schemas.LineageSelectionPreviewRequest.required.pop(),
    d=>d.components.schemas.LineageSelectionPreviewRequest.properties.source_version_ids.uniqueItems=false,
    d=>d.components.schemas.LineageSelectionPreviewRequest.properties.notebook_id.maxLength=256,
    d=>d.components.schemas.LineageBackupCreateRequest.required.pop(),
    d=>d.components.schemas.LineageBackupCreateRequest.properties.unknown={type:"string"},
    d=>d.components.schemas.LineageClosureConsent.properties.include_prior_versions.const=false,
    d=>d.components.schemas.LineageClosureConsent.additionalProperties=true,
    d=>delete d.components.schemas.LineageClosureConsent.properties.closure_fingerprint.pattern,
    d=>d.components.schemas.LineageSelectionPreview.properties.total_bytes.maximum++,
    d=>d.components.schemas.LineageSelectionPreview.properties.raw_path={type:"string"},
    d=>d.components.schemas.LineageSelectionPreview.required.pop(),
    d=>d.components.schemas.BackupCreateRequest.oneOf.pop(),
  ]) {const bad=structuredClone(doc);mutate(bad);assert.throws(()=>validateOpenApiDocument(bad));}
});

test("L3 조직 exact 조회·Workspace·역할·폐기 version·여섯 원 세션 헤더는 계약 변조를 거부한다", async () => {
  const document = await loadContract();
  const invitationPath = "/api/v1/organization/tenants/{tenant_id}/invitations";
  const rolePath = "/api/v1/organization/tenants/{tenant_id}/members/{user_id}/role";
  const revokePath = invitationPath+"/{invitation_id}";
  for (const path of ["/api/v1/organization/admin/tenants", "/api/v1/organization/tenants/{tenant_id}/workspaces", invitationPath]) assert.ok(document.paths[path]?.get, path);
  assert.deepEqual(document.components.schemas.OrganizationRolePatch.properties.role.enum, ["viewer", "editor", "reviewer", "approver"]);
  assert.equal(document.components.schemas.OrganizationInvitation.required.length, 11);
  assert.doesNotThrow(() => validateOpenApiDocument(document));
  const organizationOperations = Object.values(document.paths).flatMap(item => Object.values(item))
    .filter(operation => operation?.["x-implementation-owner"] === "S39-L3");
  assert.equal(organizationOperations.length, 9);
  for (const operation of organizationOperations) {
    const success = Object.values(operation.responses).find(response => response.headers);
    assert.equal(success.headers.ETag.$ref, "#/components/headers/ETag");
    const missing = structuredClone(document);
    const changed = Object.values(missing.paths).flatMap(item => Object.values(item))
      .find(value => value?.operationId === operation.operationId);
    delete Object.values(changed.responses).find(response => response.headers).headers.ETag;
    assert.throws(() => validateOpenApiDocument(missing), /missing ETag/u);
  }
  for (const change of [
    d => { delete d.paths[invitationPath].get; },
    d => { d.components.schemas.OrganizationInvitation.properties.code_digest = { type: "string" }; },
    d => { d.components.schemas.OrganizationInvitation.required.pop(); },
    d => { d.components.schemas.OrganizationRolePatch.properties.role.enum.push("organization_admin"); },
    d => { d.components.schemas.OrganizationRolePatch.required = d.components.schemas.OrganizationRolePatch.required.filter(field => field !== "expected_auth_version"); },
    d => { d.paths[rolePath].patch.parameters = d.paths[rolePath].patch.parameters.filter(p => p.$ref !== "#/components/parameters/OrganizationExpectedSessionId"); },
    d => { d.paths[revokePath].delete.parameters.find(p => p.name === "expected_version").required = false; },
    d => { d.paths["/api/v1/organization/admin/tenants"].post = d.paths[invitationPath].post; },
  ]) {
    const changed = structuredClone(document); change(changed); assert.throws(() => validateOpenApiDocument(changed));
  }
});

test('S24 lifecycle exact routes bodies nullable Version fields and notice security reject contract drift',async()=>{
  const document=await loadContract();
  const paths={
    '/api/v1/workspaces/{id}/studio/approval-policy':['get','patch'],
    '/api/v1/approval-requests/{id}/withdraw':['post'],
    '/api/v1/workspaces/{id}/studio/approval-notices':['get'],
    '/api/v1/workspaces/{id}/studio/approval-notices/{notification_id}':['patch'],
  };
  for(const [p,methods] of Object.entries(paths)) {
    assert.deepEqual(Object.keys(document.paths[p]??{}),methods);
    assert.ok(REQUIRED_PATHS.includes(p));
  }
  const fields=document.components.schemas.StudioVersionProjection;
  assert.deepEqual(fields.properties.approval_request_state,{type:['string','null'],enum:['pending','approved','rejected','expired','withdrawn',null]});
  assert.deepEqual(fields.properties.approval_expires_at,{type:['string','null'],format:'date-time'});
  assert.equal(fields.required.length,17);
  assert.doesNotThrow(()=>validateOpenApiDocument(document));
  const corruptions=[
    d=>{delete d.paths[Object.keys(paths)[0]].patch;},
    d=>{d.paths[Object.keys(paths)[1]].get=d.paths[Object.keys(paths)[1]].post;},
    d=>{d.components.schemas.StudioApprovalPolicyRequest.additionalProperties=true;},
    d=>{d.components.schemas.StudioApprovalPolicyRequest.properties.ttl_days.maximum=31;},
    d=>{d.components.schemas.StudioApprovalWithdrawRequest.required.pop();},
    d=>{d.paths[Object.keys(paths)[3]].patch.parameters=d.paths[Object.keys(paths)[3]].patch.parameters.filter(p=>p.$ref!=='#/components/parameters/IfMatch');},
    d=>{d.components.schemas.StudioApprovalNotice.properties.recipient_id={type:'string'};},
    d=>{d.components.schemas.StudioVersionProjection.required.pop();},
  ];
  for(const change of corruptions){const broken=structuredClone(document);change(broken);assert.throws(()=>validateOpenApiDocument(broken));}
});

test('S24 Studio export requires both exact proof headers and rejects missing or optional definitions', async () => {
  const document=await loadContract();
  const route=document.paths['/api/v1/studio-outputs/{id}/versions/{version_id}/exports/{format_name}'].get;
  for(const name of ['X-Daon-Step-Up-Authorization','Idempotency-Key']) {
    const header=route.parameters.find(p=>p.in==='header'&&p.name===name);
    assert.equal(header?.required,true,name);
    for(const fault of ['missing','optional','schema']) {
      const copy=structuredClone(document), get=copy.paths['/api/v1/studio-outputs/{id}/versions/{version_id}/exports/{format_name}'].get;
      if(fault==='missing')get.parameters=get.parameters.filter(p=>p.name!==name);
      else if(fault==='optional')get.parameters.find(p=>p.name===name).required=false;
      else get.parameters.find(p=>p.name===name).schema={type:'integer'};
      assert.throws(()=>validateOpenApiDocument(copy),/Studio export proof headers/);
    }
  }
});

test("S16 삭제 확인은 exact Notebook·추가 인증 body와 durable 복원 상태를 요구한다", async () => {
  const document = await loadContract();
  const schema = document.components.schemas.DeletionCreateRequest;
  assert.deepEqual(schema.required, ["notebook_id", "step_up_authorization_id"]);
  assert.equal(schema.additionalProperties, false);
  assert.deepEqual(Object.keys(schema.properties), ["notebook_id", "step_up_authorization_id"]);
  assert.deepEqual(document.components.schemas.NotebookSelectedContext.properties.source_deletion_requests.items.properties.state.enum,
    ["grace_period", "blocked_by_hold", "awaiting_ack", "cleanup_pending", "failed", "purged"]);
  assert.doesNotThrow(() => validateOpenApiDocument(document));
  for (const mutate of [
    d => { d.components.schemas.DeletionCreateRequest.required.pop(); },
    d => { d.components.schemas.DeletionCreateRequest.properties.inventory = { type: "array" }; },
    d => { d.components.schemas.DeletionCreateRequest.properties.step_up_authorization_id.writeOnly = false; },
    d => { d.components.schemas.NotebookSelectedContext.properties.source_deletion_requests.items.properties.state.enum.push("unknown_success"); },
    d => { delete d.paths["/api/v1/sources/{id}/deletion-requests"].post.responses["403"]; },
  ]) {
    const invalid = structuredClone(document); mutate(invalid);
    assert.throws(() => validateOpenApiDocument(invalid));
  }
});

test('open law exact four methods body snapshot17 and selection query reject mutations',async()=>{
  const document=await loadContract(),prefix='/api/v1/workspaces/{id}/connectors/mcp-open-law-go-kr/';
  for(const suffix of ['law-search','law-preview','law-imports'])assert.ok(document.paths[prefix+suffix]);
  assert.doesNotThrow(()=>validateOpenApiDocument(document));
  for(const mutate of [
    d=>{delete d.paths[prefix+'law-search'];},
    d=>{d.paths[prefix+'law-search'].get=d.paths[prefix+'law-search'].post;},
    d=>{d.components.schemas.OpenLawSearchRequest.properties.public_query_confirmed.const=false;},
    d=>{d.components.schemas.OpenLawSnapshotView.properties.text={type:'string'};},
    d=>{d.components.schemas.OpenLawSnapshotView.properties.title.maxLength=301;},
    d=>{d.components.schemas.OpenLawPreviewRequest.properties.article_code.pattern='.*';},
    d=>{d.paths['/api/v1/workspaces/{id}/source-versions/{version_id}/law-snapshot'].get.parameters.push({name:'actor_id',in:'query',schema:{type:'string'}});},
  ]){const invalid=structuredClone(document);mutate(invalid);assert.throws(()=>validateOpenApiDocument(invalid));}
});

test("XLSX original-cell content declares exact nullable cell contract and preserves old media", async () => {
  const doc = await loadContract();
  const response = doc.paths["/api/v1/workspaces/{id}/citations/{citation_id}/content"].get.responses["200"];
  assert.deepEqual(response.content["application/json"].schema.oneOf, [{ $ref: "#/components/schemas/SheetCellContent" }, { $ref: "#/components/schemas/DocxBlockContent" }, { $ref: "#/components/schemas/PptxBlockContent" }, { $ref: "#/components/schemas/AudioSegmentContent" }]);
  for (const name of ["CitationLocator", "StudioCitationLocator"]) {
    assert.ok(doc.components.schemas[name].properties.kind.enum.includes("sheet_cell"));
    assert.ok(doc.components.schemas[name].allOf.some(rule => rule.if?.properties?.kind?.const === 'sheet_cell' && rule.then?.properties?.value?.$ref === '#/components/schemas/SheetCellLocatorValue'));
  }
  const schema = doc.components.schemas.SheetCellContent;
  assert.equal(schema.additionalProperties, false);
  assert.deepEqual(schema.required, ["source_version_id", "source_sha256", "sheet_name", "sheet_visibility", "selected_range", "view_range", "cells", "merged_ranges"]);
  assert.equal(schema.properties.cells.maxItems, 324);
  const cell = doc.components.schemas.SheetCellOriginal;
  assert.equal(cell.additionalProperties, false);
  assert.deepEqual(cell.required, ["address", "value_type", "raw_value", "display_value", "formula", "cached_value", "number_format", "hidden_row", "hidden_column"]);
  for (const key of ["raw_value", "display_value", "formula", "cached_value", "number_format"]) assert.deepEqual(cell.properties[key].type, ["string", "null"]);
  for (const mime of ["application/pdf", "text/plain", "image/png", "image/jpeg"]) assert.ok(response.content[mime]);
  assert.ok(doc.paths["/api/v1/workspaces/{id}/sources"].post.requestBody.content["application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"]);
});

function clone(value) {
  return structuredClone(value);
}

test('웹사이트 승인 두 경로를 정본으로 수용하고 누락·추가 경로는 거부한다',async()=>{
  const document=await loadContract();
  assert.doesNotThrow(()=>validateOpenApiDocument(document));
  for(const apiPath of ['/api/v1/workspaces/{id}/website-imports','/api/v1/workspaces/{id}/source-versions/{version_id}/website-snapshot']){
    const missing=clone(document);delete missing.paths[apiPath];
    assert.throws(()=>validateOpenApiDocument(missing),/required path missing/);
  }
  const extra=clone(document);
  extra.paths['/api/v1/workspaces/{id}/website-original']=clone(document.paths['/api/v1/workspaces/{id}/website-imports']);
  assert.throws(()=>validateOpenApiDocument(extra),/unapproved path/);
});

test('웹사이트 검증기는 method/body/snapshot13/선택 원문query 변조를 fail-close 거부한다',async(t)=>{
  const document=await loadContract();
  const imports='/api/v1/workspaces/{id}/website-imports';
  const snapshot='/api/v1/workspaces/{id}/source-versions/{version_id}/website-snapshot';
  const sources='/api/v1/workspaces/{id}/sources';
  const mutations=[
    ['import GET 변경',d=>{d.paths[imports].get=d.paths[imports].post;delete d.paths[imports].post;}],
    ['import HEAD 추가',d=>d.paths[imports].head=clone(d.paths[imports].post)],
    ['snapshot POST 추가',d=>{d.paths[snapshot].post=clone(d.paths[imports].post);d.paths[snapshot].post.operationId='unexpectedSnapshotPost';}],
    ['body generic 교체',d=>d.paths[imports].post.requestBody.content['application/json'].schema.$ref='#/components/schemas/ResourceMutation'],
    ['body 선택적 변경',d=>d.paths[imports].post.requestBody.required=false],
    ['body actor 추가',d=>d.components.schemas.WebsiteImportRequest.properties.actor_id={type:'string'}],
    ['body 추가키 허용',d=>d.components.schemas.WebsiteImportRequest.additionalProperties=true],
    ['body 확인 false',d=>d.components.schemas.WebsiteImportRequest.properties.public_page_confirmed.const=false],
    ['body URL 한도 변경',d=>d.components.schemas.WebsiteImportRequest.properties.url.maxLength=4096],
    ['import response generic',d=>d.paths[imports].post.responses['201'].content['application/json'].schema.$ref='#/components/schemas/WebsiteSnapshotEnvelope'],
    ['import ready processing',d=>d.components.schemas.WebsiteImportResult.properties.source_state.const='processing'],
    ['import attributes 누락',d=>d.components.schemas.WebsiteImportEnvelope.properties.data.required.pop()],
    ['snapshot response generic',d=>d.paths[snapshot].get.responses['200'].content['application/json'].schema.$ref='#/components/schemas/WebsiteImportEnvelope'],
    ['snapshot text 추가',d=>d.components.schemas.WebsiteSnapshotView.properties.text={type:'string'}],
    ['snapshot required 누락',d=>d.components.schemas.WebsiteSnapshotView.required.pop()],
    ['snapshot 추가키 허용',d=>d.components.schemas.WebsiteSnapshotView.additionalProperties=true],
    ['snapshot title256',d=>d.components.schemas.WebsiteSnapshotView.properties.title.maxLength=256],
    ['snapshot text hash 약화',d=>delete d.components.schemas.WebsiteSnapshotView.properties.text_sha256.pattern],
    ['snapshot notebook query 선택적',d=>d.paths[snapshot].get.parameters.find(p=>p.name==='notebook_id').required=false],
    ['Source 선택query 누락',d=>d.paths[sources].get.parameters=d.paths[sources].get.parameters.filter(p=>p.name!=='website_source_version_id')],
    ['Source 선택query 필수 변경',d=>d.paths[sources].get.parameters.find(p=>p.name==='website_source_version_id').required=true],
    ['Source actor query 추가',d=>d.paths[sources].get.parameters.push({name:'actor_id',in:'query',required:false,schema:{$ref:'#/components/schemas/OpaqueId'}})],
    ['Source 기본 필드 누락',d=>d.components.schemas.WorkspaceSourceProjection.required.pop()],
    ['Source 원문 기본필수 변경',d=>d.components.schemas.WorkspaceSourceProjection.required.push('website')],
    ['Source 원문 snapshot 교체',d=>d.components.schemas.WorkspaceSourceProjection.properties.website.$ref='#/components/schemas/WebsiteSnapshotView'],
    ['Source 원문 1MiB 허용',d=>d.components.schemas.WebsiteSourceOriginal.properties.text.maxLength=1048576],
    ['Source 원문 추가키 허용',d=>d.components.schemas.WebsiteSourceOriginal.additionalProperties=true],
    ['Source 원문 hash 다른field',d=>d.components.schemas.WebsiteSourceOriginal.properties.text_sha256.$ref='#/components/schemas/WebsiteSnapshotView/properties/raw_sha256'],
  ];
  for(const [name,mutate] of mutations)await t.test(name,()=>{
    const invalid=clone(document);mutate(invalid);
    assert.throws(()=>validateOpenApiDocument(invalid),/Website contract/);
  });
});

test("OpenAPI v1 정본은 설계 §17.1 전체 Path와 공통 안전 계약을 통과한다", async () => {
  const document = await loadContract();
  assert.doesNotThrow(() => validateOpenApiDocument(document));
  assert.deepEqual(Object.keys(document.paths).sort(), [...REQUIRED_PATHS].sort());
  const summary = buildSummary(document);
  assert.equal(summary.contract_version, "1.0.0");
  assert.equal(summary.path_count, REQUIRED_PATHS.length);
  assert.match(summary.canonical_sha256, /^[A-F0-9]{64}$/);
  assert.equal(canonicalizeDocument(JSON.parse(JSON.stringify(document))), canonicalizeDocument(document));
});

test("OpenAPI 검증기는 누락 Path를 fail-close 거부한다", async () => {
  const document = clone(await loadContract());
  delete document.paths[REQUIRED_PATHS[0]];
  assert.throws(() => validateOpenApiDocument(document), /required path/i);
});

test("승인된 Android 인증 세 경로는 각각 필수이고 다른 Android 경로는 거부한다", async () => {
  const approved = [
    "/api/v1/session/android/transactions",
    "/api/v1/session/android/authorize",
    "/api/v1/session/android/exchange",
  ];
  for (const apiPath of approved) {
    const missing = clone(await loadContract());
    delete missing.paths[apiPath];
    assert.throws(() => validateOpenApiDocument(missing), new RegExp(`required path missing ${apiPath}`));
  }
  const extra = clone(await loadContract());
  extra.paths["/api/v1/session/android/unknown"] = clone(extra.paths[approved[0]]);
  assert.throws(() => validateOpenApiDocument(extra), /unapproved path \/api\/v1\/session\/android\/unknown/);
});

test("Native 본인 로그아웃은 필수 경로이며 응답 ETag 없이는 거부한다", async () => {
  const apiPath = "/api/v1/session/native/logout";
  const document = await loadContract();
  assert.doesNotThrow(() => validateOpenApiDocument(document));
  const missing = clone(document);
  delete missing.paths[apiPath];
  assert.throws(() => validateOpenApiDocument(missing), /required path missing \/api\/v1\/session\/native\/logout/);
  const noEtag = clone(document);
  delete noEtag.components.responses.NativeSelfLogoutResponse.headers.ETag;
  assert.throws(() => validateOpenApiDocument(noEtag), /logoutCurrentNativeSession response 200 missing ETag/);
});

test("Android 인증 세 POST만 Idempotency-Key 없이 허용하고 일반 POST는 계속 요구한다", async () => {
  const document = await loadContract();
  assert.doesNotThrow(() => validateOpenApiDocument(document));
  const ordinary = clone(document);
  ordinary.paths["/api/v1/workspaces/{id}/questions/authorization"].post.parameters = ordinary.paths[
    "/api/v1/workspaces/{id}/questions/authorization"
  ].post.parameters.filter((parameter) => parameter.$ref !== "#/components/parameters/IdempotencyKey");
  assert.throws(() => validateOpenApiDocument(ordinary), /missing Idempotency-Key/);
});

test("Android 인증 응답은 Trace를 요구하고 일반 성공 응답은 ETag를 계속 요구한다", async () => {
  const document = await loadContract();
  assert.doesNotThrow(() => validateOpenApiDocument(document));
  const noTrace = clone(document);
  delete noTrace.components.responses.AndroidTransactionResponse.headers["X-Trace-Id"];
  assert.throws(() => validateOpenApiDocument(noTrace), /missing X-Trace-Id/);
  const noEtag = clone(document);
  delete noEtag.components.responses.IdentitySessionResponse.headers.ETag;
  assert.throws(() => validateOpenApiDocument(noEtag), /missing ETag/);
});

test("Android 인증 경로는 추가 GET 없이 POST만 허용한다", async () => {
  const document = clone(await loadContract());
  document.paths["/api/v1/session/android/transactions"].get = clone(document.paths["/api/v1/session"].get);
  document.paths["/api/v1/session/android/transactions"].get.operationId = "unexpectedAndroidGet";
  assert.throws(() => validateOpenApiDocument(document), /Android method mismatch/);
});

test("OpenAPI 검증기는 중복 operationId를 fail-close 거부한다", async () => {
  const document = clone(await loadContract());
  const operations = Object.values(document.paths).flatMap((item) => Object.values(item).filter((value) => value?.operationId));
  operations[1].operationId = operations[0].operationId;
  assert.throws(() => validateOpenApiDocument(document), /duplicate operationId/i);
});

test("OpenAPI 검증기는 안전 오류의 내부 필드와 absolute server를 거부한다", async () => {
  const unsafeField = clone(await loadContract());
  unsafeField.components.schemas.SafeError.properties.stack_trace = { type: "string" };
  assert.throws(() => validateOpenApiDocument(unsafeField), /forbidden token/i);

  const absoluteServer = clone(await loadContract());
  absoluteServer.servers = [{ url: "https://internal.invalid/api" }];
  assert.throws(() => validateOpenApiDocument(absoluteServer), /server|absolute url/i);
});

test("Native Refresh 공개 계약은 단일 opaque credential과 Idempotency 예외를 고정한다", async () => {
  const document = clone(await loadContract());
  const post = document.paths["/api/v1/session/refresh"].post;
  assert.deepEqual(post.parameters ?? [], []);
  assert.equal(post.requestBody.content["application/json"].schema.$ref, "#/components/schemas/NativeRefreshRequest");
  assert.equal(post.responses["200"].$ref, "#/components/responses/NativeCredentialSessionResponse");
  const request = document.components.schemas.NativeRefreshRequest;
  assert.deepEqual(request.required, ["refresh_credential"]);
  assert.equal(request.additionalProperties, false);
  assert.equal(request.properties.refresh_credential.writeOnly, true);
  assert.equal(request.properties.refresh_credential.default, undefined);
  assert.equal(request.properties.refresh_credential.example, undefined);
});

test("Knowledge Package 세 경계와 versioned Sync item은 exact 계약이다", async () => {
  const document = await loadContract();
  assert.deepEqual(Object.keys(document.paths["/api/v1/workspaces/{id}/knowledge-packages"]), ["get"]);
  assert.deepEqual(Object.keys(document.paths["/api/v1/workspaces/{id}/operations/status"]), ["get"]);
  assert.deepEqual(Object.keys(document.paths["/api/v1/workspaces/{id}/output-version-settings"]), ["get", "patch"]);
  assert.deepEqual(Object.keys(document.paths["/api/v1/workspaces/{id}/sync-operations"]), ["get", "post"]);
  assert.ok(document.components.schemas.SyncOperation.required.includes("item_ids"));
  const outputSettings = document.components.schemas.OutputVersionSettings;
  assert.deepEqual(outputSettings.required, ["workspace_id", "default_formats", "version_save_mode", "version"]);
  assert.equal(outputSettings.properties.version_save_mode.const, "append_only");
  assert.deepEqual(document.components.schemas.OutputVersionSettingsRequest.required, ["default_formats", "expected_version"]);
  const operationsSchema = document.components.schemas.OperationsStatus;
  assert.deepEqual(operationsSchema.required, ["workspace_id", "overall_status", "checked_at", "components", "observations",
    "worker_samples", "resource_samples", "event_counts", "cost_summary", "deployment_summary", "unimplemented"]);
  assert.deepEqual(operationsSchema.properties.components.items.required, ["component_id", "status", "safe_code", "pending_count", "recovery_action"]);
  assert.deepEqual(Object.keys(document.paths["/api/v1/workspaces/{id}/knowledge-packages/{package_id}/offline-copies"]), ["post"]);
  assert.deepEqual(
    document.components.schemas.KnowledgePackage.required,
    [
      "package_id", "producer", "producer_version", "knowledge_registration_id",
      "output_version_id", "authority", "registration_state", "review_state",
      "digest_sha256", "byte_size", "content_type", "effective_at", "expires_at",
    ],
  );
  assert.equal(document.components.schemas.KnowledgePackage.properties.registration_state.const, "registered");
  assert.equal(document.components.schemas.KnowledgePackage.properties.review_state.const, "approved");
  assert.deepEqual(Object.keys(document.paths["/api/v1/offline-knowledge-copies/{copy_id}/content"]), ["get"]);
  assert.deepEqual(document.components.schemas.SyncItemInput.properties.item_kind.enum, ["source_version", "output_version"]);
  const invalid = clone(document);
  invalid.components.schemas.SyncItemInput.properties.item_kind.default = "output_version";
  assert.throws(() => validateOpenApiDocument(invalid), /versioned Sync item schema mismatch/);
});

test("OpenAPI evidence profile은 보호 baseline과 R1-M8 exact 경로만 허용한다", () => {
  assert.equal(evidenceRelativePathForProfile(), "docs/03_evidence/release_1/R1-M5-07/openapi-contract-summary.json");
  assert.equal(evidenceRelativePathForProfile("r1-m8-10"), "docs/03_evidence/release_1/R1-M8-10-WINDOWS-OFFLINE-STUDIO-01/openapi-contract-summary.json");
  assert.throws(() => evidenceRelativePathForProfile("../../outside"), /unsupported evidence profile/);
});

test("사용자 Studio 보고서 수직 Route는 exact 요청·응답 계약을 고정한다", async () => {
  const document = await loadContract();
  const create = document.paths["/api/v1/workspaces/{id}/studio/reports"].post;
  const list = document.paths["/api/v1/workspaces/{id}/studio/outputs"].get;
  assert.equal(create.requestBody.content["application/json"].schema.$ref, "#/components/schemas/StudioReportCreateRequest");
  assert.equal(create.responses["201"].$ref, "#/components/responses/StudioReportResponse");
  assert.equal(list.responses["200"].$ref, "#/components/responses/StudioOutputListResponse");
  const request = document.components.schemas.StudioReportCreateRequest;
  assert.equal(request.additionalProperties, false);
  assert.deepEqual(request.required, ["notebook_id", "source_id", "source_version_id", "run_id", "run_result_id", "title", "purpose"]);
  assert.equal(list.parameters.some((parameter) => parameter.name === "notebook_id" && parameter.in === "query" && parameter.required === true), true);
  const output = document.components.schemas.StudioOutputProjection;
  assert.equal(output.additionalProperties, false);
  assert.equal(output.properties.output_type.const, "evidence_report");
  assert.equal(output.properties.status.const, "draft");
  assert.equal(document.components.parameters.IdempotencyKey.schema.minLength, 16);
  assert.equal(document.components.parameters.IdempotencyKey.schema.maxLength, 128);
});

test("Product Studio Version 상세는 Citation과 lifecycle 재진입 계약을 공개한다", async () => {
  const document = await loadContract();
  const operation = document.paths["/api/v1/studio-outputs/{id}/versions"].get;
  assert.equal(operation.operationId, "listStudioOutputVersions");
  assert.equal(operation.responses["200"].$ref, "#/components/responses/StudioVersionHistoryResponse");
  const version = document.components.schemas.StudioVersionProjection;
  assert.equal(version.additionalProperties, false);
  assert.deepEqual(version.required, [
    "output_version_id", "content_version", "previous_version_id", "status", "content",
    "revision_type", "change_reason", "settings_snapshot_id", "citations", "review_request_id",
    "approval_request_id", "approval_id", "delivery_id", "knowledge_registration_id", "output_format",
    "approval_request_state", "approval_expires_at",
  ]);
  assert.deepEqual(document.components.schemas.StudioCitationProjection.properties.origin.enum, ["raw_source", "daon_knowledge"]);
  assert.deepEqual(document.components.schemas.StudioCitationLocator.properties.kind.enum, ["page", "section", "image_region", "sheet_cell", "docx_block", "pptx_block", "audio_segment"]);
});

test("Provider 연결 확인은 비밀·Endpoint 없는 안전 상태 계약이다", async () => {
  const document = await loadContract();
  const operation = document.paths["/api/v1/model-profiles/{provider_code}/connection-check"].get;
  assert.equal(operation.requestBody, undefined);
  assert.equal(operation.parameters.find((item) => item.name === "workspace_id").in, "query");
  assert.equal(operation.responses["200"].content["application/json"].schema.$ref, "#/components/schemas/ProviderConnectionStatusEnvelope");
  const status = document.components.schemas.ProviderConnectionStatus;
  assert.equal(status.additionalProperties, false);
  assert.deepEqual(status.required, ["provider_code", "status", "checked_at"]);
  assert.deepEqual(status.properties.status.enum, ["ready"]);
  for (const forbidden of ["credential", "api_key", "secret", "base_url", "endpoint", "response_body"]) {
    assert.equal(status.properties[forbidden], undefined, forbidden);
  }
});

test("시스템 Provider 연결 관리 계약은 admin 권한과 비밀 비노출을 고정한다", async () => {
  const document = await loadContract();
  const paths = [
    "/api/v1/admin/provider-connections",
    "/api/v1/admin/provider-connections/{connection_id}",
    "/api/v1/admin/provider-catalog/{connection_id}/refresh",
    "/api/v1/admin/provider-models/{connection_id}/{model_id}/capabilities",
  ];
  for (const path of paths) assert.ok(document.paths[path], path);
  assert.deepEqual(Object.keys(document.paths[paths[0]]), ["get", "post"]);
  assert.deepEqual(Object.keys(document.paths[paths[1]]), ["put", "delete"]);
  assert.deepEqual(Object.keys(document.paths[paths[2]]), ["post"]);
  assert.deepEqual(Object.keys(document.paths[paths[3]]), ["patch"]);
  for (const operation of [
    document.paths[paths[0]].post, document.paths[paths[1]].put,
    document.paths[paths[1]].delete, document.paths[paths[2]].post,
    document.paths[paths[3]].patch,
  ]) {
    assert.ok(operation.parameters.some((item) => item.$ref === "#/components/parameters/IdempotencyKey"));
  }
  for (const name of [
    "ProviderConnectionCreateRequest", "ProviderConnectionUpdateRequest",
    "ProviderConnectionMutationRequest", "ProviderCapabilityCorrectionRequest",
  ]) {
    const request = document.components.schemas[name];
    assert.ok(request.required.includes("expected_version"));
    assert.equal(request.properties.step_up_authorization_id, undefined);
  }
  assert.equal(document.components.schemas.ProviderConnectionCreateRequest.properties.credential.writeOnly, true);
  assert.equal(document.components.schemas.ProviderConnectionUpdateRequest.properties.credential.writeOnly, true);
  const view = document.components.schemas.ProviderConnectionAdminView;
  for (const forbidden of ["credential", "base_url", "endpoint", "api_key", "secret", "response_body"]) {
    assert.equal(view.properties[forbidden], undefined, forbidden);
  }
  assert.ok(view.required.includes("configured"));
  assert.ok(view.required.includes("credential_version"));
  const credentialDelete = document.paths[paths[1]].delete;
  assert.match(credentialDelete.summary, /Credential 삭제/);
  assert.match(credentialDelete.responses["204"].description, /Credential 삭제/);
});

test("Workspace model defaults와 credential-only rotation은 versioned secret-safe 계약이다", async () => {
  const document = await loadContract();
  const defaults = document.paths["/api/v1/workspaces/{id}/model-defaults"];
  assert.deepEqual(Object.keys(defaults), ["get", "patch"]);
  assert.ok(defaults.patch.parameters.some((item) => item.$ref === "#/components/parameters/IfMatch"));
  assert.ok(defaults.patch.parameters.some((item) => item.$ref === "#/components/parameters/IdempotencyKey"));
  const rotation = document.paths["/api/v1/admin/provider-connections/{connection_id}/credential"].post;
  assert.ok(rotation.parameters.some((item) => item.$ref === "#/components/parameters/IdempotencyKey"));
  const request = document.components.schemas.ProviderCredentialReplaceRequest;
  assert.deepEqual(request.required, ["credential", "expected_version"]);
  assert.equal(request.properties.credential.writeOnly, true);
  assert.equal(request.properties.base_url, undefined);
  const model = document.components.schemas.WorkspaceAvailableModel;
  for (const forbidden of ["credential", "credential_digest", "base_url", "endpoint", "api_key", "secret"]) {
    assert.equal(model.properties[forbidden], undefined, forbidden);
  }
});

test("OpenAPI 검증기는 body-versioned Provider mutation의 expected_version 누락을 거부한다", async () => {
  const document = clone(await loadContract());
  document.components.schemas.ProviderConnectionMutationRequest.required = [];
  assert.throws(() => validateOpenApiDocument(document), /expected_version/i);
});

test("Session Safe Projection은 Recovery 최소 권한만 exact enum 배열로 공개한다", async () => {
  const document = await loadContract();
  assert.equal(
    document.paths["/api/v1/session"].get.responses["503"].$ref,
    "#/components/responses/ServiceUnavailable"
  );
  const session = document.components.schemas.IdentitySession;
  assert.equal(session.additionalProperties, false);
  assert.deepEqual(session.required, [
    "user_id", "tenant_id", "workspace_id", "session_id", "device_id", "client_kind",
    "delivery", "expires_at", "recovery_operations"
  ]);
  assert.deepEqual(session.properties.recovery_operations, {
    type: "array",
    uniqueItems: true,
    items: {
      type: "string",
      enum: [
        "cloud_backup_create",
        "cloud_backup_get",
        "cloud_backup_list",
        "cloud_restore_cancel",
        "cloud_restore_execute",
        "cloud_restore_get",
        "cloud_restore_preview"
      ]
    }
  });
  for (const forbidden of ["role", "role_scope", "effective_permissions", "permission", "access_credential", "refresh_credential"]) {
    assert.equal(session.properties[forbidden], undefined, forbidden);
  }
});

test("OpenAPI 검증기는 Session ServiceUnavailable 503 누락을 거부한다", async () => {
  const document = clone(await loadContract());
  delete document.paths["/api/v1/session"].get.responses["503"];
  assert.throws(() => validateOpenApiDocument(document), /session.*503|503.*session/i);
});

test("OpenAPI 검증기는 Recovery Unknown Operation과 중복 허용을 거부한다", async () => {
  const unknown = clone(await loadContract());
  unknown.components.schemas.IdentitySession.required.push("workspace_id", "recovery_operations");
  unknown.components.schemas.IdentitySession.properties.workspace_id = { $ref: "#/components/schemas/OpaqueId" };
  unknown.components.schemas.IdentitySession.properties.recovery_operations = {
    type: "array", uniqueItems: true, items: { type: "string", enum: [
      "cloud_backup_create", "cloud_backup_get", "cloud_backup_list", "cloud_restore_cancel",
      "cloud_restore_execute", "cloud_restore_get", "cloud_restore_preview", "cloud_restore_admin"
    ] }
  };
  assert.throws(() => validateOpenApiDocument(unknown), /recovery operation/i);

  const duplicated = clone(await loadContract());
  duplicated.components.schemas.IdentitySession.required.push("workspace_id", "recovery_operations");
  duplicated.components.schemas.IdentitySession.properties.workspace_id = { $ref: "#/components/schemas/OpaqueId" };
  duplicated.components.schemas.IdentitySession.properties.recovery_operations = {
    type: "array", uniqueItems: true, items: { type: "string", enum: [
      "cloud_backup_create", "cloud_backup_create", "cloud_backup_get", "cloud_backup_list",
      "cloud_restore_cancel", "cloud_restore_execute", "cloud_restore_get", "cloud_restore_preview"
    ] }
  };
  assert.throws(() => validateOpenApiDocument(duplicated), /recovery operation/i);
});

test("OpenAPI 검증기는 Run Event SSE Content 누락을 거부한다", async () => {
  const document = clone(await loadContract());
  document.components.responses.EventStreamResponse.content = {
    "application/json": { schema: { $ref: "#/components/schemas/SuccessEnvelope" } }
  };
  assert.throws(() => validateOpenApiDocument(document), /text\/event-stream/i);
});

test("Native 로컬 로그인 공개 계약은 credential 비노출 경계와 Windows 고정 발급을 기술한다", async () => {
  const document = await loadContract();
  const operation = document.paths["/api/v1/auth/native/login"].post;
  assert.equal(operation.operationId, "nativeLocalLogin");
  assert.deepEqual(operation.parameters ?? [], []);
  assert.equal(operation.requestBody.content["application/json"].schema.$ref, "#/components/schemas/NativeLocalLoginRequest");
  assert.equal(operation.responses["200"].$ref, "#/components/responses/NativeCredentialSessionResponse");

  const request = document.components.schemas.NativeLocalLoginRequest;
  assert.deepEqual(request.required, ["login_id", "password"]);
  assert.equal(request.additionalProperties, false);
  assert.equal(request.properties.password.writeOnly, true);
  assert.equal(request.properties.password.default, undefined);
  assert.equal(request.properties.password.example, undefined);

  const session = document.components.schemas.NativeCredentialSession;
  assert.deepEqual(session.required, [
    "user_id", "tenant_id", "workspace_id", "session_id", "device_id", "client_kind",
    "delivery", "access_credential", "refresh_credential", "expires_at"
  ]);
  assert.equal(session.properties.client_kind.const, "native");
  assert.equal(session.properties.delivery.const, "native_https_opaque_bearer");
  for (const field of ["access_credential", "refresh_credential"]) {
    assert.equal(session.properties[field].format, undefined);
    assert.equal(session.properties[field].default, undefined);
    assert.equal(session.properties[field].example, undefined);
  }
});

test("OpenAPI 검증기는 승인된 Native 요청 password 한 곳 밖의 secret 명칭을 계속 거부한다", async () => {
  const document = clone(await loadContract());
  document.components.schemas.NativeCredentialSession.properties.password = { type: "string" };
  assert.throws(() => validateOpenApiDocument(document), /forbidden token.*password/i);
});

test("OpenAPI 검증기는 승인된 Native password 속성 내부의 secret 명칭도 거부한다", async () => {
  const document = clone(await loadContract());
  document.components.schemas.NativeLocalLoginRequest.properties.password.description = "password must never be logged";
  assert.throws(() => validateOpenApiDocument(document), /forbidden token.*password/i);
});

test("OpenAPI 검증기는 Native opaque credential의 example과 default를 각각 거부한다", async () => {
  for (const field of ["access_credential", "refresh_credential"]) {
    for (const attribute of ["example", "default"]) {
      const document = clone(await loadContract());
      document.components.schemas.NativeCredentialSession.properties[field][attribute] = "opaque-value";
      assert.throws(() => validateOpenApiDocument(document), /Native credential session/i);
    }
  }
});

test("Audit Event 목록은 generic Resource가 아닌 불변 Hash-chain 계약을 사용한다", async () => {
  const document = await loadContract();
  const operation = document.paths["/api/v1/audit-events"].get;
  assert.equal(operation.responses["200"].$ref, "#/components/responses/AuditEventListResponse");
  const required = document.components.schemas.AuditEvent.required;
  for (const field of [
    "sequence", "actor_id", "trace_id", "policy_version", "before", "after",
    "previous_event_hash", "event_hash"
  ]) assert.ok(required.includes(field), field);
  assert.equal(document.components.schemas.AuditEvent.properties.event_hash.pattern, "^[0-9a-f]{64}$");
  assert.equal(document.components.schemas.AuditEvent.properties.occurred_at.format, "date-time");
  assert.equal(document.components.schemas.AuditEventPage.properties.items.items.$ref, "#/components/schemas/AuditEvent");
});

test("OpenAPI 검증기는 Audit Event generic 응답 회귀를 거부한다", async () => {
  const document = clone(await loadContract());
  document.paths["/api/v1/audit-events"].get.responses["200"].$ref = "#/components/responses/ListSuccessResponse";
  assert.throws(() => validateOpenApiDocument(document), /AuditEventListResponse/i);
});

test("Notification·Inbox 공개 계약은 ACL·Cursor·ETag·멱등성 경계를 고정한다", async () => {
  const document = JSON.parse(await readFile(contractPath, "utf8"));
  const list = document.paths["/api/v1/notifications"].get;
  const detail = document.paths["/api/v1/notifications/{id}"].get;
  const read = document.paths["/api/v1/notifications/{id}"].patch;
  const inbox = document.paths["/api/v1/inbox"].get;
  assert.equal(list.responses["200"].$ref, "#/components/responses/NotificationListResponse");
  assert.equal(detail.responses["200"].$ref, "#/components/responses/NotificationResponse");
  assert.equal(inbox.responses["200"].$ref, "#/components/responses/InboxListResponse");
  const readParameters = new Set(read.parameters.map((item) => item.$ref));
  assert.ok(readParameters.has("#/components/parameters/IfMatch"));
  assert.ok(readParameters.has("#/components/parameters/IdempotencyKey"));
  assert.equal(read.requestBody.content["application/json"].schema.$ref, "#/components/schemas/NotificationReadRequest");
  for (const field of ["recipient_id", "source_event_id", "resource_id", "deep_link", "audit_event_id", "trace_id", "delivery_state", "read_at", "version"]) {
    assert.ok(document.components.schemas.Notification.required.includes(field), field);
  }
  assert.equal(document.components.schemas.Notification.properties.title.description.includes("Plain text"), true);
  assert.equal(document.components.schemas.InboxItem.description.includes("read-only projection"), true);
});

test("Product Studio OpenAPI는 Runtime method·exact body·7 media type 불일치를 거부한다", async () => {
  const document = clone(await loadContract());
  document.paths["/api/v1/reviews"].get = structuredClone(document.paths["/api/v1/studio-outputs"].get);
  assert.throws(() => validateOpenApiDocument(document), /Studio Runtime method mismatch/);
  delete document.paths["/api/v1/reviews"].get;
  const bodyMismatch = clone(await loadContract());
  bodyMismatch.paths["/api/v1/approvals"].post.requestBody.$ref = "#/components/requestBodies/ResourceMutation";
  assert.throws(() => validateOpenApiDocument(bodyMismatch), /Studio Runtime request mismatch/);
  const mediaMismatch = clone(await loadContract());
  delete mediaMismatch.components.responses.StudioExportResponse.content["image/png"];
  assert.throws(() => validateOpenApiDocument(mediaMismatch), /Studio export media types mismatch/);
  for (const [apiPath, response] of [
    ["/api/v1/studio-generation-requests", "#/components/responses/StudioGenerationMutationResponse"],
    ["/api/v1/studio-outputs/{id}/versions", "#/components/responses/StudioVersionMutationResponse"],
    ["/api/v1/reviews", "#/components/responses/StudioActionMutationResponse"],
  ]) {
    assert.equal(document.paths[apiPath].post.responses["201"].$ref, response);
    assert.equal(document.paths[apiPath].post.responses["200"].$ref, response);
  }
  const responseMismatch = structuredClone(document);
  responseMismatch.paths["/api/v1/reviews"].post.responses["201"].$ref = "#/components/responses/CreatedResponse";
  assert.throws(() => validateOpenApiDocument(responseMismatch), /Studio Runtime response mismatch/);
  assert.ok(document.components.schemas.StudioRevisionRequest.properties.settings.$ref.endsWith("/StudioGenerationSettings"));
  assert.ok(document.components.schemas.StudioVersionMutationEnvelope.properties.data.required.includes("content"));
  assert.deepEqual(document.components.schemas.StudioVersionMutationEnvelope.properties.data.properties.content.oneOf, [
    { type: "string" }, { type: "object" },
  ]);
  for (const responseName of ["StudioGenerationMutationResponse", "StudioVersionMutationResponse", "StudioActionMutationResponse"]) {
    assert.equal(document.components.responses[responseName].headers.ETag.$ref, "#/components/headers/ETag");
  }
});

test("Question Citation 계약은 원본 페이지와 Daon 지식 구간을 같은 locator로 표현한다", async () => {
  const document = await loadContract();
  const citation = document.components.schemas.GroundedCitation;
  assert.ok(citation.required.includes("locator"));
  assert.equal(citation.properties.locator.$ref, "#/components/schemas/CitationLocator");
  assert.deepEqual(document.components.schemas.CitationLocator.required, ["kind", "value"]);
  assert.deepEqual(document.components.schemas.CitationLocator.properties.kind.enum, ["page", "section", "image_region", "sheet_cell", "docx_block", "pptx_block", "audio_segment"]);
  const response = document.paths["/api/v1/workspaces/{id}/citations/{citation_id}/content"].get.responses["200"];
  assert.ok(response.content["application/pdf"]);
  assert.ok(response.content["text/plain"]);
  assert.equal(response.headers["X-Citation-Locator-Kind"].schema.enum.join(","), "page,section,image_region,sheet_cell,docx_block,pptx_block,audio_segment");
  assert.ok(response.content['application/json'].schema.oneOf.some(item=>item.$ref.endsWith('/DocxBlockContent')));
  assert.ok(response.content["image/png"]);
  assert.ok(response.content["image/jpeg"]);
  assert.ok(response.headers["X-Citation-Region"].schema.pattern);
  for (const name of ["CitationLocator", "StudioCitationLocator"]) {
    assert.ok(document.components.schemas[name].allOf.some((condition) => condition.if?.properties?.kind?.const === "image_region" && condition.then?.properties?.value?.pattern));
  }
});
