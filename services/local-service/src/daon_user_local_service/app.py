from __future__ import annotations

import secrets
import time
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import Final

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse
from starlette.responses import Response

from .protocol import PROTOCOL_VERSION
from .security import NonceReplayCache, TokenError, verify_request_token


STATUS_PATH: Final = "/v1/status"
CAPABILITIES_PATH: Final = "/v1/capabilities"
CAPABILITY_CATALOG_VERSION: Final = "1.0"
MAX_HEADER_BYTES: Final = 8192
MAX_REQUEST_BODY_BYTES: Final = 0
_BROWSER_HEADERS: Final = frozenset(
    {"origin", "sec-fetch-site", "sec-fetch-mode", "sec-fetch-dest", "sec-fetch-user"}
)
_PROXY_HEADERS: Final = frozenset(
    {"forwarded", "x-forwarded-for", "x-forwarded-host", "x-forwarded-proto", "x-real-ip"}
)


@dataclass(frozen=True, slots=True)
class CommandContract:
    capability: str
    command: str
    method: str
    path: str


COMMAND_REGISTRY: Final = {
    STATUS_PATH: CommandContract("runtime.read", "runtime.status.read", "GET", STATUS_PATH),
    CAPABILITIES_PATH: CommandContract(
        "runtime.read",
        "runtime.capabilities.read",
        "GET",
        CAPABILITIES_PATH,
    ),
}


def _safe_error(status_code: int, error_code: str) -> JSONResponse:
    return JSONResponse(
        status_code=status_code,
        content={"error_code": error_code, "trace_id": secrets.token_hex(8)},
    )


def _header_values(request: Request, name: str) -> list[str]:
    encoded = name.encode("ascii")
    return [
        value.decode("latin-1")
        for header_name, value in request.scope.get("headers", [])
        if header_name == encoded
    ]


def _header_bytes(request: Request) -> int:
    return sum(len(name) + len(value) + 4 for name, value in request.scope.get("headers", []))


def _request_target_is_safe(request: Request) -> bool:
    raw_path = request.scope.get("raw_path", b"")
    query = request.scope.get("query_string", b"")
    return (
        isinstance(raw_path, bytes)
        and raw_path.startswith(b"/")
        and not raw_path.startswith(b"//")
        and b"%" not in raw_path
        and b"\\" not in raw_path
        and not query
    )


def create_app(
    *,
    root_secret: str,
    app_instance_id: str,
    listener_port: int,
    clock: Callable[[], int] = lambda: int(time.time()),
) -> FastAPI:
    if not 1 <= listener_port <= 65535:
        raise ValueError("invalid listener port")
    replay_cache = NonceReplayCache()
    app = FastAPI(
        title="Daon User Local Service",
        version=PROTOCOL_VERSION,
        docs_url=None,
        redoc_url=None,
        openapi_url=None,
    )

    @app.middleware("http")
    async def local_boundary(
        request: Request,
        call_next: Callable[[Request], Awaitable[Response]],
    ) -> Response:
        if _header_bytes(request) > MAX_HEADER_BYTES:
            return _safe_error(431, "REQUEST_HEADERS_TOO_LARGE")
        if not _request_target_is_safe(request):
            return _safe_error(400, "INVALID_REQUEST_TARGET")

        host_values = _header_values(request, "host")
        if host_values != [f"127.0.0.1:{listener_port}"]:
            return _safe_error(400, "INVALID_LOOPBACK_HOST")
        header_names = {name.decode("latin-1") for name, _value in request.scope["headers"]}
        if header_names & _PROXY_HEADERS:
            return _safe_error(400, "PROXY_HEADERS_NOT_ALLOWED")
        if header_names & _BROWSER_HEADERS:
            return _safe_error(403, "BROWSER_REQUEST_NOT_ALLOWED")

        authorization_values = _header_values(request, "authorization")
        authorization = authorization_values[0] if len(authorization_values) == 1 else ""
        supplied_token = (
            authorization.removeprefix("Bearer ")
            if authorization.startswith("Bearer ")
            else ""
        )
        now = clock()
        try:
            claims = verify_request_token(
                token=supplied_token,
                root_secret=root_secret,
                expected_instance_id=app_instance_id,
                expected_capability=None,
                expected_command=None,
                now=now,
                replay_cache=replay_cache,
                consume=False,
            )
        except TokenError:
            return _safe_error(401, "LOCAL_AUTH_REQUIRED")

        contract = COMMAND_REGISTRY.get(request.url.path)
        if contract is None:
            return _safe_error(404, "COMMAND_NOT_ALLOWED")
        if not (
            secrets.compare_digest(claims.capability, contract.capability)
            and secrets.compare_digest(claims.command, contract.command)
        ):
            return _safe_error(401, "LOCAL_AUTH_REQUIRED")

        if request.method != contract.method:
            return _safe_error(405, "METHOD_NOT_ALLOWED")
        transfer_encoding = _header_values(request, "transfer-encoding")
        if transfer_encoding:
            return _safe_error(400, "TRANSFER_ENCODING_NOT_ALLOWED")
        content_lengths = _header_values(request, "content-length")
        if len(content_lengths) > 1:
            return _safe_error(400, "INVALID_CONTENT_LENGTH")
        if content_lengths:
            try:
                declared_length = int(content_lengths[0])
            except ValueError:
                return _safe_error(400, "INVALID_CONTENT_LENGTH")
            if declared_length < 0:
                return _safe_error(400, "INVALID_CONTENT_LENGTH")
            if declared_length > MAX_REQUEST_BODY_BYTES:
                return _safe_error(413, "REQUEST_BODY_NOT_ALLOWED")
        if not replay_cache.consume(claims.nonce, claims.expires_at, now):
            return _safe_error(401, "LOCAL_AUTH_REQUIRED")
        return await call_next(request)

    @app.get(STATUS_PATH)
    async def status() -> dict[str, str]:
        return {"status": "ready", "protocol_version": PROTOCOL_VERSION}

    @app.get(CAPABILITIES_PATH)
    async def capabilities() -> dict[str, object]:
        commands = sorted(contract.command for contract in COMMAND_REGISTRY.values())
        return {
            "catalog_version": CAPABILITY_CATALOG_VERSION,
            "capabilities": [{"capability": "runtime.read", "commands": commands}],
        }

    return app
