"""Explicit FastAPI process composition for the approved M4 domain cores."""

from __future__ import annotations

import hashlib
import ipaddress
import asyncio
import base64
import binascii
import os
import re
import secrets
import threading
import time
from contextlib import asynccontextmanager
from dataclasses import dataclass, field, fields, is_dataclass
from datetime import datetime, timezone
from enum import Enum
from pathlib import Path
from typing import Any, AsyncIterator, Mapping, cast
from urllib.parse import urlsplit

from fastapi import FastAPI, Header, HTTPException, Query, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from fastapi.routing import APIRoute
from pydantic import BaseModel, ConfigDict
from starlette.exceptions import HTTPException as StarletteHTTPException
from starlette.responses import Response

from .audit import AuditEvent, AuditEventStore, AuditOutcome, AuditValidationError
from .cloud_storage import PostgresCloudStore
from .authorization import (
    AccessAction,
    AccessDecision,
    Action,
    AuthorizationError,
    AuthorizationGrant,
    AuthorizationService,
    Permission,
    SqliteAuthorizationRepository,
)
from .identity import (
    ClientKind,
    IdentityError,
    IdentityPrincipal,
    IdentityService,
    SqliteIdentityRepository,
)
from .notification import (
    NotificationError,
    NotificationService,
    ReferenceNotificationRepository,
    inbox_json,
    notification_json,
    parse_inbox_filter,
    parse_notification_filter,
)
from .object_queue import (
    MinioObjectStorageAdapter,
    ObjectQueueCoordinator,
    ObjectStoragePort,
    PostgresObjectQueueStore,
)
from .sync import (
    ConflictResolutionChoice,
    ReferenceSyncRepository,
    ReferenceTransferPort,
    SyncContext,
    SyncError,
    SyncItemInput,
    SyncOperationView,
    SyncService,
    TransferPayload,
)
from .sync_postgres import (
    ObjectQueueSyncTransferPort,
    PostgresSyncService,
    UnavailableSyncTransferPort,
)
from .retention import (
    DerivativeInput, ReferenceCleanupPort, ReferenceRetentionRepository,
    RetentionContext, RetentionError, RetentionService,
)
from .recovery import (
    BackupObjectInput, RecoveryContext, RecoveryError, RecoveryService,
    ReferenceRecoveryRepository, ReferenceRestorePort, RestoreDestination,
)


WEB_SESSION_COOKIE = "__Host-daon_session"
_TRACE_ID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:-]{0,127}$")
_TRACEPARENT = re.compile(
    r"^[0-9a-f]{2}-([0-9a-f]{32})-[0-9a-f]{16}-[0-9a-f]{2}$"
)
_BODY_METHODS = frozenset({"POST", "PUT", "PATCH"})


@dataclass(frozen=True, slots=True)
class RuntimeSettings:
    profile: str
    bind_host: str
    port: int
    database_path: Path | None = None
    cloud_database_dsn: str | None = None
    object_storage_endpoint: str | None = None
    object_storage_bucket: str | None = None
    object_access_key_file: Path | None = None
    object_secret_key_file: Path | None = None
    object_storage_secure: bool = True
    policy_version: str = "runtime-policy-v1"
    public_gateway_url: str | None = None
    trusted_proxy_ips: tuple[str, ...] = ()
    max_body_bytes: int = 65_536
    max_header_bytes: int = 16_384
    request_timeout_seconds: float = 30.0
    drain_timeout_seconds: float = 10.0

    def __post_init__(self) -> None:
        if self.profile not in {"test", "development", "production"}:
            raise ValueError("RUNTIME_PROFILE_INVALID")
        if not isinstance(self.port, int) or isinstance(self.port, bool) or not 1 <= self.port <= 65_535:
            raise ValueError("RUNTIME_PORT_INVALID")
        if (
            self.max_body_bytes < 1
            or self.max_header_bytes < 1
            or self.request_timeout_seconds <= 0
            or self.drain_timeout_seconds <= 0
        ):
            raise ValueError("RUNTIME_LIMIT_INVALID")
        object_fields = (
            self.object_storage_endpoint,
            self.object_storage_bucket,
            self.object_access_key_file,
            self.object_secret_key_file,
        )
        if any(value is not None for value in object_fields) and not all(
            value is not None for value in object_fields
        ):
            raise ValueError("OBJECT_STORAGE_CONFIGURATION_INCOMPLETE")
        if self.profile in {"test", "development"}:
            try:
                loopback = ipaddress.ip_address(self.bind_host).is_loopback
            except ValueError:
                loopback = self.bind_host == "localhost"
            if not loopback:
                raise ValueError("PLAINTEXT_BIND_MUST_BE_LOOPBACK")
        else:
            if self.cloud_database_dsn is None:
                raise ValueError("CLOUD_DATABASE_DSN_REQUIRED")
            if self.public_gateway_url is None:
                raise ValueError("PUBLIC_GATEWAY_REQUIRED")
            parsed = urlsplit(self.public_gateway_url)
            if (
                parsed.scheme != "https"
                or not parsed.hostname
                or parsed.username is not None
                or parsed.password is not None
                or parsed.path not in {"", "/"}
                or parsed.query
                or parsed.fragment
            ):
                raise ValueError("PUBLIC_GATEWAY_HTTPS_REQUIRED")
            if not self.trusted_proxy_ips:
                raise ValueError("TRUSTED_PROXY_REQUIRED")
            for address in self.trusted_proxy_ips:
                ipaddress.ip_address(address)

    @classmethod
    def for_test(cls, *, database_path: Path, policy_version: str) -> "RuntimeSettings":
        return cls(
            profile="test",
            bind_host="127.0.0.1",
            port=8000,
            database_path=database_path,
            policy_version=policy_version,
        )

    @classmethod
    def from_env(cls) -> "RuntimeSettings":
        profile = os.environ.get("DAON_RUNTIME_PROFILE", "development")
        database = os.environ.get("DAON_API_DATABASE_PATH")
        proxies = tuple(
            value.strip()
            for value in os.environ.get("DAON_TRUSTED_PROXY_IPS", "").split(",")
            if value.strip()
        )
        return cls(
            profile=profile,
            bind_host=os.environ.get("DAON_API_BIND_HOST", "127.0.0.1"),
            port=int(os.environ.get("DAON_API_PORT", "8000")),
            database_path=None if database is None else Path(database),
            cloud_database_dsn=os.environ.get("DAON_CLOUD_DATABASE_DSN"),
            object_storage_endpoint=os.environ.get("DAON_OBJECT_STORAGE_ENDPOINT"),
            object_storage_bucket=os.environ.get("DAON_OBJECT_STORAGE_BUCKET"),
            object_access_key_file=(
                None
                if os.environ.get("DAON_OBJECT_ACCESS_KEY_FILE") is None
                else Path(os.environ["DAON_OBJECT_ACCESS_KEY_FILE"])
            ),
            object_secret_key_file=(
                None
                if os.environ.get("DAON_OBJECT_SECRET_KEY_FILE") is None
                else Path(os.environ["DAON_OBJECT_SECRET_KEY_FILE"])
            ),
            object_storage_secure=os.environ.get("DAON_OBJECT_STORAGE_SECURE", "true").lower() == "true",
            policy_version=os.environ.get("DAON_POLICY_VERSION", "runtime-policy-v1"),
            public_gateway_url=os.environ.get("DAON_PUBLIC_GATEWAY_URL"),
            trusted_proxy_ips=proxies,
        )


