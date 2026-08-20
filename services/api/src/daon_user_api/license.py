"""Server-authoritative product license verification and safe projection."""

from __future__ import annotations

import base64
import hashlib
import json
import re
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from threading import RLock
from typing import Callable, Mapping, Protocol


_SAFE_ID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:-]{0,127}$")
_SAFE_CODE = re.compile(r"^[a-z][a-z0-9_]{0,63}$")
_RESOURCE_CODES = frozenset({
    "users", "notebooks", "storage_bytes", "generation_runs",
    "source_versions", "studio_outputs",
})
_DIGEST_INFO_SHA256 = bytes.fromhex("3031300d060960864801650304020105000420")
_ENVELOPE_KEYS = frozenset({"schema_version", "key_id", "algorithm", "claims", "signature"})
_CLAIM_KEYS = frozenset({
    "schema_version", "license_id", "product", "edition", "organization_id",
    "issued_at", "expires_at", "features", "resource_limits",
})
_CREATION_ACTIONS = {
    "studio.generate": ("studio_generation", frozenset({"generation_runs", "studio_outputs"})),
    "source.create": ("citation", frozenset({"source_versions", "storage_bytes"})),
    "notebook.create": ("notebook_management", frozenset({"notebooks"})),
}


class LicenseError(RuntimeError):
    def __init__(self, code: str, status: int = 400) -> None:
        self.code, self.status = code, status
        super().__init__(code)


@dataclass(frozen=True, slots=True)
class LicenseContext:
    tenant_id: str
    workspace_id: str
    actor_id: str
    trace_id: str
    policy_version: str

    def __post_init__(self) -> None:
        if any(not isinstance(value, str) or _SAFE_ID.fullmatch(value) is None for value in (
            self.tenant_id, self.workspace_id, self.actor_id, self.trace_id, self.policy_version,
        )):
            raise LicenseError("LICENSE_CONTEXT_INVALID")


@dataclass(frozen=True, slots=True)
class RsaPublicKey:
    modulus: int
    exponent: int

    def __post_init__(self) -> None:
        if self.modulus.bit_length() < 2048 or self.exponent < 3 or self.exponent % 2 == 0:
            raise LicenseError("LICENSE_PUBLIC_KEY_INVALID", 503)


@dataclass(frozen=True, slots=True)
class VerifiedLicense:
    license_id: str
    product: str
    edition: str
    organization_id: str
    issued_at: datetime
    expires_at: datetime
    features: tuple[str, ...]
    resource_limits: tuple[tuple[str, int], ...]
    claims_digest: str
    key_id: str


class LicenseSignatureVerifier(Protocol):
    def verify(self, document: Mapping[str, object]) -> tuple[Mapping[str, object], str, str]: ...


class UnavailableLicenseVerifier:
    def verify(self, document: Mapping[str, object]) -> tuple[Mapping[str, object], str, str]:
        raise LicenseError("LICENSE_SIGNING_KEY_UNAVAILABLE", 503)


def load_rsa_public_keys(path) -> dict[str, RsaPublicKey]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        raise LicenseError("LICENSE_SIGNING_KEY_UNAVAILABLE", 503) from None
    if not isinstance(value, dict) or set(value) != {"schema_version", "keys"} or value["schema_version"] != 1 or not isinstance(value["keys"], list):
        raise LicenseError("LICENSE_PUBLIC_KEY_INVALID", 503)
    keys: dict[str, RsaPublicKey] = {}
    for item in value["keys"]:
        if not isinstance(item, dict) or set(item) != {"key_id", "algorithm", "n", "e"} or item.get("algorithm") != "RS256":
            raise LicenseError("LICENSE_PUBLIC_KEY_INVALID", 503)
        key_id = item.get("key_id")
        if not isinstance(key_id, str) or _SAFE_ID.fullmatch(key_id) is None or key_id in keys:
            raise LicenseError("LICENSE_PUBLIC_KEY_INVALID", 503)
        modulus = int.from_bytes(_decode_base64url(item.get("n")), "big")
        exponent = int.from_bytes(_decode_base64url(item.get("e")), "big")
        keys[key_id] = RsaPublicKey(modulus, exponent)
    if not keys:
        raise LicenseError("LICENSE_PUBLIC_KEY_INVALID", 503)
    return keys


