from __future__ import annotations

import os
import secrets
from datetime import datetime, timezone

import pytest

from daon_user_api.admin_users import AdminUserService
from daon_user_api.audit import AuditEventStore
from daon_user_api.identity import (
    DevicePlatform,
    IdentityError,
    IdentityPrincipal,
    IdentityService,
    PASSWORD_HASHER,
)
from daon_user_api.identity_postgres import PostgresIdentityRepository
from test_identity_support import POLICY_VERSION, TRACE_ID


@pytest.mark.skipif(
    not os.environ.get("DAON_TEST_POSTGRES_DSN"),
    reason="isolated PostgreSQL test DSN is not configured",
)
def test_postgres_admin_recovery_and_user_suspension_revoke_sessions() -> None:
    repository = PostgresIdentityRepository(os.environ["DAON_TEST_POSTGRES_DSN"])
    audit = AuditEventStore()
    clock = lambda: datetime.now(timezone.utc)
    identity = IdentityService(
        repository=repository,
        audit_store=audit,
        oidc_policies=(),
        clock=clock,
    )
    try:
        identity.ensure_initial_admin()
        identity.reset_initial_admin_password(
            trace_id="trace-postgres-admin-recovery", policy_version=POLICY_VERSION,
        )
        user_password = secrets.token_urlsafe(24)
        with repository.transaction() as connection:
            repository._ensure_tenant(connection, "postgres-user")
            connection.execute(
                "INSERT INTO users(user_id,issuer,subject,login_id,email,password_digest,"
                "email_verified_at,password_change_required,state) VALUES (?,?,?,?,?,?,?,?,?)",
                (
                    "postgres-user", "local", "postgres-user", "postgres-user",
                    "postgres-user@example.test", PASSWORD_HASHER.hash(user_password),
                    datetime.now(timezone.utc), False, "active",
                ),
            )
            connection.execute(
                "INSERT INTO memberships(tenant_id,user_id,role) VALUES (?,?,?)",
                ("postgres-user", "postgres-user", "personal_owner"),
            )
        credentials = identity.local_login(
            login_id="postgres-user",
            password=user_password,
            platform=DevicePlatform.WEB,
            trace_id=TRACE_ID,
            policy_version=POLICY_VERSION,
        )
        service = AdminUserService(
            repository=repository,
            audit_store=audit,
            system_admin_user_ids=frozenset({"admin"}),
            clock=clock,
        )
        result = service.change_state(
            IdentityPrincipal("admin", "admin-session", "admin-device", "admin"),
            user_id="postgres-user",
            state="suspended",
            idempotency_key="postgres-suspend-0001",
            trace_id=TRACE_ID,
            policy_version=POLICY_VERSION,
        )
        assert result.user.state == "suspended"
        with repository.transaction() as connection:
            intent = connection.execute(
                "SELECT delivered_at,metadata_json FROM admin_audit_outbox "
                "WHERE operation='change_user_state' AND target_id='postgres-user'"
            ).fetchone()
        assert intent["delivered_at"] is not None
        assert "password" not in str(intent["metadata_json"]).lower()
        with pytest.raises(IdentityError) as revoked:
            identity.validate_access(
                credentials.access_token,
                trace_id=TRACE_ID,
                policy_version=POLICY_VERSION,
            )
        assert revoked.value.code == "SESSION_REVOKED"
    finally:
        repository.close()
