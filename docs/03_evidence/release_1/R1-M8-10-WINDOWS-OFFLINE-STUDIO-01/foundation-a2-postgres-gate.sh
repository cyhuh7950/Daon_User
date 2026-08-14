#!/usr/bin/env bash
set -euo pipefail

readonly DB="daon_a2_canon_it_20260814"
readonly PG_CONTAINER="local-postgres"
readonly API_ROOT="/mnt/c/Users/cyhuh/Desktop/D Driver/Project/Daon_User/services/api"
readonly UV="/home/daon/.local/bin/uv"

cleanup() {
  docker exec "$PG_CONTAINER" dropdb -U postgres --if-exists "$DB" >/dev/null 2>&1 || true
}
trap cleanup EXIT
cleanup
docker exec "$PG_CONTAINER" createdb -U postgres -T template0 "$DB"

# The password remains process-local and is never printed, written, or passed on the CLI.
PGPASSWORD="$(docker exec "$PG_CONTAINER" printenv POSTGRES_PASSWORD)"
export DAON_DB_MIGRATION_DSN="postgresql+psycopg://postgres:${PGPASSWORD}@127.0.0.1:5432/${DB}"
export DAON_STUDIO_TEST_DSN="postgresql://postgres:${PGPASSWORD}@127.0.0.1:5432/${DB}"

cd "$API_ROOT"
"$UV" run --isolated --with alembic==1.18.5 --with "psycopg[binary]==3.3.4" alembic upgrade head
"$UV" run --isolated --with alembic==1.18.5 --with "psycopg[binary]==3.3.4" alembic downgrade 0015
"$UV" run --isolated --with alembic==1.18.5 --with "psycopg[binary]==3.3.4" alembic upgrade 0016
PYTHONPATH=src:tests "$UV" run --isolated \
  --with pytest==9.0.3 --with "psycopg[binary]==3.3.4" --with psycopg-pool --with minio \
  python -m pytest \
  tests/test_studio_workspace_postgres.py::StudioWorkspaceRealPostgresIntegrationTests::test_rls_fk_transaction_and_registration_order -q

set +e
DOWN_OUTPUT="$("$UV" run --isolated --with alembic==1.18.5 --with "psycopg[binary]==3.3.4" alembic downgrade 0015 2>&1)"
DOWN_CODE=$?
set -e
if [[ "$DOWN_CODE" -eq 0 ]]; then
  echo "A2_DOWNGRADE_UNEXPECTED_SUCCESS"
  exit 1
fi
grep -q "OUTPUT_VERSION_DOWNGRADE_BLOCKED" <<<"$DOWN_OUTPUT"
CURRENT="$(docker exec "$PG_CONTAINER" psql -U postgres -d "$DB" -Atc "select version_num from alembic_version")"
[[ "$CURRENT" == "0016" ]]
echo "A2_DOWNGRADE_BLOCKED_55000_PASS"
echo "A2_CURRENT_0016_PASS"

cleanup
if docker exec "$PG_CONTAINER" psql -U postgres -d postgres -lqt | grep -q "$DB"; then
  echo "A2_CLEANUP_FAILED"
  exit 1
fi
echo "A2_CANON_VERSION_REPLAY_PASS"
echo "A2_CLEANUP_REMAINING_0"
trap - EXIT
