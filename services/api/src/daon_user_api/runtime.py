"""Explicit FastAPI process composition for the approved M4 domain cores."""

from __future__ import annotations

import ipaddress
import asyncio
import os
import re
import secrets
import threading
import time
from contextlib import asynccontextmanager
from dataclasses import dataclass, field, fields
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
        if self.profile in {"test", "development"}:
            try:
                loopback = ipaddress.ip_address(self.bind_host).is_loopback
            except ValueError:
                loopback = self.bind_host == "localhost"
            if not loopback:
                raise ValueError("PLAINTEXT_BIND_MUST_BE_LOOPBACK")
        else:
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
    state: RuntimeState = field(default_factory=RuntimeState)
    _closed: bool = field(default=False, init=False, repr=False)

    def close(self) -> None:
        if self._closed:
            return
        self.state.begin_shutdown()
        self.state.drain(self.settings.drain_timeout_seconds)
        self.authorization_repository.close()
        self.identity_repository.close()
        self._closed = True


class AuthorizationEvaluationBody(BaseModel):
    model_config = ConfigDict(extra="forbid")
    action: Action
    requested_permissions: list[Permission]


class AccessDecisionBody(BaseModel):
    model_config = ConfigDict(extra="forbid")
    resource_id: str
    action: AccessAction


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


def _enum_json(value: object) -> object:
    if isinstance(value, Enum):
        return value.value
    if isinstance(value, datetime):
        return value.isoformat()
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

    @app.exception_handler(AuditValidationError)
    async def audit_error(request: Request, _error: AuditValidationError) -> JSONResponse:
        return _error_response(400, "INVALID_REQUEST", request.state.trace_id)

    @app.exception_handler(Exception)
    async def unexpected_error(request: Request, _error: Exception) -> JSONResponse:
        return _error_response(500, "INTERNAL_ERROR", request.state.trace_id)

    @app.get("/health/live")
    async def live() -> dict[str, str]:
        return {"status": "live"}

    @app.get("/health/ready")
    async def ready(request: Request) -> JSONResponse:
        status = 200 if dependencies.state.ready else 503
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
    return RuntimeDependencies(
        settings=settings,
        identity_service=identity_service,
        authorization_service=authorization_service,
        audit_store=audit_store,
        identity_repository=identity_repository,
        authorization_repository=authorization_repository,
    )