class LicenseRepository(Protocol):
    def current(self, context: LicenseContext) -> VerifiedLicense | None: ...
    def replay(
        self, context: LicenseContext, *, idempotency_key: str, request_fingerprint: str,
    ) -> VerifiedLicense | None: ...
    def apply(
        self, context: LicenseContext, license_value: VerifiedLicense,
        *, idempotency_key: str, request_fingerprint: str,
    ) -> tuple[VerifiedLicense, bool]: ...


def _canonical_claims(claims: Mapping[str, object]) -> bytes:
    try:
        return json.dumps(claims, ensure_ascii=True, separators=(",", ":"), sort_keys=True).encode("utf-8")
    except (TypeError, ValueError):
        raise LicenseError("LICENSE_DOCUMENT_INVALID") from None


def _decode_base64url(value: object) -> bytes:
    if not isinstance(value, str) or not value or len(value) > 4096 or "=" in value:
        raise LicenseError("LICENSE_SIGNATURE_INVALID")
    try:
        return base64.b64decode(value + "=" * (-len(value) % 4), altchars=b"-_", validate=True)
    except (ValueError, base64.binascii.Error):
        raise LicenseError("LICENSE_SIGNATURE_INVALID") from None


class RsaSha256LicenseVerifier:
    """Minimal RS256 verifier over approved in-memory public JWK projections."""

    def __init__(self, keys: Mapping[str, RsaPublicKey]) -> None:
        self._keys = dict(keys)

    def verify(self, document: Mapping[str, object]) -> tuple[Mapping[str, object], str, str]:
        if not isinstance(document, Mapping) or set(document) != _ENVELOPE_KEYS:
            raise LicenseError("LICENSE_DOCUMENT_INVALID")
        if document.get("schema_version") != 1 or document.get("algorithm") != "RS256":
            raise LicenseError("LICENSE_SCHEMA_UNSUPPORTED")
        key_id = document.get("key_id")
        if not isinstance(key_id, str) or _SAFE_ID.fullmatch(key_id) is None or key_id not in self._keys:
            raise LicenseError("LICENSE_SIGNING_KEY_UNAVAILABLE", 503)
        claims = document.get("claims")
        if not isinstance(claims, Mapping):
            raise LicenseError("LICENSE_DOCUMENT_INVALID")
        payload = _canonical_claims(claims)
        signature = _decode_base64url(document.get("signature"))
        public_key = self._keys[key_id]
        width = (public_key.modulus.bit_length() + 7) // 8
        if len(signature) != width:
            raise LicenseError("LICENSE_SIGNATURE_INVALID")
        signature_integer = int.from_bytes(signature, "big")
        if signature_integer >= public_key.modulus:
            raise LicenseError("LICENSE_SIGNATURE_INVALID")
        encoded = pow(signature_integer, public_key.exponent, public_key.modulus).to_bytes(width, "big")
        digest_info = _DIGEST_INFO_SHA256 + hashlib.sha256(payload).digest()
        padding_length = width - len(digest_info) - 3
        expected = b"\x00\x01" + b"\xff" * padding_length + b"\x00" + digest_info
        if padding_length < 8 or encoded != expected:
            raise LicenseError("LICENSE_SIGNATURE_INVALID")
        return claims, hashlib.sha256(payload).hexdigest(), key_id


def _utc_timestamp(value: object) -> datetime:
    if not isinstance(value, str) or not value.endswith("Z"):
        raise LicenseError("LICENSE_DOCUMENT_INVALID")
    try:
        parsed = datetime.fromisoformat(value[:-1] + "+00:00")
    except ValueError:
        raise LicenseError("LICENSE_DOCUMENT_INVALID") from None
    if parsed.utcoffset() != timedelta(0):
        raise LicenseError("LICENSE_DOCUMENT_INVALID")
    return parsed.astimezone(timezone.utc)


