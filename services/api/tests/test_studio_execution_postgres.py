"""실 repository SQL/transaction을 관찰하는 대역. 실제 PostgreSQL 인수 증거 아님."""
from contextlib import contextmanager
from copy import deepcopy
from datetime import datetime,timezone,timedelta
import unittest
from unittest.mock import patch
from daon_user_api.studio_execution import StudioExecutionContext
from daon_user_api.studio_execution_postgres import PostgresStudioExecutionRepository
from daon_user_api.studio_workspace import StudioError

NOW=datetime(2026,10,22,tzinfo=timezone.utc)
CTX=StudioExecutionContext("tenant-qa","workspace-qa","actor-qa","trace-qa","policy-qa","notebook-qa","session-qa","device-qa")
META={"operation":"generate","output_id":None,"previous_version_id":None,"selection":{"connection_id":"connection-qa","model_id":"model-qa","provider_code":"CUSTOM","provider_kind":"external_api","adapter_type":"openai_compatible","connection_version":1,"catalog_version":1,"credential_version":0,"reasoning_effort":None,"model_settings_version":1,"reasoning_profile_version":None,"endpoint_digest":"a"*64},"policy_fingerprint":"b"*64,"payload_digest":"c"*64,"evidence_digest":"d"*64,"sources":[{"source_id":"source-qa","source_version_id":"version-qa","source_digest":"e"*64,"index_version_id":"index-qa","index_digest":"f"*64}]}
REQUEST={"output_type":"slides","source_id":None,"source_version_ids":["version-qa"],"run_id":None,"run_result_id":None,"purpose":"자료 정리","audience":"독자","ruleset_version_id":None,"length":"short","structure":"summary","output_format":"pdf","review_condition":"review_required","language":"ko","source_only":True,"intended_model_selection":{"connection_id":"connection-qa","model_id":"model-qa","reasoning_effort":None,"model_settings_version":1,"reasoning_profile_version":None}}
META["revision_type"]="initial"
META["change_reason"]=None
class Result:
 def __init__(self,rows=()):self.rows=rows
 def fetchone(self):return self.rows[0] if self.rows else None
 def fetchall(self):return self.rows
class Store:
 def __init__(self):self.rows={};self.jobs={};self.job_versions={};self.canons={};self.costs={};self.sql=[];self.active=False;self.fail_admission=False;self.fail_cost=False
 @contextmanager
 def _transaction(self,context):
  old=deepcopy((self.rows,self.jobs,self.canons,self.costs));self.active=True
  try:yield self
  except Exception:self.rows,self.jobs,self.canons,self.costs=old;raise
  finally:self.active=False
 @contextmanager
 def transaction(self):
  old=deepcopy((self.rows,self.jobs,self.canons,self.costs))
  try:yield self
  except Exception:self.rows,self.jobs,self.canons,self.costs=old;raise
 def execute(self,sql,params=()):
  self.sql.append((sql,params))
  if "record_operations_cost_receipt" in sql:
   if self.fail_cost:raise RuntimeError("INJECTED_COST_FAILURE")
   self.costs[params[4]]=params;return Result([(params[-1],)])
  if "studio-execution:run-state" in sql:
   row=self.canons.get(("runs",params[2]));return Result([(row["state"],row["version"])] if row else [])
  if "studio-execution:job-finish" in sql:
   row=self.jobs.get(params[5])
   if row is None:return Result()
   self.jobs[params[5]]=(row[0],params[0],row[2]);return Result([(params[5],)])
  if "studio-execution:plan-read" in sql:
   row=self.rows.get(params[2]);return Result([row] if row else [])
  if "studio-execution:plan-insert" in sql:
   if params[2] in self.rows:return Result()
   value=params[4].obj
   self.rows[params[2]]=(value,params[5],params[6],params[7]);return Result([(params[2],)])
  if "studio-execution:job-replay-read" in sql:
   row=self.jobs.get(params[2]);return Result([(*row,*getattr(self,"job_outputs",{}).get(params[2],(None,None)))] if row else [])
  if "studio-execution:job-read" in sql:
   row=self.jobs.get(params[2]);return Result([row] if row else [])
  if "studio-execution:canon-read" in sql:
   import re
   table=re.search(r"FROM ([a-z_]+)",sql).group(1)
   row=self.canons.get((table,params[2]))
   columns=sql.split(" */ ")[1].split(" FROM ")[0].split(",")
   return Result([tuple(row[c] for c in columns)] if row else [])
  if "studio-execution:canon-insert" in sql:
   import re
   table=re.search(r"INTO ([a-z_]+)",sql).group(1)
   columns=sql.split("(",1)[1].split(")",1)[0].split(",")
   row={c:getattr(v,"obj",v) for c,v in zip(columns,params)}
   identity=(table,row["record_id"])
   if identity in self.canons:return Result()
   self.canons[identity]=row;return Result([(row["record_id"],)])
  if "transition_canon_state" in sql:
   row=self.canons[("runs",params[1])];row["version"]+=1;row["state"]=params[3]
   return Result([(params[3],row["version"],"succeeded",None)])
  if "studio-execution:job-claim-read" in sql:
   row=self.jobs.get(params[2]);return Result([(*row,self.job_versions.get(params[2],1))] if row else [])
  if "studio-execution:job-insert" in sql:
   if self.fail_admission:raise RuntimeError("INJECTED_WRITE_FAILURE")
   self.jobs[params[2]]=(params[3].obj,"queued",params[4]);return Result([(params[2],)])
  return Result()

