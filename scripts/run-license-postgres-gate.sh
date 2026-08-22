#!/usr/bin/env bash
set -euo pipefail

repo_root=${1:?repository root is required}
gate_suffix=${2:-manual}
gate_db="daon_license_gate_${gate_suffix}"
gate_role="daon_license_gate_${gate_suffix}"
container=local-postgres
owner=$(docker exec "$container" printenv POSTGRES_USER)
owner_password=$(docker exec "$container" printenv POSTGRES_PASSWORD)

existing_db=$(docker exec "$container" psql -U "$owner" -d postgres -Atqc \
  "SELECT count(*) FROM pg_database WHERE datname='$gate_db'")
existing_role=$(docker exec "$container" psql -U "$owner" -d postgres -Atqc \
  "SELECT count(*) FROM pg_roles WHERE rolname='$gate_role'")
if [[ $existing_db != 0 || $existing_role != 0 ]]; then
  echo "LICENSE_GATE_TARGET_ALREADY_EXISTS"
  exit 23
fi

cleanup() {
  docker exec "$container" dropdb -U "$owner" --if-exists "$gate_db" >/dev/null 2>&1 || true
  docker exec "$container" psql -U "$owner" -d postgres \
    -c "DROP ROLE IF EXISTS $gate_role" >/dev/null 2>&1 || true
  remaining_db=$(docker exec "$container" psql -U "$owner" -d postgres -Atqc \
    "SELECT count(*) FROM pg_database WHERE datname='$gate_db'")
  remaining_role=$(docker exec "$container" psql -U "$owner" -d postgres -Atqc \
    "SELECT count(*) FROM pg_roles WHERE rolname='$gate_role'")
  echo "GATE_CLEANUP_REMAINING db=$remaining_db role=$remaining_role"
}
trap cleanup EXIT

urlencode() {
  python3 -c 'import sys,urllib.parse;print(urllib.parse.quote(sys.stdin.read(), safe=""))'
}

docker exec "$container" createdb -U "$owner" "$gate_db"
owner_encoded=$(printf %s "$owner" | urlencode)
owner_password_encoded=$(printf %s "$owner_password" | urlencode)
export DAON_DB_MIGRATION_DSN="postgresql+psycopg://${owner_encoded}:${owner_password_encoded}@127.0.0.1:5432/${gate_db}"

cd "$repo_root/services/api"
uv_bin=$(command -v uv || true)
if [[ -z $uv_bin && -x /home/daon/.local/bin/uv ]]; then
  uv_bin=/home/daon/.local/bin/uv
fi
if [[ -z $uv_bin ]]; then
  echo "LICENSE_GATE_UV_UNAVAILABLE"
  exit 25
fi
alembic_cmd=("$uv_bin" run --isolated --with alembic==1.18.4 --with sqlalchemy==2.0.46 --with 'psycopg[binary]==3.3.4' alembic)
echo "GATE_FRESH_UPGRADE_START"
"${alembic_cmd[@]}" upgrade head
echo "GATE_ROLLBACK_EMPTY_START"
"${alembic_cmd[@]}" downgrade 0018
echo "GATE_REAPPLY_START"
"${alembic_cmd[@]}" upgrade head

gate_password=$(cat /proc/sys/kernel/random/uuid)
docker exec "$container" psql -U "$owner" -d postgres -v ON_ERROR_STOP=1 \
  -c "CREATE ROLE $gate_role LOGIN NOSUPERUSER NOCREATEDB NOCREATEROLE NOREPLICATION NOBYPASSRLS PASSWORD '$gate_password' IN ROLE daon_app" \
  >/dev/null
gate_password_encoded=$(printf %s "$gate_password" | urlencode)
export DAON_TEST_POSTGRES_DSN="postgresql://${gate_role}:${gate_password_encoded}@127.0.0.1:5432/${gate_db}"

echo "GATE_NON_SUPERUSER_RLS_TEST_START"
PYTHONPATH=src:tests "$uv_bin" run --isolated --with pytest==9.0.3 \
  --with 'psycopg[binary,pool]==3.3.4' \
  python -m pytest tests/test_license_postgres.py -q

echo "GATE_DOWNGRADE_WITH_LIVE_ROWS_EXPECTED_BLOCK"
set +e
downgrade_output=$("${alembic_cmd[@]}" downgrade 0018 2>&1)
downgrade_code=$?
set -e
if [[ $downgrade_code -eq 0 ]] || ! grep -Eq '(NOTEBOOK|LICENSE)_DOWNGRADE_BLOCKED' <<<"$downgrade_output"; then
  echo "GATE_DOWNGRADE_BLOCK_ASSERTION_FAILED"
  exit 24
fi
echo "GATE_DOWNGRADE_WITH_LIVE_ROWS_BLOCKED"
