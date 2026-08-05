"""Tenant and workspace authorization core for Release 1 M4-04.

The module is deliberately independent of HTTP and PostgreSQL.  It evaluates trusted
``IdentityPrincipal`` values, persists only authorization metadata in an injected
SQLite path, and records every security decision through the M4-02 Audit contract.
"""

from __future__ import annotations

import json
import re
import secrets
import sqlite3
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from enum import Enum
from pathlib import Path
from threading import RLock
from typing import Callable, Iterator

from .audit import ActorType, AuditEventDraft, AuditOutcome
from .identity import IdentityPrincipal


AUTHORIZATION_SCHEMA_VERSION = 2
_SAFE_ID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:-]{0,127}$")


class Role(str, Enum):
    PERSONAL_OWNER = "personal_owner"
    ORGANIZATION_ADMIN = "organization_admin"
    WORKSPACE_ADMIN = "workspace_admin"
    EDITOR = "editor"
    REVIEWER = "reviewer"
    APPROVER = "approver"
    VIEWER = "viewer"


class RoleScope(str, Enum):
    TENANT = "tenant"
    WORKSPACE = "workspace"


TENANT_ROLES = frozenset({Role.PERSONAL_OWNER, Role.ORGANIZATION_ADMIN})
WORKSPACE_ROLES = frozenset(
    {Role.WORKSPACE_ADMIN, Role.EDITOR, Role.REVIEWER, Role.APPROVER, Role.VIEWER}
)


class Action(str, Enum):
    VIEW = "view"
    QUERY = "query"
    ANALYZE = "analyze"
    GENERATE = "generate"
    EDIT = "edit"
    REVIEW = "review"
    REVISION_REQUEST = "revision_request"
    APPROVE = "approve"
    DELIVER = "deliver"
    KNOWLEDGE_REGISTER = "knowledge_register"
    POLICY_MANAGE = "policy_manage"
    MEMBER_MANAGE = "member_manage"


class Permission(str, Enum):
    EXTERNAL_LLM = "external_llm"
    INTERNET_SEARCH = "internet_search"
    LOCAL_INTERNAL_LLM = "local_internal_llm"
    DAON_KNOWLEDGE = "daon_knowledge"
    FILE_DOWNLOAD_SHARE = "file_download_share"
    PRODUCTION_KNOWLEDGE_REGISTRATION = "production_knowledge_registration"
    DATA_AREA_MOVE = "data_area_move"
    FINAL_APPROVAL_EXTERNAL_DELIVERY = "final_approval_external_delivery"


class PolicyEffect(str, Enum):
    GRANT = "grant"
    DENY = "deny"


class AccessAction(str, Enum):
    READ = "read"
    CITATION = "citation"
    OPEN_SOURCE = "open_source"
    EXPORT = "export"
    DELIVERY = "delivery"
    KNOWLEDGE_REGISTRATION = "knowledge_registration"
    RERUN = "rerun"


class AccessState(str, Enum):
    AVAILABLE = "available"
    PARTIALLY_REDACTED = "partially_redacted"
    ACCESS_BLOCKED = "access_blocked"


ROLE_ACTION_MATRIX: dict[Role, frozenset[Action]] = {
    Role.PERSONAL_OWNER: frozenset(Action),
    Role.ORGANIZATION_ADMIN: frozenset(Action),
    Role.WORKSPACE_ADMIN: frozenset(
        {
            Action.VIEW,
            Action.QUERY,
            Action.ANALYZE,
            Action.GENERATE,
            Action.EDIT,
            Action.REVIEW,
            Action.REVISION_REQUEST,
            Action.POLICY_MANAGE,
            Action.MEMBER_MANAGE,
        }
    ),
    Role.EDITOR: frozenset(
        {Action.VIEW, Action.QUERY, Action.ANALYZE, Action.GENERATE, Action.EDIT}
    ),
    Role.REVIEWER: frozenset(
        {Action.VIEW, Action.QUERY, Action.ANALYZE, Action.REVIEW, Action.REVISION_REQUEST}
    ),
    Role.APPROVER: frozenset(
        {
            Action.VIEW,
            Action.QUERY,
            Action.ANALYZE,
            Action.REVIEW,
            Action.APPROVE,
            Action.DELIVER,
            Action.KNOWLEDGE_REGISTER,
        }
    ),
    Role.VIEWER: frozenset({Action.VIEW}),
}


ROLE_PERMISSION_DEFAULTS: dict[Role, frozenset[Permission]] = {
    Role.PERSONAL_OWNER: frozenset(Permission),
    Role.ORGANIZATION_ADMIN: frozenset(Permission),
    Role.WORKSPACE_ADMIN: frozenset(
        {
            Permission.INTERNET_SEARCH,
            Permission.LOCAL_INTERNAL_LLM,
            Permission.DAON_KNOWLEDGE,
            Permission.FILE_DOWNLOAD_SHARE,
        }
    ),
    Role.EDITOR: frozenset(
        {
            Permission.INTERNET_SEARCH,
            Permission.LOCAL_INTERNAL_LLM,
            Permission.DAON_KNOWLEDGE,
            Permission.FILE_DOWNLOAD_SHARE,
        }
    ),
    Role.REVIEWER: frozenset(
        {Permission.LOCAL_INTERNAL_LLM, Permission.DAON_KNOWLEDGE, Permission.FILE_DOWNLOAD_SHARE}
    ),
    Role.APPROVER: frozenset(
        {
            Permission.LOCAL_INTERNAL_LLM,
            Permission.DAON_KNOWLEDGE,
            Permission.FILE_DOWNLOAD_SHARE,
            Permission.PRODUCTION_KNOWLEDGE_REGISTRATION,
            Permission.FINAL_APPROVAL_EXTERNAL_DELIVERY,
        }
    ),
    Role.VIEWER: frozenset({Permission.LOCAL_INTERNAL_LLM, Permission.DAON_KNOWLEDGE}),
}


_ACTION_PERMISSIONS: dict[Action, frozenset[Permission]] = {
    Action.APPROVE: frozenset({Permission.FINAL_APPROVAL_EXTERNAL_DELIVERY}),
    Action.DELIVER: frozenset(
        {Permission.FILE_DOWNLOAD_SHARE, Permission.FINAL_APPROVAL_EXTERNAL_DELIVERY}
    ),
    Action.KNOWLEDGE_REGISTER: frozenset({Permission.PRODUCTION_KNOWLEDGE_REGISTRATION}),
}

_ACCESS_ACTIONS: dict[AccessAction, Action] = {
    AccessAction.READ: Action.VIEW,
    AccessAction.CITATION: Action.VIEW,
    AccessAction.OPEN_SOURCE: Action.VIEW,
    AccessAction.EXPORT: Action.DELIVER,
    AccessAction.DELIVERY: Action.DELIVER,
    AccessAction.KNOWLEDGE_REGISTRATION: Action.KNOWLEDGE_REGISTER,
    AccessAction.RERUN: Action.GENERATE,
}


class AuthorizationError(RuntimeError):
    """Stable value-free error suitable for the M4-01 safe envelope."""

    def __init__(self, code: str, http_status: int = 400, *, decision: AccessDecision | None = None) -> None:
        self.code = code
        self.http_status = http_status
        self.decision = decision
        super().__init__(code)


@dataclass(frozen=True, slots=True)
class EffectivePermission:
    permission: Permission
    requested: bool
    effective: bool
    locked_by: str | None
    reason: str
    policy_version: str


@dataclass(frozen=True, slots=True)
class AuthorizationGrant:
    allowed: bool
    actor_id: str
    tenant_id: str
    workspace_id: str
    role: Role
    role_scope: RoleScope
    action: Action
    effective_permissions: tuple[EffectivePermission, ...]
    required_step_up_action: str | None
    membership_version: int
    acl_version: int
    policy_version: str


@dataclass(frozen=True, slots=True)
class EvidenceDependency:
    reference_id: str
    source_version_id: str
    segment_ids: tuple[str, ...]
    decisive: bool
    safe_separation: bool


@dataclass(frozen=True, slots=True)
class HistoricalResultDescriptor:
    result_id: str
    result_kind: str
    tenant_id: str
    workspace_id: str
    source_version_ids: tuple[str, ...]
    evidence_reference_ids: tuple[str, ...]
    dependencies: tuple[EvidenceDependency, ...]
    original_policy_version: str
    original_membership_version: int


