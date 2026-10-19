"""고정 Linux /proc 수집과 최신성. 실제 WSL/DB/화면 증거는 별도다."""
from datetime import datetime,timedelta,timezone
from dataclasses import replace
import pytest

from daon_user_api import operations_host_capacity as capacity

NOW=datetime(2026,10,20,tzinfo=timezone.utc)
BOOT='f49df042-667b-4e10-96c4-16431f32b760'
MEM='MemTotal:       1048576 kB\nMemFree: 1 kB\nMemAvailable:    524288 kB\n'
STAT1='cpu 100 0 50 800 50 0 0 0 0 0\ncpu0 50 0 25 400 25 0 0 0 0 0\ncpu1 50 0 25 400 25 0 0 0 0 0\n'
STAT2='cpu 150 0 100 900 100 0 0 0 0 0\ncpu0 75 0 50 450 50 0 0 0 0 0\ncpu1 75 0 50 450 50 0 0 0 0 0\n'

def sample(*,previous=None,stat=STAT1,boot=BOOT,at=NOW,sequence=1):
    return capacity.parse_host_sample(meminfo=MEM,stat=stat,boot_id=boot,
        observed_at=at,sequence=sequence,previous=previous)

def test_linux_meminfo_uses_memavailable_bytes_not_model_gpu_guess():
    row=sample()
    assert row.host_ref=='wsl-server-daon-user'
    assert (row.memory_total_bytes,row.memory_available_bytes)==(1073741824,536870912)
    assert row.cpu_logical_millicores==2000
    assert not hasattr(row,'gpu_available_bytes')

def test_cpu_delta_requires_same_boot_and_monotonic_counters():
    first=sample()
    second=sample(previous=first,stat=STAT2,at=NOW+timedelta(seconds=30),sequence=2)
    assert second.cpu_usage_basis_points==4000
    changed=sample(previous=first,stat=STAT2,boot='0fa29520-bb01-4225-bcca-f0e5ec6c3776',sequence=1,at=NOW+timedelta(seconds=30))
    assert changed.cpu_usage_basis_points is None
    for stat,seq,at in ((STAT1.replace('100 0 50','1 0 1',1),2,NOW+timedelta(seconds=30)),
        (STAT2,1,NOW+timedelta(seconds=30)),(STAT2,2,NOW)):
        with pytest.raises(ValueError):sample(previous=first,stat=stat,sequence=seq,at=at)

def test_old_observed_at_is_stale_even_with_new_received_at():
    row=sample()
    assert capacity.host_sample_state(row,received_at=NOW,now=NOW+timedelta(seconds=59))=='fresh'
    assert capacity.host_sample_state(row,received_at=NOW+timedelta(seconds=70),now=NOW+timedelta(seconds=70))=='stale'
    assert capacity.host_sample_state(None,received_at=None,now=NOW)=='unknown'

def test_future_bool_wrong_units_or_other_host_are_rejected():
    for changes in ({'sequence':True},{'host_ref':'other-host'},{'memory_total_bytes':True},
        {'cpu_usage_basis_points':10001},{'memory_available_bytes':1073741825}):
        with pytest.raises(ValueError):replace(sample(),**changes)
    with pytest.raises(ValueError):
        capacity.host_sample_state(sample(at=NOW+timedelta(seconds=6)),received_at=NOW,now=NOW)
    for mem in (MEM.replace(' kB',' MB'),MEM+'MemAvailable: 1 kB\n',MEM.replace('MemAvailable','UnknownAvailable')):
        with pytest.raises(ValueError):capacity.parse_host_sample(meminfo=mem,stat=STAT1,boot_id=BOOT,observed_at=NOW,sequence=1)

def test_first_cpu_sample_is_unknown_and_ram_can_be_reported():
    row=sample()
    assert row.cpu_usage_basis_points is None and row.memory_available_bytes==536870912

