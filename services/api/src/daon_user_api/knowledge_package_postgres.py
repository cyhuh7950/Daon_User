"""PostgreSQL adapter for approved Knowledge Package offline-copy grants."""

from __future__ import annotations

import hashlib
import json
import secrets
from datetime import datetime
from typing import Callable, Protocol, cast

from .cloud_storage import CloudAccessContext, CloudDatabaseError, PostgresCloudStore
from .knowledge_package import (
    KnowledgePackageContext,
    KnowledgePackageError,
    KnowledgeQuestionSource,
    KnowledgePackageView,
    OfflineKnowledgeCopyGrant,
)


class KnowledgePackageContentPort(Protocol):
    def read_package(self, context: KnowledgePackageContext, package: KnowledgePackageView) -> bytes:
        raise NotImplementedError


class PostgresKnowledgePackageService:
    def __init__(
        self,
        cloud_store: PostgresCloudStore,
        content_port: KnowledgePackageContentPort,
        *,
        clock: Callable[[], datetime],
    ) -> None:
        self._cloud_store = cloud_store
        self._content_port = content_port
        self._clock = clock

    def _transaction(self, context: KnowledgePackageContext, capability: str):
        return self._cloud_store._transaction(CloudAccessContext(
            context.tenant_id, context.workspace_id, context.actor_id, capability,
        ))

    @staticmethod
    def _view(row: tuple[object, ...]) -> KnowledgePackageView:
        return KnowledgePackageView(
            package_id=str(row[0]), producer=str(row[1]), producer_version=str(row[2]),
            knowledge_registration_id=str(row[3]), output_version_id=str(row[4]),
            authority=str(row[5]), registration_state=str(row[6]),
            review_state=str(row[7]), digest_sha256=str(row[8]),
            byte_size=int(cast(int, row[9])), content_type=str(row[10]),
            effective_at=cast(datetime, row[11]), expires_at=cast(datetime, row[12]),
        )

    def list_packages(self, context: KnowledgePackageContext) -> tuple[KnowledgePackageView, ...]:
        try:
            with self._transaction(context, "sync.read") as connection:
                rows = connection.execute("""
                    SELECT kr.canonical_json->>'package_id',
                           kr.canonical_json->>'producer',
                           kr.canonical_json->>'producer_version',
                           kr.record_id, ov.record_id,
                           kr.canonical_json->>'authority',
                           kr.state, ov.state,
                           kr.canonical_json->>'package_digest',
                           (kr.canonical_json->>'byte_size')::bigint,
                           kr.canonical_json->>'content_type',
                           (kr.canonical_json->>'effective_at')::timestamptz,
                           (kr.canonical_json->>'expires_at')::timestamptz
                      FROM knowledge_registrations kr
                      JOIN output_versions ov
                        ON ov.tenant_id=kr.tenant_id
                       AND ov.workspace_id=kr.workspace_id
                       AND ov.record_id=kr.output_version_id
                     WHERE kr.state='registered'
                       AND ov.state='approved'
                       AND kr.canonical_json->>'authority'='approved'
                       AND kr.canonical_json->>'producer' IN ('daon2','daon2_5','daon3')
                       AND (kr.canonical_json->>'effective_at')::timestamptz <= %s
                       AND (kr.canonical_json->>'expires_at')::timestamptz > %s
                     ORDER BY kr.canonical_json->>'package_id'
                """, (self._clock(), self._clock())).fetchall()
        except CloudDatabaseError as error:
            raise KnowledgePackageError(error.code, 503) from None
        return tuple(self._view(row) for row in rows)

    def resolve_question_sources(
        self, context: KnowledgePackageContext, package_ids: tuple[str, ...],
    ) -> tuple[KnowledgeQuestionSource, ...]:
        if not package_ids or len(package_ids) > 8 or len(set(package_ids)) != len(package_ids):
            raise KnowledgePackageError("QUESTION_CONTEXT_INVALID", 400)
        packages = {item.package_id: item for item in self.list_packages(context)}
        if any(package_id not in packages for package_id in package_ids):
            raise KnowledgePackageError("KNOWLEDGE_PACKAGE_UNAVAILABLE", 404)
        try:
            with self._transaction(context, "sync.read") as connection:
                rows = connection.execute("""
                    SELECT kr.canonical_json->>'package_id',sv.source_id,
                           kr.registered_source_version_id,
                           kr.canonical_json->>'package_digest'
                      FROM knowledge_registrations kr
                      JOIN source_versions sv
                        ON sv.tenant_id=kr.tenant_id
                       AND sv.workspace_id=kr.workspace_id
                       AND sv.record_id=kr.registered_source_version_id
                     WHERE kr.canonical_json->>'package_id'=ANY(%s)
                       AND kr.state='registered'
                """, (list(package_ids),)).fetchall()
        except CloudDatabaseError as error:
            raise KnowledgePackageError(error.code, 503) from None
        by_package = {str(row[0]): row for row in rows}
        if any(package_id not in by_package for package_id in package_ids):
            raise KnowledgePackageError("KNOWLEDGE_PACKAGE_UNAVAILABLE", 404)
        return tuple(KnowledgeQuestionSource(
            package_id, str(by_package[package_id][1]), str(by_package[package_id][2]),
            str(by_package[package_id][3]),
        ) for package_id in package_ids)

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
        if not approval_verified:
            raise KnowledgePackageError("STEP_UP_REQUIRED", 403)
        if device_id != context.device_id:
            raise KnowledgePackageError("CURRENT_ACCESS_DENIED", 403)
        fingerprint = hashlib.sha256(json.dumps(
            [package_id, device_id, step_up_authorization_id], separators=(",", ":")
        ).encode()).hexdigest()
        packages = {item.package_id: item for item in self.list_packages(context)}
        package = packages.get(package_id)
        if package is None:
            raise KnowledgePackageError("KNOWLEDGE_PACKAGE_UNAVAILABLE", 404)
        content = self._content_port.read_package(context, package)
        if len(content) != package.byte_size or hashlib.sha256(content).hexdigest() != package.digest_sha256:
            raise KnowledgePackageError("KNOWLEDGE_PACKAGE_DIGEST_MISMATCH")
        try:
            with self._transaction(context, "sync.write") as connection:
                replay = connection.execute(
                    "SELECT copy_id,request_fingerprint,state,expires_at,package_digest "
                    "FROM offline_knowledge_copy_grants WHERE actor_id=%s AND idempotency_key=%s",
                    (context.actor_id, idempotency_key),
                ).fetchone()
                if replay is not None:
                    if str(replay[1]) != fingerprint:
                        raise KnowledgePackageError("IDEMPOTENCY_KEY_REUSED")
                    return OfflineKnowledgeCopyGrant(
                        str(replay[0]), package_id, device_id, str(replay[2]),
                        str(replay[4]), cast(datetime, replay[3]),
                    )
                copy_id = "offline-copy-" + secrets.token_hex(12)
                now = self._clock()
                connection.execute("""
                    INSERT INTO offline_knowledge_copy_grants (
                      tenant_id,workspace_id,copy_id,package_id,device_id,actor_id,
                      knowledge_registration_id,output_version_id,producer,producer_version,
                      package_digest,byte_size,content_type,step_up_authorization_digest,
                      state,idempotency_key,request_fingerprint,approved_at,expires_at
                    ) VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,
                              'approved',%s,%s,%s,%s)
                """, (
                    context.tenant_id, context.workspace_id, copy_id, package_id, device_id,
                    context.actor_id, package.knowledge_registration_id, package.output_version_id,
                    package.producer, package.producer_version, package.digest_sha256,
                    package.byte_size, package.content_type,
                    hashlib.sha256(step_up_authorization_id.encode()).hexdigest(),
                    idempotency_key, fingerprint, now, package.expires_at,
                ))
                return OfflineKnowledgeCopyGrant(
                    copy_id, package_id, device_id, "approved",
                    package.digest_sha256, package.expires_at,
                )
        except KnowledgePackageError:
            raise
        except CloudDatabaseError as error:
            raise KnowledgePackageError(error.code, 503) from None

    def read_content(self, context: KnowledgePackageContext, *, copy_id: str) -> bytes:
        try:
            with self._transaction(context, "sync.read") as connection:
                row = connection.execute("""
                    SELECT package_id,producer,producer_version,knowledge_registration_id,
                           output_version_id,'approved','registered','approved',package_digest,
                           byte_size,content_type,approved_at,expires_at,state
                      FROM offline_knowledge_copy_grants
                     WHERE copy_id=%s AND device_id=%s
                """, (copy_id, context.device_id)).fetchone()
        except CloudDatabaseError as error:
            raise KnowledgePackageError(error.code, 503) from None
        if row is None or str(row[13]) != "approved" or cast(datetime, row[12]) <= self._clock():
            raise KnowledgePackageError("OFFLINE_KNOWLEDGE_COPY_UNAVAILABLE", 404)
        package = self._view(row[:13])
        content = self._content_port.read_package(context, package)
        if len(content) != package.byte_size or hashlib.sha256(content).hexdigest() != package.digest_sha256:
            raise KnowledgePackageError("KNOWLEDGE_PACKAGE_DIGEST_MISMATCH")
        return content
