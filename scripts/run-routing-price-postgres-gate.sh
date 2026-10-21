#!/usr/bin/env bash
# 가격 승인된 독립 자원 전용. main이 exact Git checkout에서 단계별 호출한다.
set -Eeuo pipefail
umask 077
readonly ROOT=/home/daon/deploy/daon-user-routing-price-qa-20261021
readonly PG=daon-user-routing-price-pg-20261021
readonly RUNNER=daon-user-routing-price-runner-20261021
readonly TAG=daon-user-routing-price-qa:20261021
readonly PG_IMAGE=sha256:75f6767185020459c7e2c3f88fb66f1bd2d9790c435bc91512497146c8bf8d7e
readonly BASE=python:3.14.3-slim@sha256:69f1ba39d930d9510073db2edf85af17f1552a5591b0d56c9e44524532271cae
readonly ISSUE=R3-PRICE-PG-20261021
readonly PRODUCT_BASE=16e81ce4caf2fdaf5ba4f84090ca34e8b792e929
readonly STATE="$ROOT/.routing-price-qa-state"
readonly PROBE="$ROOT/scripts/tests/routing-price-postgres-gate.py"
phase=${1:-}
[[ $# == 1 && $phase =~ ^(build|prepare|bootstrap|run|cleanup)$ ]] || exit 22
# 원문 Docker/SQL/DSN 오류는 사용자 stdout/stderr로 전파하지 않는다.
exec 2>/dev/null
safe() { printf '{"issue_id":"%s","stage":"%s","status":"%s"}\n' "$ISSUE" "$1" "$2"; }
[[ $(realpath -e "$ROOT") == "$ROOT" && $(pwd -P) == "$ROOT" && ! -L "$ROOT" ]] || { safe source FAIL; exit 22; }
[[ -z ${DOCKER_HOST:-} && -z ${DOCKER_CONTEXT:-} && $(docker context show) == default && $(docker context inspect default --format '{{.Endpoints.docker.Host}}') == unix:///var/run/docker.sock ]] || { safe docker_context FAIL; exit 22; }
revision=$(git rev-parse HEAD)
[[ $revision =~ ^[0-9a-f]{40}$ ]] && git merge-base --is-ancestor "$PRODUCT_BASE" "$revision" || exit 22
git diff --quiet && git diff --cached --quiet || { safe dirty FAIL; exit 22; }
# 도구 commit 이외 제품/계약/SQL은 승인 checkpoint와 같아야 한다.
while IFS= read -r changed; do
 case "$changed" in scripts/run-routing-price-postgres-gate.sh|scripts/tests/routing-price-postgres-gate.py|scripts/tests/test_routing_price_gate_preflight.py) ;; *) safe product_drift FAIL; exit 22 ;; esac
done < <(git diff --name-only "$PRODUCT_BASE" "$revision")
python3 - "$PROBE" "$ROOT" <<'PY'
import importlib.util,sys
s=importlib.util.spec_from_file_location('b3',sys.argv[1]);m=importlib.util.module_from_spec(s);s.loader.exec_module(m)
m.verify_sql(sys.argv[2])
import hashlib,subprocess
from pathlib import Path
for name,sha in m.HASHES.items():
 p='services/api/migrations/versions/'+name
 raw=subprocess.run(['git','show','HEAD:'+p],cwd=sys.argv[2],check=True,stdout=subprocess.PIPE).stdout
 assert hashlib.sha256(raw).hexdigest()==sha and (Path(sys.argv[2])/p).read_bytes()==raw
PY
load_state() {
 [[ -f "$STATE/state.json" && ! -L "$STATE" && ! -L "$STATE/state.json" && $(stat -c %u "$STATE") == "$(id -u)" && $(stat -c %a "$STATE/state.json") == 600 ]] || return 1
 mapfile -t values < <(python3 - "$STATE/state.json" "$revision" <<'PY'
import json,re,sys
v=json.load(open(sys.argv[1]));assert set(v)=={'revision','pg_id','runner_id','qa_image','pg_password','app_password'} and v['revision']==sys.argv[2]
assert re.fullmatch('sha256:[0-9a-f]{64}',v['qa_image'])
for k in ['pg_id','runner_id']:assert v[k]=='' or re.fullmatch('[0-9a-f]{64}',v[k])
for k in ['pg_password','app_password']:assert re.fullmatch('[0-9a-f]{64}',v[k])
for k in ['pg_id','runner_id','qa_image','pg_password','app_password']:print(v[k])
PY
 )
 [[ ${#values[@]} == 5 ]] || return 1
 pg_id=${values[0]};runner_id=${values[1]};qa_image=${values[2]};pg_password=${values[3]};app_password=${values[4]}
}
save_id() {
 python3 - "$STATE/state.json" "$1" "$2" <<'PY'
import json,os,re,sys
p,k,v=sys.argv[1:];assert k in {'pg_id','runner_id'} and re.fullmatch('[0-9a-f]{64}',v)
f=json.load(open(p));f[k]=v
with open(p,'w') as out:json.dump(f,out)
PY
}
verify_owned() {
 local kind=$1 cid=$2
 [[ $cid =~ ^[0-9a-f]{64}$ ]] || return 1
 docker inspect "$cid" | python3 -c 'import importlib.util,json,sys;s=importlib.util.spec_from_file_location("b3",sys.argv[1]);m=importlib.util.module_from_spec(s);s.loader.exec_module(m);v=json.load(sys.stdin);assert len(v)==1;m.verify_container(v[0],kind=sys.argv[2],revision=sys.argv[3],pg_id=sys.argv[4],qa_image=sys.argv[5])' "$PROBE" "$kind" "$revision" "$pg_id" "$qa_image"
}
cleanup() {
 local failed=0
 if [[ -d "$STATE" && ! -f "$STATE/state.json" ]]; then
  [[ ! -L "$STATE" && $(realpath -e "$STATE") == "$ROOT/.routing-price-qa-state" && $(stat -c %u "$STATE") == "$(id -u)" ]] || return 1
  ! docker inspect "$PG" >/dev/null 2>&1 && ! docker inspect "$RUNNER" >/dev/null 2>&1 || { safe cleanup_partial FAIL; return 1; }
  if docker image inspect "$TAG" >/dev/null 2>&1; then
   [[ -f "$STATE/image-id" && ! -L "$STATE/image-id" && $(cat "$STATE/image-id") == "$(docker image inspect "$TAG" --format '{{.Id}}')" && $(docker image inspect "$TAG" --format '{{index .Config.Labels "io.daon.qa.issue"}}') == "$ISSUE" && $(docker image inspect "$TAG" --format '{{index .Config.Labels "io.daon.qa.revision"}}') == "$revision" ]] || { safe cleanup_partial_image FAIL; return 1; }
   docker image rm "$TAG" >/dev/null || return 1
  fi
  ! find "$STATE" -type l -print -quit | grep -q . || return 1
  rm -rf -- "$STATE"
 fi
 if [[ -d "$STATE" ]]; then
  load_state || { safe cleanup_state FAIL; return 1; }
  for kind in runner pg; do
   if [[ $kind == runner ]]; then cid=$runner_id; else cid=$pg_id; fi
   if [[ -n $cid ]]; then
    if docker inspect "$cid" >/dev/null 2>&1; then
     verify_owned "$kind" "$cid" && docker rm -f "$cid" >/dev/null || failed=1
    fi
    docker inspect "$cid" >/dev/null 2>&1 && failed=1
   fi
  done
  if docker image inspect "$TAG" >/dev/null 2>&1; then
   local current
   current=$(docker image inspect "$TAG" --format '{{.Id}}')
   [[ $current == "$qa_image" && $(docker image inspect "$TAG" --format '{{index .Config.Labels "io.daon.qa.issue"}}') == "$ISSUE" && $(docker image inspect "$TAG" --format '{{index .Config.Labels "io.daon.qa.revision"}}') == "$revision" ]] && docker image rm "$TAG" >/dev/null || failed=1
  fi
  docker image inspect "$TAG" >/dev/null 2>&1 && failed=1
  if [[ $failed == 0 ]]; then
   # 지정 state 내부만 회수. source checkout 자체는 main이 Git 보존 확인 후 정리한다.
   [[ $(realpath -e "$STATE") == "$ROOT/.routing-price-qa-state" ]] && ! find "$STATE" -type l -print -quit | grep -q . || return 1
   rm -rf -- "$STATE"
  fi
 fi
 [[ $failed == 0 ]] && safe cleanup PASS || { safe cleanup FAIL; return 1; }
}
on_failure() { local status=$?; trap - ERR INT TERM; safe "$phase" FAIL; cleanup || true; exit "$status"; }
trap on_failure ERR
trap 'false' INT TERM
if [[ $phase == cleanup ]]; then cleanup; exit; fi
if [[ $phase == build ]]; then
 if [[ -e "$STATE" ]] || docker image inspect "$TAG" >/dev/null 2>&1 || docker inspect "$PG" >/dev/null 2>&1 || docker inspect "$RUNNER" >/dev/null 2>&1; then safe preexisting_resource FAIL; exit 23; fi
 [[ $(awk '/MemAvailable:/{print $2}' /proc/meminfo) -ge 2097152 ]]
 [[ $(docker image inspect "$PG_IMAGE" --format '{{.Id}}') == "$PG_IMAGE" ]]
 mkdir -m 700 "$STATE" "$STATE/context"
 git archive "$revision" pyproject.toml uv.lock services/api services/local-service/pyproject.toml scripts/tests/routing-price-postgres-gate.py scripts/run-routing-price-postgres-gate.sh | tar -x -C "$STATE/context"
 cat > "$STATE/context/Dockerfile" <<EOF
FROM $BASE
LABEL io.daon.qa.issue="$ISSUE" io.daon.qa.revision="$revision"
WORKDIR /qa
COPY pyproject.toml uv.lock /qa/
COPY services/local-service/pyproject.toml /qa/services/local-service/pyproject.toml
COPY services/api /qa/services/api
COPY scripts/tests/routing-price-postgres-gate.py /qa/scripts/tests/routing-price-postgres-gate.py
COPY scripts/run-routing-price-postgres-gate.sh /qa/scripts/run-routing-price-postgres-gate.sh
RUN chmod -R a+rX /qa && pip install --no-cache-dir uv==0.11.2 && cd /qa && uv sync --frozen --package daon-user-api --group dev --no-install-project && chmod -R a+rX /qa
ENV PATH="/qa/.venv/bin:/usr/local/bin:/usr/local/sbin:/usr/sbin:/usr/bin:/sbin:/bin" PYTHONPATH=/qa/services/api/src PYTHONDONTWRITEBYTECODE=1
CMD ["sleep","infinity"]
EOF
 docker build --platform linux/amd64 --iidfile "$STATE/image-id" --tag "$TAG" "$STATE/context" >"$STATE/build.log" 2>&1
 qa_image=$(docker image inspect "$TAG" --format '{{.Id}}')
 python3 - "$STATE/state.json" "$revision" "$qa_image" <<'PY'
import json,secrets,sys
with open(sys.argv[1],'x') as f:json.dump(dict(revision=sys.argv[2],qa_image=sys.argv[3],pg_id='',runner_id='',pg_password=secrets.token_hex(32),app_password=secrets.token_hex(32)),f)
PY
 chmod 600 "$STATE/state.json"
 rm -rf -- "$STATE/context" "$STATE/build.log"
 printf '{"issue_id":"%s","stage":"build","status":"PASS","revision":"%s","qa_image":"%s"}
' "$ISSUE" "$revision" "$qa_image"
 exit
fi
load_state
if [[ $phase == prepare ]]; then
 if [[ -n $pg_id || -n $runner_id ]] || docker inspect "$PG" >/dev/null 2>&1 || docker inspect "$RUNNER" >/dev/null 2>&1; then safe preexisting_resource FAIL; exit 23; fi
 [[ $(awk '/MemAvailable:/{print $2}' /proc/meminfo) -ge 2097152 ]]
 # PG Config.Env의 일회 비밀번호는 이 격리 컨테이너 회수 때 함께 소멸한다.
 pg_id=$(docker create --name "$PG" --label "io.daon.qa.issue=$ISSUE" --label "io.daon.qa.revision=$revision" --network none --memory 1g --cpus 1 --tmpfs /var/lib/postgresql/data:rw,noexec,nosuid,size=536870912 -e "POSTGRES_PASSWORD=$pg_password" "$PG_IMAGE")
 save_id pg_id "$pg_id";verify_owned pg "$pg_id";docker start "$pg_id" >/dev/null
 for i in {1..45}; do docker exec "$pg_id" pg_isready -U postgres -d postgres >/dev/null && break; sleep 1; done
 docker exec "$pg_id" pg_isready -U postgres -d postgres >/dev/null
 runner_id=$(docker create --name "$RUNNER" --label "io.daon.qa.issue=$ISSUE" --label "io.daon.qa.revision=$revision" --network "container:$pg_id" --memory 512m --cpus 1 --read-only --tmpfs /tmp:rw,noexec,nosuid,size=134217728 --user 65534:65534 "$qa_image")
 save_id runner_id "$runner_id";verify_owned runner "$runner_id";docker start "$runner_id" >/dev/null
 docker exec "$runner_id" python -c 'import sys,importlib.metadata as m;assert sys.version_info[:3]==(3,14,3);assert m.version("psycopg")=="3.3.4";assert m.version("alembic")=="1.18.5";assert m.version("httpx")=="0.28.1";from httpx import ASGITransport;import sqlite3;sys.path.insert(0,"/qa/services/api/tests");from test_identity_support import create_service,FakeVerifiedOidcProvider,MutableClock;from daon_user_api.runtime import create_app;import tomllib,re;d=tomllib.load(open("/qa/services/api/pyproject.toml","rb"));assert all(m.version(x.split("==")[0].split("[")[0])==x.split("==")[1] for x in d["project"]["dependencies"])'
 printf '{"issue_id":"%s","stage":"prepare","status":"PASS","pg_id":"%s","runner_id":"%s","qa_image":"%s"}
' "$ISSUE" "$pg_id" "$runner_id" "$qa_image"
 exit
fi
verify_owned pg "$pg_id";verify_owned runner "$runner_id"
if [[ $phase == bootstrap ]]; then action=bootstrap; else action=probe; fi
python3 - "$STATE/state.json" <<'PY' | docker exec -i "$runner_id" python /qa/scripts/tests/routing-price-postgres-gate.py "$action"
import json,sys
v=json.load(open(sys.argv[1]));print(json.dumps({k:v[k] for k in ['pg_password','app_password']}))
PY
if [[ $phase == run ]]; then cleanup; fi
