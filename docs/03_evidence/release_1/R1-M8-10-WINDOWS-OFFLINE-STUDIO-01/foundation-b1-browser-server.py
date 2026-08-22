"""Disposable actual Browser server for Phase B menu 1.

The process uses the production Runtime routes and ProviderSettingsService with a
disposable SQLite identity/authorization store.  It never contacts a provider:
the bounded connection checker returns a fixed safe status so the Browser Gate
can prove Web -> same-origin BFF -> Runtime wiring without a secret.
"""

from __future__ import annotations

import argparse
from datetime import datetime, timezone
from pathlib import Path

import uvicorn

from daon_user_api.audit import AuditEventStore
from daon_user_api.authorization import AuthorizationService, SqliteAuthorizationRepository
from daon_user_api.identity import IdentityService, SqliteIdentityRepository
from daon_user_api.provider_settings import (
    ProviderConnectionStatus,
    ProviderSettingsService,
    ReferenceProviderSettingsRepository,
)
from daon_user_api.runtime import RuntimeDependencies, RuntimeSettings, create_app


POLICY_VERSION = "foundation-b1-browser-v1"
LOGIN_ID = "foundation-b1-browser"
PASSWORD = "foundation-b1-browser-password"


class DiscardEmailSender:
    def send(self, **_message: str) -> None:
        return None


class FixedCredentialResolver:
    def configured(self, provider_code: str) -> bool:
        return provider_code == "UPSTAGE"

    def resolve(self, provider_code: str) -> str | None:
        return "disposable-browser-proof" if provider_code == "UPSTAGE" else None


class FixedConnectionChecker:
    def check(self, profile, credential):
        if profile.provider_code != "UPSTAGE" or credential != "disposable-browser-proof":
            raise AssertionError("UNEXPECTED_PROVIDER_CONNECTION")
        return ProviderConnectionStatus(
            provider_code="UPSTAGE",
            status="ready",
            checked_at="2026-08-15T00:00:00Z",
        )


def build_app(database_path: Path):
    audit = AuditEventStore()
    identity_repository = SqliteIdentityRepository(database_path)
    authorization_repository = SqliteAuthorizationRepository(database_path)
    identity = IdentityService(
        repository=identity_repository,
        audit_store=audit,
        oidc_policies=(),
        clock=lambda: datetime.now(timezone.utc),
        email_sender=DiscardEmailSender(),
    )
    identity.signup(
        login_id=LOGIN_ID,
        email="foundation-b1-browser@example.invalid",
        password=PASSWORD,
        trace_id="trace-foundation-b1-signup",
        policy_version=POLICY_VERSION,
    )
    with identity_repository.transaction() as connection:
        connection.execute(
            "UPDATE users SET state='active', email_verified_at=? WHERE login_id=?",
            (datetime.now(timezone.utc).isoformat(), LOGIN_ID),
        )
    authorization = AuthorizationService(
        repository=authorization_repository,
        audit_store=audit,
        clock=lambda: datetime.now(timezone.utc),
        identity_service=identity,
    )
    dependencies = RuntimeDependencies(
        settings=RuntimeSettings.for_test(
            database_path=database_path,
            policy_version=POLICY_VERSION,
        ),
        identity_service=identity,
        authorization_service=authorization,
        audit_store=audit,
        identity_repository=identity_repository,
        authorization_repository=authorization_repository,
        provider_settings_service=ProviderSettingsService(
            ReferenceProviderSettingsRepository(),
            FixedCredentialResolver(),
            FixedConnectionChecker(),
        ),
    )
    return create_app(dependencies)


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--port", type=int, required=True)
    parser.add_argument("--database", type=Path, required=True)
    arguments = parser.parse_args()
    arguments.database.parent.mkdir(parents=True, exist_ok=True)
    uvicorn.run(
        build_app(arguments.database),
        host="127.0.0.1",
        port=arguments.port,
        log_level="info",
        access_log=True,
    )


if __name__ == "__main__":
    main()
