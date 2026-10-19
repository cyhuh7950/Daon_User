"""M2-1 서버 worker의 실제 poll·프로세스 관측. 작업 성공/모델 readiness가 아니다."""
from __future__ import annotations

import ctypes
import hashlib
import json
import logging
import os
import re
import secrets
import stat
import sys
import threading
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import datetime,timedelta,timezone
from decimal import Decimal


KINDS=("document","studio","object")
METRICS=("cpu_quota_millicores","memory_limit_bytes","memory_current_bytes","runtime_available_bytes")
_LOG=logging.getLogger(__name__)
_MAX=2**63-1
_CODES={None,"WORKER_STOPPED","WORKER_POLL_FAILED"}


class TelemetryUnavailable(RuntimeError):
    def __init__(self):super().__init__("OPERATIONS_TELEMETRY_UNAVAILABLE")


@dataclass(frozen=True,slots=True,repr=False)
class ReportedCost:
    amount:Decimal
    currency:str
    receipt_id:str

    def __post_init__(self):
        if (type(self.amount) is not Decimal or not self.amount.is_finite() or self.amount<0
            or self.amount>=Decimal("1000000000000000") or self.amount.as_tuple().exponent < -9
            or type(self.currency) is not str or re.fullmatch(r"[A-Z]{3}",self.currency) is None):
            raise ValueError("COST_RECEIPT_INVALID")
        _id(self.receipt_id)


def reported_cost_from_usage(usage):
    """명시된 transport usage만 소비. 본문/float/통화 추정·가격표 계산0."""
    if not isinstance(usage,dict):return None
    raw=usage.get("cost");currency=usage.get("currency")
    if type(raw) is int:raw=str(raw)
    if type(raw) is not str or re.fullmatch(r"(?:0|[1-9][0-9]{0,14})(?:\.[0-9]{1,9})?",raw) is None:return None
    if type(currency) is not str or re.fullmatch(r"[A-Z]{3}",currency) is None:return None
    amount=Decimal(raw)
    safe_usage={name:usage[name] for name in ("prompt_tokens","completion_tokens","total_tokens")
        if type(usage.get(name)) is int and 0<=usage[name]<=2**63-1}
    digest=hashlib.sha256(json.dumps(["provider-reported-cost-v1",format(amount,"f"),currency,safe_usage],sort_keys=True,separators=(",",":")).encode()).hexdigest()
    return ReportedCost(amount,currency,"cost-"+digest)


@dataclass(frozen=True,slots=True,repr=False)
class CostReceipt:
    receipt_id:str
    provider_connection_id:str|None
    basis:str
    amount:Decimal|None
    currency:str|None
    usage_digest:str|None
    price_version:str|None
    state:str
    observed_at:datetime

    def __post_init__(self):
        _id(self.receipt_id);_utc(self.observed_at)
        if self.provider_connection_id is not None:_id(self.provider_connection_id)
        if (self.basis not in {"provider_reported","price_card"} or self.state not in {"known","unknown","uncertain"}
            or self.usage_digest is not None and (type(self.usage_digest) is not str or re.fullmatch(r"[a-f0-9]{64}",self.usage_digest) is None)):
            raise ValueError("COST_RECEIPT_INVALID")
        if self.basis=="provider_reported":
            if self.price_version is not None:raise ValueError("COST_RECEIPT_INVALID")
        else:
            _id(self.price_version)
            if self.state=="known" and self.usage_digest is None:raise ValueError("COST_RECEIPT_INVALID")
        if self.state=="known":
            ReportedCost(self.amount,self.currency,self.receipt_id)
            if self.provider_connection_id is None:raise ValueError("COST_RECEIPT_INVALID")
        elif self.amount is not None or self.currency is not None:raise ValueError("COST_RECEIPT_INVALID")


