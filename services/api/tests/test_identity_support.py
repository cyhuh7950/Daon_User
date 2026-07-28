from __future__ import annotations

import base64
import hashlib
import sys
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path

SRC = Path(__file__).resolve().parents[1] / "src"
sys.path.insert(0, str(SRC))

from daon_user_api.identity import (  # noqa: E402
    ClientKind,
    DevicePlatform,
    IdentityService,
    OidcClientPolicy,
    SqliteIdentityRepository,
    VerifiedOidcClaims,
)
from daon_user_api.audit import AuditEventStore  # noqa: E402


UTC_1 = datetime(2026, 7, 29, 0, 0, tzinfo=timezone.utc)
POLICY_VERSION = "identity-policy-v1"
TRACE_ID = "trace-identity-001"


@dataclass
class MutableClock:
    current: datetime = UTC_1

    def __call__(self) -> datetime:
        return self.current

    def advance(self, **kwargs: int) -> None:
        self.current += timedelta(**kwargs)


class FakeVerifiedOidcProvider:
    """Deterministic protocol adapter; never evidence of an external provider login."""

    def __init__(
        self,
        *,
        subject: str = "subject-001",
        authorization_code: str = "authorization-code-001",
        verification_complete: bool = True,
        override_issuer: str | None = None,
        override_audience: str | None = None,
        override_nonce: str | None = None,
        expired: bool = False,
    ) -> None:
        self.subject = subject
        self.authorization_code = authorization_code
        self.verification_complete = verification_complete
        self.override_issuer = override_issuer
        self.override_audience = override_audience
        self.override_nonce = override_nonce
        self.expired = expired

    def exchange_verified(
        self,
        *,
        authorization_code: str,
        code_verifier: str,
        expected_code_challenge: str,
        expected_issuer: str,
        expected_audience: str,
        expected_nonce_digest: str,
        now: datetime,
    ) -> VerifiedOidcClaims:
        if authorization_code != self.authorization_code:
            raise ValueError("provider raw failure must be suppressed")
        computed = base64.urlsafe_b64encode(
            hashlib.sha256(code_verifier.encode("ascii")).digest()
        ).decode("ascii").rstrip("=")
        if computed != expected_code_challenge:
            raise ValueError("pkce mismatch")
        nonce = self.override_nonce or self.expected_nonce
        if hashlib.sha256(nonce.encode("utf-8")).hexdigest() != expected_nonce_digest:
            raise ValueError("nonce mismatch")
        return VerifiedOidcClaims(
            verification_complete=self.verification_complete,
            issuer=self.override_issuer or expected_issuer,
            audience=self.override_audience or expected_audience,
            subject=self.subject,
            nonce=nonce,
            expires_at=now - timedelta(seconds=1) if self.expired else now + timedelta(minutes=5),
        )

    expected_nonce: str = ""


class FailingAuditStore:
    def append(self, _draft: object) -> object:
        raise RuntimeError("raw provider credential must never escape")


def policy(client_kind: ClientKind = ClientKind.NATIVE) -> OidcClientPolicy:
    return OidcClientPolicy(
        issuer="https://login.example.com",
        client_id="daon-native" if client_kind is ClientKind.NATIVE else "daon-web",
        audience="daon-user-api",
        redirect_uris=frozenset(
            {"com.sinsan.daon:/oidc/callback"}
            if client_kind is ClientKind.NATIVE
            else {"https://app.example.com/auth/callback"}
        ),
        client_kind=client_kind,
        tenant_id="tenant-001",
    )


def create_service(
    db_path: Path,
    *,
    clock: MutableClock | None = None,
    audit_store: object | None = None,
    policies: tuple[OidcClientPolicy, ...] | None = None,
) -> tuple[IdentityService, SqliteIdentityRepository, AuditEventStore | object, MutableClock]:
    actual_clock = clock or MutableClock()
    repository = SqliteIdentityRepository(db_path)
    actual_audit = audit_store or AuditEventStore()
    service = IdentityService(
        repository=repository,
        audit_store=actual_audit,
        oidc_policies=policies or (policy(), policy(ClientKind.WEB)),
        clock=actual_clock,
    )
    return service, repository, actual_audit, actual_clock


def native_login(
    service: IdentityService,
    provider: FakeVerifiedOidcProvider | None = None,
):
    actual_provider = provider or FakeVerifiedOidcProvider()
    start = service.begin_oidc_login(
        issuer="https://login.example.com",
        client_id="daon-native",
        audience="daon-user-api",
        redirect_uri="com.sinsan.daon:/oidc/callback",
        client_kind=ClientKind.NATIVE,
        tenant_id="tenant-001",
        trace_id=TRACE_ID,
        policy_version=POLICY_VERSION,
    )
    actual_provider.expected_nonce = start.nonce
    return service.complete_oidc_login(
        state=start.state,
        authorization_code=actual_provider.authorization_code,
        code_verifier=start.code_verifier,
        client_id="daon-native",
        redirect_uri="com.sinsan.daon:/oidc/callback",
        provider=actual_provider,
        platform=DevicePlatform.ANDROID,
        trace_id=TRACE_ID,
        policy_version=POLICY_VERSION,
    )
