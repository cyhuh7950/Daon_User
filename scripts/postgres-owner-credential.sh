#!/usr/bin/env bash
# primary PG의 보호 참조만 읽는다. 초기화 당시 container Env로 fallback하지 않는다.
case $- in *x*) set +x ;; esac

daon_load_primary_postgres_password() {
  local primary_pg_target=${1:?credential variable is required}
  local primary_pg_file=${DAON_PRIMARY_POSTGRES_ENV_FILE:-/home/daon/postgres_env/.env}
  local primary_pg_value
  case $primary_pg_target in owner_password|password) ;; *) echo PRIMARY_POSTGRES_CREDENTIAL_TARGET_INVALID >&2; return 22 ;; esac
  if ! primary_pg_value=$(python3 - "$primary_pg_file" <<'PY'
import os, re, stat, sys
try:
    fd = os.open(sys.argv[1], os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
    with os.fdopen(fd, 'r', encoding='utf-8') as stream:
        info = os.fstat(stream.fileno())
        if not stat.S_ISREG(info.st_mode) or stat.S_IMODE(info.st_mode) != 0o600 or info.st_uid != os.geteuid():
            raise ValueError()
        content = stream.read(65537)
        if len(content) > 65536:
            raise ValueError()
    entries = [line for line in content.splitlines() if line.startswith('POSTGRES_PRIMARY_PASSWORD=')]
    if len(entries) != 1:
        raise ValueError()
    match = re.fullmatch(r'POSTGRES_PRIMARY_PASSWORD=([A-Za-z0-9_-]{64})', entries[0])
    if match is None:
        raise ValueError()
    print(match.group(1))
except Exception:
    sys.exit('PRIMARY_POSTGRES_CREDENTIAL_UNAVAILABLE')
PY
  ); then
    return 23
  fi
  printf -v "$primary_pg_target" '%s' "$primary_pg_value"
  unset primary_pg_value
}
