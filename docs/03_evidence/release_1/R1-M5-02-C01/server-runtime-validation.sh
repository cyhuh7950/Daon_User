#!/usr/bin/env bash
set -euo pipefail

sha="f3da3c78a3fce3abecf94dff932df3cdb66d53d3"
short="f3da3c7"
root="/home/ubuntu/deploy/daon-user/R1-M5-02-C01"
checkout="$root/$sha"
runtime="$root/runtime-$short"
project="daon_r1_m5_02_c01_$short"
network="${project}_network"
database="${project}-database-1"
object_storage="${project}-object-storage-1"
api="${project}_api"
worker="${project}_worker"
bucket="daon-r1-m5-02-c01-$short"
image="ghcr.io/astral-sh/uv:0.11.2-python3.14-trixie"

test "$(cd "$checkout" && git rev-parse HEAD)" = "$sha"

common=(
  --network "$network"
  --mount "type=bind,src=$checkout,dst=/workspace,readonly"
  --mount "type=bind,src=$runtime/secrets,dst=/run/daon-secrets,readonly"
  -e UV_PROJECT_ENVIRONMENT=/tmp/venv -e UV_CACHE_DIR=/tmp/uv-cache
  -e DAON_OBJECT_STORAGE_ENDPOINT=object-storage:9000 -e "DAON_OBJECT_STORAGE_BUCKET=$bucket"
  -e DAON_OBJECT_ACCESS_KEY_FILE=/run/daon-secrets/object_access_key
  -e DAON_OBJECT_SECRET_KEY_FILE=/run/daon-secrets/object_secret_key -e DAON_OBJECT_STORAGE_SECURE=false
)

docker run -d --name "$api" --label "com.docker.compose.project=$project" "${common[@]}" \
  -e DAON_RUNTIME_PROFILE=production -e DAON_API_BIND_HOST=0.0.0.0 -e DAON_API_PORT=8000 \
  -e DAON_API_DATABASE_PATH=/tmp/runtime.sqlite3 \
  -e DAON_PUBLIC_GATEWAY_URL=https://gateway.example.invalid -e DAON_TRUSTED_PROXY_IPS=127.0.0.1 \
  "$image" sh -ceu '
    password=$(cat /run/daon-secrets/app_password)
    export DAON_CLOUD_DATABASE_DSN="postgresql://daon_app:${password}@database:5432/daon_r1_m5_02_c01"
    export PYTHONPATH=/workspace/services/api/src
    cd /workspace/services/api
    exec uv run --frozen python -m daon_user_api
  ' >/dev/null

ready_200() {
  docker exec "$api" python -c 'import urllib.request; r=urllib.request.Request("http://127.0.0.1:8000/health/ready", headers={"X-Forwarded-Proto":"https"}); assert urllib.request.urlopen(r, timeout=3).status == 200' 2>/dev/null
}
for _ in $(seq 1 60); do ready_200 && break; sleep 2; done
ready_200
docker exec "$api" python -c 'import urllib.request; r=urllib.request.Request("http://127.0.0.1:8000/health/live", headers={"X-Forwarded-Proto":"https"}); assert urllib.request.urlopen(r, timeout=3).status == 200'
echo "API_INITIAL live=200 ready=200"

docker stop "$object_storage" >/dev/null
docker exec "$api" python -c '
import urllib.error, urllib.request
headers={"X-Forwarded-Proto":"https"}
assert urllib.request.urlopen(urllib.request.Request("http://127.0.0.1:8000/health/live", headers=headers), timeout=3).status == 200
try: urllib.request.urlopen(urllib.request.Request("http://127.0.0.1:8000/health/ready", headers=headers), timeout=15)
except urllib.error.HTTPError as error: assert error.code == 503
else: raise AssertionError("ready stayed healthy")
'
echo "OBJECT_OUTAGE live=200 ready=503"
docker start "$object_storage" >/dev/null
for _ in $(seq 1 30); do ready_200 && break; sleep 2; done
ready_200
echo "OBJECT_RECOVERY ready=200"

docker stop "$database" >/dev/null
docker exec "$api" python -c '
import urllib.error, urllib.request
headers={"X-Forwarded-Proto":"https"}
assert urllib.request.urlopen(urllib.request.Request("http://127.0.0.1:8000/health/live", headers=headers), timeout=3).status == 200
try: urllib.request.urlopen(urllib.request.Request("http://127.0.0.1:8000/health/ready", headers=headers), timeout=15)
except urllib.error.HTTPError as error: assert error.code == 503
else: raise AssertionError("ready stayed healthy")
'
echo "DATABASE_OUTAGE live=200 ready=503"
docker start "$database" >/dev/null
for _ in $(seq 1 60); do
  [[ $(docker inspect -f '{{.State.Health.Status}}' "$database") == healthy ]] && ready_200 && break
  sleep 2
done
ready_200
echo "DATABASE_RECOVERY ready=200"

docker run -d --name "$worker" --label "com.docker.compose.project=$project" "${common[@]}" \
  -e DAON_WORKER_TENANT_ID=tenant-worker-c01 -e DAON_WORKER_WORKSPACE_ID=workspace-worker-c01 \
  -e DAON_WORKER_ACTOR_ID=actor-worker-c01 \
  "$image" sh -ceu '
    password=$(cat /run/daon-secrets/app_password)
    export DAON_CLOUD_DATABASE_DSN="postgresql://daon_app:${password}@database:5432/daon_r1_m5_02_c01"
    export PYTHONPATH=/workspace/services/api/src
    cd /workspace/services/api
    exec uv run --frozen python -m daon_user_api.object_worker
  ' >/dev/null
sleep 5
test "$(docker inspect -f '{{.State.Running}}' "$worker")" = true
docker stop --time 15 "$worker" >/dev/null
test "$(docker inspect -f '{{.State.ExitCode}}' "$worker")" = 0
echo "WORKER_SIGTERM exit=0"

docker stop --time 15 "$api" >/dev/null
test "$(docker inspect -f '{{.State.ExitCode}}' "$api")" = 0
echo "API_SIGTERM exit=0"
