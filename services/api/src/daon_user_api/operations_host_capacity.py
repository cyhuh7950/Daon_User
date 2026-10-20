"""WSL 한 호스트의 제한된 /proc 관측. GPU·모델 적재 가능 여부를 추론하지 않는다."""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime,timedelta
from pathlib import Path
import re

from .operations_run_cost import _utc

HOST_REF='wsl-server-daon-user'
HOST_METRICS=(('cpu_logical_millicores','millicores'),('cpu_usage_basis_points','basis_points'),
    ('memory_total_bytes','bytes'),('memory_available_bytes','bytes'))


@dataclass(frozen=True,slots=True)
class HostCapacitySample:
    host_ref:str
    boot_id:str
    sequence:int
    observed_at:datetime
    cpu_logical_millicores:int
    cpu_usage_basis_points:int|None
    memory_total_bytes:int
    memory_available_bytes:int
    cpu_counters:tuple[int,...]

    def __post_init__(self):
        _utc(self.observed_at)
        if (self.host_ref!=HOST_REF or type(self.boot_id) is not str
            or re.fullmatch(r'[0-9a-f]{8}(?:-[0-9a-f]{4}){3}-[0-9a-f]{12}',self.boot_id) is None
            or type(self.sequence) is not int or not 1<=self.sequence<=2**63-1
            or type(self.cpu_logical_millicores) is not int or not 1000<=self.cpu_logical_millicores<=32768000
            or self.cpu_logical_millicores%1000!=0
            or self.cpu_usage_basis_points is not None and (
                type(self.cpu_usage_basis_points) is not int or not 0<=self.cpu_usage_basis_points<=10000)
            or type(self.memory_total_bytes) is not int or not 1<=self.memory_total_bytes<=2**63-1
            or type(self.memory_available_bytes) is not int or not 0<=self.memory_available_bytes<=self.memory_total_bytes
            or type(self.cpu_counters) is not tuple or len(self.cpu_counters)!=8
            or any(type(v) is not int or not 0<=v<=2**63-1 for v in self.cpu_counters)):
            raise ValueError('HOST_CAPACITY_INVALID')


def parse_host_sample(*,meminfo,stat,boot_id,observed_at,sequence,previous=None):
    if (type(meminfo) is not str or len(meminfo.encode())>65536
        or type(stat) is not str or len(stat.encode())>1048576
        or type(boot_id) is not str or len(boot_id.encode())>64):
        raise ValueError('HOST_CAPACITY_INVALID')
    memory={}
    for line in meminfo.splitlines():
        if line.split(':',1)[0] in {'MemTotal','MemAvailable'}:
            match=re.fullmatch(r'(MemTotal|MemAvailable):\s+([0-9]+) kB',line)
            if match is None or match[1] in memory:raise ValueError('HOST_CAPACITY_INVALID')
            memory[match[1]]=int(match[2])*1024
    if set(memory)!={'MemTotal','MemAvailable'}:raise ValueError('HOST_CAPACITY_INVALID')
    counters=None
    cpus=set()
    for line in stat.splitlines():
        fields=line.split()
        if not fields:continue
        if fields[0]=='cpu':
            if (counters is not None or not 9<=len(fields)<=11
                or any(re.fullmatch(r'[0-9]{1,19}',x) is None for x in fields[1:])):
                raise ValueError('HOST_CAPACITY_INVALID')
            # guest/guest_nice는 이미 user/nice에 포함되므로 중복 합산하지 않는다.
            counters=tuple(int(v) for v in fields[1:9])
        elif re.fullmatch(r'cpu[0-9]+',fields[0]):
            if fields[0] in cpus:raise ValueError('HOST_CAPACITY_INVALID')
            cpus.add(fields[0])
    if counters is None or not cpus:raise ValueError('HOST_CAPACITY_INVALID')
    boot=boot_id.strip()
    usage=None
    if previous is not None:
        if type(previous) is not HostCapacitySample:raise ValueError('HOST_CAPACITY_INVALID')
        previous.__post_init__()
        if _utc(observed_at)<=previous.observed_at:raise ValueError('HOST_CAPACITY_REPLAY')
        if previous.boot_id==boot:
            if sequence<=previous.sequence or any(n<o for n,o in zip(counters,previous.cpu_counters)):
                raise ValueError('HOST_CAPACITY_REPLAY')
            delta=tuple(n-o for n,o in zip(counters,previous.cpu_counters))
            total=sum(delta)
            if total>0:usage=((total-delta[3]-delta[4])*10000)//total
    return HostCapacitySample(HOST_REF,boot,sequence,observed_at,len(cpus)*1000,usage,
        memory['MemTotal'],memory['MemAvailable'],counters)


