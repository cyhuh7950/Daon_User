#!/usr/bin/env bash
set -euo pipefail
export PATH="$HOME/.local/bin:$PATH"

db="daon_foundation_b12_20260815"
cleanup() { docker exec local-postgres dropdb -U postgres --if-exists "$db" >/dev/null 2>&1 || true; }
trap cleanup EXIT
cleanup
docker exec local-postgres createdb -U postgres "$db"
password="$(docker exec local-postgres printenv POSTGRES_PASSWORD)"
dsn="postgresql://postgres:${password}@127.0.0.1:5432/${db}"

cd services/api
DAON_DB_MIGRATION_DSN="$dsn" uv run --isolated --with alembic==1.18.5 --with 'psycopg[binary]==3.3.4' python -m alembic upgrade head
PYTHONPATH=src DAON_SYNC_SETTINGS_TEST_DSN="$dsn" uv run --isolated --with 'psycopg[binary]==3.3.4' --with psycopg-pool python ../../scripts/tests/foundation-b12-sync-approval-postgres-gate.py
current="$(docker exec local-postgres psql -U postgres -d "$db" -Atc 'select version_num from alembic_version')"
test "$current" = "0017"
echo "SYNC_APPROVAL_SETTINGS_MIGRATION PASS current=${current} disposable_cleanup=armed"