class RuntimeState:
    def __init__(self) -> None:
        self._condition = threading.Condition()
        self._accepting = True
        self._ready = True
        self._inflight = 0

    @property
    def accepting(self) -> bool:
        with self._condition:
            return self._accepting

    @property
    def ready(self) -> bool:
        with self._condition:
            return self._ready

    def begin_request(self) -> bool:
        with self._condition:
            if not self._accepting:
                return False
            self._inflight += 1
            return True

    def end_request(self) -> None:
        with self._condition:
            self._inflight = max(0, self._inflight - 1)
            self._condition.notify_all()

    def begin_shutdown(self) -> None:
        with self._condition:
            self._accepting = False
            self._ready = False
            self._condition.notify_all()

    def drain(self, timeout_seconds: float) -> bool:
        deadline = time.monotonic() + timeout_seconds
        with self._condition:
            while self._inflight:
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    return False
                self._condition.wait(remaining)
            return True


@dataclass(slots=True)
class RuntimeDependencies:
    settings: RuntimeSettings
    identity_service: IdentityService
    authorization_service: AuthorizationService
    audit_store: AuditEventStore
    identity_repository: SqliteIdentityRepository
    authorization_repository: SqliteAuthorizationRepository
    notification_service: NotificationService | None = None
    cloud_store: PostgresCloudStore | None = None
    object_storage: ObjectStoragePort | None = None
    sync_service: SyncService | PostgresSyncService | None = None
    retention_service: RetentionService | None = None
    recovery_service: RecoveryService | None = None
    object_queue_store: PostgresObjectQueueStore | None = None
    state: RuntimeState = field(default_factory=RuntimeState)
    _closed: bool = field(default=False, init=False, repr=False)

    def close(self) -> None:
        if self._closed:
            return
        self.state.begin_shutdown()
        self.state.drain(self.settings.drain_timeout_seconds)
        self.authorization_repository.close()
        self.identity_repository.close()
        if self.cloud_store is not None:
            self.cloud_store.close()
        if self.object_queue_store is not None:
            self.object_queue_store.close()
        self._closed = True


class AuthorizationEvaluationBody(BaseModel):
    model_config = ConfigDict(extra="forbid")
    action: Action
    requested_permissions: list[Permission]


class AccessDecisionBody(BaseModel):
    model_config = ConfigDict(extra="forbid")
    resource_id: str
    action: AccessAction


class NotificationReadBody(BaseModel):
    model_config = ConfigDict(extra="forbid")
    state: str


class SyncItemBody(BaseModel):
    model_config = ConfigDict(extra="forbid")
    item_id: str
    source_version_id: str
    local_object_id: str
    digest_sha256: str
    byte_size: int
    content_type: str
    base_cloud_version_id: str | None = None
    base_cloud_digest: str | None = None


class SyncCreateBody(BaseModel):
    model_config = ConfigDict(extra="forbid")
    target_area: str
    items: list[SyncItemBody]


class SyncApproveBody(BaseModel):
    model_config = ConfigDict(extra="forbid")
    approved_item_ids: list[str]
    step_up_authorization_id: str


class SyncTransferItemBody(BaseModel):
    model_config = ConfigDict(extra="forbid")
    item_id: str
    content_base64: str
    current_cloud_version_id: str | None = None
    current_cloud_digest: str | None = None


class SyncTransferBody(BaseModel):
    model_config = ConfigDict(extra="forbid")
    cursor: str | None = None
    items: list[SyncTransferItemBody]


class SyncResolutionBody(BaseModel):
    model_config = ConfigDict(extra="forbid")
    choice: ConflictResolutionChoice
    content_base64: str | None = None


class RetentionDerivativeBody(BaseModel):
    model_config = ConfigDict(extra="forbid")
    kind: str
    reference_id: str
    acknowledgement_required: bool = False


class DeletionCreateBody(BaseModel):
    model_config = ConfigDict(extra="forbid")
    inventory: list[RetentionDerivativeBody]


class SensitiveRetentionBody(BaseModel):
    model_config = ConfigDict(extra="forbid")
    step_up_authorization_id: str


class BackupObjectBody(BaseModel):
    model_config = ConfigDict(extra="forbid")
    object_id: str
    checksum_sha256: str
    byte_size: int


class BackupCreateBody(BaseModel):
    model_config = ConfigDict(extra="forbid")
    workspace_id: str
    trigger: str
    schema_revision: str
    retention_watermark: str
    objects: list[BackupObjectBody]


class RestoreDestinationBody(BaseModel):
    model_config = ConfigDict(extra="forbid")
    tenant_id: str
    workspace_id: str
    database_id: str
    bucket_id: str


class RestorePreviewBody(BaseModel):
    model_config = ConfigDict(extra="forbid")
    destination: RestoreDestinationBody
    step_up_authorization_id: str


class RestoreExecuteBody(BaseModel):
    model_config = ConfigDict(extra="forbid")
    preview_version: int
    step_up_authorization_id: str


def _trace_id(request: Request) -> str:
    traceparent = request.headers.get("traceparent", "").lower()
    matched = _TRACEPARENT.fullmatch(traceparent)
    if matched and matched.group(1) != "0" * 32:
        return matched.group(1)
    supplied = request.headers.get("x-trace-id", "")
    if _TRACE_ID.fullmatch(supplied):
        return supplied
    return f"trace-{secrets.token_urlsafe(24)}"


def _error_payload(
    *, code: str, trace_id: str, retryable: bool = False, message: str = "요청을 처리하지 못했습니다."
) -> dict[str, object]:
    return {
        "error": {
            "code": code,
            "message": message,
            "stage": "request",
            "impact": "request_not_completed",
            "retryable": retryable,
            "user_action": "잠시 후 다시 시도하거나 관리자에게 문의하세요." if retryable else "입력과 권한을 확인하세요.",
            "trace_id": trace_id,
            "details": {},
        }
    }


def _error_response(status: int, code: str, trace_id: str, *, retryable: bool = False) -> JSONResponse:
    response = JSONResponse(
        status_code=status,
        content=_error_payload(code=code, trace_id=trace_id, retryable=retryable),
    )
    response.headers["X-Trace-Id"] = trace_id
    response.headers["Cache-Control"] = "no-store"
    return response


def _domain_error(error: IdentityError | AuthorizationError) -> tuple[int, str, bool]:
    status = error.http_status
    if status == 401:
        return 401, "AUTHENTICATION_REQUIRED", False
    if status == 404:
        return 404, "RESOURCE_UNAVAILABLE", False
    if status == 403 and error.code == "ACTION_DENIED":
        return 403, "FORBIDDEN", False
    safe_special = {"CURRENT_ACCESS_DENIED", "STEP_UP_REQUIRED", "VERSION_CONFLICT"}
    return status, error.code if error.code in safe_special else "INVALID_REQUEST", status >= 500


def _notification_domain_error(error: NotificationError) -> tuple[int, str, bool]:
    safe_codes = {
        "CURRENT_ACCESS_DENIED", "RESOURCE_UNAVAILABLE", "VERSION_CONFLICT",
        "IDEMPOTENCY_CONFLICT", "INVALID_STATE_TRANSITION", "INVALID_CURSOR",
        "INVALID_FILTER", "UNSAFE_DEEP_LINK",
    }
    return error.http_status, error.code if error.code in safe_codes else "INVALID_REQUEST", False


_RETENTION_PUBLIC_CODES = {
    "DELETION_GRACE_PERIOD_ACTIVE": "DELETION_GRACE_PERIOD_ACTIVE",
    "LEGAL_HOLD_ACTIVE": "LEGAL_HOLD_ACTIVE",
    "DELETION_CLEANUP_PENDING": "DELETION_CLEANUP_PENDING",
    "STEP_UP_REQUIRED": "STEP_UP_REQUIRED",
    "CURRENT_ACCESS_DENIED": "CURRENT_ACCESS_DENIED",
    "DELETION_REQUEST_UNAVAILABLE": "RESOURCE_UNAVAILABLE",
    "LEGAL_HOLD_UNAVAILABLE": "RESOURCE_UNAVAILABLE",
    "SOURCE_UNAVAILABLE": "RESOURCE_UNAVAILABLE",
    "FIXTURE_ONLY_PURGE_REQUIRED": "CURRENT_ACCESS_DENIED",
}


