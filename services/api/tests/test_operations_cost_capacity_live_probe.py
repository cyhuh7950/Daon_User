"""실제 DB가 없는 연결 안전 경계 시험. SQL/롤백 성공의 증거가 아니다."""
import pytest
from operations_cost_capacity_live_probe import run_probe


def test_autocommit_connection_is_rejected_before_any_sql_write():
    calls=[]
    class Connection:
        autocommit=True
        def execute(self,*args):calls.append('execute');raise AssertionError('SQL_WAS_EXECUTED')
        def rollback(self):calls.append('rollback')
        def close(self):calls.append('close')
    with pytest.raises(ValueError,match='PROBE_REQUIRES_NEW_NON_AUTOCOMMIT_CONNECTIONS'):
        run_probe(Connection,'CREATE TABLE must_not_be_created(x int)')
    assert 'execute' not in calls
    assert calls.count('close')==2


def test_cleanup_failure_on_one_connection_still_closes_the_other():
    calls=[]
    class Connection:
        autocommit=True
        def __init__(self,label):self.label=label
        def execute(self,*args):raise AssertionError('SQL_WAS_EXECUTED')
        def rollback(self):
            calls.append((self.label,'rollback'))
            if self.label=='blocker':raise OSError('CONTROLLED_CLEANUP_FAILURE')
        def close(self):calls.append((self.label,'close'))
    pool=iter((Connection('primary'),Connection('blocker')))
    with pytest.raises(OSError,match='CONTROLLED_CLEANUP_FAILURE'):
        run_probe(lambda:next(pool),'')
    assert calls==[('blocker','rollback'),('blocker','close'),('primary','rollback'),('primary','close')]