def make_cost_receipt(reported,usage,*,provider_connection_id,outcome,observed_at):
    if reported is not None and type(reported) is not ReportedCost:raise ValueError("COST_RECEIPT_INVALID")
    if outcome not in {"succeeded","failed","cancelled"}:raise ValueError("COST_RECEIPT_INVALID")
    tokens={name:usage[name] for name in ("prompt_tokens","completion_tokens","total_tokens")
        if isinstance(usage,dict) and type(usage.get(name)) is int and 0<=usage[name]<=2**63-1}
    usage_digest=hashlib.sha256(json.dumps(tokens,sort_keys=True,separators=(",",":")).encode()).hexdigest()
    state="uncertain" if outcome!="succeeded" else "known" if reported is not None and provider_connection_id is not None else "unknown"
    identifier=hashlib.sha256(json.dumps(["run-cost-receipt-v1",reported.receipt_id if reported else None,usage_digest,state],separators=(",",":")).encode()).hexdigest()
    return CostReceipt("cost-"+identifier,provider_connection_id,"provider_reported",reported.amount if state=="known" else None,
        reported.currency if state=="known" else None,usage_digest,None,state,observed_at)


def make_price_card_cost_receipt(charge,*,price_version,provider_connection_id,outcome,observed_at):
    """승인 가격과 실측 usage의 산정 근거. Provider 청구/사전 견적과 구분한다."""
    from .operations_run_cost import PriceCardCharge
    _id(price_version);_id(provider_connection_id);_utc(observed_at)
    if (outcome not in {"succeeded","failed","cancelled"}
        or charge is not None and (type(charge) is not PriceCardCharge or charge.price_version!=price_version)):
        raise ValueError("COST_RECEIPT_INVALID")
    state="uncertain" if outcome!="succeeded" else "known" if charge is not None else "unknown"
    usage_digest=None if charge is None else charge.usage_digest
    identity=["measured-price-card-cost-v1",price_version,provider_connection_id,usage_digest,state]
    receipt_id="cost-"+hashlib.sha256(json.dumps(identity,separators=(",",":")).encode()).hexdigest()
    return CostReceipt(receipt_id,provider_connection_id,"price_card",charge.amount if state=="known" else None,
        charge.currency if state=="known" else None,usage_digest,price_version,state,observed_at)


@dataclass(frozen=True,slots=True,repr=False)
class DeploymentReceipt:
    operation_id:str
    operation:str
    code_revision:str|None
    image_digest:str|None
    previous_image_digest:str|None
    state:str
    observed_at:datetime
    safe_code:str

    def __post_init__(self):
        _id(self.operation_id);_utc(self.observed_at)
        if self.operation not in {'deploy','rollback'} or self.state not in {'succeeded','failed','uncertain'}:
            raise ValueError('DEPLOYMENT_RECEIPT_INVALID')
        if self.code_revision is not None and (type(self.code_revision) is not str or re.fullmatch('[a-f0-9]{40}',self.code_revision) is None):
            raise ValueError('DEPLOYMENT_RECEIPT_INVALID')
        for value in (self.image_digest,self.previous_image_digest):
            if value is not None and (type(value) is not str or re.fullmatch('sha256:[a-f0-9]{64}',value) is None):raise ValueError('DEPLOYMENT_RECEIPT_INVALID')
        if self.state=='succeeded' and (self.code_revision is None or self.image_digest is None or self.previous_image_digest is None):
            raise ValueError('DEPLOYMENT_RECEIPT_INVALID')
        if self.safe_code!=('DEPLOYMENT_' if self.operation=='deploy' else 'ROLLBACK_')+self.state.upper():
            raise ValueError('DEPLOYMENT_RECEIPT_INVALID')

    def digest(self):
        self.__post_init__()
        values=[getattr(self,name) for name in self.__dataclass_fields__]
        return hashlib.sha256(json.dumps(['operations-deployment-receipt-v1',*[v.isoformat() if isinstance(v,datetime) else v for v in values]],separators=(',',':')).encode()).hexdigest()


def _utc(value):
    if not isinstance(value,datetime) or value.tzinfo is None or value.utcoffset()!=timedelta(0):
        raise ValueError("WORKER_OBSERVATION_INVALID")
    return value


def _id(value):
    if type(value) is not str or re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_.:-]{0,127}",value) is None:
        raise ValueError("WORKER_OBSERVATION_INVALID")
    return value


def _number(value):
    if value is not None and (type(value) is not int or not 0<=value<=_MAX):
        raise ValueError("WORKER_OBSERVATION_INVALID")
    return value


