from __future__ import annotations

import base64
import asyncio
import json
import tempfile
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import patch

import httpx
import pytest
from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.asymmetric import padding, rsa

from daon_user_api.audit import AuditEventStore
from daon_user_api.authorization import AuthorizationService, Role, SqliteAuthorizationRepository
from daon_user_api.identity import ClientKind, IdentityPrincipal
from daon_user_api.license import (
    LicenseService,
    LicenseContext,
    ReferenceLicenseRepository,
    RsaPublicKey,
    RsaSha256LicenseVerifier,
    UnavailableLicenseVerifier,
)
from daon_user_api.runtime import (
    WEB_SESSION_COOKIE, RuntimeDependencies, RuntimeSettings,
    _requires_runtime_license_precheck, create_app,
)
from daon_user_api.studio_report import StudioReportService
from daon_user_api.studio_report_postgres import PostgresStudioReportRepository
from daon_user_api.studio_workspace import StudioWorkspaceService
from daon_user_api.studio_workspace_postgres import PostgresStudioWorkspaceRepository
from test_identity_support import POLICY_VERSION, create_service


NOW = datetime(2026, 8, 15, 8, 0, tzinfo=timezone.utc)


def _signed_document(private_key):
    claims = {
        "schema_version": 1, "license_id": "license-release-1-001",
        "product": "daon-user", "edition": "enterprise",
        "organization_id": "tenant-001",
        "issued_at": (NOW - timedelta(days=1)).isoformat().replace("+00:00", "Z"),
        "expires_at": (NOW + timedelta(days=365)).isoformat().replace("+00:00", "Z"),
        "features": ["citation", "studio_generation"],
        "resource_limits": {"generation_runs": 100, "notebooks": 20},
    }
    payload = json.dumps(claims, ensure_ascii=True, separators=(",", ":"), sort_keys=True).encode()
    signature = private_key.sign(payload, padding.PKCS1v15(), hashes.SHA256())
    return {
        "schema_version": 1, "key_id": "release-1", "algorithm": "RS256",
        "claims": claims,
        "signature": base64.urlsafe_b64encode(signature).decode().rstrip("="),
    }


def test_license_read_is_general_user_safe_and_apply_is_org_admin_step_up_only():
    asyncio.run(_exercise_license_http())


def test_runtime_precheck_is_skipped_only_for_transactional_postgres_generation_services():
    cloud = object()
    workspace = StudioWorkspaceService(PostgresStudioWorkspaceRepository(
        cloud, creation_enforcer=lambda *_args: None,
    ))
    report = StudioReportService(PostgresStudioReportRepository(
        cloud, creation_enforcer=lambda *_args: None,
    ))
    fake = type("FakeStudio", (), {"creation_license_authoritative": True})()
    fake_repository = type("FakeRepository", (), {
        "creation_license_authoritative": True,
    })()
    wrapped_fake = StudioWorkspaceService(fake_repository)

    assert _requires_runtime_license_precheck(workspace) is False
    assert _requires_runtime_license_precheck(report) is False
    assert _requires_runtime_license_precheck(fake) is True
    assert _requires_runtime_license_precheck(wrapped_fake) is True


