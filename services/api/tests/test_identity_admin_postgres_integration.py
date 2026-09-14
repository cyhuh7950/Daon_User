from __future__ import annotations

import os
from datetime import datetime, timezone

import pytest

from daon_user_api.audit import AuditEventStore
from daon_user_api.identity import (
    DevicePlatform,
    IdentityError,
    IdentityService,
    INITIAL_ADMIN_PASSWORD,
    PASSWORD_HASHER,
)
from daon_user_api.identity_postgres import PostgresIdentityRepository
from test_identity_support import POLICY_VERSION, TRACE_ID


@pytest.mark.skipif(
    not os.environ.get("DAON_TEST_POSTGRES_DSN"),
    reason="isolated PostgreSQL test DSN is not configured",
)
def test_postgres_0038_persists_admin_bootstrap_and_password_change() -> None:
    repository = PostgresIdentityRepository(os.environ["DAON_TEST_POSTGRES_DSN"])
    service = IdentityService(
        repository=repository,
        audit_store=AuditEventStore(),
        oidc_policies=(),
        clock=lambda: datetime.now(timezone.utc),
    )
    replacement = "R" * 20 + "!a9"
    try:
        service.ensure_initial_admin()
        service.ensure_initial_admin()
        repository.close()
        repository = PostgresIdentityRepository(os.environ["DAON_TEST_POSTGRES_DSN"])
        service = IdentityService(
            repository=repository,
            audit_store=AuditEventStore(),
            oidc_policies=(),
            clock=lambda: datetime.now(timezone.utc),
        )
        service.ensure_initial_admin()
        credentials = service.local_login(
            login_id="admin",
            password=INITIAL_ADMIN_PASSWORD,
            platform=DevicePlatform.WEB,
            trace_id=TRACE_ID,
            policy_version=POLICY_VERSION,
        )
        service.change_current_password(
            access_token=credentials.access_token,
            current_password=INITIAL_ADMIN_PASSWORD,
            new_password=replacement,
            trace_id=TRACE_ID,
            policy_version=POLICY_VERSION,
        )

        with repository.transaction() as connection:
            marker = connection.execute(
                "SELECT marker_key FROM bootstrap_state WHERE marker_key=?",
                ("initial_admin_v1",),
            ).fetchone()
            admin = connection.execute(
                "SELECT password_digest,password_change_required FROM users WHERE user_id=?",
                ("admin",),
            ).fetchone()
        assert marker is not None
        assert admin is not None
        assert admin["password_change_required"] is False
        assert PASSWORD_HASHER.verify(str(admin["password_digest"]), replacement)
        with pytest.raises(IdentityError):
            service.local_login(
                login_id="admin",
                password=INITIAL_ADMIN_PASSWORD,
                platform=DevicePlatform.WEB,
                trace_id=TRACE_ID,
                policy_version=POLICY_VERSION,
            )
    finally:
        repository.close()