@dataclass(frozen=True, slots=True)
class AccessDecision:
    decision_id: str
    actor_id: str
    action: AccessAction
    resource_id: str
    tenant_id: str
    workspace_id: str
    role_scope: RoleScope | None
    membership_version: int
    acl_version: int
    policy_version: str
    evaluated_at: datetime
    state: AccessState
    reason_codes: tuple[str, ...]
    allowed_reference_ids: tuple[str, ...]
    masked_reference_ids: tuple[str, ...]
    allowed_segment_ids: tuple[str, ...]
    masked_segment_ids: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class RerunSnapshot:
    tenant_id: str
    workspace_id: str
    actor_id: str
    role_scope: RoleScope
    membership_version: int
    acl_version: int
    policy_version: str
    data_area: str
    cost_limit_cents: int
    source_version_ids: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class RerunAuthorization:
    run_request_id: str
    access_decision_id: str
    snapshot: RerunSnapshot


@dataclass(frozen=True, slots=True)
class RoleBinding:
    role: Role
    scope: RoleScope
    version: int


def _checked_id(value: object) -> str:
    if not isinstance(value, str) or not _SAFE_ID.fullmatch(value):
        raise AuthorizationError("INVALID_INPUT")
    return value


def _checked_utc(value: datetime) -> datetime:
    if not isinstance(value, datetime) or value.tzinfo is None or value.utcoffset() != timedelta(0):
        raise AuthorizationError("INVALID_TIME")
    return value.astimezone(timezone.utc)


def _iso(value: datetime) -> str:
    return _checked_utc(value).isoformat(timespec="microseconds")


def _dt(value: str) -> datetime:
    return datetime.fromisoformat(value).astimezone(timezone.utc)


def _id(prefix: str) -> str:
    return f"{prefix}-{secrets.token_urlsafe(18)}"


def _json_tuple(values: tuple[str, ...]) -> str:
    return json.dumps(values, ensure_ascii=True, separators=(",", ":"))


def _decode_tuple(value: str) -> tuple[str, ...]:
    decoded = json.loads(value)
    if not isinstance(decoded, list) or not all(isinstance(item, str) for item in decoded):
        raise AuthorizationError("PERSISTENCE_CORRUPT", 503)
    return tuple(decoded)


def _validate_descriptor(descriptor: HistoricalResultDescriptor) -> None:
    if not isinstance(descriptor, HistoricalResultDescriptor):
        raise AuthorizationError("INVALID_INPUT")
    for value in (
        descriptor.result_id,
        descriptor.result_kind,
        descriptor.tenant_id,
        descriptor.workspace_id,
        descriptor.original_policy_version,
        *descriptor.source_version_ids,
        *descriptor.evidence_reference_ids,
    ):
        _checked_id(value)
    if not isinstance(descriptor.original_membership_version, int) or descriptor.original_membership_version < 1:
        raise AuthorizationError("INVALID_INPUT")
    source_ids = set(descriptor.source_version_ids)
    reference_ids = set(descriptor.evidence_reference_ids)
    if len(source_ids) != len(descriptor.source_version_ids) or len(reference_ids) != len(descriptor.evidence_reference_ids):
        raise AuthorizationError("INVALID_INPUT")
    seen_references: set[str] = set()
    for dependency in descriptor.dependencies:
        if not isinstance(dependency, EvidenceDependency):
            raise AuthorizationError("INVALID_INPUT")
        _checked_id(dependency.reference_id)
        _checked_id(dependency.source_version_id)
        if dependency.reference_id not in reference_ids or dependency.source_version_id not in source_ids:
            raise AuthorizationError("INVALID_INPUT")
        if dependency.reference_id in seen_references:
            raise AuthorizationError("INVALID_INPUT")
        seen_references.add(dependency.reference_id)
        if not dependency.segment_ids:
            raise AuthorizationError("INVALID_INPUT")
        for segment_id in dependency.segment_ids:
            _checked_id(segment_id)