def test_reader_only_reads_fixed_proc_paths_with_size_bound():
    seen=[]
    files={'/proc/meminfo':MEM,'/proc/stat':STAT1,'/proc/sys/kernel/random/boot_id':BOOT+'\n'}
    def read(path,limit):seen.append((path,limit));return files[path]
    row=capacity.read_host_sample(read_text=read,observed_at=NOW,sequence=1)
    assert row.memory_available_bytes==536870912
    assert seen==[('/proc/meminfo',65536),('/proc/stat',1048576),('/proc/sys/kernel/random/boot_id',64)]
    with pytest.raises(ValueError):
        capacity.read_host_sample(read_text=lambda path,limit:'x'*(limit+1),observed_at=NOW,sequence=1)


def test_collector_resumes_database_sequence_after_restart_without_replaying_or_reading_when_disabled():
    from types import SimpleNamespace
    class Repository:
        def __init__(self):self.row=SimpleNamespace(sample=sample(),received_at=NOW);self.writes=[];self.reads=0
        def read_host_sample_for_collector(self):self.reads+=1;return self.row
        def record_host_sample(self,row):
            assert row.sequence>self.row.sample.sequence
            self.writes.append(row);self.row=SimpleNamespace(sample=row,received_at=row.observed_at);return self.row
    repository=Repository()
    def reader(**kwargs):return sample(previous=kwargs['previous'],sequence=kwargs['sequence'],at=kwargs['observed_at'],stat=STAT2)
    collector=capacity.HostCapacityCollector(repository,clock=lambda:NOW+timedelta(seconds=30),reader=reader,enabled=False)
    assert collector.tick() is None and repository.writes==[] and repository.reads==0
    capacity.HostCapacityCollector(repository,clock=lambda:NOW+timedelta(seconds=30),reader=reader,enabled=True).tick()
    assert repository.writes[-1].sequence==2 and repository.writes[-1].cpu_usage_basis_points==4000
    capacity.HostCapacityCollector(repository,clock=lambda:NOW+timedelta(seconds=60),reader=reader,enabled=True).tick()
    assert repository.writes[-1].sequence==3


def test_collector_keeps_sequence_but_does_not_average_cpu_across_stale_gap():
    from types import SimpleNamespace
    class Repository:
        def read_host_sample_for_collector(self):return SimpleNamespace(sample=sample(),received_at=NOW)
        def record_host_sample(self,row):return row
    def reader(**kwargs):return sample(previous=kwargs['previous'],sequence=kwargs['sequence'],at=kwargs['observed_at'],stat=STAT2)
    row=capacity.HostCapacityCollector(Repository(),clock=lambda:NOW+timedelta(seconds=61),reader=reader,enabled=True).tick()
    assert row.sequence==2 and row.cpu_usage_basis_points is None


def test_host_repository_writes_exact_sample_and_authorizes_ui_read_before_and_after():
    from contextlib import contextmanager
    from dataclasses import asdict
    from types import SimpleNamespace
    from daon_user_api.operations_telemetry_postgres import PostgresHostCapacityRepository
    calls=[]
    row={**asdict(sample()),'received_at':NOW};row['cpu_counters']=list(row['cpu_counters'])
    class Connection:
        def execute(self,query,args=()):calls.append((query,args));return self
        def fetchone(self):return row
        def fetchall(self):return [row]
    class Store:
        @contextmanager
        def _transaction(self):yield Connection()
        def _authorize(self,connection,context):calls.append(('authorize',context))
    repository=PostgresHostCapacityRepository(Store())
    stored=repository.record_host_sample(sample())
    assert stored.sample==sample() and stored.received_at==NOW
    assert calls[0][0].startswith('SELECT * FROM public.record_operations_host_capacity(')
    assert calls[0][1]==('wsl-server-daon-user',BOOT,1,NOW,2000,None,1073741824,536870912,[100,0,50,800,50,0,0,0])
    calls.clear()
    context=SimpleNamespace(tenant_id='tenant-qa',workspace_id='workspace-qa',actor_id='usr-qa')
    assert repository.read_host_sample(context)==stored
    assert calls[3]==('authorize',context) and calls[-1]==calls[3]
    assert calls[4][0]=='SELECT * FROM public.read_operations_host_capacity()'
