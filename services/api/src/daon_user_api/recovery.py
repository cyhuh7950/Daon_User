"""Verified cloud backup and fixture-only isolated restore contract."""

from __future__ import annotations

import hashlib
import json
import re
import secrets
import threading
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from typing import Callable


_SAFE_ID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:-]{0,127}$")
_SHA256 = re.compile(r"^[0-9a-f]{64}$")


class RecoveryError(RuntimeError):
    def __init__(self, code: str, status: int = 409, *, retryable: bool = False) -> None:
        self.code = code
        self.status = status
        self.retryable = retryable
        super().__init__(code)


def _safe(value: object) -> str:
    if not isinstance(value, str) or not _SAFE_ID.fullmatch(value):
        raise RecoveryError("RECOVERY_INPUT_INVALID", 400)
    return value


def _fingerprint(value: object) -> str:
    raw = json.dumps(value, sort_keys=True, separators=(",", ":"), default=str).encode()
    return hashlib.sha256(raw).hexdigest()


@dataclass(frozen=True, slots=True)
class RecoveryContext:
    tenant_id: str
    workspace_id: str
    actor_id: str
    trace_id: str
    policy_version: str
    organization_admin: bool = False

    def __post_init__(self) -> None:
        for value in (
            self.tenant_id, self.workspace_id, self.actor_id,
            self.trace_id, self.policy_version,
        ):
            _safe(value)
        if not isinstance(self.organization_admin, bool):
            raise RecoveryError("RECOVERY_CONTEXT_INVALID", 400)


@dataclass(frozen=True, slots=True)
class BackupObjectInput:
    object_id: str
    checksum_sha256: str
    byte_size: int

    def __post_init__(self) -> None:
        _safe(self.object_id)
        if not isinstance(self.checksum_sha256, str) or not _SHA256.fullmatch(
            self.checksum_sha256
        ):
            raise RecoveryError("BACKUP_MANIFEST_INVALID", 400)
        if not isinstance(self.byte_size, int) or isinstance(self.byte_size, bool) or self.byte_size < 0:
            raise RecoveryError("BACKUP_MANIFEST_INVALID", 400)


@dataclass(frozen=True, slots=True)
class RestoreDestination:
    tenant_id: str
    workspace_id: str
    database_id: str
    bucket_id: str

    def __post_init__(self) -> None:
        for value in (self.tenant_id, self.workspace_id, self.database_id, self.bucket_id):
            _safe(value)


@dataclass(frozen=True, slots=True)
class BackupView:
    backup_id: str
    tenant_id: str
    workspace_id: str
    state: str
    version: int
    trigger: str
    created_at: datetime
    verified_at: datetime | None
    schema_revision: str
    retention_watermark: str
    manifest_digest: str
    object_count: int
    transitions: tuple[str, ...]

    @property
    def etag(self) -> str:
        return f'"backup:{self.backup_id}:{self.version}"'


@dataclass(frozen=True, slots=True)
class RestorePreviewView:
    version: int
    included_object_ids: tuple[str, ...]
    excluded_object_ids: tuple[str, ...]
    exclusion_reasons: tuple[tuple[str, str], ...]
    destination: RestoreDestination
    created_at: datetime


@dataclass(frozen=True, slots=True)
class RestoreRequestView:
    request_id: str
    backup_id: str
    tenant_id: str
    workspace_id: str
    state: str
    version: int
    preview: RestorePreviewView
    transitions: tuple[str, ...]
    verification_digest: str | None

    @property
    def etag(self) -> str:
        return f'"restore:{self.request_id}:{self.version}"'


@dataclass(slots=True)
class _Backup:
    backup_id: str
    context: RecoveryContext
    trigger: str
    created_at: datetime
    schema_revision: str
    retention_watermark: str
    objects: tuple[BackupObjectInput, ...]
    state: str = "queued"
    version: int = 1
    verified_at: datetime | None = None
    transitions: list[str] = field(default_factory=lambda: ["queued"])


@dataclass(slots=True)
class _RestoreRequest:
    request_id: str
    context: RecoveryContext
    backup_id: str
    preview: RestorePreviewView
    state: str = "requested"
    version: int = 1
    transitions: list[str] = field(default_factory=lambda: ["requested"])
    verification_digest: str | None = None


class ReferenceRecoveryRepository:
    def __init__(self) -> None:
        self.lock = threading.RLock()
        self.backups: dict[str, _Backup] = {}
        self.restores: dict[str, _RestoreRequest] = {}
        self.idempotency: dict[tuple[str, str, str], tuple[str, str]] = {}
        self.retention: dict[tuple[str, str], dict[str, set[str]]] = {}
        self.audit: list[dict[str, object]] = []


