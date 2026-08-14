#!/usr/bin/env bash
set -euo pipefail

readonly DB="daon_b5_compliance_it_20260815"
readonly PG_CONTAINER="local-postgres"
readonly API_ROOT="/mnt/c/Users/cyhuh/Desktop/D Driver/Project/Daon_User/services/api"
readonly UV="/home/daon/.local/bin/uv"

cleanup() {
  docker exec "$PG_CONTAINER" dropdb -U postgres --if-exists "$DB" >/dev/null 2>&1 || true
}
trap cleanup EXIT
cleanup
docker exec "$PG_CONTAINER" createdb -U postgres -T template0 "$DB"

# Password stays in this process and is never printed or written.
PGPASSWORD="$(docker exec "$PG_CONTAINER" printenv POSTGRES_PASSWORD)"
export DAON_DB_MIGRATION_DSN="postgresql+psycopg://postgres:${PGPASSWORD}@127.0.0.1:5432/${DB}"
export DAON_STUDIO_TEST_DSN="postgresql://postgres:${PGPASSWORD}@127.0.0.1:5432/${DB}"

cd "$API_ROOT"
"$UV" run --isolated --with alembic==1.18.5 --with "psycopg[binary]==3.3.4" alembic upgrade head
PYTHONPATH=src:tests "$UV" run --isolated \
  --with pytest==9.0.3 --with "psycopg[binary]==3.3.4" --with psycopg-pool --with minio \
  python -m pytest \
  tests/test_studio_workspace_postgres.py::StudioWorkspaceRealPostgresIntegrationTests::test_rls_fk_transaction_and_registration_order -q

CURRENT="$(docker exec "$PG_CONTAINER" psql -U postgres -d "$DB" -Atc "select version_num from alembic_version")"
[[ "$CURRENT" == "0016" ]]
echo "B5_CURRENT_0016_PASS"
echo "B5_COMPLIANCE_CONTENT_LINEAGE_VERSION_XLSX_PASS"

cleanup
if docker exec "$PG_CONTAINER" psql -U postgres -d postgres -lqt | grep -q "$DB"; then
  echo "B5_CLEANUP_FAILED"
  exit 1
fi
echo "B5_CLEANUP_REMAINING_0"
trap - EXIT
