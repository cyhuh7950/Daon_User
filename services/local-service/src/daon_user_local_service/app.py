from __future__ import annotations

import secrets
import time
from base64 import b64decode, b64encode
from binascii import Error as Base64Error
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import Final

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from pydantic import BaseModel, ConfigDict, Field
from starlette.responses import Response

from .protocol import PROTOCOL_VERSION
from .local_storage import LocalEncryptedStore, LocalStorageError
from .recovery import LocalRecoveryService
from .security import NonceReplayCache, TokenError, verify_request_token


STATUS_PATH: Final = "/v1/status"
CAPABILITIES_PATH: Final = "/v1/capabilities"
STORAGE_STATUS_PATH: Final = "/v1/storage/status"
FILE_PUT_PATH: Final = "/v1/storage/file/put"
FILE_GET_PATH: Final = "/v1/storage/file/get"
VECTOR_PUT_PATH: Final = "/v1/storage/vector/put"
VECTOR_SEARCH_PATH: Final = "/v1/storage/vector/search"
STORAGE_LOCK_PATH: Final = "/v1/storage/lock"
RECOVERY_SCAN_PATH: Final = "/local/v1/recovery/scans"
RECOVERY_JOB_PATH: Final = "/local/v1/recovery/jobs/{id}"
RECOVERY_REPAIR_PATH: Final = "/local/v1/recovery/jobs/{id}/repair"
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
    max_body_bytes: int = 0


COMMAND_REGISTRY: Final = {
    STATUS_PATH: CommandContract("runtime.read", "runtime.status.read", "GET", STATUS_PATH),
    CAPABILITIES_PATH: CommandContract(
        "runtime.read",
        "runtime.capabilities.read",
        "GET",
        CAPABILITIES_PATH,
    ),
    STORAGE_STATUS_PATH: CommandContract(
        "storage.read", "storage.status.read", "GET", STORAGE_STATUS_PATH
    ),
    FILE_PUT_PATH: CommandContract(
        "storage.write", "storage.file.put", "POST", FILE_PUT_PATH, 1_500_000
    ),
    FILE_GET_PATH: CommandContract(
        "storage.read", "storage.file.get", "POST", FILE_GET_PATH, 2048
    ),
    VECTOR_PUT_PATH: CommandContract(
        "storage.write", "storage.vector.put", "POST", VECTOR_PUT_PATH, 128_000
    ),
    VECTOR_SEARCH_PATH: CommandContract(
        "storage.read", "storage.vector.search", "POST", VECTOR_SEARCH_PATH, 64_000
    ),
    STORAGE_LOCK_PATH: CommandContract(
        "storage.write", "storage.lock", "POST", STORAGE_LOCK_PATH
    ),
    RECOVERY_SCAN_PATH: CommandContract(
        "recovery.write", "recovery.scan", "POST", RECOVERY_SCAN_PATH, 4096
    ),
    RECOVERY_JOB_PATH: CommandContract(
        "recovery.read", "recovery.job.read", "GET", RECOVERY_JOB_PATH
    ),
    RECOVERY_REPAIR_PATH: CommandContract(
        "recovery.write", "recovery.repair", "POST", RECOVERY_REPAIR_PATH, 1024
    ),
}


class StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class FilePutRequest(StrictModel):
    workspace_id: str = Field(max_length=64)
    area: str = Field(max_length=32)
    content_base64: str = Field(max_length=1_400_000)
    content_type: str = Field(max_length=128)


class FileGetRequest(StrictModel):
    workspace_id: str = Field(max_length=64)
    area: str = Field(max_length=32)
    object_id: str = Field(max_length=64)


class VectorPutRequest(StrictModel):
    workspace_id: str = Field(max_length=64)
    area: str = Field(max_length=32)
    item_id: str = Field(max_length=256)
    embedding: list[float] = Field(min_length=1, max_length=4096)
    model_digest: str = Field(max_length=64)
    artifact_digest: str = Field(max_length=64)
    embedding_version: str = Field(max_length=128)
    source_version: str = Field(max_length=128)
    object_version: str = Field(max_length=128)


class VectorSearchRequest(StrictModel):
    workspace_id: str = Field(max_length=64)
    area: str = Field(max_length=32)
    embedding: list[float] = Field(min_length=1, max_length=4096)
    limit: int = Field(ge=1, le=100)


class RecoveryScanRequest(StrictModel):
    workspace_id: str = Field(max_length=64)
    target_id: str = Field(max_length=256)
    snapshot_checksum: str = Field(min_length=64, max_length=64)
    metadata_checksum: str = Field(min_length=64, max_length=64)
    actual_checksum: str = Field(min_length=64, max_length=64)
    journal_present: bool