def host_sample_state(sample,*,received_at,now):
    _utc(now)
    if sample is None:
        if received_at is not None:raise ValueError('HOST_CAPACITY_INVALID')
        return 'unknown'
    if type(sample) is not HostCapacitySample:raise ValueError('HOST_CAPACITY_INVALID')
    sample.__post_init__();_utc(received_at)
    if sample.observed_at>received_at+timedelta(seconds=5) or received_at>now+timedelta(seconds=5):
        raise ValueError('HOST_CAPACITY_TIME_INVALID')
    return 'fresh' if now-sample.observed_at<timedelta(seconds=60) else 'stale'


def _read_fixed_text(path,limit):
    # 경로는 아래 고정 목록에서만 공급하며 HTTP/환경 경로를 받지 않는다.
    with Path(path).open('rb') as stream:
        data=stream.read(limit+1)
    if len(data)>limit:raise ValueError('HOST_CAPACITY_INPUT_TOO_LARGE')
    return data.decode('ascii',errors='strict')


def read_host_sample(*,observed_at,sequence,previous=None,read_text=_read_fixed_text):
    values=[]
    for path,limit in (('/proc/meminfo',65536),('/proc/stat',1048576),('/proc/sys/kernel/random/boot_id',64)):
        value=read_text(path,limit)
        if type(value) is not str or len(value.encode('utf-8'))>limit:
            raise ValueError('HOST_CAPACITY_INPUT_TOO_LARGE')
        values.append(value)
    return parse_host_sample(meminfo=values[0],stat=values[1],boot_id=values[2],
        observed_at=observed_at,sequence=sequence,previous=previous)


@dataclass(frozen=True,slots=True)
class ReceivedHostCapacitySample:
    sample:HostCapacitySample
    received_at:datetime

    def __post_init__(self):
        host_sample_state(self.sample,received_at=self.received_at,now=self.received_at)


class HostCapacityCollector:
    """기존 API 수명에서 tick을 호출한다. 자체 thread/재시도/연결·자격을 만들지 않는다."""
    def __init__(self,repository,*,clock,reader=read_host_sample,enabled=False):
        if type(enabled) is not bool:raise ValueError('HOST_CAPACITY_ENABLED_INVALID')
        self._repository,self._clock,self._reader,self.enabled=repository,clock,reader,enabled

    def tick(self):
        if not self.enabled:return None
        stored=self._repository.read_host_sample_for_collector()
        now=_utc(self._clock())
        previous=None if stored is None else stored.sample
        sequence=1 if previous is None else previous.sequence+1
        if stored is not None and host_sample_state(previous,received_at=stored.received_at,now=now)!='fresh':
            previous=None
        sample=self._reader(observed_at=now,sequence=sequence,previous=previous)
        return self._repository.record_host_sample(sample)


def project_host_resources(stored,*,now):
    _utc(now)
    if stored is not None and type(stored) is not ReceivedHostCapacitySample:
        raise ValueError('HOST_CAPACITY_INVALID')
    state=host_sample_state(None if stored is None else stored.sample,
        received_at=None if stored is None else stored.received_at,now=now)
    result=[]
    for key,unit in HOST_METRICS:
        value=None if stored is None else getattr(stored.sample,key)
        row_state='not_collected' if value is None else state
        if value is not None and value>9007199254740991:
            row_state='unavailable';value=None
        known=row_state in {'fresh','stale'}
        result.append(dict(worker_kind='server_host',metric_id=key,value=value,unit=unit,
            state=row_state,observed_at=stored.sample.observed_at.isoformat().replace('+00:00','Z') if known else None,
            received_at=stored.received_at.isoformat().replace('+00:00','Z') if known else None,
            coverage='server_host_observation',safe_code='TELEMETRY_'+row_state.upper()))
    return result
