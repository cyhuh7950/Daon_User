"""운영자만 사용하는 적용 전/후 실제 증거 수신기. 배포 명령/HTTP 쓰기/과거 합성0.

호출 전에 승인된 운영자 절차가 이 프로세스를 시작하고 READY 뒤 별도로 배포한다.
stdin의 APPLIED는 확인 재개 신호일 뿐 성공 증거가 아니다. 300초 내 실제 새
container/start/image와 Git 및 image 내부 source blob, smoke를 다시 검사한다.
전 증거는 이 프로세스 메모리에서만 유지하며 종료/실패 시 폐기한다.
"""
from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import re
import selectors
import subprocess
import sys
import time
from datetime import datetime,timezone

from daon_user_api.operations_telemetry import DeploymentReceipt,TelemetryUnavailable,_id
from daon_user_api.operations_telemetry_postgres import record_deployment_in_operator_transaction

_ROOT=Path(__file__).resolve().parents[2]
_COMPOSE=('docker','compose','-f',str(_ROOT/'deploy/daon-user/compose.yaml'))
_INSPECT='{{json .Id}} {{json .Image}} {{json .State.Running}} {{json .State.StartedAt}}'
_IMAGE_INSPECT='{{json .Id}} {{json (index .Config.Labels "org.opencontainers.image.revision")}}'
_SMOKE="import http.client; c=http.client.HTTPConnection('127.0.0.1',8000,timeout=2); c.request('GET','/health/ready'); r=c.getresponse(); ok=r.status==200; r.close(); c.close(); assert ok; print('READY')"
_BLOBS="""import os,hashlib,json,stat
result={}
for root,dirs,files in os.walk('/app/src',followlinks=False):
 dirs[:]=sorted(d for d in dirs if d!='__pycache__')
 for name in sorted(files):
  if name.endswith('.pyc'):continue
  p=os.path.join(root,name);s=os.lstat(p)
  if not stat.S_ISREG(s.st_mode) or s.st_nlink!=1 or s.st_size>16*1024*1024:raise RuntimeError('SOURCE_INVALID')
  h=hashlib.sha1(b'blob '+str(s.st_size).encode()+b'\\0')
  with open(p,'rb') as f:
   while part:=f.read(65536):h.update(part)
  result['services/api/src/'+os.path.relpath(p,'/app/src')]=h.hexdigest()
  if len(result)>4096:raise RuntimeError('SOURCE_INVALID')
out=json.dumps(result,separators=(',',':'))
if len(out)>1024*1024:raise RuntimeError('SOURCE_INVALID')
print(out)
"""


class FixedOperatorObserver:
    """서버 고정 api service만. 사용자 command/URL/Secret argv 입력0."""
    def __init__(self,*,run=None):self._runner=run or subprocess.run

    def _run(self,args):
        try:
            result=self._runner(args,cwd=_ROOT,shell=False,capture_output=True,timeout=10,check=False)
            if result.returncode!=0 or type(result.stdout) is not bytes or len(result.stdout)>1024*1024:
                raise TelemetryUnavailable()
            return result.stdout.decode('utf-8').strip()
        except (OSError,ValueError,subprocess.SubprocessError,UnicodeError):raise TelemetryUnavailable() from None

    @staticmethod
    def _values(raw,count):
        values=[];decoder=json.JSONDecoder();remaining=raw
        while remaining:
            value,end=decoder.raw_decode(remaining);values.append(value);remaining=remaining[end:].lstrip()
        if len(values)!=count:raise ValueError()
        return values

    def _identity(self):
        container=self._run((*_COMPOSE,'ps','-q','api'))
        if re.fullmatch('[a-f0-9]{64}',container) is None:raise TelemetryUnavailable()
        raw=self._run(('docker','inspect','--format',_INSPECT,container))
        # 고정 네 필드 외 inspect/environment는 요청/저장하지 않는다.
        try:
            identity,image,running,start=self._values(raw,4)
            started=datetime.fromisoformat(start.replace('Z','+00:00'))
            if (identity!=container or running is not True or type(image) is not str
                or re.fullmatch('sha256:[a-f0-9]{64}',image) is None or started.utcoffset().total_seconds()!=0):
                raise ValueError()
            image_id,revision=self._values(self._run(('docker','image','inspect','--format',_IMAGE_INSPECT,image)),2)
            if image_id!=image or type(revision) is not str or re.fullmatch('[a-f0-9]{40}',revision) is None:
                raise ValueError()
            if self._run((*_COMPOSE,'ps','-q','api'))!=container:raise ValueError()
            return dict(code_revision=revision,image_digest=image,container_id=container,started_at=started)
        except (ValueError,TypeError,AttributeError,KeyError):raise TelemetryUnavailable() from None

    def confirm(self,observation):
        if self._identity()!=observation:raise TelemetryUnavailable()

    def inspect(self):
        observation=self._identity();revision=observation['code_revision']
        try:
            # label만 신뢰하지 않는다. HEAD가 새 SHA여도 구 image의 정확 commit/blob을 읽는다.
            if self._run(('git','cat-file','-t',revision))!='commit':raise ValueError()
            expected={}
            for line in self._run(('git','ls-tree','-r','--full-tree',revision,'services/api/src')).splitlines():
                meta,path=line.split('\t',1);mode,kind,digest=meta.split()
                if mode!='100644' or kind!='blob':raise ValueError()
                expected[path]=digest
            actual=json.loads(self._run(('docker','exec',observation['container_id'],'python','-c',_BLOBS)))
            if not expected or actual!=expected:raise ValueError()
            self.confirm(observation)
            return observation
        except (ValueError,TypeError,AttributeError,KeyError):raise TelemetryUnavailable() from None

    def smoke(self,container):
        if re.fullmatch('[a-f0-9]{64}',container) is None:raise TelemetryUnavailable()
        return self._run(('docker','exec',container,'python','-c',_SMOKE))=='READY'