class StudioExecutionRepositoryTests(unittest.TestCase):
 def setUp(self):
  self.store=Store();self.repo=PostgresStudioExecutionRepository(self.store,clock=lambda:NOW)
  self.guard=patch.object(self.repo,"_current",return_value=None);self.guard.start();self.addCleanup(self.guard.stop)
 def test_immutable_plan_exact_replay_conflict_expiry_and_secret_projection(self):
  first=self.repo.prepare(CTX,REQUEST,"studio-request-key-1",META)
  same=self.repo.prepare(CTX,REQUEST,"studio-request-key-1",META)
  self.assertEqual(first,same);self.assertEqual(len(self.store.rows),1)
  self.assertEqual(first["expires_at"],(NOW+timedelta(seconds=300)).isoformat())
  with self.assertRaises(StudioError):self.repo.prepare(CTX,{**REQUEST,"purpose":"다른 요청"},"studio-request-key-1",META)
  with self.assertRaises(StudioError):self.repo.prepare(CTX,REQUEST,"studio-request-key-1",{**META,"raw_payload":"must not store"})
  self.repo.clock=lambda:NOW+timedelta(seconds=301)
  with self.assertRaises(StudioError):self.repo.prepare(CTX,REQUEST,"studio-request-key-1",META)
 def test_completed_regeneration_exact_replay_checks_its_own_latest_version_without_new_admission(self):
  metadata={**META,"operation":"regenerate","revision_type":"settings_change","change_reason":"검토 범위 조정","output_id":"output-qa","previous_version_id":"previous-qa"}
  plan=self.repo.prepare(CTX,REQUEST,"studio-regen-key-1",metadata)
  job,_=self.repo.enqueue(CTX,plan,"studio-regen-key-1",authorization_mode="policy_approved")
  payload,_,actor=self.store.jobs[job["job_id"]];self.store.jobs[job["job_id"]]=(payload,"completed",actor)
  self.store.job_outputs={job["job_id"]:("output-qa","own-new-version-qa")}
  self.repo.clock=lambda:NOW+timedelta(seconds=301)
  with patch.object(self.repo,"_current") as current:
   restored,result=self.repo.read_replay(CTX,"studio-regen-key-1",request=REQUEST,revision_type="settings_change",change_reason="검토 범위 조정",output_id="output-qa",previous_version_id="previous-qa")
  self.assertEqual(result["output_version_id"],"own-new-version-qa")
  self.assertEqual(current.call_args.args[3]["previous_version_id"],"own-new-version-qa")
  self.assertEqual(restored["metadata"]["previous_version_id"],"previous-qa")
  self.assertEqual(len(self.store.jobs),1)
  for change in ({"request":{**REQUEST,"purpose":"다른 입력"}},{"change_reason":"다른 사유"}):
   options={"request":REQUEST,"revision_type":"settings_change","change_reason":"검토 범위 조정","output_id":"output-qa","previous_version_id":"previous-qa",**change}
   with self.assertRaises(StudioError):self.repo.read_replay(CTX,"studio-regen-key-1",**options)

 def test_cloud_failure_rolls_back_admission_job_and_same_binding_can_recover(self):
  plan=self.repo.prepare(CTX,REQUEST,"studio-request-key-1",META)
  self.store.fail_admission=True
  with self.assertRaises(RuntimeError):self.repo.enqueue(CTX,plan,"studio-request-key-1",authorization_mode="policy_approved")
  self.assertEqual(len(self.store.rows),1);self.assertEqual(self.store.jobs,{})
  self.store.fail_admission=False
  job,replay=self.repo.enqueue(CTX,plan,"studio-request-key-1",authorization_mode="policy_approved")
  self.assertFalse(replay);self.assertEqual(job["status"],"queued")
  _,replay=self.repo.enqueue(CTX,plan,"studio-request-key-1",authorization_mode="policy_approved")
  self.assertTrue(replay);self.assertEqual(len(self.store.jobs),1)
  with patch.object(self.repo,"_current",side_effect=StudioError("RESOURCE_UNAVAILABLE",404)):
   with self.assertRaises(StudioError):self.repo.enqueue(CTX,plan,"studio-request-key-1",authorization_mode="policy_approved")

 def test_same_logical_key_cannot_create_new_job_after_session_or_notebook_change(self):
  from dataclasses import replace
  self.repo.prepare(CTX,REQUEST,"studio-request-key-1",META)
  for changed in (replace(CTX,session_id="session-new"),replace(CTX,notebook_id="notebook-new")):
   with self.subTest(session=changed.session_id,notebook=changed.notebook_id),self.assertRaises(StudioError):
    self.repo.prepare(changed,REQUEST,"studio-request-key-1",META)
  self.assertEqual(len(self.store.rows),1)

 def test_provider_start_is_committed_once_and_immutable_attempt_is_not_inserted_early(self):
  from types import SimpleNamespace
  plan=self.repo.prepare(CTX,REQUEST,"studio-request-key-1",META)
  job,_=self.repo.enqueue(CTX,plan,"studio-request-key-1",authorization_mode="policy_approved")
  row=self.store.jobs[job["job_id"]];self.store.jobs[job["job_id"]]=(row[0],"generating",row[2])
  policy=SimpleNamespace(organization_policy_version_id="organization-policy-1",classification="internal",fingerprint="sha256:"+"b"*64)
  with patch.object(self.repo,"_current",return_value=policy):
   first=self.repo.claim(CTX,plan,"studio-request-key-1",job_id=job["job_id"],job_version=1)
   self.assertTrue(first["dispatch_owner"])
   replay=self.repo.claim(CTX,plan,"studio-request-key-1",job_id=job["job_id"],job_version=1)
  self.assertFalse(replay["dispatch_owner"])
  self.assertFalse(any(table=="model_attempts" for table,_ in self.store.canons))
  self.assertEqual(sum(table=="run_steps" for table,_ in self.store.canons),1)
  self.assertEqual(sum(table=="runs" for table,_ in self.store.canons),1)
  snapshot=next(row["canonical_json"] for (table,_),row in self.store.canons.items() if table=="run_snapshots")
  self.assertEqual(snapshot["source_version_ids"],["version-qa"])
  self.assertIsNone(snapshot["cost_limit"]);self.assertIsNone(snapshot["currency"])
  self.assertTrue(any("RETURNING record_id" in sql and "run_steps" in sql for sql,_ in self.store.sql))

 def started(self):
  from types import SimpleNamespace
  plan=self.repo.prepare(CTX,REQUEST,"studio-request-key-1",META)
  job,_=self.repo.enqueue(CTX,plan,"studio-request-key-1",authorization_mode="policy_approved")
  row=self.store.jobs[job["job_id"]];self.store.jobs[job["job_id"]]=(row[0],"generating",row[2])
  with patch.object(self.repo,"_current",return_value=SimpleNamespace(organization_policy_version_id="organization-policy-1",classification="internal")):
   claim=self.repo.claim(CTX,plan,"studio-request-key-1",job_id=job["job_id"],job_version=1)
  return plan,job,claim

 def test_terminal_preserves_actual_attempt_and_known_cost_after_access_revoked(self):
  from decimal import Decimal
  from daon_user_api.operations_telemetry import ReportedCost
  plan,job,claim=self.started();published=[]
  with patch.object(self.repo,"_current",side_effect=StudioError("RESOURCE_UNAVAILABLE",404)):
   result=self.repo.finish(CTX,plan,"studio-request-key-1",job_id=job["job_id"],job_version=1,
    provider_outcome="succeeded",usage={"prompt_tokens":17},reported_cost=ReportedCost(Decimal("1.25"),"USD","qa-receipt-1"),
    publish=lambda conn:published.append("FORBIDDEN"))
  self.assertEqual(published,[]);self.assertEqual(result["status"],"failed")
  attempt=self.store.canons[("model_attempts",claim["ids"]["attempt"])]
  self.assertEqual(attempt["started_at"],datetime.fromisoformat(claim["started_at"]))
  self.assertEqual(attempt["usage_json"],{"prompt_tokens":17})
  self.assertEqual(len(self.store.costs),1)
  receipt=next(iter(self.store.costs.values()))
  self.assertEqual(receipt[8],Decimal("1.25"));self.assertEqual(receipt[12],"known")
  self.assertEqual(self.store.canons[("runs",claim["ids"]["run"])]["state"],"failed")
  self.assertFalse(any(table=="run_results" for table,_ in self.store.canons))

 def test_terminal_output_savepoint_rollback_keeps_cost_and_exact_replay(self):
  plan,job,claim=self.started()
  def failed_output(conn):
   self.assertTrue(("model_attempts",claim["ids"]["attempt"]) in self.store.canons)
   self.assertEqual(len(self.store.costs),1)
   self.assertIsNone(self.store.canons[("model_attempts",claim["ids"]["attempt"])]["safe_error_code"])
   conn.rows["injected-half-output"]=("must rollback",)
   raise StudioError("STUDIO_DATABASE_UNAVAILABLE",503)
  result=self.repo.finish(CTX,plan,"studio-request-key-1",job_id=job["job_id"],job_version=1,
   provider_outcome="succeeded",usage={},publish=failed_output)
  self.assertNotIn("injected-half-output",self.store.rows)
  self.assertEqual(result["status"],"failed");self.assertEqual(len(self.store.costs),1)
  self.assertEqual(next(iter(self.store.costs.values()))[12],"unknown")
  replay=self.repo.finish(CTX,plan,"studio-request-key-1",job_id=job["job_id"],job_version=1,
   provider_outcome="succeeded",usage={},publish=lambda conn:self.fail("REPLAY_MUST_NOT_PUBLISH"))
  self.assertEqual(result,replay);self.assertEqual(len(self.store.costs),1)
  with self.assertRaises(StudioError):self.repo.finish(CTX,plan,"studio-request-key-1",job_id=job["job_id"],job_version=1,
   provider_outcome="succeeded",usage={"prompt_tokens":1},publish=failed_output)

 def test_terminal_database_failure_retains_start_without_fake_completion(self):
  plan,job,claim=self.started();self.store.fail_cost=True
  with self.assertRaises(RuntimeError):self.repo.finish(CTX,plan,"studio-request-key-1",job_id=job["job_id"],job_version=1,
   provider_outcome="failed",usage={},safe_error_code="STUDIO_GENERATION_UNCERTAIN")
  self.assertIn(("run_steps",claim["ids"]["start"]),self.store.canons)
  self.assertNotIn(("model_attempts",claim["ids"]["attempt"]),self.store.canons)
  self.assertEqual(self.store.jobs[job["job_id"]][1],"generating")

 def test_terminal_success_uses_distinct_run_and_exact_parent_chain(self):
  plan,job,claim=self.started()
  result=self.repo.finish(CTX,plan,"studio-request-key-1",job_id=job["job_id"],job_version=1,
   provider_outcome="succeeded",usage={"completion_tokens":4,"prompt_tokens":True},
   publish=lambda conn:{"studio_output_id":"output-qa","output_version_id":"output-version-qa"})
  self.assertEqual(result["status"],"completed")
  run=self.store.canons[("runs",claim["ids"]["run"])];self.assertIsNone(run["conversation_id"])
  self.assertEqual(run["state"],"completed")
  saved=self.store.canons[("run_results",claim["ids"]["result"])]
  self.assertEqual(saved["selected_model_attempt_id"],claim["ids"]["attempt"])
  self.assertEqual(saved["routing_decision_id"],claim["ids"]["routing"])
  self.assertNotIn("answer",saved["canonical_json"])
  attempt=self.store.canons[("model_attempts",claim["ids"]["attempt"])]
  self.assertEqual(attempt["usage_json"],{"completion_tokens":4})
  self.assertEqual(attempt["routing_decision_id"],saved["routing_decision_id"])

 def test_terminal_model_change_and_output_database_error_preserve_accounting(self):
  from psycopg import OperationalError
  for mode in ("model-change","output-database"):
   with self.subTest(mode=mode):
    self.setUp();plan,job,claim=self.started()
    def publish(conn):raise OperationalError("DO_NOT_REFLECT_DATABASE_TEXT")
    with patch.object(self.repo,"_current",side_effect=StudioError("STUDIO_MODEL_CHANGED",409) if mode=="model-change" else None):
     result=self.repo.finish(CTX,plan,"studio-request-key-1",job_id=job["job_id"],job_version=1,
      provider_outcome="succeeded",usage={"total_tokens":22},publish=publish)
    self.assertEqual(result["status"],"failed");self.assertEqual(len(self.store.costs),1)
    self.assertEqual(sum(table=="model_attempts" for table,_ in self.store.canons),1)
    self.assertNotIn("DO_NOT_REFLECT",str(result))

 def test_actual_response_after_lease_reclaim_keeps_receipt_without_publishing(self):
  from decimal import Decimal
  from daon_user_api.operations_telemetry import ReportedCost
  plan,job,claim=self.started()
  self.store.job_versions[job["job_id"]]=2
  repeated=self.repo.claim(CTX,plan,"studio-request-key-1",job_id=job["job_id"],job_version=2)
  self.assertFalse(repeated["dispatch_owner"])
  result=self.repo.finish(CTX,plan,"studio-request-key-1",job_id=job["job_id"],job_version=1,
   provider_outcome="succeeded",usage={"total_tokens":29},reported_cost=ReportedCost(Decimal("0.25"),"USD","qa-late-receipt"),
   publish=lambda conn:self.fail("LOST_LEASE_MUST_NOT_PUBLISH"))
  self.assertEqual(result["status"],"failed")
  self.assertEqual(result["safe_error_code"],"STUDIO_GENERATION_UNCERTAIN")
  self.assertEqual(len(self.store.costs),1);self.assertEqual(next(iter(self.store.costs.values()))[12],"known")
  self.assertEqual(sum(table=="model_attempts" for table,_ in self.store.canons),1)

 def test_job_terminal_uses_database_transaction_clock_required_by_0069(self):
  plan,job,claim=self.started()
  self.repo.finish(CTX,plan,"studio-request-key-1",job_id=job["job_id"],job_version=1,
   provider_outcome="failed",usage={},safe_error_code="STUDIO_GENERATION_UNCERTAIN")
  sql,params=next((sql,params) for sql,params in self.store.sql if "studio-execution:job-finish" in sql)
  self.assertIn("completed_at=now()",sql)
  self.assertFalse(any(isinstance(v,datetime) for v in params))

 def test_reclaimer_uncertain_then_actual_owner_late_observation_no_second_charge(self):
  from decimal import Decimal
  from daon_user_api.operations_telemetry import ReportedCost
  plan,job,claim=self.started();self.store.job_versions[job["job_id"]]=2
  uncertain=self.repo.mark_uncertain(CTX,plan,"studio-request-key-1",job_id=job["job_id"],job_version=2)
  self.assertEqual(uncertain["status"],"failed")
  self.assertEqual(sum(table=="model_attempts" for table,_ in self.store.canons),0)
  receipt=next(iter(self.store.costs.values()));self.assertIsNone(receipt[6]);self.assertEqual(receipt[12],"uncertain")
  kwargs=dict(job_id=job["job_id"],job_version=1,provider_outcome="succeeded",usage={"total_tokens":18},
   reported_cost=ReportedCost(Decimal("0.30"),"USD","late-receipt-qa"),publish=lambda conn:self.fail("LATE_MUST_NOT_PUBLISH"))
  late=self.repo.finish(CTX,plan,"studio-request-key-1",**kwargs)
  self.assertEqual(late,uncertain);self.assertEqual(len(self.store.costs),2)
  self.assertEqual(sum(v[12]=="known" for v in self.store.costs.values()),1)
  self.assertEqual(sum(table=="model_attempts" for table,_ in self.store.canons),1)
  self.assertEqual(self.store.canons[("runs",claim["ids"]["run"])]["state"],"failed")
  self.assertEqual(self.repo.finish(CTX,plan,"studio-request-key-1",**kwargs),uncertain)
  self.assertEqual(len(self.store.costs),2)
  with self.assertRaises(StudioError):self.repo.finish(CTX,plan,"studio-request-key-1",**{**kwargs,"usage":{"total_tokens":19}})
 def test_same_usage_different_response_cannot_replay_terminal(self):
  plan,job,claim=self.started()
  kwargs=dict(job_id=job["job_id"],job_version=1,provider_outcome="succeeded",usage={},safe_error_code="STUDIO_OUTPUT_UNAVAILABLE",response_digest="a"*64)
  first=self.repo.finish(CTX,plan,"studio-request-key-1",**kwargs)
  self.assertEqual(self.repo.finish(CTX,plan,"studio-request-key-1",**kwargs),first)
  with self.assertRaises(StudioError):self.repo.finish(CTX,plan,"studio-request-key-1",**{**kwargs,"response_digest":"b"*64})

