#!/usr/bin/env bash
set -euo pipefail

readonly DB="${A1_PG_DATABASE:-daon_a1_security_it_20260814}"
readonly PG_CONTAINER="${A1_PG_CONTAINER:-local-postgres}"
readonly PG_PORT="${A1_PG_PORT:-5432}"
readonly API_ROOT="/mnt/c/Users/cyhuh/Desktop/D Driver/Project/Daon_User/services/api"
readonly GATE="$API_ROOT/../../docs/03_evidence/release_1/R1-M8-10-WINDOWS-OFFLINE-STUDIO-01/foundation-a1-postgres-gate.py"
readonly UV="/home/daon/.local/bin/uv"

cleanup() {
  docker exec "$PG_CONTAINER" dropdb -U postgres --if-exists "$DB" >/dev/null 2>&1 || true
}
trap cleanup EXIT
cleanup
docker exec "$PG_CONTAINER" createdb -U postgres -T template0 "$DB"

# The password remains process-local and is never printed, written, or passed on the CLI.
PGPASSWORD="$(docker exec "$PG_CONTAINER" printenv POSTGRES_PASSWORD)"
export DAON_DB_MIGRATION_DSN="postgresql+psycopg://postgres:${PGPASSWORD}@127.0.0.1:${PG_PORT}/${DB}"
export DAON_TEST_POSTGRES_DSN="postgresql://postgres:${PGPASSWORD}@127.0.0.1:${PG_PORT}/${DB}"

cd "$API_ROOT"
"$UV" run --isolated --with alembic==1.18.5 --with "psycopg[binary]==3.3.4" alembic upgrade head
"$UV" run --isolated --with alembic==1.18.5 --with "psycopg[binary]==3.3.4" alembic downgrade 0014
"$UV" run --isolated --with alembic==1.18.5 --with "psycopg[binary]==3.3.4" alembic upgrade 0015
PYTHONPATH=src "$UV" run --isolated --with "psycopg[binary]==3.3.4" --with psycopg-pool python "$GATE"

set +e
DOWN_OUTPUT="$("$UV" run --isolated --with alembic==1.18.5 --with "psycopg[binary]==3.3.4" alembic downgrade 0014 2>&1)"
DOWN_CODE=$?
set -e
if [[ "$DOWN_CODE" -eq 0 ]]; then
  echo "DOWNGRADE_UNEXPECTED_SUCCESS"
  exit 1
fi
grep -q "SECURITY_AUDIT_DOWNGRADE_BLOCKED" <<<"$DOWN_OUTPUT"
echo "DOWNGRADE_BLOCKED_55000_PASS"

CURRENT="$(docker exec "$PG_CONTAINER" psql -U postgres -d "$DB" -Atc "select version_num from alembic_version")"
[[ "$CURRENT" == "0015" ]]
echo "CURRENT_0015_PASS"

cleanup
if docker exec "$PG_CONTAINER" psql -U postgres -d postgres -lqt | grep -q "$DB"; then
  echo "CLEANUP_FAILED"
  exit 1
fi
echo "CLEANUP_REMAINING_0"
trap - EXIT