def record_operation(operation_id,operation,*,expected_revision,expected_image,observer,wait_applied,sink,clock=None,monotonic=None):
    _id(operation_id)
    if (operation not in {'deploy','rollback'} or type(expected_revision) is not str
        or re.fullmatch('[a-f0-9]{40}',expected_revision) is None or type(expected_image) is not str
        or re.fullmatch('sha256:[a-f0-9]{64}',expected_image) is None):raise TelemetryUnavailable()
    clock=clock or (lambda:datetime.now(timezone.utc));monotonic=monotonic or time.monotonic
    began=monotonic();before=observer.inspect();before_time=clock()
    wait_applied(300)
    if not 0<=monotonic()-began<=300:raise TelemetryUnavailable()
    after=observer.inspect();when=clock()
    if (after['code_revision']!=expected_revision or after['image_digest']!=expected_image
        or after['container_id']==before['container_id'] and after['started_at']==before['started_at']
        or after['started_at']<=before['started_at'] or after['started_at']<before_time or when<after['started_at']
        or operation=='rollback' and after['image_digest']==before['image_digest']):raise TelemetryUnavailable()
    state='succeeded'
    try:
        if observer.smoke(after['container_id']) is not True:state='uncertain'
    except Exception:state='uncertain'
    observer.confirm(after)
    when=clock()
    if not 0<=monotonic()-began<=300 or when<after['started_at']:raise TelemetryUnavailable()
    receipt=DeploymentReceipt(operation_id,operation,after['code_revision'],after['image_digest'],before['image_digest'],state,when,
        ('DEPLOYMENT_' if operation=='deploy' else 'ROLLBACK_')+state.upper())
    sink(receipt)
    return receipt


def _wait_applied(seconds):
    if os.name!='posix':raise TelemetryUnavailable()
    # stdin은 운영자 절차의 재개 신호만 받는다. 실행할 command/SQL은 받지 않는다.
    with selectors.DefaultSelector() as selector:
        selector.register(sys.stdin,selectors.EVENT_READ)
        if not selector.select(seconds) or os.read(sys.stdin.fileno(),64)!=b'APPLIED\n':raise TelemetryUnavailable()


def main():
    parser=argparse.ArgumentParser();parser.add_argument('operation_id');parser.add_argument('operation',choices=('deploy','rollback'))
    parser.add_argument('--expected-revision',required=True);parser.add_argument('--expected-image',required=True)
    args=parser.parse_args()
    try:
        if os.name!='posix':raise TelemetryUnavailable()
        # 기존 DSN 참조만 소비하며 새 Secret 생성/파일/값 출력0. app 접속은 SQL probe에서 거부한다.
        dsn=os.environ.get('DAON_CLOUD_DATABASE_DSN')
        if not dsn:raise TelemetryUnavailable()
        import psycopg
        def wait(seconds):
            print('OPERATION_RECEIPT_READY',flush=True);_wait_applied(seconds)
        def sink(receipt):
            try:
                with psycopg.connect(dsn,connect_timeout=2) as connection,connection.transaction():
                    connection.execute("SET LOCAL statement_timeout='2000ms'")
                    connection.execute("SET LOCAL lock_timeout='500ms'")
                    record_deployment_in_operator_transaction(connection,receipt)
            except Exception:raise TelemetryUnavailable() from None
        record_operation(args.operation_id,args.operation,expected_revision=args.expected_revision,expected_image=args.expected_image,
            observer=FixedOperatorObserver(),wait_applied=wait,sink=sink)
        print('OPERATION_RECEIPT_RECORDED');return 0
    except Exception:
        print('OPERATION_RECEIPT_UNAVAILABLE',file=sys.stderr);return 1


if __name__=='__main__':raise SystemExit(main())