def _retention_public_error_code(code: str) -> str:
    return _RETENTION_PUBLIC_CODES.get(code, "INVALID_REQUEST")


_RECOVERY_PUBLIC_CODES = {
    "CURRENT_ACCESS_DENIED": "CURRENT_ACCESS_DENIED",
    "STEP_UP_REQUIRED": "STEP_UP_REQUIRED",
    "BACKUP_UNAVAILABLE": "RESOURCE_UNAVAILABLE",
    "RESTORE_REQUEST_UNAVAILABLE": "RESOURCE_UNAVAILABLE",
    "RESOURCE_UNAVAILABLE": "RESOURCE_UNAVAILABLE",
    "FIXTURE_ONLY_RESTORE_REQUIRED": "CURRENT_ACCESS_DENIED",
    "IN_PLACE_RESTORE_FORBIDDEN": "CURRENT_ACCESS_DENIED",
}


def _recovery_public_error_code(code: str) -> str:
    return _RECOVERY_PUBLIC_CODES.get(code, "INVALID_REQUEST")


def _require_query_keys(request: Request, allowed: frozenset[str]) -> None:
    if any(key not in allowed for key in request.query_params):
        raise HTTPException(status_code=400)


def _json_with_etag(content: dict[str, object], etag_seed: str) -> JSONResponse:
    response = JSONResponse(content=content)
    digest = hashlib.sha256(etag_seed.encode("utf-8")).hexdigest()[:24]
    response.headers["ETag"] = f'"projection-{digest}"'
    return response


def _enum_json(value: object) -> object:
    if isinstance(value, Enum):
        return value.value
    if isinstance(value, datetime):
        return value.isoformat()
    if is_dataclass(value) and not isinstance(value, type):
        return _dataclass_json(value)
    if isinstance(value, Mapping):
        return {str(key): _enum_json(item) for key, item in value.items()}
    if isinstance(value, (tuple, list)):
        return [_enum_json(item) for item in value]
    return value


def _dataclass_json(value: object) -> dict[str, object]:
    return {
        item.name: _enum_json(getattr(value, item.name))
        for item in fields(value)  # type: ignore[arg-type]
    }


def _grant_json(grant: AuthorizationGrant) -> dict[str, object]:
    value = _dataclass_json(grant)
    value.pop("tenant_id", None)
    return value


def _decision_json(decision: AccessDecision) -> dict[str, object]:
    value = _dataclass_json(decision)
    value.pop("tenant_id", None)
    return value


def _audit_json(event: AuditEvent) -> dict[str, object]:
    return _dataclass_json(event)


def _credential(request: Request) -> tuple[str, ClientKind]:
    cookie = request.cookies.get(WEB_SESSION_COOKIE)
    authorization = request.headers.get("authorization")
    if cookie and authorization:
        raise IdentityError("ACCESS_INVALID", 401)
    if cookie:
        return cookie, ClientKind.WEB
    if authorization:
        scheme, separator, value = authorization.partition(" ")
        if scheme.lower() != "bearer" or not separator or not value or value != value.strip():
            raise IdentityError("ACCESS_INVALID", 401)
        return value, ClientKind.NATIVE
    raise IdentityError("ACCESS_INVALID", 401)


def _idempotency_key(value: str) -> str:
    if not _TRACE_ID.fullmatch(value):
        raise HTTPException(status_code=400)
    return value


def _sync_expected_version(value: str, operation_id: str | None = None) -> int | str:
    if value == "*" and operation_id is None:
        return value
    if operation_id is None:
        raise SyncError("IF_MATCH_INVALID", 400)
    matched = re.fullmatch(r'"sync:' + re.escape(operation_id) + r':([1-9][0-9]*)"', value)
    if matched is None:
        raise SyncError("IF_MATCH_INVALID", 400)
    return int(matched.group(1))


def _sync_content(value: str | None, *, required: bool) -> bytes | None:
    if value is None:
        if required:
            raise SyncError("SYNC_CONTENT_REQUIRED", 400)
        return None
    try:
        return base64.b64decode(value, validate=True)
    except (binascii.Error, ValueError):
        raise SyncError("SYNC_CONTENT_INVALID", 400) from None


def _sync_context(principal: IdentityPrincipal, workspace_id: str, request: Request,
                  dependencies: RuntimeDependencies) -> SyncContext:
    return SyncContext(
        tenant_id=principal.tenant_id,
        workspace_id=workspace_id,
        actor_id=principal.user_id,
        trace_id=request.state.trace_id,
        policy_version=dependencies.settings.policy_version,
    )


def _sync_view_json(view: SyncOperationView) -> dict[str, object]:
    return _dataclass_json(view)


def _retention_expected_version(value: str, resource_id: str, kind: str) -> int:
    matched = re.fullmatch(
        r'"' + re.escape(kind) + r':' + re.escape(resource_id) + r':([1-9][0-9]*)"', value
    )
    if matched is None:
        raise RetentionError("IF_MATCH_INVALID", 400)
    return int(matched.group(1))


def _retention_context(
    principal: IdentityPrincipal, workspace_id: str, request: Request,
    dependencies: RuntimeDependencies, *, organization_admin: bool = False,
) -> RetentionContext:
    return RetentionContext(
        principal.tenant_id, workspace_id, principal.user_id, request.state.trace_id,
        dependencies.settings.policy_version, organization_admin,
    )


def _recovery_expected_version(value: str, resource_id: str) -> int:
    matched = re.fullmatch(
        r'"restore:' + re.escape(resource_id) + r':([1-9][0-9]*)"', value
    )
    if matched is None:
        raise RecoveryError("IF_MATCH_INVALID", 400)
    return int(matched.group(1))


def _recovery_context(
    principal: IdentityPrincipal, workspace_id: str, request: Request,
    dependencies: RuntimeDependencies, *, organization_admin: bool = True,
) -> RecoveryContext:
    return RecoveryContext(
        principal.tenant_id, workspace_id, principal.user_id, request.state.trace_id,
        dependencies.settings.policy_version, organization_admin,
    )


def _principal(request: Request, dependencies: RuntimeDependencies) -> IdentityPrincipal:
    token, expected_kind = _credential(request)
    view = dependencies.identity_service.describe_access(
        token,
        trace_id=request.state.trace_id,
        policy_version=dependencies.settings.policy_version,
    )
    if view.client_kind is not expected_kind:
        raise IdentityError("ACCESS_INVALID", 401)
    request.state.session_view = view
    return view.principal


