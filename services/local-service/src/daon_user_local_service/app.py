from __future__ import annotations

import hmac
from collections.abc import Awaitable, Callable
from typing import Final

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse
from starlette.responses import Response

from .protocol import PROTOCOL_VERSION

STATUS_PATH: Final = "/v1/status"
ALLOWED_METHODS: Final = frozenset({"GET"})
MAX_HEADER_BYTES: Final = 8192
MAX_REQUEST_BODY_BYTES: Final = 0


def _header_bytes(request: Request) -> int:
    return sum(len(name) + len(value) + 4 for name, value in request.scope["headers"])


def create_app(*, token: str, app_instance_id: str) -> FastAPI:
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
        host = request.headers.get("host", "")
        if host != "127.0.0.1" and not host.startswith("127.0.0.1:"):
            return JSONResponse(status_code=400, content={"error_code": "INVALID_LOOPBACK_HOST"})

        if _header_bytes(request) > MAX_HEADER_BYTES:
            return JSONResponse(
                status_code=431,
                content={"error_code": "REQUEST_HEADERS_TOO_LARGE"},
            )
        if "transfer-encoding" in request.headers:
            return JSONResponse(
                status_code=400,
                content={"error_code": "TRANSFER_ENCODING_NOT_ALLOWED"},
            )
        content_length = request.headers.get("content-length")
        if content_length is not None:
            try:
                declared_length = int(content_length)
            except ValueError:
                return JSONResponse(
                    status_code=400,
                    content={"error_code": "INVALID_CONTENT_LENGTH"},
                )
            if declared_length < 0:
                return JSONResponse(
                    status_code=400,
                    content={"error_code": "INVALID_CONTENT_LENGTH"},
                )
            if declared_length > MAX_REQUEST_BODY_BYTES:
                return JSONResponse(
                    status_code=413,
                    content={"error_code": "REQUEST_BODY_NOT_ALLOWED"},
                )

        authorization = request.headers.get("authorization", "")
        supplied_token = authorization.removeprefix("Bearer ") if authorization.startswith("Bearer ") else ""
        supplied_instance = request.headers.get("x-daon-app-instance", "")
        token_matches = hmac.compare_digest(supplied_token, token)
        instance_matches = hmac.compare_digest(supplied_instance, app_instance_id)
        if not (token_matches & instance_matches):
            return JSONResponse(status_code=401, content={"error_code": "LOCAL_AUTH_REQUIRED"})

        if request.url.path == STATUS_PATH and request.method not in ALLOWED_METHODS:
            return JSONResponse(status_code=405, content={"error_code": "METHOD_NOT_ALLOWED"})
        return await call_next(request)

    @app.get(STATUS_PATH)
    async def status() -> dict[str, str]:
        return {
            "status": "ready",
            "protocol_version": PROTOCOL_VERSION,
            "app_instance_id": app_instance_id,
        }

    return app