class StudioCurrentBindingTests(unittest.TestCase):
 def current(self,metadata_row,*,revision=None,previous=("aggregate-qa",2,"previous-qa","slides","slides"),request_payload=None,origin=(1,)):
  from types import SimpleNamespace
  order=[];self_test=self
  class Connection:
   def execute(self,sql,params=()):
    order.append("metadata" if "studio-execution:model-metadata" in sql else "sql")
    if "studio-execution:model-metadata" in sql:return Result([metadata_row] if metadata_row else [])
    if "studio-execution:session" in sql:return Result([(1,)])
    if "studio-execution:source" in sql:return Result([("source-qa","e"*64,"index-qa","f"*64)])
    if "studio-typed:origin" in sql:
     for required in ("r.created_by=%s","rr.created_by=r.created_by","b.notebook_id=%s"):self_test.assertIn(required,sql)
     return Result([origin] if origin else [])
    if "studio-typed:previous" in sql:return Result([previous] if previous else [])
    if "pg_advisory_xact_lock" in sql:return Result()
    raise AssertionError("UNEXPECTED_SQL")
  repo=PostgresStudioExecutionRepository(Store(),clock=lambda:NOW)
  policy=SimpleNamespace(fingerprint="sha256:"+"b"*64)
  with patch("daon_user_api.studio_execution_postgres.assert_execution_current",side_effect=lambda *a:order.append("model-settings")), \
       patch.object(repo.workspace,"_require_generation_scope",side_effect=lambda *a:order.append("notebook")), \
       patch.object(repo.workspace,"_require_output_sources"), \
       patch("daon_user_api.studio_execution_postgres._current_auth_grant"), \
       patch.object(repo,"_effective_policy",return_value=policy):
   repo._current(Connection(),CTX,request_payload or REQUEST,revision or META)
  return order
 def test_model_settings_lock_precedes_notebook_lock(self):
  order=self.current((1,1,0,"a"*64,"CUSTOM","openai_compatible"))
  self.assertLess(order.index("model-settings"),order.index("notebook"))
 def test_current_metadata_epoch_drift_is_rejected_without_credential_read(self):
  baseline=(1,1,0,"a"*64,"CUSTOM","openai_compatible")
  self.current(baseline)
  for index,value in ((0,2),(1,2),(2,1),(3,"9"*64),(4,"GROQ"),(5,"anthropic_compatible")):
   changed=list(baseline);changed[index]=value
   with self.subTest(field=index):
    with self.assertRaises(StudioError) as error:self.current(tuple(changed))
    self.assertEqual(error.exception.code,"STUDIO_MODEL_CHANGED")
  with self.assertRaises(StudioError):self.current(None)
 def test_current_origin_guard_rejects_missing_or_foreign_parent_before_admission_and_claim(self):
  request={**REQUEST,"source_only":False,"source_id":"source-qa","run_id":"origin-run","run_result_id":"origin-result"}
  self.current((1,1,0,"a"*64,"CUSTOM","openai_compatible"),request_payload=request)
  with self.assertRaises(StudioError) as error:self.current((1,1,0,"a"*64,"CUSTOM","openai_compatible"),request_payload=request,origin=None)
  self.assertEqual(error.exception.code,"RESOURCE_UNAVAILABLE")
 def test_regeneration_previous_changed_or_different_type_rejected_before_start(self):
  metadata={**META,"operation":"regenerate","revision_type":"ai_regeneration","change_reason":"자료 재정리", "output_id":"output-qa","previous_version_id":"previous-qa"}
  self.current((1,1,0,"a"*64,"CUSTOM","openai_compatible"),revision=metadata)
  for previous in (("aggregate-qa",3,"newer-qa","slides","slides"),("aggregate-qa",2,"previous-qa","quiz","slides"),None):
   with self.subTest(previous=previous),self.assertRaises(StudioError):self.current((1,1,0,"a"*64,"CUSTOM","openai_compatible"),revision=metadata,previous=previous)