class ReferenceRestorePort:
    """Fixture-only restore port; original data is intentionally not writable."""

    def __init__(self) -> None:
        self.restored_object_ids: list[str] = []
        self.destinations: list[RestoreDestination] = []
        self.original_mutations = 0
        self.fail_object_ids: set[str] = set()

    def restore(
        self, destination: RestoreDestination, objects: tuple[BackupObjectInput, ...]
    ) -> bool:
        if any(item.object_id in self.fail_object_ids for item in objects):
            return False
        self.destinations.append(destination)
        self.restored_object_ids.extend(item.object_id for item in objects)
        return True


class UnavailableRecoveryService:
    """Fail-closed runtime port used when durable recovery is not configured."""

    @staticmethod
    def _unavailable(*_args: object, **_kwargs: object) -> object:
        raise RecoveryError("RESOURCE_UNAVAILABLE", 503, retryable=False)

    create_backup = _unavailable
    list_backups = _unavailable
    get_backup = _unavailable
    locate_backup_workspace = _unavailable
    locate_restore_workspace = _unavailable
    backup_due = _unavailable
    create_restore_preview = _unavailable
    get_restore_request = _unavailable
    execute_restore = _unavailable
    cancel_restore = _unavailable

    @staticmethod
    def close() -> None:
        return None


