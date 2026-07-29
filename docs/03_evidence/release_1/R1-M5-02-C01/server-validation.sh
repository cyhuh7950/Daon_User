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
bucket="daon-r1-m5-02-c01-$short"
db_name="daon_r1_m5_02_c01"
runner_image="ghcr.io/astral-sh/uv:0.11.2-python3.14-trixie"

test "$(cd "$checkout" && git rev-parse HEAD)" = "$sha"
test -z "$(cd "$checkout" && git status --porcelain)"
if [[ ! -f "$runtime/secrets/app_password" ]]; then
  openssl rand -hex 32 > "$runtime/secrets/app_password"
  chmod 600 "$runtime/secrets/app_password"
fi

runner() {
  docker run --rm --network "$network" \
    --mount "type=bind,src=$checkout,dst=/workspace,readonly" \
    --mount "type=bind,src=$runtime/secrets,dst=/run/daon-secrets,readonly" \
    --workdir /workspace \
    -e "DAON_DB_EXPECTED_NAME=$db_name" \
    -e "DAON_OBJECT_STORAGE_ENDPOINT=object-storage:9000" \
    -e "DAON_OBJECT_STORAGE_BUCKET=$bucket" \
    -e "DAON_OBJECT_ACCESS_KEY_FILE=/run/daon-secrets/object_access_key" \
    -e "DAON_OBJECT_SECRET_KEY_FILE=/run/daon-secrets/object_secret_key" \
    -e "DAON_OBJECT_STORAGE_SECURE=false" \
    -e "UV_PROJECT_ENVIRONMENT=/tmp/venv" -e "UV_CACHE_DIR=/tmp/uv-cache" \
    "$runner_image" sh -ceu '
      owner_password=$(cat /run/daon-secrets/db_owner_password)
      app_password=$(cat /run/daon-secrets/app_password)
      export DAON_DB_MIGRATION_DSN="postgresql://daon_owner:${owner_password}@database:5432/daon_r1_m5_02_c01"
      export DAON_DB_APP_PASSWORD="$app_password"
      export DAON_CLOUD_DATABASE_DSN="postgresql://daon_app:${app_password}@database:5432/daon_r1_m5_02_c01"
      export DAON_TEST_POSTGRES_DSN="$DAON_CLOUD_DATABASE_DSN"
      export DAON_TEST_S3_ENDPOINT="$DAON_OBJECT_STORAGE_ENDPOINT"
      export DAON_TEST_S3_ACCESS_KEY=$(cat /run/daon-secrets/object_access_key)
      export DAON_TEST_S3_SECRET_KEY=$(cat /run/daon-secrets/object_secret_key)
      export DAON_TEST_S3_BUCKET="$DAON_OBJECT_STORAGE_BUCKET"
      export DAON_TEST_S3_SECURE=false
      export PYTHONPATH=/workspace/services/api/src:/workspace/services/api/tests
      "$@"
    ' -- "$@"
}

echo "SERVER_BINDING sha=$sha arch=$(uname -m)"
docker exec "$database" pg_dump -U daon_owner -d "$db_name" > "$runtime/pre-migration.sql"
runner uv run --project services/api --frozen python -m daon_user_api.cloud_admin preflight
runner uv run --project services/api --frozen alembic -c services/api/alembic.ini upgrade 0002
runner uv run --project services/api --frozen alembic -c services/api/alembic.ini upgrade head
runner uv run --project services/api --frozen python -m daon_user_api.cloud_admin bootstrap-role
runner uv run --project services/api --frozen python -c '
import os
from minio import Minio
client = Minio(os.environ["DAON_TEST_S3_ENDPOINT"], access_key=os.environ["DAON_TEST_S3_ACCESS_KEY"], secret_key=os.environ["DAON_TEST_S3_SECRET_KEY"], secure=False)
bucket = os.environ["DAON_TEST_S3_BUCKET"]
if not client.bucket_exists(bucket): client.make_bucket(bucket)
print("BUCKET_READY=true")
'
docker exec "$database" pg_dump -U daon_owner -d "$db_name" -Fc > "$runtime/post-migration.dump"

runner uv run --project services/api --frozen python -m unittest discover -s services/api/tests -p test_object_queue.py -v
runner uv run --project services/api --frozen python -m unittest discover -s services/api/tests -p test_cloud_storage.py -v
runner uv run --project services/api --frozen python -m unittest discover -s services/api/tests -p test_runtime_http.py -v

runner uv run --project services/api --frozen alembic -c services/api/alembic.ini downgrade base
docker exec "$database" dropdb -U daon_owner "$db_name"
docker exec "$database" createdb -U daon_owner "$db_name"
docker exec -i "$database" pg_restore -U daon_owner -d "$db_name" < "$runtime/post-migration.dump"
runner uv run --project services/api --frozen alembic -c services/api/alembic.ini upgrade head
runner uv run --project services/api --frozen python -m daon_user_api.cloud_admin preflight
runner uv run --project services/api --frozen python -m unittest discover -s services/api/tests -p test_object_queue.py -v

echo "SERVER_SUITE_PASS object=16/16 cloud=11/11 runtime=15/15 restore_object=16/16"