class RecoveryRepairRequest(StrictModel):
    workspace_id: str = Field(max_length=64)
    expected_version: int = Field(ge=1)


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
    storage: LocalEncryptedStore | None = None,
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

    @app.exception_handler(RequestValidationError)
    async def validation_error(_request: Request, _error: RequestValidationError) -> JSONResponse:
        return _safe_error(400, "LOCAL_INPUT_INVALID")

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
        if contract is None and request.url.path.startswith("/local/v1/recovery/jobs/"):
            contract = (
                COMMAND_REGISTRY[RECOVERY_REPAIR_PATH]
                if request.url.path.endswith("/repair")
                else COMMAND_REGISTRY[RECOVERY_JOB_PATH]
            )
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
            if declared_length > contract.max_body_bytes:
                return _safe_error(413, "REQUEST_BODY_NOT_ALLOWED")
        if not replay_cache.consume(claims.nonce, claims.expires_at, now):
            return _safe_error(401, "LOCAL_AUTH_REQUIRED")
        return await call_next(request)

    @app.get(STATUS_PATH)
    async def status() -> dict[str, str]:
        return {"status": "ready", "protocol_version": PROTOCOL_VERSION}

    @app.get(CAPABILITIES_PATH)
    async def capabilities() -> dict[str, object]:
        commands_by_capability: dict[str, list[str]] = {}
        for contract in COMMAND_REGISTRY.values():
            commands_by_capability.setdefault(contract.capability, []).append(contract.command)
        return {
            "catalog_version": CAPABILITY_CATALOG_VERSION,
            "capabilities": [
                {"capability": capability, "commands": sorted(commands)}
                for capability, commands in sorted(commands_by_capability.items())
            ],
        }

    def active_storage() -> LocalEncryptedStore:
        if storage is None or not storage.is_unlocked():
            raise LocalStorageError("LOCAL_KEY_UNAVAILABLE")
        return storage

    def storage_error(error: LocalStorageError) -> JSONResponse:
        return _safe_error(423, str(error))

    @app.get(STORAGE_STATUS_PATH)
    async def storage_status() -> dict[str, str]:
        return {"state": "unlocked" if storage is not None and storage.is_unlocked() else "locked"}

    @app.post(FILE_PUT_PATH, response_model=None)
    async def file_put(request: FilePutRequest) -> dict[str, str] | JSONResponse:
        try:
            plaintext = b64decode(request.content_base64, validate=True)
            return {
                "object_id": active_storage().put_file(
                    request.workspace_id,
                    request.area,
                    plaintext,
                    content_type=request.content_type,
                )
            }
        except (Base64Error, ValueError):
            return _safe_error(400, "LOCAL_INPUT_INVALID")
        except LocalStorageError as error:
            return storage_error(error)

    @app.post(FILE_GET_PATH, response_model=None)
    async def file_get(request: FileGetRequest) -> dict[str, str] | JSONResponse:
        try:
            plaintext = active_storage().get_file(
                request.workspace_id, request.area, request.object_id
            )
            return {"content_base64": b64encode(plaintext).decode("ascii")}
        except LocalStorageError as error:
            return storage_error(error)

    @app.post(VECTOR_PUT_PATH, response_model=None)
    async def vector_put(request: VectorPutRequest) -> dict[str, str] | JSONResponse:
        try:
            active_storage().put_vector(
                request.workspace_id,
                request.area,
                request.item_id,
                request.embedding,
                model_digest=request.model_digest,
                artifact_digest=request.artifact_digest,
                embedding_version=request.embedding_version,
                source_version=request.source_version,
                object_version=request.object_version,
            )
            return {"status": "stored"}
        except LocalStorageError as error:
            return storage_error(error)

    @app.post(VECTOR_SEARCH_PATH, response_model=None)
    async def vector_search(request: VectorSearchRequest) -> dict[str, list[str]] | JSONResponse:
        try:
            return {
                "item_ids": active_storage().search_vectors(
                    request.workspace_id, request.area, request.embedding, request.limit
                )
            }
        except LocalStorageError as error:
            return storage_error(error)

    @app.post(STORAGE_LOCK_PATH, response_model=None)
    async def storage_lock() -> dict[str, str] | JSONResponse:
        try:
            active_storage().lock()
            return {"state": "locked"}
        except LocalStorageError as error:
            return storage_error(error)

    @app.post(RECOVERY_SCAN_PATH, response_model=None)
    async def recovery_scan(request: RecoveryScanRequest) -> dict[str, object] | JSONResponse:
        try:
            service = LocalRecoveryService(active_storage())
            return {"data": service.view(service.scan(
                request.workspace_id, target_id=request.target_id,
                snapshot_checksum=request.snapshot_checksum,
                metadata_checksum=request.metadata_checksum,
                actual_checksum=request.actual_checksum,
                journal_present=request.journal_present,
            ))}
        except LocalStorageError as error:
            return storage_error(error)

    @app.get(RECOVERY_JOB_PATH, response_model=None)
    async def recovery_job(id: str) -> dict[str, object] | JSONResponse:
        try:
            service = LocalRecoveryService(active_storage())
            return {"data": service.view(service.find(id))}
        except LocalStorageError as error:
            return storage_error(error)

    @app.post(RECOVERY_REPAIR_PATH, response_model=None)
    async def recovery_repair(
        id: str, request: RecoveryRepairRequest
    ) -> dict[str, object] | JSONResponse:
        try:
            service = LocalRecoveryService(active_storage())
            return {"data": service.view(service.repair(
                request.workspace_id, id, expected_version=request.expected_version,
            ))}
        except LocalStorageError as error:
            return storage_error(error)

    return app