@dataclass(frozen=True,slots=True)
class ResourceValues:
    cpu_quota_millicores:int|None=None
    memory_limit_bytes:int|None=None
    memory_current_bytes:int|None=None
    runtime_available_bytes:int|None=None


@dataclass(frozen=True,slots=True,repr=False)
class WorkerSample:
    worker_id:str
    boot_id:str
    worker_kind:str
    process_started_at:datetime
    sequence:int
    observed_at:datetime
    poll_started_at:datetime|None=None
    poll_finished_at:datetime|None=None
    active_started_at:datetime|None=None
    last_safe_code:str|None=None
    cpu_quota_millicores:int|None=None
    memory_limit_bytes:int|None=None
    memory_current_bytes:int|None=None
    runtime_available_bytes:int|None=None
    producer_version:int=1


@dataclass(frozen=True,slots=True,repr=False)
class ReceivedWorkerSample:
    sample:WorkerSample
    received_at:datetime


def validate_worker_sample(sample,*,received_at,previous=()):
    """형식/수명 검사. DB의 clock/원자 sequence 검사는 보호 함수가 별도로 수행한다."""
    if type(sample) is not WorkerSample:raise ValueError("WORKER_OBSERVATION_INVALID")
    _utc(received_at);_id(sample.worker_id);_id(sample.boot_id)
    if sample.worker_kind not in KINDS or type(sample.producer_version) is not int or sample.producer_version!=1:
        raise ValueError("WORKER_OBSERVATION_INVALID")
    if type(sample.sequence) is not int or not 1<=sample.sequence<=_MAX:raise ValueError("WORKER_OBSERVATION_INVALID")
    if not _utc(sample.process_started_at)<=_utc(sample.observed_at)<=received_at+timedelta(seconds=5):
        raise ValueError("WORKER_OBSERVATION_INVALID")
    for field in ("poll_started_at","poll_finished_at","active_started_at"):
        value=getattr(sample,field)
        if value is not None and not sample.process_started_at<=_utc(value)<=sample.observed_at:
            raise ValueError("WORKER_OBSERVATION_INVALID")
    if sample.active_started_at is not None and (sample.poll_started_at is None or sample.active_started_at<sample.poll_started_at):
        raise ValueError("WORKER_OBSERVATION_INVALID")
    if sample.poll_finished_at is not None and sample.poll_started_at is None:raise ValueError("WORKER_OBSERVATION_INVALID")
    if sample.last_safe_code not in _CODES or (sample.last_safe_code=="WORKER_STOPPED" and sample.active_started_at is not None):
        raise ValueError("WORKER_OBSERVATION_INVALID")
    for field in METRICS:_number(getattr(sample,field))
    for stored in previous:
        if type(stored) is not ReceivedWorkerSample:raise ValueError("WORKER_OBSERVATION_INVALID")
        old=stored.sample
        if old.worker_id!=sample.worker_id:continue
        if old.worker_kind!=sample.worker_kind:raise ValueError("WORKER_OBSERVATION_INVALID")
        if old.boot_id==sample.boot_id:
            if (old.process_started_at!=sample.process_started_at or sample.sequence<=old.sequence
                or sample.observed_at<old.observed_at):raise ValueError("WORKER_OBSERVATION_INVALID")
        elif sample.process_started_at<=old.process_started_at:raise ValueError("WORKER_OBSERVATION_INVALID")
    return sample


def _time(value):return None if value is None else value.isoformat().replace("+00:00","Z")


def project_worker(stored,*,now,worker_kind=None):
    _utc(now)
    kind=worker_kind if stored is None else stored.sample.worker_kind
    if kind not in KINDS:raise ValueError("WORKER_OBSERVATION_INVALID")
    common=dict(state="not_collected",observed_at=None,received_at=None,coverage="workspace_operational_counts",
        safe_code="TELEMETRY_NOT_COLLECTED",worker_kind=kind,process_state="unknown",
        poll_started_at=None,poll_finished_at=None,active_started_at=None)
    if stored is None:return common
    validate_worker_sample(stored.sample,received_at=stored.received_at)
    if stored.received_at>now:raise ValueError("WORKER_OBSERVATION_INVALID")
    sample=stored.sample;state="fresh" if now-stored.received_at<=timedelta(seconds=45) else "stale"
    process="alive"
    if sample.last_safe_code=="WORKER_STOPPED":process="stopped"
    elif sample.active_started_at is not None:process="processing"
    elif now-(sample.poll_finished_at or sample.poll_started_at or sample.process_started_at)>timedelta(seconds=45):process="poll_stale"
    common.update(state=state,observed_at=_time(sample.observed_at),received_at=_time(stored.received_at),safe_code="TELEMETRY_"+state.upper(),
        process_state=process,poll_started_at=_time(sample.poll_started_at),poll_finished_at=_time(sample.poll_finished_at),active_started_at=_time(sample.active_started_at))
    return common


