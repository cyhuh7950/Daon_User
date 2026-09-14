"""Server-console-only recovery commands for the Daon User API."""

from __future__ import annotations

import os
import sqlite3
import sys
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

from .audit import AuditEventStore
from .identity import (
    INITIAL_ADMIN_BOOTSTRAP_MARKER,
    INITIAL_ADMIN_USER_ID,
    IdentityError,
    IdentityService,
    SqliteIdentityRepository,
    is_protected_initial_admin_record,
)
from .identity_postgres import PostgresIdentityRepository

@dataclass(frozen=True, slots=True)
class _RecoverySettings:
    profile: str
    database_path: Path | None
    cloud_database_dsn: str | None
    policy_version: str

    @classmethod
    def from_env(cls) -> "_RecoverySettings":
        profile = os.environ.get("DAON_RUNTIME_PROFILE", "development")
        if profile not in {"test", "development", "production"}:
            raise IdentityError("RUNTIME_PROFILE_INVALID", 503)
        database = os.environ.get("DAON_API_DATABASE_PATH")
        return cls(
            profile=profile,
            database_path=None if database is None else Path(database),
            cloud_database_dsn=os.environ.get("DAON_CLOUD_DATABASE_DSN"),
            policy_version=os.environ.get("DAON_POLICY_VERSION", "runtime-policy-v1"),
        )


@dataclass(slots=True)
class _RecoveryDependencies:
    identity_service: IdentityService
    identity_repository: SqliteIdentityRepository
    audit_store: object

    def close(self) -> None:
        self.identity_repository.close()
        closer = getattr(self.audit_store, "close", None)
        if callable(closer):
            closer()


def _preflight_existing_sqlite(path: Path) -> None:
    """Validate the recovery target through a read-only handle before any adapter opens it."""
    if not path.is_file():
        raise IdentityError("INITIAL_ADMIN_MISSING", 503)
    connection = None
    try:
        connection = sqlite3.connect(
            f"file:{path.resolve().as_posix()}?mode=ro&immutable=1", uri=True
        )
        connection.row_factory = sqlite3.Row
        tables = {
            str(row[0])
            for row in connection.execute(
                "SELECT name FROM sqlite_master WHERE type='table' AND name IN ('users','bootstrap_state')"
            )
        }
        if tables != {"users", "bootstrap_state"}:
            raise IdentityError("INITIAL_ADMIN_MISSING", 503)
        marker = connection.execute(
            "SELECT marker_key FROM bootstrap_state WHERE marker_key=?",
            (INITIAL_ADMIN_BOOTSTRAP_MARKER,),
        ).fetchone()
        row = connection.execute(
            "SELECT user_id,issuer,subject,login_id,email,password_digest,"
            "password_change_required,state FROM users WHERE user_id=?",
            (INITIAL_ADMIN_USER_ID,),
        ).fetchone()
        if marker is None or row is None:
            raise IdentityError("INITIAL_ADMIN_MISSING", 503)
        if not is_protected_initial_admin_record(row):
            raise IdentityError("INITIAL_ADMIN_CONFLICT", 503)
    except IdentityError:
        raise
    except sqlite3.Error as error:
        raise IdentityError("INITIAL_ADMIN_MISSING", 503) from error
    finally:
        if connection is not None:
            connection.close()


def _build_recovery_dependencies(settings: _RecoverySettings) -> _RecoveryDependencies:
    clock = lambda: datetime.now(timezone.utc)
    if settings.profile == "production":
        if settings.cloud_database_dsn is None:
            raise IdentityError("PERSISTENCE_UNAVAILABLE", 503)
        from .audit import PostgresSecurityAuditStore
        repository = PostgresIdentityRepository(settings.cloud_database_dsn)
        audit_store: object = PostgresSecurityAuditStore(settings.cloud_database_dsn)
    else:
        if settings.database_path is None:
            raise IdentityError("PERSISTENCE_UNAVAILABLE", 503)
        _preflight_existing_sqlite(settings.database_path)
        repository = SqliteIdentityRepository(settings.database_path)
        audit_store = AuditEventStore()
    service = IdentityService(
        repository=repository,
        audit_store=audit_store,
        oidc_policies=(),
        clock=clock,
        dispatch_pending_audits=False,
    )
    return _RecoveryDependencies(service, repository, audit_store)


def main(argv: Sequence[str] | None = None) -> int:
    arguments = list(sys.argv[1:] if argv is None else argv)
    if arguments != ["reset-initial-password"]:
        print(
            "usage: python -m daon_user_api.admin_cli reset-initial-password",
            file=sys.stderr,
        )
        return 2
    dependencies = None
    try:
        settings = _RecoverySettings.from_env()
        dependencies = _build_recovery_dependencies(settings)
        dependencies.identity_service.require_initial_admin_recovery_target()
        dependencies.identity_service.reset_initial_admin_password(
            trace_id="trace-server-console-admin-recovery",
            policy_version=settings.policy_version,
        )
    except IdentityError as error:
        print(f"INITIAL_ADMIN_PASSWORD_RESET_FAILED:{error.code}", file=sys.stderr)
        return 1
    except Exception:
        print("INITIAL_ADMIN_PASSWORD_RESET_FAILED:RUNTIME_UNAVAILABLE", file=sys.stderr)
        return 1
    finally:
        if dependencies is not None:
            dependencies.close()
    print("INITIAL_ADMIN_PASSWORD_RESET")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
