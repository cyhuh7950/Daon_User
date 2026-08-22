from __future__ import annotations

import hashlib
import hmac
import secrets
import time
import re
from base64 import b64decode, b64encode
from binascii import Error as Base64Error
from collections.abc import Awaitable, Callable
from dataclasses import asdict, dataclass
from typing import Final, cast

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from pydantic import BaseModel, ConfigDict, Field
from starlette.responses import Response

from .protocol import PROTOCOL_VERSION
from .local_storage import LocalEncryptedStore, LocalStorageError
from .recovery import LocalRecoveryService
from .security import NonceReplayCache, TokenError, verify_request_token
from .knowledge_context import KnowledgeContextSnapshot, OfflineStudioError
from .offline_studio import (
    ConfirmSettingsInput, OfflineDraftView, OfflineStudioService, SectionInput,
)
from .raw_source import RawSourceError, RawSourceService


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
STUDIO_MODELS_PATH: Final = "/local/v1/studio/models"
STUDIO_PROVIDER_SETTINGS_PATH: Final = "/local/v1/studio/provider-settings"
STUDIO_RAW_SOURCES_PATH: Final = "/local/v1/studio/raw-sources"
STUDIO_CONTEXTS_PATH: Final = "/local/v1/studio/knowledge-contexts"
STUDIO_SETTINGS_PATH: Final = "/local/v1/studio/settings/confirm"
STUDIO_GENERATE_PATH: Final = "/local/v1/studio/drafts/generate"
STUDIO_DRAFT_PATH: Final = "/local/v1/studio/drafts/{id}"
STUDIO_VERSION_PATH: Final = "/local/v1/studio/drafts/{id}/versions"
STUDIO_QUEUE_PATH: Final = "/local/v1/studio/drafts/{id}/sync-queue"
STUDIO_KNOWLEDGE_COPY_PATH: Final = "/local/v1/studio/knowledge-copies"
STUDIO_KNOWLEDGE_REFRESH_PATH: Final = "/local/v1/studio/knowledge-copies/{id}/refresh"
STUDIO_SYNC_STATE_PATH: Final = "/local/v1/studio/sync-operations/{id}"
STUDIO_SYNC_STATE_APPEND_PATH: Final = "/local/v1/studio/sync-operations/{id}/states"
CAPABILITY_CATALOG_VERSION: Final = "1.0"
MAX_HEADER_BYTES: Final = 8192
MAX_REQUEST_BODY_BYTES: Final = 0
_BROWSER_HEADERS: Final = frozenset(
    {"origin", "sec-fetch-site", "sec-fetch-mode", "sec-fetch-dest", "sec-fetch-user"}
)
_PROXY_HEADERS: Final = frozenset(
    {"forwarded", "x-forwarded-for", "x-forwarded-host", "x-forwarded-proto", "x-real-ip"}
)
_SAFE_ID: Final = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:-]{0,255}$")


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
    STUDIO_MODELS_PATH: CommandContract(
        "studio.read", "studio_models_list", "GET", STUDIO_MODELS_PATH
    ),
    STUDIO_PROVIDER_SETTINGS_PATH: CommandContract(
        "studio.write", "studio_provider_settings_import", "POST",
        STUDIO_PROVIDER_SETTINGS_PATH, 256 * 1024,
    ),
    STUDIO_RAW_SOURCES_PATH + ":GET": CommandContract(
        "studio.read", "studio_raw_sources_list", "GET", STUDIO_RAW_SOURCES_PATH,
    ),
    STUDIO_RAW_SOURCES_PATH + ":POST": CommandContract(
        "studio.write", "studio_raw_source_import", "POST", STUDIO_RAW_SOURCES_PATH,
        36 * 1024 * 1024,
    ),
    STUDIO_CONTEXTS_PATH: CommandContract(
        "studio.write", "studio_context_prepare", "POST", STUDIO_CONTEXTS_PATH, 32_768
    ),
    STUDIO_SETTINGS_PATH: CommandContract(
        "studio.write", "studio_settings_confirm", "POST", STUDIO_SETTINGS_PATH, 16_384
    ),
    STUDIO_GENERATE_PATH: CommandContract(
        "studio.write", "studio_draft_generate", "POST", STUDIO_GENERATE_PATH, 4096
    ),
    STUDIO_DRAFT_PATH: CommandContract(
        "studio.read", "studio_draft_get", "GET", STUDIO_DRAFT_PATH
    ),
    STUDIO_VERSION_PATH: CommandContract(
        "studio.write", "studio_draft_append_version", "POST", STUDIO_VERSION_PATH, 1_100_000
    ),
    STUDIO_QUEUE_PATH: CommandContract(
        "studio.write", "studio_sync_queue", "POST", STUDIO_QUEUE_PATH, 32_768
    ),
    STUDIO_KNOWLEDGE_COPY_PATH: CommandContract(
        "knowledge.write", "studio_knowledge_copy_import", "POST",
        STUDIO_KNOWLEDGE_COPY_PATH, 16 * 1024 * 1024,
    ),
    STUDIO_KNOWLEDGE_REFRESH_PATH: CommandContract(
        "knowledge.write", "studio_knowledge_copy_refresh", "POST",
        STUDIO_KNOWLEDGE_REFRESH_PATH, 32 * 1024,
    ),
    STUDIO_SYNC_STATE_PATH: CommandContract(
        "sync.read", "studio_sync_state_read", "GET", STUDIO_SYNC_STATE_PATH,
    ),
    STUDIO_SYNC_STATE_APPEND_PATH: CommandContract(
        "sync.write", "studio_sync_state_append", "POST",
        STUDIO_SYNC_STATE_APPEND_PATH, 64 * 1024,
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


class StudioProviderProfileRequest(StrictModel):
    profile_id: str = Field(pattern=r"^[A-Za-z0-9][A-Za-z0-9._:-]{0,255}$")
    provider_code: str = Field(pattern=r"^OLLAMA$")
    provider_kind: str = Field(pattern=r"^server_internal$")
    base_url: str = Field(min_length=1, max_length=2048)
    active: bool
    version: int = Field(ge=1)


class StudioProviderDeploymentRequest(StrictModel):
    deployment_id: str = Field(pattern=r"^[A-Za-z0-9][A-Za-z0-9._:-]{0,255}$")
    profile_id: str = Field(pattern=r"^[A-Za-z0-9][A-Za-z0-9._:-]{0,255}$")
    provider_code: str = Field(pattern=r"^OLLAMA$")
    model_id: str = Field(min_length=1, max_length=256)
    roles: list[str] = Field(min_length=1, max_length=16)
    active: bool
    selected: bool
    version: int = Field(ge=1)


class StudioProviderSettingsRequest(StrictModel):
    workspace_id: str = Field(pattern=r"^[0-9a-f-]{36}$", max_length=36)
    profiles: list[StudioProviderProfileRequest] = Field(max_length=128)
    deployments: list[StudioProviderDeploymentRequest] = Field(max_length=512)
    policy_version: str = Field(pattern=r"^[A-Za-z0-9][A-Za-z0-9._:-]{0,255}$")


class StudioRawSourceImportRequest(StrictModel):
    workspace_id: str = Field(pattern=r"^[0-9a-f-]{36}$", max_length=36)
    filename: str = Field(min_length=1, max_length=255)
    content_type: str = Field(pattern=r"^(application/pdf|text/plain|text/markdown)$")
    content_base64: str = Field(min_length=1, max_length=35 * 1024 * 1024)
    content_digest_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    idempotency_key: str = Field(pattern=r"^[A-Za-z0-9][A-Za-z0-9._:-]{0,255}$")

class StudioContextRequest(StrictModel):
    workspace_id: str = Field(pattern=r"^[0-9a-f-]{36}$", max_length=36)
    mode: str = Field(pattern=r"^(daon_priority|mixed|raw_only)$")
    daon_knowledge_ids: list[str] = Field(max_length=500)
    raw_source_version_ids: list[str] = Field(max_length=500)
    idempotency_key: str = Field(pattern=r"^[A-Za-z0-9][A-Za-z0-9._:-]{0,255}$")


class StudioSettingsRequest(StrictModel):
    workspace_id: str = Field(pattern=r"^[0-9a-f-]{36}$", max_length=36)
    title: str = Field(min_length=1, max_length=200)
    purpose: str = Field(min_length=1, max_length=4000)
    temperature: float = Field(ge=0, le=2)
    max_output_tokens: int = Field(ge=1, le=32768)
    context_snapshot_id: str = Field(pattern=r"^[A-Za-z0-9][A-Za-z0-9._:-]{0,255}$")
    model_deployment_id: str = Field(pattern=r"^[A-Za-z0-9][A-Za-z0-9._:-]{0,255}$")
    idempotency_key: str = Field(pattern=r"^[A-Za-z0-9][A-Za-z0-9._:-]{0,255}$")
    selection_actor_id: str = Field(pattern=r"^[A-Za-z0-9][A-Za-z0-9._:-]{0,255}$")


class StudioGenerateRequest(StrictModel):
    workspace_id: str = Field(pattern=r"^[0-9a-f-]{36}$", max_length=36)
    request_id: str = Field(pattern=r"^[A-Za-z0-9][A-Za-z0-9._:-]{0,255}$")
    idempotency_key: str = Field(pattern=r"^[A-Za-z0-9][A-Za-z0-9._:-]{0,255}$")


class StudioSectionRequest(StrictModel):
    title: str = Field(min_length=1, max_length=200)
    body: str = Field(min_length=1, max_length=1_048_576)
    unverified: bool


class StudioAppendRequest(StrictModel):
    workspace_id: str = Field(pattern=r"^[0-9a-f-]{36}$", max_length=36)
    previous_version_id: str = Field(pattern=r"^[A-Za-z0-9][A-Za-z0-9._:-]{0,255}$")
    sections: list[StudioSectionRequest] = Field(min_length=1, max_length=50)
    idempotency_key: str = Field(pattern=r"^[A-Za-z0-9][A-Za-z0-9._:-]{0,255}$")


class StudioQueueRequest(StrictModel):
    workspace_id: str = Field(pattern=r"^[0-9a-f-]{36}$", max_length=36)
    output_version_id: str = Field(pattern=r"^[A-Za-z0-9][A-Za-z0-9._:-]{0,255}$")
    source_dependency_ids: list[str] = Field(max_length=500)
    idempotency_key: str = Field(pattern=r"^[A-Za-z0-9][A-Za-z0-9._:-]{0,255}$")


class KnowledgeCopyImportRequest(StrictModel):
    workspace_id: str = Field(pattern=r"^[0-9a-f-]{36}$", max_length=36)
    copy_id: str = Field(pattern=r"^[A-Za-z0-9][A-Za-z0-9._:-]{0,255}$")
    package_id: str = Field(pattern=r"^[A-Za-z0-9][A-Za-z0-9._:-]{0,255}$")
    producer_product: str = Field(min_length=1, max_length=64)
    producer_version: str = Field(min_length=1, max_length=128)
    knowledge_registration_id: str = Field(pattern=r"^[A-Za-z0-9][A-Za-z0-9._:-]{0,255}$")
    output_version_id: str = Field(pattern=r"^[A-Za-z0-9][A-Za-z0-9._:-]{0,255}$")
    authority: str = Field(min_length=1, max_length=64)
    registration_state: str = Field(pattern=r"^registered$")
    review_state: str = Field(pattern=r"^approved$")
    effective_at: str = Field(min_length=20, max_length=40)
    expires_at: str = Field(min_length=20, max_length=40)
    schema_version: int = Field(ge=1)
    content_digest_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    manifest_digest_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    canonical_package_base64: str = Field(max_length=16 * 1024 * 1024)
    idempotency_key: str = Field(pattern=r"^[A-Za-z0-9][A-Za-z0-9._:-]{0,255}$")


class KnowledgeCopyRefreshRequest(StrictModel):
    workspace_id: str = Field(pattern=r"^[0-9a-f-]{36}$", max_length=36)
    state: str = Field(pattern=r"^(approved|revoked|expired)$")
    recorded_at: str = Field(min_length=20, max_length=40)


class SyncStateAppendRequest(StrictModel):
    workspace_id: str = Field(pattern=r"^[0-9a-f-]{36}$", max_length=36)
    version: int = Field(ge=1)
    approval_state: str = Field(
        pattern=r"^(draft|awaiting_approval|approved|transferring|conflict|reindex_requested)$"
    )
    manifest_digest: str = Field(pattern=r"^[0-9a-f]{64}$")
    batch_cursor: str | None = Field(default=None, max_length=256)
    conflict_id: str | None = Field(default=None, max_length=256)
    queued_at: str = Field(min_length=20, max_length=40)
    previous_version: int | None = Field(default=None, ge=1)


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
    offline_studio: OfflineStudioService | None = None,
    raw_source_service: RawSourceService | None = None,
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
        if request.url.path == STUDIO_RAW_SOURCES_PATH:
            contract = COMMAND_REGISTRY.get(f"{STUDIO_RAW_SOURCES_PATH}:{request.method}")
        if contract is None and request.url.path.startswith("/local/v1/recovery/jobs/"):
            contract = (
                COMMAND_REGISTRY[RECOVERY_REPAIR_PATH]
                if request.url.path.endswith("/repair")
                else COMMAND_REGISTRY[RECOVERY_JOB_PATH]
            )
        if contract is None and request.url.path.startswith("/local/v1/studio/drafts/"):
            segments = request.url.path.split("/")
            if len(segments) in {6, 7} and _SAFE_ID.fullmatch(segments[5]):
                if len(segments) == 6:
                    contract = COMMAND_REGISTRY[STUDIO_DRAFT_PATH]
                elif segments[6] == "versions":
                    contract = COMMAND_REGISTRY[STUDIO_VERSION_PATH]
                elif segments[6] == "sync-queue":
                    contract = COMMAND_REGISTRY[STUDIO_QUEUE_PATH]
        if contract is None and request.url.path.startswith("/local/v1/studio/knowledge-copies/"):
            segments = request.url.path.split("/")
            if len(segments) == 7 and _SAFE_ID.fullmatch(segments[5]) and segments[6] == "refresh":
                contract = COMMAND_REGISTRY[STUDIO_KNOWLEDGE_REFRESH_PATH]
        if contract is None and request.url.path.startswith("/local/v1/studio/sync-operations/"):
            segments = request.url.path.split("/")
            if len(segments) in {6, 7} and _SAFE_ID.fullmatch(segments[5]):
                contract = (
                    COMMAND_REGISTRY[STUDIO_SYNC_STATE_APPEND_PATH]
                    if len(segments) == 7 and segments[6] == "states"
                    else COMMAND_REGISTRY[STUDIO_SYNC_STATE_PATH] if len(segments) == 6 else None
                )
        if contract is None:
            return _safe_error(404, "COMMAND_NOT_ALLOWED")
        if not (
            secrets.compare_digest(claims.capability, contract.capability)
            and secrets.compare_digest(claims.command, contract.command)
        ):
            return _safe_error(401, "LOCAL_AUTH_REQUIRED")

        if contract.capability in {"studio.read", "studio.write", "knowledge.write", "sync.read", "sync.write"}:
            workspace_values = _header_values(request, "x-daon-workspace-id")
            proof_values = _header_values(request, "x-daon-workspace-proof")
            if (
                len(workspace_values) != 1
                or len(proof_values) != 1
                or re.fullmatch(r"[0-9a-f-]{36}", workspace_values[0]) is None
                or re.fullmatch(r"[0-9a-f]{64}", proof_values[0]) is None
            ):
                return _safe_error(403, "LOCAL_WORKSPACE_REQUIRED")
            expected_proof = hmac.new(
                bytes.fromhex(root_secret),
                f"{supplied_token}|{workspace_values[0]}".encode("ascii"),
                hashlib.sha256,
            ).hexdigest()
            if not secrets.compare_digest(expected_proof, proof_values[0]):
                return _safe_error(403, "LOCAL_WORKSPACE_REQUIRED")
            request.state.workspace_id = workspace_values[0]

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

    def active_studio() -> OfflineStudioService:
        if offline_studio is None:
            raise OfflineStudioError("LOCAL_STUDIO_UNAVAILABLE")
        return offline_studio

    def studio_error(error: OfflineStudioError) -> JSONResponse:
        status = 503 if str(error) in {"LOCAL_STUDIO_UNAVAILABLE", "LOCAL_MODEL_UNAVAILABLE"} else 409
        return _safe_error(status, str(error))

    def active_raw_source() -> RawSourceService:
        if raw_source_service is None:
            raise RawSourceError("LOCAL_STUDIO_UNAVAILABLE")
        return raw_source_service

    def raw_source_error(error: RawSourceError) -> JSONResponse:
        code = str(error)
        status = 503 if code in {"LOCAL_STUDIO_UNAVAILABLE", "RAW_SOURCE_STORAGE_FAILED"} else 409
        if code in {
            "RAW_SOURCE_INPUT_INVALID", "RAW_SOURCE_CONTENT_TYPE_UNSUPPORTED",
            "RAW_SOURCE_SIZE_INVALID", "RAW_SOURCE_DIGEST_MISMATCH",
            "RAW_SOURCE_TEXT_INVALID", "RAW_SOURCE_PDF_INVALID",
            "RAW_SOURCE_EVIDENCE_EMPTY", "RAW_SOURCE_EVIDENCE_TOO_LARGE",
            "RAW_SOURCE_PROJECTION_INVALID",
        }:
            status = 400
        return _safe_error(status, code)

    def context_view(context: KnowledgeContextSnapshot) -> dict[str, object]:
        payload = cast(dict[str, object], asdict(context))
        payload["mode"] = context.mode.value
        return payload

    def draft_view(draft: OfflineDraftView) -> dict[str, object]:
        payload = cast(dict[str, object], asdict(draft))
        payload["context"] = context_view(draft.context)
        return payload

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

    @app.post(STUDIO_PROVIDER_SETTINGS_PATH, response_model=None)
    async def studio_provider_settings(
        http_request: Request, request: StudioProviderSettingsRequest
    ) -> dict[str, object] | JSONResponse:
        if request.workspace_id != http_request.state.workspace_id:
            return _safe_error(403, "LOCAL_WORKSPACE_REQUIRED")
        try:
            active_studio().import_provider_settings(
                workspace_id=request.workspace_id,
                profiles=tuple(item.model_dump() for item in request.profiles),
                deployments=tuple(item.model_dump() for item in request.deployments),
                policy_version=request.policy_version,
            )
            return {"data": {"workspace_id": request.workspace_id, "state": "stored"}}
        except OfflineStudioError as error:
            return studio_error(error)

    @app.get(STUDIO_RAW_SOURCES_PATH, response_model=None)
    async def studio_raw_sources(request: Request) -> dict[str, object] | JSONResponse:
        try:
            return {"data": [asdict(item) for item in active_raw_source().list_sources(
                request.state.workspace_id
            )]}
        except RawSourceError as error:
            return raw_source_error(error)

    @app.post(STUDIO_RAW_SOURCES_PATH, response_model=None)
    async def studio_raw_source_import(
        http_request: Request, request: StudioRawSourceImportRequest
    ) -> dict[str, object] | JSONResponse:
        if request.workspace_id != http_request.state.workspace_id:
            return _safe_error(403, "LOCAL_WORKSPACE_REQUIRED")
        try:
            content = b64decode(request.content_base64, validate=True)
            return {"data": asdict(active_raw_source().import_source(
                workspace_id=request.workspace_id,
                filename=request.filename,
                content_type=request.content_type,
                content=content,
                content_digest_sha256=request.content_digest_sha256,
                idempotency_key=request.idempotency_key,
            ))}
        except (Base64Error, ValueError):
            return _safe_error(400, "RAW_SOURCE_INPUT_INVALID")
        except RawSourceError as error:
            return raw_source_error(error)
    @app.get(STUDIO_MODELS_PATH, response_model=None)
    async def studio_models(request: Request) -> dict[str, object] | JSONResponse:
        try:
            models = active_studio().list_models(workspace_id=request.state.workspace_id)
            return {"data": [{
                "deployment_id": model.deployment_id, "label": model.model_id,
                "provider_code": model.provider_code,
                "provider_kind": model.provider_kind,
                "version": model.model_version, "readiness": "ready",
            } for model in models]}
        except OfflineStudioError as error:
            return studio_error(error)

    @app.post(STUDIO_CONTEXTS_PATH, response_model=None)
    async def studio_context(
        http_request: Request, request: StudioContextRequest
    ) -> dict[str, object] | JSONResponse:
        if request.workspace_id != http_request.state.workspace_id:
            return _safe_error(403, "LOCAL_WORKSPACE_REQUIRED")
        try:
            return {"data": context_view(active_studio().prepare_context(
                workspace_id=request.workspace_id, mode=request.mode,
                daon_knowledge_ids=tuple(request.daon_knowledge_ids),
                raw_source_version_ids=tuple(request.raw_source_version_ids),
                idempotency_key=request.idempotency_key,
            ))}
        except OfflineStudioError as error:
            return studio_error(error)

    @app.post(STUDIO_SETTINGS_PATH, response_model=None)
    async def studio_settings(
        http_request: Request, request: StudioSettingsRequest
    ) -> dict[str, object] | JSONResponse:
        if request.workspace_id != http_request.state.workspace_id:
            return _safe_error(403, "LOCAL_WORKSPACE_REQUIRED")
        try:
            view = active_studio().confirm_settings(
                workspace_id=request.workspace_id,
                request=ConfirmSettingsInput(
                    request.title, request.purpose, request.temperature, request.max_output_tokens
                ),
                context_snapshot_id=request.context_snapshot_id,
                model_deployment_id=request.model_deployment_id,
                idempotency_key=request.idempotency_key,
                selection_actor_id=request.selection_actor_id,
            )
            return {"data": asdict(view)}
        except OfflineStudioError as error:
            return studio_error(error)

    @app.post(STUDIO_GENERATE_PATH, response_model=None)
    async def studio_generate(
        http_request: Request, request: StudioGenerateRequest
    ) -> dict[str, object] | JSONResponse:
        if request.workspace_id != http_request.state.workspace_id:
            return _safe_error(403, "LOCAL_WORKSPACE_REQUIRED")
        try:
            return {"data": draft_view(active_studio().generate_draft(
                workspace_id=request.workspace_id, request_id=request.request_id,
                idempotency_key=request.idempotency_key,
            ))}
        except OfflineStudioError as error:
            return studio_error(error)

    @app.get(STUDIO_DRAFT_PATH, response_model=None)
    async def studio_get(request: Request, id: str) -> dict[str, object] | JSONResponse:
        try:
            return {"data": draft_view(active_studio().get_draft(
                workspace_id=request.state.workspace_id, draft_id=id,
            ))}
        except (OfflineStudioError, LocalStorageError) as error:
            return studio_error(OfflineStudioError(str(error)))

    @app.post(STUDIO_VERSION_PATH, response_model=None)
    async def studio_append(
        http_request: Request, id: str, request: StudioAppendRequest
    ) -> dict[str, object] | JSONResponse:
        if request.workspace_id != http_request.state.workspace_id:
            return _safe_error(403, "LOCAL_WORKSPACE_REQUIRED")
        if sum(len(section.body.encode("utf-8")) for section in request.sections) > 1_048_576:
            return _safe_error(413, "LOCAL_INPUT_INVALID")
        try:
            return {"data": draft_view(active_studio().append_edit(
                workspace_id=request.workspace_id, draft_id=id,
                previous_version_id=request.previous_version_id,
                sections=tuple(SectionInput(
                    section.title, section.body, section.unverified
                ) for section in request.sections),
                idempotency_key=request.idempotency_key,
            ))}
        except OfflineStudioError as error:
            return studio_error(error)

    @app.post(STUDIO_QUEUE_PATH, response_model=None)
    async def studio_queue(
        http_request: Request, id: str, request: StudioQueueRequest
    ) -> dict[str, object] | JSONResponse:
        if request.workspace_id != http_request.state.workspace_id:
            return _safe_error(403, "LOCAL_WORKSPACE_REQUIRED")
        try:
            return {"data": asdict(active_studio().queue_sync_preview(
                workspace_id=request.workspace_id, draft_id=id,
                output_version_id=request.output_version_id,
                source_dependency_ids=tuple(request.source_dependency_ids),
                idempotency_key=request.idempotency_key,
            ))}
        except OfflineStudioError as error:
            return studio_error(error)

    def knowledge_error(error: LocalStorageError) -> JSONResponse:
        code = str(error)
        if code == "LOCAL_KNOWLEDGE_COPY_NOT_FOUND" or code == "LOCAL_SYNC_QUEUE_NOT_FOUND":
            return _safe_error(404, code)
        if code in {
            "LOCAL_KNOWLEDGE_COPY_INVALID", "LOCAL_KNOWLEDGE_COPY_DIGEST_MISMATCH",
            "LOCAL_SYNC_QUEUE_INVALID",
        }:
            return _safe_error(400, code)
        if code in {"LOCAL_KNOWLEDGE_COPY_IDEMPOTENCY_CONFLICT", "LOCAL_KNOWLEDGE_COPY_IMMUTABLE"}:
            return _safe_error(409, code)
        return storage_error(error)

    @app.post(STUDIO_KNOWLEDGE_COPY_PATH, response_model=None)
    async def studio_knowledge_copy_import(
        request: KnowledgeCopyImportRequest,
    ) -> dict[str, object] | JSONResponse:
        try:
            canonical_package = b64decode(request.canonical_package_base64, validate=True)
            if len(canonical_package) > 12 * 1024 * 1024:
                return _safe_error(413, "LOCAL_KNOWLEDGE_COPY_TOO_LARGE")
            payload = request.model_dump(exclude={
                "canonical_package_base64", "manifest_digest_sha256", "idempotency_key",
            })
            copy = active_storage().import_knowledge_copy(
                manifest=payload,
                manifest_digest_sha256=request.manifest_digest_sha256,
                canonical_package=canonical_package,
                idempotency_key=request.idempotency_key,
            )
            return {"data": asdict(copy)}
        except (Base64Error, ValueError):
            return _safe_error(400, "LOCAL_KNOWLEDGE_COPY_INVALID")
        except LocalStorageError as error:
            return knowledge_error(error)

    @app.post(STUDIO_KNOWLEDGE_REFRESH_PATH, response_model=None)
    async def studio_knowledge_copy_refresh(
        id: str, request: KnowledgeCopyRefreshRequest,
    ) -> dict[str, object] | JSONResponse:
        try:
            return {"data": asdict(active_storage().refresh_knowledge_copy(
                request.workspace_id, id, state=request.state, recorded_at=request.recorded_at,
            ))}
        except LocalStorageError as error:
            return knowledge_error(error)

    @app.get(STUDIO_SYNC_STATE_PATH, response_model=None)
    async def studio_sync_state(id: str) -> dict[str, object] | JSONResponse:
        try:
            return {"data": asdict(active_storage().get_sync_queue_state_global(id))}
        except LocalStorageError as error:
            return knowledge_error(error)

    @app.post(STUDIO_SYNC_STATE_APPEND_PATH, response_model=None)
    async def studio_sync_state_append(
        id: str, request: SyncStateAppendRequest,
    ) -> dict[str, object] | JSONResponse:
        try:
            active_storage().append_sync_queue_state(
                request.workspace_id, operation_id=id, version=request.version,
                approval_state=request.approval_state,
                manifest_digest=request.manifest_digest, batch_cursor=request.batch_cursor,
                conflict_id=request.conflict_id, queued_at=request.queued_at,
                previous_version=request.previous_version,
            )
            return {"data": asdict(active_storage().get_sync_queue_state(request.workspace_id, id))}
        except LocalStorageError as error:
            return knowledge_error(error)

    return app
