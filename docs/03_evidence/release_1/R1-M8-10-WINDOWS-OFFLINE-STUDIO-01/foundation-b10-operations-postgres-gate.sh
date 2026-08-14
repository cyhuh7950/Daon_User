#!/usr/bin/env bash
set -euo pipefail
readonly DB="daon_b10_operations_it_20260815"
readonly PG_CONTAINER="local-postgres"
readonly API_ROOT="/mnt/c/Users/cyhuh/Desktop/D Driver/Project/Daon_User/services/api"
readonly UV="/home/daon/.local/bin/uv"
cleanup() { docker exec "$PG_CONTAINER" dropdb -U postgres --if-exists "$DB" >/dev/null 2>&1 || true; }
trap cleanup EXIT
cleanup
docker exec "$PG_CONTAINER" createdb -U postgres -T template0 "$DB"
PGPASSWORD="$(docker exec "$PG_CONTAINER" printenv POSTGRES_PASSWORD)"
export DAON_DB_MIGRATION_DSN="postgresql+psycopg://postgres:${PGPASSWORD}@127.0.0.1:5432/${DB}"
export DAON_OPERATIONS_TEST_DSN="postgresql://postgres:${PGPASSWORD}@127.0.0.1:5432/${DB}"
cd "$API_ROOT"
"$UV" run --isolated --with alembic==1.18.5 --with "psycopg[binary]==3.3.4" alembic upgrade head
PYTHONPATH=src:tests "$UV" run --isolated --with pytest==9.0.3 --with "psycopg[binary]==3.3.4" --with psycopg-pool python -m pytest tests/test_operations_status.py -q
[[ "$(docker exec "$PG_CONTAINER" psql -U postgres -d "$DB" -Atc "select version_num from alembic_version")" == "0016" ]]
echo "B10_CURRENT_0016_PASS"
echo "B10_OPERATIONS_RLS_COUNTS_PASS"
cleanup
if docker exec "$PG_CONTAINER" psql -U postgres -d postgres -lqt | grep -q "$DB"; then exit 1; fi
echo "B10_CLEANUP_REMAINING_0"
trap - EXIT