class RecoveryService:
    def __init__(
        self,
        repository: ReferenceRecoveryRepository,
        restore_port: ReferenceRestorePort,
        *,
        clock: Callable[[], datetime],
        fixture_prefix: str = "fixture-",
        rpo: timedelta = timedelta(minutes=15),
    ) -> None:
        self._repository = repository
        self._restore = restore_port
        self._clock = clock
        self._fixture_prefix = fixture_prefix
        self._rpo = rpo

    @staticmethod
    def _id(prefix: str) -> str:
        return f"{prefix}-{secrets.token_hex(12)}"

    @staticmethod
    def _require_admin(context: RecoveryContext) -> None:
        if not context.organization_admin:
            raise RecoveryError("CURRENT_ACCESS_DENIED", 403)

    @staticmethod
    def _require_step_up(context: RecoveryContext, verified: bool) -> None:
        RecoveryService._require_admin(context)
        if not verified:
            raise RecoveryError("STEP_UP_REQUIRED", 403)

    def _audit(self, context: RecoveryContext, action: str, target: str) -> None:
        previous = "0" * 64 if not self._repository.audit else str(
            self._repository.audit[-1]["event_hash"]
        )
        event: dict[str, object] = {
            "actor_id": context.actor_id,
            "action": action,
            "target": target,
            "policy_version": context.policy_version,
            "trace_id": context.trace_id,
            "timestamp": self._clock().isoformat(),
            "previous_hash": previous,
        }
        event["event_hash"] = _fingerprint(event)
        self._repository.audit.append(event)

    def _replay(self, key: tuple[str, str, str], fingerprint: str) -> str | None:
        replay = self._repository.idempotency.get(key)
        if replay is None:
            return None
        if replay[0] != fingerprint:
            raise RecoveryError("IDEMPOTENCY_KEY_REUSED", 409)
        return replay[1]

    def _backup(self, context: RecoveryContext, backup_id: str) -> _Backup:
        backup = self._repository.backups.get(backup_id)
        if (
            backup is None
            or backup.context.tenant_id != context.tenant_id
            or backup.context.workspace_id != context.workspace_id
        ):
            raise RecoveryError("BACKUP_UNAVAILABLE", 404)
        return backup

    def _restore_request(self, context: RecoveryContext, request_id: str) -> _RestoreRequest:
        request = self._repository.restores.get(request_id)
        if (
            request is None
            or request.context.tenant_id != context.tenant_id
            or request.context.workspace_id != context.workspace_id
        ):
            raise RecoveryError("RESTORE_REQUEST_UNAVAILABLE", 404)
        return request

    @staticmethod
    def _manifest_digest(backup: _Backup) -> str:
        return _fingerprint((
            backup.context.tenant_id,
            backup.context.workspace_id,
            backup.schema_revision,
            backup.retention_watermark,
            backup.objects,
        ))

    def _backup_view(self, backup: _Backup) -> BackupView:
        return BackupView(
            backup.backup_id, backup.context.tenant_id, backup.context.workspace_id,
            backup.state, backup.version, backup.trigger, backup.created_at,
            backup.verified_at, backup.schema_revision, backup.retention_watermark,
            self._manifest_digest(backup), len(backup.objects), tuple(backup.transitions),
        )

    @staticmethod
    def _restore_view(request: _RestoreRequest) -> RestoreRequestView:
        return RestoreRequestView(
            request.request_id, request.backup_id, request.context.tenant_id,
            request.context.workspace_id, request.state, request.version, request.preview,
            tuple(request.transitions), request.verification_digest,
        )

    def create_backup(
        self,
        context: RecoveryContext,
        *,
        trigger: str,
        schema_revision: str,
        retention_watermark: str,
        objects: tuple[BackupObjectInput, ...],
        idempotency_key: str,
    ) -> BackupView:
        self._require_admin(context)
        _safe(idempotency_key)
        _safe(schema_revision)
        _safe(retention_watermark)
        if trigger not in {"automatic", "manual"} or not objects:
            raise RecoveryError("RECOVERY_INPUT_INVALID", 400)
        if len({item.object_id for item in objects}) != len(objects):
            raise RecoveryError("BACKUP_MANIFEST_INVALID", 400)
        fingerprint = _fingerprint((
            context.tenant_id, context.workspace_id, trigger, schema_revision,
            retention_watermark, objects,
        ))
        key = (context.actor_id, "backup", idempotency_key)
        with self._repository.lock:
            replay = self._replay(key, fingerprint)
            if replay is not None:
                return self._backup_view(self._backup(context, replay))
            now = self._clock()
            backup = _Backup(
                self._id("backup"), context, trigger, now, schema_revision,
                retention_watermark, objects,
            )
            for state in ("capturing", "verifying", "ready"):
                backup.state = state
                backup.transitions.append(state)
            backup.verified_at = now
            backup.version += 1
            self._repository.backups[backup.backup_id] = backup
            self._repository.idempotency[key] = (fingerprint, backup.backup_id)
            self._audit(context, "backup.verified", backup.backup_id)
            return self._backup_view(backup)

    def list_backups(self, context: RecoveryContext) -> tuple[BackupView, ...]:
        self._require_admin(context)
        with self._repository.lock:
            return tuple(
                self._backup_view(backup)
                for backup in sorted(
                    self._repository.backups.values(), key=lambda item: item.created_at,
                    reverse=True,
                )
                if backup.context.tenant_id == context.tenant_id
                and backup.context.workspace_id == context.workspace_id
            )

    def get_backup(self, context: RecoveryContext, backup_id: str) -> BackupView:
        self._require_admin(context)
        with self._repository.lock:
            return self._backup_view(self._backup(context, backup_id))

    def locate_backup_workspace(self, tenant_id: str, backup_id: str) -> str:
        with self._repository.lock:
            backup = self._repository.backups.get(backup_id)
            if backup is None or backup.context.tenant_id != tenant_id:
                raise RecoveryError("BACKUP_UNAVAILABLE", 404)
            return backup.context.workspace_id

    def locate_restore_workspace(self, tenant_id: str, request_id: str) -> str:
        with self._repository.lock:
            request = self._repository.restores.get(request_id)
            if request is None or request.context.tenant_id != tenant_id:
                raise RecoveryError("RESTORE_REQUEST_UNAVAILABLE", 404)
            return request.context.workspace_id

    def backup_due(self, context: RecoveryContext) -> bool:
        with self._repository.lock:
            ready = [
                backup for backup in self._repository.backups.values()
                if backup.context.tenant_id == context.tenant_id
                and backup.context.workspace_id == context.workspace_id
                and backup.state == "ready" and backup.verified_at is not None
            ]
            return not ready or self._clock() - max(
                backup.verified_at for backup in ready if backup.verified_at is not None
            ) >= self._rpo

    def set_current_retention(
        self,
        context: RecoveryContext,
        *,
        purged: set[str],
        held: set[str],
        tombstoned: set[str],
    ) -> None:
        for value in purged | held | tombstoned:
            _safe(value)
        with self._repository.lock:
            self._repository.retention[(context.tenant_id, context.workspace_id)] = {
                "purged": set(purged), "held": set(held), "tombstoned": set(tombstoned),
            }

    def _preview(
        self, context: RecoveryContext, backup: _Backup, destination: RestoreDestination,
        *, version: int,
    ) -> RestorePreviewView:
        if any(
            not value.startswith(self._fixture_prefix)
            for value in (
                destination.tenant_id, destination.workspace_id,
                destination.database_id, destination.bucket_id,
            )
        ):
            raise RecoveryError("FIXTURE_ONLY_RESTORE_REQUIRED", 403)
        if (
            destination.tenant_id == backup.context.tenant_id
            or destination.workspace_id == backup.context.workspace_id
        ):
            raise RecoveryError("IN_PLACE_RESTORE_FORBIDDEN", 403)
        retention = self._repository.retention.get(
            (context.tenant_id, context.workspace_id),
            {"purged": set(), "held": set(), "tombstoned": set()},
        )
        reasons: list[tuple[str, str]] = []
        included: list[str] = []
        for item in backup.objects:
            reason = next((
                state for state in ("purged", "held", "tombstoned")
                if item.object_id in retention[state]
            ), None)
            if reason is None:
                included.append(item.object_id)
            else:
                reasons.append((item.object_id, reason))
        return RestorePreviewView(
            version, tuple(included), tuple(item[0] for item in reasons), tuple(reasons),
            destination, self._clock(),
        )

    def create_restore_preview(
        self,
        context: RecoveryContext,
        backup_id: str,
        *,
        destination: RestoreDestination,
        idempotency_key: str,
        step_up_verified: bool,
    ) -> RestoreRequestView:
        self._require_step_up(context, step_up_verified)
        _safe(idempotency_key)
        fingerprint = _fingerprint((backup_id, destination, context.policy_version))
        key = (context.actor_id, "restore-preview", idempotency_key)
        with self._repository.lock:
            backup = self._backup(context, backup_id)
            if backup.state != "ready":
                raise RecoveryError("BACKUP_NOT_READY", 409)
            replay = self._replay(key, fingerprint)
            if replay is not None:
                return self._restore_view(self._restore_request(context, replay))
            preview = self._preview(context, backup, destination, version=1)
            request = _RestoreRequest(self._id("restore"), context, backup_id, preview)
            request.state = "preview_ready"
            request.transitions.append("preview_ready")
            request.version += 1
            self._repository.restores[request.request_id] = request
            self._repository.idempotency[key] = (fingerprint, request.request_id)
            self._audit(context, "restore.previewed", request.request_id)
            return self._restore_view(request)

    def get_restore_request(
        self, context: RecoveryContext, request_id: str
    ) -> RestoreRequestView:
        self._require_admin(context)
        with self._repository.lock:
            return self._restore_view(self._restore_request(context, request_id))

    def execute_restore(
        self,
        context: RecoveryContext,
        request_id: str,
        *,
        expected_version: int,
        preview_version: int,
        idempotency_key: str,
        step_up_verified: bool,
    ) -> RestoreRequestView:
        self._require_step_up(context, step_up_verified)
        _safe(idempotency_key)
        fingerprint = _fingerprint((
            request_id, expected_version, preview_version, context.policy_version,
        ))
        key = (context.actor_id, "restore-execute", idempotency_key)
        with self._repository.lock:
            request = self._restore_request(context, request_id)
            replay = self._replay(key, fingerprint)
            if replay is not None:
                return self._restore_view(request)
            if request.version != expected_version or request.preview.version != preview_version:
                raise RecoveryError("RESTORE_VERSION_CONFLICT", 409)
            if request.state != "preview_ready":
                raise RecoveryError("RESTORE_STATE_INVALID", 409)
            backup = self._backup(context, request.backup_id)
            current_preview = self._preview(
                context, backup, request.preview.destination,
                version=request.preview.version + 1,
            )
            request.preview = current_preview
            for state in ("authorized", "restoring"):
                request.state = state
                request.transitions.append(state)
            allowed = set(current_preview.included_object_ids)
            objects = tuple(item for item in backup.objects if item.object_id in allowed)
            if not self._restore.restore(current_preview.destination, objects):
                request.state = "failed"
                request.transitions.append("failed")
                request.version += 1
                raise RecoveryError("RESOURCE_UNAVAILABLE", 503, retryable=True)
            request.state = "verifying"
            request.transitions.append("verifying")
            request.verification_digest = _fingerprint((
                request.request_id, current_preview.destination, objects,
                self._repository.audit[-1]["event_hash"] if self._repository.audit else None,
            ))
            request.state = "completed"
            request.transitions.append("completed")
            request.version += 1
            self._repository.idempotency[key] = (fingerprint, request.request_id)
            self._audit(context, "restore.completed", request.request_id)
            return self._restore_view(request)

    def cancel_restore(
        self,
        context: RecoveryContext,
        request_id: str,
        *,
        expected_version: int,
        idempotency_key: str,
    ) -> RestoreRequestView:
        self._require_admin(context)
        _safe(idempotency_key)
        fingerprint = _fingerprint((request_id, expected_version))
        key = (context.actor_id, "restore-cancel", idempotency_key)
        with self._repository.lock:
            request = self._restore_request(context, request_id)
            replay = self._replay(key, fingerprint)
            if replay is not None:
                return self._restore_view(request)
            if request.version != expected_version:
                raise RecoveryError("RESTORE_VERSION_CONFLICT", 409)
            if request.state not in {"requested", "preview_ready", "authorized"}:
                raise RecoveryError("RESTORE_STATE_INVALID", 409)
            request.state = "cancelled"
            request.transitions.append("cancelled")
            request.version += 1
            self._repository.idempotency[key] = (fingerprint, request.request_id)
            self._audit(context, "restore.cancelled", request.request_id)
            return self._restore_view(request)
