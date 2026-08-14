"""Disposable current-source Browser server for Phase B menu 2."""

from __future__ import annotations

import argparse
import hashlib
from datetime import datetime, timedelta, timezone
from pathlib import Path

import uvicorn

from daon_user_api.audit import AuditEventStore
from daon_user_api.authorization import AuthorizationService, SqliteAuthorizationRepository
from daon_user_api.identity import IdentityService, SqliteIdentityRepository
from daon_user_api.knowledge_package import (
    KnowledgePackageRecord,
    KnowledgePackageService,
    ReferenceKnowledgePackageRepository,
)
from daon_user_api.runtime import RuntimeDependencies, RuntimeSettings, create_app
from daon_user_api.studio_report import WorkspaceSourceProjection


POLICY_VERSION = "foundation-b2-browser-v1"
LOGIN_ID = "foundation-b2-browser"
PASSWORD = "foundation-b2-browser-password"


class DiscardEmailSender:
    def send(self, **_message: str) -> None:
        return None


class SourceProjectionRepository:
    def list_sources(self, _context):
        return (
            WorkspaceSourceProjection(
                "source-browser-ready", "source-version-browser-ready", "verified-source.pdf",
                "ready", "completed", "completed",
            ),
            WorkspaceSourceProjection(
                "source-browser-review", "source-version-browser-review", "review-required.pdf",
                "needs_review", "completed", "completed",
            ),
        )

    def list_outputs(self, _context):
        return ()


class EmptyStudioWorkspaceService:
    def list_outputs(self, _context):
        return {"outputs": (), "studio_locks": ()}


def build_app(database_path: Path):
    now = datetime.now(timezone.utc)
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
        email="foundation-b2-browser@example.invalid",
        password=PASSWORD,
        trace_id="trace-foundation-b2-signup",
        policy_version=POLICY_VERSION,
    )
    with identity_repository.transaction() as connection:
        connection.execute(
            "UPDATE users SET state='active', email_verified_at=? WHERE login_id=?",
            (now.isoformat(), LOGIN_ID),
        )
        membership = connection.execute(
            "SELECT tenant_id, user_id FROM memberships ORDER BY tenant_id LIMIT 1"
        ).fetchone()
    tenant_id = str(membership["tenant_id"])
    workspace_id = "workspace-" + hashlib.sha256(tenant_id.encode("utf-8")).hexdigest()[:24]
    content = b'{"title":"Verified Daon 2.5 knowledge"}'
    package_repository = ReferenceKnowledgePackageRepository()
    package_repository.add(KnowledgePackageRecord(
        package_id="knowledge-package-browser-1",
        tenant_id=tenant_id,
        workspace_id=workspace_id,
        producer="daon2_5",
        producer_version="2.5.7",
        knowledge_registration_id="knowledge-registration-browser-1",
        output_version_id="output-version-browser-1",
        authority="approved",
        review_state="approved",
        registration_state="registered",
        digest_sha256=hashlib.sha256(content).hexdigest(),
        byte_size=len(content),
        content_type="application/vnd.daon.knowledge+json",
        content=content,
        effective_at=now - timedelta(days=1),
        expires_at=now + timedelta(days=30),
    ))
    authorization = AuthorizationService(
        repository=authorization_repository,
        audit_store=audit,
        clock=lambda: datetime.now(timezone.utc),
        identity_service=identity,
    )
    dependencies = RuntimeDependencies(
        settings=RuntimeSettings.for_test(database_path=database_path, policy_version=POLICY_VERSION),
        identity_service=identity,
        authorization_service=authorization,
        audit_store=audit,
        identity_repository=identity_repository,
        authorization_repository=authorization_repository,
        knowledge_package_service=KnowledgePackageService(
            package_repository, clock=lambda: datetime.now(timezone.utc)
        ),
        studio_report_repository=SourceProjectionRepository(),
        studio_workspace_service=EmptyStudioWorkspaceService(),
    )
    return create_app(dependencies)


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--port", type=int, required=True)
    parser.add_argument("--database", type=Path, required=True)
    arguments = parser.parse_args()
    arguments.database.parent.mkdir(parents=True, exist_ok=True)
    uvicorn.run(build_app(arguments.database), host="127.0.0.1", port=arguments.port, log_level="info", access_log=True)


if __name__ == "__main__":
    main()