def create_app(dependencies: RuntimeDependencies) -> FastAPI:
    notification_service = dependencies.notification_service or NotificationService(
        repository=ReferenceNotificationRepository(),
        authorization_service=dependencies.authorization_service,
        audit_store=dependencies.audit_store,
        clock=lambda: datetime.now(timezone.utc),
    )
    sync_service = dependencies.sync_service or SyncService(
        ReferenceSyncRepository(), ReferenceTransferPort(),
        clock=lambda: datetime.now(timezone.utc),
    )
    retention_service = dependencies.retention_service or RetentionService(
        ReferenceRetentionRepository(), ReferenceCleanupPort(),
        clock=lambda: datetime.now(timezone.utc),
    )
    recovery_service = dependencies.recovery_service or RecoveryService(
        ReferenceRecoveryRepository(), ReferenceRestorePort(),
        clock=lambda: datetime.now(timezone.utc),
    )
    @asynccontextmanager
    async def lifespan(_app: FastAPI) -> AsyncIterator[None]:
        yield
        dependencies.close()

    class TimedApiRoute(APIRoute):
        def get_route_handler(self) -> Any:
            original = super().get_route_handler()

            async def timed_handler(request: Request) -> Response:
                try:
                    async with asyncio.timeout(dependencies.settings.request_timeout_seconds):
                        return cast(Response, await original(request))
                except TimeoutError:
                    return _error_response(
                        504, "REQUEST_TIMEOUT", request.state.trace_id, retryable=True
                    )

            return timed_handler

    app = FastAPI(title="Daon User API", version="1.0.0", lifespan=lifespan)
    app.router.route_class = TimedApiRoute

    @app.middleware("http")
    async def runtime_boundary(request: Request, call_next: Any) -> Response:
        trace_id = _trace_id(request)
        request.state.trace_id = trace_id
        is_health = request.url.path in {"/health/live", "/health/ready"}
        counted = False
        if not is_health:
            if not dependencies.state.begin_request():
                return _error_response(503, "SHUTTING_DOWN", trace_id, retryable=True)
            counted = True
        try:
            if dependencies.settings.profile == "production" and request.url.scheme != "https":
                return _error_response(400, "HTTPS_REQUIRED", trace_id)
            if sum(len(key) + len(value) for key, value in request.scope["headers"]) > dependencies.settings.max_header_bytes:
                return _error_response(431, "REQUEST_HEADERS_TOO_LARGE", trace_id)
            if request.method in _BODY_METHODS:
                content_type = request.headers.get("content-type", "").split(";", 1)[0].strip().lower()
                if content_type != "application/json":
                    return _error_response(415, "UNSUPPORTED_MEDIA_TYPE", trace_id)
                declared = request.headers.get("content-length")
                if declared is not None:
                    try:
                        if int(declared) > dependencies.settings.max_body_bytes:
                            return _error_response(413, "REQUEST_TOO_LARGE", trace_id)
                    except ValueError:
                        return _error_response(400, "INVALID_REQUEST", trace_id)
                body = await request.body()
                if len(body) > dependencies.settings.max_body_bytes:
                    return _error_response(413, "REQUEST_TOO_LARGE", trace_id)
            response = cast(Response, await call_next(request))
            response.headers["X-Trace-Id"] = trace_id
            response.headers["Cache-Control"] = "no-store"
            response.headers["X-Content-Type-Options"] = "nosniff"
            response.headers["Referrer-Policy"] = "no-referrer"
            return response
        finally:
            if counted:
                dependencies.state.end_request()

    @app.exception_handler(RequestValidationError)
    async def validation_error(request: Request, _error: RequestValidationError) -> JSONResponse:
        return _error_response(400, "INVALID_REQUEST", request.state.trace_id)

    @app.exception_handler(StarletteHTTPException)
    async def http_error(request: Request, error: StarletteHTTPException) -> JSONResponse:
        codes = {
            400: "INVALID_REQUEST",
            401: "AUTHENTICATION_REQUIRED",
            403: "FORBIDDEN",
            404: "RESOURCE_UNAVAILABLE",
            405: "METHOD_NOT_ALLOWED",
        }
        return _error_response(error.status_code, codes.get(error.status_code, "INVALID_REQUEST"), request.state.trace_id)

    @app.exception_handler(IdentityError)
    @app.exception_handler(AuthorizationError)
    async def domain_error(request: Request, error: IdentityError | AuthorizationError) -> JSONResponse:
        status, code, retryable = _domain_error(error)
        return _error_response(status, code, request.state.trace_id, retryable=retryable)

    @app.exception_handler(NotificationError)
    async def notification_error(request: Request, error: NotificationError) -> JSONResponse:
        status, code, retryable = _notification_domain_error(error)
        return _error_response(status, code, request.state.trace_id, retryable=retryable)

    @app.exception_handler(AuditValidationError)
    async def audit_error(request: Request, _error: AuditValidationError) -> JSONResponse:
        return _error_response(400, "INVALID_REQUEST", request.state.trace_id)

    @app.exception_handler(SyncError)
    async def sync_error(request: Request, error: SyncError) -> JSONResponse:
        return _error_response(
            error.status, error.code, request.state.trace_id, retryable=error.retryable
        )

    @app.exception_handler(RetentionError)
    async def retention_error(request: Request, error: RetentionError) -> JSONResponse:
        return _error_response(
            error.status, _retention_public_error_code(error.code),
            request.state.trace_id, retryable=error.retryable,
        )

    @app.exception_handler(RecoveryError)
    async def recovery_error(request: Request, error: RecoveryError) -> JSONResponse:
        return _error_response(
            error.status, _recovery_public_error_code(error.code),
            request.state.trace_id, retryable=error.retryable,
        )

    @app.exception_handler(Exception)
    async def unexpected_error(request: Request, _error: Exception) -> JSONResponse:
        return _error_response(500, "INTERNAL_ERROR", request.state.trace_id)

    @app.get("/health/live")
    async def live() -> dict[str, str]:
        return {"status": "live"}

    @app.get("/health/ready")
    async def ready(request: Request) -> JSONResponse:
        cloud_ready = (
            dependencies.cloud_store is None
            or (await asyncio.to_thread(dependencies.cloud_store.readiness)).ready
        )
        object_ready = (
            dependencies.object_storage is None
            or await asyncio.to_thread(dependencies.object_storage.health)
        )
        status = 200 if dependencies.state.ready and cloud_ready and object_ready else 503
        response = JSONResponse(
            status_code=status,
            content={"status": "ready" if status == 200 else "not_ready"},
        )
        response.headers["X-Trace-Id"] = request.state.trace_id
        return response

    @app.get("/api/v1/session")
    async def session(request: Request) -> dict[str, object]:
        token, expected_kind = _credential(request)
        view = dependencies.identity_service.describe_access(
            token, trace_id=request.state.trace_id,
            policy_version=dependencies.settings.policy_version,
        )
        if view.client_kind is not expected_kind:
            raise IdentityError("ACCESS_INVALID", 401)
        principal = view.principal
        return {
            "data": {
                "user_id": principal.user_id,
                "tenant_id": principal.tenant_id,
                "session_id": principal.session_id,
                "device_id": principal.device_id,
                "client_kind": view.client_kind.value,
                "delivery": "same_origin_secure_cookie" if view.client_kind is ClientKind.WEB else "native_https_opaque_bearer",
                "expires_at": view.expires_at.isoformat(),
            },
            "meta": {"trace_id": request.state.trace_id},
        }

    @app.post("/api/v1/workspaces/{workspace_id}/authorization/evaluations")
    async def authorization_evaluation(
        workspace_id: str,
        body: AuthorizationEvaluationBody,
        request: Request,
        idempotency_key: str = Header(alias="Idempotency-Key"),
    ) -> dict[str, object]:
        _idempotency_key(idempotency_key)
        principal = _principal(request, dependencies)
        grant = dependencies.authorization_service.authorize_action(
            principal=principal,
            workspace_id=workspace_id,
            action=body.action,
            requested_permissions=tuple(body.requested_permissions),
            trace_id=request.state.trace_id,
            policy_version=dependencies.settings.policy_version,
        )
        return {"data": _grant_json(grant), "trace_id": request.state.trace_id}

    @app.post("/api/v1/access-decisions", status_code=201)
    async def access_decision(
        body: AccessDecisionBody,
        request: Request,
        idempotency_key: str = Header(alias="Idempotency-Key"),
    ) -> dict[str, object]:
        _idempotency_key(idempotency_key)
        principal = _principal(request, dependencies)
        decision = dependencies.authorization_service.evaluate_historical_access(
            principal=principal,
            result_id=body.resource_id,
            action=body.action,
            trace_id=request.state.trace_id,
            policy_version=dependencies.settings.policy_version,
        )
        return {"data": _decision_json(decision), "trace_id": request.state.trace_id}

    @app.get("/api/v1/audit-events")
    async def audit_events(
        request: Request,
        tenant_id: str,
        workspace_id: str | None = None,
        action: str | None = None,
        outcome: AuditOutcome | None = None,
        trace_id: str | None = None,
        occurred_after: datetime | None = None,
        occurred_before: datetime | None = None,
        cursor: str | None = None,
        limit: int = Query(default=50, ge=1, le=200),
        filter: str | None = None,
        search: str | None = None,
    ) -> dict[str, object]:
        if filter is not None or search is not None:
            raise HTTPException(status_code=400)
        principal = _principal(request, dependencies)
        if tenant_id != principal.tenant_id:
            raise AuthorizationError("RESOURCE_UNAVAILABLE", 404)
        dependencies.authorization_service.authorize_audit_read(
            principal=principal, workspace_id=workspace_id,
            trace_id=request.state.trace_id,
            policy_version=dependencies.settings.policy_version,
        )
        page = dependencies.audit_store.list(
            tenant_id=principal.tenant_id,
            workspace_id=workspace_id,
            action=action,
            outcome=outcome,
            trace_id=trace_id,
            occurred_after=occurred_after,
            occurred_before=occurred_before,
            cursor=cursor,
            limit=limit,
        )
        return {
            "data": {"items": [_audit_json(item) for item in page.items], "next_cursor": page.next_cursor},
            "trace_id": request.state.trace_id,
        }

    @app.get("/api/v1/notifications")
    async def notifications(
        request: Request,
        cursor: str | None = Query(default=None, min_length=1, max_length=512),
        limit: int = Query(default=50, ge=1, le=200),
        filter: str | None = Query(default=None, min_length=1, max_length=128),
        search: str | None = Query(default=None, min_length=1, max_length=128),
    ) -> JSONResponse:
        _require_query_keys(request, frozenset({"cursor", "limit", "filter", "search"}))
        principal = _principal(request, dependencies)
        page = notification_service.list_notifications(
            principal=principal, limit=limit, cursor=cursor,
            filters=parse_notification_filter(filter), search=search,
            trace_id=request.state.trace_id,
            policy_version=dependencies.settings.policy_version,
        )
        content = {
            "data": {
                "items": [notification_json(item) for item in page.items],
                "next_cursor": page.next_cursor,
                "unread_count": page.unread_count,
            },
            "meta": {"trace_id": request.state.trace_id},
        }
        seed = "|".join(f"{item.notification_id}:{item.version}" for item in page.items) + f"|{page.unread_count}|{page.next_cursor}"
        return _json_with_etag(content, seed)

    @app.get("/api/v1/notifications/{notification_id}")
    async def notification_detail(notification_id: str, request: Request) -> JSONResponse:
        _require_query_keys(request, frozenset())
        principal = _principal(request, dependencies)
        item = notification_service.get_notification(
            principal=principal, notification_id=notification_id,
            trace_id=request.state.trace_id,
            policy_version=dependencies.settings.policy_version,
        )
        response = JSONResponse(content={
            "data": notification_json(item), "meta": {"trace_id": request.state.trace_id}
        })
        response.headers["ETag"] = item.etag
        return response

    @app.patch("/api/v1/notifications/{notification_id}")
    async def notification_read(
        notification_id: str,
        body: NotificationReadBody,
        request: Request,
        if_match: str = Header(alias="If-Match"),
        idempotency_key: str = Header(alias="Idempotency-Key"),
    ) -> JSONResponse:
        _require_query_keys(request, frozenset())
        _idempotency_key(idempotency_key)
        principal = _principal(request, dependencies)
        item = notification_service.mark_read(
            principal=principal, notification_id=notification_id,
            expected_etag=if_match, idempotency_key=idempotency_key,
            requested_state=body.state, trace_id=request.state.trace_id,
            policy_version=dependencies.settings.policy_version,
        )
        response = JSONResponse(content={
            "data": notification_json(item), "meta": {"trace_id": request.state.trace_id}
        })
        response.headers["ETag"] = item.etag
        return response

    @app.get("/api/v1/inbox")
    async def inbox(
        request: Request,
        cursor: str | None = Query(default=None, min_length=1, max_length=512),
        limit: int = Query(default=50, ge=1, le=200),
        filter: str | None = Query(default=None, min_length=1, max_length=128),
        search: str | None = Query(default=None, min_length=1, max_length=128),
    ) -> JSONResponse:
        _require_query_keys(request, frozenset({"cursor", "limit", "filter", "search"}))
        principal = _principal(request, dependencies)
        page = notification_service.list_inbox(
            principal=principal, limit=limit, cursor=cursor,
            filters=parse_inbox_filter(filter), search=search,
            trace_id=request.state.trace_id,
            policy_version=dependencies.settings.policy_version,
        )
        content = {
            "data": {"items": [inbox_json(item) for item in page.items], "next_cursor": page.next_cursor},
            "meta": {"trace_id": request.state.trace_id},
        }
        seed = "|".join(f"{item.request_id}:{item.status}" for item in page.items) + f"|{page.next_cursor}"
        return _json_with_etag(content, seed)

    @app.post("/api/v1/workspaces/{id}/sync-operations", status_code=201)
    async def create_sync_operation(
        id: str,
        body: SyncCreateBody,
        request: Request,
        if_match: str = Header(alias="If-Match"),
        idempotency_key: str = Header(alias="Idempotency-Key"),
    ) -> JSONResponse:
        _require_query_keys(request, frozenset())
        _idempotency_key(idempotency_key)
        principal = _principal(request, dependencies)
        dependencies.authorization_service.authorize_action(
            principal=principal, workspace_id=id, action=Action.EDIT,
            requested_permissions=(Permission.DATA_AREA_MOVE,),
            trace_id=request.state.trace_id,
            policy_version=dependencies.settings.policy_version,
        )
        view = sync_service.create_operation(
            _sync_context(principal, id, request, dependencies),
            target_area=body.target_area,
            items=tuple(SyncItemInput(**item.model_dump()) for item in body.items),
            idempotency_key=idempotency_key,
            if_match=cast(str, _sync_expected_version(if_match)),
        )
        response = JSONResponse(content={
            "data": _sync_view_json(view), "meta": {"trace_id": request.state.trace_id}
        }, status_code=201)
        response.headers["ETag"] = view.etag
        return response

    @app.get("/api/v1/sync-operations/{id}")
    async def get_sync_operation(id: str, request: Request) -> JSONResponse:
        _require_query_keys(request, frozenset())
        principal = _principal(request, dependencies)
        workspace_id = sync_service.locate_workspace(principal.tenant_id, id)
        dependencies.authorization_service.authorize_action(
            principal=principal, workspace_id=workspace_id, action=Action.VIEW,
            trace_id=request.state.trace_id,
            policy_version=dependencies.settings.policy_version,
        )
        view = sync_service.get_operation(
            _sync_context(principal, workspace_id, request, dependencies), id
        )
        response = JSONResponse(content={
            "data": _sync_view_json(view), "meta": {"trace_id": request.state.trace_id}
        })
        response.headers["ETag"] = view.etag
        return response

    @app.post("/api/v1/sync-operations/{id}/approve")
    async def approve_sync_operation(
        id: str,
        body: SyncApproveBody,
        request: Request,
        if_match: str = Header(alias="If-Match"),
        idempotency_key: str = Header(alias="Idempotency-Key"),
    ) -> JSONResponse:
        _require_query_keys(request, frozenset())
        _idempotency_key(idempotency_key)
        access_token, _ = _credential(request)
        principal = _principal(request, dependencies)
        workspace_id = sync_service.locate_workspace(principal.tenant_id, id)
        dependencies.authorization_service.authorize_action(
            principal=principal, workspace_id=workspace_id, action=Action.APPROVE,
            requested_permissions=(Permission.DATA_AREA_MOVE,),
            trace_id=request.state.trace_id,
            policy_version=dependencies.settings.policy_version,
        )
        dependencies.identity_service.consume_step_up(
            step_up_authorization=body.step_up_authorization_id,
            access_token=access_token,
            action_group="data_area_move", target_id=id,
            policy_version=dependencies.settings.policy_version,
            trace_id=request.state.trace_id,
        )
        view = sync_service.approve(
            _sync_context(principal, workspace_id, request, dependencies),
            operation_id=id, approved_item_ids=tuple(body.approved_item_ids),
            step_up_authorization_id=body.step_up_authorization_id,
            expected_version=cast(int, _sync_expected_version(if_match, id)),
            idempotency_key=idempotency_key, approval_verified=True,
        )
        response = JSONResponse(content={
            "data": _sync_view_json(view), "meta": {"trace_id": request.state.trace_id}
        })
        response.headers["ETag"] = view.etag
        return response

    @app.post("/api/v1/sync-operations/{id}/transfer-batches")
    async def transfer_sync_batch(
        id: str,
        body: SyncTransferBody,
        request: Request,
        if_match: str = Header(alias="If-Match"),
        idempotency_key: str = Header(alias="Idempotency-Key"),
    ) -> JSONResponse:
        _require_query_keys(request, frozenset())
        _idempotency_key(idempotency_key)
        principal = _principal(request, dependencies)
        workspace_id = sync_service.locate_workspace(principal.tenant_id, id)
        dependencies.authorization_service.authorize_action(
            principal=principal, workspace_id=workspace_id, action=Action.EDIT,
            requested_permissions=(Permission.DATA_AREA_MOVE,),
            trace_id=request.state.trace_id,
            policy_version=dependencies.settings.policy_version,
        )
        batch = sync_service.transfer_batch(
            _sync_context(principal, workspace_id, request, dependencies),
            operation_id=id,
            expected_version=cast(int, _sync_expected_version(if_match, id)),
            idempotency_key=idempotency_key, cursor=body.cursor,
            payloads=tuple(TransferPayload(
                item.item_id, cast(bytes, _sync_content(item.content_base64, required=True)),
                item.current_cloud_version_id, item.current_cloud_digest,
            ) for item in body.items),
        )
        view = sync_service.get_operation(
            _sync_context(principal, workspace_id, request, dependencies), id
        )
        response = JSONResponse(content={
            "data": _dataclass_json(batch),
            "operation": _sync_view_json(view),
            "meta": {"trace_id": request.state.trace_id},
        })
        response.headers["ETag"] = view.etag
        return response

    @app.post("/api/v1/sync-operations/{id}/conflicts/{conflict_id}/resolution")
    async def resolve_sync_conflict(
        id: str,
        conflict_id: str,
        body: SyncResolutionBody,
        request: Request,
        if_match: str = Header(alias="If-Match"),
        idempotency_key: str = Header(alias="Idempotency-Key"),
    ) -> JSONResponse:
        _require_query_keys(request, frozenset())
        _idempotency_key(idempotency_key)
        principal = _principal(request, dependencies)
        workspace_id = sync_service.locate_workspace(principal.tenant_id, id)
        dependencies.authorization_service.authorize_action(
            principal=principal, workspace_id=workspace_id, action=Action.EDIT,
            requested_permissions=(Permission.DATA_AREA_MOVE,),
            trace_id=request.state.trace_id,
            policy_version=dependencies.settings.policy_version,
        )
        resolution = sync_service.resolve_conflict(
            _sync_context(principal, workspace_id, request, dependencies),
            operation_id=id, conflict_id=conflict_id,
            expected_version=cast(int, _sync_expected_version(if_match, id)),
            idempotency_key=idempotency_key, choice=body.choice,
            content=_sync_content(
                body.content_base64,
                required=body.choice is not ConflictResolutionChoice.KEEP_CLOUD,
            ),
        )
        view = sync_service.get_operation(
            _sync_context(principal, workspace_id, request, dependencies), id
        )
        response = JSONResponse(content={
            "data": _dataclass_json(resolution),
            "operation": _sync_view_json(view),
            "meta": {"trace_id": request.state.trace_id},
        })
        response.headers["ETag"] = view.etag
        return response

    @app.post("/api/v1/sources/{id}/deletion-requests", status_code=201)
    async def create_deletion_request(
        id: str, body: DeletionCreateBody, request: Request,
        if_match: str = Header(alias="If-Match"),
        idempotency_key: str = Header(alias="Idempotency-Key"),
    ) -> JSONResponse:
        _require_query_keys(request, frozenset())
        _idempotency_key(idempotency_key)
        principal = _principal(request, dependencies)
        workspace_id = retention_service.locate_source_workspace(principal.tenant_id, id)
        dependencies.authorization_service.authorize_action(
            principal=principal, workspace_id=workspace_id, action=Action.EDIT,
            trace_id=request.state.trace_id,
            policy_version=dependencies.settings.policy_version,
        )
        view = retention_service.create_request(
            _retention_context(principal, workspace_id, request, dependencies),
            source_id=id,
            inventory=tuple(DerivativeInput(**item.model_dump()) for item in body.inventory),
            idempotency_key=idempotency_key, if_match=if_match,
        )
        response = JSONResponse(
            {"data": _dataclass_json(view), "meta": {"trace_id": request.state.trace_id}},
            status_code=201,
        )
        response.headers["ETag"] = view.etag
        return response

    @app.get("/api/v1/deletion-requests/{id}")
    async def get_deletion_request(id: str, request: Request) -> JSONResponse:
        _require_query_keys(request, frozenset())
        principal = _principal(request, dependencies)
        workspace_id = retention_service.locate_workspace(principal.tenant_id, id)
        dependencies.authorization_service.authorize_action(
            principal=principal, workspace_id=workspace_id, action=Action.VIEW,
            trace_id=request.state.trace_id,
            policy_version=dependencies.settings.policy_version,
        )
        view = retention_service.get_request(
            _retention_context(principal, workspace_id, request, dependencies), id
        )
        response = JSONResponse({
            "data": _dataclass_json(view), "meta": {"trace_id": request.state.trace_id}
        })
        response.headers["ETag"] = view.etag
        return response

    @app.post("/api/v1/deletion-requests/{id}/cancel")
    async def cancel_deletion_request(
        id: str, request: Request,
        if_match: str = Header(alias="If-Match"),
        idempotency_key: str = Header(alias="Idempotency-Key"),
    ) -> JSONResponse:
        _require_query_keys(request, frozenset())
        _idempotency_key(idempotency_key)
        principal = _principal(request, dependencies)
        workspace_id = retention_service.locate_workspace(principal.tenant_id, id)
        dependencies.authorization_service.authorize_action(
            principal=principal, workspace_id=workspace_id, action=Action.EDIT,
            trace_id=request.state.trace_id,
            policy_version=dependencies.settings.policy_version,
        )
        view = retention_service.cancel(
            _retention_context(principal, workspace_id, request, dependencies), id,
            expected_version=_retention_expected_version(if_match, id, "deletion"),
            idempotency_key=idempotency_key,
        )
        response = JSONResponse({
            "data": _dataclass_json(view), "meta": {"trace_id": request.state.trace_id}
        })
        response.headers["ETag"] = view.etag
        return response

    @app.post("/api/v1/deletion-requests/{id}/purge")
    async def purge_deletion_request(
        id: str, body: SensitiveRetentionBody, request: Request,
        if_match: str = Header(alias="If-Match"),
        idempotency_key: str = Header(alias="Idempotency-Key"),
    ) -> JSONResponse:
        _require_query_keys(request, frozenset())
        _idempotency_key(idempotency_key)
        access_token, _ = _credential(request)
        principal = _principal(request, dependencies)
        workspace_id = retention_service.locate_workspace(principal.tenant_id, id)
        dependencies.authorization_service.authorize_action(
            principal=principal, workspace_id=workspace_id, action=Action.POLICY_MANAGE,
            trace_id=request.state.trace_id,
            policy_version=dependencies.settings.policy_version,
        )
        dependencies.identity_service.consume_step_up(
            step_up_authorization=body.step_up_authorization_id,
            access_token=access_token,
            action_group="permanent_delete_or_restore_rollback", target_id=id,
            policy_version=dependencies.settings.policy_version,
            trace_id=request.state.trace_id,
        )
        view = retention_service.purge(
            _retention_context(
                principal, workspace_id, request, dependencies, organization_admin=True
            ), id,
            expected_version=_retention_expected_version(if_match, id, "deletion"),
            idempotency_key=idempotency_key, step_up_verified=True,
        )
        response = JSONResponse({
            "data": _dataclass_json(view), "meta": {"trace_id": request.state.trace_id}
        })
        response.headers["ETag"] = view.etag
        return response

    @app.post("/api/v1/sources/{id}/legal-holds", status_code=201)
    async def apply_legal_hold(
        id: str, body: SensitiveRetentionBody, request: Request,
        if_match: str = Header(alias="If-Match"),
        idempotency_key: str = Header(alias="Idempotency-Key"),
    ) -> JSONResponse:
        _require_query_keys(request, frozenset())
        _idempotency_key(idempotency_key)
        access_token, _ = _credential(request)
        principal = _principal(request, dependencies)
        workspace_id = retention_service.locate_source_workspace(principal.tenant_id, id)
        dependencies.authorization_service.authorize_action(
            principal=principal, workspace_id=workspace_id, action=Action.POLICY_MANAGE,
            trace_id=request.state.trace_id,
            policy_version=dependencies.settings.policy_version,
        )
        dependencies.identity_service.consume_step_up(
            step_up_authorization=body.step_up_authorization_id,
            access_token=access_token,
            action_group="permanent_delete_or_restore_rollback", target_id=id,
            policy_version=dependencies.settings.policy_version,
            trace_id=request.state.trace_id,
        )
        source_etag, source_version = retention_service.source_etag(principal.tenant_id, id)
        if if_match != source_etag:
            raise RetentionError("IF_MATCH_INVALID", 400)
        hold = retention_service.apply_legal_hold(
            _retention_context(
                principal, workspace_id, request, dependencies, organization_admin=True
            ), source_id=id, expected_version=source_version,
            idempotency_key=idempotency_key, step_up_verified=True,
        )
        response = JSONResponse(
            {"data": _dataclass_json(hold), "meta": {"trace_id": request.state.trace_id}},
            status_code=201,
        )
        response.headers["ETag"] = hold.etag
        return response

    @app.post("/api/v1/legal-holds/{id}/release")
    async def release_legal_hold(
        id: str, body: SensitiveRetentionBody, request: Request,
        if_match: str = Header(alias="If-Match"),
        idempotency_key: str = Header(alias="Idempotency-Key"),
    ) -> JSONResponse:
        _require_query_keys(request, frozenset())
        _idempotency_key(idempotency_key)
        access_token, _ = _credential(request)
        principal = _principal(request, dependencies)
        workspace_id = retention_service.locate_hold_workspace(principal.tenant_id, id)
        dependencies.authorization_service.authorize_action(
            principal=principal, workspace_id=workspace_id, action=Action.POLICY_MANAGE,
            trace_id=request.state.trace_id,
            policy_version=dependencies.settings.policy_version,
        )
        dependencies.identity_service.consume_step_up(
            step_up_authorization=body.step_up_authorization_id,
            access_token=access_token,
            action_group="permanent_delete_or_restore_rollback", target_id=id,
            policy_version=dependencies.settings.policy_version,
            trace_id=request.state.trace_id,
        )
        hold = retention_service.release_legal_hold(
            _retention_context(
                principal, workspace_id, request, dependencies, organization_admin=True
            ), id,
            expected_version=_retention_expected_version(if_match, id, "legal-hold"),
            idempotency_key=idempotency_key, step_up_verified=True,
        )
        response = JSONResponse({
            "data": _dataclass_json(hold), "meta": {"trace_id": request.state.trace_id}
        })
        response.headers["ETag"] = hold.etag
        return response

    @app.post("/api/v1/backups", status_code=201)
    async def create_backup(
        body: BackupCreateBody, request: Request,
        idempotency_key: str = Header(alias="Idempotency-Key"),
    ) -> JSONResponse:
        _require_query_keys(request, frozenset())
        _idempotency_key(idempotency_key)
        principal = _principal(request, dependencies)
        dependencies.authorization_service.authorize_action(
            principal=principal, workspace_id=body.workspace_id,
            action=Action.POLICY_MANAGE, trace_id=request.state.trace_id,
            policy_version=dependencies.settings.policy_version,
        )
        view = recovery_service.create_backup(
            _recovery_context(principal, body.workspace_id, request, dependencies),
            trigger=body.trigger, schema_revision=body.schema_revision,
            retention_watermark=body.retention_watermark,
            objects=tuple(BackupObjectInput(**item.model_dump()) for item in body.objects),
            idempotency_key=idempotency_key,
        )
        response = JSONResponse(
            {"data": _dataclass_json(view), "meta": {"trace_id": request.state.trace_id}},
            status_code=201,
        )
        response.headers["ETag"] = view.etag
        return response

    @app.get("/api/v1/backups")
    async def list_backups(request: Request, workspace_id: str = Query()) -> JSONResponse:
        _require_query_keys(request, frozenset({"workspace_id"}))
        principal = _principal(request, dependencies)
        dependencies.authorization_service.authorize_action(
            principal=principal, workspace_id=workspace_id, action=Action.POLICY_MANAGE,
            trace_id=request.state.trace_id,
            policy_version=dependencies.settings.policy_version,
        )
        views = recovery_service.list_backups(
            _recovery_context(principal, workspace_id, request, dependencies)
        )
        return _json_with_etag(
            {"data": [_dataclass_json(view) for view in views],
             "meta": {"trace_id": request.state.trace_id}},
            "|".join(view.etag for view in views),
        )

    @app.get("/api/v1/backups/{id}")
    async def get_backup(id: str, request: Request) -> JSONResponse:
        _require_query_keys(request, frozenset())
        principal = _principal(request, dependencies)
        workspace_id = recovery_service.locate_backup_workspace(principal.tenant_id, id)
        dependencies.authorization_service.authorize_action(
            principal=principal, workspace_id=workspace_id, action=Action.POLICY_MANAGE,
            trace_id=request.state.trace_id,
            policy_version=dependencies.settings.policy_version,
        )
        view = recovery_service.get_backup(
            _recovery_context(principal, workspace_id, request, dependencies), id
        )
        response = JSONResponse({
            "data": _dataclass_json(view), "meta": {"trace_id": request.state.trace_id}
        })
        response.headers["ETag"] = view.etag
        return response

    @app.post("/api/v1/backups/{id}/restore-previews", status_code=201)
    async def create_restore_preview(
        id: str, body: RestorePreviewBody, request: Request,
        idempotency_key: str = Header(alias="Idempotency-Key"),
    ) -> JSONResponse:
        _require_query_keys(request, frozenset())
        _idempotency_key(idempotency_key)
        access_token, _ = _credential(request)
        principal = _principal(request, dependencies)
        workspace_id = recovery_service.locate_backup_workspace(principal.tenant_id, id)
        dependencies.authorization_service.authorize_action(
            principal=principal, workspace_id=workspace_id, action=Action.POLICY_MANAGE,
            trace_id=request.state.trace_id,
            policy_version=dependencies.settings.policy_version,
        )
        dependencies.identity_service.consume_step_up(
            step_up_authorization=body.step_up_authorization_id,
            access_token=access_token,
            action_group="permanent_delete_or_restore_rollback", target_id=id,
            policy_version=dependencies.settings.policy_version,
            trace_id=request.state.trace_id,
        )
        view = recovery_service.create_restore_preview(
            _recovery_context(principal, workspace_id, request, dependencies), id,
            destination=RestoreDestination(**body.destination.model_dump()),
            idempotency_key=idempotency_key, step_up_verified=True,
        )
        response = JSONResponse(
            {"data": _dataclass_json(view), "meta": {"trace_id": request.state.trace_id}},
            status_code=201,
        )
        response.headers["ETag"] = view.etag
        return response

    @app.get("/api/v1/restore-requests/{id}")
    async def get_restore_request(id: str, request: Request) -> JSONResponse:
        _require_query_keys(request, frozenset())
        principal = _principal(request, dependencies)
        workspace_id = recovery_service.locate_restore_workspace(principal.tenant_id, id)
        dependencies.authorization_service.authorize_action(
            principal=principal, workspace_id=workspace_id, action=Action.POLICY_MANAGE,
            trace_id=request.state.trace_id,
            policy_version=dependencies.settings.policy_version,
        )
        view = recovery_service.get_restore_request(
            _recovery_context(principal, workspace_id, request, dependencies), id
        )
        response = JSONResponse({
            "data": _dataclass_json(view), "meta": {"trace_id": request.state.trace_id}
        })
        response.headers["ETag"] = view.etag
        return response

    @app.post("/api/v1/restore-requests/{id}/execute")
    async def execute_restore(
        id: str, body: RestoreExecuteBody, request: Request,
        if_match: str = Header(alias="If-Match"),
        idempotency_key: str = Header(alias="Idempotency-Key"),
    ) -> JSONResponse:
        _require_query_keys(request, frozenset())
        _idempotency_key(idempotency_key)
        access_token, _ = _credential(request)
        principal = _principal(request, dependencies)
        workspace_id = recovery_service.locate_restore_workspace(principal.tenant_id, id)
        dependencies.authorization_service.authorize_action(
            principal=principal, workspace_id=workspace_id, action=Action.POLICY_MANAGE,
            trace_id=request.state.trace_id,
            policy_version=dependencies.settings.policy_version,
        )
        dependencies.identity_service.consume_step_up(
            step_up_authorization=body.step_up_authorization_id,
            access_token=access_token,
            action_group="permanent_delete_or_restore_rollback", target_id=id,
            policy_version=dependencies.settings.policy_version,
            trace_id=request.state.trace_id,
        )
        view = recovery_service.execute_restore(
            _recovery_context(principal, workspace_id, request, dependencies), id,
            expected_version=_recovery_expected_version(if_match, id),
            preview_version=body.preview_version, idempotency_key=idempotency_key,
            step_up_verified=True,
        )
        response = JSONResponse({
            "data": _dataclass_json(view), "meta": {"trace_id": request.state.trace_id}
        })
        response.headers["ETag"] = view.etag
        return response

    @app.post("/api/v1/restore-requests/{id}/cancel")
    async def cancel_restore(
        id: str, request: Request,
        if_match: str = Header(alias="If-Match"),
        idempotency_key: str = Header(alias="Idempotency-Key"),
    ) -> JSONResponse:
        _require_query_keys(request, frozenset())
        _idempotency_key(idempotency_key)
        principal = _principal(request, dependencies)
        workspace_id = recovery_service.locate_restore_workspace(principal.tenant_id, id)
        dependencies.authorization_service.authorize_action(
            principal=principal, workspace_id=workspace_id, action=Action.POLICY_MANAGE,
            trace_id=request.state.trace_id,
            policy_version=dependencies.settings.policy_version,
        )
        view = recovery_service.cancel_restore(
            _recovery_context(principal, workspace_id, request, dependencies), id,
            expected_version=_recovery_expected_version(if_match, id),
            idempotency_key=idempotency_key,
        )
        response = JSONResponse({
            "data": _dataclass_json(view), "meta": {"trace_id": request.state.trace_id}
        })
        response.headers["ETag"] = view.etag
        return response

    return app