class StudioTypedPersistenceTests(unittest.TestCase):
 def test_terminal_material_actions_are_read_only_from_matching_immutable_observation(self):
  import hashlib
  from types import SimpleNamespace
  from daon_user_api.data_canon import canonical_json_bytes
  from daon_user_api.studio_generation_queue import StudioGenerationJob
  from daon_user_api.studio_workspace_postgres import PostgresStudioWorkspaceRepository
  job=StudioGenerationJob(CTX.tenant_id,CTX.workspace_id,"job-qa",CTX.actor_id,CTX.trace_id,CTX.policy_version,"key-qa",
   {**REQUEST,"notebook_id":CTX.notebook_id,"execution_plan_id":"plan-qa"},"unavailable",1,2,"STUDIO_OUTPUT_UNAVAILABLE")
  store=Store();repo=PostgresStudioWorkspaceRepository(store);repo._generation_queue=SimpleNamespace(get=lambda *args:job)
  result={"job_id":"job-qa","status":"unavailable","safe_error_code":"STUDIO_OUTPUT_UNAVAILABLE","next_actions":["선택한 자료를 보완해 주세요."],"studio_output_id":None,"output_version_id":None}
  payload={"kind":"studio_execution_terminal","job_id":"job-qa","result":result}
  text=canonical_json_bytes(payload).decode();row=(payload,text,hashlib.sha256(text.encode()).hexdigest(),CTX.actor_id)
  with patch.object(repo,"_require_generation_scope"),patch.object(store,"execute",side_effect=lambda sql,params:Result([row]) if "studio-typed:terminal-actions" in sql else self.fail("UNEXPECTED_QUERY")):
   view=repo.get_generation_job(CTX,"job-qa")
   self.assertEqual(view["next_actions"],result["next_actions"])
   row=(payload,text,"0"*64,CTX.actor_id)
   with self.assertRaises(StudioError):repo.get_generation_job(CTX,"job-qa")

 def test_revision_input_is_restored_only_from_exact_stored_snapshot_and_current_previous(self):
  import hashlib,json
  from daon_user_api.data_canon import canonical_json_bytes
  from daon_user_api.studio_workspace_postgres import PostgresStudioWorkspaceRepository
  payload={**REQUEST,"intended_model_selection":None}
  text=canonical_json_bytes(payload).decode();checksum=hashlib.sha256(text.encode()).hexdigest()
  root={"output_type":"slides"};row=(payload,text,checksum,payload,text,checksum,"slides")
  store=Store();repo=PostgresStudioWorkspaceRepository(store)
  def execute(sql,params=()):
   if "studio-typed:revision-input" in sql:return Result([row])
   raise AssertionError("UNEXPECTED_REVISION_INPUT_QUERY")
  with patch.object(repo,"_require_output_sources"),patch.object(repo,"require_typed_revision_current",return_value=("aggregate",1,"previous-qa","slides","slides")) as current,patch.object(repo,"_require_generation_scope"),patch.object(repo,"_policy_projection",return_value={"review_condition":"review_required","ruleset_version_id":None}),patch.object(store,"execute",side_effect=execute):
   restored=repo.load_revision_input(CTX,"output-qa","previous-qa",None)
   self.assertEqual(restored.output_type,"slides");self.assertEqual(restored.source_version_ids,("version-qa",))
   self.assertIsNone(restored.intended_model_selection)
   current.assert_called_once()
   with self.assertRaises(StudioError):repo.load_revision_input(CTX,"output-qa","previous-qa",{"purpose":"누락된 나머지 설정"})
   row=(payload,text,"0"*64,payload,text,checksum,"slides")
   with self.assertRaises(StudioError):repo.load_revision_input(CTX,"output-qa","previous-qa",None)

 def test_validated_content_is_saved_in_actual_studio_writer_with_actual_execution_lineage(self):
  from types import SimpleNamespace
  from daon_user_api.studio_workspace_postgres import PostgresStudioWorkspaceRepository
  from daon_user_api.studio_workspace import StudioGenerationRequest
  from daon_user_api.studio_typed_generation import parse_typed_output
  request=StudioGenerationRequest(**{**REQUEST,"source_version_ids":tuple(REQUEST["source_version_ids"])})
  evidence=[{"chunk_id":"chunk-qa","text":"원문 전체입니다.","page":1,"source_version_id":"version-qa","source_id":"source-qa","evidence_span_id":"span-qa","locator_kind":"docx_block","locator_value":"block-qa"}]
  typed=parse_typed_output(request,{"content":{"slides":[{"title":"원문","body":"원문 전체입니다.","evidence":["chunk-qa"]}]},
   "cited_chunk_ids":["chunk-qa"],"insufficient":False,"next_actions":[]},evidence,request_id="generation-qa",model_id="model-qa")
  repo=PostgresStudioWorkspaceRepository(Store());writes=[];transitions=[];audits=[]
  def insert(conn,ctx,table,record,payload,**kwargs):writes.append((table,record,payload,kwargs))
  with patch.object(repo,"_require_generation_scope"), \
       patch.object(repo,"_policy_projection",return_value={"review_condition":"review_required","ruleset_version_id":None}), \
       patch.object(repo,"_insert",side_effect=insert), \
       patch.object(repo,"_transition",side_effect=lambda *args:transitions.append(args[2:]) or 2), \
       patch.object(repo,"_finish",side_effect=lambda *args:audits.append(args)):
   result=repo.persist_typed_generation(repo._cloud_store,CTX,request,"studio-request-key-1",typed,evidence,
    execution_ids={"run":"studio-run-qa","attempt":"studio-attempt-qa","result":"studio-result-qa"},metadata=META)
   with patch.object(repo,"_require_output_sources"),patch.object(repo._cloud_store,"execute",return_value=Result([("aggregate-qa",2,"previous-qa","slides","slides")])):
    repo.persist_typed_generation(repo._cloud_store,CTX,request,"studio-request-key-2",typed,evidence,
     execution_ids={"run":"studio-run-qa-2","attempt":"studio-attempt-qa-2","result":"studio-result-qa-2"},
     metadata={**META,"operation":"regenerate","revision_type":"settings_change","change_reason":"검토 범위를 조정했습니다.","output_id":"output-qa","previous_version_id":"previous-qa"})
   before=len(writes)
   for prior_type,root_type in (("quiz","slides"),("slides","quiz")):
    with self.subTest(previous=prior_type,root=root_type),patch.object(repo,"_require_output_sources"),patch.object(repo._cloud_store,"execute",return_value=Result([("aggregate-qa",2,"previous-qa",prior_type,root_type)])):
     with self.assertRaises(StudioError):repo.persist_typed_generation(repo._cloud_store,CTX,request,"studio-request-key-3",typed,evidence,
      execution_ids={"run":"studio-run-qa-3","attempt":"studio-attempt-qa-3","result":"studio-result-qa-3"},
      metadata={**META,"operation":"regenerate","revision_type":"ai_regeneration","change_reason":"근거를 다시 정리해 주세요.","output_id":"output-qa","previous_version_id":"previous-qa"})
   self.assertEqual(len(writes),before)
  self.assertEqual(set(result),{"studio_output_id","output_version_id"})
  payload=next(p for table,_,p,_ in writes if table=="output_versions")
  self.assertEqual(payload["content"],typed.content)
  citation=next(p for table,_,p,_ in writes if table=="citations")
  citation_record=next(record for table,record,_,_ in writes if table=="citations")
  self.assertNotEqual(citation_record,"chunk-qa")
  self.assertEqual(citation["run_result_id"],"studio-result-qa")
  self.assertEqual(citation["locator"],{"kind":"docx_block","value":"block-qa"})
  self.assertEqual(payload["execution_lineage"],{"run_id":"studio-run-qa","model_attempt_id":"studio-attempt-qa"})
  self.assertEqual(payload["actual_model_selection"],META["selection"])
  self.assertEqual(payload["generator_version"],"studio-selected-evidence-v1")
  self.assertTrue(any(table=="evidence_references" and v["extra_values"][-2:]==("version-qa","span-qa") for table,_,_,v in writes))
  self.assertEqual(len(audits),2)
  changed=[(p,v) for table,_,p,v in writes if table=="output_versions"][-1]
  self.assertEqual(changed[0]["revision_type"],"settings_change")
  self.assertEqual(changed[0]["change_reason"],"검토 범위를 조정했습니다.")
  self.assertEqual(changed[1]["aggregate_id"],"aggregate-qa")
  self.assertEqual(changed[1]["extra_values"][-2:],(3,"previous-qa"))