def project_resources(stored,*,now,worker_kind):
    base=project_worker(stored,now=now,worker_kind=worker_kind)
    result=[]
    for field in METRICS:
        item={key:base[key] for key in ("state","observed_at","received_at","coverage","safe_code")}
        value=None if stored is None else getattr(stored.sample,field)
        if value is None:
            item.update(state="not_collected",observed_at=None,received_at=None,safe_code="TELEMETRY_NOT_COLLECTED")
        item.update(worker_kind=worker_kind,metric_id=field,value=value,unit="millicores" if field==METRICS[0] else "bytes")
        result.append(item)
    return result


def _read_fixed(path,limit=256):
    fd=os.open(path,os.O_RDONLY|os.O_NOFOLLOW|os.O_CLOEXEC)
    try:
        if not stat.S_ISREG(os.fstat(fd).st_mode):raise ValueError()
        raw=os.read(fd,limit+1)
        if len(raw)>limit:raise ValueError()
        return raw.decode("ascii").strip()
    finally:os.close(fd)


class LocalResourceReader:
    """사용자 path/host 전체 추정0. 현재 container cgroup v2·기존 영속 mount만."""
    def __init__(self,*,_platform=None,_read=None,_statvfs=None):
        self.platform=sys.platform if _platform is None else _platform
        self.read_fixed=_read or _read_fixed;self.statvfs=_statvfs or getattr(os,"statvfs",None)

    def read(self):
        if self.platform!="linux":return ResourceValues()
        def measured(fn):
            try:return _number(fn())
            except Exception:return None
        def integer(name):
            raw=self.read_fixed("/sys/fs/cgroup/"+name)
            if not re.fullmatch(r"[0-9]+",raw):raise ValueError()
            return int(raw)
        def cpu():
            quota,period=self.read_fixed("/sys/fs/cgroup/cpu.max").split()
            if not quota.isascii() or not quota.isdigit() or not period.isascii() or not period.isdigit() or int(period)<=0:raise ValueError()
            return int(quota)*1000//int(period)
        def available():
            row=self.statvfs("/var/lib/daon-user")
            if type(row.f_bavail) is not int or type(row.f_frsize) is not int or row.f_bavail<0 or row.f_frsize<=0:raise ValueError()
            return row.f_bavail*row.f_frsize
        return ResourceValues(measured(cpu),measured(lambda:integer("memory.max")),measured(lambda:integer("memory.current")),measured(available))


