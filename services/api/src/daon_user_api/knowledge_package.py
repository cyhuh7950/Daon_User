"""Approved Daon Knowledge Package projection and offline-copy grant contract."""

from __future__ import annotations

import hashlib
import json
import re
import secrets
import threading
from dataclasses import dataclass
from datetime import datetime
from typing import Callable


_SAFE_ID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:-]{0,127}$")
_DIGEST = re.compile(r"^[0-9a-f]{64}$")
_PRODUCERS = frozenset({"daon2", "daon2_5", "daon3"})


class KnowledgePackageError(RuntimeError):
    def __init__(self, code: str, status: int = 409) -> None:
        self.code = code
        self.status = status
        super().__init__(code)


def _require_safe_id(value: str, code: str = "KNOWLEDGE_PACKAGE_INVALID") -> None:
    if not isinstance(value, str) or not _SAFE_ID.fullmatch(value):
        raise KnowledgePackageError(code, 400)


@dataclass(frozen=True, slots=True)
class KnowledgePackageContext:
    tenant_id: str
    workspace_id: str
    actor_id: str
    trace_id: str
    policy_version: str
    device_id: str

    def __post_init__(self) -> None:
        for value in (
            self.tenant_id, self.workspace_id, self.actor_id, self.trace_id,
            self.policy_version, self.device_id,
        ):
            _require_safe_id(value, "KNOWLEDGE_PACKAGE_CONTEXT_INVALID")


@dataclass(frozen=True, slots=True)
class KnowledgePackageRecord:
    package_id: str
    tenant_id: str
    workspace_id: str
    producer: str
    producer_version: str
    knowledge_registration_id: str
    output_version_id: str
    authority: str
    review_state: str
    registration_state: str
    digest_sha256: str
    byte_size: int
    content_type: str
    content: bytes
    effective_at: datetime
    expires_at: datetime
    registered_source_id: str | None = None
    registered_source_version_id: str | None = None


@dataclass(frozen=True, slots=True)
class KnowledgeQuestionSource:
    package_id: str
    source_id: str
    source_version_id: str
    digest_sha256: str


@dataclass(frozen=True, slots=True)
class KnowledgePackageView:
    package_id: str
    producer: str
    producer_version: str
    knowledge_registration_id: str
    output_version_id: str
    authority: str
    registration_state: str
    review_state: str
    digest_sha256: str
    byte_size: int
    content_type: str
    effective_at: datetime
    expires_at: datetime


@dataclass(frozen=True, slots=True)
class OfflineKnowledgeCopyGrant:
    copy_id: str
    package_id: str
    device_id: str
    state: str
    digest_sha256: str
    expires_at: datetime


class ReferenceKnowledgePackageRepository:
    def __init__(self) -> None:
        self._lock = threading.RLock()
        self._packages: dict[str, KnowledgePackageRecord] = {}
        self._grants: dict[str, tuple[KnowledgePackageContext, OfflineKnowledgeCopyGrant]] = {}
        self._idempotency: dict[tuple[str, str, str, str], tuple[str, str]] = {}

    @property
    def grant_count(self) -> int:
        return len(self._grants)

    def add(self, record: KnowledgePackageRecord) -> None:
        with self._lock:
            self._packages[record.package_id] = record


