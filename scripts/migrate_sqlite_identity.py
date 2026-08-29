"""Copy legacy SQLite identity records into PostgreSQL identity tables.

The source database is opened read-only. The destination DSN is supplied via
``DAON_CLOUD_DATABASE_DSN`` (or ``DAON_IDENTITY_DATABASE_DSN``) and should
already point at the dedicated ``daon_user`` database.
"""

from __future__ import annotations

import os
import sqlite3
import sys
from pathlib import Path

import psycopg


def _dsn() -> str:
    value = os.environ.get("DAON_IDENTITY_DATABASE_DSN") or os.environ.get("DAON_CLOUD_DATABASE_DSN")
    if not value:
        raise SystemExit("DAON_IDENTITY_DATABASE_DSN_REQUIRED")
    return value


def _rows(connection: sqlite3.Connection, table: str) -> list[sqlite3.Row]:
    try:
        return list(connection.execute(f"SELECT * FROM {table}"))
    except sqlite3.OperationalError:
        return []


def migrate(source: Path, destination: str) -> dict[str, int]:
    if not source.exists():
        raise FileNotFoundError(source)
    sqlite = sqlite3.connect(f"file:{source.resolve()}?mode=ro", uri=True)
    sqlite.row_factory = sqlite3.Row
    counts = {"users": 0, "email_tokens": 0, "password_tokens": 0, "refresh_families": 0, "refresh_tokens": 0}
    try:
        with psycopg.connect(destination) as pg:
            with pg.cursor() as cursor:
                for row in _rows(sqlite, "users"):
                    cursor.execute(
                        """
                        INSERT INTO identity_users
                          (user_id, issuer, subject, login_id, email, password_digest,
                           email_verified_at, state, created_at, updated_at)
                        VALUES (%s,%s,%s,%s,%s,%s,%s,%s,now(),now())
                        ON CONFLICT (user_id) DO NOTHING
                        """,
                        (row["user_id"], row["issuer"], row["subject"], row["login_id"],
                         row["email"], row["password_digest"], row["email_verified_at"], row["state"]),
                    )
                    counts["users"] += cursor.rowcount
                for row in _rows(sqlite, "email_verification_tokens"):
                    cursor.execute(
                        """
                        INSERT INTO identity_email_verification_tokens
                          (token_id,user_id,token_digest,expires_at,used_at,attempts,created_at)
                        VALUES (%s,%s,%s,%s,%s,%s,%s) ON CONFLICT (token_id) DO NOTHING
                        """,
                        tuple(row[name] for name in ("token_id", "user_id", "token_digest", "expires_at", "used_at", "attempts", "created_at")),
                    )
                    counts["email_tokens"] += cursor.rowcount
                for row in _rows(sqlite, "password_reset_tokens"):
                    cursor.execute(
                        """
                        INSERT INTO identity_password_reset_tokens
                          (token_id,user_id,token_digest,expires_at,used_at,attempts,created_at)
                        VALUES (%s,%s,%s,%s,%s,%s,%s) ON CONFLICT (token_id) DO NOTHING
                        """,
                        tuple(row[name] for name in ("token_id", "user_id", "token_digest", "expires_at", "used_at", "attempts", "created_at")),
                    )
                    counts["password_tokens"] += cursor.rowcount
            pg.commit()
    finally:
        sqlite.close()
    return counts


if __name__ == "__main__":
    if len(sys.argv) != 2:
        raise SystemExit("usage: migrate_sqlite_identity.py /path/to/runtime.sqlite3")
    print(migrate(Path(sys.argv[1]), _dsn()))
