#!/usr/bin/env bash
set -euo pipefail
repo_root=${1:?repository root is required}
suffix=${2:-manual}
gate_db="daon_notebook_gate_${suffix}"
gate_role="daon_notebook_gate_${suffix}"
container=local-postgres
owner=$(docker exec "$container" printenv POSTGRES_USER)
owner_password=$(docker exec "$container" printenv POSTGRES_PASSWORD)
if [[ $(docker exec "$container" psql -U "$owner" -d postgres -Atqc "SELECT count(*) FROM pg_database WHERE datname='$gate_db'") != 0 ]]; then echo NOTEBOOK_GATE_TARGET_ALREADY_EXISTS; exit 23; fi
cleanup(){ docker exec "$container" dropdb -U "$owner" --if-exists "$gate_db" >/dev/null 2>&1 || true; docker exec "$container" psql -U "$owner" -d postgres -c "DROP ROLE IF EXISTS $gate_role" >/dev/null 2>&1 || true; echo "NOTEBOOK_GATE_CLEANUP db=$(docker exec "$container" psql -U "$owner" -d postgres -Atqc "SELECT count(*) FROM pg_database WHERE datname='$gate_db'") role=$(docker exec "$container" psql -U "$owner" -d postgres -Atqc "SELECT count(*) FROM pg_roles WHERE rolname='$gate_role'")"; }
trap cleanup EXIT
urlencode(){ python3 -c 'import sys,urllib.parse;print(urllib.parse.quote(sys.stdin.read(), safe=""))'; }
docker exec "$container" createdb -U "$owner" "$gate_db"
export DAON_DB_MIGRATION_DSN="postgresql+psycopg://$(printf %s "$owner" | urlencode):$(printf %s "$owner_password" | urlencode)@127.0.0.1:5432/$gate_db"
cd "$repo_root/services/api"
uv_bin=$(command -v uv || true); [[ -n $uv_bin ]] || uv_bin=/home/daon/.local/bin/uv
alembic_cmd=("$uv_bin" run --isolated --with alembic==1.18.4 --with sqlalchemy==2.0.46 --with 'psycopg[binary]==3.3.4' alembic)
echo NOTEBOOK_GATE_FRESH_UPGRADE
"${alembic_cmd[@]}" upgrade head
echo NOTEBOOK_GATE_ROLLBACK_EMPTY
"${alembic_cmd[@]}" downgrade 0019
echo NOTEBOOK_GATE_REAPPLY
"${alembic_cmd[@]}" upgrade head
gate_password=$(cat /proc/sys/kernel/random/uuid)
docker exec "$container" psql -U "$owner" -d postgres -v ON_ERROR_STOP=1 -c "CREATE ROLE $gate_role LOGIN NOSUPERUSER NOCREATEDB NOCREATEROLE NOREPLICATION NOBYPASSRLS PASSWORD '$gate_password' IN ROLE daon_app" >/dev/null
export DAON_TEST_POSTGRES_DSN="postgresql://$gate_role:$(printf %s "$gate_password" | urlencode)@127.0.0.1:5432/$gate_db"
export DAON_NOTEBOOK_CONTEXT_EVIDENCE_PATH="$repo_root/docs/03_evidence/release_1/R1-M8-10-WINDOWS-OFFLINE-STUDIO-01/phase-d-notebook-context-actual.json"
echo NOTEBOOK_GATE_ACTUAL_TESTS
PYTHONPATH=src:tests "$uv_bin" run --isolated --with pytest==9.0.3 --with 'psycopg[binary,pool]==3.3.4' python -m pytest tests/test_notebook_postgres.py -q
echo NOTEBOOK_GATE_DOWNGRADE_LIVE_EXPECT_BLOCK
set +e
output=$("${alembic_cmd[@]}" downgrade 0019 2>&1); code=$?
set -e
if [[ $code -eq 0 ]] || ! grep -Eq 'RETENTION_RUNTIME_DOWNGRADE_BLOCKED|NOTEBOOK_SOURCE_UNBINDING_DOWNGRADE_BLOCKED|NOTEBOOK_DOWNGRADE_BLOCKED' <<<"$output"; then echo NOTEBOOK_DOWNGRADE_BLOCK_ASSERTION_FAILED; exit 24; fi
echo NOTEBOOK_GATE_DOWNGRADE_LIVE_BLOCKED