class KnowledgePackageService:
    def __init__(self, repository: ReferenceKnowledgePackageRepository, *, clock: Callable[[], datetime]) -> None:
        self._repository = repository
        self._clock = clock

    def _eligible(self, context: KnowledgePackageContext, record: KnowledgePackageRecord) -> bool:
        now = self._clock()
        return (
            record.tenant_id == context.tenant_id
            and record.workspace_id == context.workspace_id
            and record.producer in _PRODUCERS
            and record.registration_state == "registered"
            and record.review_state == "approved"
            and record.authority == "approved"
            and record.effective_at <= now < record.expires_at
            and 0 < record.byte_size <= 8 * 1024 * 1024
            and record.byte_size == len(record.content)
            and _DIGEST.fullmatch(record.digest_sha256) is not None
            and hashlib.sha256(record.content).hexdigest() == record.digest_sha256
        )

    def list_packages(self, context: KnowledgePackageContext) -> tuple[KnowledgePackageView, ...]:
        with self._repository._lock:
            return tuple(
                KnowledgePackageView(
                    package_id=record.package_id,
                    producer=record.producer,
                    producer_version=record.producer_version,
                    knowledge_registration_id=record.knowledge_registration_id,
                    output_version_id=record.output_version_id,
                    authority=record.authority,
                    registration_state=record.registration_state,
                    review_state=record.review_state,
                    digest_sha256=record.digest_sha256,
                    byte_size=record.byte_size,
                    content_type=record.content_type,
                    effective_at=record.effective_at,
                    expires_at=record.expires_at,
                )
                for record in sorted(self._repository._packages.values(), key=lambda item: item.package_id)
                if self._eligible(context, record)
            )

    def resolve_question_sources(
        self, context: KnowledgePackageContext, package_ids: tuple[str, ...],
    ) -> tuple[KnowledgeQuestionSource, ...]:
        if not package_ids or len(package_ids) > 8 or len(set(package_ids)) != len(package_ids):
            raise KnowledgePackageError("QUESTION_CONTEXT_INVALID", 400)
        with self._repository._lock:
            resolved: list[KnowledgeQuestionSource] = []
            for package_id in package_ids:
                _require_safe_id(package_id, "QUESTION_CONTEXT_INVALID")
                record = self._repository._packages.get(package_id)
                if (
                    record is None or not self._eligible(context, record)
                    or record.registered_source_id is None
                    or record.registered_source_version_id is None
                ):
                    raise KnowledgePackageError("KNOWLEDGE_PACKAGE_UNAVAILABLE", 404)
                resolved.append(KnowledgeQuestionSource(
                    package_id, record.registered_source_id,
                    record.registered_source_version_id, record.digest_sha256,
                ))
            return tuple(resolved)

    def create_offline_copy(
        self,
        context: KnowledgePackageContext,
        *,
        package_id: str,
        device_id: str,
        step_up_authorization_id: str,
        idempotency_key: str,
        approval_verified: bool,
    ) -> OfflineKnowledgeCopyGrant:
        for value in (package_id, device_id, idempotency_key):
            _require_safe_id(value)
        if (
            not isinstance(step_up_authorization_id, str)
            or not 1 <= len(step_up_authorization_id) <= 512
            or step_up_authorization_id != step_up_authorization_id.strip()
        ):
            raise KnowledgePackageError("KNOWLEDGE_PACKAGE_INVALID", 400)
        fingerprint = hashlib.sha256(json.dumps(
            [package_id, device_id, step_up_authorization_id], separators=(",", ":")
        ).encode()).hexdigest()
        key = (context.tenant_id, context.workspace_id, context.actor_id, idempotency_key)
        with self._repository._lock:
            replay = self._repository._idempotency.get(key)
            if replay is not None:
                if replay[0] != fingerprint:
                    raise KnowledgePackageError("IDEMPOTENCY_KEY_REUSED")
                return self._repository._grants[replay[1]][1]
            if device_id != context.device_id:
                raise KnowledgePackageError("CURRENT_ACCESS_DENIED", 403)
            if not approval_verified:
                raise KnowledgePackageError("STEP_UP_REQUIRED", 403)
            record = self._repository._packages.get(package_id)
            if record is None or not self._eligible(context, record):
                raise KnowledgePackageError("KNOWLEDGE_PACKAGE_UNAVAILABLE", 404)
            copy_id = "offline-copy-" + secrets.token_hex(12)
            grant = OfflineKnowledgeCopyGrant(
                copy_id, package_id, device_id, "approved",
                record.digest_sha256, record.expires_at,
            )
            self._repository._grants[copy_id] = (context, grant)
            self._repository._idempotency[key] = (fingerprint, copy_id)
            return grant

    def read_content(self, context: KnowledgePackageContext, *, copy_id: str) -> bytes:
        _require_safe_id(copy_id)
        with self._repository._lock:
            stored = self._repository._grants.get(copy_id)
            if stored is None:
                raise KnowledgePackageError("OFFLINE_KNOWLEDGE_COPY_UNAVAILABLE", 404)
            owner, grant = stored
            if (
                owner.tenant_id != context.tenant_id
                or owner.workspace_id != context.workspace_id
                or grant.device_id != context.device_id
                or grant.state != "approved"
                or self._clock() >= grant.expires_at
            ):
                raise KnowledgePackageError("OFFLINE_KNOWLEDGE_COPY_UNAVAILABLE", 404)
            record = self._repository._packages[grant.package_id]
            if hashlib.sha256(record.content).hexdigest() != grant.digest_sha256:
                raise KnowledgePackageError("KNOWLEDGE_PACKAGE_DIGEST_MISMATCH", 409)
            return record.content
