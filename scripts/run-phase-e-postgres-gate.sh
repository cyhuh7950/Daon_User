#!/usr/bin/env bash
set -euo pipefail

repo_root=${1:?repository root is required}
suffix=${2:-manual}
gate_db="daon_phase_e_gate_${suffix}"
gate_role="daon_phase_e_gate_${suffix}"
container=local-postgres
owner=$(docker exec "$container" printenv POSTGRES_USER)
owner_password=$(docker exec "$container" printenv POSTGRES_PASSWORD)

if [[ $(docker exec "$container" psql -U "$owner" -d postgres -Atqc "SELECT count(*) FROM pg_database WHERE datname='$gate_db'") != 0 ]]; then
  echo PHASE_E_GATE_TARGET_ALREADY_EXISTS
  exit 23
fi

cleanup() {
  docker exec "$container" dropdb -U "$owner" --if-exists "$gate_db" >/dev/null 2>&1 || true
  docker exec "$container" psql -U "$owner" -d postgres -c "DROP ROLE IF EXISTS $gate_role" >/dev/null 2>&1 || true
  echo "PHASE_E_GATE_CLEANUP db=$(docker exec "$container" psql -U "$owner" -d postgres -Atqc "SELECT count(*) FROM pg_database WHERE datname='$gate_db'") role=$(docker exec "$container" psql -U "$owner" -d postgres -Atqc "SELECT count(*) FROM pg_roles WHERE rolname='$gate_role'")"
}
trap cleanup EXIT

urlencode() {
  python3 -c 'import sys,urllib.parse;print(urllib.parse.quote(sys.stdin.read(), safe=""))'
}

docker exec "$container" createdb -U "$owner" "$gate_db"
export DAON_DB_MIGRATION_DSN="postgresql+psycopg://$(printf %s "$owner" | urlencode):$(printf %s "$owner_password" | urlencode)@127.0.0.1:5432/$gate_db"
cd "$repo_root/services/api"
uv_bin=$(command -v uv || true)
[[ -n $uv_bin ]] || uv_bin=/home/daon/.local/bin/uv
alembic_cmd=("$uv_bin" run --isolated --with alembic==1.18.4 --with sqlalchemy==2.0.46 --with 'psycopg[binary]==3.3.4' alembic)

echo PHASE_E_GATE_FRESH_UPGRADE
"${alembic_cmd[@]}" upgrade head
gate_password=$(cat /proc/sys/kernel/random/uuid)
docker exec "$container" psql -U "$owner" -d postgres -v ON_ERROR_STOP=1 -c "CREATE ROLE $gate_role LOGIN NOSUPERUSER NOCREATEDB NOCREATEROLE NOREPLICATION NOBYPASSRLS PASSWORD '$gate_password' IN ROLE daon_app" >/dev/null
export DAON_TEST_POSTGRES_DSN="postgresql://$gate_role:$(printf %s "$gate_password" | urlencode)@127.0.0.1:5432/$gate_db"

echo PHASE_E_GATE_ACTUAL_TESTS
PYTHONPATH=src:tests "$uv_bin" run --isolated --with pytest==9.0.3 --with 'psycopg[binary,pool]==3.3.4' python -m pytest \
  tests/test_cloud_storage.py tests/test_notebook_postgres.py \
  --no-header -vv
echo "PHASE_E_GATE_ASSERTIONS expected_tests=18 skipped=0 raw_sqlstate_output=0"
