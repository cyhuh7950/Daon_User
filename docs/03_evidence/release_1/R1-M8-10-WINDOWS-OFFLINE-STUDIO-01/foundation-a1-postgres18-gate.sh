#!/usr/bin/env bash
set -euo pipefail

readonly CONTAINER="daon-a1-pg18-it"
readonly PORT="55418"
readonly GATE="/mnt/c/Users/cyhuh/Desktop/D Driver/Project/Daon_User/docs/03_evidence/release_1/R1-M8-10-WINDOWS-OFFLINE-STUDIO-01/foundation-a1-postgres-gate.sh"

cleanup() {
  docker rm -f "$CONTAINER" >/dev/null 2>&1 || true
}
trap cleanup EXIT
cleanup
DISPOSABLE_PASSWORD="$(openssl rand -hex 24)"
docker run -d --name "$CONTAINER" -p "127.0.0.1:${PORT}:5432" \
  -e POSTGRES_PASSWORD="$DISPOSABLE_PASSWORD" pgvector/pgvector:pg18 >/dev/null || {
    echo "PG18_CONTAINER_START_FAILED" >&2
    exit 1
  }
for _ in $(seq 1 30); do
  if docker exec "$CONTAINER" pg_isready -U postgres >/dev/null 2>&1; then
    break
  fi
  sleep 1
done
docker exec "$CONTAINER" pg_isready -U postgres >/dev/null
A1_PG_CONTAINER="$CONTAINER" A1_PG_PORT="$PORT" \
  A1_PG_DATABASE="daon_a1_security_pg18_it_20260814" bash "$GATE"
cleanup
if docker ps -a --format '{{.Names}}' | grep -qx "$CONTAINER"; then
  echo "PG18_CONTAINER_CLEANUP_FAILED"
  exit 1
fi
echo "PG18_CONTAINER_CLEANUP_0"
trap - EXIT
