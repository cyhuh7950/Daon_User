"""Seeded process fixture for the R1-M4-07 browser/runtime verification."""

from __future__ import annotations

import json
import os
from datetime import datetime, timezone
from pathlib import Path

from fastapi.responses import RedirectResponse

from test_identity_support import (
    POLICY_VERSION,
    TRACE_ID,
    FakeVerifiedOidcProvider,
    create_service,
)
from daon_user_api.audit import AuditEventStore
from daon_user_api.authorization import Role, SqliteAuthorizationRepository, AuthorizationService
from daon_user_api.identity import ClientKind, DevicePlatform
from daon_user_api.notification import (
    InboxRequest,
    NotificationEvent,
    NotificationService,
    ReferenceNotificationRepository,
)
from daon_user_api.runtime import RuntimeDependencies, RuntimeSettings, create_app


fixture_root = Path(os.environ["DAON_NOTIFICATION_FIXTURE_ROOT"]).resolve()
fixture_root.mkdir(parents=True, exist_ok=True)
database_path = fixture_root / "notification-runtime.sqlite3"
audit_store = AuditEventStore()
identity, identity_repository, _, clock = create_service(database_path, audit_store=audit_store)
start = identity.begin_oidc_login(
    issuer="https://login.example.com",
    client_id="daon-web",
    audience="daon-user-api",
    redirect_uri="https://app.example.com/auth/callback",
    client_kind=ClientKind.WEB,
    tenant_id="tenant-001",
    trace_id=TRACE_ID,
    policy_version=POLICY_VERSION,
)
provider = FakeVerifiedOidcProvider()
provider.expected_nonce = start.nonce
session = identity.complete_oidc_login(
    state=start.state,
    authorization_code=provider.authorization_code,
    code_verifier=start.code_verifier,
    client_id="daon-web",
    redirect_uri="https://app.example.com/auth/callback",
    provider=provider,
    platform=DevicePlatform.WEB,
    trace_id=TRACE_ID,
    policy_version=POLICY_VERSION,
)
principal = identity.describe_access(
    session.access_token, trace_id=TRACE_ID, policy_version=POLICY_VERSION
).principal
authorization_repository = SqliteAuthorizationRepository(database_path)
authorization_repository.bootstrap_workspace(
    tenant_id=session.tenant_id,
    workspace_id="workspace-001",
    owner_user_id=session.user_id,
    owner_role=Role.ORGANIZATION_ADMIN,
    workspace_kind="organization",
    data_area="cloud_sync",
    cost_limit_cents=1000,
    now=clock(),
)
authorization = AuthorizationService(
    repository=authorization_repository,
    audit_store=audit_store,
    clock=clock,
    identity_service=identity,
)
notification = NotificationService(
    repository=ReferenceNotificationRepository(),
    authorization_service=authorization,
    audit_store=audit_store,
    clock=clock,
)
notification.publish(
    NotificationEvent(
        event_id="event-browser-run-failed-001",
        tenant_id=session.tenant_id,
        workspace_id="workspace-001",
        kind="run_failed",
        severity="warning",
        title="실행 실패 알림",
        summary="실행 상태와 복구 조치를 확인하세요.",
        resource_type="run",
        resource_id="run-browser-001",
        deep_link="/operations?run=run-browser-001",
        trace_id="trace-browser-notification-001",
        policy_version=POLICY_VERSION,
    ),
    candidates=(principal,),
)
if os.environ.get("DAON_NOTIFICATION_CONCURRENT_FIXTURE") == "1":
    notification.publish(
        NotificationEvent(
            event_id="event-concurrent-policy-001",
            tenant_id=session.tenant_id,
            workspace_id="workspace-001",
            kind="policy",
            severity="info",
            title="동시성 검증 알림",
            summary="원자적 읽음 전이의 실제 HTTP 경쟁을 검증합니다.",
            resource_type="policy",
            resource_id="policy-concurrent-001",
            deep_link="/notifications?notification=concurrent",
            trace_id="trace-concurrent-notification-001",
            policy_version=POLICY_VERSION,
        ),
        candidates=(principal,),
    )
notification.project_request(InboxRequest(
    request_id="approval-browser-001",
    request_kind="approval",
    status="pending",
    tenant_id=session.tenant_id,
    workspace_id="workspace-001",
    recipient_id=session.user_id,
    actor_id="actor-browser-001",
    due_at=datetime.now(timezone.utc),
    resource_type="output_version",
    resource_id="output-browser-001",
    deep_link="/operations?request=approval-browser-001",
))

settings = RuntimeSettings.for_test(database_path=database_path, policy_version=POLICY_VERSION)
dependencies = RuntimeDependencies(
    settings=settings,
    identity_service=identity,
    authorization_service=authorization,
    audit_store=audit_store,
    identity_repository=identity_repository,
    authorization_repository=authorization_repository,
    notification_service=notification,
)
app = create_app(dependencies)


@app.get("/__test__/session", include_in_schema=False)
async def browser_session() -> RedirectResponse:
    response = RedirectResponse(os.environ["DAON_NOTIFICATION_WEB_ORIGIN"], status_code=302)
    response.set_cookie(
        key="__Host-daon_session",
        value=session.access_token,
        secure=True,
        httponly=True,
        samesite="strict",
        path="/",
    )
    return response

control_path = fixture_root / "api-control.json"
control_path.write_text(
    json.dumps({"access_token": session.access_token, "user_id": session.user_id}),
    encoding="utf-8",
)
if os.name != "nt":
    control_path.chmod(0o600)