class SqliteAuthorizationRepository:
    """Restart-safe authorization metadata adapter using an injected SQLite path."""

    def __init__(self, path: str | Path) -> None:
        self._path = Path(path)
        self._lock = RLock()
        self._closed = False
        try:
            connection = self._connect()
            self._create_schema(connection)
            connection.close()
        except sqlite3.Error as error:
            raise AuthorizationError("PERSISTENCE_UNAVAILABLE", 503) from error

    def _connect(self) -> sqlite3.Connection:
        connection = sqlite3.connect(str(self._path), isolation_level=None, check_same_thread=False)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA foreign_keys = ON")
        connection.execute("PRAGMA journal_mode = WAL")
        return connection

    def _create_schema(self, connection: sqlite3.Connection) -> None:
        connection.executescript(
            """
            CREATE TABLE IF NOT EXISTS auth_schema_metadata (
              singleton INTEGER PRIMARY KEY CHECK(singleton=1), schema_version INTEGER NOT NULL
            );
            INSERT OR IGNORE INTO auth_schema_metadata(singleton,schema_version) VALUES (1,1);
            CREATE TABLE IF NOT EXISTS auth_workspaces (
              workspace_id TEXT PRIMARY KEY, tenant_id TEXT NOT NULL, workspace_kind TEXT NOT NULL,
              data_area TEXT NOT NULL, cost_limit_cents INTEGER NOT NULL,
              acl_version INTEGER NOT NULL, version INTEGER NOT NULL, updated_at TEXT NOT NULL,
              UNIQUE(tenant_id,workspace_id)
            );
            CREATE TABLE IF NOT EXISTS auth_tenant_roles (
              tenant_id TEXT NOT NULL, user_id TEXT NOT NULL, role TEXT NOT NULL,
              state TEXT NOT NULL, version INTEGER NOT NULL, updated_at TEXT NOT NULL,
              PRIMARY KEY(tenant_id,user_id)
            );
            CREATE TABLE IF NOT EXISTS auth_memberships (
              tenant_id TEXT NOT NULL, workspace_id TEXT NOT NULL, user_id TEXT NOT NULL,
              role TEXT NOT NULL, state TEXT NOT NULL, version INTEGER NOT NULL, updated_at TEXT NOT NULL,
              PRIMARY KEY(tenant_id,workspace_id,user_id),
              FOREIGN KEY(tenant_id,workspace_id) REFERENCES auth_workspaces(tenant_id,workspace_id)
            );
            CREATE TABLE IF NOT EXISTS auth_tenant_policies (
              tenant_id TEXT PRIMARY KEY, version INTEGER NOT NULL, updated_at TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS auth_tenant_permission_rules (
              tenant_id TEXT NOT NULL REFERENCES auth_tenant_policies(tenant_id),
              permission TEXT NOT NULL, effect TEXT NOT NULL, locked INTEGER NOT NULL,
              PRIMARY KEY(tenant_id,permission)
            );
            CREATE TABLE IF NOT EXISTS auth_workspace_policies (
              tenant_id TEXT NOT NULL, workspace_id TEXT NOT NULL, version INTEGER NOT NULL, updated_at TEXT NOT NULL,
              PRIMARY KEY(tenant_id,workspace_id),
              FOREIGN KEY(tenant_id,workspace_id) REFERENCES auth_workspaces(tenant_id,workspace_id)
            );
            CREATE TABLE IF NOT EXISTS auth_workspace_permission_rules (
              tenant_id TEXT NOT NULL, workspace_id TEXT NOT NULL, permission TEXT NOT NULL,
              effect TEXT NOT NULL, locked INTEGER NOT NULL,
              PRIMARY KEY(tenant_id,workspace_id,permission),
              FOREIGN KEY(tenant_id,workspace_id) REFERENCES auth_workspace_policies(tenant_id,workspace_id)
            );
            CREATE TABLE IF NOT EXISTS auth_source_access (
              tenant_id TEXT NOT NULL, workspace_id TEXT NOT NULL, user_id TEXT NOT NULL,
              source_version_id TEXT NOT NULL, allowed INTEGER NOT NULL, version INTEGER NOT NULL, updated_at TEXT NOT NULL,
              PRIMARY KEY(tenant_id,workspace_id,user_id,source_version_id),
              FOREIGN KEY(tenant_id,workspace_id) REFERENCES auth_workspaces(tenant_id,workspace_id)
            );
            CREATE TABLE IF NOT EXISTS auth_historical_results (
              result_id TEXT PRIMARY KEY, result_kind TEXT NOT NULL, tenant_id TEXT NOT NULL,
              workspace_id TEXT NOT NULL, source_version_ids TEXT NOT NULL,
              evidence_reference_ids TEXT NOT NULL, original_policy_version TEXT NOT NULL,
              original_membership_version INTEGER NOT NULL, created_at TEXT NOT NULL,
              FOREIGN KEY(tenant_id,workspace_id) REFERENCES auth_workspaces(tenant_id,workspace_id)
            );
            CREATE TABLE IF NOT EXISTS auth_result_dependencies (
              result_id TEXT NOT NULL REFERENCES auth_historical_results(result_id),
              reference_id TEXT NOT NULL, source_version_id TEXT NOT NULL, segment_ids TEXT NOT NULL,
              decisive INTEGER NOT NULL, safe_separation INTEGER NOT NULL,
              PRIMARY KEY(result_id,reference_id)
            );
            CREATE TABLE IF NOT EXISTS auth_access_decisions (
              decision_id TEXT PRIMARY KEY, actor_id TEXT NOT NULL, action TEXT NOT NULL,
              resource_id TEXT NOT NULL, tenant_id TEXT NOT NULL, workspace_id TEXT NOT NULL,
              role_scope TEXT,
              membership_version INTEGER NOT NULL, acl_version INTEGER NOT NULL,
              policy_version TEXT NOT NULL, evaluated_at TEXT NOT NULL, state TEXT NOT NULL,
              reason_codes TEXT NOT NULL, allowed_reference_ids TEXT NOT NULL,
              masked_reference_ids TEXT NOT NULL, allowed_segment_ids TEXT NOT NULL,
              masked_segment_ids TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS auth_rerun_requests (
              run_request_id TEXT PRIMARY KEY, result_id TEXT NOT NULL,
              access_decision_id TEXT NOT NULL REFERENCES auth_access_decisions(decision_id),
              tenant_id TEXT NOT NULL, workspace_id TEXT NOT NULL, actor_id TEXT NOT NULL,
              snapshot TEXT NOT NULL, created_at TEXT NOT NULL
            );
            """
        )
        columns = {
            str(row[1]) for row in connection.execute("PRAGMA table_info(auth_access_decisions)")
        }
        if "role_scope" not in columns:
            connection.execute("ALTER TABLE auth_access_decisions ADD COLUMN role_scope TEXT")
        connection.execute(
            "UPDATE auth_schema_metadata SET schema_version=? WHERE singleton=1",
            (AUTHORIZATION_SCHEMA_VERSION,),
        )

    def _ensure_open(self) -> None:
        if self._closed:
            raise AuthorizationError("PERSISTENCE_UNAVAILABLE", 503)

    @contextmanager
    def transaction(self) -> Iterator[sqlite3.Connection]:
        with self._lock:
            self._ensure_open()
            connection: sqlite3.Connection | None = None
            try:
                connection = self._connect()
                connection.execute("BEGIN IMMEDIATE")
                yield connection
                connection.execute("COMMIT")
            except AuthorizationError:
                if connection is not None and connection.in_transaction:
                    connection.execute("ROLLBACK")
                raise
            except sqlite3.IntegrityError as error:
                if connection is not None and connection.in_transaction:
                    connection.execute("ROLLBACK")
                raise AuthorizationError("PERSISTENCE_CONFLICT", 409) from error
            except sqlite3.Error as error:
                if connection is not None and connection.in_transaction:
                    connection.execute("ROLLBACK")
                raise AuthorizationError("PERSISTENCE_UNAVAILABLE", 503) from error
            except Exception:
                if connection is not None and connection.in_transaction:
                    connection.execute("ROLLBACK")
                raise
            finally:
                if connection is not None:
                    connection.close()

    def close(self) -> None:
        with self._lock:
            self._closed = True

    def bootstrap_workspace(
        self, *, tenant_id: str, workspace_id: str, owner_user_id: str,
        owner_role: Role, workspace_kind: str, data_area: str,
        cost_limit_cents: int, now: datetime,
    ) -> None:
        tenant_id = _checked_id(tenant_id)
        workspace_id = _checked_id(workspace_id)
        owner_user_id = _checked_id(owner_user_id)
        workspace_kind = _checked_id(workspace_kind)
        data_area = _checked_id(data_area)
        if not isinstance(owner_role, Role) or not isinstance(cost_limit_cents, int) or cost_limit_cents < 0:
            raise AuthorizationError("INVALID_INPUT")
        expected_role = {
            "personal": Role.PERSONAL_OWNER,
            "organization": Role.ORGANIZATION_ADMIN,
        }.get(workspace_kind)
        if expected_role is None or owner_role is not expected_role:
            raise AuthorizationError("INVALID_ROLE_SCOPE")
        timestamp = _iso(now)
        with self.transaction() as connection:
            existing_workspace = connection.execute(
                "SELECT tenant_id,workspace_kind,data_area "
                "FROM auth_workspaces WHERE workspace_id=?",
                (workspace_id,),
            ).fetchone()
            if existing_workspace is not None:
                existing_role = connection.execute(
                    "SELECT role,state FROM auth_tenant_roles WHERE tenant_id=? AND user_id=?",
                    (tenant_id, owner_user_id),
                ).fetchone()
                if (
                    str(existing_workspace["tenant_id"]) != tenant_id
                    or str(existing_workspace["workspace_kind"]) != workspace_kind
                    or str(existing_workspace["data_area"]) != data_area
                    or existing_role is None
                    or str(existing_role["role"]) != owner_role.value
                    or str(existing_role["state"]) != "active"
                ):
                    raise AuthorizationError("PERSISTENCE_CONFLICT", 409)
                return
            incompatible = connection.execute(
                "SELECT 1 FROM auth_workspaces WHERE tenant_id=? AND workspace_kind<>? LIMIT 1",
                (tenant_id, workspace_kind),
            ).fetchone()
            if incompatible is not None:
                raise AuthorizationError("INVALID_ROLE_SCOPE")
            existing_role = connection.execute(
                "SELECT role,state FROM auth_tenant_roles WHERE tenant_id=? AND user_id=?",
                (tenant_id, owner_user_id),
            ).fetchone()
            if existing_role is not None and (
                str(existing_role["role"]) != owner_role.value or str(existing_role["state"]) != "active"
            ):
                raise AuthorizationError("INVALID_ROLE_SCOPE")
            connection.execute(
                """INSERT INTO auth_workspaces(workspace_id,tenant_id,workspace_kind,data_area,
                cost_limit_cents,acl_version,version,updated_at) VALUES (?,?,?,?,?,?,?,?)""",
                (workspace_id, tenant_id, workspace_kind, data_area, cost_limit_cents, 1, 1, timestamp),
            )
            connection.execute(
                """INSERT OR IGNORE INTO auth_tenant_roles(
                tenant_id,user_id,role,state,version,updated_at) VALUES (?,?,?,?,?,?)""",
                (tenant_id, owner_user_id, owner_role.value, "active", 1, timestamp),
            )
            connection.execute(
                "INSERT OR IGNORE INTO auth_tenant_policies(tenant_id,version,updated_at) VALUES (?,?,?)",
                (tenant_id, 1, timestamp),
            )
            connection.execute(
                "INSERT INTO auth_workspace_policies(tenant_id,workspace_id,version,updated_at) VALUES (?,?,?,?)",
                (tenant_id, workspace_id, 1, timestamp),
            )

    def primary_workspace_id(self, tenant_id: str) -> str | None:
        checked_tenant = _checked_id(tenant_id)
        with self._lock:
            self._ensure_open()
            connection = self._connect()
            try:
                row = connection.execute(
                    "SELECT workspace_id FROM auth_workspaces WHERE tenant_id=? "
                    "ORDER BY CASE workspace_kind WHEN 'personal' THEN 0 ELSE 1 END, workspace_id LIMIT 1",
                    (checked_tenant,),
                ).fetchone()
                return None if row is None else str(row[0])
            finally:
                connection.close()

    def foreign_keys_enabled(self) -> int:
        with self._lock:
            self._ensure_open()
            connection = self._connect()
            try:
                return int(connection.execute("PRAGMA foreign_keys").fetchone()[0])
            finally:
                connection.close()

    def journal_mode(self) -> str:
        with self._lock:
            self._ensure_open()
            connection = self._connect()
            try:
                return str(connection.execute("PRAGMA journal_mode").fetchone()[0])
            finally:
                connection.close()

    def membership_version(self, tenant_id: str, workspace_id: str, user_id: str) -> int | None:
        with self.transaction() as connection:
            row = connection.execute(
                "SELECT version FROM auth_memberships WHERE tenant_id=? AND workspace_id=? AND user_id=?",
                (_checked_id(tenant_id), _checked_id(workspace_id), _checked_id(user_id)),
            ).fetchone()
            return None if row is None else int(row[0])

    def tenant_role_version(self, tenant_id: str, user_id: str) -> int | None:
        with self.transaction() as connection:
            row = connection.execute(
                "SELECT version FROM auth_tenant_roles WHERE tenant_id=? AND user_id=?",
                (_checked_id(tenant_id), _checked_id(user_id)),
            ).fetchone()
            return None if row is None else int(row[0])

    def _insert_descriptor(
        self, connection: sqlite3.Connection, descriptor: HistoricalResultDescriptor, now: datetime
    ) -> None:
        _validate_descriptor(descriptor)
        connection.execute(
            """INSERT INTO auth_historical_results(result_id,result_kind,tenant_id,workspace_id,
            source_version_ids,evidence_reference_ids,original_policy_version,
            original_membership_version,created_at) VALUES (?,?,?,?,?,?,?,?,?)""",
            (
                descriptor.result_id,
                descriptor.result_kind,
                descriptor.tenant_id,
                descriptor.workspace_id,
                _json_tuple(descriptor.source_version_ids),
                _json_tuple(descriptor.evidence_reference_ids),
                descriptor.original_policy_version,
                descriptor.original_membership_version,
                _iso(now),
            ),
        )
        for dependency in descriptor.dependencies:
            connection.execute(
                """INSERT INTO auth_result_dependencies(result_id,reference_id,source_version_id,
                segment_ids,decisive,safe_separation) VALUES (?,?,?,?,?,?)""",
                (
                    descriptor.result_id,
                    dependency.reference_id,
                    dependency.source_version_id,
                    _json_tuple(dependency.segment_ids),
                    int(dependency.decisive),
                    int(dependency.safe_separation),
                ),
            )

    def insert_historical_result(self, descriptor: HistoricalResultDescriptor, now: datetime) -> None:
        with self.transaction() as connection:
            self._insert_descriptor(connection, descriptor, now)

    def _read_descriptor(
        self, connection: sqlite3.Connection, tenant_id: str, workspace_id: str, result_id: str
    ) -> HistoricalResultDescriptor | None:
        row = connection.execute(
            """SELECT * FROM auth_historical_results
            WHERE tenant_id=? AND workspace_id=? AND result_id=?""",
            (tenant_id, workspace_id, result_id),
        ).fetchone()
        if row is None:
            return None
        dependencies = tuple(
            EvidenceDependency(
                str(item["reference_id"]),
                str(item["source_version_id"]),
                _decode_tuple(str(item["segment_ids"])),
                bool(item["decisive"]),
                bool(item["safe_separation"]),
            )
            for item in connection.execute(
                "SELECT * FROM auth_result_dependencies WHERE result_id=? ORDER BY reference_id",
                (result_id,),
            ).fetchall()
        )
        return HistoricalResultDescriptor(
            str(row["result_id"]),
            str(row["result_kind"]),
            str(row["tenant_id"]),
            str(row["workspace_id"]),
            _decode_tuple(str(row["source_version_ids"])),
            _decode_tuple(str(row["evidence_reference_ids"])),
            dependencies,
            str(row["original_policy_version"]),
            int(row["original_membership_version"]),
        )

    def read_historical_result(
        self, tenant_id: str, workspace_id: str, result_id: str
    ) -> HistoricalResultDescriptor | None:
        with self.transaction() as connection:
            return self._read_descriptor(
                connection, _checked_id(tenant_id), _checked_id(workspace_id), _checked_id(result_id)
            )

    def access_decision_count(self) -> int:
        with self.transaction() as connection:
            return int(connection.execute("SELECT COUNT(*) FROM auth_access_decisions").fetchone()[0])


