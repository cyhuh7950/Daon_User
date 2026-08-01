from __future__ import annotations

from dataclasses import dataclass, replace
from datetime import datetime, timedelta, timezone
import hashlib


class RelayAuthorizationError(ValueError):
    """Raised when a local-node certificate or relay policy check fails."""


@dataclass(frozen=True)
class DeviceIdentity:
    tenant_id: str
    device_id: str
    public_key_digest: str
    certificate_digest: str
    certificate_expires_at: datetime
    status: str
    certificate_generation: int


class LocalNodeRelay:
    """In-memory internal contract for paired local nodes and outbound relay."""

    def __init__(self, certificate_ttl: timedelta = timedelta(minutes=10)) -> None:
        if certificate_ttl <= timedelta(0):
            raise ValueError("certificate_ttl must be positive")
        self._certificate_ttl = certificate_ttl
        self._identities: dict[tuple[str, str], DeviceIdentity] = {}

    @staticmethod
    def _key(tenant_id: str, device_id: str) -> tuple[str, str]:
        if not tenant_id or not device_id:
            raise ValueError("tenant_id and device_id are required")
        return tenant_id, device_id

    @staticmethod
    def _digest(value: str) -> str:
        if not value:
            raise ValueError("key material is required")
        return "sha256:" + hashlib.sha256(value.encode("utf-8")).hexdigest()

    def _certificate(self, tenant_id: str, device_id: str, public_key_digest: str, generation: int) -> str:
        return self._digest(f"{tenant_id}:{device_id}:{public_key_digest}:{generation}")

    def pair(self, tenant_id: str, device_id: str, public_key: str) -> DeviceIdentity:
        key = self._key(tenant_id, device_id)
        public_key_digest = self._digest(public_key)
        current = self._identities.get(key)
        generation = 1 if current is None else current.certificate_generation + 1
        now = datetime.now(timezone.utc)
        identity = DeviceIdentity(
            tenant_id=tenant_id,
            device_id=device_id,
            public_key_digest=public_key_digest,
            certificate_digest=self._certificate(tenant_id, device_id, public_key_digest, generation),
            certificate_expires_at=now + self._certificate_ttl,
            status="online",
            certificate_generation=generation,
        )
        self._identities[key] = identity
        return identity

    def rotate_certificate(self, tenant_id: str, device_id: str, public_key: str) -> DeviceIdentity:
        key = self._key(tenant_id, device_id)
        current = self._identities.get(key)
        if current is None:
            raise RelayAuthorizationError("DEVICE_NOT_PAIRED")
        if current.status == "revoked":
            raise RelayAuthorizationError("DEVICE_REVOKED")
        return self.pair(tenant_id, device_id, public_key)

    def verify_certificate(self, tenant_id: str, device_id: str, certificate_digest: str) -> DeviceIdentity:
        identity = self._identities.get(self._key(tenant_id, device_id))
        if identity is None:
            raise RelayAuthorizationError("DEVICE_NOT_PAIRED")
        if identity.status == "revoked":
            raise RelayAuthorizationError("DEVICE_REVOKED")
        if identity.certificate_digest != certificate_digest:
            raise RelayAuthorizationError("CERTIFICATE_INVALID")
        if identity.certificate_expires_at <= datetime.now(timezone.utc):
            raise RelayAuthorizationError("CERTIFICATE_EXPIRED")
        return identity

    def authorize(
        self,
        tenant_id: str,
        device_id: str,
        certificate_digest: str,
        direction: str,
    ) -> bool:
        if direction != "outbound":
            raise RelayAuthorizationError("PUBLIC_INBOUND_FORBIDDEN")
        identity = self.verify_certificate(tenant_id, device_id, certificate_digest)
        if identity.status != "online":
            raise RelayAuthorizationError("DEVICE_NOT_ONLINE")
        return True

    def revoke(self, tenant_id: str, device_id: str) -> DeviceIdentity:
        key = self._key(tenant_id, device_id)
        identity = self._identities.get(key)
        if identity is None:
            raise RelayAuthorizationError("DEVICE_NOT_PAIRED")
        revoked = replace(identity, status="revoked", certificate_digest="")
        self._identities[key] = revoked
        return revoked