async def _exercise_license_http():
    with tempfile.TemporaryDirectory() as directory:
        db_path = Path(directory) / "runtime.sqlite3"
        audit = AuditEventStore()
        identity, identity_repository, _, clock = create_service(db_path, audit_store=audit)
        auth_repository = SqliteAuthorizationRepository(db_path)
        auth_repository.bootstrap_workspace(
            tenant_id="tenant-001", workspace_id="workspace-001", owner_user_id="org-admin",
            owner_role=Role.ORGANIZATION_ADMIN, workspace_kind="organization",
            data_area="cloud_sync", cost_limit_cents=1000, now=clock(),
        )
        with auth_repository.transaction() as connection:
            connection.execute(
                "INSERT INTO auth_memberships (tenant_id,workspace_id,user_id,role,state,version,updated_at) "
                "VALUES (?,?,?,?, 'active',1,?)",
                ("tenant-001", "workspace-001", "member-001", Role.VIEWER.value, clock().isoformat()),
            )
        authorization = AuthorizationService(
            repository=auth_repository, audit_store=audit, clock=clock,
            identity_service=identity,
        )
        private_key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
        numbers = private_key.public_key().public_numbers()
        usage = {"generation_runs": 2, "notebooks": 1}
        license_repository = ReferenceLicenseRepository()
        license_service = LicenseService(
            license_repository,
            RsaSha256LicenseVerifier({"release-1": RsaPublicKey(numbers.n, numbers.e)}),
            product_code="daon-user", clock=lambda: NOW,
            usage_reader=lambda _context: usage,
        )
        studio = type("Studio", (), {"calls": 0, "generate": lambda self, *_args: setattr(self, "calls", self.calls + 1)})()
        dependencies = RuntimeDependencies(
            settings=RuntimeSettings.for_test(database_path=db_path, policy_version=POLICY_VERSION),
            identity_service=identity, authorization_service=authorization,
            audit_store=audit, identity_repository=identity_repository,
            authorization_repository=auth_repository, license_service=license_service,
            studio_workspace_service=studio,
        )
        document = _signed_document(private_key)
        admin = IdentityPrincipal("org-admin", "session-admin", "device-admin", "tenant-001")
        member = IdentityPrincipal("member-001", "session-member", "device-member", "tenant-001")
        admin_view = type("SessionView", (), {"client_kind": ClientKind.WEB, "principal": admin})()
        member_view = type("SessionView", (), {"client_kind": ClientKind.WEB, "principal": member})()
        try:
            async with httpx.AsyncClient(transport=httpx.ASGITransport(app=create_app(dependencies)), base_url="http://test") as client:
                with patch.object(identity, "describe_access", return_value=member_view):
                    initial = await client.get(
                        "/api/v1/workspaces/workspace-001/license",
                        cookies={WEB_SESSION_COOKIE: "opaque-session"},
                    )
                    denied = await client.post(
                        "/api/v1/organizations/tenant-001/license",
                        cookies={WEB_SESSION_COOKIE: "opaque-session"},
                        headers={"Idempotency-Key": "license-http-idem-0001"},
                        json={"document": document, "step_up_authorization_id": "step-up-1"},
                    )
                assert initial.status_code == 200
                assert initial.json()["data"]["status"] == "not_configured"
                assert initial.json()["data"]["can_apply"] is False
                assert "signature" not in initial.text and "claims" not in initial.text
                assert denied.status_code == 403

                with patch.object(identity, "describe_access", return_value=admin_view), patch.object(
                    identity, "consume_step_up", return_value=None,
                ) as consume:
                    applied = await client.post(
                        "/api/v1/organizations/tenant-001/license",
                        cookies={WEB_SESSION_COOKIE: "opaque-session"},
                        headers={"Idempotency-Key": "license-http-idem-0002"},
                        json={"document": document, "step_up_authorization_id": "step-up-2"},
                    )
                    current = await client.get(
                        "/api/v1/workspaces/workspace-001/license",
                        cookies={WEB_SESSION_COOKIE: "opaque-session"},
                    )
                assert applied.status_code == 201
                assert current.status_code == 200
                assert current.json()["data"]["license_id_hint"] == "…1-001"
                assert current.json()["data"]["can_apply"] is True
                assert "signature" not in applied.text and "claims" not in applied.text
                consume.assert_called_once()
                assert consume.call_args.kwargs["action_group"] == "organization_security_or_connector_policy_change"
                assert consume.call_args.kwargs["operation"] == "license.organization.apply"

                license_service._verifier = UnavailableLicenseVerifier()
                license_service._clock = lambda: NOW + timedelta(days=730)
                with patch.object(
                    identity, "describe_access", return_value=admin_view,
                ), patch.object(
                    identity, "consume_step_up", side_effect=AssertionError("replay must not consume Step-up"),
                ) as replay_consume:
                    replay = await client.post(
                        "/api/v1/organizations/tenant-001/license",
                        cookies={WEB_SESSION_COOKIE: "opaque-session"},
                        headers={"Idempotency-Key": "license-http-idem-0002"},
                        json={"document": document, "step_up_authorization_id": "revoked-step-up"},
                    )
                    mismatched = json.loads(json.dumps(document))
                    mismatched["claims"]["license_id"] = "license-release-1-999"
                    conflict = await client.post(
                        "/api/v1/organizations/tenant-001/license",
                        cookies={WEB_SESSION_COOKIE: "opaque-session"},
                        headers={"Idempotency-Key": "license-http-idem-0002"},
                        json={"document": mismatched, "step_up_authorization_id": "revoked-step-up"},
                    )
                assert replay.status_code == 200
                assert replay.json()["meta"]["replayed"] is True
                assert replay.json()["data"]["license_id_hint"] == "…1-001"
                assert (conflict.status_code, conflict.json()["error"]["code"]) == (409, "IDEMPOTENCY_KEY_REUSED")
                replay_consume.assert_not_called()
                assert license_repository.count(LicenseContext(
                    "tenant-001", "workspace-001", "org-admin", "trace-count", POLICY_VERSION,
                )) == 1
                license_service._clock = lambda: NOW
                usage["generation_runs"] = 100
                generation = {
                    "workspace_id": "workspace-001", "notebook_id": "notebook-license-limit",
                    "output_type": "evidence_report", "source_id": "source-1",
                    "source_version_ids": ["source-version-1"], "run_id": "run-1", "run_result_id": "result-1",
                    "settings": {"purpose": "목적", "audience": "독자", "source_version_ids": ["source-version-1"],
                                 "ruleset_version_id": None, "length": "short", "structure": "summary",
                                 "output_format": "pdf", "review_condition": "review_required"},
                }
                with patch.object(identity, "describe_access", return_value=admin_view):
                    blocked = await client.post(
                        "/api/v1/studio-generation-requests", cookies={WEB_SESSION_COOKIE: "opaque-session"},
                        headers={"Idempotency-Key": "license-limit-generation-0001"}, json=generation,
                    )
                    readable = await client.get(
                        "/api/v1/workspaces/workspace-001/license", cookies={WEB_SESSION_COOKIE: "opaque-session"},
                    )
                assert (blocked.status_code, blocked.json()["error"]["code"]) == (409, "LICENSE_RESOURCE_LIMIT_REACHED")
                assert readable.status_code == 200 and readable.json()["data"]["existing_read_allowed"] is True
                assert studio.calls == 0
        finally:
            dependencies.close()