class AuthorizationService:
    def __init__(
        self, *, repository: SqliteAuthorizationRepository, audit_store: object,
        clock: Callable[[], datetime], identity_service: object | None = None,
    ) -> None:
        self._repository = repository
        self._audit_store = audit_store
        self._clock = clock
        self._identity_service = identity_service
        self._lock = RLock()

    def _now(self) -> datetime:
        return _checked_utc(self._clock())

    def _audit(
        self, *, action: str, outcome: AuditOutcome, principal: IdentityPrincipal,
        trace_id: str, policy_version: str, workspace_id: str | None,
        target_type: str, target_id: str, before: dict[str, object] | None = None,
        after: dict[str, object] | None = None, metadata: dict[str, object] | None = None,
    ) -> None:
        draft = AuditEventDraft(
            event_id=_id("audit"),
            occurred_at=self._now(),
            actor_id=_checked_id(principal.user_id),
            actor_type=ActorType.USER,
            tenant_id=_checked_id(principal.tenant_id),
            workspace_id=None if workspace_id is None else _checked_id(workspace_id),
            action=_checked_id(action),
            target_type=_checked_id(target_type),
            target_id=_checked_id(target_id),
            outcome=outcome,
            trace_id=_checked_id(trace_id),
            policy_version=_checked_id(policy_version),
            before=before,
            after=after,
            metadata=metadata or {},
        )
        try:
            self._audit_store.append(draft)  # type: ignore[attr-defined]
        except Exception as error:
            raise AuthorizationError("AUDIT_WRITE_FAILED", 503) from error

    def _workspace(
        self, connection: sqlite3.Connection, principal: IdentityPrincipal, workspace_id: str,
        trace_id: str, policy_version: str,
    ) -> sqlite3.Row:
        row = connection.execute(
            "SELECT * FROM auth_workspaces WHERE tenant_id=? AND workspace_id=?",
            (_checked_id(principal.tenant_id), _checked_id(workspace_id)),
        ).fetchone()
        if row is None:
            self._audit(
                action="authorization.resource.denied", outcome=AuditOutcome.DENIED,
                principal=principal, trace_id=trace_id, policy_version=policy_version,
                workspace_id=None, target_type="resource", target_id="unavailable-resource",
                metadata={"reason_code": "RESOURCE_UNAVAILABLE"},
            )
            raise AuthorizationError("RESOURCE_UNAVAILABLE", 404)
        return row

    def _membership(
        self, connection: sqlite3.Connection, principal: IdentityPrincipal, workspace_id: str
    ) -> sqlite3.Row | None:
        return connection.execute(
            """SELECT * FROM auth_memberships
            WHERE tenant_id=? AND workspace_id=? AND user_id=? AND state='active'""",
            (principal.tenant_id, workspace_id, principal.user_id),
        ).fetchone()

    def _tenant_role(
        self, connection: sqlite3.Connection, principal: IdentityPrincipal
    ) -> sqlite3.Row | None:
        return connection.execute(
            """SELECT * FROM auth_tenant_roles
            WHERE tenant_id=? AND user_id=? AND state='active'""",
            (principal.tenant_id, principal.user_id),
        ).fetchone()

    def _role_binding(
        self, connection: sqlite3.Connection, principal: IdentityPrincipal,
        workspace: sqlite3.Row,
    ) -> RoleBinding | None:
        tenant_role = self._tenant_role(connection, principal)
        if tenant_role is not None:
            role = Role(str(tenant_role["role"]))
            expected_kind = (
                "personal" if role is Role.PERSONAL_OWNER else "organization"
                if role is Role.ORGANIZATION_ADMIN else None
            )
            if expected_kind == str(workspace["workspace_kind"]):
                return RoleBinding(role, RoleScope.TENANT, int(tenant_role["version"]))
            return None
        membership = self._membership(connection, principal, str(workspace["workspace_id"]))
        if membership is None:
            return None
        role = Role(str(membership["role"]))
        if role not in WORKSPACE_ROLES:
            return None
        return RoleBinding(role, RoleScope.WORKSPACE, int(membership["version"]))

    def _policy_versions(
        self, connection: sqlite3.Connection, tenant_id: str, workspace_id: str
    ) -> tuple[int, int, str]:
        tenant = connection.execute(
            "SELECT version FROM auth_tenant_policies WHERE tenant_id=?", (tenant_id,)
        ).fetchone()
        workspace = connection.execute(
            "SELECT version FROM auth_workspace_policies WHERE tenant_id=? AND workspace_id=?",
            (tenant_id, workspace_id),
        ).fetchone()
        tenant_version = int(tenant[0]) if tenant else 0
        workspace_version = int(workspace[0]) if workspace else 0
        return tenant_version, workspace_version, f"tenant-{tenant_version}:workspace-{workspace_version}"

    def _effective_permission(
        self, connection: sqlite3.Connection, *, role: Role, tenant_id: str,
        workspace_id: str, permission: Permission, requested: bool,
    ) -> EffectivePermission:
        _, _, policy_version = self._policy_versions(connection, tenant_id, workspace_id)
        if not requested:
            return EffectivePermission(permission, False, False, None, "NOT_REQUESTED", policy_version)
        tenant_rule = connection.execute(
            "SELECT effect,locked FROM auth_tenant_permission_rules WHERE tenant_id=? AND permission=?",
            (tenant_id, permission.value),
        ).fetchone()
        workspace_rule = connection.execute(
            """SELECT effect,locked FROM auth_workspace_permission_rules
            WHERE tenant_id=? AND workspace_id=? AND permission=?""",
            (tenant_id, workspace_id, permission.value),
        ).fetchone()
        if tenant_rule is not None and (bool(tenant_rule["locked"]) or tenant_rule["effect"] == PolicyEffect.DENY.value):
            effective = tenant_rule["effect"] == PolicyEffect.GRANT.value
            return EffectivePermission(
                permission, True, effective, "tenant-policy",
                "ORGANIZATION_LOCK" if bool(tenant_rule["locked"]) else "ORGANIZATION_DENY",
                policy_version,
            )
        if workspace_rule is not None:
            effective = workspace_rule["effect"] == PolicyEffect.GRANT.value
            return EffectivePermission(
                permission, True, effective,
                "workspace-policy" if bool(workspace_rule["locked"]) else None,
                "WORKSPACE_GRANT" if effective else "WORKSPACE_DENY", policy_version,
            )
        if tenant_rule is not None:
            return EffectivePermission(
                permission, True, True, None, "ORGANIZATION_GRANT", policy_version
            )
        effective = permission in ROLE_PERMISSION_DEFAULTS[role]
        return EffectivePermission(
            permission, True, effective, None,
            "ROLE_DEFAULT_GRANT" if effective else "ROLE_DEFAULT_DENY", policy_version,
        )

    def evaluate_permission(
        self, *, principal: IdentityPrincipal, workspace_id: str,
        permission: Permission, requested: bool,
    ) -> EffectivePermission:
        if not isinstance(principal, IdentityPrincipal) or not isinstance(permission, Permission):
            raise AuthorizationError("INVALID_INPUT")
        with self._repository.transaction() as connection:
            workspace = connection.execute(
                "SELECT * FROM auth_workspaces WHERE tenant_id=? AND workspace_id=?",
                (principal.tenant_id, _checked_id(workspace_id)),
            ).fetchone()
            binding = None if workspace is None else self._role_binding(connection, principal, workspace)
            if workspace is None:
                raise AuthorizationError("RESOURCE_UNAVAILABLE", 404)
            if binding is None:
                raise AuthorizationError("ACTION_DENIED", 403)
            return self._effective_permission(
                connection, role=binding.role, tenant_id=principal.tenant_id,
                workspace_id=workspace_id, permission=permission, requested=requested,
            )

    def _required_step_up(
        self, action: Action, permissions: tuple[Permission, ...]
    ) -> str | None:
        if action is Action.POLICY_MANAGE:
            return "organization_security_or_connector_policy_change"
        ordered = (
            (Permission.DATA_AREA_MOVE, "data_area_move"),
            (Permission.PRODUCTION_KNOWLEDGE_REGISTRATION, "final_approval_or_knowledge_registration"),
            (Permission.FINAL_APPROVAL_EXTERNAL_DELIVERY, "final_approval_or_knowledge_registration"),
            (Permission.FILE_DOWNLOAD_SHARE, "external_share_or_download"),
            (Permission.EXTERNAL_LLM, "external_transfer"),
        )
        permission_set = set(permissions)
        return next((group for permission, group in ordered if permission in permission_set), None)

    def _action_grant(
        self, connection: sqlite3.Connection, *, principal: IdentityPrincipal,
        workspace: sqlite3.Row, binding: RoleBinding | None, action: Action,
        requested_permissions: tuple[Permission, ...],
    ) -> AuthorizationGrant | None:
        if binding is None:
            return None
        role = binding.role
        if action not in ROLE_ACTION_MATRIX[role]:
            return None
        permissions = tuple(dict.fromkeys((*_ACTION_PERMISSIONS.get(action, frozenset()), *requested_permissions)))
        effective = tuple(
            self._effective_permission(
                connection, role=role, tenant_id=principal.tenant_id,
                workspace_id=str(workspace["workspace_id"]), permission=permission, requested=True,
            )
            for permission in permissions
        )
        if any(not decision.effective for decision in effective):
            return None
        _, _, policy_version = self._policy_versions(
            connection, principal.tenant_id, str(workspace["workspace_id"])
        )
        return AuthorizationGrant(
            True, principal.user_id, principal.tenant_id, str(workspace["workspace_id"]),
            role, binding.scope, action, effective, self._required_step_up(action, permissions),
            binding.version, int(workspace["acl_version"]), policy_version,
        )

    def authorize_action(
        self, *, principal: IdentityPrincipal, workspace_id: str, action: Action,
        trace_id: str, policy_version: str,
        requested_permissions: tuple[Permission, ...] = (),
    ) -> AuthorizationGrant:
        if not isinstance(principal, IdentityPrincipal) or not isinstance(action, Action):
            raise AuthorizationError("INVALID_INPUT")
        if any(not isinstance(permission, Permission) for permission in requested_permissions):
            raise AuthorizationError("INVALID_INPUT")
        with self._repository.transaction() as connection:
            workspace = self._workspace(connection, principal, workspace_id, trace_id, policy_version)
            binding = self._role_binding(connection, principal, workspace)
            grant = self._action_grant(
                connection, principal=principal, workspace=workspace, binding=binding,
                action=action, requested_permissions=requested_permissions,
            )
            if grant is None:
                self._audit(
                    action="authorization.action.denied", outcome=AuditOutcome.DENIED,
                    principal=principal, trace_id=trace_id, policy_version=policy_version,
                    workspace_id=workspace_id, target_type="workspace_action", target_id=workspace_id,
                    metadata={"reason_code": "ACTION_DENIED", "requested_action": action.value},
                )
                raise AuthorizationError("ACTION_DENIED", 403)
            self._audit(
                action="authorization.action.allowed", outcome=AuditOutcome.SUCCEEDED,
                principal=principal, trace_id=trace_id, policy_version=policy_version,
                workspace_id=workspace_id, target_type="workspace_action", target_id=workspace_id,
                metadata={
                    "requested_action": action.value,
                    "role": grant.role.value,
                    "role_scope": grant.role_scope.value,
                },
            )
            return grant

    def authorize_audit_read(
        self,
        *,
        principal: IdentityPrincipal,
        workspace_id: str | None,
        trace_id: str,
        policy_version: str,
    ) -> None:
        """Authorize workspace or tenant-wide Audit reads from current bindings."""
        if workspace_id is not None:
            self.authorize_action(
                principal=principal,
                workspace_id=workspace_id,
                action=Action.VIEW,
                trace_id=trace_id,
                policy_version=policy_version,
            )
            return
        with self._repository.transaction() as connection:
            tenant_role = self._tenant_role(connection, principal)
            role = None if tenant_role is None else Role(str(tenant_role["role"]))
            allowed = role in TENANT_ROLES
            self._audit(
                action=(
                    "authorization.audit_read.allowed"
                    if allowed
                    else "authorization.audit_read.denied"
                ),
                outcome=AuditOutcome.SUCCEEDED if allowed else AuditOutcome.DENIED,
                principal=principal,
                trace_id=trace_id,
                policy_version=policy_version,
                workspace_id=None,
                target_type="audit_scope",
                target_id="tenant-audit",
                metadata={
                    "scope": "tenant",
                    "reason_code": "TENANT_ROLE_ALLOWED" if allowed else "ACTION_DENIED",
                },
            )
            if not allowed:
                raise AuthorizationError("ACTION_DENIED", 403)

    def set_membership(
        self, *, principal: IdentityPrincipal, workspace_id: str, user_id: str,
        role: Role, expected_version: int, trace_id: str, policy_version: str,
    ) -> int:
        if not isinstance(role, Role) or not isinstance(expected_version, int) or expected_version < 0:
            raise AuthorizationError("INVALID_INPUT")
        user_id = _checked_id(user_id)
        now = self._now()
        with self._repository.transaction() as connection:
            workspace = self._workspace(connection, principal, workspace_id, trace_id, policy_version)
            if role not in WORKSPACE_ROLES:
                self._audit(
                    action="authorization.membership.change_denied",
                    outcome=AuditOutcome.DENIED, principal=principal,
                    trace_id=trace_id, policy_version=policy_version,
                    workspace_id=workspace_id, target_type="membership",
                    target_id="protected-membership",
                    metadata={"reason_code": "INVALID_ROLE_SCOPE"},
                )
                raise AuthorizationError("INVALID_ROLE_SCOPE")
            binding = self._role_binding(connection, principal, workspace)
            actor_role = None if binding is None else binding.role
            assignable = {
                Role.PERSONAL_OWNER: WORKSPACE_ROLES,
                Role.ORGANIZATION_ADMIN: WORKSPACE_ROLES,
                Role.WORKSPACE_ADMIN: frozenset(
                    {Role.EDITOR, Role.REVIEWER, Role.APPROVER, Role.VIEWER}
                ),
            }
            if actor_role not in assignable or role not in assignable[actor_role]:
                self._audit(
                    action="authorization.membership.change_denied", outcome=AuditOutcome.DENIED,
                    principal=principal, trace_id=trace_id, policy_version=policy_version,
                    workspace_id=workspace_id, target_type="membership", target_id="protected-membership",
                    metadata={"reason_code": "PRIVILEGE_ESCALATION_DENIED"},
                )
                raise AuthorizationError("PRIVILEGE_ESCALATION_DENIED", 403)
            current = connection.execute(
                "SELECT role,state,version FROM auth_memberships WHERE tenant_id=? AND workspace_id=? AND user_id=?",
                (principal.tenant_id, workspace_id, user_id),
            ).fetchone()
            current_version = 0 if current is None else int(current["version"])
            if current_version != expected_version:
                self._audit(
                    action="authorization.membership.change_denied",
                    outcome=AuditOutcome.DENIED, principal=principal,
                    trace_id=trace_id, policy_version=policy_version,
                    workspace_id=workspace_id, target_type="membership",
                    target_id="protected-membership",
                    metadata={"reason_code": "VERSION_CONFLICT"},
                )
                raise AuthorizationError("VERSION_CONFLICT", 412)
            next_version = current_version + 1
            before = None if current is None else {
                "role": str(current["role"]), "state": str(current["state"]), "version": current_version
            }
            connection.execute(
                """INSERT INTO auth_memberships(tenant_id,workspace_id,user_id,role,state,version,updated_at)
                VALUES (?,?,?,?,?,?,?) ON CONFLICT(tenant_id,workspace_id,user_id) DO UPDATE SET
                role=excluded.role,state='active',version=excluded.version,updated_at=excluded.updated_at""",
                (principal.tenant_id, workspace_id, user_id, role.value, "active", next_version, _iso(now)),
            )
            connection.execute(
                "UPDATE auth_workspaces SET acl_version=acl_version+1,updated_at=? WHERE tenant_id=? AND workspace_id=?",
                (_iso(now), principal.tenant_id, workspace_id),
            )
            self._audit(
                action="authorization.membership.changed", outcome=AuditOutcome.SUCCEEDED,
                principal=principal, trace_id=trace_id, policy_version=policy_version,
                workspace_id=workspace_id, target_type="membership", target_id=user_id,
                before=before, after={"role": role.value, "state": "active", "version": next_version},
            )
            return next_version

    def set_tenant_role(
        self, *, principal: IdentityPrincipal, user_id: str, role: Role | None,
        expected_version: int, trace_id: str, policy_version: str,
    ) -> int:
        if role is not None and (not isinstance(role, Role) or role not in TENANT_ROLES):
            raise AuthorizationError("INVALID_ROLE_SCOPE")
        if not isinstance(expected_version, int) or expected_version < 0:
            raise AuthorizationError("INVALID_INPUT")
        user_id = _checked_id(user_id)
        now = self._now()
        with self._lock, self._repository.transaction() as connection:
            actor = self._tenant_role(connection, principal)
            if actor is None or Role(str(actor["role"])) not in TENANT_ROLES:
                self._audit(
                    action="authorization.tenant_role.change_denied",
                    outcome=AuditOutcome.DENIED, principal=principal,
                    trace_id=trace_id, policy_version=policy_version,
                    workspace_id=None, target_type="tenant_role",
                    target_id="protected-tenant-role",
                    metadata={"reason_code": "PRIVILEGE_ESCALATION_DENIED"},
                )
                raise AuthorizationError("PRIVILEGE_ESCALATION_DENIED", 403)
            kinds = {
                str(row[0]) for row in connection.execute(
                    "SELECT DISTINCT workspace_kind FROM auth_workspaces WHERE tenant_id=?",
                    (principal.tenant_id,),
                )
            }
            expected_role = (
                Role.PERSONAL_OWNER if kinds == {"personal"}
                else Role.ORGANIZATION_ADMIN if kinds == {"organization"}
                else None
            )
            if expected_role is None or (role is not None and role is not expected_role):
                raise AuthorizationError("INVALID_ROLE_SCOPE")
            current = connection.execute(
                "SELECT role,state,version FROM auth_tenant_roles WHERE tenant_id=? AND user_id=?",
                (principal.tenant_id, user_id),
            ).fetchone()
            current_version = 0 if current is None else int(current["version"])
            if current_version != expected_version:
                self._audit(
                    action="authorization.tenant_role.change_denied",
                    outcome=AuditOutcome.DENIED, principal=principal,
                    trace_id=trace_id, policy_version=policy_version,
                    workspace_id=None, target_type="tenant_role",
                    target_id="protected-tenant-role",
                    metadata={"reason_code": "VERSION_CONFLICT"},
                )
                raise AuthorizationError("VERSION_CONFLICT", 412)
            if current is None and role is None:
                raise AuthorizationError("INVALID_ROLE_SCOPE")
            next_version = current_version + 1
            next_role = role.value if role is not None else str(current["role"])
            next_state = "active" if role is not None else "revoked"
            connection.execute(
                """INSERT INTO auth_tenant_roles(tenant_id,user_id,role,state,version,updated_at)
                VALUES (?,?,?,?,?,?) ON CONFLICT(tenant_id,user_id) DO UPDATE SET
                role=excluded.role,state=excluded.state,version=excluded.version,
                updated_at=excluded.updated_at""",
                (principal.tenant_id, user_id, next_role, next_state, next_version, _iso(now)),
            )
            self._audit(
                action="authorization.tenant_role.changed", outcome=AuditOutcome.SUCCEEDED,
                principal=principal, trace_id=trace_id, policy_version=policy_version,
                workspace_id=None, target_type="tenant_role", target_id=user_id,
                before=None if current is None else {
                    "role": str(current["role"]), "state": str(current["state"]),
                    "version": current_version,
                },
                after={"role": next_role, "state": next_state, "version": next_version},
            )
            return next_version

    def set_source_access(
        self, *, principal: IdentityPrincipal, workspace_id: str, user_id: str,
        source_version_id: str, allowed: bool, expected_version: int,
        trace_id: str, policy_version: str,
    ) -> int:
        user_id = _checked_id(user_id)
        source_version_id = _checked_id(source_version_id)
        if not isinstance(allowed, bool) or not isinstance(expected_version, int) or expected_version < 0:
            raise AuthorizationError("INVALID_INPUT")
        now = self._now()
        with self._repository.transaction() as connection:
            workspace = self._workspace(connection, principal, workspace_id, trace_id, policy_version)
            binding = self._role_binding(connection, principal, workspace)
            if binding is None or binding.role not in {
                Role.PERSONAL_OWNER, Role.ORGANIZATION_ADMIN, Role.WORKSPACE_ADMIN
            }:
                raise AuthorizationError("ACTION_DENIED", 403)
            current = connection.execute(
                """SELECT allowed,version FROM auth_source_access
                WHERE tenant_id=? AND workspace_id=? AND user_id=? AND source_version_id=?""",
                (principal.tenant_id, workspace_id, user_id, source_version_id),
            ).fetchone()
            current_version = 0 if current is None else int(current["version"])
            if current_version != expected_version:
                raise AuthorizationError("VERSION_CONFLICT", 412)
            next_version = current_version + 1
            connection.execute(
                """INSERT INTO auth_source_access(tenant_id,workspace_id,user_id,source_version_id,allowed,version,updated_at)
                VALUES (?,?,?,?,?,?,?) ON CONFLICT(tenant_id,workspace_id,user_id,source_version_id)
                DO UPDATE SET allowed=excluded.allowed,version=excluded.version,updated_at=excluded.updated_at""",
                (principal.tenant_id, workspace_id, user_id, source_version_id, int(allowed), next_version, _iso(now)),
            )
            connection.execute(
                "UPDATE auth_workspaces SET acl_version=acl_version+1,updated_at=? WHERE tenant_id=? AND workspace_id=?",
                (_iso(now), principal.tenant_id, workspace_id),
            )
            self._audit(
                action="authorization.source_access.changed", outcome=AuditOutcome.SUCCEEDED,
                principal=principal, trace_id=trace_id, policy_version=policy_version,
                workspace_id=workspace_id, target_type="source_access", target_id=source_version_id,
                before=None if current is None else {"allowed": bool(current["allowed"]), "version": current_version},
                after={"allowed": allowed, "version": next_version},
            )
            return next_version

    def set_permission_policy(
        self, *, access_token: str, step_up_authorization: str | None, scope: str,
        workspace_id: str, permission: Permission, effect: PolicyEffect, locked: bool,
        expected_version: int, trace_id: str, policy_version: str,
    ) -> int:
        if self._identity_service is None:
            raise AuthorizationError("IDENTITY_BOUNDARY_REQUIRED", 503)
        if scope not in {"tenant", "workspace"} or not isinstance(permission, Permission):
            raise AuthorizationError("INVALID_INPUT")
        if not isinstance(effect, PolicyEffect) or not isinstance(locked, bool):
            raise AuthorizationError("INVALID_INPUT")
        if not isinstance(expected_version, int) or expected_version < 1:
            raise AuthorizationError("INVALID_INPUT")
        principal = self._identity_service.validate_access(  # type: ignore[attr-defined]
            access_token, trace_id=trace_id, policy_version=policy_version
        )
        target_id = principal.tenant_id if scope == "tenant" else _checked_id(workspace_id)
        with self._repository.transaction() as connection:
            workspace = self._workspace(
                connection, principal, workspace_id, trace_id, policy_version
            )
            binding = self._role_binding(connection, principal, workspace)
            allowed = (
                binding is not None and binding.scope is RoleScope.TENANT
                if scope == "tenant"
                else binding is not None and binding.role in {
                    Role.PERSONAL_OWNER, Role.ORGANIZATION_ADMIN, Role.WORKSPACE_ADMIN
                }
            )
            if not allowed:
                self._audit(
                    action="authorization.policy.change_denied",
                    outcome=AuditOutcome.DENIED, principal=principal,
                    trace_id=trace_id, policy_version=policy_version,
                    workspace_id=workspace_id, target_type=f"{scope}_policy",
                    target_id="protected-policy",
                    metadata={"reason_code": "PRIVILEGE_ESCALATION_DENIED"},
                )
                raise AuthorizationError("PRIVILEGE_ESCALATION_DENIED", 403)
        self._identity_service.consume_step_up(  # type: ignore[attr-defined]
            step_up_authorization=step_up_authorization,
            access_token=access_token,
            action_group="organization_security_or_connector_policy_change",
            target_id=target_id,
            policy_version=policy_version,
            trace_id=trace_id,
        )
        now = self._now()
        with self._lock, self._repository.transaction() as connection:
            workspace = self._workspace(connection, principal, workspace_id, trace_id, policy_version)
            binding = self._role_binding(connection, principal, workspace)
            allowed = (
                binding is not None and binding.scope is RoleScope.TENANT
                if scope == "tenant"
                else binding is not None and binding.role in {
                    Role.PERSONAL_OWNER, Role.ORGANIZATION_ADMIN, Role.WORKSPACE_ADMIN
                }
            )
            if not allowed:
                self._audit(
                    action="authorization.policy.change_denied",
                    outcome=AuditOutcome.DENIED, principal=principal,
                    trace_id=trace_id, policy_version=policy_version,
                    workspace_id=workspace_id, target_type=f"{scope}_policy",
                    target_id="protected-policy",
                    metadata={"reason_code": "PRIVILEGE_ESCALATION_DENIED"},
                )
                raise AuthorizationError("PRIVILEGE_ESCALATION_DENIED", 403)
            if scope == "tenant":
                header = connection.execute(
                    "SELECT version FROM auth_tenant_policies WHERE tenant_id=?", (principal.tenant_id,)
                ).fetchone()
                rule = connection.execute(
                    "SELECT effect,locked FROM auth_tenant_permission_rules WHERE tenant_id=? AND permission=?",
                    (principal.tenant_id, permission.value),
                ).fetchone()
            else:
                header = connection.execute(
                    "SELECT version FROM auth_workspace_policies WHERE tenant_id=? AND workspace_id=?",
                    (principal.tenant_id, workspace_id),
                ).fetchone()
                rule = connection.execute(
                    """SELECT effect,locked FROM auth_workspace_permission_rules
                    WHERE tenant_id=? AND workspace_id=? AND permission=?""",
                    (principal.tenant_id, workspace_id, permission.value),
                ).fetchone()
            current_version = 0 if header is None else int(header["version"])
            if current_version != expected_version:
                self._audit(
                    action="authorization.policy.change_denied",
                    outcome=AuditOutcome.DENIED, principal=principal,
                    trace_id=trace_id, policy_version=policy_version,
                    workspace_id=workspace_id, target_type=f"{scope}_policy",
                    target_id="protected-policy",
                    metadata={"reason_code": "VERSION_CONFLICT"},
                )
                raise AuthorizationError("VERSION_CONFLICT", 412)
            next_version = current_version + 1
            if scope == "tenant":
                connection.execute(
                    "UPDATE auth_tenant_policies SET version=?,updated_at=? WHERE tenant_id=?",
                    (next_version, _iso(now), principal.tenant_id),
                )
                connection.execute(
                    """INSERT INTO auth_tenant_permission_rules(tenant_id,permission,effect,locked)
                    VALUES (?,?,?,?) ON CONFLICT(tenant_id,permission) DO UPDATE SET
                    effect=excluded.effect,locked=excluded.locked""",
                    (principal.tenant_id, permission.value, effect.value, int(locked)),
                )
            else:
                connection.execute(
                    """UPDATE auth_workspace_policies SET version=?,updated_at=?
                    WHERE tenant_id=? AND workspace_id=?""",
                    (next_version, _iso(now), principal.tenant_id, workspace_id),
                )
                connection.execute(
                    """INSERT INTO auth_workspace_permission_rules(tenant_id,workspace_id,permission,effect,locked)
                    VALUES (?,?,?,?,?) ON CONFLICT(tenant_id,workspace_id,permission) DO UPDATE SET
                    effect=excluded.effect,locked=excluded.locked""",
                    (principal.tenant_id, workspace_id, permission.value, effect.value, int(locked)),
                )
            self._audit(
                action="authorization.policy.changed", outcome=AuditOutcome.SUCCEEDED,
                principal=principal, trace_id=trace_id, policy_version=policy_version,
                workspace_id=str(workspace["workspace_id"]), target_type=f"{scope}_policy",
                target_id=target_id,
                before=None if rule is None else {
                    "permission": permission.value, "effect": str(rule["effect"]),
                    "locked": bool(rule["locked"]), "version": current_version,
                },
                after={
                    "permission": permission.value, "effect": effect.value,
                    "locked": locked, "version": next_version,
                },
            )
            return next_version

    def register_historical_result(
        self, *, principal: IdentityPrincipal, descriptor: HistoricalResultDescriptor,
        trace_id: str, policy_version: str,
    ) -> None:
        _validate_descriptor(descriptor)
        if descriptor.tenant_id != principal.tenant_id:
            raise AuthorizationError("RESOURCE_UNAVAILABLE", 404)
        self.authorize_action(
            principal=principal, workspace_id=descriptor.workspace_id, action=Action.GENERATE,
            trace_id=trace_id, policy_version=policy_version,
        )
        with self._repository.transaction() as connection:
            self._workspace(connection, principal, descriptor.workspace_id, trace_id, policy_version)
            if connection.execute(
                "SELECT 1 FROM auth_historical_results WHERE result_id=?", (descriptor.result_id,)
            ).fetchone() is not None:
                raise AuthorizationError("HISTORICAL_RESULT_IMMUTABLE", 409)
            self._repository._insert_descriptor(connection, descriptor, self._now())
            self._audit(
                action="authorization.historical_result.registered", outcome=AuditOutcome.SUCCEEDED,
                principal=principal, trace_id=trace_id, policy_version=policy_version,
                workspace_id=descriptor.workspace_id, target_type="historical_result",
                target_id=descriptor.result_id,
                after={
                    "result_kind": descriptor.result_kind,
                    "source_count": len(descriptor.source_version_ids),
                    "reference_count": len(descriptor.evidence_reference_ids),
                },
            )

    def _find_descriptor_for_principal(
        self, connection: sqlite3.Connection, principal: IdentityPrincipal, result_id: str,
        trace_id: str, policy_version: str,
    ) -> HistoricalResultDescriptor:
        result_id = _checked_id(result_id)
        row = connection.execute(
            "SELECT workspace_id FROM auth_historical_results WHERE tenant_id=? AND result_id=?",
            (principal.tenant_id, result_id),
        ).fetchone()
        if row is None:
            self._audit(
                action="authorization.resource.denied", outcome=AuditOutcome.DENIED,
                principal=principal, trace_id=trace_id, policy_version=policy_version,
                workspace_id=None, target_type="historical_result", target_id="unavailable-resource",
                metadata={"reason_code": "RESOURCE_UNAVAILABLE"},
            )
            raise AuthorizationError("RESOURCE_UNAVAILABLE", 404)
        descriptor = self._repository._read_descriptor(
            connection, principal.tenant_id, str(row["workspace_id"]), result_id
        )
        if descriptor is None:
            raise AuthorizationError("RESOURCE_UNAVAILABLE", 404)
        return descriptor

    def evaluate_historical_access(
        self, *, principal: IdentityPrincipal, result_id: str, action: AccessAction,
        trace_id: str, policy_version: str,
    ) -> AccessDecision:
        if not isinstance(principal, IdentityPrincipal) or not isinstance(action, AccessAction):
            raise AuthorizationError("INVALID_INPUT")
        with self._repository.transaction() as connection:
            descriptor = self._find_descriptor_for_principal(
                connection, principal, result_id, trace_id, policy_version
            )
            workspace = self._workspace(
                connection, principal, descriptor.workspace_id, trace_id, policy_version
            )
            binding = self._role_binding(connection, principal, workspace)
            base_action = _ACCESS_ACTIONS[action]
            grant = self._action_grant(
                connection, principal=principal, workspace=workspace, binding=binding,
                action=base_action, requested_permissions=(),
            )
            reasons: list[str] = []
            allowed_references: list[str] = []
            masked_references: list[str] = []
            allowed_segments: list[str] = []
            masked_segments: list[str] = []
            state = AccessState.AVAILABLE
            if grant is None:
                state = AccessState.ACCESS_BLOCKED
                reasons.append("CURRENT_MEMBERSHIP_OR_PERMISSION_DENIED")
                masked_references.extend(descriptor.evidence_reference_ids)
                masked_segments.extend(
                    segment for dependency in descriptor.dependencies for segment in dependency.segment_ids
                )
            else:
                for dependency in descriptor.dependencies:
                    source_rule = connection.execute(
                        """SELECT allowed FROM auth_source_access WHERE tenant_id=? AND workspace_id=?
                        AND user_id=? AND source_version_id=?""",
                        (
                            principal.tenant_id,
                            descriptor.workspace_id,
                            principal.user_id,
                            dependency.source_version_id,
                        ),
                    ).fetchone()
                    source_allowed = source_rule is None or bool(source_rule["allowed"])
                    if source_allowed:
                        allowed_references.append(dependency.reference_id)
                        allowed_segments.extend(dependency.segment_ids)
                    else:
                        masked_references.append(dependency.reference_id)
                        masked_segments.extend(dependency.segment_ids)
                        if dependency.decisive:
                            reasons.append("DECISIVE_DEPENDENCY_DENIED")
                            state = AccessState.ACCESS_BLOCKED
                        elif not dependency.safe_separation:
                            reasons.append("SAFE_SEPARATION_UNAVAILABLE")
                            state = AccessState.ACCESS_BLOCKED
                        elif state is not AccessState.ACCESS_BLOCKED:
                            reasons.append("SOURCE_ACCESS_REVOKED")
                            state = AccessState.PARTIALLY_REDACTED
            _, _, current_policy = self._policy_versions(
                connection, principal.tenant_id, descriptor.workspace_id
            )
            decision = AccessDecision(
                decision_id=_id("acd"),
                actor_id=principal.user_id,
                action=action,
                resource_id=descriptor.result_id,
                tenant_id=principal.tenant_id,
                workspace_id=descriptor.workspace_id,
                role_scope=None if binding is None else binding.scope,
                membership_version=0 if binding is None else binding.version,
                acl_version=int(workspace["acl_version"]),
                policy_version=current_policy,
                evaluated_at=self._now(),
                state=state,
                reason_codes=tuple(sorted(set(reasons))) or ("CURRENT_ACCESS_ALLOWED",),
                allowed_reference_ids=tuple(sorted(allowed_references)),
                masked_reference_ids=tuple(sorted(masked_references)),
                allowed_segment_ids=tuple(sorted(allowed_segments)),
                masked_segment_ids=tuple(sorted(masked_segments)),
            )
            connection.execute(
                """INSERT INTO auth_access_decisions(decision_id,actor_id,action,resource_id,tenant_id,
                workspace_id,role_scope,membership_version,acl_version,policy_version,evaluated_at,state,reason_codes,
                allowed_reference_ids,masked_reference_ids,allowed_segment_ids,masked_segment_ids)
                VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                (
                    decision.decision_id,
                    decision.actor_id,
                    decision.action.value,
                    decision.resource_id,
                    decision.tenant_id,
                    decision.workspace_id,
                    None if decision.role_scope is None else decision.role_scope.value,
                    decision.membership_version,
                    decision.acl_version,
                    decision.policy_version,
                    _iso(decision.evaluated_at),
                    decision.state.value,
                    _json_tuple(decision.reason_codes),
                    _json_tuple(decision.allowed_reference_ids),
                    _json_tuple(decision.masked_reference_ids),
                    _json_tuple(decision.allowed_segment_ids),
                    _json_tuple(decision.masked_segment_ids),
                ),
            )
            self._audit(
                action="authorization.access.decided",
                outcome=(AuditOutcome.SUCCEEDED if state is not AccessState.ACCESS_BLOCKED else AuditOutcome.DENIED),
                principal=principal,
                trace_id=trace_id,
                policy_version=policy_version,
                workspace_id=descriptor.workspace_id,
                target_type="access_decision",
                target_id=decision.decision_id,
                metadata={
                    "access_state": state.value,
                    "requested_action": action.value,
                    "reason_codes": list(decision.reason_codes),
                    "allowed_reference_count": len(decision.allowed_reference_ids),
                    "masked_reference_count": len(decision.masked_reference_ids),
                },
            )
            return decision

    def require_historical_access(
        self, *, principal: IdentityPrincipal, result_id: str, action: AccessAction,
        trace_id: str, policy_version: str,
    ) -> AccessDecision:
        decision = self.evaluate_historical_access(
            principal=principal, result_id=result_id, action=action,
            trace_id=trace_id, policy_version=policy_version,
        )
        if decision.state is AccessState.ACCESS_BLOCKED:
            raise AuthorizationError("CURRENT_ACCESS_DENIED", 403, decision=decision)
        return decision

    def authorize_rerun(
        self, *, principal: IdentityPrincipal, result_id: str,
        trace_id: str, policy_version: str,
    ) -> RerunAuthorization:
        decision = self.require_historical_access(
            principal=principal, result_id=result_id, action=AccessAction.RERUN,
            trace_id=trace_id, policy_version=policy_version,
        )
        with self._repository.transaction() as connection:
            descriptor = self._find_descriptor_for_principal(
                connection, principal, result_id, trace_id, policy_version
            )
            workspace = self._workspace(
                connection, principal, descriptor.workspace_id, trace_id, policy_version
            )
            binding = self._role_binding(connection, principal, workspace)
            if binding is None:
                raise AuthorizationError("CURRENT_ACCESS_DENIED", 403, decision=decision)
            allowed_reference_set = set(decision.allowed_reference_ids)
            allowed_sources = tuple(
                sorted(
                    {
                        dependency.source_version_id
                        for dependency in descriptor.dependencies
                        if dependency.reference_id in allowed_reference_set
                    }
                )
            )
            snapshot = RerunSnapshot(
                tenant_id=principal.tenant_id,
                workspace_id=descriptor.workspace_id,
                actor_id=principal.user_id,
                role_scope=binding.scope,
                membership_version=binding.version,
                acl_version=int(workspace["acl_version"]),
                policy_version=decision.policy_version,
                data_area=str(workspace["data_area"]),
                cost_limit_cents=int(workspace["cost_limit_cents"]),
                source_version_ids=allowed_sources,
            )
            run_request_id = _id("rrq")
            snapshot_payload = {
                "tenant_id": snapshot.tenant_id,
                "workspace_id": snapshot.workspace_id,
                "actor_id": snapshot.actor_id,
                "role_scope": snapshot.role_scope.value,
                "membership_version": snapshot.membership_version,
                "acl_version": snapshot.acl_version,
                "policy_version": snapshot.policy_version,
                "data_area": snapshot.data_area,
                "cost_limit_cents": snapshot.cost_limit_cents,
                "source_version_ids": snapshot.source_version_ids,
            }
            connection.execute(
                """INSERT INTO auth_rerun_requests(run_request_id,result_id,access_decision_id,
                tenant_id,workspace_id,actor_id,snapshot,created_at) VALUES (?,?,?,?,?,?,?,?)""",
                (
                    run_request_id,
                    descriptor.result_id,
                    decision.decision_id,
                    principal.tenant_id,
                    descriptor.workspace_id,
                    principal.user_id,
                    json.dumps(snapshot_payload, sort_keys=True, separators=(",", ":")),
                    _iso(self._now()),
                ),
            )
            self._audit(
                action="authorization.rerun.authorized", outcome=AuditOutcome.SUCCEEDED,
                principal=principal, trace_id=trace_id, policy_version=policy_version,
                workspace_id=descriptor.workspace_id, target_type="run_request",
                target_id=run_request_id,
                metadata={
                    "access_state": decision.state.value,
                    "source_count": len(snapshot.source_version_ids),
                    "current_snapshot": True,
                },
            )
            return RerunAuthorization(run_request_id, decision.decision_id, snapshot)


def authorization_contract_summary() -> dict[str, object]:
    return {
        "schema_version": str(AUTHORIZATION_SCHEMA_VERSION),
        "roles": [role.value for role in Role],
        "role_scopes": [scope.value for scope in RoleScope],
        "tenant_roles": [role.value for role in Role if role in TENANT_ROLES],
        "workspace_roles": [role.value for role in Role if role in WORKSPACE_ROLES],
        "actions": [action.value for action in Action],
        "permissions": [permission.value for permission in Permission],
        "access_actions": [action.value for action in AccessAction],
        "access_states": [state.value for state in AccessState],
        "tenant_isolation": "service_auth_predicate_m4_04;postgres_rls_m5",
        "historical_access": "current_role_binding_acl_source_policy",
        "http_runtime_implemented": False,
        "postgresql_rls_implemented": False,
    }