def build_dependencies(settings: RuntimeSettings) -> RuntimeDependencies:
    if settings.database_path is None:
        raise ValueError("DAON_API_DATABASE_PATH_REQUIRED")
    settings.database_path.parent.mkdir(parents=True, exist_ok=True)
    audit_store = AuditEventStore()
    identity_repository = SqliteIdentityRepository(settings.database_path)
    authorization_repository = SqliteAuthorizationRepository(settings.database_path)
    identity_service = IdentityService(
        repository=identity_repository,
        audit_store=audit_store,
        oidc_policies=(),
        clock=lambda: datetime.now(timezone.utc),
    )
    authorization_service = AuthorizationService(
        repository=authorization_repository,
        audit_store=audit_store,
        clock=lambda: datetime.now(timezone.utc),
        identity_service=identity_service,
    )
    notification_service = NotificationService(
        repository=ReferenceNotificationRepository(),
        authorization_service=authorization_service,
        audit_store=audit_store,
        clock=lambda: datetime.now(timezone.utc),
    )
    cloud_store = (
        None
        if settings.cloud_database_dsn is None
        else PostgresCloudStore(settings.cloud_database_dsn)
    )
    object_storage: ObjectStoragePort | None = None
    if settings.object_storage_endpoint is not None:
        assert settings.object_storage_bucket is not None
        assert settings.object_access_key_file is not None
        assert settings.object_secret_key_file is not None
        try:
            access_key = settings.object_access_key_file.read_text(encoding="utf-8").strip()
            secret_key = settings.object_secret_key_file.read_text(encoding="utf-8").strip()
        except OSError:
            raise ValueError("OBJECT_SECRET_REFERENCE_UNAVAILABLE") from None
        object_storage = MinioObjectStorageAdapter(
            endpoint=settings.object_storage_endpoint,
            bucket=settings.object_storage_bucket,
            access_key=access_key,
            secret_key=secret_key,
            secure=settings.object_storage_secure,
        )
    sync_service: SyncService | PostgresSyncService
    object_queue_store: PostgresObjectQueueStore | None = None
    if cloud_store is None:
        sync_service = SyncService(
            ReferenceSyncRepository(), ReferenceTransferPort(),
            clock=lambda: datetime.now(timezone.utc),
        )
    else:
        transfer_port: ObjectQueueSyncTransferPort | UnavailableSyncTransferPort
        if object_storage is None:
            transfer_port = UnavailableSyncTransferPort()
        else:
            assert settings.cloud_database_dsn is not None
            object_queue_store = PostgresObjectQueueStore(settings.cloud_database_dsn)
            transfer_port = ObjectQueueSyncTransferPort(ObjectQueueCoordinator(
                object_queue_store, object_storage, id_factory=lambda: secrets.token_hex(16)
            ))
        sync_service = PostgresSyncService(
            cloud_store, transfer_port, clock=lambda: datetime.now(timezone.utc)
        )
    return RuntimeDependencies(
        settings=settings,
        identity_service=identity_service,
        authorization_service=authorization_service,
        audit_store=audit_store,
        identity_repository=identity_repository,
        authorization_repository=authorization_repository,
        notification_service=notification_service,
        cloud_store=cloud_store,
        object_storage=object_storage,
        sync_service=sync_service,
        retention_service=RetentionService(
            ReferenceRetentionRepository(), ReferenceCleanupPort(),
            clock=lambda: datetime.now(timezone.utc),
        ),
        object_queue_store=object_queue_store,
    )