def process_started_at():
    """관측 시작 now를 OS 프로세스 시작으로 가장하지 않는다."""
    try:
        if os.name=="nt":
            from ctypes import wintypes
            kernel=ctypes.WinDLL("kernel32",use_last_error=True)
            kernel.GetCurrentProcess.restype=wintypes.HANDLE
            kernel.GetProcessTimes.argtypes=[wintypes.HANDLE]+[ctypes.POINTER(wintypes.FILETIME)]*4
            values=[wintypes.FILETIME() for _ in range(4)]
            if not kernel.GetProcessTimes(kernel.GetCurrentProcess(),*(ctypes.byref(value) for value in values)):raise OSError()
            ticks=(values[0].dwHighDateTime<<32)|values[0].dwLowDateTime
            return datetime(1601,1,1,tzinfo=timezone.utc)+timedelta(microseconds=ticks//10)
        if sys.platform=="linux":
            fields=_read_fixed("/proc/self/stat",65536).rsplit(")",1)[1].split()
            boot=[line.split()[1] for line in _read_fixed("/proc/stat",65536).splitlines() if line.startswith("btime ")]
            if len(boot)!=1:raise ValueError()
            return datetime.fromtimestamp(int(boot[0])+int(fields[19])/os.sysconf("SC_CLK_TCK"),timezone.utc)
    except Exception:pass
    raise TelemetryUnavailable()


def safe_hook(publisher,name,*args,**kwargs):
    if publisher is None:return
    try:getattr(publisher,name)(*args,**kwargs)
    except Exception:_LOG.warning("WORKER_OBSERVATION_UNAVAILABLE")


@contextmanager
def observe_poll(publisher):
    safe_hook(publisher,"poll_started");failed=True
    try:
        yield
        failed=False
    finally:safe_hook(publisher,"poll_finished",safe_code="WORKER_POLL_FAILED" if failed else None)


def start_worker_observation(publisher,kind,worker_id):
    if publisher is None:return
    try:publisher.start(kind,worker_id,secrets.token_hex(16),process_started_at())
    except Exception:_LOG.warning("WORKER_OBSERVATION_UNAVAILABLE")


class WorkerObservationPublisher:
    def __init__(self,repository,*,clock=None,resources=None):
        self.repository=repository;self.clock=clock or (lambda:datetime.now(timezone.utc));self.resources=resources or LocalResourceReader()
        self._lock=threading.Lock();self._publish_lock=threading.Lock();self._stop=threading.Event();self._thread=None
        self._sample=None;self._closed=False;self._wait=lambda stop,seconds:stop.wait(seconds)

    @property
    def running(self):return self._thread is not None and self._thread.is_alive()

    def start(self,worker_kind,worker_id,boot_id,process_started_at):
        if self.running:raise TelemetryUnavailable()
        sample=WorkerSample(worker_id,boot_id,worker_kind,process_started_at,1,self.clock())
        validate_worker_sample(sample,received_at=sample.observed_at)
        with self._lock:
            self._sample=sample;self._sequence=0;self._closed=False
        self._stop.clear()
        def run():
            self._publish()
            while not self._wait(self._stop,15.0):self._publish()
        self._thread=threading.Thread(target=run,name="operations-observation-publisher",daemon=False)
        self._thread.start()

    def _change(self,**fields):
        from dataclasses import replace
        with self._lock:
            if self._sample is not None:self._sample=replace(self._sample,**fields)

    def poll_started(self):self._change(poll_started_at=self.clock(),last_safe_code=None)
    def processing_started(self):self._change(active_started_at=self.clock())
    def poll_finished(self,safe_code=None):
        self._change(poll_finished_at=self.clock(),active_started_at=None,last_safe_code=safe_code)

    def _publish(self):
        from dataclasses import replace
        with self._publish_lock:
            try:
                try:resources=self.resources.read()
                except Exception:resources=ResourceValues()
                if type(resources) is not ResourceValues:resources=ResourceValues()
                with self._lock:
                    if self._sample is None:return
                    self._sequence+=1
                    sample=replace(self._sample,sequence=self._sequence,observed_at=self.clock(),
                        **{field:getattr(resources,field) for field in METRICS})
                validate_worker_sample(sample,received_at=sample.observed_at)
                self.repository.record_worker_sample(sample)
            except Exception:_LOG.warning("WORKER_OBSERVATION_UNAVAILABLE")

    def stop(self,*,publish_stopped=True):
        self._stop.set()
        if self._thread is not None:
            self._thread.join(5)
            if self._thread.is_alive():raise TelemetryUnavailable()
        if self._closed:return
        if self._sample is not None:
            if publish_stopped:self._change(last_safe_code="WORKER_STOPPED",active_started_at=None)
            self._publish()
        self._closed=True
        close=getattr(self.repository,"close",None)
        if callable(close):close()


def worker_publisher(database_dsn,*,worker_kind=None,worker_id=None):
    try:
        from .operations_telemetry_postgres import PostgresOperationsTelemetryRepository,expected_worker_ids
        expected=expected_worker_ids()
        if worker_kind is not None:expected[worker_kind]=worker_id
        return WorkerObservationPublisher(PostgresOperationsTelemetryRepository(database_dsn,expected_workers=expected))
    except Exception:
        _LOG.warning("WORKER_OBSERVATION_UNAVAILABLE")
        return None
