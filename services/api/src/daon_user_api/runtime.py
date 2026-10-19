"""Explicit FastAPI process composition for the approved M4 domain cores."""

from __future__ import annotations

import hashlib
import ipaddress
import asyncio
import base64
import binascii
import json
import logging
import os
import re
import secrets
import subprocess
import sys
import threading
import time
from concurrent.futures import Future
from contextlib import asynccontextmanager, contextmanager
from dataclasses import dataclass, field, fields, is_dataclass, replace
from datetime import datetime, timedelta, timezone
from enum import Enum
from pathlib import Path
from typing import Annotated, Any, AsyncIterator, Literal, Mapping, cast
from urllib.parse import urlsplit

from fastapi import FastAPI, Header, HTTPException, Query, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from fastapi.routing import APIRoute
from pydantic import BaseModel, ConfigDict, Field, RootModel, model_validator
from starlette.exceptions import HTTPException as StarletteHTTPException
from starlette.responses import Response

from .audit import (
    ActorType, AuditEvent, AuditEventDraft, AuditEventStore, AuditOutcome,
    AuditValidationError,
)
from .admin_users import AdminUserService
from .admin_membership_roles import AdminMembershipRoleService, REASON_CODES
from .cloud_storage import CloudAccessContext, PostgresCloudStore
from .internet_connector import InternetConnector, WebsiteImportContext, WebsiteFetchPolicy
from .safe_http_fetch import SafeHttpTransport, normalize_public_url, WebsiteFetchError
from .website_source_import import WebsiteImportError, WebsiteSourceImportService, WebsiteObjectPromoter, WebsiteProcessStorage
from .website_source_postgres import PostgresWebsiteSourceRepository
from .open_law_client import OpenLawError,OpenLawSelection,validate_search,validate_search_result
from .open_law_mcp_client import OpenLawMcpClient
from .open_law_mcp_server import PROTOCOL as LAW_PROTOCOL,SCHEMA_DIGEST as LAW_SCHEMA_DIGEST
from .open_law_source_import import OpenLawImportError,OpenLawSourceImportService,OpenLawObjectPromoter
from .open_law_source_postgres import PostgresOpenLawSourceRepository
from .open_law_source_access import open_law_enabled,require_open_law_source_access
from .data_canon import PostgresDataCanonStore
from .data_canon import canonical_json_bytes
from .authorization import (
    AccessAction,
    AccessDecision,
    Action,
    AuthorizationError,
    AuthorizationGrant,
    AuthorizationService,
    Permission,
    Role,
    SqliteAuthorizationRepository,
    WORKSPACE_ROLES,
)
from .identity import (
    ClientKind,
    DevicePlatform,
    IdentityError,
    IdentityPrincipal,
    IdentityService,
    SqliteIdentityRepository,
)
from .identity_postgres import PostgresIdentityRepository
from .android_browser_auth import AndroidBrowserAuthService
from .organization_api import OrganizationApi
from .organization_membership import OrganizationWorkflowError, SqliteOrganizationRepository
from .postgres_adapters import PostgresAuthorizationRepository, PostgresOrganizationRepository
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
    ObjectKeyPolicy,
    ObjectQueueCoordinator,
    ObjectStoragePort,
    PostgresObjectQueueStore,
)
from .data_canon import PostgresDataCanonStore
from .source_ingest import SourceIngestor, SourceRejected
from .source_upload import (
    PostgresSourceUploadService,
    SourceUploadError,
    SourceUploadPort,
)
from .document_processing import (
    DocumentProcessingContext,
    DocumentProcessingSubmissionService,
)
from .document_processing_postgres import PostgresDocumentProcessingRepository
from .document_readiness import ReadinessDispatcherSettings
from .document_understanding_adapter import (
    DocumentUnderstandingError, ServerProviderCredentialResolver,
    UrlLibDocumentUnderstandingTransport,
)
from .document_index_postgres import PostgresDocumentIndex
from .question_answering_postgres import (
    PostgresQuestionAnsweringRepository, QuestionContext, QuestionRepositoryError,
)
from .question_answering_service import (
    QuestionAdapterRegistry, QuestionAnsweringError, QuestionAnsweringService,
    QuestionInputSource, question_request_fingerprint,
)
from .question_answering import classify_question_intent
from .notebook_deletion import NotebookDeletionStore, NotebookDeletionWorker, PostgresNotebookDeletionStore
from .egress_policy import EgressPolicyService
from .egress_policy import EgressPolicyContext, EgressPolicyError, EgressPolicyPayload
from .egress_policy_postgres import PostgresEgressPolicyRepository
from .question_egress import PostgresQuestionEgressAuthorizer, external_question_policy_matches
from .studio_report import (
    StudioReportContext, StudioReportCreateRequest, StudioReportError, StudioReportService,
)
from .studio_report_postgres import PostgresStudioReportRepository
from .studio_workspace import StudioContext, StudioError, StudioGenerationRequest, StudioWorkspaceService
from .studio_workspace_postgres import PostgresStudioWorkspaceRepository
from .studio_approval_lifecycle import StudioApprovalLifecycleRepository
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
from .knowledge_package import (
    KnowledgePackageContext,
    KnowledgePackageError,
    KnowledgePackageService,
    ReferenceKnowledgePackageRepository,
)
from .knowledge_package_postgres import PostgresKnowledgePackageService
from .mcp_connector import ConnectorError, ConnectorRegistry, create_open_law_connector
from .connector_postgres import PostgresConnectorRepository
from .approved_knowledge_connector import ApprovedKnowledgeConnector
from .retention import (
    DerivativeInput, ReferenceCleanupPort, ReferenceRetentionRepository,
    RetentionContext, RetentionError, RetentionService,
)
from .recovery import (
    BackupObjectInput, BackupSelectionInput, RecoveryContext, RecoveryError, RecoveryService,
    RestoreDestination, UnavailableRecoveryService,
)
from .recovery_postgres import MinioRecoveryStorageAdapter, PostgresBackupSelectionResolver, PostgresRecoveryService
from .recovery_capture import FileCaptureStore
from .recovery_lineage_capture import FileLineageCaptureStore
from .provider_settings import (
    PostgresProviderSettingsRepository,
    ProviderSettingsContext,
    ProviderSettingsError,
    ProviderSettingsService,
    ReferenceProviderSettingsRepository,
    ServerCredentialPresenceResolver,
)
from .provider_health_settings import (
    PostgresProviderHealthCheckSettingsService,
    PostgresProviderHealthSettingsRepository,
    ProviderHealthSettingsError,
)
from .provider_health_monitor import ProviderHealthConnection, ProviderHealthMonitor
from .provider_connection_admin import (
    ProviderCapabilityCommand,
    ProviderConnectionAdminContext,
    ProviderConnectionAdminError,
    ProviderConnectionCreateCommand,
    ProviderCredentialReplaceCommand,
    ProviderConnectionUpdateCommand,
    PostgresProviderConnectionService,
)
from .provider_credentials import ProviderCredentialCipher
from .user_provider_credentials import (
    PostgresUserProviderCredentialService,
    UserProviderCredentialError,
)
from .workspace_model_defaults import (
    PostgresWorkspaceModelDefaultsService, PostgresWorkspaceModelResolver,
    WorkspaceModelDefaultsContext, WorkspaceModelDefaultsError,
)
from .retention_inventory_postgres import PostgresRetentionInventoryProvider
from .retention_request_postgres import PostgresRetentionRequestService
from .operations_status import OperationsStatusContext, OperationsStatusService, SystemOperationsStatusService
from .operations_status_postgres import (
    OperationsStatusError,
    PostgresOperationsStatusRepository,
)
from .output_version_settings import (
    OutputVersionSettingsContext,
    OutputVersionSettingsError,
    OutputVersionSettingsService,
    ReferenceOutputVersionSettingsRepository,
)
from .output_version_settings_postgres import PostgresOutputVersionSettingsRepository
from .screen_preferences import (
    ReferenceScreenPreferenceRepository,
    ScreenPreferenceContext,
    ScreenPreferenceError,
    ScreenPreferenceService,
)
from .screen_preferences_postgres import PostgresScreenPreferenceRepository
from .license import (
    LicenseContext, LicenseError, LicenseService, ReferenceLicenseRepository,
    RsaSha256LicenseVerifier, UnavailableLicenseVerifier, load_rsa_public_keys,
)
from .license_postgres import PostgresLicenseRepository, enforce_license_creation
from .notebook import (
    NotebookContext, NotebookError, NotebookHomeView, NotebookService,
    NotebookDeletionView,
    ReferenceNotebookRepository,
)
from .notebook_postgres import PostgresNotebookRepository


WEB_SESSION_COOKIE = "__Host-daon_session"
WEB_SESSION_COOKIE_MAX_AGE = 10 * 365 * 24 * 60 * 60
_ADMIN_AUDIT_RETRY_INTERVAL_SECONDS = 60.0
_ADMIN_AUDIT_RETRY_SHUTDOWN_SECONDS = 5.0
_TRACE_ID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:-]{0,127}$")
_TRACEPARENT = re.compile(
    r"^[0-9a-f]{2}-([0-9a-f]{32})-[0-9a-f]{16}-[0-9a-f]{2}$"
)

_source_boundary_logger = logging.getLogger(__name__)
_runtime_logger = logging.getLogger(__name__)


def _source_boundary_event(
    *, event: str, phase: str, trace_id: str, http_status: int,
    safe_error_code: str | None = None, source_id_present: bool = False,
    processing_run_id_present: bool = False, db_commit: bool = False,
) -> None:
    """Emit upload/processing diagnostics without source or credential values."""
    _source_boundary_logger.info(
        "source_boundary event=%s phase=%s trace_id=%s http_status=%d "
        "safe_error_code=%s source_id_present=%s processing_run_id_present=%s "
        "db_commit=%s",
        event, phase, trace_id, http_status, safe_error_code or "none",
        source_id_present, processing_run_id_present, db_commit,
    )
_BODY_METHODS = frozenset({"POST", "PUT", "PATCH"})
_SOURCE_UPLOAD_PATH = re.compile(r"^/api/v1/workspaces/[A-Za-z0-9][A-Za-z0-9._:-]{0,127}/sources$")
_SOURCE_FILENAME = re.compile(r"^[^/\\\x00-\x1f]{1,251}$")
_QUESTION_REQUEST_TIMEOUT_SECONDS = None
_RFC1918_NETWORKS = tuple(
    ipaddress.ip_network(value) for value in ("10.0.0.0/8", "172.16.0.0/12", "192.168.0.0/16")
)


def _is_private_http_gateway(parsed: object) -> bool:
    try:
        scheme = parsed.scheme  # type: ignore[attr-defined]
        hostname = parsed.hostname  # type: ignore[attr-defined]
        port = parsed.port  # type: ignore[attr-defined]
        address = ipaddress.ip_address(hostname)
    except (AttributeError, TypeError, ValueError):
        return False
    return (
        scheme == "http"
        and port is not None
        and address.version == 4
        and any(address in network for network in _RFC1918_NETWORKS)
    )


def request_timeout_for_path(settings: "RuntimeSettings", path: str, *, method: str = 'POST') -> float | None:
    if re.fullmatch(r'/api/v1/workspaces/[A-Za-z0-9][A-Za-z0-9._:-]{0,127}/(?:connectors/mcp-open-law-go-kr/law-(?:search|preview|imports)|source-versions/[A-Za-z0-9][A-Za-z0-9._:-]{0,127}/law-snapshot)',path):
        return 25.0
    if re.fullmatch(r"/api/v1/workspaces/[A-Za-z0-9][A-Za-z0-9._:-]{0,127}/website-imports", path):
        return 20.0
    if method == 'POST' and re.fullmatch(r"/api/v1/workspaces/[A-Za-z0-9][A-Za-z0-9._:-]{0,127}/questions", path):
        return _QUESTION_REQUEST_TIMEOUT_SECONDS
    return settings.request_timeout_seconds


def _website_outbound(url):
    """Public-confirmed 웹 요청에 private Source/질문/세션을 결속하지 않는다.

    privacy flags는 유지한다. 제거 대상이 없는 고정 GET만 검증하며 URL의
    userinfo/query/fragment와 임의 header/body는 이 경계에서 표현할 수 없다.
    실제 전송은 W1 SafeHttpTransport의 동일 고정 헤더만 사용한다.
    """
    try:
        target=normalize_public_url(url)
    except (ValueError, TypeError, WebsiteFetchError):
        raise WebsiteImportError('WEBSITE_FETCH_BLOCKED',403) from None
    return {'url':target,'method':'GET','headers':{'Host':urlsplit(target).hostname,
        'User-Agent':'Daon-User-Safe-Fetch/1.0','Accept':'text/html, text/plain','Accept-Encoding':'identity'}}


def _website_public_page(page):
    # W1의 빈 HTML 제목/plain text fallback은 final URL이다. 긴 URL을
    # 제목으로 절단하거나 W2에 저장한 뒤 consumer가 거부하게 하지 않는다.
    if not isinstance(page.title,str) or not page.title.strip() or len(page.title)>255:
        raise WebsiteImportError('WEBSITE_FETCH_FAILED',502)
    return page


class _WebsiteRequestRepository(PostgresWebsiteSourceRepository):
    """요청 소유 repository만 API 절대deadline에 참여. W2/global store 불변."""
    def __init__(self,*args,request_deadline,**kwargs):
        super().__init__(*args,**kwargs)
        self._request_deadline=request_deadline

    @contextmanager
    def _transaction(self,context,*,enforce_deadline=True):
        if enforce_deadline and time.monotonic()>=self._request_deadline:
            raise WebsiteImportError('WEBSITE_FETCH_TIMEOUT',504)
        with super()._transaction(context,enforce_deadline=enforce_deadline) as conn:
            if enforce_deadline:
                remaining=self._request_deadline-time.monotonic()
                if remaining<=0:raise WebsiteImportError('WEBSITE_FETCH_TIMEOUT',504)
                conn.execute("SELECT set_config('statement_timeout',%s,true)",(str(max(1,min(2000,int(remaining*1000)))),))
            yield conn
            if enforce_deadline and time.monotonic()>=self._request_deadline:
                raise WebsiteImportError('WEBSITE_FETCH_TIMEOUT',504)


class _OpenLawRequestRepository(PostgresOpenLawSourceRepository):
    def __init__(self,*args,request_deadline,cancelled=lambda:False,**kwargs):
        super().__init__(*args,**kwargs);self._request_deadline=request_deadline;self._cancelled=cancelled

    @contextmanager
    def _transaction(self,ctx,*,enforce_deadline=True):
        if enforce_deadline and (self._cancelled() or time.monotonic()>=self._request_deadline):raise OpenLawImportError('OPEN_LAW_TIMEOUT')
        with super()._transaction(ctx,enforce_deadline=enforce_deadline) as conn:
            if enforce_deadline:
                remaining=self._request_deadline-time.monotonic()
                if remaining<=0:raise OpenLawImportError('OPEN_LAW_TIMEOUT')
                conn.execute("SELECT set_config('statement_timeout',%s,true)",(str(max(1,min(2000,int(remaining*1000)))),))
            yield conn
            if enforce_deadline and (self._cancelled() or time.monotonic()>=self._request_deadline):raise OpenLawImportError('OPEN_LAW_TIMEOUT')


class _WebsiteRequestPromoter(WebsiteObjectPromoter):
    def __init__(self,*args,request_deadline):
        super().__init__(*args)
        self._request_deadline=request_deadline

    def promote(self,context,claim,kind,content,deadline):
        return super().promote(context,claim,kind,content,min(deadline,self._request_deadline))


class _OpenLawRequestPromoter(OpenLawObjectPromoter):
    def __init__(self,*args,request_deadline):
        super().__init__(*args);self._request_deadline=request_deadline

    def promote(self,context,claim,kind,content,deadline):
        return super().promote(context,claim,kind,content,min(deadline,self._request_deadline))


def _checked_website_text(raw,digest,size):
    try:
        if (type(raw) is not bytes or type(size) is not int or not 0<size<=524288
                or len(raw)!=size or hashlib.sha256(raw).hexdigest()!=digest):
            raise ValueError
        text=raw.decode('utf-8',errors='strict')
        if not text.strip():
            raise ValueError
        return text
    except (ValueError, UnicodeError):
        raise WebsiteImportError('WEBSITE_STORAGE_UNAVAILABLE') from None


def _website_json_response(content,*,maximum=32768,status=200,headers=None):
    body=bytearray()
    for part in json.JSONEncoder(ensure_ascii=False,allow_nan=False,separators=(',',':')).iterencode(content):
        encoded=part.encode('utf-8')
        if len(encoded)>maximum-len(body):
            raise WebsiteImportError('WEBSITE_CONTENT_TOO_LARGE',413)
        body.extend(encoded)
    return Response(bytes(body),status_code=status,media_type='application/json',headers={
        'Cache-Control':'private, no-store','X-Content-Type-Options':'nosniff',**(headers or {})})


def _website_json_pairs(pairs):
    value={}
    for key,item in pairs:
        if key in value:raise ValueError
        value[key]=item
    return value


def _website_query(request,allowed):
    _require_query_keys(request,frozenset(allowed))
    if any(len(request.query_params.getlist(key))!=1 for key in request.query_params):
        raise HTTPException(status_code=400)


def _website_read_exchange(child,payload,deadline):
    output=bytearray(); failed=threading.Event()
    def write():
        try:
            child.stdin.write(payload); child.stdin.flush()
        except (OSError,ValueError): failed.set()
        finally: child.stdin.close()
    def read():
        try:
            while True:
                chunk=child.stdout.read(min(1024,524289-len(output)))
                if not chunk: break
                if len(chunk)>524288-len(output):
                    failed.set(); child.kill(); break
                output.extend(chunk)
        except (OSError,ValueError): failed.set()
    writer=threading.Thread(target=write); reader=threading.Thread(target=read)
    writer.start(); reader.start()
    try:
        try: child.wait(timeout=max(.001,deadline-time.monotonic()))
        except subprocess.TimeoutExpired:
            child.kill(); child.wait()
            raise WebsiteImportError('WEBSITE_FETCH_TIMEOUT',504) from None
    finally:
        if child.poll() is None: child.kill(); child.wait()
        writer.join(); reader.join()
    if failed.is_set() or child.returncode!=0 or time.monotonic()>=deadline:
        raise WebsiteImportError('WEBSITE_STORAGE_UNAVAILABLE')
    return bytes(output)


def _website_text_child():
    """자격은 bounded stdin/memory에만. DB 확정/쓰기와 raw 오류 출력 없음."""
    try:
        payload=sys.stdin.buffer.read(32769)
        if len(payload)>32768: raise ValueError
        request=json.loads(payload); remaining=request['deadline']-time.monotonic()
        if remaining<=0: raise ValueError
        adapter=MinioObjectStorageAdapter(**request['config'])
        import urllib3
        import certifi
        adapter._client._http.clear()
        adapter._client._http=urllib3.PoolManager(timeout=urllib3.Timeout(connect=min(2,remaining),read=remaining),
            retries=False,cert_reqs='CERT_REQUIRED',ca_certs=os.environ.get('SSL_CERT_FILE') or certifi.where())
        adapter._stat_verified(request['key'],expected_digest=request['digest'],expected_size=request['size'],content_type='text/plain')
        response=adapter._client.get_object(adapter._bucket,request['key'])
        try:
            body=bytearray()
            while True:
                chunk=response.read(min(4096,524289-len(body)))
                if not chunk: break
                if len(chunk)>524288-len(body): raise ValueError
                body.extend(chunk)
            raw=bytes(body)
        finally:
            response.close(); response.release_conn()
        _checked_website_text(raw,request['digest'],request['size'])
        sys.stdout.buffer.write(raw); sys.stdout.buffer.flush()
    except Exception:
        raise SystemExit(1) from None


def _read_open_law_original(context,key,digest,size,storage,*,request_deadline=None):
    """법령 text 전용 읽기: 고정 child/5초/512KiB, 자격은 stdin만."""
    from .open_law_source_import import OpenLawImportError
    try:
        if (not isinstance(storage,WebsiteProcessStorage) or type(size) is not int
                or not 0<size<=524288 or not isinstance(key,str) or len(key)>4096
                or not isinstance(digest,str) or not re.fullmatch('[0-9a-f]{64}',digest)):
            raise ValueError
        ObjectKeyPolicy().validate_final(CloudAccessContext(context.tenant_id,context.workspace_id,context.actor_id,'object.read'),'source',key)
        deadline=min(time.monotonic()+5.0,request_deadline) if request_deadline is not None else time.monotonic()+5.0
        if time.monotonic()>=deadline:raise OpenLawImportError('OPEN_LAW_TIMEOUT')
        payload=json.dumps({'config':storage._config,'key':key,'digest':digest,'size':size,'deadline':deadline},separators=(',',':')).encode()
        if len(payload)>32768:raise ValueError
        with subprocess.Popen([sys.executable,'-B','-c','from daon_user_api.runtime import _website_text_child; _website_text_child()'],
                stdin=subprocess.PIPE,stdout=subprocess.PIPE,stderr=subprocess.DEVNULL,shell=False,
                creationflags=subprocess.CREATE_NO_WINDOW if os.name=='nt' else 0) as child:
            raw=_website_read_exchange(child,payload,deadline)
        _checked_website_text(raw,digest,size)
        return raw
    except OpenLawImportError:raise
    except WebsiteImportError as error:
        code='OPEN_LAW_TIMEOUT' if error.code=='WEBSITE_FETCH_TIMEOUT' else 'OPEN_LAW_STORAGE_UNAVAILABLE'
        raise OpenLawImportError(code,504 if code=='OPEN_LAW_TIMEOUT' else 503) from None
    except Exception:raise OpenLawImportError('OPEN_LAW_STORAGE_UNAVAILABLE',503) from None


def _read_website_original(repo,context,version_id,storage):
    snapshot=repo.get_snapshot(context,version_id)
    if not isinstance(storage,WebsiteProcessStorage):
        raise WebsiteImportError('WEBSITE_STORAGE_UNAVAILABLE')
    with repo._transaction(context) as conn:
        repo._notebook(conn,context)
        row=conn.execute('SELECT o.object_key,o.digest_sha256,o.byte_size FROM website_source_snapshots w '
            'JOIN object_records o ON o.tenant_id=w.tenant_id AND o.workspace_id=w.workspace_id AND o.object_id=w.text_object_id '
            'WHERE w.tenant_id=%s AND w.workspace_id=%s AND w.created_by=%s AND w.notebook_id=%s '
            "AND w.source_version_id=%s AND o.created_by=w.created_by AND o.status='completed' "
            "AND o.content_type='text/plain' AND o.digest_sha256=w.text_sha256",
            (*repo._scope(context),version_id)).fetchone()
    if (row is None or row[1]!=snapshot.text_sha256 or type(row[2]) is not int or not 0<row[2]<=524288
            or not isinstance(row[0],str) or len(row[0])>4096):
        raise WebsiteImportError('WEBSITE_STORAGE_UNAVAILABLE')
    try:
        ObjectKeyPolicy().validate_final(CloudAccessContext(context.tenant_id,context.workspace_id,context.actor_id,'object.read'),'source',row[0])
    except Exception:
        raise WebsiteImportError('WEBSITE_STORAGE_UNAVAILABLE') from None
    deadline=time.monotonic()+5.0
    payload=json.dumps({'config':storage._config,'key':row[0],'digest':row[1],'size':row[2],'deadline':deadline},separators=(',',':')).encode()
    if len(payload)>32768: raise WebsiteImportError('WEBSITE_STORAGE_UNAVAILABLE')
    try:
        with subprocess.Popen([sys.executable,'-B','-c','from daon_user_api.runtime import _website_text_child; _website_text_child()'],
                stdin=subprocess.PIPE,stdout=subprocess.PIPE,stderr=subprocess.DEVNULL,
                creationflags=subprocess.CREATE_NO_WINDOW if os.name=='nt' else 0) as child:
            raw=_website_read_exchange(child,payload,deadline)
    except WebsiteImportError: raise
    except Exception: raise WebsiteImportError('WEBSITE_STORAGE_UNAVAILABLE') from None
    text=_checked_website_text(raw,row[1],row[2])
    if repo.get_snapshot(context,version_id)!=snapshot:
        raise WebsiteImportError('WEBSITE_ACCESS_DENIED',403)
    return text


@dataclass(frozen=True, slots=True)
class RuntimeSettings:
    profile: str
    bind_host: str
    port: int
    database_path: Path | None = None
    organization_database_path: Path | None = None
    cloud_database_dsn: str | None = None
    object_storage_endpoint: str | None = None
    object_storage_bucket: str | None = None
    object_access_key_file: Path | None = None
    object_secret_key_file: Path | None = None
    recovery_manifest_key_file: Path | None = None
    recovery_capture_root: Path | None = None
    recovery_capture_key_version: str | None = None
    recovery_capture_key_reference: str | None = None
    recovery_restore_config_file: Path | None = field(default=None,repr=False)
    step_up_token_key_file: Path | None = None
    provider_credential_key_file: Path | None = None
    license_public_keys_file: Path | None = None
    object_storage_secure: bool = True
    object_storage_provision_bucket: bool = False
    policy_version: str = "runtime-policy-v1"
    public_gateway_url: str | None = None
    android_auth_clients: Mapping[str, frozenset[str]] = field(default_factory=dict)
    qa_structured_ollama_workspace_id: str = ""
    allow_private_http_gateway: bool = False
    trusted_proxy_ips: tuple[str, ...] = ()
    max_body_bytes: int = 65_536
    source_upload_max_bytes: int = 25 * 1024 * 1024
    max_header_bytes: int = 16_384
    request_timeout_seconds: float = 30.0
    drain_timeout_seconds: float = 10.0
    dev_auth_bypass: bool = False
    development_stage: bool | None = None
    system_admin_user_ids: frozenset[str] = frozenset({"admin"})
    organization_admin_v2_enabled: bool = False
    notification_inbox_enabled: bool = False
    document_readiness_settings: ReadinessDispatcherSettings = field(default_factory=ReadinessDispatcherSettings)

    def __post_init__(self) -> None:
        if type(self.document_readiness_settings) is not ReadinessDispatcherSettings:
            raise ValueError("DOCUMENT_PROCESSING_CONTEXT_INVALID")
        if self.recovery_restore_config_file is not None and (
            not isinstance(self.recovery_restore_config_file,Path) or not self.recovery_restore_config_file.is_absolute()
            or ".." in self.recovery_restore_config_file.parts):
            raise ValueError("REGISTERED_RESTORE_CONFIG_INVALID")
        capture = (self.recovery_capture_root, self.recovery_capture_key_version, self.recovery_capture_key_reference)
        if any(value is not None for value in capture):
            if (not all(value is not None for value in capture)
                    or not isinstance(self.recovery_capture_root, Path) or not self.recovery_capture_root.is_absolute()
                    or self.recovery_capture_root == Path(self.recovery_capture_root.anchor)
                    or not isinstance(self.recovery_capture_key_version, str)
                    or re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._:-]{0,127}", self.recovery_capture_key_version) is None
                    or self.recovery_capture_key_reference != "DAON_RECOVERY_MANIFEST_KEY_FILE"
                    or self.recovery_manifest_key_file is None):
                raise ValueError("RECOVERY_CAPTURE_CONFIGURATION_INVALID")
        if type(self.organization_admin_v2_enabled) is not bool:
            raise ValueError("ORGANIZATION_ADMIN_V2_FLAG_INVALID")
        if type(self.notification_inbox_enabled) is not bool:
            raise ValueError("NOTIFICATION_INBOX_FLAG_INVALID")
        if (
            not isinstance(self.qa_structured_ollama_workspace_id, str)
            or (self.qa_structured_ollama_workspace_id and not re.fullmatch(
                r"[A-Za-z0-9][A-Za-z0-9._:-]{0,127}", self.qa_structured_ollama_workspace_id,
            ))
        ):
            raise ValueError("RUNTIME_QA_STRUCTURED_OLLAMA_TARGET_INVALID")
        if self.profile not in {"test", "development", "production"}:
            raise ValueError("RUNTIME_PROFILE_INVALID")
        if not isinstance(self.port, int) or isinstance(self.port, bool) or not 1 <= self.port <= 65_535:
            raise ValueError("RUNTIME_PORT_INVALID")
        if (
            self.max_body_bytes < 1
            or self.source_upload_max_bytes < 1
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
            if not loopback and not (self.profile == "development" and self.dev_auth_bypass):
                raise ValueError("PLAINTEXT_BIND_MUST_BE_LOOPBACK")
        else:
            if self.cloud_database_dsn is None:
                raise ValueError("CLOUD_DATABASE_DSN_REQUIRED")
            if self.public_gateway_url is None:
                raise ValueError("PUBLIC_GATEWAY_REQUIRED")
            if self.step_up_token_key_file is None:
                raise ValueError("STEP_UP_TOKEN_KEY_REFERENCE_REQUIRED")
            parsed = urlsplit(self.public_gateway_url)
            if (
                not (
                    parsed.scheme == "https"
                    or (self.allow_private_http_gateway and _is_private_http_gateway(parsed))
                )
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
    def for_test(
        cls, *, database_path: Path, policy_version: str,
        system_admin_user_ids: frozenset[str] = frozenset({"admin"}),
    ) -> "RuntimeSettings":
        return cls(
            profile="test",
            bind_host="127.0.0.1",
            port=8000,
            database_path=database_path,
            policy_version=policy_version,
            development_stage=True,
            system_admin_user_ids=system_admin_user_ids,
        )

    @classmethod
    def from_env(cls) -> "RuntimeSettings":
        profile = os.environ.get("DAON_RUNTIME_PROFILE", "development")
        organization_flag = os.environ.get("DAON_ORGANIZATION_ADMIN_V2_ENABLED", "false")
        if organization_flag not in {"true", "false"}:
            raise ValueError("ORGANIZATION_ADMIN_V2_FLAG_INVALID")
        notification_flag = os.environ.get("DAON_NOTIFICATION_INBOX_ENABLED", "false")
        if notification_flag not in {"true", "false"}:
            raise ValueError("NOTIFICATION_INBOX_FLAG_INVALID")
        database = os.environ.get("DAON_API_DATABASE_PATH")
        try:
            android_clients_raw = json.loads(os.environ.get("DAON_ANDROID_AUTH_CLIENTS_JSON", "{}"))
            if not isinstance(android_clients_raw, dict) or any(
                not isinstance(client, str) or not isinstance(redirects, list)
                or not redirects or any(not isinstance(uri, str) for uri in redirects)
                for client, redirects in android_clients_raw.items()
            ):
                raise ValueError
            android_clients = {
                client: frozenset(redirects) for client, redirects in android_clients_raw.items()
            }
        except (ValueError, TypeError) as error:
            raise ValueError("ANDROID_AUTH_POLICY_INVALID") from error
        proxies = tuple(
            value.strip()
            for value in os.environ.get("DAON_TRUSTED_PROXY_IPS", "").split(",")
            if value.strip()
        )
        return cls(
            profile=profile,
            document_readiness_settings=ReadinessDispatcherSettings.from_env(),
            organization_admin_v2_enabled=organization_flag == "true",
            notification_inbox_enabled=notification_flag == "true",
            bind_host=os.environ.get("DAON_API_BIND_HOST", "127.0.0.1"),
            port=int(os.environ.get("DAON_API_PORT", "8000")),
            database_path=None if database is None else Path(database),
            organization_database_path=(
                None if os.environ.get("DAON_ORGANIZATION_DATABASE_PATH") is None
                else Path(os.environ["DAON_ORGANIZATION_DATABASE_PATH"])
            ),
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
            recovery_manifest_key_file=(
                None
                if os.environ.get("DAON_RECOVERY_MANIFEST_KEY_FILE") is None
                else Path(os.environ["DAON_RECOVERY_MANIFEST_KEY_FILE"])
            ),
            recovery_capture_root=(None if os.environ.get("DAON_RECOVERY_CAPTURE_ROOT") is None
                else Path(os.environ["DAON_RECOVERY_CAPTURE_ROOT"])),
            recovery_capture_key_version=os.environ.get("DAON_RECOVERY_CAPTURE_KEY_VERSION"),
            recovery_capture_key_reference=os.environ.get("DAON_RECOVERY_CAPTURE_KEY_REFERENCE"),
            recovery_restore_config_file=(None if os.environ.get("DAON_RECOVERY_RESTORE_CONFIG_FILE") is None
                else Path(os.environ["DAON_RECOVERY_RESTORE_CONFIG_FILE"])),
            object_storage_secure=os.environ.get("DAON_OBJECT_STORAGE_SECURE", "true").lower() == "true",
            object_storage_provision_bucket=(
                os.environ.get("DAON_OBJECT_STORAGE_PROVISION_BUCKET", "false").lower() == "true"
            ),
            step_up_token_key_file=(
                None if os.environ.get("DAON_STEP_UP_TOKEN_KEY_FILE") is None
                else Path(os.environ["DAON_STEP_UP_TOKEN_KEY_FILE"])
            ),
            provider_credential_key_file=(
                None if os.environ.get("DAON_PROVIDER_CREDENTIAL_KEY_FILE") is None
                else Path(os.environ["DAON_PROVIDER_CREDENTIAL_KEY_FILE"])
            ),
            license_public_keys_file=(
                None if os.environ.get("DAON_LICENSE_PUBLIC_KEYS_FILE") is None
                else Path(os.environ["DAON_LICENSE_PUBLIC_KEYS_FILE"])
            ),
            policy_version=os.environ.get("DAON_POLICY_VERSION", "runtime-policy-v1"),
            public_gateway_url=os.environ.get("DAON_PUBLIC_GATEWAY_URL"),
            android_auth_clients=android_clients,
            qa_structured_ollama_workspace_id=os.environ.get("DAON_QA_STRUCTURED_OLLAMA_WORKSPACE_ID", ""),
            allow_private_http_gateway=(
                os.environ.get("DAON_ALLOW_PRIVATE_HTTP_GATEWAY", "false").lower() == "true"
            ),
            trusted_proxy_ips=proxies,
            source_upload_max_bytes=int(
                os.environ.get("DAON_SOURCE_UPLOAD_MAX_BYTES", str(25 * 1024 * 1024))
            ),
            dev_auth_bypass=os.environ.get("DAON_DEV_AUTH_BYPASS", "false").lower() == "true",
            development_stage=(
                None
                if os.environ.get("DAON_DEVELOPMENT_STAGE") is None
                else os.environ.get("DAON_DEVELOPMENT_STAGE", "false").lower() == "true"
            ),
            system_admin_user_ids=frozenset(
                {"admin"}
                | {
                    value.strip()
                    for value in os.environ.get("DAON_SYSTEM_ADMIN_USER_IDS", "").split(",")
                    if value.strip()
                }
            ),
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


def _build_document_readiness_runtime(cloud_store, settings):
    configured = settings.document_readiness_settings
    if cloud_store is None or not configured.scopes:
        return None
    status = cloud_store.readiness()
    if status.ready is not True or status.schema_revision != "0072":
        raise ValueError("DOCUMENT_READINESS_SCHEMA_REQUIRED")
    from .document_readiness_postgres import PostgresDocumentReadinessRepository
    from .workspace_model_defaults import read_document_readiness_policy
    return PostgresDocumentReadinessRepository(cloud_store, settings=configured,
        current_policy_reader=lambda connection, context, wait, grant:
            read_document_readiness_policy(connection, context, wait, settings=configured))


def _document_readiness_consumer(repository):
    if repository is None:
        return None
    return lambda: repository.dispatch_ready(max_items=32, max_seconds=2)


def _document_readiness_observer(repository):
    if repository is None:
        return None
    from .document_readiness import ValidatedReadinessObservation
    from .workspace_model_defaults import read_readiness_catalog
    def observe(probe):
        if (type(probe) is not dict or set(probe) != {"event_id", "connection_id", "adapter_type",
                "configuration_version", "observed_at", "probe_kind", "capability", "status"}
                or not ((probe["probe_kind"] == "provider_connection_check" and probe["capability"] is None
                         and probe["status"] in {"healthy", "unavailable"})
                    or (probe["probe_kind"] == "native_asr_ready" and probe["capability"] == "speech_to_text"
                        and probe["adapter_type"] == "asr_base_native" and probe["status"] == "healthy"))):
            raise DocumentUnderstandingError("DOCUMENT_PROCESSING_CONTEXT_INVALID")
        # 타입·UTC·safe ID를 공통 닫힌 타입으로 먼저 확인해 잘못된 값의 DB 접근을 막는다.
        ValidatedReadinessObservation(1, probe["event_id"], probe["connection_id"], probe["adapter_type"],
            probe["capability"], "0" * 64, probe["configuration_version"], probe["observed_at"], probe["observed_at"],
            probe["probe_kind"], probe["status"], 1, "0" * 64)
        with repository._global_transaction() as connection:
            current = connection.execute(
                "SELECT /* readiness-probe-current */ version,adapter_type,enabled,access_mode,verification_status "
                "FROM public.system_provider_connections WHERE connection_id=%s", (probe["connection_id"],)).fetchone()
            expected_status = "verified" if probe["status"] == "healthy" else "failed"
            if (current is None or (current[0], current[1], current[2], current[3], current[4]) !=
                    (probe["configuration_version"], probe["adapter_type"], True, "public", expected_status)):
                raise DocumentUnderstandingError("SOURCE_VERSION_CONFLICT", status=409)
            catalog = read_readiness_catalog(connection, probe["connection_id"])
            if probe["probe_kind"] == "native_asr_ready":
                model = next((item for item in json.loads(catalog.canonical_json)["models"]
                              if item["model_id"] == "faster-whisper-base"), None)
                if (model is None or model["allowed"] is not True or model["catalog_status"] != "ready"
                        or model["effective_capabilities"] != ["speech_to_text"]):
                    raise DocumentUnderstandingError("SOURCE_VERSION_CONFLICT", status=409)
        payload = {**probe, "schema_version": 1, "model_catalog_digest": catalog.digest_sha256,
                   "observed_at": probe["observed_at"].isoformat()}
        digest = hashlib.sha256(canonical_json_bytes(payload)).hexdigest()
        # received_at/sequence는 입력 구성용이며 R2 저장 port가 버리고 DB가 실제값을 배정한다.
        observation = ValidatedReadinessObservation(1, probe["event_id"], probe["connection_id"], probe["adapter_type"],
            probe["capability"], catalog.digest_sha256, probe["configuration_version"], probe["observed_at"], probe["observed_at"],
            probe["probe_kind"], probe["status"], 1, digest)
        return repository.record_observation(observation)
    return observe


@dataclass(slots=True)
class RuntimeDependencies:
    settings: RuntimeSettings
    identity_service: IdentityService
    authorization_service: AuthorizationService
    audit_store: AuditEventStore
    identity_repository: SqliteIdentityRepository
    authorization_repository: SqliteAuthorizationRepository
    admin_user_service: AdminUserService | None = None
    admin_membership_role_service: AdminMembershipRoleService | None = None
    notification_service: NotificationService | None = None
    cloud_store: PostgresCloudStore | None = None
    object_storage: ObjectStoragePort | None = None
    sync_service: SyncService | PostgresSyncService | None = None
    knowledge_package_service: KnowledgePackageService | PostgresKnowledgePackageService | None = None
    retention_service: RetentionService | None = None
    recovery_service: RecoveryService | PostgresRecoveryService | UnavailableRecoveryService | None = None
    object_queue_store: PostgresObjectQueueStore | None = None
    provider_settings_service: ProviderSettingsService | None = None
    provider_connection_service: Any | None = None
    provider_health_settings_service: PostgresProviderHealthCheckSettingsService | None = None
    workspace_model_defaults_service: Any | None = None
    operations_status_service: OperationsStatusService | None = None
    output_version_settings_service: OutputVersionSettingsService | None = None
    screen_preference_service: ScreenPreferenceService | None = None
    license_service: LicenseService | None = None
    notebook_service: NotebookService | None = None
    notebook_deletion_store: NotebookDeletionStore | None = None
    notebook_deletion_worker: NotebookDeletionWorker | None = None
    source_upload_service: SourceUploadPort | None = None
    website_import_service: Any | None = None
    website_repository: Any | None = None
    website_storage: WebsiteProcessStorage | None = None
    open_law_client: Any | None = None
    open_law_import_service: Any | None = None
    open_law_repository: Any | None = None
    document_processing_service: DocumentProcessingSubmissionService | None = None
    document_readiness_repository: Any | None = None
    question_answering_service: Any | None = None
    citation_content_repository: Any | None = None
    studio_report_service: Any | None = None
    studio_report_repository: Any | None = None
    studio_workspace_service: Any | None = None
    studio_approval_lifecycle_repository: Any | None = None
    organization_repository: SqliteOrganizationRepository | None = None
    egress_policy_service: EgressPolicyService | None = None
    connector_registry: ConnectorRegistry | None = None
    connector_repository: PostgresConnectorRepository | None = None
    state: RuntimeState = field(default_factory=RuntimeState)
    _closed: bool = field(default=False, init=False, repr=False)

    def close(self) -> None:
        if self._closed:
            return
        self.state.begin_shutdown()
        self.state.drain(self.settings.drain_timeout_seconds)
        self.authorization_repository.close()
        self.identity_repository.close()
        if self.organization_repository is not None:
            self.organization_repository.close()
        if self.cloud_store is not None:
            self.cloud_store.close()
        if self.object_queue_store is not None:
            self.object_queue_store.close()
        source_upload_close = (
            None if self.source_upload_service is None else getattr(self.source_upload_service, "close", None)
        )
        if callable(source_upload_close):
            source_upload_close()
        if self.website_repository is not None:
            close = getattr(self.website_repository, "close", None)
            if callable(close):
                close()
        if self.open_law_repository is not None:
            close=getattr(self.open_law_repository,'close',None)
            if callable(close):close()
        recovery_close = (
            None if self.recovery_service is None else getattr(self.recovery_service, "close", None)
        )
        if callable(recovery_close):
            recovery_close()
        self._closed = True


class AuthorizationEvaluationBody(BaseModel):
    model_config = ConfigDict(extra="forbid")
    action: Action
    requested_permissions: list[Permission]


class SourceReprocessingAttributes(BaseModel):
    model_config = ConfigDict(extra="forbid")
    workspace_id: str = Field(min_length=1, max_length=128, pattern=r"^[A-Za-z0-9][A-Za-z0-9._:-]*$")
    notebook_id: str = Field(min_length=1, max_length=128, pattern=r"^[A-Za-z0-9][A-Za-z0-9._:-]*$")
    source_version_id: str = Field(min_length=1, max_length=128, pattern=r"^[A-Za-z0-9][A-Za-z0-9._:-]*$")
    processing_mode: Literal["auto", "local_only", "pinned"] | None = None

    @model_validator(mode="before")
    @classmethod
    def explicit_mode_must_be_known(cls, value):
        if isinstance(value, dict) and "processing_mode" in value and (
                type(value["processing_mode"]) is not str
                or value["processing_mode"] not in ("auto", "local_only", "pinned")):
            raise ValueError("DOCUMENT_PROCESSING_CONTEXT_INVALID")
        return value


class SourceReprocessingBody(BaseModel):
    model_config = ConfigDict(extra="forbid")
    kind: str
    attributes: SourceReprocessingAttributes
    step_up_authorization_id: str | None = None


class EgressPolicyVersionBody(BaseModel):
    model_config = ConfigDict(extra="forbid")
    mode: str
    allowed_provider_kinds: list[str] = Field(max_length=32)
    allowed_destinations: list[str] = Field(max_length=64)
    classification: str
    max_bytes: int = Field(ge=0, le=104_857_600)
    masking_required: bool
    redaction_required: bool
    required_approver: str
    step_up_authorization_id: str = Field(min_length=1, max_length=4096)


class SignupBody(BaseModel):
    model_config = ConfigDict(extra="forbid")
    login_id: str
    email: str
    password: str = Field(min_length=12, max_length=1024)


class LoginBody(BaseModel):
    model_config = ConfigDict(extra="forbid")
    login_id: str
    password: str


class NativeLocalLoginBody(BaseModel):
    model_config = ConfigDict(extra="forbid")
    login_id: str
    password: str


class NativeRefreshBody(BaseModel):
    model_config = ConfigDict(extra="forbid")
    refresh_credential: str


class AndroidTransactionBody(BaseModel):
    model_config = ConfigDict(extra="forbid")
    client_id: str
    redirect_uri: str
    state: str
    code_challenge: str
    code_challenge_method: str


class AndroidAuthorizeBody(BaseModel):
    model_config = ConfigDict(extra="forbid")
    transaction_id: str


class AndroidExchangeBody(BaseModel):
    model_config = ConfigDict(extra="forbid")
    transaction_id: str
    code: str
    code_verifier: str
    redirect_uri: str


class TokenBody(BaseModel):
    model_config = ConfigDict(extra="forbid")
    token: str


class IdentifierBody(BaseModel):
    model_config = ConfigDict(extra="forbid")
    identifier: str


class PasswordResetConfirmBody(BaseModel):
    model_config = ConfigDict(extra="forbid")
    token: str
    new_password: str


class PasswordChangeBody(BaseModel):
    model_config = ConfigDict(extra="forbid")
    current_password: str
    new_password: str


class AdminUserStateBody(BaseModel):
    model_config = ConfigDict(extra="forbid")
    state: str


class AdminUserCreateBody(BaseModel):
    model_config = ConfigDict(extra="forbid")
    login_id: str = Field(min_length=1, max_length=255)
    email: str = Field(min_length=3, max_length=320)
    initial_password: str = Field(min_length=12, max_length=256, repr=False)


class AdminUserUpdateBody(BaseModel):
    model_config = ConfigDict(extra="forbid")
    email: str = Field(min_length=3, max_length=320)


class UserProviderCredentialBody(BaseModel):
    model_config = ConfigDict(extra="forbid")
    credential: str = Field(min_length=1, max_length=16384, repr=False)
    expected_version: int = Field(ge=0)


class UserProviderCredentialDeleteBody(BaseModel):
    model_config = ConfigDict(extra="forbid")
    expected_version: int = Field(ge=0)


class AccessDecisionBody(BaseModel):
    model_config = ConfigDict(extra="forbid")
    resource_id: str
    action: AccessAction


class StepUpBody(BaseModel):
    model_config = ConfigDict(extra="forbid")
    action_group: str
    target_id: str
    password: str
    ttl_seconds: int = 300


class AccountProfileBody(BaseModel):
    model_config = ConfigDict(extra="forbid")
    display_name: str = Field(strict=True, min_length=1)
    expected_version: int = Field(strict=True, ge=0, le=9007199254740990)


class AccountTrustBody(BaseModel):
    model_config = ConfigDict(extra="forbid")


class AccountDeviceRevokeBody(BaseModel):
    model_config = ConfigDict(extra="forbid")
    step_up_authorization: str = Field(min_length=40, max_length=512)


class AccountSessionRevokeBody(AccountDeviceRevokeBody):
    session_id: str = Field(min_length=1, max_length=128, pattern=r"^[A-Za-z0-9][A-Za-z0-9._:-]{0,127}$")


class NotificationReadBody(BaseModel):
    model_config = ConfigDict(extra="forbid")
    state: str


class QuestionEvidenceResourceBody(BaseModel):
    model_config = ConfigDict(extra="forbid")
    resource_kind: str = Field(min_length=1, max_length=64, pattern=r"^[a-z][a-z0-9_]{0,63}$")
    resource_id: str
    version_id: str | None = None


class QuestionKnowledgeContextBody(BaseModel):
    model_config = ConfigDict(extra="forbid")
    mode: str
    resources: list[QuestionEvidenceResourceBody] = Field(min_length=1, max_length=8)

    @model_validator(mode="after")
    def validate_mode(self) -> "QuestionKnowledgeContextBody":
        identities = {(item.resource_kind, item.resource_id, item.version_id) for item in self.resources}
        if len(identities) != len(self.resources):
            raise ValueError("QUESTION_CONTEXT_INVALID")
        knowledge = [item for item in self.resources if item.resource_kind == "knowledge_package"]
        non_knowledge = [item for item in self.resources if item.resource_kind != "knowledge_package"]
        valid = (
            self.mode == "raw_only" and bool(non_knowledge) and not knowledge
            or self.mode == "daon_priority" and bool(knowledge)
            or self.mode == "mixed" and bool(non_knowledge) and bool(knowledge)
        )
        if not valid:
            raise ValueError("QUESTION_CONTEXT_INVALID")
        return self


class QuestionBody(BaseModel):
    model_config = ConfigDict(extra="forbid")
    notebook_id: str = Field(min_length=1, max_length=128, pattern=r"^[A-Za-z0-9][A-Za-z0-9._:-]{0,127}$")
    source_id: str | None = None
    source_version_id: str | None = None
    knowledge_context: QuestionKnowledgeContextBody | None = None
    question: str
    step_up_authorization_id: str | None = Field(default=None, min_length=1, max_length=4096)

    @model_validator(mode="after")
    def validate_source_contract(self) -> "QuestionBody":
        legacy = self.source_id is not None or self.source_version_id is not None
        if legacy != (self.source_id is not None and self.source_version_id is not None):
            raise ValueError("QUESTION_CONTEXT_INVALID")
        if legacy and self.knowledge_context is not None:
            raise ValueError("QUESTION_CONTEXT_INVALID")
        return self


class QuestionAuthorizationBody(BaseModel):
    model_config = ConfigDict(extra="forbid")
    notebook_id: str = Field(min_length=1, max_length=128, pattern=r"^[A-Za-z0-9][A-Za-z0-9._:-]{0,127}$")
    source_id: str | None = None
    source_version_id: str | None = None
    knowledge_context: QuestionKnowledgeContextBody | None = None
    question: str
    # Development auth bypass does not require a password.  Production still
    # enforces re-authentication immediately before issuing step-up below.
    password: str | None = None

    @model_validator(mode="after")
    def validate_source_contract(self) -> "QuestionAuthorizationBody":
        legacy = self.source_id is not None or self.source_version_id is not None
        if legacy != (self.source_id is not None and self.source_version_id is not None):
            raise ValueError("QUESTION_CONTEXT_INVALID")
        if legacy and self.knowledge_context is not None:
            raise ValueError("QUESTION_CONTEXT_INVALID")
        return self


class StudioReportBody(BaseModel):
    model_config = ConfigDict(extra="forbid")
    notebook_id: str
    source_id: str
    source_version_id: str
    run_id: str
    run_result_id: str
    title: str
    purpose: str


class StudioSettingsBody(BaseModel):
    model_config = ConfigDict(extra="forbid")
    purpose: str
    audience: str
    source_version_ids: list[str]
    ruleset_version_id: str | None = None
    length: str
    structure: str
    output_format: str
    review_condition: str
    language: str = "ko"


class StudioGenerationBody(BaseModel):
    model_config = ConfigDict(extra="forbid")
    workspace_id: str
    notebook_id: str
    output_type: str
    source_id: str | None = None
    source_version_ids: list[str]
    run_id: str | None = None
    run_result_id: str | None = None
    source_only: bool = False
    settings: StudioSettingsBody


class StudioRevisionBody(BaseModel):
    model_config = ConfigDict(extra="forbid")
    workspace_id: str
    notebook_id: str
    previous_version_id: str
    revision_type: str
    change_reason: str
    content: str
    settings: StudioSettingsBody | None = None


class StudioActionBody(BaseModel):
    model_config = ConfigDict(extra="forbid")
    workspace_id: str
    notebook_id: str
    output_version_id: str
    step_up_authorization: str | None = None
    review_request_id: str | None = None
    approval_request_id: str | None = None
    approval_id: str | None = None
    decision: str | None = None
    recipient: str | None = None
    explicit: bool | None = None


class SyncItemBody(BaseModel):
    model_config = ConfigDict(extra="forbid")
    item_id: str
    source_version_id: str | None = None
    local_object_id: str
    digest_sha256: str
    byte_size: int
    content_type: str
    base_cloud_version_id: str | None = None
    base_cloud_digest: str | None = None
    item_kind: str = "source_version"
    output_version_id: str | None = None
    dependency_item_ids: list[str] = Field(default_factory=list)


class KnowledgeCopyBody(BaseModel):
    model_config = ConfigDict(extra="forbid")
    device_id: str
    step_up_authorization_id: str


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
    notebook_id: str = Field(min_length=1, max_length=128, pattern=r"^[A-Za-z0-9][A-Za-z0-9._:-]{0,127}$")
    step_up_authorization_id: str = Field(min_length=1, max_length=4096)


class SensitiveRetentionBody(BaseModel):
    model_config = ConfigDict(extra="forbid")
    step_up_authorization_id: str


class BackupObjectBody(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    object_id: str = Field(pattern=r"^[A-Za-z0-9][A-Za-z0-9._:-]{0,127}$")
    checksum_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    byte_size: int = Field(ge=0)


class LegacyBackupCreateBody(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    workspace_id: str = Field(pattern=r"^[A-Za-z0-9][A-Za-z0-9._:-]{0,127}$")
    trigger: Literal["automatic", "manual"]
    schema_revision: str = Field(pattern=r"^[A-Za-z0-9][A-Za-z0-9._:-]{0,127}$")
    retention_watermark: str = Field(pattern=r"^[A-Za-z0-9][A-Za-z0-9._:-]{0,127}$")
    objects: list[BackupObjectBody] = Field(min_length=1)


class SelectionBackupCreateBody(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    workspace_id: str = Field(pattern=r"^[A-Za-z0-9][A-Za-z0-9._:-]{0,127}$")
    notebook_id: str = Field(pattern=r"^[A-Za-z0-9][A-Za-z0-9._:-]{0,127}$")
    trigger: Literal["manual"]
    source_version_ids: list[Annotated[str, Field(pattern=r"^[A-Za-z0-9][A-Za-z0-9._:-]{0,127}$")]] = Field(
        min_length=1, max_length=100, json_schema_extra={"uniqueItems": True})

    @model_validator(mode="after")
    def unique_selection(self) -> SelectionBackupCreateBody:
        if len(set(self.source_version_ids)) != len(self.source_version_ids):
            raise ValueError("RECOVERY_INPUT_INVALID")
        return self


class LineageSelectionPreviewBody(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    workspace_id: str = Field(pattern=r"^[A-Za-z0-9][A-Za-z0-9._:-]{0,127}$")
    notebook_id: str = Field(pattern=r"^[A-Za-z0-9][A-Za-z0-9._:-]{0,127}$")
    source_version_ids: list[Annotated[str, Field(pattern=r"^[A-Za-z0-9][A-Za-z0-9._:-]{0,127}$")]] = Field(
        min_length=1, max_length=100, json_schema_extra={"uniqueItems": True})
    lineage_mode: Literal["exact"]

    @model_validator(mode="after")
    def unique_selection(self):
        if len(set(self.source_version_ids)) != len(self.source_version_ids): raise ValueError("RECOVERY_INPUT_INVALID")
        return self


class LineageClosureConsentBody(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    include_prior_versions: Literal[True]
    closure_fingerprint: str = Field(pattern=r"^[0-9a-f]{64}$")

    @model_validator(mode="before")
    @classmethod
    def explicit_true(cls, value):
        if not isinstance(value, dict) or value.get("include_prior_versions") is not True: raise ValueError("RECOVERY_INPUT_INVALID")
        return value


class LineageBackupCreateBody(LineageSelectionPreviewBody):
    trigger: Literal["manual"]
    closure_consent: LineageClosureConsentBody


def _backup_one_of(schema: dict[str, Any]) -> None:
    schema["oneOf"] = schema.pop("anyOf")


class BackupCreateBody(RootModel[LegacyBackupCreateBody | SelectionBackupCreateBody | LineageBackupCreateBody]):
    model_config = ConfigDict(json_schema_extra=_backup_one_of)


class RestoreDestinationBody(BaseModel):
    model_config = ConfigDict(extra="forbid")
    tenant_id: str
    workspace_id: str
    database_id: str
    bucket_id: str


class LegacyRestorePreviewBody(BaseModel):
    model_config = ConfigDict(extra="forbid")
    destination: RestoreDestinationBody
    step_up_authorization_id: str


class RegisteredRestorePreviewBody(BaseModel):
    model_config = ConfigDict(extra="forbid",strict=True)
    workspace_id: str = Field(pattern=r"^[A-Za-z0-9][A-Za-z0-9._:-]{0,127}$")
    destination_id: str = Field(pattern=r"^[A-Za-z0-9][A-Za-z0-9._:-]{0,127}$")
    destination_version: int = Field(gt=0,le=2147483647)
    step_up_authorization_id: str = Field(min_length=1,max_length=512)


class RestorePreviewBody(RootModel[LegacyRestorePreviewBody | RegisteredRestorePreviewBody]):
    model_config = ConfigDict(json_schema_extra=_backup_one_of)


class RestoreExecuteBody(BaseModel):
    model_config = ConfigDict(extra="forbid")
    preview_version: int
    step_up_authorization_id: str


class ProviderProfileBody(BaseModel):
    model_config = ConfigDict(extra="forbid")
    workspace_id: str
    provider_code: str
    base_url: str
    active: bool
    expected_version: int


class ProviderConnectionCreateBody(BaseModel):
    model_config = ConfigDict(extra="forbid")
    connection_id: str = Field(min_length=1, max_length=128, pattern=r"^[A-Za-z0-9][A-Za-z0-9._:-]{0,127}$")
    provider_code: str = Field(min_length=1, max_length=64, pattern=r"^[A-Z][A-Z0-9_]{0,63}$")
    display_name: str = Field(min_length=1, max_length=256)
    provider_name: str | None = Field(default=None, min_length=1, max_length=256)
    base_url: str = Field(min_length=1, max_length=2048)
    credential: str | None = Field(default=None, min_length=1, max_length=16384, repr=False)
    test_credential: str | None = Field(default=None, min_length=1, max_length=16384, repr=False)
    logical_model_ids: list[str] = Field(default_factory=list, max_length=256)
    allowed_model_ids: list[str] = Field(default_factory=list, max_length=256)
    auto_model_id: str | None = Field(default=None, max_length=256)
    access_mode: str = "public"
    credential_requirement: str = "required"
    short_code: str = ""
    adapter_type: str = ""
    enabled: bool
    expected_version: int = Field(ge=0)
    step_up_authorization_id: str | None = Field(default=None, min_length=1, max_length=512)


class ProviderConnectionUpdateBody(BaseModel):
    model_config = ConfigDict(extra="forbid")
    display_name: str = Field(min_length=1, max_length=256)
    provider_name: str | None = Field(default=None, min_length=1, max_length=256)
    base_url: str = Field(min_length=1, max_length=2048)
    credential: str | None = Field(default=None, min_length=1, max_length=16384, repr=False)
    test_credential: str | None = Field(default=None, min_length=1, max_length=16384, repr=False)
    logical_model_ids: list[str] = Field(default_factory=list, max_length=256)
    allowed_model_ids: list[str] = Field(default_factory=list, max_length=256)
    auto_model_id: str | None = Field(default=None, max_length=256)
    access_mode: str = "public"
    credential_requirement: str = "required"
    short_code: str = ""
    adapter_type: str = ""
    enabled: bool
    expected_version: int = Field(ge=1)
    step_up_authorization_id: str | None = Field(default=None, min_length=1, max_length=512)


class ProviderConnectionModelPreviewBody(BaseModel):
    model_config = ConfigDict(extra="forbid")
    connection_id: str = Field(min_length=1, max_length=128, pattern=r"^[A-Za-z0-9][A-Za-z0-9._:-]{0,127}$")
    provider_code: str = Field(min_length=1, max_length=64)
    adapter_type: str = Field(min_length=1, max_length=64)
    base_url: str = Field(min_length=1, max_length=2048)
    credential: str | None = Field(default=None, min_length=1, max_length=16384, repr=False)
    step_up_authorization_id: str | None = Field(default=None, min_length=1, max_length=512)


class ProviderConnectionMutationBody(BaseModel):
    model_config = ConfigDict(extra="forbid")
    expected_version: int = Field(ge=1)
    step_up_authorization_id: str | None = Field(default=None, min_length=1, max_length=512)


class ProviderHealthSettingsBody(BaseModel):
    model_config = ConfigDict(extra="forbid")
    interval_minutes: int = Field(ge=0, le=1440)
    expected_version: int = Field(ge=0)


class ProviderCredentialReplaceBody(ProviderConnectionMutationBody):
    credential: str = Field(min_length=1, max_length=16384, repr=False)


class ProviderCapabilityBody(ProviderConnectionMutationBody):
    effective_capabilities: list[str] = Field(min_length=1, max_length=12)


class ModelDeploymentBody(BaseModel):
    model_config = ConfigDict(extra="forbid")
    workspace_id: str
    deployment_id: str
    provider_code: str
    model_id: str
    roles: list[str]
    active: bool
    selected: bool
    expected_version: int


class ModelPolicyBody(BaseModel):
    model_config = ConfigDict(extra="forbid")
    bindings: dict[str, str]
    expected_version: int


class WorkspaceModelDefaultBody(BaseModel):
    model_config = ConfigDict(extra="forbid")
    capability: str
    connection_id: str = Field(min_length=1, max_length=256)
    model_id: str = Field(min_length=1, max_length=256)
    expected_version: int = Field(ge=0)


class OutputVersionSettingsBody(BaseModel):
    model_config = ConfigDict(extra="forbid")
    default_formats: dict[str, str]
    expected_version: int


class ScreenPreferenceBody(BaseModel):
    model_config = ConfigDict(extra="forbid")
    theme: str


class LicenseApplyBody(BaseModel):
    model_config = ConfigDict(extra="forbid")
    document: dict[str, object]
    step_up_authorization_id: str = Field(min_length=1, max_length=512)


class NotebookCreateBody(BaseModel):
    model_config = ConfigDict(extra="forbid")
    title: str = Field(min_length=1, max_length=240)
    description: str | None = Field(default=None, max_length=1000)


class NotebookTitleBody(BaseModel):
    model_config = ConfigDict(extra="forbid")
    title: str = Field(min_length=1, max_length=240)


class NotebookDeletionBody(BaseModel):
    model_config = ConfigDict(extra="forbid")
    title_confirmation: str = Field(min_length=1, max_length=240)


class NotebookSourceUnbindBody(BaseModel):
    model_config = ConfigDict(extra="forbid")
    source_id: str = Field(min_length=1, max_length=128)
    source_version_id: str = Field(min_length=1, max_length=128)


class ConnectorRegisterBody(BaseModel):
    model_config = ConfigDict(extra="forbid")
    kind: str = Field(min_length=1, max_length=64)
    name: str = Field(min_length=1, max_length=160)


def _notification_dispatch_tick(
    dependencies: RuntimeDependencies, *, stop: threading.Event,
    after: tuple[str, str, str] | None = None,
    projection_cursors: dict[tuple[str, str, str], str] | None = None,
    limit: int = 100,
) -> tuple[str, str, str] | None:
    """기존 재시도 루프의 제한 배치다. 발견한 실제 scope에서 각 소비기가 현재 권한을 재검사한다.

    cursor는 DB가 반환한 마지막 tuple 그대로다. 빈 page에서만 None으로 wrap하며
    재시작도 None부터 시작한다. 이는 처리 시도 순서이지 전달 성공/60초 완료 보장이 아니다.
    """
    from psycopg import Error, connect
    from psycopg.rows import tuple_row
    from .notification import _checked_id

    if type(limit) is not int or not 1 <= limit <= 100:
        raise NotificationError('INVALID_INPUT', 400)
    if after is not None:
        if type(after) is not tuple or len(after) != 3:
            raise NotificationError('INVALID_INPUT', 400)
        for value in after:
            _checked_id(value)
    if projection_cursors is None:
        projection_cursors = {}
    elif type(projection_cursors) is not dict:
        raise NotificationError('INVALID_INPUT', 400)
    if stop.is_set() or dependencies.settings.notification_inbox_enabled is not True:
        return after
    cloud = dependencies.cloud_store
    if cloud is None or getattr(cloud, '_notification_inbox_enabled', False) is not True:
        return after
    ready = cloud.readiness()  # 0069의 원 조직/알림 보호 probe를 함께 검사한다.
    if ready.ready is not True or ready.schema_revision != '0069':
        raise NotificationError('DATABASE_UNAVAILABLE', 503)
    if stop.is_set():
        return after
    try:
        # 함수만 호출한다. 앱에 금지된 source/audit 직접 조회나 전체 사용자 열거는 없다.
        with connect(cloud._dsn, connect_timeout=3, autocommit=True, row_factory=tuple_row) as connection:
            with connection.transaction():
                connection.execute("SET LOCAL statement_timeout='5s'")
                connection.execute("SET LOCAL lock_timeout='2s'")
                rows = connection.execute(
                    'SELECT * FROM public.pending_notification_dispatch_scopes(%s,%s,%s,%s)',
                    (limit, *(after if after is not None else (None, None, None))),
                ).fetchall()
    except (Error, OSError):
        # 실패를 EOF로 취급하지 않는다. 호출자는 기존 cursor를 보존하고 다음 주기에 재시도한다.
        raise NotificationError('DATABASE_UNAVAILABLE', 503) from None
    if len(rows) > limit or len(set(rows)) != len(rows):
        raise NotificationError('DATABASE_RESPONSE_INVALID', 503)
    contexts = []
    for row in rows:
        if type(row) is not tuple or len(row) != 3:
            raise NotificationError('DATABASE_RESPONSE_INVALID', 503)
        try:
            contexts.append(CloudAccessContext(*row, 'view'))
        except ValueError:
            raise NotificationError('DATABASE_RESPONSE_INVALID', 503) from None
    if stop.is_set():
        return after
    if not contexts:
        return None
    from .notification_events import (
        materialize_pending_studio, materialize_pending_completed, materialize_pending_accounts,
        dispatch_pending_notification_audits,
    )
    from .notification_identity_login import materialize_pending_identity_logins
    from .notification_password import materialize_pending_password_changes
    from .notification_password_reset import materialize_pending_password_resets
    from .notification_organization import materialize_pending_organization_roles
    from .notification_c9_roles import materialize_pending_c9_roles
    from .notification_egress_policy import materialize_pending_workspace_policies
    from .notification_source_access import materialize_pending_source_access
    from .notification_recovery import materialize_pending_restore_completed
    from .notification_studio_approval import StudioApprovalNotificationRepository

    lifecycle = (dependencies.studio_approval_lifecycle_repository
        or StudioApprovalLifecycleRepository(cloud))
    trace_id = 'trace-notification-dispatch-' + secrets.token_hex(16)
    cursor = after
    for scope, context in zip(rows, contexts):
        # 선택된 metadata는 본인 인증이 아니다. 각 실제 소비기의 현재 VIEW/Source 검사를 유지한다.
        studio_context = StudioContext(context.tenant_id, context.workspace_id, context.actor_id,
            trace_id, dependencies.settings.policy_version)
        consumers = (
            ('studio', materialize_pending_studio, studio_context),
            ('completed', materialize_pending_completed, context),
            ('account', materialize_pending_accounts, context),
            ('login', materialize_pending_identity_logins, context),
            ('password', materialize_pending_password_changes, context),
            ('password_reset', materialize_pending_password_resets, context),
            ('organization', materialize_pending_organization_roles, context),
            ('c9', materialize_pending_c9_roles, context),
            ('egress', materialize_pending_workspace_policies, context),
            ('source_access', materialize_pending_source_access, context),
            ('recovery', materialize_pending_restore_completed, context),
        )
        for kind, materialize, current in consumers:
            if stop.is_set():
                return cursor
            try:
                materialize(cloud._dsn, context=current, limit=limit)
            except Exception:
                # 종류별 원 transaction이 rollback한다. 오류 본문/원자료/연결정보를 로그에 넣지 않는다.
                _runtime_logger.warning('알림 소비 배치 실패: %s', kind)
        if stop.is_set():
            return cursor
        try:
            prepared = StudioApprovalNotificationRepository(lifecycle, context=context).prepare_projections(
                limit=limit, after=projection_cursors.get(scope), trace_id=trace_id,
                policy_version=dependencies.settings.policy_version)
            if (type(prepared) is not dict or type(prepared.get('eof')) is not bool
                or type(prepared.get('scanned')) is not int or not 0 <= prepared['scanned'] <= limit
                or (prepared['eof'] and prepared.get('next_cursor') is not None)):
                raise NotificationError('DATABASE_RESPONSE_INVALID', 503)
            if prepared['eof']:
                projection_cursors.pop(scope, None)
            else:
                if prepared['scanned'] == 0:
                    raise NotificationError('DATABASE_RESPONSE_INVALID', 503)
                projection_cursors[scope] = _checked_id(prepared.get('next_cursor'))
        except Exception:
            # 기존 cursor를 유지한다. 현재 준비는 과거 생성/읽음 감사를 소급 합성하지 않는다.
            _runtime_logger.warning('알림 승인 notice 준비 배치 실패')
        if stop.is_set():
            return cursor
        try:
            dispatch_pending_notification_audits(cloud._dsn, context=context,
                audit_store=dependencies.audit_store, limit=limit, stop=stop)
        except Exception:
            _runtime_logger.warning('알림 중앙 감사 전달 배치 실패')
        cursor = scope
    return cursor


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


def _error_response(
    status: int, code: str, trace_id: str, *, retryable: bool = False,
    details: dict[str, object] | None = None,
) -> JSONResponse:
    payload = _error_payload(code=code, trace_id=trace_id, retryable=retryable)
    if details is not None:
        payload["error"]["details"] = details
    response = JSONResponse(
        status_code=status,
        content=payload,
    )
    response.headers["X-Trace-Id"] = trace_id
    response.headers["Cache-Control"] = "no-store"
    return response


def _domain_error(error: IdentityError | AuthorizationError) -> tuple[int, str, bool]:
    status = error.http_status
    if status == 401 and error.code == "REFRESH_REPLAYED":
        return 401, "REFRESH_REPLAYED", False
    if status == 401:
        return 401, "AUTHENTICATION_REQUIRED", False
    if status == 404:
        return 404, "RESOURCE_UNAVAILABLE", False
    if status == 403 and error.code == "ACTION_DENIED":
        return 403, "FORBIDDEN", False
    safe_special = {
        "CURRENT_ACCESS_DENIED", "STEP_UP_REQUIRED", "VERSION_CONFLICT",
        "EMAIL_DELIVERY_UNAVAILABLE", "CSRF_VALIDATION_FAILED",
        "PASSWORD_CHANGE_REQUIRED", "FORBIDDEN", "PROTECTED_ADMIN_ACCOUNT",
        "IDEMPOTENCY_KEY_REUSED", "IDEMPOTENCY_KEY_INVALID", "INVALID_USER_STATE",
        "USER_EMAIL_REQUIRED", "EMAIL_VERIFICATION_REQUIRED",
        "ACCOUNT_VERSION_CONFLICT", "ACCOUNT_INPUT_INVALID", "ACCOUNT_CURSOR_INVALID", "ACCOUNT_STORAGE_UNAVAILABLE", "ACCOUNT_SESSION_CHANGED",
        "ORGANIZATION_MEMBERSHIP_SUSPENDED",
    }
    return status, error.code if error.code in safe_special else "INVALID_REQUEST", status >= 500


def _notification_domain_error(error: NotificationError) -> tuple[int, str, bool]:
    if error.http_status >= 500:
        return 503, "RESOURCE_UNAVAILABLE", True
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
    "BACKUP_CAPTURE_CLEANUP_UNCERTAIN": "RESOURCE_UNAVAILABLE",
    "CURRENT_ACCESS_DENIED": "CURRENT_ACCESS_DENIED",
    "STEP_UP_REQUIRED": "STEP_UP_REQUIRED",
    "BACKUP_UNAVAILABLE": "RESOURCE_UNAVAILABLE",
    "RESTORE_REQUEST_UNAVAILABLE": "RESOURCE_UNAVAILABLE",
    "RESOURCE_UNAVAILABLE": "RESOURCE_UNAVAILABLE",
    "FIXTURE_ONLY_RESTORE_REQUIRED": "CURRENT_ACCESS_DENIED",
    "IN_PLACE_RESTORE_FORBIDDEN": "CURRENT_ACCESS_DENIED",
}


class _LineageRecheckAuthError(RecoveryError):
    """FileStore의 안전 정리까지만 RecoveryError로 운반하고 원 인증 handler로 돌려준다."""
    def __init__(self, original: IdentityError | AuthorizationError):
        super().__init__("CURRENT_ACCESS_DENIED", original.http_status)
        self.original = original


def _recovery_public_error_code(code: str) -> str:
    return _RECOVERY_PUBLIC_CODES.get(code, "INVALID_REQUEST")


def _require_query_keys(request: Request, allowed: frozenset[str]) -> None:
    if any(key not in allowed for key in request.query_params):
        raise HTTPException(status_code=400)


def _etag_header(etag_seed: str) -> str:
    digest = hashlib.sha256(etag_seed.encode("utf-8")).hexdigest()[:24]
    return f'"projection-{digest}"'


def _json_with_etag(content: dict[str, object], etag_seed: str) -> JSONResponse:
    response = JSONResponse(content=content)
    response.headers["ETag"] = _etag_header(etag_seed)
    return response


def _enum_json(value: object) -> object:
    if isinstance(value, Enum):
        return value.value
    if isinstance(value, datetime):
        # Web DTO timestamps use the canonical UTC `Z` form.  In particular,
        # notebook context deletion views carry `grace_until`; emitting
        # `+00:00` made the browser's strict context contract reject an
        # otherwise valid notebook and surface NOTEBOOK_CONTEXT_INVALID.
        return value.isoformat().replace("+00:00", "Z")
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


def _require_validated_web_csrf(request: Request, settings: RuntimeSettings) -> None:
    if request.headers.get("x-daon-bff-transport") != "internal":
        raise IdentityError("CSRF_VALIDATION_FAILED", 403)
    origin = request.headers.get("x-daon-csrf-origin")
    referer = request.headers.get("x-daon-csrf-referer")
    if not origin or not referer:
        raise IdentityError("CSRF_VALIDATION_FAILED", 403)
    try:
        parsed_origin = urlsplit(origin)
        parsed_referer = urlsplit(referer)
    except ValueError as error:
        raise IdentityError("CSRF_VALIDATION_FAILED", 403) from error
    valid_transport = parsed_origin.scheme == "https" or (
        settings.allow_private_http_gateway and _is_private_http_gateway(parsed_origin)
    )
    if (
        not valid_transport or not parsed_origin.netloc
        or parsed_origin.path not in {"", "/"} or parsed_origin.query or parsed_origin.fragment
        or (parsed_referer.scheme, parsed_referer.netloc)
        != (parsed_origin.scheme, parsed_origin.netloc)
    ):
        raise IdentityError("CSRF_VALIDATION_FAILED", 403)
    if settings.public_gateway_url is not None:
        expected = urlsplit(settings.public_gateway_url)
        if (parsed_origin.scheme, parsed_origin.netloc) != (expected.scheme, expected.netloc):
            raise IdentityError("CSRF_VALIDATION_FAILED", 403)


def _idempotency_key(value: str) -> str:
    if not _TRACE_ID.fullmatch(value):
        raise HTTPException(status_code=400)
    return value


def _studio_idempotency_key(value: str) -> str:
    if len(value) < 16 or len(value) > 128 or not _TRACE_ID.fullmatch(value):
        raise HTTPException(status_code=400)
    return value


def _egress_idempotency_key(value: str) -> str:
    if len(value) < 16 or len(value) > 128 or not _TRACE_ID.fullmatch(value):
        raise HTTPException(status_code=400)
    return value


def _personal_workspace_id(tenant_id: str) -> str:
    return f"workspace-{hashlib.sha256(tenant_id.encode('utf-8')).hexdigest()[:24]}"


def _ensure_personal_workspace(
    dependencies: RuntimeDependencies, *, tenant_id: str, user_id: str,
) -> str:
    workspace_id = dependencies.authorization_repository.primary_workspace_id(
        tenant_id
    ) or _personal_workspace_id(tenant_id)
    dependencies.authorization_repository.bootstrap_workspace(
        tenant_id=tenant_id,
        workspace_id=workspace_id,
        owner_user_id=user_id,
        owner_role=Role.PERSONAL_OWNER,
        workspace_kind="personal",
        data_area="cloud_sync",
        cost_limit_cents=1000,
        now=datetime.now(timezone.utc),
    )
    if dependencies.cloud_store is not None:
        dependencies.cloud_store.seed_scope(
            CloudAccessContext(
                tenant_id, workspace_id, user_id, "workspace.bootstrap",
            )
        )
    return workspace_id


def _ensure_development_cloud_scope(
    dependencies: RuntimeDependencies, *, principal: IdentityPrincipal, workspace_id: str,
) -> None:
    if dependencies.cloud_store is not None:
        seed_scope = getattr(
            dependencies.cloud_store,
            "seed_development_scope",
            dependencies.cloud_store.seed_scope,
        )
        seed_scope(
            CloudAccessContext(
                principal.tenant_id, workspace_id, principal.user_id, "workspace.bootstrap",
            )
        )


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


def _knowledge_package_context(
    principal: IdentityPrincipal,
    workspace_id: str,
    request: Request,
    dependencies: RuntimeDependencies,
) -> KnowledgePackageContext:
    return KnowledgePackageContext(
        principal.tenant_id, workspace_id, principal.user_id,
        request.state.trace_id, dependencies.settings.policy_version,
        principal.device_id,
    )


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
        session_id=principal.session_id, device_id=principal.device_id,
    )


def _provider_settings_context(
    principal: IdentityPrincipal, workspace_id: str, request: Request,
    dependencies: RuntimeDependencies,
) -> ProviderSettingsContext:
    return ProviderSettingsContext(
        principal.tenant_id, workspace_id, principal.user_id, request.state.trace_id,
        dependencies.settings.policy_version,
    )


def _operations_workspace_scope(principal,workspace_id,dependencies):
    """보안 판단을 대신하지 않는 optional 관측. 실제 현재 VIEW가 확인된 Workspace만 기록한다."""
    from .operations_audit_scope import VerifiedOperationsScope
    try:
        value=VerifiedOperationsScope.for_workspace(principal.tenant_id,principal.user_id,
            principal.session_id,principal.device_id,workspace_id)
        with dependencies.authorization_service._repository.transaction() as connection:
            workspace=connection.execute("SELECT * FROM auth_workspaces WHERE tenant_id=? AND workspace_id=?",
                (principal.tenant_id,workspace_id)).fetchone()
            if workspace is None:return None
            dependencies.authorization_service._assert_organization_active(connection,principal,workspace)
            binding=dependencies.authorization_service._role_binding(connection,principal,workspace)
            if dependencies.authorization_service._action_grant(connection,principal=principal,workspace=workspace,
                    binding=binding,action=Action.VIEW,requested_permissions=()) is None:return None
        return value
    except Exception:
        _runtime_logger.warning("OPERATIONS_SCOPE_UNAVAILABLE")
        return None


def _egress_policy_context(
    principal: IdentityPrincipal, workspace_id: str, request: Request,
    dependencies: RuntimeDependencies,
) -> EgressPolicyContext:
    return EgressPolicyContext(
        principal.tenant_id, principal.tenant_id, workspace_id, principal.user_id,
        request.state.trace_id, dependencies.settings.policy_version,
    )


def _studio_context(
    principal: IdentityPrincipal, workspace_id: str, request: Request,
    dependencies: RuntimeDependencies, notebook_id: str | None = None,
) -> StudioReportContext:
    return StudioReportContext(
        principal.tenant_id, workspace_id, principal.user_id, request.state.trace_id,
        dependencies.settings.policy_version, notebook_id,
    )


def _product_studio_context(
    principal: IdentityPrincipal, workspace_id: str, request: Request,
    dependencies: RuntimeDependencies, notebook_id: str | None = None,
) -> StudioContext:
    return StudioContext(
        principal.tenant_id, workspace_id, principal.user_id, request.state.trace_id,
        dependencies.settings.policy_version,
        notebook_id,
    )


def _license_context(
    principal: IdentityPrincipal, workspace_id: str, request: Request,
    dependencies: RuntimeDependencies,
) -> LicenseContext:
    return LicenseContext(
        principal.tenant_id, workspace_id, principal.user_id,
        request.state.trace_id, dependencies.settings.policy_version,
    )


def _principal(request: Request, dependencies: RuntimeDependencies) -> IdentityPrincipal:
    if dependencies.settings.dev_auth_bypass and dependencies.settings.profile in {"test", "development"}:
        principal = IdentityPrincipal(
            user_id="dev-user", session_id="dev-session", device_id="dev-device", tenant_id="dev-tenant",
        )
        workspace_match = re.search(r"/workspaces/([A-Za-z0-9][A-Za-z0-9._:-]{0,127})", request.url.path)
        if workspace_match:
            dependencies.authorization_repository.bootstrap_workspace(
                tenant_id=principal.tenant_id,
                workspace_id=workspace_match.group(1),
                owner_user_id=principal.user_id,
                owner_role=Role.PERSONAL_OWNER,
                workspace_kind="personal",
                data_area="cloud_sync",
                cost_limit_cents=1000,
                now=datetime.now(timezone.utc),
            )
            _ensure_development_cloud_scope(
                dependencies, principal=principal, workspace_id=workspace_match.group(1),
            )
        return principal
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


class _UnavailableKnowledgePackageContentPort:
    def read_package(self, _context: object, _package: object) -> bytes:
        raise KnowledgePackageError("KNOWLEDGE_PACKAGE_CONTENT_UNAVAILABLE", 503)


def _resolve_knowledge_package_service(
    configured: KnowledgePackageService | PostgresKnowledgePackageService | None,
    cloud_store: PostgresCloudStore | None,
) -> KnowledgePackageService | PostgresKnowledgePackageService:
    if configured is not None:
        return configured
    if cloud_store is not None:
        return PostgresKnowledgePackageService(
            cloud_store,
            _UnavailableKnowledgePackageContentPort(),
            clock=lambda: datetime.now(timezone.utc),
        )
    return KnowledgePackageService(
        ReferenceKnowledgePackageRepository(),
        clock=lambda: datetime.now(timezone.utc),
    )


def _license_enforcement_enabled(settings: RuntimeSettings, *, configured_service: bool) -> bool:
    """Enable license gates outside development stage or for an injected test service."""
    development_stage = (
        settings.development_stage
        if settings.development_stage is not None
        else settings.profile in {"test", "development"}
    )
    return configured_service or not development_stage


def _requires_runtime_license_precheck(service: object) -> bool:
    if type(service) is StudioWorkspaceService:
        repository = getattr(service, "_repository", None)
        return not (
            type(repository) is PostgresStudioWorkspaceRepository
            and repository.creation_license_authoritative
        )
    if type(service) is StudioReportService:
        repository = getattr(service, "_repository", None)
        return not (
            type(repository) is PostgresStudioReportRepository
            and repository.creation_license_authoritative
        )
    return True


def _organization_provisioner(dependencies: RuntimeDependencies) -> Any:
    """Provision the identity and authorization records for an approved org."""
    def provision(request: Any) -> None:
        identifier = str(request.requested_org_identifier).strip().lower()
        if not re.fullmatch(r"[a-z0-9][a-z0-9._:-]{0,127}", identifier):
            raise OrganizationWorkflowError("INVALID_ORGANIZATION_IDENTIFIER", 400)
        tenant_id = f"tenant-{identifier}"
        dependencies.identity_repository.add_tenant_membership(
            tenant_id=tenant_id, user_id=str(request.applicant_user_id),
            role=Role.ORGANIZATION_ADMIN.value,
        )
        dependencies.authorization_repository.bootstrap_workspace(
            tenant_id=tenant_id, workspace_id=f"workspace-{identifier}",
            owner_user_id=str(request.applicant_user_id),
            owner_role=Role.ORGANIZATION_ADMIN, workspace_kind="organization",
            data_area="cloud_sync", cost_limit_cents=1000,
            now=datetime.now(timezone.utc),
        )
    return provision


def _organization_management_ready(dependencies: RuntimeDependencies) -> bool:
    """실제 같은 PG와 0068 보호가 확인된 경우에만 여섯 쓰기를 연다."""
    from .organization_access import OrganizationAccessBridge
    if not dependencies.settings.organization_admin_v2_enabled:
        return False
    repositories = (dependencies.identity_repository, dependencies.authorization_repository, dependencies.organization_repository)
    if any(type(repository) is not expected for repository, expected in zip(repositories,
            (PostgresIdentityRepository, PostgresAuthorizationRepository, PostgresOrganizationRepository))):
        return False
    store = dependencies.cloud_store
    if type(store) is not PostgresCloudStore or store._organization_admin_v2_enabled is not True:
        return False
    if any(repository._dsn != store._dsn for repository in repositories):
        return False
    bridge = dependencies.organization_repository._access_bridge
    if type(bridge) is not OrganizationAccessBridge or bridge._postgres is not True:
        return False
    try:
        return store.readiness().ready is True
    except Exception:
        return False


def create_app(dependencies: RuntimeDependencies) -> FastAPI:
    from .account_settings import AccountSettingsService
    account_settings_service = AccountSettingsService(dependencies.identity_service,
        notification_inbox_enabled=dependencies.settings.notification_inbox_enabled)
    android_origin = dependencies.settings.public_gateway_url
    if android_origin is not None and urlsplit(android_origin).scheme != "https":
        android_origin = None
    android_auth = AndroidBrowserAuthService(
        repository=dependencies.identity_repository,
        identity=dependencies.identity_service,
        clock=lambda: datetime.now(timezone.utc),
        clients=dependencies.settings.android_auth_clients,
        authorization_origin=android_origin,
    )
    android_rate_lock = threading.Lock()
    android_start_times: dict[str, tuple[float, int]] = {}

    def android_start_allowed(request: Request) -> bool:
        peer = request.client.host if request.client is not None else "unknown"
        now = time.monotonic()
        with android_rate_lock:
            window, count = android_start_times.get(peer, (now, 0))
            if now - window >= 60:
                window, count = now, 0
            if peer not in android_start_times and len(android_start_times) >= 1024:
                expired = [key for key, (started, _) in android_start_times.items()
                           if now - started >= 60]
                for key in expired:
                    del android_start_times[key]
                if len(android_start_times) >= 1024:
                    return False
            if count >= 10:
                return False
            android_start_times[peer] = (window, count + 1)
            return True

    notification_service = dependencies.notification_service or NotificationService(
        repository=ReferenceNotificationRepository(),
        authorization_service=dependencies.authorization_service,
        audit_store=dependencies.audit_store,
        clock=lambda: datetime.now(timezone.utc),
    )
    def notification_service_for(principal: IdentityPrincipal, trace_id: str) -> NotificationService:
        if not dependencies.settings.notification_inbox_enabled:
            return notification_service
        from .notification_postgres import ScopedPostgresNotificationRepository
        cloud = dependencies.cloud_store
        if cloud is None or not getattr(cloud, '_notification_inbox_enabled', False):
            raise NotificationError('DATABASE_UNAVAILABLE', 503)
        ready = cloud.readiness()
        if ready.ready is not True or ready.schema_revision != '0069':
            raise NotificationError('DATABASE_UNAVAILABLE', 503)
        try:
            workspaces = dependencies.identity_service.notification_workspace_ids(
                user_id=principal.user_id, tenant_id=principal.tenant_id)
        except IdentityError as error:
            if error.http_status >= 500:
                raise NotificationError('DATABASE_UNAVAILABLE', 503) from None
            raise
        contexts = []
        for workspace in workspaces:
            try:
                dependencies.authorization_service.authorize_action(principal=principal, workspace_id=workspace,
                    action=Action.VIEW, trace_id=trace_id, policy_version=dependencies.settings.policy_version)
            except AuthorizationError as error:
                if error.http_status >= 500:
                    raise NotificationError('DATABASE_UNAVAILABLE', 503) from None
                if error.http_status not in {403,404}:
                    raise
                continue
            contexts.append(CloudAccessContext(principal.tenant_id, workspace, principal.user_id, 'view'))
        return NotificationService(repository=ScopedPostgresNotificationRepository(cloud._dsn,
            tenant_id=principal.tenant_id, recipient_id=principal.user_id, contexts=tuple(contexts),
            studio_approval_repository=studio_approval_lifecycle_repository),
            authorization_service=dependencies.authorization_service, audit_store=dependencies.audit_store,
            clock=lambda:datetime.now(timezone.utc))
    sync_service = dependencies.sync_service or SyncService(
        ReferenceSyncRepository(), ReferenceTransferPort(),
        clock=lambda: datetime.now(timezone.utc),
    )
    knowledge_package_service = _resolve_knowledge_package_service(
        dependencies.knowledge_package_service, dependencies.cloud_store,
    )
    connector_registry = dependencies.connector_registry or ConnectorRegistry(
        (
            create_open_law_connector(api_key=os.environ.get("DAON_OPEN_LAW_API_KEY")),
            ApprovedKnowledgeConnector(
                token=os.environ.get("DAON_APPROVED_KNOWLEDGE_TOKEN"),
                timeout_seconds=5,
                max_retries=2,
            ).as_connector(),
        )
    )
    connector_repository = dependencies.connector_repository or (
        PostgresConnectorRepository(dependencies.cloud_store) if dependencies.cloud_store is not None else None
    )
    # Reference (non-Postgres) runtime still isolates local law registrations.
    # None is a local unregister tombstone, never a global Registry deletion.
    open_law_registrations = {}
    open_law_receipts = {}

    def scoped_open_law(principal, workspace_id):
        if connector_repository is not None:
            return connector_repository.get(principal.tenant_id, workspace_id, principal.user_id, "mcp-open-law-go-kr")
        view = open_law_registrations.get((principal.tenant_id, workspace_id))
        if view is None:
            raise ConnectorError("CONNECTOR_NOT_FOUND")
        return view

    def set_scoped_open_law(principal, workspace_id, status, error_code):
        previous = scoped_open_law(principal, workspace_id)
        if connector_repository is not None:
            return connector_repository.set_status(principal.tenant_id, workspace_id, principal.user_id,
                previous.connector_id, status=status, error_code=error_code)
        view = replace(previous, status=status, error_code=error_code,
            last_checked_at=datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"))
        open_law_registrations[(principal.tenant_id, workspace_id)] = view
        return view
    retention_service = (
        PostgresRetentionRequestService(
            dependencies.cloud_store,
            PostgresRetentionInventoryProvider(dependencies.cloud_store),
            clock=lambda: datetime.now(timezone.utc),
            object_storage=dependencies.object_storage,
        )
        if dependencies.cloud_store is not None
        else dependencies.retention_service or RetentionService(
            ReferenceRetentionRepository(), ReferenceCleanupPort(),
            clock=lambda: datetime.now(timezone.utc),
        )
    )
    recovery_service = dependencies.recovery_service or UnavailableRecoveryService()
    provider_settings_service = dependencies.provider_settings_service or ProviderSettingsService(
        (
            ReferenceProviderSettingsRepository()
            if dependencies.cloud_store is None
            else PostgresProviderSettingsRepository(dependencies.cloud_store)
        ),
        ServerCredentialPresenceResolver(),
    )
    provider_connection_service = dependencies.provider_connection_service
    provider_health_settings_service = dependencies.provider_health_settings_service
    user_provider_credential_service = None
    provider_cipher = None
    if (
        dependencies.cloud_store is not None
        and dependencies.settings.provider_credential_key_file is not None
    ):
        try:
            provider_key = dependencies.settings.provider_credential_key_file.read_bytes()
        except OSError:
            raise ValueError("PROVIDER_CREDENTIAL_KEY_REFERENCE_UNAVAILABLE") from None
        provider_cipher = ProviderCredentialCipher(provider_key, encryption_key_version=1)
    if (
        provider_connection_service is None
        and dependencies.cloud_store is not None
        and provider_cipher is not None
    ):
        provider_connection_service = PostgresProviderConnectionService(
            dependencies.cloud_store, provider_cipher,
            readiness_observer=_document_readiness_observer(dependencies.document_readiness_repository),
        )
    if dependencies.cloud_store is not None and provider_cipher is not None:
        user_provider_credential_service = PostgresUserProviderCredentialService(
            dependencies.cloud_store, provider_cipher,
        )
    workspace_model_defaults_service = dependencies.workspace_model_defaults_service
    if workspace_model_defaults_service is None and dependencies.cloud_store is not None:
        workspace_model_defaults_service = PostgresWorkspaceModelDefaultsService(dependencies.cloud_store)
    retention_request_service = retention_service
    operations_status_service = dependencies.operations_status_service
    if operations_status_service is None and dependencies.cloud_store is not None:
        operations_status_service = OperationsStatusService(
            PostgresOperationsStatusRepository(dependencies.cloud_store)
        )
    output_version_settings_service = dependencies.output_version_settings_service or OutputVersionSettingsService(
        ReferenceOutputVersionSettingsRepository()
        if dependencies.cloud_store is None
        else PostgresOutputVersionSettingsRepository(dependencies.cloud_store)
    )
    screen_preference_service = dependencies.screen_preference_service or ScreenPreferenceService(
        ReferenceScreenPreferenceRepository()
        if dependencies.cloud_store is None
        else PostgresScreenPreferenceRepository(dependencies.cloud_store)
    )
    if dependencies.license_service is not None:
        license_service = dependencies.license_service
    else:
        license_repository = (
            ReferenceLicenseRepository()
            if dependencies.cloud_store is None
            else PostgresLicenseRepository(dependencies.cloud_store)
        )
        if dependencies.settings.license_public_keys_file is None:
            license_verifier = UnavailableLicenseVerifier()
        else:
            license_verifier = RsaSha256LicenseVerifier(
                load_rsa_public_keys(dependencies.settings.license_public_keys_file)
            )
        usage_reader = (
            (lambda _context: {})
            if dependencies.cloud_store is None
            else license_repository.usage
        )
        license_service = LicenseService(
            license_repository, license_verifier, product_code="daon-user",
            clock=lambda: datetime.now(timezone.utc), usage_reader=usage_reader,
        )
    license_enforcement_enabled = _license_enforcement_enabled(
        dependencies.settings,
        configured_service=dependencies.license_service is not None,
    )
    if dependencies.notebook_service is not None:
        notebook_service = dependencies.notebook_service
    else:
        notebook_repository = (
            ReferenceNotebookRepository()
            if dependencies.cloud_store is None
            else PostgresNotebookRepository(
                dependencies.cloud_store,
                creation_enforcer=(
                    (
                        (lambda *_args: None)
                        if dependencies.settings.dev_auth_bypass
                        else enforce_license_creation
                    )
                    if license_enforcement_enabled
                    else None
                ),
            )
        )
        notebook_service = NotebookService(
            notebook_repository,
            clock=lambda: datetime.now(timezone.utc),
            require_create=(
                None
                if not license_enforcement_enabled
                else lambda context: license_service.require_creation(
                    LicenseContext(
                        context.tenant_id, context.workspace_id, context.actor_id,
                        context.trace_id, context.policy_version,
                    ),
                    "notebook.create", {"notebooks": 1},
                )
            ),
        )
    egress_policy_service = dependencies.egress_policy_service
    if egress_policy_service is None and dependencies.cloud_store is not None:
        egress_policy_service = EgressPolicyService(
            PostgresEgressPolicyRepository(dependencies.cloud_store)
        )
    citation_content_repository = dependencies.citation_content_repository
    question_answering_service = dependencies.question_answering_service
    if (
        question_answering_service is None and dependencies.cloud_store is not None
        and dependencies.object_storage is not None and provider_cipher is not None
    ):
        citation_content_repository = PostgresQuestionAnsweringRepository(
            dependencies.cloud_store, dependencies.object_storage,
            open_law_text_reader=lambda context,key,digest,size: _read_open_law_original(
                context,key,digest,size,dependencies.website_storage),
        )
        question_answering_service = QuestionAnsweringService(
            PostgresWorkspaceModelResolver(
                dependencies.cloud_store, provider_cipher, user_provider_credential_service,
            ),
            citation_content_repository,
            PostgresDocumentIndex(dependencies.cloud_store),
            ServerProviderCredentialResolver(), UrlLibDocumentUnderstandingTransport(),
            PostgresQuestionEgressAuthorizer(
                dependencies.cloud_store,
                cast(EgressPolicyService, egress_policy_service),
                development_auth_bypass=(
                    dependencies.settings.dev_auth_bypass
                    and dependencies.settings.profile in {"test", "development"}
                ),
            ),
            adapter_registry=QuestionAdapterRegistry(),
            qa_structured_ollama_workspace_id=dependencies.settings.qa_structured_ollama_workspace_id,
        )
    studio_report_repository = dependencies.studio_report_repository
    studio_report_service = dependencies.studio_report_service
    if studio_report_repository is None and dependencies.cloud_store is not None:
        studio_report_repository = PostgresStudioReportRepository(
            dependencies.cloud_store,
            creation_enforcer=enforce_license_creation if license_enforcement_enabled else None,
        )
    if studio_report_service is None and studio_report_repository is not None:
        studio_report_service = StudioReportService(studio_report_repository)
    studio_workspace_service = dependencies.studio_workspace_service
    studio_approval_lifecycle_repository = dependencies.studio_approval_lifecycle_repository
    if studio_approval_lifecycle_repository is None and dependencies.cloud_store is not None:
        studio_approval_lifecycle_repository = StudioApprovalLifecycleRepository(dependencies.cloud_store)
    if studio_workspace_service is None and dependencies.cloud_store is not None:
        studio_workspace_service = StudioWorkspaceService(PostgresStudioWorkspaceRepository(
            dependencies.cloud_store, dependencies.object_storage,
            creation_enforcer=enforce_license_creation if license_enforcement_enabled else None,
        ))
    provider_health_stop = asyncio.Event()
    provider_health_task: asyncio.Task[None] | None = None

    async def provider_health_loop() -> None:
        if provider_connection_service is None or provider_health_settings_service is None:
            return
        health_context = ProviderConnectionAdminContext(
            tenant_id="system", actor_id="provider-health-monitor",
            trace_id="provider-health-monitor", policy_version=dependencies.settings.policy_version,
        )
        monitor = ProviderHealthMonitor(
            list_connections=lambda: tuple(
                ProviderHealthConnection(connection_id)
                for connection_id in provider_connection_service.list_active_connection_ids(health_context)
            ),
            check_connection=lambda item: str(
                provider_connection_service.check_active_connection(
                    health_context, item.connection_id,
                )["status"]
            ),
            readiness_consumer=_document_readiness_consumer(dependencies.document_readiness_repository),
        )
        await monitor.run_with_settings(
            provider_health_stop,
            lambda: provider_health_settings_service.get(health_context),
        )

    def admin_audit_retry_loop(stop: threading.Event, finished: Future[None]) -> None:
        service = dependencies.admin_membership_role_service
        notification_cursor = None
        notification_projection_cursors = {}
        try:
            while not stop.is_set():
                if service is not None:
                    try:
                        service._dispatch_pending_audit(stop=stop)
                    except Exception:
                        # The committed operation remains pending for the next bounded batch.
                        pass
                try:
                    dependencies.identity_service.dispatch_pending_tenant_switch_audits()
                    account_settings_service.dispatch_pending_audits()
                except Exception:
                    # A committed switch remains pending until the next bounded batch.
                    pass
                if dependencies.organization_repository is not None:
                    try:
                        dependencies.organization_repository.dispatch_pending_management_audits(stop=stop)
                    except Exception:
                        # S39 장애는 기존 감사 처리나 다음 주기를 건너뛰지 않는다.
                        pass
                if not stop.is_set():
                    try:
                        notification_cursor = _notification_dispatch_tick(dependencies, stop=stop,
                            after=notification_cursor, projection_cursors=notification_projection_cursors)
                    except Exception:
                        # 발견/준비 장애는 원감사 순서와 cursor를 보존하고 다음 60초 주기에 재시도한다.
                        _runtime_logger.warning('알림 자동 처리 주기 실패')
                stop.wait(_ADMIN_AUDIT_RETRY_INTERVAL_SECONDS)
        finally:
            finished.set_result(None)

    @asynccontextmanager
    async def lifespan(_app: FastAPI) -> AsyncIterator[None]:
        # Resume durable deletion requests after the process is ready. The
        # request path also schedules newly accepted work; this hook is kept
        # injectable so deployment workers can provide tenant-scoped contexts.
        resume = getattr(dependencies.notebook_deletion_worker, "resume_startup", None)
        if callable(resume):
            await asyncio.to_thread(resume)
        nonlocal provider_health_task
        if provider_connection_service is not None and provider_health_settings_service is not None:
            provider_health_task = asyncio.create_task(provider_health_loop())
        admin_audit_stop = threading.Event()
        admin_audit_finished: Future[None] = Future()
        admin_audit_thread = (
            threading.Thread(
                target=admin_audit_retry_loop, args=(admin_audit_stop, admin_audit_finished),
                name="admin-role-audit-outbox", daemon=True,
            )
            if dependencies.admin_membership_role_service is not None
            or dependencies.identity_service is not None else None
        )
        if admin_audit_thread is not None:
            admin_audit_thread.start()
        try:
            yield
        finally:
            admin_audit_stop.set()
            provider_health_stop.set()
            if provider_health_task is not None:
                await provider_health_task
            if admin_audit_thread is not None:
                await asyncio.to_thread(admin_audit_thread.join, _ADMIN_AUDIT_RETRY_SHUTDOWN_SECONDS)
            if admin_audit_thread is not None and admin_audit_thread.is_alive():
                # A running thread still owns its stores; close only after it exits.
                admin_audit_finished.add_done_callback(lambda _future: dependencies.close())
            else:
                dependencies.close()

    class TimedApiRoute(APIRoute):
        def get_route_handler(self) -> Any:
            original = super().get_route_handler()

            async def timed_handler(request: Request) -> Response:
                website=request.url.path.endswith('/website-imports')
                law=request.url.path.endswith(('/law-search','/law-preview','/law-imports','/law-snapshot'))
                budget=request_timeout_for_path(dependencies.settings, request.url.path, method=request.method)
                if website:
                    request.state.website_deadline=time.monotonic()+budget
                if law:request.state.law_deadline=time.monotonic()+budget
                try:
                    async with asyncio.timeout(budget):
                        response=cast(Response, await original(request))
                    # 이벤트 루프의 늦은 timer/cancellation delivery에도 웹 작업의
                    # 절대deadline 이후 ready 응답을 내보내지 않는다.
                    if website and time.monotonic()>=request.state.website_deadline:
                        raise TimeoutError
                    if law and time.monotonic()>=request.state.law_deadline:raise TimeoutError
                    return response
                except TimeoutError:
                    if law:request.state.law_cancelled=True
                    return _error_response(
                        504, "OPEN_LAW_TIMEOUT" if law else "WEBSITE_FETCH_TIMEOUT" if website else "REQUEST_TIMEOUT", request.state.trace_id, retryable=True
                    )
                except asyncio.CancelledError:
                    if law:request.state.law_cancelled=True
                    raise

            return timed_handler

    app = FastAPI(title="Daon User API", version="1.0.0", lifespan=lifespan)
    app.router.route_class = TimedApiRoute
    if dependencies.organization_repository is not None:
        OrganizationApi(
            dependencies.organization_repository,
            dependencies.authorization_repository,
            system_admin_checker=lambda principal: principal.user_id in dependencies.settings.system_admin_user_ids,
            organization_provisioner=_organization_provisioner(dependencies),
            identity_repository=dependencies.identity_repository,
            policy_version=dependencies.settings.policy_version,
        ).mount(app, lambda request: _principal(request, dependencies))

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
            internal_bff_transport = request.headers.get("x-daon-bff-transport") == "internal"
            if (
                dependencies.settings.profile == "production"
                and not is_health
                and request.url.scheme != "https"
                and not internal_bff_transport
            ):
                return _error_response(400, "HTTPS_REQUIRED", trace_id)
            if sum(len(key) + len(value) for key, value in request.scope["headers"]) > dependencies.settings.max_header_bytes:
                return _error_response(431, "REQUEST_HEADERS_TOO_LARGE", trace_id)
            if request.method in _BODY_METHODS:
                content_type = request.headers.get("content-type", "").split(";", 1)[0].strip().lower()
                is_source_upload = (
                    request.method == "POST" and _SOURCE_UPLOAD_PATH.fullmatch(request.url.path) is not None
                )
                if is_source_upload:
                    filename = request.headers.get("x-source-filename", "")
                    expected_type = SourceIngestor.expected_mime_for_filename(filename)
                    if expected_type is None:
                        return _error_response(400, "SOURCE_FILENAME_INVALID", trace_id)
                    if content_type != expected_type:
                        return _error_response(415, "UNSUPPORTED_MEDIA_TYPE", trace_id)
                elif content_type != "application/json" and not (
                    request.method == "POST" and request.url.path in {
                        "/api/v1/session/tenant", "/api/v1/session/native/logout",
                    }
                ):
                    return _error_response(415, "UNSUPPORTED_MEDIA_TYPE", trace_id)
                declared = request.headers.get("content-length")
                if declared is not None:
                    try:
                        limit = (
                            dependencies.settings.source_upload_max_bytes
                            if is_source_upload
                            else dependencies.settings.max_body_bytes
                        )
                        if int(declared) > limit:
                            return _error_response(413, "REQUEST_TOO_LARGE", trace_id)
                    except ValueError:
                        return _error_response(400, "INVALID_REQUEST", trace_id)
                if not is_source_upload:
                    body = await request.body()
                    if len(body) > dependencies.settings.max_body_bytes:
                        return _error_response(413, "REQUEST_TOO_LARGE", trace_id)
            allowed_restricted_request = (request.method, request.url.path) in {
                ("GET", "/api/v1/session"),
                ("POST", "/api/v1/auth/password/change"),
                ("POST", "/api/v1/session/logout"),
                ("POST", "/api/v1/session/native/logout"),
            }
            if not is_health and not allowed_restricted_request:
                try:
                    restricted_token, restricted_kind = _credential(request)
                    restricted_view = dependencies.identity_service.describe_access(
                        restricted_token,
                        trace_id=trace_id,
                        policy_version=dependencies.settings.policy_version,
                    )
                except IdentityError:
                    restricted_view = None
                if (
                    restricted_view is not None
                    and restricted_view.client_kind is restricted_kind
                    and restricted_view.password_change_required
                ):
                    return _error_response(403, "PASSWORD_CHANGE_REQUIRED", trace_id)
            if (
                dependencies.organization_repository is not None
                and request.url.path.startswith("/api/v1/workspaces/")
            ):
                # Approved memberships are represented in the organization
                # workflow store. A suspended member must not continue using
                # workspace business APIs; users without a workflow row keep
                # the existing personal/workspace authorization path.
                try:
                    current_principal = _principal(request, dependencies)
                    membership_state = dependencies.organization_repository.membership_state(
                        tenant_id=current_principal.tenant_id, user_id=current_principal.user_id,
                    )
                except IdentityError:
                    membership_state = None
                if membership_state is not None and membership_state.value == "suspended":
                    return _error_response(403, "ORGANIZATION_MEMBERSHIP_SUSPENDED", trace_id)
            organization_write = (
                request.method == "POST" and (request.url.path == "/api/v1/organization/join-requests"
                    or re.fullmatch(r"/api/v1/organization/join-requests/[^/]+/decision", request.url.path)
                    or re.fullmatch(r"/api/v1/organization/tenants/[^/]+/invitations", request.url.path))
                or request.method == "DELETE" and re.fullmatch(r"/api/v1/organization/tenants/[^/]+/invitations/[^/]+", request.url.path)
                or request.method == "PATCH" and re.fullmatch(r"/api/v1/organization/tenants/[^/]+/members/[^/]+/(role|state)", request.url.path)
            )
            if organization_write:
                try:
                    _principal(request, dependencies)
                except IdentityError:
                    return _error_response(401, "AUTHENTICATION_REQUIRED", trace_id)
                if not await asyncio.to_thread(_organization_management_ready, dependencies):
                    return _error_response(503, "ORGANIZATION_WORKFLOW_UNAVAILABLE", trace_id)
            response = cast(Response, await call_next(request))
            response.headers["X-Trace-Id"] = trace_id
            response.headers["Cache-Control"] = "private, no-store" if response.headers.get("content-type", "").split(";", 1)[0] == "audio/wav" else "no-store"
            response.headers["X-Content-Type-Options"] = "nosniff"
            response.headers["Referrer-Policy"] = "no-referrer"
            return response
        finally:
            if counted:
                dependencies.state.end_request()

    @app.exception_handler(RequestValidationError)
    async def validation_error(request: Request, _error: RequestValidationError) -> JSONResponse:
        if request.url.path in {"/api/v1/account/profile", "/api/v1/account/devices", "/api/v1/account/sessions"}:
            return _error_response(422, "ACCOUNT_INPUT_INVALID", request.state.trace_id)
        organization_operation = (request.method, getattr(request.scope.get("route"), "path", ""))
        organization_writes = {
            ("POST", "/api/v1/organization/join-requests"),
            ("POST", "/api/v1/organization/join-requests/{request_id}/decision"),
            ("POST", "/api/v1/organization/tenants/{tenant_id}/invitations"),
            ("DELETE", "/api/v1/organization/tenants/{tenant_id}/invitations/{invitation_id}"),
            ("PATCH", "/api/v1/organization/tenants/{tenant_id}/members/{user_id}/role"),
            ("PATCH", "/api/v1/organization/tenants/{tenant_id}/members/{user_id}/state"),
        }
        # 승인된 헤더/폐기 query만 422이며 본문과 다른 경로의 기존 오류는 유지한다.
        if organization_operation in organization_writes and any(
            tuple(error.get("loc", ())) in {
                ("header", "X-Daon-Expected-Session-Id"), ("header", "Idempotency-Key"),
            } or (request.method == "DELETE" and tuple(error.get("loc", ())) == ("query", "expected_version"))
            for error in _error.errors()
        ):
            return _error_response(422, "INVALID_REQUEST", request.state.trace_id)
        if (request.method == "POST"
                and re.fullmatch(r"/api/v1/sources/[^/]+/deletion-requests", request.url.path)
                and any(error.get("loc") == ("body", "step_up_authorization_id")
                        and (error.get("type") in {"missing", "string_too_short"} or error.get("input") is None)
                        for error in _error.errors())):
            return _error_response(403, "STEP_UP_REQUIRED", request.state.trace_id)
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

    @app.exception_handler(OrganizationWorkflowError)
    async def organization_workflow_error(request: Request, error: OrganizationWorkflowError) -> JSONResponse:
        return _error_response(error.status, error.code, request.state.trace_id)

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

    @app.exception_handler(KnowledgePackageError)
    async def knowledge_package_error(
        request: Request, error: KnowledgePackageError,
    ) -> JSONResponse:
        safe_codes = {
            "STEP_UP_REQUIRED", "CURRENT_ACCESS_DENIED", "IDEMPOTENCY_KEY_REUSED",
            "KNOWLEDGE_PACKAGE_UNAVAILABLE", "OFFLINE_KNOWLEDGE_COPY_UNAVAILABLE",
            "KNOWLEDGE_PACKAGE_DIGEST_MISMATCH",
        }
        return _error_response(
            error.status,
            error.code if error.code in safe_codes else "INVALID_REQUEST",
            request.state.trace_id,
        )

    @app.exception_handler(ConnectorError)
    async def connector_error(request: Request, error: ConnectorError) -> JSONResponse:
        safe_codes = {
            "CONNECTOR_NOT_FOUND", "CONNECTOR_KIND_UNSUPPORTED", "CONNECTOR_STATUS_INVALID",
            "CONNECTOR_INPUT_INVALID", "CONNECTOR_SOURCE_ID_INVALID", "CONNECTOR_UNAVAILABLE", "CONNECTOR_FIXED",
            "OPEN_LAW_BINDING_CONFLICT",
        }
        return _error_response(404 if str(error) == "CONNECTOR_NOT_FOUND" else 409,
                               str(error) if str(error) in safe_codes else "CONNECTOR_UNAVAILABLE",
                               request.state.trace_id, retryable=str(error) == "CONNECTOR_UNAVAILABLE")

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
    async def unexpected_error(request: Request, error: Exception) -> JSONResponse:
        _runtime_logger.exception(
            "unexpected_api_error trace_id=%s path=%s error_type=%s",
            request.state.trace_id, request.url.path, type(error).__name__,
        )
        return _error_response(500, "INTERNAL_ERROR", request.state.trace_id)

    @app.get("/health/live")
    async def live() -> dict[str, str]:
        return {"status": "live"}

    @app.get("/health/ready")
    async def ready(request: Request) -> JSONResponse:
        cloud_ready = (
            not dependencies.settings.organization_admin_v2_enabled
            if dependencies.cloud_store is None
            else (await asyncio.to_thread(dependencies.cloud_store.readiness)).ready
        )
        object_ready = (
            dependencies.object_storage is None
            or await asyncio.to_thread(dependencies.object_storage.health)
        )
        organization_ready = (not dependencies.settings.organization_admin_v2_enabled
            or await asyncio.to_thread(_organization_management_ready, dependencies))
        status = 200 if dependencies.state.ready and cloud_ready and object_ready and organization_ready else 503
        response = JSONResponse(
            status_code=status,
            content={"status": "ready" if status == 200 else "not_ready"},
        )
        response.headers["X-Trace-Id"] = request.state.trace_id
        return response

    @app.post("/api/v1/auth/signup", status_code=202)
    async def signup(body: SignupBody, request: Request) -> dict[str, object]:
        dependencies.identity_service.signup(
            login_id=body.login_id, email=body.email, password=body.password,
            trace_id=request.state.trace_id, policy_version=dependencies.settings.policy_version,
        )
        return {"data": {"status": "verification_required"}, "meta": {"trace_id": request.state.trace_id}}

    @app.exception_handler(ProviderSettingsError)
    async def provider_settings_error(
        request: Request, error: ProviderSettingsError,
    ) -> JSONResponse:
        public_codes = {
            "VERSION_CONFLICT", "PROVIDER_CODE_UNSUPPORTED", "PROVIDER_BASE_URL_INVALID",
            "PROVIDER_PROFILE_REQUIRED", "MODEL_DEPLOYMENT_INVALID", "MODEL_ROLE_UNSUPPORTED",
            "PROVIDER_PROFILE_INACTIVE", "PROVIDER_CREDENTIAL_REQUIRED",
            "PROVIDER_CONNECTION_UNAVAILABLE",
            "MODEL_BINDING_INVALID", "DATABASE_UNAVAILABLE", "DATABASE_TIMEOUT",
        }
        return _error_response(
            error.status,
            error.code if error.code in public_codes else "INVALID_REQUEST",
            request.state.trace_id,
            retryable=error.retryable,
        )

    @app.exception_handler(OperationsStatusError)
    async def operations_status_error(
        request: Request, _error: OperationsStatusError,
    ) -> JSONResponse:
        return _error_response(
            503, "OPERATIONS_STATUS_UNAVAILABLE", request.state.trace_id, retryable=True,
        )

    @app.exception_handler(OutputVersionSettingsError)
    async def output_version_settings_error(
        request: Request, error: OutputVersionSettingsError,
    ) -> JSONResponse:
        public_codes = {
            "OUTPUT_VERSION_SETTINGS_INVALID", "OUTPUT_VERSION_SETTINGS_UNAVAILABLE",
            "VERSION_CONFLICT", "IDEMPOTENCY_KEY_REUSED",
        }
        return _error_response(
            error.status,
            error.code if error.code in public_codes else "INVALID_REQUEST",
            request.state.trace_id,
            retryable=error.retryable,
        )

    @app.exception_handler(ScreenPreferenceError)
    async def screen_preference_error(
        request: Request, error: ScreenPreferenceError,
    ) -> JSONResponse:
        public_codes = {"SCREEN_PREFERENCE_INVALID", "SCREEN_PREFERENCE_UNAVAILABLE"}
        return _error_response(
            error.status,
            error.code if error.code in public_codes else "INVALID_REQUEST",
            request.state.trace_id,
            retryable=error.code == "SCREEN_PREFERENCE_UNAVAILABLE",
        )

    @app.exception_handler(LicenseError)
    async def license_error(request: Request, error: LicenseError) -> JSONResponse:
        public_codes = {
            "LICENSE_NOT_CONFIGURED", "LICENSE_SIGNATURE_INVALID",
            "LICENSE_SIGNING_KEY_UNAVAILABLE", "LICENSE_SCHEMA_UNSUPPORTED",
            "LICENSE_PRODUCT_MISMATCH", "LICENSE_ORGANIZATION_MISMATCH",
            "LICENSE_PERIOD_INVALID", "LICENSE_EXPIRED",
            "LICENSE_RESOURCE_LIMIT_REACHED", "LICENSE_UNAVAILABLE",
            "LICENSE_USAGE_UNAVAILABLE", "IDEMPOTENCY_KEY_REUSED",
            "IDEMPOTENCY_KEY_INVALID",
        }
        return _error_response(
            error.status, error.code if error.code in public_codes else "LICENSE_DOCUMENT_INVALID",
            request.state.trace_id,
            retryable=error.code in {"LICENSE_UNAVAILABLE", "LICENSE_USAGE_UNAVAILABLE"},
        )

    @app.exception_handler(NotebookError)
    async def notebook_error(request: Request, error: NotebookError) -> JSONResponse:
        _runtime_logger.warning(
            "notebook_api_error trace_id=%s path=%s code=%s status=%s",
            request.state.trace_id, request.url.path, error.code, error.status,
        )
        public_codes = {
            "NOTEBOOK_NOT_FOUND", "NOTEBOOK_TITLE_INVALID", "NOTEBOOK_DESCRIPTION_INVALID",
            "NOTEBOOK_ETAG_INVALID", "NOTEBOOK_ETAG_MISMATCH", "NOTEBOOK_UNAVAILABLE",
            "NOTEBOOK_TITLE_CONFIRMATION_MISMATCH", "NOTEBOOK_DELETION_IN_PROGRESS",
            "NOTEBOOK_DELETION_NOT_FOUND", "DELETE_SHARED_DATA_BLOCKED", "RETENTION_HOLD",
            "LICENSE_NOT_CONFIGURED", "LICENSE_EXPIRED", "LICENSE_FEATURE_NOT_ALLOWED",
            "LICENSE_RESOURCE_LIMIT_REACHED", "IDEMPOTENCY_KEY_INVALID", "IDEMPOTENCY_KEY_REUSED",
            "QUESTION_FAILED",
        }
        return _error_response(
            error.status, error.code if error.code in public_codes else "INVALID_REQUEST",
            request.state.trace_id, retryable=error.code == "NOTEBOOK_UNAVAILABLE",
        )

    @app.exception_handler(EgressPolicyError)
    async def egress_policy_error(
        request: Request, error: EgressPolicyError,
    ) -> JSONResponse:
        public_codes = {
            "EGRESS_POLICY_UNAVAILABLE", "EGRESS_POLICY_STALE", "EGRESS_POLICY_DENIED",
            "VERSION_CONFLICT", "IDEMPOTENCY_KEY_REUSED", "EGRESS_POLICY_PAYLOAD_INVALID",
        }
        return _error_response(
            error.status, error.code if error.code in public_codes else "INVALID_REQUEST",
            request.state.trace_id,
        )

    @app.exception_handler(QuestionAnsweringError)
    async def question_answering_error(
        request: Request, error: QuestionAnsweringError,
    ) -> JSONResponse:
        public_codes = {
            "TEXT_MODEL_NOT_SELECTED", "TEXT_MODEL_UNAVAILABLE",
            "TEXT_PROVIDER_UNAVAILABLE", "TEXT_GENERATION_GROUNDING_INVALID",
            "TEXT_GENERATION_RESPONSE_INVALID", "QUESTION_SOURCE_UNAVAILABLE",
            "EGRESS_POLICY_UNAVAILABLE", "EGRESS_POLICY_STALE", "EGRESS_POLICY_DENIED",
        }
        return _error_response(
            error.status, error.code if error.code in public_codes else "QUESTION_FAILED",
            request.state.trace_id, retryable=error.retryable,
        )

    @app.exception_handler(QuestionRepositoryError)
    async def question_repository_error(
        request: Request, error: QuestionRepositoryError,
    ) -> JSONResponse:
        public_codes = {
            "QUESTION_SOURCE_UNAVAILABLE", "CITATION_CONTENT_UNAVAILABLE",
            "QUESTION_DATABASE_UNAVAILABLE", "IDEMPOTENCY_KEY_REUSED",
        }
        return _error_response(
            error.status, error.code if error.code in public_codes else "QUESTION_FAILED",
            request.state.trace_id, retryable=error.retryable,
        )

    @app.exception_handler(StudioReportError)
    async def studio_report_error(
        request: Request, error: StudioReportError,
    ) -> JSONResponse:
        public_codes = {
            "EVIDENCE_REQUIRED", "RESOURCE_UNAVAILABLE", "IDEMPOTENCY_CONFLICT",
            "STUDIO_DATABASE_UNAVAILABLE", "STUDIO_SERVICE_UNAVAILABLE",
        }
        return _error_response(
            error.status, error.code if error.code in public_codes else "INVALID_REQUEST",
            request.state.trace_id, retryable=error.retryable,
        )

    @app.exception_handler(StudioError)
    async def studio_workspace_error(request: Request, error: StudioError) -> JSONResponse:
        public_codes = {
            "STUDIO_INPUT_INVALID", "STUDIO_DATABASE_UNAVAILABLE", "STUDIO_SERVICE_UNAVAILABLE",
            "STEP_UP_REQUIRED", "CHANGE_REASON_REQUIRED", "REVISION_TYPE_INVALID",
            "EXPORT_FORMAT_UNSUPPORTED", "RESOURCE_UNAVAILABLE", "IDEMPOTENCY_CONFLICT",
            "POLICY_PROJECTION_MISMATCH", "EVIDENCE_COVERAGE_INCOMPLETE", "REVIEW_REQUEST_REQUIRED",
            "POLICY_PROJECTION_UNAVAILABLE",
            "APPROVAL_REQUEST_EXPIRED", "APPROVAL_REQUEST_PENDING", "APPROVAL_REQUEST_REQUIRED", "PRECONDITION_FAILED",
            "KNOWLEDGE_CYCLE_DETECTED", "OBJECT_STORAGE_UNAVAILABLE", "OBJECT_CHECKSUM_MISMATCH",
        }
        return _error_response(
            error.status, error.code if error.code in public_codes else "INVALID_REQUEST",
            request.state.trace_id,
        )

    @app.exception_handler(SourceUploadError)
    async def source_upload_error(
        request: Request, error: SourceUploadError,
    ) -> JSONResponse:
        public_codes = {
            "SOURCE_FILENAME_INVALID", "CORRUPTED_SOURCE", "MIME_MISMATCH",
            "SOURCE_STORAGE_PENDING", "SOURCE_STORAGE_FAILED",
            "SOURCE_STORAGE_UNAVAILABLE", "IDEMPOTENCY_KEY_REUSED",
            "SOURCE_CANON_CONFLICT", "SOURCE_CANON_UNAVAILABLE",
        }
        return _error_response(
            error.status,
            error.code if error.code in public_codes else "INVALID_REQUEST",
            request.state.trace_id,
            retryable=error.retryable,
        )

    @app.exception_handler(DocumentUnderstandingError)
    async def document_processing_error(
        request: Request, error: DocumentUnderstandingError,
    ) -> JSONResponse:
        public_codes = {
            "DOCUMENT_PROCESSING_UNAVAILABLE", "PROCESSING_RUN_NOT_FOUND",
            "PROCESSING_RUN_CONFLICT", "SOURCE_NOT_FOUND",
            "SOURCE_PROCESSING_STATE_INVALID", "DATABASE_UNAVAILABLE", "DATABASE_TIMEOUT",
        }
        return _error_response(
            error.status,
            error.code if error.code in public_codes else "INVALID_REQUEST",
            request.state.trace_id,
            retryable=error.retryable,
        )

    @app.exception_handler(ProviderConnectionAdminError)
    async def provider_connection_admin_error(
        request: Request, error: ProviderConnectionAdminError,
    ) -> JSONResponse:
        safe_codes = {
            "PROVIDER_CONNECTION_NOT_FOUND", "PROVIDER_CONNECTION_ID_INVALID",
            "PROVIDER_CODE_INVALID", "PROVIDER_BASE_URL_INVALID", "PROVIDER_ADAPTER_UNSUPPORTED",
            "PROVIDER_CREDENTIAL_REQUIRED", "PROVIDER_CREDENTIAL_INVALID",
            "PROVIDER_AUTHENTICATION_FAILED", "PROVIDER_CONNECTION_FAILED",
            "PROVIDER_CATALOG_UNAVAILABLE", "PROVIDER_CATALOG_RESPONSE_INVALID",
            "PROVIDER_LOGICAL_MODEL_INVALID", "PROVIDER_REDIRECT_BLOCKED",
            "PROVIDER_RATE_LIMITED", "PROVIDER_CAPABILITY_UNSUPPORTED", "VERSION_CONFLICT",
            "IDEMPOTENCY_KEY_REUSED",
            "PROVIDER_SHORT_CODE_INVALID", "PROVIDER_SHORT_CODE_CONFLICT",
            "PROVIDER_ACCESS_MODE_INVALID", "PROVIDER_PERSONAL_SYSTEM_KEY_FORBIDDEN",
            "PROVIDER_ALLOWED_MODEL_INVALID", "PROVIDER_ALLOWED_MODEL_UNKNOWN",
            "PROVIDER_NAME_INVALID", "PROVIDER_MODEL_IDS_INVALID", "PROVIDER_PROBE_RESPONSE_INVALID",
            "PROVIDER_ADAPTER_IMMUTABLE", "PROVIDER_TEST_CREDENTIAL_FORBIDDEN",
            "PROVIDER_MODEL_DEFAULT_REFERENCED", "PROVIDER_CONNECTION_REFERENCED",
        }
        return _error_response(
            error.status, error.code if error.code in safe_codes else "INVALID_REQUEST",
            request.state.trace_id, retryable=error.retryable,
            details={"references": error.references} if error.references else None,
        )

    @app.exception_handler(ProviderHealthSettingsError)
    async def provider_health_settings_error(
        request: Request, error: ProviderHealthSettingsError,
    ) -> JSONResponse:
        return _error_response(
            error.status,
            error.code if error.code in {"PROVIDER_HEALTH_INTERVAL_INVALID", "VERSION_CONFLICT"} else "INVALID_REQUEST",
            request.state.trace_id,
        )

    @app.exception_handler(UserProviderCredentialError)
    async def user_provider_credential_error(
        request: Request, error: UserProviderCredentialError,
    ) -> JSONResponse:
        safe_codes = {
            "PROVIDER_CONNECTION_NOT_FOUND", "PROVIDER_CREDENTIAL_INVALID",
            "PROVIDER_CREDENTIAL_VERSION_INVALID", "VERSION_CONFLICT",
            "PERSISTENCE_UNAVAILABLE",
        }
        return _error_response(
            error.status, error.code if error.code in safe_codes else "INVALID_REQUEST",
            request.state.trace_id,
        )

    @app.exception_handler(WorkspaceModelDefaultsError)
    async def workspace_model_defaults_error(
        request: Request, error: WorkspaceModelDefaultsError,
    ) -> JSONResponse:
        safe_codes = {
            "PROVIDER_CAPABILITY_UNSUPPORTED", "WORKSPACE_MODEL_DEFAULT_UNAVAILABLE",
            "VERSION_CONFLICT", "IDEMPOTENCY_KEY_REUSED",
        }
        return _error_response(
            error.status,
            error.code if error.code in safe_codes else "WORKSPACE_MODEL_DEFAULTS_UNAVAILABLE",
            request.state.trace_id,
            retryable=error.retryable,
        )

    @app.post("/api/v1/auth/verify-email")
    async def verify_email(body: TokenBody, request: Request) -> dict[str, object]:
        dependencies.identity_service.verify_email(token=body.token, trace_id=request.state.trace_id, policy_version=dependencies.settings.policy_version)
        return {"data": {"status": "verified"}, "meta": {"trace_id": request.state.trace_id}}

    @app.post("/api/v1/auth/resend-verification", status_code=202)
    async def resend_verification(body: IdentifierBody, request: Request) -> dict[str, object]:
        dependencies.identity_service.resend_verification(identifier=body.identifier, trace_id=request.state.trace_id, policy_version=dependencies.settings.policy_version)
        return {"data": {"status": "accepted"}, "meta": {"trace_id": request.state.trace_id}}

    @app.post("/api/v1/auth/password-reset/request", status_code=202)
    async def password_reset_request(body: IdentifierBody, request: Request) -> dict[str, object]:
        dependencies.identity_service.request_password_reset(identifier=body.identifier, trace_id=request.state.trace_id, policy_version=dependencies.settings.policy_version)
        return {"data": {"status": "accepted"}, "meta": {"trace_id": request.state.trace_id}}

    @app.post("/api/v1/auth/password-reset/confirm")
    async def password_reset_confirm(body: PasswordResetConfirmBody, request: Request) -> dict[str, object]:
        dependencies.identity_service.confirm_password_reset(token=body.token, new_password=body.new_password, trace_id=request.state.trace_id, policy_version=dependencies.settings.policy_version)
        return {"data": {"status": "reset"}, "meta": {"trace_id": request.state.trace_id}}

    def account_access(request, *, write=False):
        _principal(request, dependencies)
        token, kind = _credential(request)
        if write and kind is ClientKind.WEB: _require_validated_web_csrf(request, dependencies.settings)
        return dict(access_token=token, trace_id=request.state.trace_id, policy_version=dependencies.settings.policy_version)

    def account_response(request, result):
        response = JSONResponse({"data": result, "meta": {"trace_id": request.state.trace_id}})
        response.headers["ETag"] = '"account:' + hashlib.sha256(json.dumps(result, sort_keys=True).encode()).hexdigest() + '"'
        return response

    @app.get("/api/v1/account/profile")
    async def get_account_profile(request: Request):
        _require_query_keys(request, frozenset())
        result = await asyncio.to_thread(account_settings_service.get_profile, **account_access(request))
        return account_response(request, result)

    @app.patch("/api/v1/account/profile")
    async def save_account_profile(body: AccountProfileBody, request: Request, idempotency_key: str = Header(alias="Idempotency-Key"),
        expected_session_id: str = Header(alias="X-Daon-Expected-Session-Id", min_length=1, max_length=128, pattern=r"^[A-Za-z0-9][A-Za-z0-9._:-]{0,127}$")):
        _require_query_keys(request, frozenset())
        if len(request.headers.getlist("x-daon-expected-session-id")) != 1: raise IdentityError("ACCOUNT_INPUT_INVALID", 422)
        result = await asyncio.to_thread(account_settings_service.save_profile, **account_access(request, write=True),
            display_name=body.display_name, expected_version=body.expected_version, idempotency_key=idempotency_key, expected_session_id=expected_session_id)
        return account_response(request, result)

    @app.get("/api/v1/account/devices")
    async def list_account_devices(request: Request, limit: int = Query(default=20, ge=1, le=100), cursor: str | None = Query(default=None, max_length=2048)):
        _require_query_keys(request, frozenset({"limit", "cursor"}))
        result = await asyncio.to_thread(account_settings_service.list_devices, **account_access(request), limit=limit, cursor=cursor)
        return account_response(request, result)

    @app.get("/api/v1/account/sessions")
    async def list_account_sessions(request: Request, limit: int = Query(default=20, ge=1, le=100), cursor: str | None = Query(default=None, max_length=2048)):
        _require_query_keys(request, frozenset({"limit", "cursor"}))
        result = await asyncio.to_thread(account_settings_service.list_sessions, **account_access(request), limit=limit, cursor=cursor)
        return account_response(request, result)

    @app.post("/api/v1/devices/{id}/trust")
    async def trust_account_device(id: str, body: AccountTrustBody, request: Request, idempotency_key: str = Header(alias="Idempotency-Key")):
        _require_query_keys(request, frozenset())
        result = await asyncio.to_thread(account_settings_service.trust_device, **account_access(request, write=True), device_id=id, idempotency_key=idempotency_key)
        # Preserve the existing public Versioned Resource success envelope.
        response = JSONResponse({"data": {"id": result["device_id"], "kind": "device", "version": result["version"],
            "attributes": {"state": "trusted", "replayed": result["replayed"]}}, "trace_id": request.state.trace_id})
        response.headers["ETag"] = '"device:' + result["version"] + '"'
        return response

    def account_revocation_response(request, result, current):
        replayed = result.pop("replayed")
        result["state"] = "revoked"
        response = JSONResponse({"data": result, "meta": {"trace_id": request.state.trace_id, "replayed": replayed}})
        response.headers["ETag"] = '"account:' + hashlib.sha256(json.dumps(result, sort_keys=True).encode()).hexdigest() + '"'
        if current:
            response.delete_cookie(WEB_SESSION_COOKIE, path="/", secure=True, httponly=True, samesite="lax")
        return response

    @app.post("/api/v1/devices/{id}/revoke")
    async def revoke_account_device(id: str, body: AccountDeviceRevokeBody, request: Request, idempotency_key: str = Header(alias="Idempotency-Key")):
        _require_query_keys(request, frozenset())
        options = account_access(request, write=True)
        principal = _principal(request, dependencies)
        result = await asyncio.to_thread(account_settings_service.revoke_device, **options, device_id=id,
            step_up_authorization=body.step_up_authorization, idempotency_key=idempotency_key)
        return account_revocation_response(request, result, id == principal.device_id)

    @app.post("/api/v1/session/revoke")
    async def revoke_account_session(body: AccountSessionRevokeBody, request: Request, idempotency_key: str = Header(alias="Idempotency-Key")):
        _require_query_keys(request, frozenset())
        options = account_access(request, write=True)
        principal = _principal(request, dependencies)
        result = await asyncio.to_thread(account_settings_service.revoke_session, **options, session_id=body.session_id,
            step_up_authorization=body.step_up_authorization, idempotency_key=idempotency_key)
        return account_revocation_response(request, result, body.session_id == principal.session_id)

    @app.post("/api/v1/auth/password/change")
    async def change_current_password(body: PasswordChangeBody, request: Request) -> JSONResponse:
        _require_validated_web_csrf(request, dependencies.settings)
        token, expected_kind = _credential(request)
        if expected_kind is not ClientKind.WEB:
            raise IdentityError("ACCESS_INVALID", 401)
        dependencies.identity_service.change_current_password(
            access_token=token,
            current_password=body.current_password,
            new_password=body.new_password,
            trace_id=request.state.trace_id,
            policy_version=dependencies.settings.policy_version,
        )
        response = JSONResponse({
            "data": {"status": "password_changed"},
            "meta": {"trace_id": request.state.trace_id},
        })
        response.delete_cookie(
            WEB_SESSION_COOKIE, path="/", secure=True, httponly=True, samesite="lax",
        )
        return response

    @app.post("/api/v1/auth/login")
    async def login(body: LoginBody, request: Request) -> JSONResponse:
        credentials = dependencies.identity_service.local_login(
            login_id=body.login_id, password=body.password, platform=DevicePlatform.WEB,
            trace_id=request.state.trace_id, policy_version=dependencies.settings.policy_version,
        )
        workspace_id = _ensure_personal_workspace(
            dependencies,
            tenant_id=credentials.tenant_id,
            user_id=credentials.user_id,
        )
        response = JSONResponse({"data": {"user_id": credentials.user_id, "tenant_id": credentials.tenant_id, "workspace_id": workspace_id}, "meta": {"trace_id": request.state.trace_id}})
        response.set_cookie(WEB_SESSION_COOKIE, credentials.access_token, max_age=WEB_SESSION_COOKIE_MAX_AGE, httponly=True, secure=True, samesite="lax", path="/")
        return response

    @app.post("/api/v1/auth/native/login")
    async def native_local_login(
        body: NativeLocalLoginBody, request: Request,
    ) -> dict[str, object]:
        credentials = dependencies.identity_service.local_native_login(
            login_id=body.login_id, password=body.password,
            trace_id=request.state.trace_id,
            policy_version=dependencies.settings.policy_version,
        )
        workspace_id = _ensure_personal_workspace(
            dependencies,
            tenant_id=credentials.tenant_id,
            user_id=credentials.user_id,
        )
        session = dependencies.identity_service.describe_access(
            credentials.access_token, trace_id=request.state.trace_id,
            policy_version=dependencies.settings.policy_version,
        )
        return {
            "data": {
                "user_id": credentials.user_id,
                "tenant_id": credentials.tenant_id,
                "workspace_id": workspace_id,
                "session_id": credentials.session_id,
                "device_id": credentials.device_id,
                "client_kind": ClientKind.NATIVE.value,
                "delivery": "native_https_opaque_bearer",
                "access_credential": credentials.access_token,
                "refresh_credential": credentials.refresh_token,
                "expires_at": session.expires_at.isoformat(),
            },
            "meta": {"trace_id": request.state.trace_id},
        }

    @app.post("/api/v1/session/android/transactions", status_code=201)
    async def begin_android_transaction(
        body: AndroidTransactionBody, request: Request,
    ) -> Any:
        _require_query_keys(request, frozenset())
        if request.headers.get("cookie") or request.headers.get("authorization"):
            raise IdentityError("INVALID_INPUT", 400)
        if not android_start_allowed(request):
            return _error_response(429, "RATE_LIMITED", request.state.trace_id, retryable=True)
        started = android_auth.begin(
            body.client_id, body.redirect_uri, body.state, body.code_challenge,
            body.code_challenge_method, request.state.trace_id,
        )
        return {
            "data": {
                "transaction_id": started.transaction_id,
                "authorization_url": started.authorization_url,
                "expires_at": started.expires_at.isoformat(),
            },
            "meta": {"trace_id": request.state.trace_id},
        }

    @app.post("/api/v1/session/android/authorize")
    async def authorize_android_transaction(
        body: AndroidAuthorizeBody, request: Request,
    ) -> dict[str, object]:
        _require_query_keys(request, frozenset())
        token, expected_kind = _credential(request)
        if expected_kind is not ClientKind.WEB:
            raise IdentityError("ACCESS_INVALID", 401)
        _require_validated_web_csrf(request, dependencies.settings)
        view = dependencies.identity_service.describe_access(
            token, trace_id=request.state.trace_id,
            policy_version=dependencies.settings.policy_version,
        )
        if view.client_kind is not ClientKind.WEB:
            raise IdentityError("ACCESS_INVALID", 401)
        redirect = android_auth.authorize(
            body.transaction_id, token, view.principal.tenant_id,
            request.state.trace_id,
        )
        return {
            "data": {"authorization_url": redirect.url},
            "meta": {"trace_id": request.state.trace_id},
        }

    @app.post("/api/v1/session/android/exchange", status_code=201)
    async def exchange_android_code(
        body: AndroidExchangeBody, request: Request,
    ) -> dict[str, object]:
        _require_query_keys(request, frozenset())
        if request.headers.get("cookie") or request.headers.get("authorization"):
            raise IdentityError("INVALID_INPUT", 400)
        exchanged = android_auth.exchange_with_projection(
            body.transaction_id, body.code, body.code_verifier, body.redirect_uri,
            request.state.trace_id,
        )
        credentials = exchanged.credentials
        return {
            "data": {
                "user_id": credentials.user_id,
                "tenant_id": credentials.tenant_id,
                "workspace_id": exchanged.workspace_id,
                "session_id": credentials.session_id,
                "device_id": credentials.device_id,
                "client_kind": ClientKind.NATIVE.value,
                "delivery": "native_https_opaque_bearer",
                "access_credential": credentials.access_token,
                "refresh_credential": credentials.refresh_token,
                "expires_at": exchanged.expires_at.isoformat(),
            },
            "meta": {"trace_id": request.state.trace_id},
        }

    @app.post("/api/v1/session/refresh")
    async def rotate_native_refresh(
        body: NativeRefreshBody, request: Request,
    ) -> dict[str, object]:
        credentials = dependencies.identity_service.rotate_refresh(
            body.refresh_credential,
            trace_id=request.state.trace_id,
            policy_version=dependencies.settings.policy_version,
        )
        accessible = dependencies.identity_service.selectable_tenant_workspaces(
            user_id=credentials.user_id,
        )
        workspace_id = next(
            (workspace for tenant, _, workspace in accessible if tenant == credentials.tenant_id),
            None,
        ) or dependencies.authorization_repository.primary_workspace_id(
            credentials.tenant_id
        ) or _personal_workspace_id(credentials.tenant_id)
        session = dependencies.identity_service.describe_access(
            credentials.access_token, trace_id=request.state.trace_id,
            policy_version=dependencies.settings.policy_version,
        )
        return {
            "data": {
                "user_id": credentials.user_id,
                "tenant_id": credentials.tenant_id,
                "workspace_id": workspace_id,
                "session_id": credentials.session_id,
                "device_id": credentials.device_id,
                "client_kind": ClientKind.NATIVE.value,
                "delivery": "native_https_opaque_bearer",
                "access_credential": credentials.access_token,
                "refresh_credential": credentials.refresh_token,
                "expires_at": session.expires_at.isoformat(),
            },
            "meta": {"trace_id": request.state.trace_id},
        }

    @app.post("/api/v1/session/logout")
    async def logout_current_web_session(request: Request) -> JSONResponse:
        _require_query_keys(request, frozenset())
        _require_validated_web_csrf(request, dependencies.settings)
        body = await request.body()
        if body not in {b"", b"{}"}:
            raise HTTPException(status_code=400)
        token, expected_kind = _credential(request)
        if expected_kind is not ClientKind.WEB:
            raise IdentityError("ACCESS_INVALID", 401)
        result = dependencies.identity_service.revoke_current_web_session(
            access_token=token, trace_id=request.state.trace_id,
            policy_version=dependencies.settings.policy_version,
        )
        response = JSONResponse({
            "data": {"status": "logged_out", "replayed": result.replayed},
            "meta": {"trace_id": request.state.trace_id},
        })
        response.delete_cookie(
            WEB_SESSION_COOKIE, path="/", secure=True, httponly=True, samesite="lax",
        )
        response.headers["ETag"] = '"session:logged-out"'
        return response

    @app.post("/api/v1/session/native/logout")
    async def logout_current_native_session(request: Request) -> JSONResponse:
        _require_query_keys(request, frozenset())
        body = await request.body()
        if body:
            if request.headers.get("content-type", "").split(";", 1)[0].strip().lower() != "application/json":
                raise HTTPException(status_code=400)
            try:
                parsed_body = json.loads(body)
            except (UnicodeDecodeError, ValueError) as error:
                raise HTTPException(status_code=400) from error
            if parsed_body != {} or not isinstance(parsed_body, dict):
                raise HTTPException(status_code=400)
        token, expected_kind = _credential(request)
        if expected_kind is not ClientKind.NATIVE:
            raise IdentityError("ACCESS_INVALID", 401)
        result = dependencies.identity_service.revoke_current_native_session(
            access_token=token, trace_id=request.state.trace_id,
            policy_version=dependencies.settings.policy_version,
        )
        response = JSONResponse({
            "data": {"status": "logged_out", "replayed": result.replayed},
            "meta": {"trace_id": request.state.trace_id},
        })
        response.headers["ETag"] = '"session:logged-out"'
        return response

    @app.post("/api/v1/admin/users", status_code=201)
    async def create_admin_user(
        body: AdminUserCreateBody,
        request: Request,
        idempotency_key: str = Header(alias="Idempotency-Key"),
    ) -> dict[str, object]:
        _require_query_keys(request, frozenset())
        if request.headers.get("x-daon-bff-transport") != "internal":
            raise IdentityError("CSRF_VALIDATION_FAILED", 403)
        principal = _principal(request, dependencies)
        if dependencies.admin_user_service is None:
            raise IdentityError("PERSISTENCE_UNAVAILABLE", 503)
        if license_enforcement_enabled:
            license_service.require_user_capacity(
                _license_context(principal, principal.tenant_id, request, dependencies)
            )
        result = dependencies.admin_user_service.create_user(
            principal, login_id=body.login_id, email=body.email,
            initial_password=body.initial_password, idempotency_key=idempotency_key,
            trace_id=request.state.trace_id, policy_version=dependencies.settings.policy_version,
        )
        user = result.user
        return {"data": {"user": {
            "user_id": user.user_id, "login_id": user.login_id, "email": user.email,
            "has_email": user.has_email, "state": user.state, "protected": user.protected,
        }, "replayed": result.replayed}, "meta": {"trace_id": request.state.trace_id}}

    @app.patch("/api/v1/admin/users/{user_id}")
    async def update_admin_user(
        user_id: str,
        body: AdminUserUpdateBody,
        request: Request,
        idempotency_key: str = Header(alias="Idempotency-Key"),
    ) -> dict[str, object]:
        _require_query_keys(request, frozenset())
        if request.headers.get("x-daon-bff-transport") != "internal":
            raise IdentityError("CSRF_VALIDATION_FAILED", 403)
        principal = _principal(request, dependencies)
        if dependencies.admin_user_service is None:
            raise IdentityError("PERSISTENCE_UNAVAILABLE", 503)
        result = dependencies.admin_user_service.update_user(
            principal, user_id=user_id, email=body.email, idempotency_key=idempotency_key,
            trace_id=request.state.trace_id, policy_version=dependencies.settings.policy_version,
        )
        user = result.user
        return {"data": {"user": {
            "user_id": user.user_id, "login_id": user.login_id, "email": user.email,
            "has_email": user.has_email, "state": user.state, "protected": user.protected,
        }, "replayed": result.replayed}, "meta": {"trace_id": request.state.trace_id}}

    @app.post("/api/v1/admin/users/{user_id}/password-reset", status_code=202)
    async def request_admin_user_password_reset(
        user_id: str,
        request: Request,
        idempotency_key: str = Header(alias="Idempotency-Key"),
    ) -> dict[str, object]:
        _require_query_keys(request, frozenset())
        if request.headers.get("x-daon-bff-transport") != "internal":
            raise IdentityError("CSRF_VALIDATION_FAILED", 403)
        principal = _principal(request, dependencies)
        if dependencies.admin_user_service is None:
            raise IdentityError("PERSISTENCE_UNAVAILABLE", 503)
        result = dependencies.admin_user_service.request_password_reset(
            principal,
            user_id=user_id,
            idempotency_key=idempotency_key,
            trace_id=request.state.trace_id,
            policy_version=dependencies.settings.policy_version,
        )
        return {
            "data": {"status": result.status, "replayed": result.replayed},
            "meta": {"trace_id": request.state.trace_id},
        }

    @app.post("/api/v1/admin/users/{user_id}/approve")
    async def approve_admin_user(
        user_id: str,
        request: Request,
        idempotency_key: str = Header(alias="Idempotency-Key"),
    ) -> dict[str, object]:
        _require_query_keys(request, frozenset())
        if request.headers.get("x-daon-bff-transport") != "internal":
            raise IdentityError("CSRF_VALIDATION_FAILED", 403)
        principal = _principal(request, dependencies)
        if dependencies.admin_user_service is None:
            raise IdentityError("PERSISTENCE_UNAVAILABLE", 503)
        result = dependencies.admin_user_service.approve_user(
            principal, user_id=user_id, idempotency_key=idempotency_key,
            trace_id=request.state.trace_id, policy_version=dependencies.settings.policy_version,
        )
        user = result.user
        return {"data": {"user": {
            "user_id": user.user_id, "login_id": user.login_id, "email": user.email,
            "has_email": user.has_email, "state": user.state, "protected": user.protected,
        }, "replayed": result.replayed}, "meta": {"trace_id": request.state.trace_id}}

    @app.delete("/api/v1/admin/users/{user_id}")
    async def delete_admin_user(
        user_id: str,
        request: Request,
        idempotency_key: str = Header(alias="Idempotency-Key"),
    ) -> dict[str, object]:
        _require_query_keys(request, frozenset())
        if request.headers.get("x-daon-bff-transport") != "internal":
            raise IdentityError("CSRF_VALIDATION_FAILED", 403)
        principal = _principal(request, dependencies)
        if dependencies.admin_user_service is None:
            raise IdentityError("PERSISTENCE_UNAVAILABLE", 503)
        result = dependencies.admin_user_service.delete_user(
            principal, user_id=user_id, idempotency_key=idempotency_key,
            trace_id=request.state.trace_id, policy_version=dependencies.settings.policy_version,
        )
        return {"data": {"user_id": result.user_id, "replayed": result.replayed},
                "meta": {"trace_id": request.state.trace_id}}

    @app.get("/api/v1/admin/users")
    async def list_admin_users(request: Request) -> dict[str, object]:
        _require_query_keys(request, frozenset())
        principal = _principal(request, dependencies)
        if dependencies.admin_user_service is None:
            raise IdentityError("PERSISTENCE_UNAVAILABLE", 503)
        users = dependencies.admin_user_service.list_users(principal)
        return {
            "data": {
                "users": [
                    {
                        "user_id": user.user_id,
                        "login_id": user.login_id,
                        "email": user.email,
                        "has_email": user.has_email,
                        "state": user.state,
                        "protected": user.protected,
                    }
                    for user in users
                ]
            },
            "meta": {"trace_id": request.state.trace_id},
        }

    @app.get("/api/v1/admin/tenants")
    async def list_admin_tenants(request: Request) -> dict[str, object]:
        _require_query_keys(request, frozenset())
        principal = _principal(request, dependencies)
        service = dependencies.admin_membership_role_service
        if service is None:
            raise AuthorizationError("PERSISTENCE_UNAVAILABLE", 503)
        tenants = service.list_tenants(principal)
        return {
            "data": {"tenants": [{"tenant_id": tenant_id, "name": name} for tenant_id, name in tenants]},
            "meta": {"trace_id": request.state.trace_id},
        }

    @app.get("/api/v1/admin/tenants/{tenant_id}/users/{user_id}/memberships")
    async def list_admin_effective_memberships(
        tenant_id: str, user_id: str, request: Request,
    ) -> dict[str, object]:
        _require_query_keys(request, frozenset())
        principal = _principal(request, dependencies)
        service = dependencies.admin_membership_role_service
        if service is None:
            raise AuthorizationError("PERSISTENCE_UNAVAILABLE", 503)
        result = service.list_effective_memberships(principal, tenant_id, user_id)
        return {
            "data": {
                "tenant": None if result.tenant is None else {
                    "tenant_id": result.tenant.tenant_id, "role": result.tenant.role.value,
                    "state": result.tenant.state, "version": result.tenant.version,
                },
                "workspaces": [{
                    "workspace_id": item.workspace_id, "role": item.role.value,
                    "state": item.state, "version": item.version,
                } for item in result.workspaces],
                "target_is_system_admin": user_id in dependencies.settings.system_admin_user_ids,
            },
            "meta": {"trace_id": request.state.trace_id},
        }

    @app.patch("/api/v1/admin/tenants/{tenant_id}/workspaces/{workspace_id}/memberships/{user_id}/role")
    async def change_admin_workspace_role(
        tenant_id: str, workspace_id: str, user_id: str, request: Request,
        idempotency_key: str | None = Header(default=None, alias="Idempotency-Key"),
    ) -> dict[str, object]:
        _require_query_keys(request, frozenset())
        _require_validated_web_csrf(request, dependencies.settings)
        principal = _principal(request, dependencies)
        if principal.user_id not in dependencies.settings.system_admin_user_ids:
            raise AuthorizationError("ACTION_DENIED", 403)
        service = dependencies.admin_membership_role_service
        if service is None:
            raise AuthorizationError("PERSISTENCE_UNAVAILABLE", 503)
        try:
            body = await request.json()
        except (ValueError, UnicodeDecodeError) as error:
            raise AuthorizationError("INVALID_INPUT", 422) from error
        if not isinstance(body, dict) or set(body) != {"role", "expected_version", "reason"}:
            raise AuthorizationError("INVALID_INPUT", 422)
        role_name, version, reason = body["role"], body["expected_version"], body["reason"]
        if not isinstance(role_name, str) or role_name not in {item.value for item in WORKSPACE_ROLES}:
            raise AuthorizationError("INVALID_ROLE_SCOPE", 422)
        if isinstance(version, bool) or not isinstance(version, int) or version < 1:
            raise AuthorizationError("INVALID_INPUT", 422)
        if not isinstance(reason, str) or reason not in REASON_CODES or idempotency_key is None:
            raise AuthorizationError("INVALID_INPUT", 422)
        result = service.change_existing_workspace_role(
            principal, tenant_id, workspace_id, user_id, Role(role_name), version,
            idempotency_key, reason,
        )
        return {
            "data": {
                "tenant_id": result.tenant_id, "workspace_id": result.workspace_id,
                "user_id": result.user_id, "role": result.role.value, "state": result.state,
                "version": result.version, "acl_version": result.acl_version,
                "replayed": result.replayed,
            },
            "meta": {"trace_id": request.state.trace_id},
        }

    @app.patch("/api/v1/admin/users/{user_id}/state")
    async def change_admin_user_state(
        user_id: str,
        body: AdminUserStateBody,
        request: Request,
        idempotency_key: str = Header(alias="Idempotency-Key"),
    ) -> dict[str, object]:
        _require_query_keys(request, frozenset())
        if request.headers.get("x-daon-bff-transport") != "internal":
            raise IdentityError("CSRF_VALIDATION_FAILED", 403)
        principal = _principal(request, dependencies)
        if dependencies.admin_user_service is None:
            raise IdentityError("PERSISTENCE_UNAVAILABLE", 503)
        result = dependencies.admin_user_service.change_state(
            principal,
            user_id=user_id,
            state=body.state,
            idempotency_key=idempotency_key,
            trace_id=request.state.trace_id,
            policy_version=dependencies.settings.policy_version,
        )
        user = result.user
        return {
            "data": {
                "user": {
                    "user_id": user.user_id,
                    "login_id": user.login_id,
                    "email": user.email,
                    "has_email": user.has_email,
                    "state": user.state,
                    "protected": user.protected,
                },
                "replayed": result.replayed,
            },
            "meta": {"trace_id": request.state.trace_id},
        }

    def website_context(principal, workspace_id, notebook_id, request):
        return WebsiteImportContext(principal.tenant_id, workspace_id, notebook_id,
            principal.user_id, request.state.trace_id, dependencies.settings.policy_version)

    def website_access(context, request, action):
        if hasattr(request.state,'website_deadline') and time.monotonic()>=request.state.website_deadline:
            raise WebsiteImportError('WEBSITE_FETCH_TIMEOUT',504)
        principal = _principal(request, dependencies)
        if principal.tenant_id != context.tenant_id or principal.user_id != context.actor_id:
            raise WebsiteImportError("WEBSITE_ACCESS_DENIED", 403)
        grant = dependencies.authorization_service.authorize_action(
            principal=principal, workspace_id=context.workspace_id, action=action,
            trace_id=context.trace_id, policy_version=context.policy_version)
        notebook_service.get(notebook_context(principal, context.workspace_id, request), context.notebook_id)
        return principal, grant

    def website_service(context, request):
        if (dependencies.settings.cloud_database_dsn is None or dependencies.object_queue_store is None
                or dependencies.website_storage is None or egress_policy_service is None):
            raise WebsiteImportError("WEBSITE_STORAGE_UNAVAILABLE")
        frozen = []
        def current_policy(ctx):
            principal, grant = website_access(ctx, request, Action.EDIT)
            effective = egress_policy_service.get_effective(
                _egress_policy_context(principal, ctx.workspace_id, request, dependencies))
            approvers = {"workspace_manager": {Role.PERSONAL_OWNER, Role.WORKSPACE_ADMIN, Role.ORGANIZATION_ADMIN},
                "organization_admin": {Role.PERSONAL_OWNER, Role.ORGANIZATION_ADMIN}}
            if (effective.mode != "allow_approved_external" or effective.classification != "public"
                    or "external_api" not in effective.allowed_provider_kinds or effective.max_bytes <= 0
                    or grant.role not in approvers.get(effective.required_approver, set())):
                raise WebsiteImportError("WEBSITE_FETCH_BLOCKED", 403)
            if frozen and frozen[0] != effective.fingerprint:
                raise WebsiteImportError("WEBSITE_FETCH_BLOCKED", 403)
            if not frozen:
                frozen.append(effective.fingerprint)
            return effective
        repo = _WebsiteRequestRepository(dependencies.settings.cloud_database_dsn,request_deadline=request.state.website_deadline,
            canon_store=PostgresDataCanonStore(dependencies.settings.cloud_database_dsn,
                creation_enforcer=enforce_license_creation if license_enforcement_enabled else None),
            policy_fingerprint=lambda ctx: current_policy(ctx).fingerprint)
        def fetch_page(ctx, url):
            effective = current_policy(ctx)
            policy = WebsiteFetchPolicy(effective.fingerprint, effective.allowed_destinations, min(2097152, effective.max_bytes))
            def authorize_hop(hop, captured):
                now = current_policy(ctx)
                outbound = _website_outbound(hop)
                host = urlsplit(hop).hostname
                if (now.fingerprint != captured.fingerprint or host not in now.allowed_destinations
                        or len(outbound['url'].encode("utf-8")) > now.max_bytes):
                    raise WebsiteImportError("WEBSITE_FETCH_BLOCKED", 403)
            return _website_public_page(InternetConnector().fetch_public_page(ctx, url, policy, SafeHttpTransport(), authorize_hop))
        def authorize(ctx):
            current_policy(ctx)
        return WebsiteSourceImportService(repo, _WebsiteRequestPromoter(dependencies.object_queue_store, dependencies.website_storage,request_deadline=request.state.website_deadline),
            fetch_page=fetch_page, authorize_context=authorize), repo

    @app.exception_handler(WebsiteImportError)
    async def website_error(request, error):
        response=_error_response(error.status,error.code,request.state.trace_id,retryable=error.status>=500)
        response.headers['X-Content-Type-Options']='nosniff'
        return response

    @app.exception_handler(OpenLawImportError)
    async def law_error(request,error):
        response=_error_response(error.status,error.code,request.state.trace_id,retryable=error.status>=500)
        response.headers['X-Content-Type-Options']='nosniff'
        return response

    def law_json(content,*,status=200):
        try:
            response=_website_json_response(content,maximum=4*1024*1024,status=status)
            response.headers['ETag']='"law:'+hashlib.sha256(response.body).hexdigest()+'"'
            return response
        except WebsiteImportError:raise OpenLawImportError('OPEN_LAW_CONTENT_TOO_LARGE') from None

    def law_access(ctx,request,action):
        if getattr(request.state,'law_cancelled',False) or time.monotonic()>=request.state.law_deadline:raise OpenLawImportError('OPEN_LAW_TIMEOUT')
        principal=_principal(request,dependencies)
        if principal.tenant_id!=ctx.tenant_id or principal.user_id!=ctx.actor_id or not open_law_enabled(ctx):
            raise OpenLawImportError('OPEN_LAW_ACCESS_DENIED')
        grant=dependencies.authorization_service.authorize_action(principal=principal,workspace_id=ctx.workspace_id,action=action,
            trace_id=ctx.trace_id,policy_version=ctx.policy_version)
        notebook_service.get(notebook_context(principal,ctx.workspace_id,request),ctx.notebook_id)
        try:view=scoped_open_law(principal,ctx.workspace_id)
        except ConnectorError:raise OpenLawImportError('OPEN_LAW_NOT_FOUND') from None
        if view.status=='disconnected':raise OpenLawImportError('OPEN_LAW_UNAVAILABLE')
        return principal,grant

    def law_policy(ctx,request,frozen,action=Action.EDIT):
        principal,grant=law_access(ctx,request,action)
        if egress_policy_service is None:raise OpenLawImportError('OPEN_LAW_EGRESS_BLOCKED')
        effective=egress_policy_service.get_effective(_egress_policy_context(principal,ctx.workspace_id,request,dependencies))
        approvers={'workspace_manager':{Role.PERSONAL_OWNER,Role.ORGANIZATION_ADMIN,Role.WORKSPACE_ADMIN},
            'organization_admin':{Role.PERSONAL_OWNER,Role.ORGANIZATION_ADMIN}}
        if (effective.mode!='allow_approved_external' or effective.classification!='public'
            or 'external_api' not in effective.allowed_provider_kinds or 'www.law.go.kr' not in effective.allowed_destinations
            or effective.max_bytes<=0 or grant.role not in approvers.get(effective.required_approver,set())
            or frozen and frozen[0]!=effective.fingerprint):raise OpenLawImportError('OPEN_LAW_EGRESS_BLOCKED')
        # Source-free public law parameters and fixed headers only; privacy
        # flags remain unchanged, no Source/question/session content is sent.
        if not frozen:frozen.append(effective.fingerprint)
        return effective

    def law_client(ctx,request,frozen):
        effective=law_policy(ctx,request,frozen)
        def delegate():
            return dependencies.open_law_client or OpenLawMcpClient(api_key=os.environ.get('DAON_OPEN_LAW_API_KEY'),enabled=True,qa_workspace_id=ctx.workspace_id,
                authorize_context=lambda current:law_policy(current,request,frozen).fingerprint,
                max_raw_bytes=min(2097152,effective.max_bytes),
                timeout_seconds=min(10,max(.001,request.state.law_deadline-time.monotonic())))
        def invoke(current,tool,inputs,function,*args):
            law_policy(current,request,frozen);started=time.monotonic();code='OPEN_LAW_TOOL_SUCCEEDED'
            principal=_principal(request,dependencies)
            observed=connector_repository.law_verification(current.tenant_id,current.workspace_id,current.actor_id)[2] if connector_repository is not None else scoped_open_law(principal,current.workspace_id)
            outcome=AuditOutcome.SUCCEEDED
            try:
                value=function(current,*args);law_policy(current,request,frozen)
                return value
            except (OpenLawError,OpenLawImportError) as error:
                code=OpenLawImportError(str(error)).code;outcome=AuditOutcome.FAILED
                if code in {'OPEN_LAW_CREDENTIAL_REQUIRED','OPEN_LAW_UPSTREAM_FAILED'}:
                    if connector_repository is not None:
                        connector_repository.mark_law_unavailable(current.tenant_id,current.workspace_id,current.actor_id,code,observed)
                    elif open_law_registrations.get((current.tenant_id,current.workspace_id)) is observed and observed.status=='connected':
                        open_law_registrations[(current.tenant_id,current.workspace_id)]=replace(observed,status='unavailable',error_code=code,
                            last_checked_at=datetime.now(timezone.utc).isoformat().replace('+00:00','Z'))
                        open_law_receipts.pop((current.tenant_id,current.workspace_id),None)
                raise
            except Exception:
                code='OPEN_LAW_UPSTREAM_FAILED';outcome=AuditOutcome.FAILED
                raise OpenLawImportError(code) from None
            finally:
                dependencies.audit_store.append(AuditEventDraft(event_id='law-tool-'+secrets.token_hex(16),occurred_at=datetime.now(timezone.utc),
                    actor_id=current.actor_id,actor_type=ActorType.USER,tenant_id=current.tenant_id,workspace_id=current.workspace_id,
                    action='open_law.tool',target_type='connector',target_id='mcp-open-law-go-kr',outcome=outcome,
                    trace_id=current.trace_id,policy_version=current.policy_version,metadata={'notebook_id':current.notebook_id,
                        'destination_id':'www.law.go.kr','tool_id':tool,'input_fingerprint':hashlib.sha256(canonical_json_bytes(inputs)).hexdigest(),
                        'policy_fingerprint':frozen[0],'reason_code':code,'elapsed_ms':max(0,int((time.monotonic()-started)*1000))}))
        class ScopedClient:
            def search(self,current,query,page):return invoke(current,'search_law',{'query':query,'page':page},delegate().search,query,page)
            def read(self,current,selection):return invoke(current,'get_law_text',_dataclass_json(selection),delegate().read,selection)
            def probe(self,current):return invoke(current,'search_law',{'query':'민법','page':1},delegate().probe)
        return ScopedClient()

    def law_verified(ctx,principal):
        if connector_repository is not None:
            view,receipt,_=connector_repository.law_verification(ctx.tenant_id,ctx.workspace_id,ctx.actor_id)
        else:
            view=scoped_open_law(principal,ctx.workspace_id);receipt=open_law_receipts.get((ctx.tenant_id,ctx.workspace_id))
        if (view.status!='connected' or not isinstance(receipt,dict) or set(receipt)!={'protocol_version','tool_schema_digest','checked_at'}
            or receipt['protocol_version']!=LAW_PROTOCOL or receipt['tool_schema_digest']!=LAW_SCHEMA_DIGEST):raise OpenLawImportError('OPEN_LAW_UNAVAILABLE')

    def law_repository(ctx,request,frozen):
        if dependencies.open_law_repository is not None:return dependencies.open_law_repository,False
        dsn=dependencies.settings.cloud_database_dsn
        if dsn is None:raise OpenLawImportError('OPEN_LAW_STORAGE_UNAVAILABLE')
        return _OpenLawRequestRepository(dsn,request_deadline=request.state.law_deadline,
            cancelled=lambda:getattr(request.state,'law_cancelled',False),
            canon_store=PostgresDataCanonStore(dsn,creation_enforcer=enforce_license_creation if license_enforcement_enabled else None),
            policy_fingerprint=lambda current:law_policy(current,request,frozen).fingerprint),True

    def law_service(ctx,request,frozen):
        principal,_=law_access(ctx,request,Action.EDIT);law_verified(ctx,principal)
        client=law_client(ctx,request,frozen)
        if dependencies.open_law_import_service is not None:return dependencies.open_law_import_service,None
        if dependencies.object_queue_store is None or dependencies.website_storage is None:raise OpenLawImportError('OPEN_LAW_STORAGE_UNAVAILABLE')
        repo,owned=law_repository(ctx,request,frozen)
        def authorize(current):law_policy(current,request,frozen)
        return OpenLawSourceImportService(repo,_OpenLawRequestPromoter(dependencies.object_queue_store,dependencies.website_storage,request_deadline=request.state.law_deadline),
            client=client,authorize_context=authorize),repo if owned else None

    async def law_body(request,keys):
        if request.query_params:raise OpenLawImportError('OPEN_LAW_INPUT_INVALID')
        try:
            if request.headers.get('content-type','').split(';',1)[0].strip().lower()!='application/json':raise ValueError
            raw=bytearray()
            async for chunk in request.stream():
                if len(raw)+len(chunk)>32768:raise ValueError
                raw.extend(chunk)
            body=json.loads(raw.decode('utf-8',errors='strict'),object_pairs_hook=_website_json_pairs)
            if not isinstance(body,dict) or set(body)!=set(keys) or not isinstance(body.get('notebook_id'),str) or not re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9._:-]{0,127}',body['notebook_id']):raise ValueError
            return body
        except (ValueError,TypeError,UnicodeError):raise OpenLawImportError('OPEN_LAW_INPUT_INVALID') from None

    async def law_call(function,*args):
        try:return await asyncio.to_thread(function,*args)
        except OpenLawError as error:raise OpenLawImportError(str(error)) from None

    async def law_request_context(id,body,request):
        principal=_principal(request,dependencies)
        ctx=website_context(principal,id,body['notebook_id'],request)
        await asyncio.to_thread(law_access,ctx,request,Action.EDIT)
        return ctx,principal

    @app.post('/api/v1/workspaces/{id}/connectors/mcp-open-law-go-kr/law-search',operation_id='searchOpenLaw')
    async def search_open_law(id:str,request:Request):
        body=await law_body(request,{'notebook_id','query','page','public_query_confirmed'})
        try:
            validate_search(body['query'],body['page'])
            if body['public_query_confirmed'] is not True:raise ValueError
        except (OpenLawError,ValueError):raise OpenLawImportError('OPEN_LAW_INPUT_INVALID') from None
        ctx,principal=await law_request_context(id,body,request);frozen=[]
        client=law_client(ctx,request,frozen)
        if connector_repository is not None:
            view,receipt,registration=await asyncio.to_thread(connector_repository.law_verification,ctx.tenant_id,id,ctx.actor_id)
        else:
            view=scoped_open_law(principal,id);registration=view
            receipt=open_law_receipts.get((ctx.tenant_id,id))
        if view.status!='connected' or not isinstance(receipt,dict) or receipt.get('protocol_version')!=LAW_PROTOCOL or receipt.get('tool_schema_digest')!=LAW_SCHEMA_DIGEST:
            if view.status=='disconnected':raise OpenLawImportError('OPEN_LAW_UNAVAILABLE')
            receipt=await law_call(client.probe,ctx)
            if (not isinstance(receipt,dict) or set(receipt)!={'protocol_version','tool_schema_digest','checked_at'}
                or receipt.get('protocol_version')!=LAW_PROTOCOL or receipt.get('tool_schema_digest')!=LAW_SCHEMA_DIGEST
                or not isinstance(receipt.get('checked_at'),str) or not re.fullmatch(r'\d{4}-\d{2}-\d{2}T.{1,48}',receipt['checked_at'])):raise OpenLawImportError('OPEN_LAW_PROTOCOL_INVALID')
            law_policy(ctx,request,frozen)
            if connector_repository is not None:
                await asyncio.to_thread(connector_repository.verify_law,ctx.tenant_id,id,ctx.actor_id,receipt,registration)
            else:
                if scoped_open_law(principal,id) is not registration:raise OpenLawImportError('OPEN_LAW_UNAVAILABLE')
                open_law_receipts[(ctx.tenant_id,id)]=receipt
                set_scoped_open_law(principal,id,'connected',None)
        law_policy(ctx,request,frozen)
        result=await law_call(client.search,ctx,body['query'],body['page'])
        law_policy(ctx,request,frozen)
        try:validate_search_result(result,body['page'])
        except OpenLawError:raise OpenLawImportError('OPEN_LAW_PROTOCOL_INVALID') from None
        return law_json({'data':result,'meta':{'trace_id':ctx.trace_id}})

    @app.post('/api/v1/workspaces/{id}/connectors/mcp-open-law-go-kr/law-preview',operation_id='previewOpenLaw')
    async def preview_open_law(id:str,request:Request):
        body=await law_body(request,{'notebook_id','mst','effective_date','article_code'})
        try:selection=OpenLawSelection(body['mst'],body['effective_date'],body['article_code'])
        except OpenLawError:raise OpenLawImportError('OPEN_LAW_INPUT_INVALID') from None
        ctx,_=await law_request_context(id,body,request);frozen=[];service,owned=law_service(ctx,request,frozen)
        try:
            document=await law_call(service.preview,ctx,selection);law_policy(ctx,request,frozen)
            return law_json({'data':{'law':document.law,'text':document.text_content.decode('utf-8'),'raw_sha256':document.raw_sha256,
                'text_sha256':document.text_sha256,'fetched_at':document.fetched_at,'extraction_version':document.extraction_version},'meta':{'trace_id':ctx.trace_id}})
        finally:
            if owned is not None:owned.close()

    @app.post('/api/v1/workspaces/{id}/connectors/mcp-open-law-go-kr/law-imports',operation_id='importOpenLaw')
    async def import_open_law(id:str,request:Request):
        body=await law_body(request,{'notebook_id','mst','effective_date','article_code','expected_raw_sha256','expected_text_sha256'})
        key=request.headers.get('idempotency-key','')
        if not re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9._:-]{0,127}',key) or any(not isinstance(body[k],str) or not re.fullmatch('[0-9a-f]{64}',body[k]) for k in ('expected_raw_sha256','expected_text_sha256')):raise OpenLawImportError('OPEN_LAW_INPUT_INVALID')
        try:selection=OpenLawSelection(body['mst'],body['effective_date'],body['article_code'])
        except OpenLawError:raise OpenLawImportError('OPEN_LAW_INPUT_INVALID') from None
        ctx,_=await law_request_context(id,body,request);frozen=[];service,owned=law_service(ctx,request,frozen)
        try:
            result=await law_call(service.import_selection,ctx,selection,body['expected_raw_sha256'],body['expected_text_sha256'],key)
            law_policy(ctx,request,frozen)
            return law_json({'data':{'kind':'open_law_import','id':result.snapshot_id,'version':'1','attributes':_dataclass_json(result)},'meta':{'trace_id':ctx.trace_id}},status=200 if result.replayed else 201)
        finally:
            if owned is not None:owned.close()

    @app.get('/api/v1/workspaces/{id}/source-versions/{version_id}/law-snapshot',operation_id='getOpenLawSnapshot')
    async def get_open_law_snapshot(id:str,version_id:str,request:Request):
        if set(request.query_params)!={'notebook_id'} or len(request.query_params.getlist('notebook_id'))!=1 or not re.fullmatch(r'law-version-[0-9a-f]{32}',version_id):raise OpenLawImportError('OPEN_LAW_INPUT_INVALID')
        nb=request.query_params['notebook_id']
        if not re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9._:-]{0,127}',nb):raise OpenLawImportError('OPEN_LAW_INPUT_INVALID')
        principal=_principal(request,dependencies);ctx=website_context(principal,id,nb,request);frozen=[]
        law_policy(ctx,request,frozen,Action.VIEW);law_verified(ctx,principal);repo,owned=law_repository(ctx,request,frozen)
        try:
            def read():
                snapshot=repo.get_snapshot(ctx,version_id)
                with repo._transaction(ctx) as conn:
                    require_open_law_source_access(ctx,version_id,conn)
                    row=conn.execute('SELECT o.object_key,o.digest_sha256,o.byte_size FROM open_law_source_snapshots w '
                        'JOIN object_records o ON o.tenant_id=w.tenant_id AND o.workspace_id=w.workspace_id AND o.object_id=w.text_object_id '
                        "WHERE w.tenant_id=%s AND w.workspace_id=%s AND w.created_by=%s AND w.notebook_id=%s AND w.source_version_id=%s "
                        "AND o.created_by=w.created_by AND o.status='completed' AND o.content_type='text/plain' AND o.digest_sha256=w.text_sha256",
                        (*repo._scope(ctx),version_id)).fetchone()
                if row is None or row[1]!=snapshot.text_sha256:raise OpenLawImportError('OPEN_LAW_STORAGE_UNAVAILABLE')
                raw=_read_open_law_original(ctx,*row,dependencies.website_storage,request_deadline=request.state.law_deadline)
                if repo.get_snapshot(ctx,version_id)!=snapshot:raise OpenLawImportError('OPEN_LAW_ACCESS_DENIED')
                return snapshot,raw.decode('utf-8',errors='strict')
            snapshot,text=await asyncio.to_thread(read)
            law_policy(ctx,request,frozen,Action.VIEW)
            return law_json({'data':{'snapshot':_dataclass_json(snapshot),'text':text},'meta':{'trace_id':ctx.trace_id}})
        finally:
            if owned:repo.close()

    @app.post("/api/v1/workspaces/{id}/website-imports",operation_id="importWebsiteSource",openapi_extra={
        'requestBody':{'required':True,'content':{'application/json':{'schema':{
            'type':'object','additionalProperties':False,'required':['notebook_id','url','public_page_confirmed'],
            'properties':{'notebook_id':{'type':'string','minLength':1,'maxLength':128},
                'url':{'type':'string','minLength':1,'maxLength':2048},
                'public_page_confirmed':{'type':'boolean','const':True}}}}}}})
    async def import_website(id: str, request: Request, idempotency_key: str = Header(alias="Idempotency-Key")):
        _require_query_keys(request, frozenset())
        _idempotency_key(idempotency_key)
        principal = _principal(request, dependencies)
        try:
            if request.headers.get('content-type','').split(';',1)[0].strip().lower()!='application/json':
                raise ValueError
            raw=await request.body()
            if len(raw)>32768:raise ValueError
            body = json.loads(raw.decode('utf-8',errors='strict'),object_pairs_hook=_website_json_pairs)
            if (not isinstance(body, dict) or set(body) != {"notebook_id", "url", "public_page_confirmed"}
                    or body["public_page_confirmed"] is not True or not isinstance(body["notebook_id"], str)
                    or re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._:-]{0,127}", body["notebook_id"]) is None):
                raise ValueError
            url = normalize_public_url(body["url"])
        except (ValueError, TypeError, WebsiteFetchError):
            raise WebsiteImportError("WEBSITE_URL_INVALID", 400) from None
        ctx = website_context(principal, id, body["notebook_id"], request)
        await asyncio.to_thread(website_access, ctx, request, Action.EDIT)
        service = dependencies.website_import_service
        owned = None
        if service is None:
            service, owned = website_service(ctx, request)
        try:
            result = await asyncio.to_thread(service.import_url, ctx, url, True, idempotency_key)
        finally:
            if owned is not None:
                owned.close()
        return _website_json_response({"data": {"kind": "website_import", "id": result.snapshot_id, "version": "1",
            "attributes": _dataclass_json(result)}, "meta": {"trace_id": request.state.trace_id}},
            status=200 if result.replayed else 201,headers={'ETag':f'"website:{result.snapshot_id}"'})

    @app.get("/api/v1/workspaces/{id}/source-versions/{version_id}/website-snapshot",operation_id="getWebsiteSnapshot")
    async def get_website_snapshot(id: str, version_id: str, request: Request, notebook_id: str = Query()):
        _website_query(request, {'notebook_id'})
        principal = _principal(request, dependencies)
        ctx = website_context(principal, id, notebook_id, request)
        await asyncio.to_thread(website_access, ctx, request, Action.VIEW)
        repo = dependencies.website_repository
        owned = False
        if repo is None:
            if dependencies.settings.cloud_database_dsn is None:
                raise WebsiteImportError("WEBSITE_STORAGE_UNAVAILABLE")
            repo = PostgresWebsiteSourceRepository(dependencies.settings.cloud_database_dsn)
            owned = True
        try:
            snapshot = await asyncio.to_thread(repo.get_snapshot, ctx, version_id)
            await asyncio.to_thread(website_access,ctx,request,Action.VIEW)
        finally:
            if owned:
                repo.close()
        return _website_json_response({"data": _dataclass_json(snapshot), "meta": {"trace_id": request.state.trace_id}},
            headers={'ETag':f'"website:{snapshot.snapshot_id}:{snapshot.text_sha256}"'})

    @app.get("/api/v1/workspaces/{id}/sources")
    async def list_workspace_sources(
        id: str, request: Request, notebook_id: str = Query(), website_source_version_id: str | None = Query(default=None),
    ) -> JSONResponse:
        _require_query_keys(request, frozenset({"notebook_id", "website_source_version_id"}))
        if website_source_version_id is not None:
            _website_query(request,{'notebook_id','website_source_version_id'})
        principal = _principal(request, dependencies)
        dependencies.authorization_service.authorize_action(
            principal=principal, workspace_id=id, action=Action.VIEW,
            trace_id=request.state.trace_id,
            policy_version=dependencies.settings.policy_version,
        )
        if studio_report_repository is None:
            raise StudioReportError("STUDIO_SERVICE_UNAVAILABLE", status=503, retryable=True)
        sources = await asyncio.to_thread(
            studio_report_repository.list_sources,
            _studio_context(principal, id, request, dependencies, notebook_id),
        )
        selected = await asyncio.to_thread(
            notebook_service.read_selected_context,
            notebook_context(principal, id, request), notebook_id,
        )
        selected_sources = set(selected.sources)
        sources = tuple(
            source for source in sources
            if (source.source_id, source.source_version_id) in selected_sources
        )
        content = {
            "data": {"sources": [_dataclass_json(source) for source in sources]},
            "meta": {"trace_id": request.state.trace_id, "workspace_id": id},
        }
        if website_source_version_id is not None:
            if re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9._:-]{0,127}',website_source_version_id) is None:
                raise WebsiteImportError('WEBSITE_ACCESS_DENIED',403)
            matches=[item for item in content['data']['sources'] if item['source_version_id']==website_source_version_id and item['source_state']=='ready']
            if len(matches)!=1:
                raise WebsiteImportError('WEBSITE_ACCESS_DENIED',403)
            ctx=website_context(principal,id,notebook_id,request)
            await asyncio.to_thread(website_access,ctx,request,Action.VIEW)
            repo=dependencies.website_repository
            owned=repo is None
            if owned:
                if dependencies.settings.cloud_database_dsn is None:
                    raise WebsiteImportError('WEBSITE_STORAGE_UNAVAILABLE')
                repo=PostgresWebsiteSourceRepository(dependencies.settings.cloud_database_dsn)
            try:
                snapshot=await asyncio.to_thread(repo.get_snapshot,ctx,website_source_version_id)
                if snapshot.source_id!=matches[0]['source_id']:
                    raise WebsiteImportError('WEBSITE_ACCESS_DENIED',403)
                text=await asyncio.to_thread(_read_website_original,repo,ctx,website_source_version_id,dependencies.website_storage)
                await asyncio.to_thread(website_access,ctx,request,Action.VIEW)
                current=await asyncio.to_thread(notebook_service.read_selected_context,notebook_context(_principal(request,dependencies),id,request),notebook_id)
                if (snapshot.source_id,website_source_version_id) not in set(current.sources) or await asyncio.to_thread(repo.get_snapshot,ctx,website_source_version_id)!=snapshot:
                    raise WebsiteImportError('WEBSITE_ACCESS_DENIED',403)
                matches[0]['website']={**_dataclass_json(snapshot),'text':text}
            finally:
                if owned: repo.close()
        response = _website_json_response(content,maximum=8388608) if website_source_version_id is not None else JSONResponse(content)
        response.headers["ETag"] = selected.etag
        return response

    @app.get("/api/v1/workspaces/{id}/connectors")
    async def list_connectors(id: str, request: Request) -> JSONResponse:
        _require_query_keys(request, frozenset())
        principal = _principal(request, dependencies)
        dependencies.authorization_service.authorize_action(
            principal=principal, workspace_id=id, action=Action.VIEW,
            trace_id=request.state.trace_id, policy_version=dependencies.settings.policy_version,
        )
        connectors = (
            connector_repository.list(principal.tenant_id, id, principal.user_id, connector_registry.list())
            if connector_repository is not None else connector_registry.list()
        )
        if connector_repository is None:
            scope = (principal.tenant_id, id)
            if scope in open_law_registrations:
                connectors = tuple(item for item in connectors if item.connector_id != "mcp-open-law-go-kr")
                if open_law_registrations[scope] is not None:
                    connectors += (open_law_registrations[scope],)
        return JSONResponse({"data": {"connectors": [_dataclass_json(item) for item in connectors]},
                             "meta": {"trace_id": request.state.trace_id, "workspace_id": id}})

    @app.post("/api/v1/workspaces/{id}/connectors", status_code=201)
    async def register_connector(id: str, body: ConnectorRegisterBody, request: Request) -> JSONResponse:
        _require_query_keys(request, frozenset())
        principal = _principal(request, dependencies)
        dependencies.authorization_service.authorize_action(
            principal=principal, workspace_id=id, action=Action.EDIT,
            trace_id=request.state.trace_id, policy_version=dependencies.settings.policy_version,
        )
        if body.kind == "mcp" and body.name == "국가법령정보센터":
            connector = create_open_law_connector(api_key=os.environ.get("DAON_OPEN_LAW_API_KEY"))
        elif body.kind == "daon_approved_knowledge":
            connector = ApprovedKnowledgeConnector(
                token=os.environ.get("DAON_APPROVED_KNOWLEDGE_TOKEN"), timeout_seconds=5, max_retries=2,
            ).as_connector()
        else:
            raise ConnectorError("CONNECTOR_KIND_UNSUPPORTED")
        # Law registration has no Notebook/public consent input. Do not probe
        # here: L3's approved Notebook-bound law-search verifies the connection.
        view = connector.view() if connector.connector_id == "mcp-open-law-go-kr" else connector_registry.register(connector)
        if connector_repository is not None:
            connector_repository.register(principal.tenant_id, id, principal.user_id, view)
        elif connector.connector_id == "mcp-open-law-go-kr":
            open_law_registrations[(principal.tenant_id, id)] = view
        return JSONResponse({"data": _dataclass_json(view), "meta": {
            "trace_id": request.state.trace_id, "workspace_id": id,
        }}, status_code=201)

    @app.post("/api/v1/workspaces/{id}/connectors/{connector_id}/reconnect")
    async def reconnect_connector(id: str, connector_id: str, request: Request) -> JSONResponse:
        _require_query_keys(request, frozenset())
        principal = _principal(request, dependencies)
        dependencies.authorization_service.authorize_action(
            principal=principal, workspace_id=id, action=Action.EDIT,
            trace_id=request.state.trace_id, policy_version=dependencies.settings.policy_version,
        )
        if connector_id == "mcp-open-law-go-kr":
            view = set_scoped_open_law(principal, id, "unavailable", "CONNECTOR_NOT_VERIFIED")
        else:
            view = connector_registry.reconnect(connector_id)
            if connector_repository is not None:
                view = connector_repository.set_status(principal.tenant_id, id, principal.user_id, connector_id, status=view.status, error_code=view.error_code)
        return JSONResponse({"data": _dataclass_json(view), "meta": {
            "trace_id": request.state.trace_id, "workspace_id": id,
        }})

    @app.post("/api/v1/workspaces/{id}/connectors/{connector_id}/disconnect")
    async def disconnect_connector(id: str, connector_id: str, request: Request) -> JSONResponse:
        _require_query_keys(request, frozenset())
        principal = _principal(request, dependencies)
        dependencies.authorization_service.authorize_action(
            principal=principal, workspace_id=id, action=Action.EDIT,
            trace_id=request.state.trace_id, policy_version=dependencies.settings.policy_version,
        )
        if connector_id == "mcp-open-law-go-kr":
            view = set_scoped_open_law(principal, id, "disconnected", "CONNECTOR_DISCONNECTED")
        else:
            view = connector_registry.disconnect(connector_id)
            if connector_repository is not None:
                view = connector_repository.set_status(principal.tenant_id, id, principal.user_id, connector_id, status=view.status, error_code=view.error_code)
        return JSONResponse({"data": _dataclass_json(view), "meta": {
            "trace_id": request.state.trace_id, "workspace_id": id,
        }})

    @app.delete("/api/v1/workspaces/{id}/connectors/{connector_id}", status_code=204)
    async def unregister_connector(id: str, connector_id: str, request: Request) -> Response:
        _require_query_keys(request, frozenset())
        principal = _principal(request, dependencies)
        dependencies.authorization_service.authorize_action(
            principal=principal, workspace_id=id, action=Action.EDIT,
            trace_id=request.state.trace_id, policy_version=dependencies.settings.policy_version,
        )
        if connector_id == "mcp-open-law-go-kr":
            scoped_open_law(principal, id)
            if connector_repository is not None:
                connector_repository.unregister(principal.tenant_id, id, principal.user_id, connector_id)
            else:
                open_law_registrations[(principal.tenant_id, id)] = None
            return Response(status_code=204)
        connector = connector_registry.get(connector_id)
        if connector_repository is not None:
            if connector.kind == "daon_approved_knowledge":
                raise ConnectorError("CONNECTOR_FIXED")
            connector_repository.unregister(principal.tenant_id, id, principal.user_id, connector_id)
        else:
            connector_registry.unregister(connector_id)
        return Response(status_code=204)

    @app.get("/api/v1/workspaces/{id}/connectors/{connector_id}/sources")
    async def list_connector_sources(id: str, connector_id: str, request: Request) -> JSONResponse:
        _require_query_keys(request, frozenset())
        principal = _principal(request, dependencies)
        dependencies.authorization_service.authorize_action(
            principal=principal, workspace_id=id, action=Action.VIEW,
            trace_id=request.state.trace_id, policy_version=dependencies.settings.policy_version,
        )
        if connector_id == "mcp-open-law-go-kr" and connector_repository is None:
            scoped_open_law(principal, id)
            return JSONResponse({"data": {"sources": []}, "meta": {
                "trace_id": request.state.trace_id, "workspace_id": id}})
        sources = (
            connector_repository.sources(principal.tenant_id, id, principal.user_id, connector_id)
            if connector_repository is not None else connector_registry.sources(connector_id)
        )
        return JSONResponse({"data": {"sources": [_dataclass_json(item) for item in sources]},
                             "meta": {"trace_id": request.state.trace_id, "workspace_id": id}})

    @app.post("/api/v1/workspaces/{id}/sources", status_code=202)
    async def upload_pdf_source(
        id: str,
        request: Request,
        source_filename: str = Header(alias="X-Source-Filename"),
        notebook_id: str = Header(alias="X-Notebook-Id"),
        idempotency_key: str = Header(alias="Idempotency-Key"),
    ) -> JSONResponse:
        _require_query_keys(request, frozenset())
        _idempotency_key(idempotency_key)
        if _SOURCE_FILENAME.fullmatch(source_filename) is None:
            raise SourceUploadError("SOURCE_FILENAME_INVALID")
        principal = _principal(request, dependencies)
        dependencies.authorization_service.authorize_action(
            principal=principal,
            workspace_id=id,
            action=Action.EDIT,
            trace_id=request.state.trace_id,
            policy_version=dependencies.settings.policy_version,
        )
        if dependencies.source_upload_service is None:
            raise SourceUploadError("SOURCE_STORAGE_UNAVAILABLE", 503, retryable=True)
        if dependencies.document_processing_service is None:
            raise DocumentUnderstandingError(
                "DOCUMENT_PROCESSING_UNAVAILABLE", status=503, retryable=True,
            )
        await asyncio.to_thread(
            notebook_service.get, notebook_context(principal, id, request), notebook_id,
        )
        chunks: list[bytes] = []
        byte_size = 0
        async for chunk in request.stream():
            byte_size += len(chunk)
            if byte_size > dependencies.settings.source_upload_max_bytes:
                raise SourceUploadError("REQUEST_TOO_LARGE", 413)
            chunks.append(chunk)
        content = b"".join(chunks)
        content_type = request.headers.get("content-type", "").split(";", 1)[0].strip().lower()
        try:
            SourceIngestor().register_file(source_filename, content_type, content)
        except SourceRejected as error:
            raise SourceUploadError(str(error)) from None
        try:
            result = await asyncio.to_thread(
                dependencies.source_upload_service.register_pdf,
                tenant_id=principal.tenant_id,
                workspace_id=id,
                notebook_id=notebook_id,
                actor_id=principal.user_id,
                filename=source_filename,
                content=content,
                idempotency_key=idempotency_key,
                trace_id=request.state.trace_id,
                content_type=content_type,
            )
        except SourceUploadError as error:
            _source_boundary_event(
                event="source_upload", phase="register", trace_id=request.state.trace_id,
                http_status=error.status, safe_error_code=error.code,
            )
            raise
        _source_boundary_event(
            event="source_upload", phase="register", trace_id=request.state.trace_id,
            http_status=202, source_id_present=bool(result.source_id), db_commit=True,
        )
        try:
            processing = await asyncio.to_thread(
                dependencies.document_processing_service.submit,
                DocumentProcessingContext(
                    principal.tenant_id, id, principal.user_id,
                    request.state.trace_id, dependencies.settings.policy_version,
                ),
                result.source_version_id,
                notebook_id=notebook_id,
            )
        except DocumentUnderstandingError as error:
            _source_boundary_event(
                event="source_upload", phase="processing_submit", trace_id=request.state.trace_id,
                http_status=error.status, safe_error_code=error.code,
                source_id_present=bool(result.source_id), db_commit=True,
            )
            raise
        _source_boundary_event(
            event="source_upload", phase="processing_submit", trace_id=request.state.trace_id,
            http_status=202, source_id_present=bool(result.source_id),
            processing_run_id_present=bool(processing.processing_run_id), db_commit=True,
        )
        # Keep the upload response aligned with the public browser contract.
        # Internal storage metadata (content type and deletion policy) is not
        # part of the upload DTO and must not make the client reject an
        # otherwise successful registration as a malformed response.
        response_data = {
            "source_id": result.source_id,
            "source_version_id": result.source_version_id,
            "object_id": result.object_id,
            "digest_sha256": result.digest_sha256,
            "byte_size": result.byte_size,
            "status": result.status,
            "replayed": result.replayed,
        }
        response_data.update({
            "processing_run_id": processing.processing_run_id,
            "processing_state": processing.processing_state,
            "job_state": processing.job_state,
        })
        response = JSONResponse(
            {
                "data": response_data,
                "meta": {"trace_id": request.state.trace_id, "workspace_id": id},
            },
            status_code=202,
        )
        response.headers["ETag"] = f'"source:{result.source_id}:1"'
        return response

    @app.post("/api/v1/sources/{id}/processing-runs", status_code=201)
    async def reprocess_source(
        id: str, body: SourceReprocessingBody, request: Request,
        idempotency_key: str = Header(alias="Idempotency-Key"),
    ) -> JSONResponse:
        _require_query_keys(request, frozenset())
        _idempotency_key(idempotency_key)
        principal = _principal(request, dependencies)
        if body.kind != "processing_run" or body.step_up_authorization_id is not None:
            raise DocumentUnderstandingError("DOCUMENT_PROCESSING_CONTEXT_INVALID")
        attributes = body.attributes
        grant = dependencies.authorization_service.authorize_action(
            principal=principal, workspace_id=attributes.workspace_id, action=Action.EDIT,
            trace_id=request.state.trace_id, policy_version=dependencies.settings.policy_version,
        )
        await asyncio.to_thread(notebook_service.get, notebook_context(principal, attributes.workspace_id, request), attributes.notebook_id)
        await asyncio.to_thread(
            notebook_service.require_selected_bindings,
            notebook_context(principal, attributes.workspace_id, request), attributes.notebook_id,
            required=(("source", id, attributes.source_version_id),),
        )
        if dependencies.document_processing_service is None:
            raise DocumentUnderstandingError("DOCUMENT_PROCESSING_UNAVAILABLE", status=503, retryable=True)
        if egress_policy_service is None:
            raise DocumentUnderstandingError("EGRESS_POLICY_UNAVAILABLE", status=503, retryable=True)
        effective = await asyncio.to_thread(
            egress_policy_service.get_effective,
            _egress_policy_context(principal, attributes.workspace_id, request, dependencies),
        )
        snapshot = {"acl_version": grant.acl_version, "membership_version": grant.membership_version,
                    "role": grant.role.value, "policy_version": grant.policy_version,
                    "external_transfer_policy": effective.frozen_context()}
        mode_option = ({"processing_mode": attributes.processing_mode}
                       if "processing_mode" in attributes.model_fields_set else {})
        result = await asyncio.to_thread(
            dependencies.document_processing_service.reprocess,
            DocumentProcessingContext(principal.tenant_id, attributes.workspace_id, principal.user_id,
                                      request.state.trace_id, dependencies.settings.policy_version),
            id, attributes.source_version_id, notebook_id=attributes.notebook_id,
            idempotency_key=idempotency_key, authorization_snapshot=snapshot,
            **mode_option,
        )
        if mode_option:
            saved_mode = result.get("processing_mode")
            if type(saved_mode) is not str or saved_mode not in ("auto", "local_only", "pinned"):
                raise DocumentUnderstandingError("DOCUMENT_PROCESSING_DATABASE_UNAVAILABLE", status=503)
            # 현재 설정은 준비/실행 성공이 아니다. replay에서도 저장된 bool을 재사용하지 않는다.
            result = {**result, "auto_requeue_configured": saved_mode in ("auto", "local_only") and (
                principal.tenant_id, attributes.workspace_id, principal.user_id, attributes.source_version_id
            ) in dependencies.settings.document_readiness_settings.scopes}
        response = JSONResponse({"data": {"id": result["processing_run_id"], "kind": "processing_run",
                                         "version": "2", "attributes": result},
                                 "trace_id": request.state.trace_id}, status_code=201)
        response.headers["ETag"] = f'"processing-run:{result["processing_run_id"]}:2"'
        return response

    @app.get("/api/v1/workspaces/{id}/processing-runs/{processing_run_id}")
    async def get_document_processing_status(
        id: str, processing_run_id: str, request: Request, notebook_id: str = Query(),
    ) -> JSONResponse:
        _require_query_keys(request, frozenset({"notebook_id"}))
        principal = _principal(request, dependencies)
        dependencies.authorization_service.authorize_action(
            principal=principal, workspace_id=id, action=Action.VIEW,
            trace_id=request.state.trace_id,
            policy_version=dependencies.settings.policy_version,
        )
        if dependencies.document_processing_service is None:
            raise DocumentUnderstandingError(
                "DOCUMENT_PROCESSING_UNAVAILABLE", status=503, retryable=True,
            )
        try:
            status = await asyncio.to_thread(
                dependencies.document_processing_service.get_status,
                DocumentProcessingContext(
                    principal.tenant_id, id, principal.user_id,
                    request.state.trace_id, dependencies.settings.policy_version,
                ),
                processing_run_id,
                notebook_id=notebook_id,
            )
        except DocumentUnderstandingError as error:
            _source_boundary_event(
                event="processing_status", phase="status", trace_id=request.state.trace_id,
                http_status=error.status, safe_error_code=error.code,
                processing_run_id_present=True,
            )
            raise
        await asyncio.to_thread(
            notebook_service.require_selected_bindings,
            notebook_context(principal, id, request), notebook_id,
            (("source", status.source_id, status.source_version_id),),
        )
        _source_boundary_event(
            event="processing_status", phase="status", trace_id=request.state.trace_id,
            http_status=200, source_id_present=bool(status.source_id),
            processing_run_id_present=bool(status.processing_run_id), db_commit=False,
            safe_error_code=status.safe_error_code,
        )
        return _json_with_etag(
            {
                "data": _dataclass_json(status),
                "meta": {"trace_id": request.state.trace_id, "workspace_id": id},
            },
            "|".join(filter(None, (
                status.processing_run_id, status.processing_state, status.source_state,
                status.job_state, status.safe_error_code,
            ))),
        )

    def _question_run_id(
        principal: IdentityPrincipal, workspace_id: str, notebook_id: str, key: str,
    ) -> str:
        return "run-" + hashlib.sha256(
            f"{principal.tenant_id}|{workspace_id}|{notebook_id}|{principal.user_id}|{key}".encode("utf-8")
        ).hexdigest()[:32]

    def _question_inputs(
        principal: IdentityPrincipal, workspace_id: str, request: Request,
        body: QuestionAuthorizationBody | QuestionBody,
    ) -> tuple[str, tuple[QuestionInputSource, ...], str | None, str | None]:
        if body.knowledge_context is None:
            if body.source_id is None or body.source_version_id is None:
                return "general_ungrounded", (), None, None
            source = QuestionInputSource(
                "raw_source", body.source_id, body.source_id, body.source_version_id,
            )
            return "raw_only", (source,), body.source_id, body.source_version_id
        package_ids = tuple(
            item.resource_id for item in body.knowledge_context.resources
            if item.resource_kind == "knowledge_package"
        )
        package_sources = knowledge_package_service.resolve_question_sources(
            _knowledge_package_context(principal, workspace_id, request, dependencies),
            package_ids,
        ) if package_ids else ()
        sources = tuple(QuestionInputSource(
            "daon_knowledge", item.package_id, item.source_id,
            item.source_version_id, item.digest_sha256,
            knowledge_registration_id=getattr(item,"knowledge_registration_id",None),
        ) for item in package_sources)
        for item in body.knowledge_context.resources:
            if item.resource_kind == "knowledge_package":
                continue
            if item.resource_kind != "source" or item.version_id is None:
                raise QuestionAnsweringError("QUESTION_RESOURCE_UNSUPPORTED", status=409)
            sources += (QuestionInputSource(
                "raw_source", item.resource_id, item.resource_id, item.version_id,
            ),)
        if not sources:
            raise QuestionAnsweringError("QUESTION_CONTEXT_INVALID")
        return (
            body.knowledge_context.mode, sources,
            sources[0].source_id, sources[0].source_version_id,
        )

    def _question_authorization_fingerprint(
        *, principal: IdentityPrincipal, workspace_id: str, run_id: str,
        body: QuestionAuthorizationBody | QuestionBody, prepared: object,
        context_mode: str, context_sources: tuple[QuestionInputSource, ...],
        policy_fingerprint: str, idempotency_key: str,
    ) -> str:
        payload = cast(Any, prepared).provider_payload
        selection = cast(Any, prepared).selection
        return "sha256:" + hashlib.sha256(canonical_json_bytes({
            "actor_id": principal.user_id, "tenant_id": principal.tenant_id,
            "workspace_id": workspace_id, "run_id": run_id,
            "knowledge_context": {
                "mode": context_mode,
                "items": [{"origin":item.origin,"context_item_id":item.context_item_id,
                    "source_id":item.source_id,"source_version_id":item.source_version_id,
                    "digest_sha256":item.digest_sha256} for item in context_sources],
            },
            "question_fingerprint": "sha256:" + hashlib.sha256(body.question.strip().encode()).hexdigest(),
            "provider_payload_fingerprint": "sha256:" + hashlib.sha256(payload).hexdigest(),
            "provider_kind": selection.provider_kind,
            "deployment_id": selection.deployment_id,
            "connection_id": getattr(selection, "connection_id", None),
            "credential_version": getattr(selection, "credential_version", 0),
            "default_version": selection.binding_version,
            "catalog_version": getattr(selection, "catalog_version", 0),
            "effective_policy_fingerprint": policy_fingerprint,
            "idempotency_key": idempotency_key,
        })).hexdigest()

    def _require_external_question_policy(
        effective: object, *, provider_kind: str, destination: str,
        payload_bytes: int, classification: str,
        masking_required: bool, redaction_required: bool,
    ) -> None:
        if not external_question_policy_matches(
            effective, provider_kind=provider_kind, destination=destination,
            payload_bytes=payload_bytes, classification=classification,
            masking_required=masking_required, redaction_required=redaction_required,
        ):
            raise EgressPolicyError("EGRESS_POLICY_DENIED", 403)

    def _question_replay_external_scope(answer: object) -> tuple[str, int, str, bool, bool]:
        scope = getattr(answer, "egress_scope", None)
        if not isinstance(scope, Mapping) or set(scope) != {
            "classification", "destination", "masking_required", "max_bytes",
            "redaction_required",
        }:
            raise QuestionAnsweringError("QUESTION_REPLAY_UNAVAILABLE", status=409)
        destination = scope.get("destination")
        payload_bytes = scope.get("max_bytes")
        classification = scope.get("classification")
        masking_required = scope.get("masking_required")
        redaction_required = scope.get("redaction_required")
        if (
            not isinstance(destination, str) or not destination
            or not isinstance(payload_bytes, int) or isinstance(payload_bytes, bool)
            or payload_bytes < 0 or not isinstance(classification, str)
            or not isinstance(masking_required, bool)
            or not isinstance(redaction_required, bool)
        ):
            raise QuestionAnsweringError("QUESTION_REPLAY_UNAVAILABLE", status=409)
        return (
            destination, payload_bytes, classification,
            masking_required, redaction_required,
        )

    _QUESTION_MODES = frozenset({
        "work_support", "explicit_source_lookup", "source_backed_action", "approved_web_research",
    })
    _QUESTION_GROUNDINGS = frozenset({
        "source_backed", "ungrounded", "source_evidence_unavailable", "web_backed",
    })

    def _question_metadata(
        stored: object, *, question: str, context_mode: str,
    ) -> dict[str, object]:
        intent = classify_question_intent(question)
        if intent not in _QUESTION_MODES:
            raise QuestionAnsweringError("QUESTION_RESPONSE_INVALID", status=500)
        has_source_context = context_mode != "general_ungrounded"
        mode = intent
        if has_source_context and intent == "work_support":
            mode = "explicit_source_lookup"
        citations = tuple(getattr(stored, "citations", ()))
        insufficient = bool(getattr(stored, "insufficient", False))
        if insufficient and has_source_context:
            return {
                "mode": mode,
                "grounding": "source_evidence_unavailable",
                "source_scope_summary": "선택한 Source 범위",
                "mismatch": {
                    "code": "SOURCE_SCOPE_MISMATCH",
                    "detail": "질문과 선택한 Source의 범위가 일치하지 않습니다.",
                },
                "next_actions": ["다른 Source 선택", "Source 추가", "승인된 웹 조사 요청"],
            }
        return {
            "mode": mode,
            "grounding": "source_backed" if has_source_context and citations else "ungrounded",
            "source_scope_summary": "선택한 Source 범위" if has_source_context else None,
            "mismatch": None,
            "next_actions": ["승인된 웹 조사 요청"] if intent == "approved_web_research" else [],
        }

    def _question_result_response(
        answer: object, request: Request, workspace_id: str, *,
        question: str, context_mode: str,
    ) -> JSONResponse:
        stored = cast(Any, answer)
        metadata = _question_metadata(stored, question=question, context_mode=context_mode)
        response = JSONResponse({
            "data": {
                "run_id": stored.run_id, "run_result_id": stored.run_result_id,
                "answer": stored.answer, "insufficient": stored.insufficient,
                "citations": [_dataclass_json(item) for item in stored.citations],
                **metadata,
            },
            "meta": {"trace_id": request.state.trace_id, "workspace_id": workspace_id},
        })
        response.headers["ETag"] = f'"run-result:{stored.run_result_id}"'
        return response

    @app.post("/api/v1/workspaces/{id}/questions/authorization", status_code=201)
    async def authorize_grounded_question(
        id: str, body: QuestionAuthorizationBody, request: Request,
        idempotency_key: str = Header(alias="Idempotency-Key"),
    ) -> JSONResponse:
        _require_query_keys(request, frozenset())
        _egress_idempotency_key(idempotency_key)
        principal = _principal(request, dependencies)
        grant = dependencies.authorization_service.authorize_action(
            principal=principal, workspace_id=id, action=Action.QUERY,
            requested_permissions=(Permission.EXTERNAL_LLM,),
            trace_id=request.state.trace_id,
            policy_version=dependencies.settings.policy_version,
        )
        if question_answering_service is None or egress_policy_service is None:
            raise QuestionAnsweringError("QUESTION_SERVICE_UNAVAILABLE", status=503, retryable=True)
        context = QuestionContext(
            principal.tenant_id, id, principal.user_id,
            request.state.trace_id, dependencies.settings.policy_version,
            body.notebook_id,
        )
        context_mode, context_sources, source_id, source_version_id = _question_inputs(
            principal, id, request, body,
        )
        await asyncio.to_thread(
            notebook_service.require_selected_bindings,
            notebook_context(principal, id, request), body.notebook_id,
            required=tuple(
                ("knowledge_context", item.context_item_id, None)
                if item.origin == "daon_knowledge"
                else ("source", item.source_id, item.source_version_id)
                for item in context_sources
            ),
        )
        prepared = await asyncio.to_thread(
            question_answering_service.prepare_authorization, context,
            source_id=source_id, source_version_id=source_version_id,
            question=body.question, context_mode=context_mode, context_sources=context_sources,
        )
        effective = egress_policy_service.get_effective(
            _egress_policy_context(principal, id, request, dependencies)
        )
        if cast(Any, prepared).selection.provider_kind != "external_api":
            raise QuestionAnsweringError("QUESTION_EXTERNAL_AUTHORIZATION_NOT_REQUIRED", status=409)
        approver_roles = {
            # A personal workspace has no organization/workspace-admin
            # membership; its owner is the only valid approver for its own
            # external-transfer step-up.
            "workspace_manager": {
                Role.PERSONAL_OWNER, Role.WORKSPACE_ADMIN, Role.ORGANIZATION_ADMIN,
            },
            "organization_admin": {Role.PERSONAL_OWNER, Role.ORGANIZATION_ADMIN},
        }
        if grant.role not in approver_roles[effective.required_approver]:
            raise AuthorizationError("ACTION_DENIED", 403)
        run_id = _question_run_id(principal, id, body.notebook_id, idempotency_key)
        request_fingerprint = _question_authorization_fingerprint(
            principal=principal, workspace_id=id, run_id=run_id, body=body,
            context_mode=context_mode, context_sources=context_sources,
            prepared=prepared, policy_fingerprint=effective.fingerprint,
            idempotency_key=idempotency_key,
        )
        development_auth_bypass = (
            dependencies.settings.dev_auth_bypass
            and dependencies.settings.profile in {"test", "development"}
        )
        if development_auth_bypass:
            step_up_authorization_id = f"dev-bypass:{run_id}"
            expires_at = datetime.now(timezone.utc) + timedelta(minutes=5)
        else:
            if not body.password:
                raise IdentityError("REAUTHENTICATION_REQUIRED", 401)
            access_token, _ = _credential(request)
            step_up = dependencies.identity_service.issue_step_up_after_reauthentication(
                access_token=access_token, password=body.password,
                action_group="external_transfer", target_id=run_id,
                policy_version=request_fingerprint, trace_id=request.state.trace_id,
            )
            step_up_authorization_id = step_up.authorization
            expires_at = step_up.expires_at
        response = JSONResponse({"data": {
            "step_up_authorization_id": step_up_authorization_id,
            "expires_at": expires_at.isoformat(), "run_id": run_id,
            "request_fingerprint": request_fingerprint,
        }, "meta": {"trace_id": request.state.trace_id}}, status_code=201)
        response.headers["ETag"] = f'"question-authorization:{request_fingerprint}"'
        return response

    @app.post("/api/v1/workspaces/{id}/questions")
    async def ask_grounded_question(
        id: str, body: QuestionBody, request: Request,
        idempotency_key: str = Header(alias="Idempotency-Key"),
    ) -> JSONResponse:
        _require_query_keys(request, frozenset())
        _egress_idempotency_key(idempotency_key)
        principal = _principal(request, dependencies)
        dependencies.authorization_service.authorize_action(
            principal=principal, workspace_id=id, action=Action.QUERY,
            trace_id=request.state.trace_id,
            policy_version=dependencies.settings.policy_version,
        )
        if question_answering_service is None:
            raise QuestionAnsweringError(
                "QUESTION_SERVICE_UNAVAILABLE", status=503, retryable=True,
            )
        run_id = _question_run_id(principal, id, body.notebook_id, idempotency_key)
        question_context = QuestionContext(
            principal.tenant_id, id, principal.user_id,
            request.state.trace_id, dependencies.settings.policy_version,
            body.notebook_id,
        )
        replay_fingerprint = question_request_fingerprint(
            question_context, run_id=run_id, idempotency_key=idempotency_key,
            request_payload=body.model_dump(
                exclude={"step_up_authorization_id"}, exclude_none=True,
            ),
        )
        context_mode, context_sources, source_id, source_version_id = _question_inputs(
            principal, id, request, body,
        )
        await asyncio.to_thread(
            notebook_service.require_selected_bindings,
            notebook_context(principal, id, request), body.notebook_id,
            required=tuple(
                ("knowledge_context", item.context_item_id, None)
                if item.origin == "daon_knowledge"
                else ("source", item.source_id, item.source_version_id)
                for item in context_sources
            ),
        )
        replay = await asyncio.to_thread(
            question_answering_service.replay, question_context,
            run_id=run_id, request_fingerprint=replay_fingerprint,
        )
        if replay is not None:
            replay_provider_kind = getattr(replay, "provider_kind", None)
            if replay_provider_kind not in {"external_api", "server_internal", "local_runtime"}:
                raise QuestionAnsweringError("QUESTION_REPLAY_UNAVAILABLE", status=409)
            if replay_provider_kind == "external_api":
                dependencies.authorization_service.authorize_action(
                    principal=principal, workspace_id=id, action=Action.VIEW,
                    requested_permissions=(Permission.EXTERNAL_LLM,),
                    trace_id=request.state.trace_id,
                    policy_version=dependencies.settings.policy_version,
                )
                if egress_policy_service is None:
                    raise EgressPolicyError("EGRESS_POLICY_UNAVAILABLE", 503)
                effective = egress_policy_service.get_effective(
                    _egress_policy_context(principal, id, request, dependencies)
                )
                (
                    replay_destination, replay_payload_bytes, replay_classification,
                    replay_masking, replay_redaction,
                ) = _question_replay_external_scope(replay)
                _require_external_question_policy(
                    effective, provider_kind="external_api",
                    destination=replay_destination, payload_bytes=replay_payload_bytes,
                    classification=replay_classification,
                    masking_required=replay_masking,
                    redaction_required=replay_redaction,
                )
            return _question_result_response(
                replay, request, id, question=body.question, context_mode=context_mode,
            )
        approved_authorization = None
        if egress_policy_service is not None:
            context = question_context
            prepared = await asyncio.to_thread(
                question_answering_service.prepare_authorization, context,
                source_id=source_id, source_version_id=source_version_id,
                question=body.question, context_mode=context_mode, context_sources=context_sources,
            )
            if cast(Any, prepared).selection.provider_kind == "external_api":
                dependencies.authorization_service.authorize_action(
                    principal=principal, workspace_id=id, action=Action.VIEW,
                    requested_permissions=(Permission.EXTERNAL_LLM,),
                    trace_id=request.state.trace_id,
                    policy_version=dependencies.settings.policy_version,
                )
                effective = egress_policy_service.get_effective(
                    _egress_policy_context(principal, id, request, dependencies)
                )
                destination = (
                    urlsplit(cast(Any, prepared).selection.base_url).hostname or ""
                ).casefold()
                _require_external_question_policy(
                    effective, provider_kind="external_api", destination=destination,
                    payload_bytes=len(cast(Any, prepared).provider_payload),
                    classification="internal", masking_required=True,
                    redaction_required=True,
                )
                request_fingerprint = _question_authorization_fingerprint(
                    principal=principal, workspace_id=id, run_id=run_id, body=body,
                    context_mode=context_mode, context_sources=context_sources,
                    prepared=prepared, policy_fingerprint=effective.fingerprint,
                    idempotency_key=idempotency_key,
                )
                if not (
                    dependencies.settings.dev_auth_bypass
                    and dependencies.settings.profile in {"test", "development"}
                ):
                    access_token, _ = _credential(request)
                    dependencies.identity_service.consume_step_up(
                        step_up_authorization=body.step_up_authorization_id,
                        access_token=access_token, action_group="external_transfer",
                        target_id=run_id, policy_version=request_fingerprint,
                        trace_id=request.state.trace_id, operation="question.external_transfer",
                        idempotency_key=idempotency_key,
                        operations_scope=_operations_workspace_scope(principal,id,dependencies),
                    )
                approved_authorization = {
                    "request_fingerprint": request_fingerprint,
                    "policy_fingerprint": effective.fingerprint,
                    "provider_payload_fingerprint": "sha256:" + hashlib.sha256(
                        cast(Any, prepared).provider_payload,
                    ).hexdigest(),
                    "provider_kind": cast(Any, prepared).selection.provider_kind,
                    "deployment_id": cast(Any, prepared).selection.deployment_id,
                    "connection_id": cast(Any, prepared).selection.connection_id,
                    "credential_version": str(cast(Any, prepared).selection.credential_version),
                    "default_version": str(cast(Any, prepared).selection.binding_version),
                    "catalog_version": str(cast(Any, prepared).selection.catalog_version),
                }
                if (
                    dependencies.settings.dev_auth_bypass
                    and dependencies.settings.profile in {"test", "development"}
                ):
                    approved_authorization["authorization_mode"] = "development_auth_bypass"
        answer = await asyncio.to_thread(
            question_answering_service.ask,
            QuestionContext(
                principal.tenant_id, id, principal.user_id,
                request.state.trace_id, dependencies.settings.policy_version,
                body.notebook_id,
            ),
            source_id=source_id, source_version_id=source_version_id,
            question=body.question, run_id=run_id,
            approved_authorization=approved_authorization,
            context_mode=context_mode, context_sources=context_sources,
            request_fingerprint=replay_fingerprint, replay_checked=True,
            request_payload=body.model_dump(exclude={"step_up_authorization_id"}, exclude_none=True),
            idempotency_key=idempotency_key,
        )
        return _question_result_response(
            answer, request, id, question=body.question, context_mode=context_mode,
        )

    @app.post("/api/v1/studio-generation-requests", status_code=201)
    async def create_product_studio_generation(
        body: StudioGenerationBody, request: Request,
        idempotency_key: str = Header(alias="Idempotency-Key"),
    ) -> JSONResponse:
        _require_query_keys(request, frozenset())
        _studio_idempotency_key(idempotency_key)
        principal = _principal(request, dependencies)
        dependencies.authorization_service.authorize_action(
            principal=principal, workspace_id=body.workspace_id, action=Action.EDIT,
            trace_id=request.state.trace_id, policy_version=dependencies.settings.policy_version,
        )
        if studio_workspace_service is None:
            raise StudioError("STUDIO_SERVICE_UNAVAILABLE", 503)
        if license_enforcement_enabled and _requires_runtime_license_precheck(
            studio_workspace_service,
        ):
            await asyncio.to_thread(
                license_service.require_new_generation,
                _license_context(principal, body.workspace_id, request, dependencies),
            )
        if body.source_version_ids != body.settings.source_version_ids:
            raise StudioError("STUDIO_INPUT_INVALID")
        if body.source_only != (body.run_id is None and body.run_result_id is None):
            raise StudioError("STUDIO_INPUT_INVALID")
        if not body.source_only and (body.run_id is None or body.run_result_id is None):
            raise StudioError("STUDIO_INPUT_INVALID")
        output, replayed = await asyncio.to_thread(
            studio_workspace_service.enqueue,
            _product_studio_context(principal, body.workspace_id, request, dependencies, body.notebook_id),
            StudioGenerationRequest(
                body.output_type, body.source_id, tuple(body.source_version_ids), body.run_id,
                body.run_result_id, body.settings.purpose, body.settings.audience,
                body.settings.ruleset_version_id, body.settings.length, body.settings.structure,
                body.settings.output_format, body.settings.review_condition, body.settings.language,
                body.source_only,
            ),
            idempotency_key,
        )
        serialized_output = _enum_json(output)
        if not isinstance(serialized_output, dict):
            raise StudioError("STUDIO_RESULT_INVALID", 500)
        output_projection = dict(serialized_output)
        output_projection["lineage"] = {
            "notebook_id": body.notebook_id,
            "source_version_ids": list(body.source_version_ids),
            "artifact_type": body.output_type,
            "instruction": body.settings.purpose,
            "run_id": body.run_id,
            "source_only": body.source_only,
            "citations": output_projection.get("citations", []),
            "verification_required": False,
        }
        response = JSONResponse({"data": output_projection, "meta": {
            "trace_id": request.state.trace_id, "workspace_id": body.workspace_id, "replayed": replayed,
        }}, status_code=200 if replayed else 201)
        response.headers["ETag"] = f'"studio-job:{output["job_id"]}"'
        return response

    @app.get("/api/v1/studio-generation-jobs/{job_id}")
    async def get_product_studio_generation_job(
        job_id: str, request: Request, workspace_id: str = Query(), notebook_id: str = Query(),
    ) -> JSONResponse:
        _require_query_keys(request, frozenset({"workspace_id", "notebook_id"}))
        principal = _principal(request, dependencies)
        dependencies.authorization_service.authorize_action(
            principal=principal, workspace_id=workspace_id, action=Action.VIEW,
            trace_id=request.state.trace_id, policy_version=dependencies.settings.policy_version,
        )
        if studio_workspace_service is None:
            raise StudioError("STUDIO_SERVICE_UNAVAILABLE", 503)
        job = await asyncio.to_thread(
            studio_workspace_service.generation_job,
            _product_studio_context(principal, workspace_id, request, dependencies, notebook_id), job_id,
        )
        data = _enum_json(job)
        if not isinstance(data, dict):
            raise StudioError("STUDIO_RESULT_INVALID", 500)
        data["lineage"] = {
            "notebook_id": notebook_id, "source_version_ids": data.get("output", {}).get("source_version_ids", []) if isinstance(data.get("output"), dict) else [],
            "artifact_type": data.get("output_type"), "instruction": data.get("title"),
            "run_id": data.get("output", {}).get("run_id") if isinstance(data.get("output"), dict) else None,
            "citations": data.get("output", {}).get("citations", []) if isinstance(data.get("output"), dict) else [],
            "verification_required": data.get("status") != "completed",
        }
        return JSONResponse({"data": data, "meta": {"trace_id": request.state.trace_id, "workspace_id": workspace_id}})

    @app.get("/api/v1/studio-outputs")
    async def list_product_studio_outputs(
        request: Request, workspace_id: str = Query(), notebook_id: str = Query(),
    ) -> JSONResponse:
        _require_query_keys(request, frozenset({"workspace_id", "notebook_id"}))
        principal = _principal(request, dependencies)
        dependencies.authorization_service.authorize_action(
            principal=principal, workspace_id=workspace_id, action=Action.VIEW,
            trace_id=request.state.trace_id, policy_version=dependencies.settings.policy_version,
        )
        if studio_workspace_service is None:
            raise StudioError("STUDIO_SERVICE_UNAVAILABLE", 503)
        projection = await asyncio.to_thread(
            studio_workspace_service.list_outputs,
            _product_studio_context(principal, workspace_id, request, dependencies, notebook_id),
        )
        if isinstance(projection, Mapping):
            outputs = _enum_json(projection.get("outputs", ()))
            locks = _enum_json(projection.get("studio_locks", ()))
        else:
            outputs = _enum_json(projection)
            locks = []
        if isinstance(outputs, list):
            for item in outputs:
                if not isinstance(item, dict):
                    continue
                source_version_ids = item.get("source_version_ids", [])
                citations = item.get("citations", [])
                item["lineage"] = {
                    "notebook_id": notebook_id,
                    "source_version_ids": source_version_ids if isinstance(source_version_ids, list) else [],
                    "artifact_type": item.get("output_type"),
                    "instruction": item.get("purpose"),
                    "run_id": item.get("run_id"),
                    "citations": citations if isinstance(citations, list) else [],
                    "verification_required": not (
                        isinstance(source_version_ids, list) and bool(source_version_ids)
                        and isinstance(citations, list) and bool(citations)
                    ),
                }
        data = {"outputs": outputs, "studio_locks": locks}
        return JSONResponse({"data": data, "meta": {
            "trace_id": request.state.trace_id, "workspace_id": workspace_id,
        }})

    @app.post("/api/v1/studio-outputs/{output_id}/versions", status_code=201)
    async def create_product_studio_version(
        output_id: str, body: StudioRevisionBody, request: Request,
        idempotency_key: str = Header(alias="Idempotency-Key"),
    ) -> JSONResponse:
        _require_query_keys(request, frozenset())
        _studio_idempotency_key(idempotency_key)
        principal = _principal(request, dependencies)
        dependencies.authorization_service.authorize_action(
            principal=principal, workspace_id=body.workspace_id, action=Action.EDIT,
            trace_id=request.state.trace_id, policy_version=dependencies.settings.policy_version,
        )
        if studio_workspace_service is None:
            raise StudioError("STUDIO_SERVICE_UNAVAILABLE", 503)
        version, replayed = await asyncio.to_thread(
            studio_workspace_service.revise,
            _product_studio_context(principal, body.workspace_id, request, dependencies, body.notebook_id), output_id,
            body.model_dump(exclude={"workspace_id", "notebook_id"}), idempotency_key,
        )
        response = JSONResponse({"data": _enum_json(version), "meta": {
            "trace_id": request.state.trace_id, "workspace_id": body.workspace_id, "replayed": replayed,
        }}, status_code=200 if replayed else 201)
        response.headers["ETag"] = f'"studio-version:{version["output_version_id"]}"'
        return response

    @app.get("/api/v1/studio-outputs/{output_id}/versions")
    async def list_product_studio_versions(
        output_id: str, request: Request, workspace_id: str = Query(), notebook_id: str = Query(),
    ) -> JSONResponse:
        _require_query_keys(request, frozenset({"workspace_id", "notebook_id"}))
        principal = _principal(request, dependencies)
        dependencies.authorization_service.authorize_action(
            principal=principal, workspace_id=workspace_id, action=Action.VIEW,
            trace_id=request.state.trace_id, policy_version=dependencies.settings.policy_version,
        )
        if studio_workspace_service is None:
            raise StudioError("STUDIO_SERVICE_UNAVAILABLE", 503)
        versions = await asyncio.to_thread(
            studio_workspace_service.list_versions,
            _product_studio_context(principal, workspace_id, request, dependencies, notebook_id), output_id,
        )
        return JSONResponse({"data": {"output_id": output_id, "versions": _enum_json(versions)}, "meta": {
            "trace_id": request.state.trace_id, "workspace_id": workspace_id,
        }})

    def lifecycle_id(value):
        if not isinstance(value,str) or not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._:-]{0,255}",value):
            raise HTTPException(status_code=400)
        return value

    async def lifecycle_body(request,required,optional=frozenset()):
        def pairs(values):
            result={}
            for key,value in values:
                if key in result: raise ValueError()
                result[key]=value
            return result
        raw=await request.body()
        try:
            if len(raw)>16384 or request.headers.get("content-type","").split(";")[0].strip().lower()!="application/json":
                raise ValueError()
            value=json.loads(raw,object_pairs_hook=pairs)
            if not isinstance(value,dict) or not required<=value.keys() or not value.keys()<=required|optional:
                raise ValueError()
            return value
        except (ValueError,UnicodeError):
            raise HTTPException(status_code=400) from None

    def lifecycle_access(request,workspace,action,notebook=None):
        lifecycle_id(workspace)
        if notebook is not None: lifecycle_id(notebook)
        principal=_principal(request,dependencies)
        dependencies.authorization_service.authorize_action(principal=principal,workspace_id=workspace,action=action,
            trace_id=request.state.trace_id,policy_version=dependencies.settings.policy_version)
        if studio_approval_lifecycle_repository is None: raise StudioError("STUDIO_SERVICE_UNAVAILABLE",503)
        return principal,_product_studio_context(principal,workspace,request,dependencies,notebook)

    def lifecycle_response(request,workspace,result,replayed=None,etag=None):
        meta={"trace_id":request.state.trace_id,"workspace_id":workspace}
        if replayed is not None: meta["replayed"]=replayed
        response=JSONResponse({"data":_enum_json(result),"meta":meta})
        response.headers["ETag"]=etag or '"studio-approval:'+hashlib.sha256(json.dumps(_enum_json(result),sort_keys=True,separators=(",",":")).encode()).hexdigest()+'"'
        return response

    @app.get("/api/v1/workspaces/{id}/studio/approval-policy")
    async def get_studio_approval_policy(id:str,request:Request):
        _require_query_keys(request,frozenset())
        _,ctx=lifecycle_access(request,id,Action.VIEW)
        result=await asyncio.to_thread(studio_approval_lifecycle_repository.get_policy,ctx)
        return lifecycle_response(request,id,result)

    @app.patch("/api/v1/workspaces/{id}/studio/approval-policy")
    async def save_studio_approval_policy(id:str,request:Request,idempotency_key:str=Header(alias="Idempotency-Key")):
        _require_query_keys(request,frozenset()); _studio_idempotency_key(idempotency_key)
        body=await lifecycle_body(request,{"ttl_days","expected_policy_version"},{"step_up_authorization"})
        if type(body["ttl_days"]) is not int or not 1<=body["ttl_days"]<=30 or type(body["expected_policy_version"]) is not int or body["expected_policy_version"]<0:
            raise HTTPException(status_code=400)
        principal,ctx=lifecycle_access(request,id,Action.POLICY_MANAGE)
        policy=await asyncio.to_thread(studio_approval_lifecycle_repository.get_policy,ctx)
        if not policy["can_manage"]: raise StudioError("RESOURCE_UNAVAILABLE",404)
        access_token,_=_credential(request)
        dependencies.identity_service.consume_step_up(step_up_authorization=body.get("step_up_authorization"),access_token=access_token,
            action_group="organization_security_or_connector_policy_change",target_id=policy["step_up_target_id"],
            policy_version=dependencies.settings.policy_version,trace_id=request.state.trace_id,
            operation="studio.approval.policy.save",idempotency_key=idempotency_key,
            operations_scope=_operations_workspace_scope(principal,id,dependencies))
        result,replayed=await asyncio.to_thread(studio_approval_lifecycle_repository.save_policy,ctx,body["ttl_days"],body["expected_policy_version"],idempotency_key)
        return lifecycle_response(request,id,result,replayed)

    @app.post("/api/v1/approval-requests/{id}/withdraw")
    async def withdraw_studio_approval(id:str,request:Request,idempotency_key:str=Header(alias="Idempotency-Key")):
        _require_query_keys(request,frozenset()); _studio_idempotency_key(idempotency_key); lifecycle_id(id)
        body=await lifecycle_body(request,{"workspace_id","notebook_id","output_version_id"})
        lifecycle_id(body["output_version_id"])
        _,ctx=lifecycle_access(request,body["workspace_id"],Action.EDIT,body["notebook_id"])
        result,replayed=await asyncio.to_thread(studio_approval_lifecycle_repository.withdraw,ctx,id,body["output_version_id"],idempotency_key)
        return lifecycle_response(request,body["workspace_id"],result,replayed)

    @app.get("/api/v1/workspaces/{id}/studio/approval-notices")
    async def list_studio_approval_notices(id:str,request:Request,notebook_id:str=Query()):
        _require_query_keys(request,frozenset({"notebook_id"}))
        _,ctx=lifecycle_access(request,id,Action.VIEW,notebook_id)
        result=await asyncio.to_thread(studio_approval_lifecycle_repository.list_notices,ctx)
        return lifecycle_response(request,id,result)

    @app.patch("/api/v1/workspaces/{id}/studio/approval-notices/{notification_id}")
    async def read_studio_approval_notice(id:str,notification_id:str,request:Request,
        if_match:str=Header(alias="If-Match"),idempotency_key:str=Header(alias="Idempotency-Key")):
        _require_query_keys(request,frozenset()); _studio_idempotency_key(idempotency_key); lifecycle_id(notification_id)
        body=await lifecycle_body(request,{"notebook_id","state"})
        if body["state"]!="read": raise HTTPException(status_code=400)
        matched=re.fullmatch(r'"studio-approval-notice:'+re.escape(notification_id)+r':([1-9][0-9]{0,9})"',if_match)
        if matched is None or int(matched[1])>2147483647: raise HTTPException(status_code=400)
        _,ctx=lifecycle_access(request,id,Action.VIEW,body["notebook_id"])
        result,replayed=await asyncio.to_thread(studio_approval_lifecycle_repository.mark_notice_read,ctx,notification_id,int(matched[1]),idempotency_key)
        return lifecycle_response(request,id,result,replayed,f'"studio-approval-notice:{notification_id}:{result["version"]}"')

    @app.post("/api/v1/reviews", status_code=201)
    @app.post("/api/v1/approval-requests", status_code=201)
    @app.post("/api/v1/approvals", status_code=201)
    @app.post("/api/v1/deliveries", status_code=201)
    @app.post("/api/v1/knowledge-registrations", status_code=201)
    async def create_product_studio_action(
        body: StudioActionBody, request: Request,
        idempotency_key: str = Header(alias="Idempotency-Key"),
    ) -> JSONResponse:
        _require_query_keys(request, frozenset())
        _studio_idempotency_key(idempotency_key)
        resource = request.url.path.rsplit("/", 1)[-1]
        action_name = {
            "reviews": "review", "approval-requests": "approval_request", "approvals": "approval",
            "deliveries": "delivery", "knowledge-registrations": "knowledge_registration",
        }[resource]
        permission_action = {
            "review": Action.REVIEW, "approval_request": Action.EDIT, "approval": Action.APPROVE,
            "delivery": Action.DELIVER, "knowledge_registration": Action.KNOWLEDGE_REGISTER,
        }[action_name]
        principal = _principal(request, dependencies)
        grant = dependencies.authorization_service.authorize_action(
            principal=principal, workspace_id=body.workspace_id, action=permission_action,
            trace_id=request.state.trace_id, policy_version=dependencies.settings.policy_version,
        )
        if studio_workspace_service is None:
            raise StudioError("STUDIO_SERVICE_UNAVAILABLE", 503)
        if action_name in {"approval", "delivery", "knowledge_registration"}:
            access_token, _ = _credential(request)
            dependencies.identity_service.consume_step_up(
                step_up_authorization=body.step_up_authorization,
                access_token=access_token,
                action_group=grant.required_step_up_action or "final_approval_or_knowledge_registration",
                target_id=body.output_version_id,
                policy_version=dependencies.settings.policy_version,
                trace_id=request.state.trace_id,
                operation=f"studio.{action_name}", idempotency_key=idempotency_key,
                operations_scope=_operations_workspace_scope(principal,body.workspace_id,dependencies),
            )
        result, replayed = await asyncio.to_thread(
            studio_workspace_service.action,
            _product_studio_context(principal, body.workspace_id, request, dependencies, body.notebook_id), action_name,
            body.model_dump(exclude={"workspace_id", "notebook_id", "step_up_authorization"}, exclude_none=True),
            idempotency_key,
        )
        response = JSONResponse({"data": _enum_json(result), "meta": {
            "trace_id": request.state.trace_id, "workspace_id": body.workspace_id, "replayed": replayed,
        }}, status_code=200 if replayed else 201)
        response.headers["ETag"] = f'"studio-action:{result["record_id"]}"'
        return response

    @app.get("/api/v1/studio-outputs/{output_id}/versions/{version_id}/exports/{format_name}")
    async def download_product_studio_export(
        output_id: str, version_id: str, format_name: str, request: Request,
        workspace_id: str = Query(), notebook_id: str = Query(),
        step_up_authorization: str | None = Header(default=None, alias="X-Daon-Step-Up-Authorization"),
        idempotency_key: str = Header(alias="Idempotency-Key"),
    ) -> Response:
        _require_query_keys(request, frozenset({"workspace_id", "notebook_id"}))
        _studio_idempotency_key(idempotency_key)
        if any(not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._:-]{0,255}", value)
               for value in (workspace_id, notebook_id, output_id, version_id)):
            raise HTTPException(status_code=400)
        if format_name not in {"docx", "pdf", "xlsx", "csv", "json", "svg", "png"}:
            raise HTTPException(status_code=400)
        principal = _principal(request, dependencies)
        grant = dependencies.authorization_service.authorize_action(
            principal=principal, workspace_id=workspace_id, action=Action.DELIVER,
            trace_id=request.state.trace_id, policy_version=dependencies.settings.policy_version,
        )
        if studio_workspace_service is None:
            raise StudioError("STUDIO_SERVICE_UNAVAILABLE", 503)
        target = "studio-export-" + hashlib.sha256(json.dumps(
            [workspace_id, notebook_id, output_id, version_id, format_name], separators=(",", ":"),
        ).encode("utf-8")).hexdigest()
        access_token, _ = _credential(request)
        dependencies.identity_service.consume_step_up(
            step_up_authorization=step_up_authorization, access_token=access_token,
            action_group=grant.required_step_up_action or "final_approval_or_knowledge_registration",
            target_id=target, policy_version=dependencies.settings.policy_version,
            trace_id=request.state.trace_id, operation=f"studio.export.{format_name}",
            idempotency_key=idempotency_key,
            operations_scope=_operations_workspace_scope(principal,workspace_id,dependencies),
        )
        exported = await asyncio.to_thread(
            studio_workspace_service.export,
            _product_studio_context(principal, workspace_id, request, dependencies, notebook_id),
            output_id, version_id, format_name,
        )
        return Response(exported.content, media_type=exported.media_type, headers={
            "Content-Disposition": f'attachment; filename="{exported.filename}"',
            "X-Content-Type-Options": "nosniff", "Cache-Control": "no-store",
            "ETag": f'"sha256:{exported.checksum_sha256}"',
        })

    @app.post("/api/v1/workspaces/{id}/studio/reports", status_code=201)
    async def create_studio_report(
        id: str, body: StudioReportBody, request: Request,
        idempotency_key: str = Header(alias="Idempotency-Key"),
    ) -> JSONResponse:
        _require_query_keys(request, frozenset())
        _studio_idempotency_key(idempotency_key)
        principal = _principal(request, dependencies)
        dependencies.authorization_service.authorize_action(
            principal=principal, workspace_id=id, action=Action.EDIT,
            trace_id=request.state.trace_id,
            policy_version=dependencies.settings.policy_version,
        )
        if studio_report_service is None:
            raise StudioReportError("STUDIO_SERVICE_UNAVAILABLE", status=503, retryable=True)
        if license_enforcement_enabled and _requires_runtime_license_precheck(
            studio_report_service,
        ):
            await asyncio.to_thread(
                license_service.require_new_generation,
                _license_context(principal, id, request, dependencies),
            )
        output, replayed = await asyncio.to_thread(
            studio_report_service.create,
            _studio_context(principal, id, request, dependencies, body.notebook_id),
            StudioReportCreateRequest(
                body.source_id, body.source_version_id, body.run_id, body.run_result_id,
                body.title, body.purpose,
            ),
            idempotency_key,
        )
        response = JSONResponse({
            "data": _dataclass_json(output),
            "meta": {
                "trace_id": request.state.trace_id, "workspace_id": id, "replayed": replayed,
            },
        }, status_code=200 if replayed else 201)
        response.headers["ETag"] = f'"studio-output:{output.output_version_id}"'
        return response

    @app.get("/api/v1/workspaces/{id}/studio/outputs")
    async def list_studio_outputs(
        id: str, request: Request, notebook_id: str = Query(),
    ) -> JSONResponse:
        _require_query_keys(request, frozenset({"notebook_id"}))
        principal = _principal(request, dependencies)
        dependencies.authorization_service.authorize_action(
            principal=principal, workspace_id=id, action=Action.VIEW,
            trace_id=request.state.trace_id,
            policy_version=dependencies.settings.policy_version,
        )
        if studio_report_service is None:
            raise StudioReportError("STUDIO_SERVICE_UNAVAILABLE", status=503, retryable=True)
        outputs = await asyncio.to_thread(
            studio_report_service.list_outputs,
            _studio_context(principal, id, request, dependencies, notebook_id),
        )
        content = {
            "data": {"outputs": [_dataclass_json(output) for output in outputs]},
            "meta": {"trace_id": request.state.trace_id, "workspace_id": id},
        }
        return _json_with_etag(content, json.dumps(content["data"], sort_keys=True))

    @app.get("/api/v1/workspaces/{id}/citations/{citation_id}/content")
    async def get_citation_content(
        id: str, citation_id: str, request: Request, notebook_id: str = Query(),
        representation: str | None = Query(default=None),
    ) -> Response:
        _require_query_keys(request, frozenset({"notebook_id", "representation"}))
        if representation not in {None, "audio"}:
            raise QuestionRepositoryError("CITATION_CONTENT_UNAVAILABLE", status=422)
        principal = _principal(request, dependencies)
        dependencies.authorization_service.authorize_action(
            principal=principal, workspace_id=id, action=Action.VIEW,
            trace_id=request.state.trace_id,
            policy_version=dependencies.settings.policy_version,
        )
        if citation_content_repository is None:
            raise QuestionRepositoryError(
                "CITATION_CONTENT_UNAVAILABLE", status=503, retryable=True,
            )
        content, locator = await asyncio.to_thread(
            citation_content_repository.read_citation_content,
            QuestionContext(
                principal.tenant_id, id, principal.user_id,
                request.state.trace_id, dependencies.settings.policy_version,
                notebook_id,
            ),
            citation_id,
            **({"representation": "audio"} if representation == "audio" else {}),
        )
        filename = re.sub(r"[^A-Za-z0-9._-]", "_", content.filename) or "source.pdf"
        headers = {
                "Content-Disposition": f'inline; filename="{filename}"',
                "X-Citation-Locator-Kind": locator["kind"],
                "ETag": f'"citation:{hashlib.sha256(content.content).hexdigest()}"',
                "Cache-Control": "private, no-store",
                "X-Content-Type-Options": "nosniff",
            }
        if locator["kind"] == "page":
            headers["X-Citation-Page"] = locator["value"]
        elif locator["kind"] == "image_region":
            headers["X-Citation-Region"] = locator["value"]
        return Response(
            content=content.content, media_type=content.media_type,
            headers=headers,
        )

    @app.get("/api/v1/session/tenants")
    async def list_session_tenants(request: Request) -> dict[str, object]:
        token, expected_kind = _credential(request)
        view = dependencies.identity_service.describe_access(
            token, trace_id=request.state.trace_id,
            policy_version=dependencies.settings.policy_version,
        )
        if view.client_kind is not expected_kind:
            raise IdentityError("ACCESS_INVALID", 401)
        if request.query_params or await request.body():
            raise IdentityError("INVALID_INPUT", 422)
        candidates = dependencies.identity_service.selectable_tenant_workspaces(
            user_id=view.principal.user_id,
        )
        names: dict[str, str] = {}
        try:
            with dependencies.authorization_repository.transaction() as connection:
                for tenant_id, _, _ in candidates:
                    if isinstance(dependencies.authorization_repository, PostgresAuthorizationRepository):
                        connection.execute("SELECT set_config('app.tenant_id', ?, true)", (tenant_id,))
                    row = connection.execute(
                        "SELECT display_name FROM tenants WHERE tenant_id=?", (tenant_id,),
                    ).fetchone()
                    if row is None or not str(row["display_name"]).strip():
                        raise IdentityError("TENANT_NAME_UNAVAILABLE", 503)
                    names[tenant_id] = str(row["display_name"])
        except IdentityError:
            raise
        except Exception as error:
            raise IdentityError("TENANT_NAME_UNAVAILABLE", 503) from error
        return {
            "data": {
                "current_tenant_id": view.principal.tenant_id,
                "tenants": [
                    {"tenant_id": tenant_id, "display_name": names[tenant_id],
                     "kind": kind, "workspace_id": workspace_id}
                    for tenant_id, kind, workspace_id in candidates
                ],
            },
            "meta": {"trace_id": request.state.trace_id},
        }

    @app.post("/api/v1/session/tenant")
    async def switch_session_tenant(request: Request) -> JSONResponse:
        token, expected_kind = _credential(request)
        view = dependencies.identity_service.describe_access(
            token, trace_id=request.state.trace_id,
            policy_version=dependencies.settings.policy_version,
        )
        if view.client_kind is not expected_kind:
            raise IdentityError("ACCESS_INVALID", 401)
        target_tenant_id = None
        try:
            if expected_kind is ClientKind.WEB:
                _require_validated_web_csrf(request, dependencies.settings)
            if request.query_params or request.headers.get("content-type", "").split(";", 1)[0].strip().lower() != "application/json":
                raise IdentityError("INVALID_INPUT", 422)
            try:
                body = await request.json()
            except (ValueError, UnicodeDecodeError) as error:
                raise IdentityError("INVALID_INPUT", 422) from error
            if (
                not isinstance(body, dict) or set(body) != {"tenant_id"}
                or not isinstance(body["tenant_id"], str)
                or _TRACE_ID.fullmatch(body["tenant_id"]) is None
            ):
                raise IdentityError("INVALID_INPUT", 422)
            target_tenant_id = body["tenant_id"]
            credentials, workspace_id = dependencies.identity_service.switch_session_tenant(
                access_token=token, tenant_id=target_tenant_id,
                trace_id=request.state.trace_id,
                policy_version=dependencies.settings.policy_version,
            )
        except IdentityError as error:
            if error.code in {"TENANT_UNAVAILABLE", "INVALID_INPUT", "CSRF_VALIDATION_FAILED"}:
                dependencies.identity_service.audit_tenant_switch_denied(
                    principal=view.principal, target_tenant_id=target_tenant_id,
                    reason_code=error.code, trace_id=request.state.trace_id,
                    policy_version=dependencies.settings.policy_version,
                )
            raise
        if expected_kind is ClientKind.WEB:
            response = JSONResponse({
                "data": {
                    "user_id": credentials.user_id, "tenant_id": credentials.tenant_id,
                    "workspace_id": workspace_id, "session_id": credentials.session_id,
                },
                "meta": {"trace_id": request.state.trace_id},
            })
            if credentials.session_id != view.principal.session_id:
                response.set_cookie(
                    WEB_SESSION_COOKIE, credentials.access_token,
                    max_age=WEB_SESSION_COOKIE_MAX_AGE, httponly=True,
                    secure=True, samesite="lax", path="/",
                )
            return response
        new_view = dependencies.identity_service.describe_access(
            credentials.access_token, trace_id=request.state.trace_id,
            policy_version=dependencies.settings.policy_version,
        )
        data = {
            "user_id": credentials.user_id, "tenant_id": credentials.tenant_id,
            "workspace_id": workspace_id, "session_id": credentials.session_id,
            "device_id": credentials.device_id, "client_kind": ClientKind.NATIVE.value,
            "delivery": "native_https_opaque_bearer",
            "expires_at": new_view.expires_at.isoformat(),
        }
        if credentials.session_id != view.principal.session_id:
            data["access_credential"] = credentials.access_token
            data["refresh_credential"] = credentials.refresh_token
        return JSONResponse({"data": data, "meta": {"trace_id": request.state.trace_id}})

    @app.get("/api/v1/session")
    async def session(request: Request) -> dict[str, object]:
        if dependencies.settings.dev_auth_bypass and dependencies.settings.profile in {"test", "development"}:
            principal = IdentityPrincipal(
                user_id="dev-user", session_id="dev-session", device_id="dev-device", tenant_id="dev-tenant",
            )
            workspace_id = dependencies.authorization_repository.primary_workspace_id(principal.tenant_id) or "dev-workspace"
            dependencies.authorization_repository.bootstrap_workspace(
                tenant_id=principal.tenant_id,
                workspace_id=workspace_id,
                owner_user_id=principal.user_id,
                owner_role=Role.PERSONAL_OWNER,
                workspace_kind="personal",
                data_area="cloud_sync",
                cost_limit_cents=1000,
                now=datetime.now(timezone.utc),
            )
            _ensure_development_cloud_scope(
                dependencies, principal=principal, workspace_id=workspace_id,
            )
            return {
                "data": {
                    "user_id": principal.user_id, "tenant_id": principal.tenant_id,
                    "login_id": principal.user_id,
                    "workspace_id": workspace_id, "session_id": principal.session_id,
                    "device_id": principal.device_id, "client_kind": ClientKind.WEB.value,
                    "delivery": "same_origin_secure_cookie", "expires_at": "2099-01-01T00:00:00Z",
                    "recovery_operations": [], "password_change_required": False,
                    "is_system_admin": principal.user_id in dependencies.settings.system_admin_user_ids,
                },
                "meta": {"trace_id": request.state.trace_id},
            }
        token, expected_kind = _credential(request)
        view = dependencies.identity_service.describe_access(
            token, trace_id=request.state.trace_id,
            policy_version=dependencies.settings.policy_version,
        )
        if view.client_kind is not expected_kind:
            raise IdentityError("ACCESS_INVALID", 401)
        principal = view.principal
        accessible = dependencies.identity_service.selectable_tenant_workspaces(
            user_id=principal.user_id,
        )
        workspace_id = next(
            (workspace for tenant, _, workspace in accessible if tenant == principal.tenant_id),
            None,
        ) or dependencies.authorization_repository.primary_workspace_id(principal.tenant_id)
        recovery_operations: list[str] = []
        if workspace_id is not None or principal.user_id not in dependencies.settings.system_admin_user_ids:
            try:
                dependencies.authorization_service.authorize_action(
                    principal=principal,
                    workspace_id=workspace_id,
                    action=Action.POLICY_MANAGE,
                    trace_id=request.state.trace_id,
                    policy_version=dependencies.settings.policy_version,
                )
            except AuthorizationError as error:
                if not (error.http_status == 403 and error.code in {"ACTION_DENIED", "ORGANIZATION_MEMBERSHIP_SUSPENDED"}):
                    raise
            else:
                recovery_operations = [
                    "cloud_backup_create",
                    "cloud_backup_get",
                    "cloud_backup_list",
                    "cloud_restore_cancel",
                    "cloud_restore_execute",
                    "cloud_restore_get",
                    "cloud_restore_preview",
                ]
        return {
            "data": {
                "user_id": principal.user_id,
                "login_id": view.login_id,
                "tenant_id": principal.tenant_id,
                "workspace_id": workspace_id,
                "session_id": principal.session_id,
                "device_id": principal.device_id,
                "client_kind": view.client_kind.value,
                "delivery": "same_origin_secure_cookie" if view.client_kind is ClientKind.WEB else "native_https_opaque_bearer",
                "expires_at": view.expires_at.isoformat().replace("+00:00", "Z"),
                "recovery_operations": recovery_operations,
                "password_change_required": view.password_change_required,
                "is_system_admin": principal.user_id in dependencies.settings.system_admin_user_ids,
            },
            "meta": {"trace_id": request.state.trace_id},
        }

    def notebook_context(principal: IdentityPrincipal, workspace_id: str, request: Request) -> NotebookContext:
        return NotebookContext(
            principal.tenant_id, workspace_id, principal.user_id,
            request.state.trace_id, dependencies.settings.policy_version,
        )

    def notebook_json(view: NotebookHomeView) -> dict[str, object]:
        return {
            "notebook_id": view.notebook_id, "title": view.title,
            "source_count": view.source_count, "output_count": view.output_count,
            "updated_at": view.updated_at, "status": view.status, "etag": view.etag,
        }

    def notebook_deletion_json(view: NotebookDeletionView) -> dict[str, object]:
        return {
            "request_id": view.request_id, "notebook_id": view.notebook_id,
            "status": view.state, "current_step": view.current_step,
            "attempts": view.attempts, "safe_error_code": view.safe_error_code,
            "requested_at": view.requested_at.isoformat().replace("+00:00", "Z"),
            "completed_at": view.completed_at.isoformat().replace("+00:00", "Z") if view.completed_at else None,
        }

    def notebook_context_json(selected) -> dict[str, object]:  # type: ignore[no-untyped-def]
        return {
            "notebook_id": selected.notebook_id,
            "sources": [
                {"source_id": source_id, "source_version_id": version_id}
                for source_id, version_id in selected.sources
            ],
            "knowledge_context_ids": list(selected.knowledge_context_ids),
            "conversation_thread_ids": list(selected.conversation_thread_ids),
            "studio_output_ids": list(selected.studio_output_ids),
            "output_version_ids": list(selected.output_version_ids),
            "generation_settings_ids": list(selected.generation_settings_ids),
            "source_deletion_requests": [_dataclass_json(item) for item in selected.source_deletion_requests],
            "conversation": None if selected.conversation is None else {
                "conversation_thread_id": selected.conversation.conversation_thread_id,
                "answer": {
                    "run_id": selected.conversation.run_id,
                    "run_result_id": selected.conversation.run_result_id,
                    "answer": selected.conversation.answer,
                    "insufficient": selected.conversation.insufficient,
                    "citations": [_dataclass_json(item) for item in selected.conversation.citations],
                },
            },
        }

    @app.post("/api/v1/workspaces/{id}/notebooks", status_code=201)
    async def create_notebook(
        id: str, body: NotebookCreateBody, request: Request,
        idempotency_key: str = Header(alias="Idempotency-Key"),
    ) -> JSONResponse:
        _require_query_keys(request, frozenset())
        _egress_idempotency_key(idempotency_key)
        principal = _principal(request, dependencies)
        dependencies.authorization_service.authorize_action(
            principal=principal, workspace_id=id, action=Action.EDIT,
            trace_id=request.state.trace_id, policy_version=dependencies.settings.policy_version,
        )
        view, replayed = await asyncio.to_thread(
            notebook_service.create, notebook_context(principal, id, request),
            title=body.title, description=body.description, idempotency_key=idempotency_key,
        )
        response = JSONResponse({
            "data": notebook_json(view),
            "meta": {"trace_id": request.state.trace_id, "workspace_id": id, "replayed": replayed},
        }, status_code=200 if replayed else 201)
        response.headers["ETag"] = view.etag
        return response

    @app.get("/api/v1/workspaces/{id}/notebooks")
    async def list_notebooks(id: str, request: Request) -> JSONResponse:
        _require_query_keys(request, frozenset())
        principal = _principal(request, dependencies)
        dependencies.authorization_service.authorize_action(
            principal=principal, workspace_id=id, action=Action.VIEW,
            trace_id=request.state.trace_id, policy_version=dependencies.settings.policy_version,
        )
        views = await asyncio.to_thread(
            notebook_service.list, notebook_context(principal, id, request),
        )
        content = {
            "data": [notebook_json(view) for view in views],
            "meta": {"trace_id": request.state.trace_id, "workspace_id": id},
        }
        return _json_with_etag(content, json.dumps(content["data"], sort_keys=True))

    @app.get("/api/v1/workspaces/{id}/notebooks/{notebook_id}")
    async def get_notebook(id: str, notebook_id: str, request: Request) -> JSONResponse:
        _require_query_keys(request, frozenset())
        principal = _principal(request, dependencies)
        dependencies.authorization_service.authorize_action(
            principal=principal, workspace_id=id, action=Action.VIEW,
            trace_id=request.state.trace_id, policy_version=dependencies.settings.policy_version,
        )
        view = await asyncio.to_thread(
            notebook_service.get, notebook_context(principal, id, request), notebook_id,
        )
        response = JSONResponse({
            "data": notebook_json(view),
            "meta": {"trace_id": request.state.trace_id, "workspace_id": id},
        })
        response.headers["ETag"] = view.etag
        return response

    @app.patch("/api/v1/workspaces/{id}/notebooks/{notebook_id}")
    async def update_notebook_title(
        id: str, notebook_id: str, body: NotebookTitleBody, request: Request,
        idempotency_key: str = Header(alias="Idempotency-Key"),
        if_match: str = Header(alias="If-Match"),
    ) -> JSONResponse:
        _require_query_keys(request, frozenset())
        _egress_idempotency_key(idempotency_key)
        principal = _principal(request, dependencies)
        dependencies.authorization_service.authorize_action(
            principal=principal, workspace_id=id, action=Action.EDIT,
            trace_id=request.state.trace_id, policy_version=dependencies.settings.policy_version,
        )
        view, replayed = await asyncio.to_thread(
            notebook_service.update_title, notebook_context(principal, id, request), notebook_id,
            title=body.title, expected_etag=if_match, idempotency_key=idempotency_key,
        )
        response = JSONResponse({
            "data": notebook_json(view),
            "meta": {"trace_id": request.state.trace_id, "workspace_id": id, "replayed": replayed},
        })
        response.headers["ETag"] = view.etag
        return response

    @app.delete("/api/v1/workspaces/{id}/notebooks/{notebook_id}", status_code=202)
    async def request_notebook_deletion(
        id: str, notebook_id: str, body: NotebookDeletionBody, request: Request,
        idempotency_key: str = Header(alias="Idempotency-Key"),
        if_match: str = Header(alias="If-Match"),
    ) -> JSONResponse:
        _require_query_keys(request, frozenset())
        _egress_idempotency_key(idempotency_key)
        principal = _principal(request, dependencies)
        dependencies.authorization_service.authorize_action(
            principal=principal, workspace_id=id, action=Action.EDIT,
            trace_id=request.state.trace_id, policy_version=dependencies.settings.policy_version,
        )
        view, replayed = await asyncio.to_thread(
            notebook_service.request_deletion, notebook_context(principal, id, request), notebook_id,
            title_confirmation=body.title_confirmation, expected_etag=if_match,
            idempotency_key=idempotency_key,
        )
        if not replayed and dependencies.notebook_deletion_worker is not None:
            deletion_context = notebook_context(principal, id, request)
            asyncio.create_task(asyncio.to_thread(
                dependencies.notebook_deletion_worker.process,
                deletion_context, view.request_id,
            ))
        return JSONResponse({
            "data": {"deletion_request_id": view.request_id, "status": view.state},
            "meta": {"trace_id": request.state.trace_id, "workspace_id": id, "replayed": replayed},
        }, status_code=200 if replayed else 202)

    @app.get("/api/v1/workspaces/{id}/notebooks/{notebook_id}/deletion-requests/{request_id}")
    async def get_notebook_deletion(
        id: str, notebook_id: str, request_id: str, request: Request,
    ) -> JSONResponse:
        _require_query_keys(request, frozenset())
        principal = _principal(request, dependencies)
        dependencies.authorization_service.authorize_action(
            principal=principal, workspace_id=id, action=Action.VIEW,
            trace_id=request.state.trace_id, policy_version=dependencies.settings.policy_version,
        )
        view = await asyncio.to_thread(
            notebook_service.get_deletion, notebook_context(principal, id, request), notebook_id, request_id,
        )
        return JSONResponse({
            "data": notebook_deletion_json(view),
            "meta": {"trace_id": request.state.trace_id, "workspace_id": id},
        })

    @app.get("/api/v1/workspaces/{id}/notebooks/{notebook_id}/context")
    async def get_notebook_selected_context(
        id: str, notebook_id: str, request: Request,
    ) -> JSONResponse:
        _require_query_keys(request, frozenset({'run_id', 'studio_job_id'}))
        selectors = dict(request.query_params)
        if (len(selectors) > 1 or len(request.query_params.multi_items()) != len(selectors)
            or any(re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9._:-]{0,127}', value) is None
                for value in selectors.values())):
            raise HTTPException(status_code=400)
        principal = _principal(request, dependencies)
        dependencies.authorization_service.authorize_action(
            principal=principal, workspace_id=id, action=Action.VIEW,
            trace_id=request.state.trace_id, policy_version=dependencies.settings.policy_version,
        )
        selected = await asyncio.to_thread(
            notebook_service.read_selected_context,
            notebook_context(principal, id, request), notebook_id, **selectors,
        )
        content = {
            "data": notebook_context_json(selected),
            "meta": {"trace_id": request.state.trace_id, "workspace_id": id},
        }
        response = JSONResponse(content)
        response.headers["ETag"] = selected.etag
        return response

    @app.post("/api/v1/workspaces/{id}/notebooks/{notebook_id}/source-unbindings")
    async def unbind_notebook_source(
        id: str, notebook_id: str, body: NotebookSourceUnbindBody, request: Request,
        idempotency_key: str = Header(alias="Idempotency-Key"),
        if_match: str = Header(alias="If-Match"),
    ) -> JSONResponse:
        _require_query_keys(request, frozenset())
        _egress_idempotency_key(idempotency_key)
        principal = _principal(request, dependencies)
        dependencies.authorization_service.authorize_action(
            principal=principal, workspace_id=id, action=Action.EDIT,
            trace_id=request.state.trace_id, policy_version=dependencies.settings.policy_version,
        )
        view, replayed = await asyncio.to_thread(
            notebook_service.unbind_source, notebook_context(principal, id, request), notebook_id,
            source_id=body.source_id, source_version_id=body.source_version_id,
            expected_etag=if_match, idempotency_key=idempotency_key,
        )
        response = JSONResponse({
            "data": {
                "notebook_id": view.notebook_id, "source_id": view.source_id,
                "source_version_id": view.source_version_id, "status": view.status,
            },
            "meta": {"trace_id": request.state.trace_id, "workspace_id": id, "replayed": replayed},
        })
        response.headers["ETag"] = view.etag
        return response

    @app.get("/api/v1/preferences/screen")
    async def get_screen_preferences(request: Request) -> dict[str, object]:
        _require_query_keys(request, frozenset())
        principal = _principal(request, dependencies)
        preferences = await asyncio.to_thread(
            screen_preference_service.get,
            ScreenPreferenceContext(principal.tenant_id, principal.user_id),
        )
        return {"data": preferences, "meta": {"trace_id": request.state.trace_id}}

    @app.put("/api/v1/preferences/screen")
    async def put_screen_preferences(
        body: ScreenPreferenceBody, request: Request,
    ) -> dict[str, object]:
        _require_query_keys(request, frozenset())
        principal = _principal(request, dependencies)
        preferences = await asyncio.to_thread(
            screen_preference_service.save,
            ScreenPreferenceContext(principal.tenant_id, principal.user_id),
            body.model_dump(),
        )
        return {"data": preferences, "meta": {"trace_id": request.state.trace_id}}

    @app.get("/api/v1/workspaces/{id}/license")
    async def get_product_license(id: str, request: Request) -> JSONResponse:
        _require_query_keys(request, frozenset())
        principal = _principal(request, dependencies)
        dependencies.authorization_service.authorize_action(
            principal=principal, workspace_id=id, action=Action.VIEW,
            trace_id=request.state.trace_id, policy_version=dependencies.settings.policy_version,
        )
        can_apply = False
        if principal.user_id in dependencies.settings.system_admin_user_ids:
            can_apply = True
        else:
            try:
                dependencies.authorization_service.organization_admin_workspace(
                    principal=principal, trace_id=request.state.trace_id,
                    policy_version=dependencies.settings.policy_version,
                )
            except AuthorizationError as error:
                if error.code != "ACTION_DENIED":
                    raise
            else:
                can_apply = True
        view = await asyncio.to_thread(
            license_service.get, _license_context(principal, id, request, dependencies),
        )
        content = {
            "data": {**view, "can_apply": can_apply},
            "meta": {"trace_id": request.state.trace_id, "workspace_id": id},
        }
        response = JSONResponse(content)
        response.headers["ETag"] = '"license:' + hashlib.sha256(
            canonical_json_bytes(content["data"])
        ).hexdigest()[:32] + '"'
        return response

    @app.post("/api/v1/organizations/{id}/license", status_code=201)
    async def apply_product_license(
        id: str, body: LicenseApplyBody, request: Request,
        idempotency_key: str = Header(alias="Idempotency-Key"),
    ) -> JSONResponse:
        _require_query_keys(request, frozenset())
        _egress_idempotency_key(idempotency_key)
        access_token, _ = _credential(request)
        principal = _principal(request, dependencies)
        if id != principal.tenant_id:
            raise AuthorizationError("ACCESS_DENIED", 403)
        if principal.user_id in dependencies.settings.system_admin_user_ids:
            workspace_id = dependencies.authorization_repository.primary_workspace_id(
                principal.tenant_id
            ) or f"{principal.tenant_id}-workspace"
        else:
            workspace_id = dependencies.authorization_service.organization_admin_workspace(
                principal=principal, trace_id=request.state.trace_id,
                policy_version=dependencies.settings.policy_version,
            )
        context = _license_context(principal, workspace_id, request, dependencies)
        replay = await asyncio.to_thread(
            license_service.replay, context, body.document, idempotency_key,
        )
        if replay is not None:
            view, replayed = replay
            claims_digest = hashlib.sha256(
                canonical_json_bytes(body.document["claims"])
            ).hexdigest()
        else:
            verified = await asyncio.to_thread(
                license_service.verify_document, context, body.document,
            )
            dependencies.identity_service.consume_step_up(
                step_up_authorization=body.step_up_authorization_id,
                access_token=access_token,
                action_group="organization_security_or_connector_policy_change",
                target_id=id, policy_version=dependencies.settings.policy_version,
                trace_id=request.state.trace_id,
                operation="license.organization.apply", idempotency_key=idempotency_key,
            )
            view, replayed = await asyncio.to_thread(
                license_service.apply, context, body.document, idempotency_key,
            )
            claims_digest = verified.claims_digest
        if not replayed:
            event_id = "license-" + hashlib.sha256(
                f"{principal.tenant_id}|{principal.user_id}|{idempotency_key}".encode()
            ).hexdigest()[:32]
            dependencies.audit_store.append(AuditEventDraft(
                event_id=event_id, occurred_at=datetime.now(timezone.utc),
                actor_id=principal.user_id, actor_type=ActorType.USER,
                tenant_id=principal.tenant_id, workspace_id=workspace_id,
                action="license.organization.applied", target_type="organization_license",
                target_id=id, outcome=AuditOutcome.SUCCEEDED,
                trace_id=request.state.trace_id, policy_version=dependencies.settings.policy_version,
                after={
                    "product": view["product"], "edition": view["edition"],
                    "expires_at": view["expires_at"], "status": view["status"],
                }, metadata={"reason_code": "LICENSE_APPLIED"},
            ))
        response = JSONResponse({
            "data": {**view, "can_apply": True},
            "meta": {"trace_id": request.state.trace_id, "replayed": replayed},
        }, status_code=200 if replayed else 201)
        response.headers["ETag"] = f'"license:{claims_digest[:32]}"'
        return response

    @app.post("/api/v1/session/step-up", status_code=201)
    async def issue_step_up(
        body: StepUpBody, request: Request,
        idempotency_key: str = Header(alias="Idempotency-Key"),
    ) -> dict[str, object]:
        _require_query_keys(request, frozenset())
        _egress_idempotency_key(idempotency_key)
        access_token, _ = _credential(request)
        if body.target_id.startswith("rrexec-v1:"):
            raise IdentityError("INVALID_INPUT",400)
        target_id=body.target_id
        if body.action_group=="permanent_delete_or_restore_rollback" and isinstance(recovery_service,PostgresRecoveryService):
            principal=_principal(request,dependencies)
            try:workspace_id=recovery_service.locate_restore_workspace(principal.tenant_id,body.target_id)
            except RecoveryError as error:
                if (error.code,error.status)!=("RESTORE_REQUEST_UNAVAILABLE",404):raise
            else:
                def current():
                    value=_principal(request,dependencies)
                    if (value.tenant_id,value.user_id,value.session_id,value.device_id)!=(principal.tenant_id,principal.user_id,principal.session_id,principal.device_id):
                        raise RecoveryError("CURRENT_ACCESS_DENIED",403)
                    dependencies.authorization_service.authorize_action(principal=value,workspace_id=workspace_id,action=Action.POLICY_MANAGE,
                        trace_id=request.state.trace_id,policy_version=dependencies.settings.policy_version)
                current()
                context=_recovery_context(principal,workspace_id,request,dependencies)
                view=recovery_service.get_restore_request(context,body.target_id)
                from .recovery import RegisteredRestorePreviewView
                if isinstance(view.preview,RegisteredRestorePreviewView):
                    view=await _recovery_heavy(recovery_service.registered_restore_step_up_preview,context,body.target_id,recheck=current)
                    current()
                    target_id=_registered_restore_execute_step_up_target(view)
        grant = dependencies.identity_service.issue_step_up_after_reauthentication(
            access_token=access_token, action_group=body.action_group, target_id=target_id,
            password=body.password,
            policy_version=dependencies.settings.policy_version, trace_id=request.state.trace_id,
            ttl_seconds=body.ttl_seconds, idempotency_key=idempotency_key,
        )
        return {"data": {
            "step_up_authorization": grant.authorization,
            "issued_at": grant.issued_at.isoformat(), "expires_at": grant.expires_at.isoformat(),
        }, "meta": {"trace_id": request.state.trace_id}}

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
        service = await asyncio.to_thread(notification_service_for, principal, request.state.trace_id)
        page = await asyncio.to_thread(service.list_notifications,
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
        service = await asyncio.to_thread(notification_service_for, principal, request.state.trace_id)
        item = await asyncio.to_thread(service.get_notification,
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
        service = await asyncio.to_thread(notification_service_for, principal, request.state.trace_id)
        item = await asyncio.to_thread(service.mark_read,
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
        service = await asyncio.to_thread(notification_service_for, principal, request.state.trace_id)
        page = await asyncio.to_thread(service.list_inbox,
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

    @app.get("/api/v1/workspaces/{id}/knowledge-packages")
    async def list_knowledge_packages(id: str, request: Request) -> JSONResponse:
        _require_query_keys(request, frozenset())
        principal = _principal(request, dependencies)
        dependencies.authorization_service.authorize_action(
            principal=principal, workspace_id=id, action=Action.VIEW,
            trace_id=request.state.trace_id,
            policy_version=dependencies.settings.policy_version,
        )
        items = knowledge_package_service.list_packages(
            _knowledge_package_context(principal, id, request, dependencies)
        )
        response = JSONResponse(content={
            "data": {"items": [_dataclass_json(item) for item in items]},
            "meta": {"trace_id": request.state.trace_id},
        })
        response.headers["ETag"] = '"knowledge-packages-' + hashlib.sha256(
            "|".join(f"{item.package_id}:{item.digest_sha256}" for item in items).encode()
        ).hexdigest()[:24] + '"'
        return response

    @app.post(
        "/api/v1/workspaces/{id}/knowledge-packages/{package_id}/offline-copies",
        status_code=201,
    )
    async def create_offline_knowledge_copy(
        id: str,
        package_id: str,
        body: KnowledgeCopyBody,
        request: Request,
        idempotency_key: str = Header(alias="Idempotency-Key"),
    ) -> JSONResponse:
        _require_query_keys(request, frozenset())
        _idempotency_key(idempotency_key)
        access_token, _ = _credential(request)
        principal = _principal(request, dependencies)
        dependencies.authorization_service.authorize_action(
            principal=principal, workspace_id=id, action=Action.APPROVE,
            requested_permissions=(Permission.DATA_AREA_MOVE,),
            trace_id=request.state.trace_id,
            policy_version=dependencies.settings.policy_version,
        )
        dependencies.identity_service.consume_step_up(
            step_up_authorization=body.step_up_authorization_id,
            access_token=access_token, action_group="data_area_move",
            target_id=package_id,
            policy_version=dependencies.settings.policy_version,
            trace_id=request.state.trace_id,
            operation="knowledge.offline_copy", idempotency_key=idempotency_key,
            operations_scope=_operations_workspace_scope(principal,id,dependencies),
        )
        grant = knowledge_package_service.create_offline_copy(
            _knowledge_package_context(principal, id, request, dependencies),
            package_id=package_id, device_id=body.device_id,
            step_up_authorization_id=body.step_up_authorization_id,
            idempotency_key=idempotency_key, approval_verified=True,
        )
        response = JSONResponse(
            content={"data": _dataclass_json(grant), "meta": {"trace_id": request.state.trace_id}},
            status_code=201,
        )
        response.headers["ETag"] = f'"offline-copy-{grant.digest_sha256[:24]}"'
        return response

    @app.get("/api/v1/offline-knowledge-copies/{copy_id}/content")
    async def read_offline_knowledge_copy(copy_id: str, request: Request) -> Response:
        _require_query_keys(request, frozenset())
        principal = _principal(request, dependencies)
        if request.state.session_view.client_kind is not ClientKind.NATIVE:
            raise KnowledgePackageError("CURRENT_ACCESS_DENIED", 403)
        workspace_id = request.headers.get("x-daon-workspace-id", "")
        dependencies.authorization_service.authorize_action(
            principal=principal, workspace_id=workspace_id, action=Action.VIEW,
            trace_id=request.state.trace_id,
            policy_version=dependencies.settings.policy_version,
        )
        content = knowledge_package_service.read_content(
            _knowledge_package_context(principal, workspace_id, request, dependencies),
            copy_id=copy_id,
        )
        response = Response(
            content=content,
            media_type="application/octet-stream",
            headers={"Cache-Control": "no-store", "X-Content-Type-Options": "nosniff"},
        )
        response.headers["ETag"] = f'"offline-copy-content-{hashlib.sha256(content).hexdigest()[:24]}"'
        return response

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
            items=tuple(SyncItemInput(
                **(item.model_dump() | {
                    "dependency_item_ids": tuple(item.dependency_item_ids),
                })
            ) for item in body.items),
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
            operation="sync.approve", idempotency_key=idempotency_key,
            operations_scope=_operations_workspace_scope(principal,workspace_id,dependencies),
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
        workspace_id = retention_request_service.locate_source_workspace(principal.tenant_id, id)
        dependencies.authorization_service.authorize_action(
            principal=principal, workspace_id=workspace_id, action=Action.EDIT,
            trace_id=request.state.trace_id,
            policy_version=dependencies.settings.policy_version,
        )
        await asyncio.to_thread(
            notebook_service.get, notebook_context(principal, workspace_id, request), body.notebook_id,
        )
        access_token, _ = _credential(request)
        # A fresh confirmation may resume the same durable business request.
        # Bind the one-use authorization separately; never persist the token.
        authorization_key = hashlib.sha256(json.dumps([
            idempotency_key, workspace_id, body.notebook_id, id,
            hashlib.sha256(body.step_up_authorization_id.encode()).hexdigest(),
        ], separators=(",", ":")).encode()).hexdigest()
        dependencies.identity_service.consume_step_up(
            access_token=access_token,
            step_up_authorization=body.step_up_authorization_id,
            action_group="permanent_delete_or_restore_rollback", target_id=id,
            policy_version=dependencies.settings.policy_version,
            trace_id=request.state.trace_id,
            operation="retention.source.delete", idempotency_key=authorization_key,
            operations_scope=_operations_workspace_scope(principal,workspace_id,dependencies),
        )
        view = await asyncio.to_thread(
            retention_request_service.create_request,
            _retention_context(principal, workspace_id, request, dependencies),
            source_id=id, notebook_id=body.notebook_id, step_up_verified=True, authorization_key=authorization_key,
            inventory=None,
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
        workspace_id = retention_request_service.locate_workspace(principal.tenant_id, id)
        dependencies.authorization_service.authorize_action(
            principal=principal, workspace_id=workspace_id, action=Action.VIEW,
            trace_id=request.state.trace_id,
            policy_version=dependencies.settings.policy_version,
        )
        view = retention_request_service.get_request(
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
        workspace_id = retention_request_service.locate_workspace(principal.tenant_id, id)
        dependencies.authorization_service.authorize_action(
            principal=principal, workspace_id=workspace_id, action=Action.EDIT,
            trace_id=request.state.trace_id,
            policy_version=dependencies.settings.policy_version,
        )
        view = retention_request_service.cancel(
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
            operation="retention.purge", idempotency_key=idempotency_key,
            operations_scope=_operations_workspace_scope(principal,workspace_id,dependencies),
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
            operation="retention.legal_hold.apply", idempotency_key=idempotency_key,
            operations_scope=_operations_workspace_scope(principal,workspace_id,dependencies),
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
            operation="retention.legal_hold.release", idempotency_key=idempotency_key,
            operations_scope=_operations_workspace_scope(principal,workspace_id,dependencies),
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

    def _provider_admin_context(request: Request) -> tuple[IdentityPrincipal, ProviderConnectionAdminContext]:
        principal = _principal(request, dependencies)
        if principal.user_id not in dependencies.settings.system_admin_user_ids:
            raise AuthorizationError("ACCESS_DENIED", 403)
        return principal, ProviderConnectionAdminContext(
            tenant_id=principal.tenant_id, actor_id=principal.user_id,
            trace_id=request.state.trace_id, policy_version=dependencies.settings.policy_version,
        )

    def _provider_connection_context(request: Request) -> tuple[IdentityPrincipal, ProviderConnectionAdminContext]:
        """Read shared system connections for any authenticated user.

        The projection is secret-safe. Full connection/model mutations remain
        protected by _provider_admin_context; ordinary users only reach the
        credential rotation endpoint below.
        """
        principal = _principal(request, dependencies)
        return principal, ProviderConnectionAdminContext(
            tenant_id=principal.tenant_id, actor_id=principal.user_id,
            trace_id=request.state.trace_id, policy_version=dependencies.settings.policy_version,
        )

    def _provider_admin_mutation_context(
        request: Request, *, idempotency_key: str,
    ) -> ProviderConnectionAdminContext:
        _egress_idempotency_key(idempotency_key)
        _principal_value, context = _provider_admin_context(request)
        return context

    def _provider_credential_mutation_context(
        request: Request, *, idempotency_key: str,
    ) -> ProviderConnectionAdminContext:
        _egress_idempotency_key(idempotency_key)
        _principal_value, context = _provider_admin_context(request)
        return context

    def _provider_admin_service() -> Any:
        if provider_connection_service is None:
            raise ProviderConnectionAdminError("PROVIDER_CATALOG_UNAVAILABLE", 503, retryable=True)
        return provider_connection_service

    @app.get("/api/v1/provider-credentials")
    async def list_user_provider_credentials(request: Request) -> JSONResponse:
        _require_query_keys(request, frozenset())
        principal = _principal(request, dependencies)
        if user_provider_credential_service is None:
            raise UserProviderCredentialError("PERSISTENCE_UNAVAILABLE", 503)
        items = await asyncio.to_thread(
            user_provider_credential_service.list_credentials,
            tenant_id=principal.tenant_id, user_id=principal.user_id,
        )
        return JSONResponse({
            "data": {"credentials": [
                {"connection_id": item.connection_id, "provider_code": item.provider_code,
                 "configured": item.configured, "credential_version": item.credential_version,
                 "verification_status": item.verification_status}
                for item in items
            ]},
            "meta": {"trace_id": request.state.trace_id},
        })

    @app.put("/api/v1/provider-credentials/{connection_id}")
    async def replace_user_provider_credential(
        connection_id: str, body: UserProviderCredentialBody, request: Request,
    ) -> JSONResponse:
        _require_query_keys(request, frozenset())
        principal = _principal(request, dependencies)
        if user_provider_credential_service is None:
            raise UserProviderCredentialError("PERSISTENCE_UNAVAILABLE", 503)
        try:
            item = await asyncio.to_thread(
                user_provider_credential_service.replace_credential,
                tenant_id=principal.tenant_id, user_id=principal.user_id,
                connection_id=connection_id, credential=body.credential,
                expected_version=body.expected_version,
            )
        finally:
            body.credential = ""
        return JSONResponse({
            "data": {"connection_id": item.connection_id, "provider_code": item.provider_code,
                     "configured": item.configured, "credential_version": item.credential_version,
                     "verification_status": item.verification_status},
            "meta": {"trace_id": request.state.trace_id},
        })

    @app.delete("/api/v1/provider-credentials/{connection_id}")
    async def delete_user_provider_credential(
        connection_id: str, body: UserProviderCredentialDeleteBody, request: Request,
    ) -> Response:
        _require_query_keys(request, frozenset())
        principal = _principal(request, dependencies)
        if user_provider_credential_service is None:
            raise UserProviderCredentialError("PERSISTENCE_UNAVAILABLE", 503)
        await asyncio.to_thread(
            user_provider_credential_service.delete_credential,
            tenant_id=principal.tenant_id, user_id=principal.user_id,
            connection_id=connection_id, expected_version=body.expected_version,
        )
        return Response(status_code=204)

    @app.get("/api/v1/admin/provider-health-settings")
    async def get_provider_health_settings(request: Request) -> JSONResponse:
        _require_query_keys(request, frozenset())
        _principal_value, context = _provider_admin_context(request)
        if provider_health_settings_service is None:
            raise ProviderConnectionAdminError("PROVIDER_CATALOG_UNAVAILABLE", 503, retryable=True)
        settings_view = await asyncio.to_thread(
            provider_health_settings_service.get, context,
        )
        return JSONResponse({
            "data": {
                "interval_minutes": settings_view.interval_minutes,
                "version": settings_view.version,
            },
            "meta": {"trace_id": request.state.trace_id},
        })

    @app.patch("/api/v1/admin/provider-health-settings")
    async def save_provider_health_settings(
        body: ProviderHealthSettingsBody, request: Request,
    ) -> JSONResponse:
        _require_query_keys(request, frozenset())
        _principal_value, context = _provider_admin_context(request)
        if provider_health_settings_service is None:
            raise ProviderConnectionAdminError("PROVIDER_CATALOG_UNAVAILABLE", 503, retryable=True)
        settings_view = await asyncio.to_thread(
            provider_health_settings_service.save, context,
            interval_minutes=body.interval_minutes,
            expected_version=body.expected_version,
        )
        return JSONResponse({
            "data": {
                "interval_minutes": settings_view.interval_minutes,
                "version": settings_view.version,
            },
            "meta": {"trace_id": request.state.trace_id},
        })

    @app.get("/api/v1/admin/provider-connections")
    async def list_provider_connections(request: Request) -> JSONResponse:
        _require_query_keys(request, frozenset())
        _principal_value, context = _provider_connection_context(request)
        items = await asyncio.to_thread(_provider_admin_service().list_connections, context)
        return _json_with_etag(
            {"data": items, "meta": {"trace_id": request.state.trace_id}},
            "provider-connections:"
            + "|".join(
                f"{item['connection_id']}:{item['version']}:{item['catalog_version']}"
                for item in items
            ),
        )

    @app.post("/api/v1/admin/provider-connections/model-preview")
    async def preview_provider_models(
        body: ProviderConnectionModelPreviewBody, request: Request,
    ) -> JSONResponse:
        _require_query_keys(request, frozenset())
        _principal_value, context = _provider_admin_context(request)
        try:
            model_ids = await asyncio.to_thread(
                _provider_admin_service().preview_models,
                context, connection_id=body.connection_id, provider_code=body.provider_code,
                adapter_type=body.adapter_type, base_url=body.base_url,
                credential=body.credential,
            )
        finally:
            body.credential = ""
        return JSONResponse({
            "data": {"model_ids": list(model_ids)},
            "meta": {"trace_id": request.state.trace_id},
        })

    @app.post("/api/v1/admin/provider-connections", status_code=201)
    async def create_provider_connection(
        body: ProviderConnectionCreateBody, request: Request,
        idempotency_key: str = Header(alias="Idempotency-Key"),
    ) -> JSONResponse:
        _require_query_keys(request, frozenset())
        context = _provider_admin_mutation_context(request, idempotency_key=idempotency_key)
        command = ProviderConnectionCreateCommand(
            connection_id=body.connection_id, provider_code=body.provider_code,
            display_name=body.display_name, base_url=body.base_url, credential=body.credential,
            provider_name=body.provider_name or body.provider_code,
            logical_model_ids=tuple(body.logical_model_ids), enabled=body.enabled,
            expected_version=body.expected_version,
            allowed_model_ids=tuple(body.allowed_model_ids), access_mode=body.access_mode,
            credential_requirement=body.credential_requirement, short_code=body.short_code,
            adapter_type=body.adapter_type, test_credential=body.test_credential,
            auto_model_id=body.auto_model_id,
        )
        try:
            item, replayed = await asyncio.to_thread(
                _provider_admin_service().create_connection, context, command, idempotency_key,
            )
        finally:
            body.credential = None
            body.test_credential = None
        response = _json_with_etag(
            {"data": item, "meta": {"trace_id": request.state.trace_id, "replayed": replayed}},
            f"provider-connection:{item['connection_id']}:{item['version']}:{item['catalog_version']}",
        )
        response.status_code = 200 if replayed else 201
        return response

    @app.put("/api/v1/admin/provider-connections/{connection_id}")
    async def update_provider_connection(
        connection_id: str, body: ProviderConnectionUpdateBody, request: Request,
        idempotency_key: str = Header(alias="Idempotency-Key"),
    ) -> JSONResponse:
        _require_query_keys(request, frozenset())
        context = _provider_admin_mutation_context(request, idempotency_key=idempotency_key)
        command = ProviderConnectionUpdateCommand(
            display_name=body.display_name, base_url=body.base_url, credential=body.credential,
            provider_name=body.provider_name or "",
            logical_model_ids=tuple(body.logical_model_ids), enabled=body.enabled,
            expected_version=body.expected_version,
            allowed_model_ids=tuple(body.allowed_model_ids), access_mode=body.access_mode,
            credential_requirement=body.credential_requirement, short_code=body.short_code,
            adapter_type=body.adapter_type, test_credential=body.test_credential,
            auto_model_id=body.auto_model_id,
            auto_model_id_specified="auto_model_id" in body.model_fields_set,
        )
        try:
            item, replayed = await asyncio.to_thread(
                _provider_admin_service().update_connection,
                context, connection_id, command, idempotency_key,
            )
        finally:
            body.credential = None
            body.test_credential = None
        return _json_with_etag(
            {"data": item, "meta": {"trace_id": request.state.trace_id, "replayed": replayed}},
            f"provider-connection:{item['connection_id']}:{item['version']}:{item['catalog_version']}",
        )

    @app.delete("/api/v1/admin/provider-connections/{connection_id}", status_code=204)
    async def delete_provider_connection(
        connection_id: str, body: ProviderConnectionMutationBody, request: Request,
        idempotency_key: str = Header(alias="Idempotency-Key"),
    ) -> Response:
        _require_query_keys(request, frozenset())
        context = _provider_admin_mutation_context(request, idempotency_key=idempotency_key)
        await asyncio.to_thread(
            _provider_admin_service().delete_connection,
            context, connection_id, body.expected_version, idempotency_key,
        )
        return Response(status_code=204)

    @app.delete("/api/v1/admin/provider-connections/{connection_id}/credential")
    async def delete_provider_credential(
        connection_id: str, body: ProviderConnectionMutationBody, request: Request,
        idempotency_key: str = Header(alias="Idempotency-Key"),
    ) -> JSONResponse:
        _require_query_keys(request, frozenset())
        context = _provider_credential_mutation_context(request, idempotency_key=idempotency_key)
        item, replayed = await asyncio.to_thread(
            _provider_admin_service().delete_credential,
            context, connection_id, body.expected_version, idempotency_key,
        )
        return _json_with_etag(
            {"data": item, "meta": {"trace_id": request.state.trace_id, "replayed": replayed}},
            f"provider-connection:{item['connection_id']}:{item['version']}:{item['catalog_version']}",
        )

    @app.post("/api/v1/admin/provider-connections/{connection_id}/credential")
    async def replace_provider_credential(
        connection_id: str, body: ProviderCredentialReplaceBody, request: Request,
        idempotency_key: str = Header(alias="Idempotency-Key"),
    ) -> JSONResponse:
        _require_query_keys(request, frozenset())
        context = _provider_credential_mutation_context(request, idempotency_key=idempotency_key)
        command = ProviderCredentialReplaceCommand(
            credential=body.credential, expected_version=body.expected_version,
        )
        try:
            item, replayed = await asyncio.to_thread(
                _provider_admin_service().replace_credential,
                context, connection_id, command, idempotency_key,
            )
        finally:
            body.credential = ""
        return _json_with_etag(
            {"data": item, "meta": {"trace_id": request.state.trace_id, "replayed": replayed}},
            f"provider-connection:{item['connection_id']}:{item['version']}:{item['catalog_version']}",
        )

    @app.post("/api/v1/admin/provider-catalog/{connection_id}/refresh")
    async def refresh_provider_catalog(
        connection_id: str, body: ProviderConnectionMutationBody, request: Request,
        idempotency_key: str = Header(alias="Idempotency-Key"),
    ) -> JSONResponse:
        _require_query_keys(request, frozenset())
        context = _provider_admin_mutation_context(request, idempotency_key=idempotency_key)
        item, replayed = await asyncio.to_thread(
            _provider_admin_service().refresh_catalog,
            context, connection_id, body.expected_version, idempotency_key,
        )
        return _json_with_etag(
            {"data": item, "meta": {"trace_id": request.state.trace_id, "replayed": replayed}},
            f"provider-connection:{item['connection_id']}:{item['version']}:{item['catalog_version']}",
        )

    @app.patch("/api/v1/admin/provider-models/{connection_id}/{model_id}/capabilities")
    async def correct_provider_capabilities(
        connection_id: str, model_id: str, body: ProviderCapabilityBody, request: Request,
        idempotency_key: str = Header(alias="Idempotency-Key"),
    ) -> JSONResponse:
        _require_query_keys(request, frozenset())
        context = _provider_admin_mutation_context(request, idempotency_key=idempotency_key)
        command = ProviderCapabilityCommand(
            effective_capabilities=tuple(body.effective_capabilities),
            expected_version=body.expected_version,
        )
        item, replayed = await asyncio.to_thread(
            _provider_admin_service().correct_capabilities,
            context, connection_id, model_id, command, idempotency_key,
        )
        return _json_with_etag(
            {"data": item, "meta": {"trace_id": request.state.trace_id, "replayed": replayed}},
            f"provider-model:{connection_id}:{model_id}:{item['catalog_version']}",
        )

    @app.get("/api/v1/model-profiles")
    async def list_model_profiles(request: Request, workspace_id: str = Query()) -> JSONResponse:
        _require_query_keys(request, frozenset({"workspace_id", "cursor", "limit", "filter", "search"}))
        principal = _principal(request, dependencies)
        dependencies.authorization_service.authorize_action(
            principal=principal, workspace_id=workspace_id, action=Action.VIEW,
            trace_id=request.state.trace_id,
            policy_version=dependencies.settings.policy_version,
        )
        snapshot = provider_settings_service.snapshot(
            _provider_settings_context(principal, workspace_id, request, dependencies)
        )
        return _json_with_etag(
            {
                "data": [
                    {**_dataclass_json(item), "etag": item.etag}
                    for item in snapshot.profiles
                ],
                "meta": {"trace_id": request.state.trace_id, "workspace_id": workspace_id},
            },
            "|".join(item.etag for item in snapshot.profiles),
        )

    @app.get("/api/v1/system/operations/status")
    async def get_system_operations_status(request: Request) -> JSONResponse:
        _require_query_keys(request,frozenset())
        if await request.body():raise HTTPException(status_code=400,detail="SYSTEM_OPERATIONS_INPUT_INVALID")
        principal=_principal(request,dependencies)
        if principal.user_id not in dependencies.settings.system_admin_user_ids:
            raise AuthorizationError("ACTION_DENIED",403)
        async def checked(call):
            try:return await asyncio.to_thread(call) is True
            except Exception:return False
        database_ready=await checked(lambda: dependencies.cloud_store.readiness().ready) if dependencies.cloud_store is not None else False
        object_ready=await checked(dependencies.object_storage.health) if dependencies.object_storage is not None else False
        repository=PostgresOperationsStatusRepository(dependencies.cloud_store) if dependencies.cloud_store is not None else None
        view=await asyncio.to_thread(SystemOperationsStatusService(repository).read,
            api_ready=True,database_ready=database_ready,object_storage_ready=object_ready,
            checked_at=datetime.now(timezone.utc).isoformat().replace("+00:00","Z"))
        current=_principal(request,dependencies)
        if (current.user_id,current.tenant_id,current.session_id,current.device_id)!=(principal.user_id,principal.tenant_id,principal.session_id,principal.device_id) \
            or current.user_id not in dependencies.settings.system_admin_user_ids:
            raise AuthorizationError("CURRENT_ACCESS_DENIED",403)
        response = _json_with_etag({"data":view,"meta":{"trace_id":request.state.trace_id}},
            f"system-operations|{view['checked_at']}|{principal.user_id}|{principal.session_id}")
        response.headers["Cache-Control"] = "no-store"
        return response

    def require_operations_read(principal,workspace_id,scope):
        # 기존 grant 계산의 읽기 경로만 사용한다. 상태 조회로 audit/권한을 생성하지 않는다.
        with dependencies.authorization_service._repository.transaction() as connection:
            workspace=connection.execute("SELECT * FROM auth_workspaces WHERE tenant_id=? AND workspace_id=?",
                (principal.tenant_id,workspace_id)).fetchone()
            if workspace is None:raise AuthorizationError("CURRENT_ACCESS_DENIED",403)
            dependencies.authorization_service._assert_organization_active(connection,principal,workspace)
            binding=dependencies.authorization_service._role_binding(connection,principal,workspace)
            action=Action.POLICY_MANAGE if scope=="workspace_visible" else Action.VIEW
            if dependencies.authorization_service._action_grant(connection,principal=principal,workspace=workspace,
                    binding=binding,action=action,requested_permissions=()) is None:
                raise AuthorizationError("CURRENT_ACCESS_DENIED",403)

    @app.get("/api/v1/workspaces/{id}/operations/status")
    async def get_workspace_operations_status(id: str, request: Request) -> JSONResponse:
        _require_query_keys(request, frozenset({"scope"}))
        if len(request.query_params.getlist("scope"))>1 or await request.body():
            raise HTTPException(status_code=400,detail="OPERATIONS_STATUS_INPUT_INVALID")
        scope=request.query_params.get("scope","self")
        principal = _principal(request, dependencies)
        try:
            context=OperationsStatusContext(principal.tenant_id,id,principal.user_id,
                request.state.trace_id,dependencies.settings.policy_version,scope)
        except ValueError:raise HTTPException(status_code=400,detail="OPERATIONS_STATUS_INPUT_INVALID") from None
        require_operations_read(principal,id,scope)
        if operations_status_service is None:
            raise OperationsStatusError()
        try:
            snapshot = provider_settings_service.snapshot(
                _provider_settings_context(principal, id, request, dependencies)
            )
            database_ready = (
                False
                if dependencies.cloud_store is None
                else (await asyncio.to_thread(dependencies.cloud_store.readiness)).ready
            )
            object_storage_ready = (
                False
                if dependencies.object_storage is None
                else await asyncio.to_thread(dependencies.object_storage.health)
            )
            view = await asyncio.to_thread(
                operations_status_service.read,
                context,
                active_providers=sum(
                    1 for item in snapshot.profiles
                    if item.active and item.credential_configured
                ),
                selected_deployments=sum(
                    1 for item in snapshot.deployments if item.active and item.selected
                ),
                api_ready=dependencies.state.ready,
                database_ready=database_ready,
                object_storage_ready=object_storage_ready,
                checked_at=datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
            )
        except OperationsStatusError:
            raise
        except (IdentityError, AuthorizationError, NotificationError, HTTPException):
            raise
        except Exception:
            raise OperationsStatusError() from None
        current = _principal(request, dependencies)
        if (current.tenant_id, current.user_id, current.session_id, current.device_id) != (
                principal.tenant_id, principal.user_id, principal.session_id, principal.device_id):
            raise AuthorizationError("CURRENT_ACCESS_DENIED", 403)
        require_operations_read(current,id,scope)
        response = _json_with_etag({
            "data": _dataclass_json(view),
            "meta": {"trace_id": request.state.trace_id, "workspace_id": id},
        }, f"{id}|{scope}|{view.overall_status}|{view.checked_at}")
        response.headers["Cache-Control"]="no-store"
        return response

    @app.get("/api/v1/workspaces/{id}/output-version-settings")
    async def get_workspace_output_version_settings(id: str, request: Request) -> JSONResponse:
        _require_query_keys(request, frozenset())
        principal = _principal(request, dependencies)
        dependencies.authorization_service.authorize_action(
            principal=principal, workspace_id=id, action=Action.VIEW,
            trace_id=request.state.trace_id,
            policy_version=dependencies.settings.policy_version,
        )
        view = await asyncio.to_thread(
            output_version_settings_service.get,
            OutputVersionSettingsContext(principal.tenant_id, id, principal.user_id),
        )
        response = JSONResponse({
            "data": _dataclass_json(view),
            "meta": {"trace_id": request.state.trace_id, "workspace_id": id},
        })
        response.headers["ETag"] = view.etag
        return response

    @app.get("/api/v1/workspaces/{id}/model-defaults")
    async def get_workspace_model_defaults(id: str, request: Request) -> JSONResponse:
        _require_query_keys(request, frozenset())
        if workspace_model_defaults_service is None:
            raise WorkspaceModelDefaultsError(
                "WORKSPACE_MODEL_DEFAULTS_UNAVAILABLE", 503, retryable=True,
            )
        principal = _principal(request, dependencies)
        dependencies.authorization_service.authorize_action(
            principal=principal, workspace_id=id, action=Action.VIEW,
            trace_id=request.state.trace_id,
            policy_version=dependencies.settings.policy_version,
        )
        result = await asyncio.to_thread(
            workspace_model_defaults_service.read,
            WorkspaceModelDefaultsContext(
                principal.tenant_id, id, principal.user_id,
                request.state.trace_id, dependencies.settings.policy_version,
            ),
        )
        data = {key: value for key, value in result.items() if key != "etag"}
        response = JSONResponse({
            "data": data,
            "meta": {"trace_id": request.state.trace_id, "workspace_id": id},
        })
        response.headers["ETag"] = str(result["etag"])
        return response

    @app.patch("/api/v1/workspaces/{id}/model-defaults")
    async def patch_workspace_model_defaults(
        id: str,
        body: WorkspaceModelDefaultBody,
        request: Request,
        if_match: str = Header(alias="If-Match"),
        idempotency_key: str = Header(alias="Idempotency-Key"),
    ) -> JSONResponse:
        _require_query_keys(request, frozenset())
        _idempotency_key(idempotency_key)
        if workspace_model_defaults_service is None:
            raise WorkspaceModelDefaultsError(
                "WORKSPACE_MODEL_DEFAULTS_UNAVAILABLE", 503, retryable=True,
            )
        principal = _principal(request, dependencies)
        dependencies.authorization_service.authorize_action(
            principal=principal, workspace_id=id, action=Action.POLICY_MANAGE,
            trace_id=request.state.trace_id,
            policy_version=dependencies.settings.policy_version,
        )
        result, replayed = await asyncio.to_thread(
            workspace_model_defaults_service.save,
            WorkspaceModelDefaultsContext(
                principal.tenant_id, id, principal.user_id,
                request.state.trace_id, dependencies.settings.policy_version,
            ),
            capability=body.capability,
            connection_id=body.connection_id,
            model_id=body.model_id,
            expected_version=body.expected_version,
            expected_etag=if_match,
            idempotency_key=idempotency_key,
        )
        data = {key: value for key, value in result.items() if key != "etag"}
        response = JSONResponse({
            "data": data,
            "meta": {
                "trace_id": request.state.trace_id,
                "workspace_id": id,
                "replayed": replayed,
            },
        })
        response.headers["ETag"] = str(result["etag"])
        return response

    @app.get("/api/v1/workspaces/{id}/sync-operations")
    async def list_sync_operations(id: str, request: Request) -> JSONResponse:
        _require_query_keys(request, frozenset())
        principal = _principal(request, dependencies)
        dependencies.authorization_service.authorize_action(
            principal=principal, workspace_id=id, action=Action.VIEW,
            trace_id=request.state.trace_id,
            policy_version=dependencies.settings.policy_version,
        )
        operations = sync_service.list_operations(
            _sync_context(principal, id, request, dependencies)
        )
        response = JSONResponse(content={
            "data": {"operations": [_sync_view_json(view) for view in operations]},
            "meta": {"trace_id": request.state.trace_id, "workspace_id": id},
        })
        digest = hashlib.sha256("|".join(
            f"{view.operation_id}:{view.version}" for view in operations
        ).encode()).hexdigest()[:24]
        response.headers["ETag"] = f'"sync-list:{id}:{digest}"'
        return response

    @app.patch("/api/v1/workspaces/{id}/output-version-settings")
    async def patch_workspace_output_version_settings(
        id: str,
        body: OutputVersionSettingsBody,
        request: Request,
        if_match: str = Header(alias="If-Match"),
        idempotency_key: str = Header(alias="Idempotency-Key"),
    ) -> JSONResponse:
        _require_query_keys(request, frozenset())
        _studio_idempotency_key(idempotency_key)
        expected_etag = f'"output-version-settings:{id}:{body.expected_version}"'
        if if_match != expected_etag:
            raise OutputVersionSettingsError("VERSION_CONFLICT", 409)
        principal = _principal(request, dependencies)
        dependencies.authorization_service.authorize_action(
            principal=principal, workspace_id=id, action=Action.EDIT,
            trace_id=request.state.trace_id,
            policy_version=dependencies.settings.policy_version,
        )
        view = await asyncio.to_thread(
            output_version_settings_service.save,
            OutputVersionSettingsContext(principal.tenant_id, id, principal.user_id),
            body.default_formats,
            expected_version=body.expected_version,
            idempotency_key=idempotency_key,
        )
        response = JSONResponse({
            "data": _dataclass_json(view),
            "meta": {"trace_id": request.state.trace_id, "workspace_id": id},
        })
        response.headers["ETag"] = view.etag
        return response

    @app.get("/api/v1/workspaces/{id}/egress-policy")
    async def get_workspace_egress_policy(id: str, request: Request) -> JSONResponse:
        _require_query_keys(request, frozenset())
        if egress_policy_service is None:
            raise EgressPolicyError("EGRESS_POLICY_UNAVAILABLE", 503)
        principal = _principal(request, dependencies)
        dependencies.authorization_service.authorize_action(
            principal=principal, workspace_id=id, action=Action.VIEW,
            trace_id=request.state.trace_id,
            policy_version=dependencies.settings.policy_version,
        )
        view = egress_policy_service.get_effective(
            _egress_policy_context(principal, id, request, dependencies)
        )
        response = JSONResponse({
            "data": {
                **view.frozen_context(), "parent_locked": view.parent_locked,
                "organization_etag": view.organization_etag,
                "workspace_etag": view.workspace_etag,
                "organization_policy": view.organization_policy,
                "workspace_policy": view.workspace_policy,
            },
            "meta": {"trace_id": request.state.trace_id},
        })
        response.headers["ETag"] = view.etag
        return response

    async def create_egress_policy_version(
        *, scope_type: str, scope_id: str, workspace_id: str,
        body: EgressPolicyVersionBody, request: Request,
        if_match: str, idempotency_key: str,
    ) -> JSONResponse:
        if egress_policy_service is None:
            raise EgressPolicyError("EGRESS_POLICY_UNAVAILABLE", 503)
        _egress_idempotency_key(idempotency_key)
        access_token, _ = _credential(request)
        principal = _principal(request, dependencies)
        if scope_type == "organization" and scope_id != principal.tenant_id:
            raise AuthorizationError("ACCESS_DENIED", 403)
        if scope_type == "organization":
            if principal.user_id in dependencies.settings.system_admin_user_ids:
                workspace_id = dependencies.authorization_repository.primary_workspace_id(
                    principal.tenant_id
                ) or f"{principal.tenant_id}-workspace"
            else:
                workspace_id = dependencies.authorization_service.organization_admin_workspace(
                    principal=principal, trace_id=request.state.trace_id,
                    policy_version=dependencies.settings.policy_version,
                )
        else:
            dependencies.authorization_service.authorize_action(
                principal=principal, workspace_id=workspace_id, action=Action.POLICY_MANAGE,
                requested_permissions=(Permission.EXTERNAL_LLM,),
                trace_id=request.state.trace_id,
                policy_version=dependencies.settings.policy_version,
            )
        dependencies.identity_service.consume_step_up(
            step_up_authorization=body.step_up_authorization_id,
            access_token=access_token,
            action_group="organization_security_or_connector_policy_change",
            target_id=scope_id,
            policy_version=dependencies.settings.policy_version,
            trace_id=request.state.trace_id,
            operation=f"egress_policy.{scope_type}.activate", idempotency_key=idempotency_key,
        )
        stored = egress_policy_service.create_and_activate(
            _egress_policy_context(principal, workspace_id, request, dependencies),
            scope_type=scope_type,
            payload=EgressPolicyPayload(
                mode=body.mode,
                allowed_provider_kinds=tuple(body.allowed_provider_kinds),
                allowed_destinations=tuple(body.allowed_destinations),
                classification=body.classification,
                max_bytes=body.max_bytes,
                masking_required=body.masking_required,
                redaction_required=body.redaction_required,
                required_approver=body.required_approver,
            ),
            expected_etag=if_match,
            idempotency_key=idempotency_key,
        )
        response = JSONResponse({
            "data": {
                "scope_type": stored.scope_type,
                "policy_version_id": stored.policy_version_id,
                "policy_version": stored.policy_version,
                "binding_id": stored.binding_id,
                "binding_version": stored.binding_version,
                **stored.payload.as_dict(),
            },
            "meta": {"trace_id": request.state.trace_id},
        }, status_code=201)
        response.headers["ETag"] = stored.etag
        return response

    @app.post("/api/v1/organizations/{id}/egress-policy-versions", status_code=201)
    async def create_organization_egress_policy_version(
        id: str, body: EgressPolicyVersionBody, request: Request,
        if_match: str = Header(alias="If-Match"),
        idempotency_key: str = Header(alias="Idempotency-Key"),
    ) -> JSONResponse:
        _require_query_keys(request, frozenset())
        return await create_egress_policy_version(
            scope_type="organization", scope_id=id, workspace_id="server-resolved",
            body=body, request=request, if_match=if_match, idempotency_key=idempotency_key,
        )

    @app.post("/api/v1/workspaces/{id}/egress-policy-versions", status_code=201)
    async def create_workspace_egress_policy_version(
        id: str, body: EgressPolicyVersionBody, request: Request,
        if_match: str = Header(alias="If-Match"),
        idempotency_key: str = Header(alias="Idempotency-Key"),
    ) -> JSONResponse:
        _require_query_keys(request, frozenset())
        return await create_egress_policy_version(
            scope_type="workspace", scope_id=id, workspace_id=id,
            body=body, request=request, if_match=if_match, idempotency_key=idempotency_key,
        )

    @app.post("/api/v1/model-profiles", status_code=201)
    async def save_model_profile(
        body: ProviderProfileBody, request: Request,
        idempotency_key: str = Header(alias="Idempotency-Key"),
    ) -> JSONResponse:
        _require_query_keys(request, frozenset())
        _idempotency_key(idempotency_key)
        principal = _principal(request, dependencies)
        dependencies.authorization_service.authorize_action(
            principal=principal, workspace_id=body.workspace_id, action=Action.POLICY_MANAGE,
            trace_id=request.state.trace_id,
            policy_version=dependencies.settings.policy_version,
        )
        profile = provider_settings_service.save_profile(
            _provider_settings_context(principal, body.workspace_id, request, dependencies),
            provider_code=body.provider_code, base_url=body.base_url, active=body.active,
            expected_version=body.expected_version,
        )
        response = JSONResponse(
            {"data": {**_dataclass_json(profile), "etag": profile.etag},
             "meta": {"trace_id": request.state.trace_id}},
            status_code=201,
        )
        response.headers["ETag"] = profile.etag
        return response

    @app.get("/api/v1/model-profiles/{provider_code}/connection-check")
    async def check_model_provider_connection(
        provider_code: str, request: Request, workspace_id: str = Query(),
    ) -> JSONResponse:
        _require_query_keys(request, frozenset({"workspace_id"}))
        principal = _principal(request, dependencies)
        dependencies.authorization_service.authorize_action(
            principal=principal, workspace_id=workspace_id, action=Action.POLICY_MANAGE,
            trace_id=request.state.trace_id,
            policy_version=dependencies.settings.policy_version,
        )
        status = provider_settings_service.check_connection(
            _provider_settings_context(principal, workspace_id, request, dependencies),
            provider_code,
        )
        return _json_with_etag({
            "data": _dataclass_json(status),
            "meta": {"trace_id": request.state.trace_id, "workspace_id": workspace_id},
        }, f"{status.provider_code}|{status.status}|{status.checked_at}")

    @app.get("/api/v1/model-deployments")
    async def list_model_deployments(
        request: Request, workspace_id: str = Query(),
    ) -> JSONResponse:
        _require_query_keys(request, frozenset({"workspace_id", "cursor", "limit", "filter", "search"}))
        principal = _principal(request, dependencies)
        dependencies.authorization_service.authorize_action(
            principal=principal, workspace_id=workspace_id, action=Action.VIEW,
            trace_id=request.state.trace_id,
            policy_version=dependencies.settings.policy_version,
        )
        snapshot = provider_settings_service.snapshot(
            _provider_settings_context(principal, workspace_id, request, dependencies)
        )
        return _json_with_etag(
            {
                "data": [
                    {**_dataclass_json(item), "etag": item.etag}
                    for item in snapshot.deployments
                ],
                "meta": {"trace_id": request.state.trace_id, "workspace_id": workspace_id},
            },
            "|".join(item.etag for item in snapshot.deployments),
        )

    @app.post("/api/v1/model-deployments", status_code=201)
    async def save_model_deployment(
        body: ModelDeploymentBody, request: Request,
        idempotency_key: str = Header(alias="Idempotency-Key"),
    ) -> JSONResponse:
        _require_query_keys(request, frozenset())
        _idempotency_key(idempotency_key)
        principal = _principal(request, dependencies)
        dependencies.authorization_service.authorize_action(
            principal=principal, workspace_id=body.workspace_id, action=Action.POLICY_MANAGE,
            trace_id=request.state.trace_id,
            policy_version=dependencies.settings.policy_version,
        )
        deployment = provider_settings_service.save_deployment(
            _provider_settings_context(principal, body.workspace_id, request, dependencies),
            deployment_id=body.deployment_id, provider_code=body.provider_code,
            model_id=body.model_id, roles=tuple(body.roles), active=body.active,
            selected=body.selected, expected_version=body.expected_version,
        )
        response = JSONResponse(
            {"data": {**_dataclass_json(deployment), "etag": deployment.etag},
             "meta": {"trace_id": request.state.trace_id}},
            status_code=201,
        )
        response.headers["ETag"] = deployment.etag
        return response

    @app.get("/api/v1/workspaces/{id}/model-policy")
    async def get_model_policy(id: str, request: Request) -> JSONResponse:
        _require_query_keys(request, frozenset())
        principal = _principal(request, dependencies)
        dependencies.authorization_service.authorize_action(
            principal=principal, workspace_id=id, action=Action.VIEW,
            trace_id=request.state.trace_id,
            policy_version=dependencies.settings.policy_version,
        )
        snapshot = provider_settings_service.snapshot(
            _provider_settings_context(principal, id, request, dependencies)
        )
        etag = f'"model-policy:{id}:{snapshot.binding_version}"'
        response = JSONResponse({
            "data": {"workspace_id": id, "bindings": snapshot.role_bindings,
                     "version": snapshot.binding_version},
            "meta": {"trace_id": request.state.trace_id},
        })
        response.headers["ETag"] = etag
        return response

    @app.patch("/api/v1/workspaces/{id}/model-policy")
    async def update_model_policy(
        id: str, body: ModelPolicyBody, request: Request,
        if_match: str = Header(alias="If-Match"),
        idempotency_key: str = Header(alias="Idempotency-Key"),
    ) -> JSONResponse:
        _require_query_keys(request, frozenset())
        _idempotency_key(idempotency_key)
        if if_match != f'"model-policy:{id}:{body.expected_version}"':
            raise ProviderSettingsError("VERSION_CONFLICT", 409)
        principal = _principal(request, dependencies)
        dependencies.authorization_service.authorize_action(
            principal=principal, workspace_id=id, action=Action.POLICY_MANAGE,
            trace_id=request.state.trace_id,
            policy_version=dependencies.settings.policy_version,
        )
        bindings, version = provider_settings_service.save_role_bindings(
            _provider_settings_context(principal, id, request, dependencies),
            bindings=body.bindings, expected_version=body.expected_version,
        )
        etag = f'"model-policy:{id}:{version}"'
        response = JSONResponse({
            "data": {"workspace_id": id, "bindings": bindings, "version": version},
            "meta": {"trace_id": request.state.trace_id},
        })
        response.headers["ETag"] = etag
        return response

    @app.post("/api/v1/backups/selection-previews")
    async def preview_lineage_selection(body: LineageSelectionPreviewBody, request: Request) -> JSONResponse:
        _require_query_keys(request, frozenset())
        principal = _principal(request, dependencies)
        dependencies.authorization_service.authorize_action(principal=principal, workspace_id=body.workspace_id,
            action=Action.POLICY_MANAGE, trace_id=request.state.trace_id, policy_version=dependencies.settings.policy_version)
        if dependencies.cloud_store is None: raise RecoveryError("RESOURCE_UNAVAILABLE", 503)
        context = _recovery_context(principal, body.workspace_id, request, dependencies)
        selection = await _recovery_heavy(PostgresBackupSelectionResolver(dependencies.cloud_store).resolve_lineage,
            context, BackupSelectionInput(body.notebook_id, tuple(body.source_version_ids)))
        current = _principal(request, dependencies)
        if (current.tenant_id, current.user_id, current.session_id, current.device_id) != (
                principal.tenant_id, principal.user_id, principal.session_id, principal.device_id):
            raise RecoveryError("CURRENT_ACCESS_DENIED", 403)
        dependencies.authorization_service.authorize_action(principal=current, workspace_id=body.workspace_id,
            action=Action.POLICY_MANAGE, trace_id=request.state.trace_id, policy_version=dependencies.settings.policy_version)
        return _json_with_etag({"data": {
            "root_source_version_ids": list(selection.root_source_version_ids),
            "dependency_source_version_ids": list(selection.dependency_source_version_ids),
            "dependency_source_ids": list(selection.dependency_source_ids),
            "source_version_count": selection.source_version_count, "source_count": selection.source_count,
            "unique_object_count": selection.unique_object_count, "total_bytes": selection.total_bytes,
            "metadata_bytes": selection.metadata_bytes, "closure_fingerprint": selection.fingerprint,
        }, "meta": {"trace_id": request.state.trace_id}}, selection.fingerprint)

    @app.post("/api/v1/backups", status_code=201)
    async def create_backup(
        body: BackupCreateBody, request: Request,
        idempotency_key: str = Header(alias="Idempotency-Key"),
    ) -> JSONResponse:
        _require_query_keys(request, frozenset())
        _idempotency_key(idempotency_key)
        body = body.root
        principal = _principal(request, dependencies)
        dependencies.authorization_service.authorize_action(
            principal=principal, workspace_id=body.workspace_id,
            action=Action.POLICY_MANAGE, trace_id=request.state.trace_id,
            policy_version=dependencies.settings.policy_version,
        )
        context = _recovery_context(principal, body.workspace_id, request, dependencies)
        def check_principal():
            current = _principal(request, dependencies)
            if (current.tenant_id, current.user_id, current.session_id, current.device_id) != (
                    principal.tenant_id, principal.user_id, principal.session_id, principal.device_id):
                raise RecoveryError("CURRENT_ACCESS_DENIED", 403)
            dependencies.authorization_service.authorize_action(principal=current, workspace_id=body.workspace_id,
                action=Action.POLICY_MANAGE, trace_id=request.state.trace_id, policy_version=dependencies.settings.policy_version)
        # Reference service는 명시 주입된 역사적 domain/HTTP 대역에서만 소비한다.
        # 실제 runtime의 신규/legacy는 metadata 확인 이후에도 capture port가 없으면 생성하지 않는다.
        if isinstance(body, LineageBackupCreateBody):
            if dependencies.cloud_store is None: raise RecoveryError("RESOURCE_UNAVAILABLE", 503)
            resolver = PostgresBackupSelectionResolver(dependencies.cloud_store)
            def recheck_lineage():
                try:
                    check_principal()
                    selection = resolver.resolve_lineage(context, BackupSelectionInput(body.notebook_id, tuple(body.source_version_ids)))
                    selection.require_consent(body.closure_consent.closure_fingerprint, body.closure_consent.include_prior_versions)
                    check_principal()
                    return selection
                except (IdentityError, AuthorizationError) as error:
                    raise _LineageRecheckAuthError(error) from None
            try:
                selection = await _recovery_heavy(recheck_lineage)
                if not isinstance(recovery_service, PostgresRecoveryService): raise RecoveryError("RESOURCE_UNAVAILABLE", 503)
                view = await _recovery_heavy(recovery_service.create_lineage_backup,context, selection, idempotency_key=idempotency_key,
                    recheck=recheck_lineage, trigger=body.trigger)
            except _LineageRecheckAuthError as error:
                # cleanup 불확정은 별도 503이므로 이 분기에 들어오지 않는다.
                raise error.original from None
        elif isinstance(body, SelectionBackupCreateBody) or not isinstance(recovery_service, RecoveryService):
            if dependencies.cloud_store is None:
                raise RecoveryError("RESOURCE_UNAVAILABLE", 503)
            resolver = PostgresBackupSelectionResolver(dependencies.cloud_store)
            def resolve_selection():
                if isinstance(body, SelectionBackupCreateBody):
                    return resolver.resolve(context, BackupSelectionInput(body.notebook_id, tuple(body.source_version_ids)),
                        idempotency_key=idempotency_key)
                return resolver.resolve_legacy(context, schema_revision=body.schema_revision,
                    retention_watermark=body.retention_watermark,
                    objects=tuple(BackupObjectInput(**item.model_dump()) for item in body.objects),
                    idempotency_key=idempotency_key, trigger=body.trigger)
            def recheck():
                check_principal()
                current = resolve_selection()
                current_metadata = resolver.capture_metadata(context, current, trigger=body.trigger)
                check_principal()
                return current, current_metadata
            resolved = await _recovery_heavy(resolve_selection)
            if not isinstance(recovery_service, PostgresRecoveryService):
                raise RecoveryError("RESOURCE_UNAVAILABLE", 503, retryable=False)
            metadata = await _recovery_heavy(resolver.capture_metadata,context, resolved, trigger=body.trigger)
            view = await _recovery_heavy(recovery_service.create_selected_backup,context, resolved, metadata,
                idempotency_key=idempotency_key, recheck=recheck, trigger=body.trigger)
        else:
            view = recovery_service.create_backup(
                context,
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

    @app.get("/api/v1/backups/{id}/restore-destinations")
    async def get_restore_destinations(id: str,request: Request) -> JSONResponse:
        _require_query_keys(request,frozenset())
        principal=_principal(request,dependencies)
        workspace_id=recovery_service.locate_backup_workspace(principal.tenant_id,id)
        dependencies.authorization_service.authorize_action(principal=principal,workspace_id=workspace_id,
            action=Action.POLICY_MANAGE,trace_id=request.state.trace_id,policy_version=dependencies.settings.policy_version)
        context=_recovery_context(principal,workspace_id,request,dependencies)
        def current():
            value=_principal(request,dependencies)
            if (value.tenant_id,value.user_id,value.session_id,value.device_id)!=(principal.tenant_id,principal.user_id,principal.session_id,principal.device_id):
                raise RecoveryError("CURRENT_ACCESS_DENIED",403)
            dependencies.authorization_service.authorize_action(principal=value,workspace_id=workspace_id,
                action=Action.POLICY_MANAGE,trace_id=request.state.trace_id,policy_version=dependencies.settings.policy_version)
        if not isinstance(recovery_service,PostgresRecoveryService): raise RecoveryError("RESOURCE_UNAVAILABLE",503)
        values=await _recovery_heavy(recovery_service.list_restore_destinations,context,id,recheck=current)
        current()
        # 보안용 서버 refs/물리 영수증/원문은 등록 요약 DTO에도 반사하지 않는다.
        fields=("destination_id","version","display_name","expires_at")
        result=[{key:value[key] for key in fields} for value in values]
        return _json_with_etag({"data":result,"meta":{"trace_id":request.state.trace_id}},
            hashlib.sha256(json.dumps(result,sort_keys=True,separators=(",",":"),ensure_ascii=False).encode()).hexdigest())

    @app.post("/api/v1/backups/{id}/restore-previews", status_code=201)
    async def create_restore_preview(
        id: str, body: RestorePreviewBody, request: Request,
        idempotency_key: str = Header(alias="Idempotency-Key"),
    ) -> JSONResponse:
        body=body.root
        _require_query_keys(request, frozenset())
        _idempotency_key(idempotency_key)
        access_token, _ = _credential(request)
        principal = _principal(request, dependencies)
        workspace_id = recovery_service.locate_backup_workspace(principal.tenant_id, id)
        if isinstance(body,RegisteredRestorePreviewBody) and body.workspace_id!=workspace_id:
            raise RecoveryError("CURRENT_ACCESS_DENIED",403)
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
            operation="recovery.restore_preview", idempotency_key=idempotency_key,
            operations_scope=_operations_workspace_scope(principal,workspace_id,dependencies),
        )
        context=_recovery_context(principal,workspace_id,request,dependencies)
        if isinstance(body,RegisteredRestorePreviewBody):
            if not isinstance(recovery_service,PostgresRecoveryService): raise RecoveryError("RESOURCE_UNAVAILABLE",503)
            def current():
                value=_principal(request,dependencies)
                if (value.tenant_id,value.user_id,value.session_id,value.device_id)!=(principal.tenant_id,principal.user_id,principal.session_id,principal.device_id):
                    raise RecoveryError("CURRENT_ACCESS_DENIED",403)
                dependencies.authorization_service.authorize_action(principal=value,workspace_id=workspace_id,action=Action.POLICY_MANAGE,
                    trace_id=request.state.trace_id,policy_version=dependencies.settings.policy_version)
            view=await _recovery_heavy(recovery_service.preview_registered_restore,context,id,destination_id=body.destination_id,destination_version=body.destination_version,
                idempotency_key=idempotency_key,step_up_verified=True,recheck=current)
            current()
        else:
            view = recovery_service.create_restore_preview(context, id,
                destination=RestoreDestination(**body.destination.model_dump()),
                idempotency_key=idempotency_key, step_up_verified=True)
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
        context=_recovery_context(principal,workspace_id,request,dependencies)
        expected_version=_recovery_expected_version(if_match,id)
        from .recovery import RegisteredRestorePreviewView
        current_view=recovery_service.get_restore_request(context,id)
        if isinstance(current_view.preview,RegisteredRestorePreviewView) and body.preview_version!=current_view.preview.version:
            raise RecoveryError("RESTORE_VERSION_CONFLICT",409)
        target_id=_registered_restore_execute_step_up_target(current_view) if isinstance(current_view.preview,RegisteredRestorePreviewView) else id
        dependencies.identity_service.consume_step_up(
            step_up_authorization=body.step_up_authorization_id,
            access_token=access_token,
            action_group="permanent_delete_or_restore_rollback", target_id=target_id,
            policy_version=dependencies.settings.policy_version,
            trace_id=request.state.trace_id,
            operation="recovery.restore_execute", idempotency_key=idempotency_key,
            operations_scope=_operations_workspace_scope(principal,workspace_id,dependencies),
        )
        if isinstance(current_view.preview,RegisteredRestorePreviewView):
            if not isinstance(recovery_service,PostgresRecoveryService):raise RecoveryError("RESOURCE_UNAVAILABLE",503)
            def current():
                try:
                    value=_principal(request,dependencies)
                    if (value.tenant_id,value.user_id,value.session_id,value.device_id)!=(principal.tenant_id,principal.user_id,principal.session_id,principal.device_id):
                        raise RecoveryError("CURRENT_ACCESS_DENIED",403)
                    dependencies.authorization_service.authorize_action(principal=value,workspace_id=workspace_id,action=Action.POLICY_MANAGE,
                        trace_id=request.state.trace_id,policy_version=dependencies.settings.policy_version)
                except (IdentityError,AuthorizationError) as error:raise _LineageRecheckAuthError(error) from None
            try:
                view=await _recovery_heavy(recovery_service.execute_registered_restore,context,id,expected_version=expected_version,
                    preview_version=body.preview_version,idempotency_key=idempotency_key,step_up_verified=True,recheck=current)
                current()
            except _LineageRecheckAuthError as error:raise error.original from None
        else:
            view = recovery_service.execute_restore(context,id,expected_version=expected_version,
                preview_version=body.preview_version,idempotency_key=idempotency_key,step_up_verified=True)
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


def _registered_restore_execute_step_up_target(view):
    preview=view.preview
    raw=json.dumps(["registered-restore-execute-step-up-v1",view.request_id,preview.version,
        preview.source_manifest_digest,preview.closure_fingerprint,preview.created_at.isoformat()],
        ensure_ascii=False,separators=(",",":")).encode("utf-8")
    return "rrexec-v1:"+hashlib.sha256(raw).hexdigest()


async def _recovery_heavy(call,*args,**kwargs):
    """S44의 동기 I/O/암호화만 분리. 취소는 다음 기존 쓰기 전 재검사에서 안전 거부한다.

    thread를 강제 종료하거나 진행된 외부 쓰기를 rollback했다고 주장하지 않는다.
    기존 R3 phase/불확정 보존과 store 정리 경계가 결과를 관리한다.
    """
    cancelled=threading.Event()
    original=kwargs.get("recheck")
    if original is not None:
        def current():
            if cancelled.is_set():raise RecoveryError("RESOURCE_UNAVAILABLE",503,retryable=False)
            result=original()
            if cancelled.is_set():raise RecoveryError("RESOURCE_UNAVAILABLE",503,retryable=False)
            return result
        kwargs["recheck"]=current
    try:return await asyncio.to_thread(call,*args,**kwargs)
    except asyncio.CancelledError:
        cancelled.set()
        raise


def _build_recovery_service(settings: RuntimeSettings, object_storage):
    if settings.recovery_restore_config_file is not None and settings.recovery_capture_root is None:
        raise ValueError("REGISTERED_RESTORE_CONFIG_INVALID")
    if settings.cloud_database_dsn is None or object_storage is None or settings.recovery_manifest_key_file is None:
        if settings.recovery_capture_root is not None:
            raise ValueError("RECOVERY_CAPTURE_DEPENDENCY_REQUIRED")
        return UnavailableRecoveryService()
    try:
        if settings.recovery_restore_config_file is None:
            manifest_key = settings.recovery_manifest_key_file.read_bytes()
        else:
            from .recovery_postgres import _read_restore_protected_file
            manifest_key = _read_restore_protected_file(settings.recovery_manifest_key_file,4096)
    except OSError:
        raise ValueError("RECOVERY_MANIFEST_KEY_REFERENCE_UNAVAILABLE") from None
    capture_store = lineage_capture_store = None
    if settings.recovery_capture_root is not None:
        try:
            # 기존 root만 사용한다. mkdir/기본 경로 추정/새 key 파일/비밀 fallback은 없다.
            capture_store = FileCaptureStore(settings.recovery_capture_root, master_key=manifest_key,
                key_version=settings.recovery_capture_key_version, key_reference=settings.recovery_capture_key_reference)
            lineage_capture_store = FileLineageCaptureStore(settings.recovery_capture_root, master_key=manifest_key,
                key_version=settings.recovery_capture_key_version, key_reference=settings.recovery_capture_key_reference)
        except (RecoveryError, OSError):
            raise ValueError("RECOVERY_CAPTURE_STORE_UNAVAILABLE") from None
    ports={};owned=()
    if settings.recovery_restore_config_file is not None:
        from .recovery_postgres import _load_registered_restore_ports
        registry,probe,factory,owned=_load_registered_restore_ports(settings.recovery_restore_config_file,
            master_key=manifest_key,key_version=settings.recovery_capture_key_version)
        ports=dict(destination_registry=registry,destination_probe=probe,registered_target_factory=factory,
            owned_restore_resources=owned)
    try:
        return PostgresRecoveryService(settings.cloud_database_dsn, MinioRecoveryStorageAdapter(object_storage),
            manifest_key=manifest_key, clock=lambda: datetime.now(timezone.utc),
            notification_inbox_enabled=settings.notification_inbox_enabled, capture_store=capture_store,
            lineage_capture_store=lineage_capture_store,**ports)
    except BaseException:
        for resource in reversed(owned):
            try:resource.close()
            except Exception:pass
        raise


def build_dependencies(settings: RuntimeSettings) -> RuntimeDependencies:
    if settings.database_path is None:
        raise ValueError("DAON_API_DATABASE_PATH_REQUIRED")
    settings.database_path.parent.mkdir(parents=True, exist_ok=True)
    if settings.profile == "production":
        assert settings.cloud_database_dsn is not None and settings.step_up_token_key_file is not None
        try:
            step_up_token_key = settings.step_up_token_key_file.read_bytes()
        except OSError:
            raise ValueError("STEP_UP_TOKEN_KEY_REFERENCE_UNAVAILABLE") from None
        if len(step_up_token_key) < 32:
            raise ValueError("STEP_UP_TOKEN_KEY_REFERENCE_INVALID")
        from .audit import PostgresSecurityAuditStore
        audit_store = PostgresSecurityAuditStore(settings.cloud_database_dsn)
    else:
        step_up_token_key = None
        audit_store = AuditEventStore()
    if settings.profile == "production":
        assert settings.cloud_database_dsn is not None
        identity_repository = PostgresIdentityRepository(settings.cloud_database_dsn)
        authorization_repository = PostgresAuthorizationRepository(settings.cloud_database_dsn)
        organization_repository = PostgresOrganizationRepository(settings.cloud_database_dsn,
            audit_store=audit_store, notification_inbox_enabled=settings.notification_inbox_enabled)
    else:
        identity_repository = SqliteIdentityRepository(settings.database_path)
        authorization_repository = SqliteAuthorizationRepository(settings.database_path)
        organization_database_path = settings.organization_database_path or settings.database_path.with_name(
            settings.database_path.stem + "-organization.sqlite"
        )
        organization_database_path.parent.mkdir(parents=True, exist_ok=True)
        organization_repository = SqliteOrganizationRepository(organization_database_path, audit_store=audit_store)
    identity_service = IdentityService(
        repository=identity_repository,
        audit_store=audit_store,
        oidc_policies=(),
        clock=lambda: datetime.now(timezone.utc),
        step_up_token_key=step_up_token_key,
        notification_inbox_enabled=settings.notification_inbox_enabled,
    )
    identity_service.ensure_initial_admin()
    admin_user_service = AdminUserService(
        repository=identity_repository,
        audit_store=audit_store,
        system_admin_user_ids=settings.system_admin_user_ids,
        clock=lambda: datetime.now(timezone.utc),
        password_reset_requester=identity_service.request_password_reset,
    )
    admin_membership_role_service = AdminMembershipRoleService(
        repository=authorization_repository,
        system_admin_user_ids=settings.system_admin_user_ids,
        clock=lambda: datetime.now(timezone.utc),
        audit_store=audit_store,
        notification_inbox_enabled=settings.notification_inbox_enabled,
    )
    authorization_repository.bootstrap_workspace(
        tenant_id="admin",
        workspace_id=_personal_workspace_id("admin"),
        owner_user_id="admin",
        owner_role=Role.PERSONAL_OWNER,
        workspace_kind="personal",
        data_area="cloud_sync",
        cost_limit_cents=1000,
        now=datetime.now(timezone.utc),
    )
    authorization_service = AuthorizationService(
        repository=authorization_repository,
        audit_store=audit_store,
        clock=lambda: datetime.now(timezone.utc),
        identity_service=identity_service,
        notification_inbox_enabled=settings.notification_inbox_enabled,
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
        else PostgresCloudStore(settings.cloud_database_dsn, organization_admin_v2_enabled=settings.organization_admin_v2_enabled,
            notification_inbox_enabled=settings.notification_inbox_enabled)
    )
    document_readiness_repository = _build_document_readiness_runtime(cloud_store, settings)
    provider_connection_service = None
    provider_health_settings_service = None
    workspace_model_defaults_service = None
    if cloud_store is not None and settings.provider_credential_key_file is not None:
        try:
            provider_key = settings.provider_credential_key_file.read_bytes()
        except OSError:
            raise ValueError("PROVIDER_CREDENTIAL_KEY_REFERENCE_UNAVAILABLE") from None
        provider_cipher = ProviderCredentialCipher(provider_key, encryption_key_version=1)
        provider_connection_service = PostgresProviderConnectionService(cloud_store, provider_cipher,
            readiness_observer=_document_readiness_observer(document_readiness_repository))
        provider_health_settings_service = PostgresProviderHealthCheckSettingsService(
            PostgresProviderHealthSettingsRepository(cloud_store),
        )
        workspace_model_defaults_service = PostgresWorkspaceModelDefaultsService(cloud_store)
    object_storage: ObjectStoragePort | None = None
    website_storage: WebsiteProcessStorage | None = None
    if settings.object_storage_endpoint is not None:
        assert settings.object_storage_bucket is not None
        assert settings.object_access_key_file is not None
        assert settings.object_secret_key_file is not None
        try:
            access_key = settings.object_access_key_file.read_text(encoding="utf-8").strip()
            secret_key = settings.object_secret_key_file.read_text(encoding="utf-8").strip()
        except OSError:
            raise ValueError("OBJECT_SECRET_REFERENCE_UNAVAILABLE") from None
        adapter = MinioObjectStorageAdapter(
            endpoint=settings.object_storage_endpoint,
            bucket=settings.object_storage_bucket,
            access_key=access_key,
            secret_key=secret_key,
            secure=settings.object_storage_secure,
        )
        if settings.object_storage_provision_bucket:
            adapter.ensure_bucket()
        object_storage = adapter
        website_storage = WebsiteProcessStorage(endpoint=settings.object_storage_endpoint,
            bucket=settings.object_storage_bucket, access_key=access_key, secret_key=secret_key,
            secure=settings.object_storage_secure)
    sync_service: SyncService | PostgresSyncService
    recovery_service: PostgresRecoveryService | UnavailableRecoveryService
    object_queue_store: PostgresObjectQueueStore | None = None
    source_upload_service: PostgresSourceUploadService | None = None
    document_processing_service: DocumentProcessingSubmissionService | None = None
    license_creation_enforcer = (
        enforce_license_creation
        if _license_enforcement_enabled(settings, configured_service=False)
        else None
    )
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
            object_queue_store = PostgresObjectQueueStore(
                settings.cloud_database_dsn, creation_enforcer=license_creation_enforcer,
            )
            transfer_port = ObjectQueueSyncTransferPort(ObjectQueueCoordinator(
                object_queue_store, object_storage, id_factory=lambda: secrets.token_hex(16)
            ))
        sync_service = PostgresSyncService(
            cloud_store, transfer_port, clock=lambda: datetime.now(timezone.utc)
        )
    recovery_service = _build_recovery_service(settings, object_storage)
    if (
        settings.cloud_database_dsn is not None
        and object_storage is not None
        and object_queue_store is not None
    ):
        source_upload_service = PostgresSourceUploadService(
            queue_store=object_queue_store,
            object_storage=object_storage,
            canon_store=PostgresDataCanonStore(
                settings.cloud_database_dsn, creation_enforcer=license_creation_enforcer,
            ),
        )
        document_processing_service = DocumentProcessingSubmissionService(
            PostgresDocumentProcessingRepository(cloud_store, object_storage,
                readiness_port=document_readiness_repository, readiness_settings=settings.document_readiness_settings)
        )
    return RuntimeDependencies(
        settings=settings,
        identity_service=identity_service,
        authorization_service=authorization_service,
        audit_store=audit_store,
        identity_repository=identity_repository,
        authorization_repository=authorization_repository,
        admin_user_service=admin_user_service,
        admin_membership_role_service=admin_membership_role_service,
        notification_service=notification_service,
        cloud_store=cloud_store,
        object_storage=object_storage,
        sync_service=sync_service,
        retention_service=RetentionService(
            ReferenceRetentionRepository(), ReferenceCleanupPort(),
            clock=lambda: datetime.now(timezone.utc),
            inventory_provider=None if cloud_store is None else PostgresRetentionInventoryProvider(cloud_store),
        ),
        recovery_service=recovery_service,
        object_queue_store=object_queue_store,
        provider_connection_service=provider_connection_service,
        provider_health_settings_service=provider_health_settings_service,
        workspace_model_defaults_service=workspace_model_defaults_service,
        source_upload_service=source_upload_service,
        website_storage=website_storage,
        document_processing_service=document_processing_service,
        document_readiness_repository=document_readiness_repository,
        organization_repository=organization_repository,
        notebook_deletion_store=(
            None if cloud_store is None else PostgresNotebookDeletionStore(cloud_store, object_storage)
        ),
        notebook_deletion_worker=(
            None if cloud_store is None else NotebookDeletionWorker(
                PostgresNotebookDeletionStore(cloud_store, object_storage)
            )
        ),
    )
