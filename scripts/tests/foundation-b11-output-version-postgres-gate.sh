#!/usr/bin/env bash
set -euo pipefail

db="daon_foundation_b11_20260815"
cleanup() {
  docker exec local-postgres dropdb -U postgres --if-exists "$db" >/dev/null 2>&1 || true
}
trap cleanup EXIT
cleanup
docker exec local-postgres createdb -U postgres "$db"
password="$(docker exec local-postgres printenv POSTGRES_PASSWORD)"
dsn="postgresql://postgres:${password}@127.0.0.1:5432/${db}"

cd services/api
DAON_DB_MIGRATION_DSN="$dsn" uv run --isolated \
  --with alembic==1.18.5 --with 'psycopg[binary]==3.3.4' \
  python -m alembic upgrade head
PYTHONPATH=src DAON_OUTPUT_SETTINGS_TEST_DSN="$dsn" uv run --isolated \
  --with 'psycopg[binary]==3.3.4' --with psycopg-pool \
  python ../../scripts/tests/foundation-b11-output-version-postgres-gate.py
DAON_DB_MIGRATION_DSN="$dsn" uv run --isolated \
  --with alembic==1.18.5 --with 'psycopg[binary]==3.3.4' \
  python -m alembic downgrade 0016
DAON_DB_MIGRATION_DSN="$dsn" uv run --isolated \
  --with alembic==1.18.5 --with 'psycopg[binary]==3.3.4' \
  python -m alembic upgrade 0017
current="$(docker exec local-postgres psql -U postgres -d "$db" -Atc 'select version_num from alembic_version')"
test "$current" = "0017"
echo "OUTPUT_VERSION_SETTINGS_MIGRATION PASS current=${current} rollback_reapply=PASS"
