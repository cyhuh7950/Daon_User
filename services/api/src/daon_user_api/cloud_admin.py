"""Deployment-only database preflight and least-privilege role bootstrap."""

from __future__ import annotations

import argparse
import json
import os

import psycopg
from psycopg import sql


def _required(name: str) -> str:
    value = os.environ.get(name)
    if not value:
        raise RuntimeError(f"{name}_REQUIRED")
    return value


def server_version_supported(value: str) -> bool:
    return value == "18.4" or value.startswith("18.4 ")


def preflight() -> dict[str, object]:
    expected_database = _required("DAON_DB_EXPECTED_NAME")
    with psycopg.connect(_required("DAON_DB_MIGRATION_DSN")) as connection:
        row = connection.execute(
            "SELECT current_setting('server_version') AS server_version, current_database() AS database_name"
        ).fetchone()
        extension = connection.execute(
            "SELECT extversion FROM pg_extension WHERE extname = %s", ("vector",)
        ).fetchone()
    if row is None or not server_version_supported(str(row[0])) or str(row[1]) != expected_database:
        raise RuntimeError("DATABASE_TARGET_MISMATCH")
    return {
        "status": "pass",
        "server_version": "18.4",
        "target_identity_verified": True,
        "vector_version": None if extension is None else str(extension[0]),
    }


def bootstrap_role() -> dict[str, object]:
    password = _required("DAON_DB_APP_PASSWORD")
    with psycopg.connect(_required("DAON_DB_MIGRATION_DSN")) as connection:
        connection.execute(
            sql.SQL("ALTER ROLE daon_app LOGIN PASSWORD {}").format(sql.Literal(password))
        )
        row = connection.execute(
            "SELECT rolsuper, rolcreatedb, rolcreaterole, rolinherit, rolbypassrls "
            "FROM pg_roles WHERE rolname = %s",
            ("daon_app",),
        ).fetchone()
    if row is None or any(bool(value) for value in row):
        raise RuntimeError("APPLICATION_ROLE_PRIVILEGE_INVALID")
    return {"status": "pass", "application_role_least_privilege": True}


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("operation", choices=("preflight", "bootstrap-role"))
    operation = parser.parse_args().operation
    result = preflight() if operation == "preflight" else bootstrap_role()
    print(json.dumps(result, sort_keys=True, separators=(",", ":")))


if __name__ == "__main__":
    main()