def _iso(value: datetime) -> str:
    return value.astimezone(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")


def _safe_claim_text(value: object) -> str:
    if not isinstance(value, str) or _SAFE_ID.fullmatch(value) is None:
        raise LicenseError("LICENSE_DOCUMENT_INVALID")
    return value


class LicenseService:
    def __init__(
        self, repository: LicenseRepository, verifier: LicenseSignatureVerifier,
        *, product_code: str, clock: Callable[[], datetime],
        usage_reader: Callable[[LicenseContext], Mapping[str, int]],
    ) -> None:
        self._repository = repository
        self._verifier = verifier
        self._product_code = _safe_claim_text(product_code)
        self._clock = clock
        self._usage_reader = usage_reader

    def verify_document(self, context: LicenseContext, document: Mapping[str, object]) -> VerifiedLicense:
        claims, claims_digest, key_id = self._verifier.verify(document)
        if set(claims) != _CLAIM_KEYS or claims.get("schema_version") != 1:
            raise LicenseError("LICENSE_SCHEMA_UNSUPPORTED")
        product = _safe_claim_text(claims.get("product"))
        if product != self._product_code:
            raise LicenseError("LICENSE_PRODUCT_MISMATCH")
        organization_id = _safe_claim_text(claims.get("organization_id"))
        if organization_id != context.tenant_id:
            raise LicenseError("LICENSE_ORGANIZATION_MISMATCH", 403)
        issued_at = _utc_timestamp(claims.get("issued_at"))
        expires_at = _utc_timestamp(claims.get("expires_at"))
        now = self._clock().astimezone(timezone.utc)
        if issued_at > now + timedelta(minutes=5) or expires_at <= issued_at:
            raise LicenseError("LICENSE_PERIOD_INVALID")
        if expires_at <= now:
            raise LicenseError("LICENSE_EXPIRED", 409)
        features = claims.get("features")
        limits = claims.get("resource_limits")
        if (
            not isinstance(features, list) or not 1 <= len(features) <= 64
            or any(not isinstance(item, str) or _SAFE_CODE.fullmatch(item) is None for item in features)
            or features != sorted(set(features))
            or not isinstance(limits, Mapping) or not 1 <= len(limits) <= 64
        ):
            raise LicenseError("LICENSE_DOCUMENT_INVALID")
        normalized_limits: list[tuple[str, int]] = []
        for resource, limit in limits.items():
            if (
                not isinstance(resource, str) or resource not in _RESOURCE_CODES
                or not isinstance(limit, int) or isinstance(limit, bool) or not 1 <= limit <= 2**63 - 1
            ):
                raise LicenseError("LICENSE_DOCUMENT_INVALID")
            normalized_limits.append((resource, limit))
        if normalized_limits != sorted(normalized_limits):
            raise LicenseError("LICENSE_DOCUMENT_INVALID")
        license_id = _safe_claim_text(claims.get("license_id"))
        if len(license_id) < 5:
            raise LicenseError("LICENSE_DOCUMENT_INVALID")
        return VerifiedLicense(
            license_id, product,
            _safe_claim_text(claims.get("edition")), organization_id,
            issued_at, expires_at, tuple(features), tuple(normalized_limits), claims_digest, key_id,
        )

    def apply(
        self, context: LicenseContext, document: Mapping[str, object], idempotency_key: str,
    ) -> tuple[dict[str, object], bool]:
        idempotency_key = self._idempotency_key(idempotency_key)
        fingerprint = self._request_fingerprint(context, document)
        stored = self._repository.replay(
            context, idempotency_key=idempotency_key, request_fingerprint=fingerprint,
        )
        if stored is not None:
            return self._view(context, stored), True
        verified = self.verify_document(context, document)
        return self.apply_verified(
            context, verified, idempotency_key, request_fingerprint=fingerprint,
        )

    def apply_verified(
        self, context: LicenseContext, verified: VerifiedLicense, idempotency_key: str,
        *, request_fingerprint: str,
    ) -> tuple[dict[str, object], bool]:
        stored, replayed = self._repository.apply(
            context, verified, idempotency_key=idempotency_key,
            request_fingerprint=request_fingerprint,
        )
        return self._view(context, stored), replayed

    @staticmethod
    def _idempotency_key(value: str) -> str:
        if not isinstance(value, str) or not 16 <= len(value) <= 128 or _SAFE_ID.fullmatch(value) is None:
            raise LicenseError("IDEMPOTENCY_KEY_INVALID")
        return value

    @staticmethod
    def _request_fingerprint(
        context: LicenseContext, document: Mapping[str, object],
    ) -> str:
        if not isinstance(document, Mapping) or set(document) != _ENVELOPE_KEYS:
            raise LicenseError("LICENSE_DOCUMENT_INVALID")
        if not isinstance(document.get("claims"), Mapping):
            raise LicenseError("LICENSE_DOCUMENT_INVALID")
        try:
            canonical = json.dumps(
                {
                    "tenant_id": context.tenant_id,
                    "workspace_id": context.workspace_id,
                    "document": document,
                },
                ensure_ascii=True, separators=(",", ":"), sort_keys=True,
            ).encode("utf-8")
        except (TypeError, ValueError):
            raise LicenseError("LICENSE_DOCUMENT_INVALID") from None
        return hashlib.sha256(canonical).hexdigest()

    def replay(
        self, context: LicenseContext, document: Mapping[str, object], idempotency_key: str,
    ) -> tuple[dict[str, object], bool] | None:
        key = self._idempotency_key(idempotency_key)
        stored = self._repository.replay(
            context, idempotency_key=key,
            request_fingerprint=self._request_fingerprint(context, document),
        )
        return None if stored is None else (self._view(context, stored), True)

    def get(self, context: LicenseContext) -> dict[str, object]:
        stored = self._repository.current(context)
        if stored is None:
            return {
                "product": self._product_code, "edition": None, "license_id_hint": None,
                "issued_at": None, "expires_at": None, "status": "not_configured",
                "features": [], "resources": [],
                "warning": {"code": "LICENSE_NOT_CONFIGURED", "action": "조직 관리자에게 라이선스 적용을 요청하세요."},
                "creation_allowed": False, "existing_read_allowed": True, "existing_export_allowed": True,
            }
        return self._view(context, stored)

    def _view(self, context: LicenseContext, stored: VerifiedLicense) -> dict[str, object]:
        now = self._clock().astimezone(timezone.utc)
        usage = dict(self._usage_reader(context))
        resources: list[dict[str, object]] = []
        limit_reached = False
        for resource, limit in stored.resource_limits:
            used = usage.get(resource, 0)
            if not isinstance(used, int) or isinstance(used, bool) or used < 0:
                raise LicenseError("LICENSE_USAGE_UNAVAILABLE", 503)
            remaining = max(0, limit - used)
            reached = used >= limit
            limit_reached = limit_reached or reached
            resources.append({
                "resource": resource, "limit": limit, "used": used,
                "remaining": remaining, "status": "limit_reached" if reached else "available",
            })
        expired = stored.expires_at <= now
        expiring = not expired and stored.expires_at - now <= timedelta(days=30)
        if expired:
            status, warning = "expired", {"code": "LICENSE_EXPIRED", "action": "조직 관리자에게 라이선스 갱신을 요청하세요."}
        elif limit_reached:
            status, warning = "limit_reached", {"code": "LICENSE_RESOURCE_LIMIT_REACHED", "action": "사용량을 확인하거나 조직 관리자에게 한도 변경을 요청하세요."}
        elif expiring:
            status, warning = "expiring_soon", {"code": "LICENSE_EXPIRES_WITHIN_30_DAYS", "action": "조직 관리자에게 라이선스 갱신을 요청하세요."}
        else:
            status, warning = "active", None
        return {
            "product": stored.product, "edition": stored.edition,
            "license_id_hint": "…" + stored.license_id[-5:],
            "issued_at": _iso(stored.issued_at), "expires_at": _iso(stored.expires_at),
            "status": status, "features": list(stored.features), "resources": resources,
            "warning": warning, "creation_allowed": not expired and not limit_reached,
            "existing_read_allowed": True, "existing_export_allowed": True,
        }

    def require_new_generation(self, context: LicenseContext) -> None:
        self.require_creation(
            context, "studio.generate", {"generation_runs": 1, "studio_outputs": 1},
        )

    def require_creation(
        self, context: LicenseContext, action: str, increments: Mapping[str, int],
    ) -> None:
        requirement = _CREATION_ACTIONS.get(action)
        if requirement is None or not isinstance(increments, Mapping) or not increments:
            raise LicenseError("LICENSE_CREATION_ACTION_INVALID")
        feature, allowed_resources = requirement
        if any(
            resource not in allowed_resources
            or not isinstance(amount, int) or isinstance(amount, bool) or amount < 1
            for resource, amount in increments.items()
        ):
            raise LicenseError("LICENSE_CREATION_ACTION_INVALID")
        stored = self._repository.current(context)
        if stored is None:
            raise LicenseError("LICENSE_NOT_CONFIGURED", 409)
        if stored.expires_at <= self._clock().astimezone(timezone.utc):
            raise LicenseError("LICENSE_EXPIRED", 409)
        if feature not in stored.features:
            raise LicenseError("LICENSE_FEATURE_NOT_ALLOWED", 409)
        usage = dict(self._usage_reader(context))
        limits = dict(stored.resource_limits)
        for resource, amount in increments.items():
            if resource not in limits:
                continue
            used = usage.get(resource, 0)
            if not isinstance(used, int) or isinstance(used, bool) or used < 0:
                raise LicenseError("LICENSE_USAGE_UNAVAILABLE", 503)
            if used + amount > limits[resource]:
                raise LicenseError("LICENSE_RESOURCE_LIMIT_REACHED", 409)


class ReferenceLicenseRepository:
    """Non-production adapter for isolated tests."""

    def __init__(self) -> None:
        self._lock = RLock()
        self._values: dict[str, list[VerifiedLicense]] = {}
        self._idempotency: dict[tuple[str, str, str], tuple[str, VerifiedLicense]] = {}

    def current(self, context: LicenseContext) -> VerifiedLicense | None:
        with self._lock:
            values = self._values.get(context.tenant_id, [])
            return None if not values else values[-1]

    def apply(
        self, context: LicenseContext, license_value: VerifiedLicense,
        *, idempotency_key: str, request_fingerprint: str,
    ) -> tuple[VerifiedLicense, bool]:
        key = (context.tenant_id, context.actor_id, idempotency_key)
        with self._lock:
            replay = self._idempotency.get(key)
            if replay is not None:
                if replay[0] != request_fingerprint:
                    raise LicenseError("IDEMPOTENCY_KEY_REUSED", 409)
                return replay[1], True
            self._values.setdefault(context.tenant_id, []).append(license_value)
            self._idempotency[key] = (request_fingerprint, license_value)
            return license_value, False

    def replay(
        self, context: LicenseContext, *, idempotency_key: str, request_fingerprint: str,
    ) -> VerifiedLicense | None:
        key = (context.tenant_id, context.actor_id, idempotency_key)
        with self._lock:
            replay = self._idempotency.get(key)
            if replay is None:
                return None
            if replay[0] != request_fingerprint:
                raise LicenseError("IDEMPOTENCY_KEY_REUSED", 409)
            return replay[1]

    def count(self, context: LicenseContext) -> int:
        with self._lock:
            return len(self._values.get(context.tenant_id, []))
