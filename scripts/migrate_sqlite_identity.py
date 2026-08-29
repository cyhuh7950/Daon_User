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
    counts = {"users": 0, "email_tokens": 0, "password_tokens": 0, "memberships": 0, "devices": 0, "sessions": 0, "refresh_families": 0, "refresh_tokens": 0}
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
                for row in _rows(sqlite, "memberships"):
                    cursor.execute(
                        "INSERT INTO identity_memberships (tenant_id,user_id,role) VALUES (%s,%s,%s) ON CONFLICT (tenant_id,user_id) DO NOTHING",
                        (row["tenant_id"], row["user_id"], row["role"]),
                    )
                    counts["memberships"] += cursor.rowcount
                for row in _rows(sqlite, "devices"):
                    cursor.execute(
                        """
                        INSERT INTO identity_devices
                          (device_id,tenant_id,user_id,platform,state,last_seen_at,created_at,updated_at)
                        VALUES (%s,%s,%s,%s,%s,%s,%s,%s) ON CONFLICT (device_id) DO NOTHING
                        """,
                        tuple(row[name] for name in ("device_id", "tenant_id", "user_id", "platform", "state", "last_seen_at", "created_at", "updated_at")),
                    )
                    counts["devices"] += cursor.rowcount
                for row in _rows(sqlite, "sessions"):
                    cursor.execute(
                        """
                        INSERT INTO identity_sessions
                          (session_id,tenant_id,user_id,device_id,client_kind,access_digest,
                           access_expires_at,state,created_at,updated_at)
                        VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s) ON CONFLICT (session_id) DO NOTHING
                        """,
                        tuple(row[name] for name in ("session_id", "tenant_id", "user_id", "device_id", "client_kind", "access_digest", "access_expires_at", "state", "created_at", "updated_at")),
                    )
                    counts["sessions"] += cursor.rowcount
                for row in _rows(sqlite, "refresh_families"):
                    session = sqlite.execute("SELECT tenant_id FROM sessions WHERE session_id=?", (row["session_id"],)).fetchone()
                    if session is None:
                        continue
                    cursor.execute(
                        "INSERT INTO identity_refresh_families (family_id,tenant_id,session_id,state,created_at,updated_at) VALUES (%s,%s,%s,%s,%s,%s) ON CONFLICT (family_id) DO NOTHING",
                        (row["family_id"], session["tenant_id"], row["session_id"], row["state"], row["created_at"], row["updated_at"]),
                    )
                    counts["refresh_families"] += cursor.rowcount
                for row in _rows(sqlite, "refresh_tokens"):
                    cursor.execute(
                        "INSERT INTO identity_refresh_tokens (refresh_id,family_id,refresh_digest,expires_at,used_at,created_at) VALUES (%s,%s,%s,%s,%s,%s) ON CONFLICT (refresh_id) DO NOTHING",
                        tuple(row[name] for name in ("refresh_id", "family_id", "refresh_digest", "expires_at", "used_at", "created_at")),
                    )
                    counts["refresh_tokens"] += cursor.rowcount
            pg.commit()
    finally:
        sqlite.close()
    return counts


if __name__ == "__main__":
    if len(sys.argv) != 2:
        raise SystemExit("usage: migrate_sqlite_identity.py /path/to/runtime.sqlite3")
    print(migrate(Path(sys.argv[1]), _dsn()))
