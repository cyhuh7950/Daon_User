"""Identity, session, device and step-up domain core for Release 1 M4.

The module deliberately stops at the verified OIDC-provider protocol and HTTP/cookie
boundary.  It persists only digests of browser/native credentials and PKCE material.
"""

from __future__ import annotations

import base64
import hashlib
import os
import re
import secrets
import sqlite3
import smtplib
from email.message import EmailMessage
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from enum import Enum
from pathlib import Path
from threading import RLock
from typing import Callable, Iterator, Protocol

from argon2 import PasswordHasher
from argon2.exceptions import VerifyMismatchError

from .audit import ActorType, AuditEventDraft, AuditOutcome


SCHEMA_VERSION = 1
PASSWORD_MIN_LENGTH = 12
PASSWORD_HASHER = PasswordHasher()
OIDC_TRANSACTION_TTL = timedelta(minutes=5)
ACCESS_TTL = timedelta(hours=1)
REFRESH_TTL = timedelta(days=30)
DEFAULT_STEP_UP_TTL_SECONDS = 300
MAX_STEP_UP_TTL_SECONDS = 600
MINIMUM_STEP_UP_ACTION_GROUPS = frozenset(
    {
        "external_transfer",
        "data_area_move",
        "external_share_or_download",
        "final_approval_or_knowledge_registration",
        "organization_security_or_connector_policy_change",
        "device_session_or_sync_key_revoke",
        "permanent_delete_or_restore_rollback",
    }
)

_SAFE_ID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:-]{0,127}$")


class ClientKind(str, Enum):
    WEB = "web"
    NATIVE = "native"


class DevicePlatform(str, Enum):
    WEB = "web"
    WINDOWS = "windows"
    ANDROID = "android"
    IOS = "ios"


class IdentityError(RuntimeError):
    """Stable, value-free error safe for an API error envelope."""

    def __init__(self, code: str, http_status: int = 400) -> None:
        self.code = code
        self.http_status = http_status
        super().__init__(code)


@dataclass(frozen=True, slots=True)
class OidcClientPolicy:
    issuer: str
    client_id: str
    audience: str
    redirect_uris: frozenset[str]
    client_kind: ClientKind
    tenant_id: str


@dataclass(frozen=True, slots=True)
class VerifiedOidcClaims:
    verification_complete: bool
    issuer: str
    audience: str
    subject: str
    nonce: str
    expires_at: datetime


class VerifiedOidcProvider(Protocol):
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
    ) -> VerifiedOidcClaims: ...


@dataclass(frozen=True, slots=True)
class OidcLoginStart:
    transaction_id: str
    state: str
    nonce: str
    code_verifier: str
    code_challenge: str
    code_challenge_method: str
    expires_at: datetime


@dataclass(frozen=True, slots=True)
class SessionCredentials:
    access_token: str
    refresh_token: str | None
    user_id: str
    session_id: str
    device_id: str
    tenant_id: str
    client_kind: ClientKind
    delivery: str


@dataclass(frozen=True, slots=True)
class IdentityPrincipal:
    user_id: str
    session_id: str
    device_id: str
    tenant_id: str


@dataclass(frozen=True, slots=True)
class IdentitySessionView:
    principal: IdentityPrincipal
    client_kind: ClientKind
    expires_at: datetime


@dataclass(frozen=True, slots=True)
class StepUpGrant:
    authorization: str
    issued_at: datetime
    expires_at: datetime


@dataclass(frozen=True, slots=True)
class DeviceRevocationEvent:
    device_id: str
    sync_key_revoke_required: bool


@dataclass(frozen=True, slots=True)
class SessionRevocationEvent:
    session_id: str


class EmailSender(Protocol):
    def send(self, *, recipient: str, subject: str, body: str) -> None: ...


class SmtpEmailSender:
    def __init__(self, *, host: str | None, port: int, username: str | None,
                 password: str | None, sender: str | None, secure: bool) -> None:
        self.host, self.port = host, port
        self.username, self.password, self.sender, self.secure = username, password, sender, secure

    @classmethod
    def from_env(cls) -> "SmtpEmailSender":
        return cls(
            host=os.environ.get("DAON_SMTP_HOST"),
            port=int(os.environ.get("DAON_SMTP_PORT", "587")),
            username=os.environ.get("DAON_SMTP_USERNAME"),
            password=os.environ.get("DAON_SMTP_PASSWORD"),
            sender=os.environ.get("DAON_EMAIL_FROM"),
            secure=os.environ.get("DAON_SMTP_SECURE", "true").lower() == "true",
        )

    def send(self, *, recipient: str, subject: str, body: str) -> None:
        if not self.host or not self.sender:
            raise IdentityError("EMAIL_DELIVERY_UNAVAILABLE", 503)
        message = EmailMessage()
        message["From"], message["To"], message["Subject"] = self.sender, recipient, subject
        message.set_content(body)
        try:
            with smtplib.SMTP(self.host, self.port, timeout=10) as server:
                if self.secure:
                    server.starttls()
                if self.username:
                    server.login(self.username, self.password or "")
                server.send_message(message)
        except (OSError, smtplib.SMTPException) as error:
            raise IdentityError("EMAIL_DELIVERY_UNAVAILABLE", 503) from error


def _digest(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def _opaque() -> str:
    return secrets.token_urlsafe(48)


def _id(prefix: str) -> str:
    return f"{prefix}-{secrets.token_urlsafe(18)}"


def _iso(value: datetime) -> str:
    return value.astimezone(timezone.utc).isoformat(timespec="microseconds")


def _dt(value: str) -> datetime:
    return datetime.fromisoformat(value).astimezone(timezone.utc)


def _checked_text(value: object, *, opaque: bool = False) -> str:
    if not isinstance(value, str) or not value or value != value.strip() or len(value) > 512:
        raise IdentityError("INVALID_INPUT")
    if any(ord(character) < 32 for character in value):
        raise IdentityError("INVALID_INPUT")
    if not opaque and not _SAFE_ID.fullmatch(value):
        raise IdentityError("INVALID_INPUT")
    return value


def _checked_utc(value: datetime) -> datetime:
    if not isinstance(value, datetime) or value.tzinfo is None or value.utcoffset() != timedelta(0):
        raise IdentityError("INVALID_TIME")
    return value.astimezone(timezone.utc)


def _email(value: object) -> str:
    if not isinstance(value, str) or value != value.strip() or len(value) > 320:
        raise IdentityError("INVALID_INPUT")
    normalized = value.strip().lower()
    if not re.fullmatch(r"[^@\s]+@[^@\s]+\.[^@\s]+", normalized):
        raise IdentityError("INVALID_INPUT")
    return normalized


def _password(value: object) -> str:
    if not isinstance(value, str) or len(value) < PASSWORD_MIN_LENGTH or len(value) > 256:
        raise IdentityError("PASSWORD_POLICY_FAILED")
    if any(ord(character) < 32 for character in value):
        raise IdentityError("PASSWORD_POLICY_FAILED")
    return value


class SqliteIdentityRepository:
    """Injected-path, transactional SQLite adapter with restart-safe IAM state."""

    def __init__(self, path: str | Path) -> None:
        self._lock = RLock()
        self._closed = False
        self._path = Path(path)
        try:
            connection = self._connect()
            self._create_schema(connection)
            connection.close()
        except sqlite3.Error as error:
            raise IdentityError("PERSISTENCE_UNAVAILABLE", 503) from error

    def _connect(self) -> sqlite3.Connection:
        connection = sqlite3.connect(
            str(self._path), isolation_level=None, check_same_thread=False
        )
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA foreign_keys = ON")
        connection.execute("PRAGMA journal_mode = WAL")
        return connection

    def _create_schema(self, connection: sqlite3.Connection) -> None:
        schema = """
        CREATE TABLE IF NOT EXISTS tenants (
          tenant_id TEXT PRIMARY KEY
        );
        CREATE TABLE IF NOT EXISTS users (
          user_id TEXT PRIMARY KEY,
          issuer TEXT NOT NULL DEFAULT 'oidc',
          subject TEXT NOT NULL,
          login_id TEXT,
          email TEXT,
          password_digest TEXT,
          email_verified_at TEXT,
          state TEXT NOT NULL DEFAULT 'active',
          UNIQUE(issuer, subject)
        );
        CREATE TABLE IF NOT EXISTS email_verification_tokens (
          token_id TEXT PRIMARY KEY,
          user_id TEXT NOT NULL REFERENCES users(user_id),
          token_digest TEXT NOT NULL UNIQUE,
          expires_at TEXT NOT NULL,
          used_at TEXT,
          attempts INTEGER NOT NULL DEFAULT 0,
          created_at TEXT NOT NULL
        );
        CREATE TABLE IF NOT EXISTS password_reset_tokens (
          token_id TEXT PRIMARY KEY,
          user_id TEXT NOT NULL REFERENCES users(user_id),
          token_digest TEXT NOT NULL UNIQUE,
          expires_at TEXT NOT NULL,
          used_at TEXT,
          attempts INTEGER NOT NULL DEFAULT 0,
          created_at TEXT NOT NULL
        );
        CREATE TABLE IF NOT EXISTS memberships (
          tenant_id TEXT NOT NULL REFERENCES tenants(tenant_id),
          user_id TEXT NOT NULL REFERENCES users(user_id),
          role TEXT NOT NULL,
          PRIMARY KEY(tenant_id, user_id)
        );
        CREATE TABLE IF NOT EXISTS devices (
          device_id TEXT PRIMARY KEY,
          tenant_id TEXT NOT NULL REFERENCES tenants(tenant_id),
          user_id TEXT NOT NULL REFERENCES users(user_id),
          platform TEXT NOT NULL,
          state TEXT NOT NULL,
          last_seen_at TEXT NOT NULL,
          created_at TEXT NOT NULL,
          updated_at TEXT NOT NULL
        );
        CREATE TABLE IF NOT EXISTS sessions (
          session_id TEXT PRIMARY KEY,
          tenant_id TEXT NOT NULL REFERENCES tenants(tenant_id),
          user_id TEXT NOT NULL REFERENCES users(user_id),
          device_id TEXT NOT NULL REFERENCES devices(device_id),
          client_kind TEXT NOT NULL,
          access_digest TEXT NOT NULL UNIQUE,
          access_expires_at TEXT NOT NULL,
          state TEXT NOT NULL,
          created_at TEXT NOT NULL,
          updated_at TEXT NOT NULL
        );
        CREATE TABLE IF NOT EXISTS refresh_families (
          family_id TEXT PRIMARY KEY,
          session_id TEXT NOT NULL REFERENCES sessions(session_id),
          state TEXT NOT NULL,
          created_at TEXT NOT NULL,
          updated_at TEXT NOT NULL
        );
        CREATE TABLE IF NOT EXISTS refresh_tokens (
          refresh_id TEXT PRIMARY KEY,
          family_id TEXT NOT NULL REFERENCES refresh_families(family_id),
          refresh_digest TEXT NOT NULL UNIQUE,
          expires_at TEXT NOT NULL,
          used_at TEXT,
          created_at TEXT NOT NULL
        );
        CREATE TABLE IF NOT EXISTS oidc_transactions (
          transaction_id TEXT PRIMARY KEY,
          state_digest TEXT NOT NULL UNIQUE,
          nonce_digest TEXT NOT NULL,
          code_challenge TEXT NOT NULL,
          issuer TEXT NOT NULL,
          client_id TEXT NOT NULL,
          audience TEXT NOT NULL,
          redirect_uri TEXT NOT NULL,
          client_kind TEXT NOT NULL,
          tenant_id TEXT NOT NULL,
          expires_at TEXT NOT NULL,
          used_at TEXT,
          created_at TEXT NOT NULL
        );
        CREATE TABLE IF NOT EXISTS tenant_step_up_actions (
          tenant_id TEXT NOT NULL REFERENCES tenants(tenant_id),
          action_group TEXT NOT NULL,
          is_mandatory INTEGER NOT NULL,
          PRIMARY KEY(tenant_id, action_group)
        );
        CREATE TABLE IF NOT EXISTS step_up_authorizations (
          step_up_id TEXT PRIMARY KEY,
          authorization_digest TEXT NOT NULL UNIQUE,
          tenant_id TEXT NOT NULL REFERENCES tenants(tenant_id),
          actor_id TEXT NOT NULL REFERENCES users(user_id),
          session_id TEXT NOT NULL REFERENCES sessions(session_id),
          device_id TEXT NOT NULL REFERENCES devices(device_id),
          action_group TEXT NOT NULL,
          target_id TEXT NOT NULL,
          policy_version TEXT NOT NULL,
          issued_at TEXT NOT NULL,
          expires_at TEXT NOT NULL,
          used_at TEXT
        );
        """
        connection.executescript(schema)
        columns = {str(row[1]) for row in connection.execute("PRAGMA table_info(users)")}
        for name, definition in (
            ("login_id", "TEXT"), ("email", "TEXT"), ("password_digest", "TEXT"),
            ("email_verified_at", "TEXT"), ("state", "TEXT NOT NULL DEFAULT 'active'"),
        ):
            if name not in columns:
                connection.execute(f"ALTER TABLE users ADD COLUMN {name} {definition}")
        connection.execute("CREATE UNIQUE INDEX IF NOT EXISTS users_login_id_unique ON users(login_id) WHERE login_id IS NOT NULL")
        connection.execute("CREATE UNIQUE INDEX IF NOT EXISTS users_email_unique ON users(email) WHERE email IS NOT NULL")
        connection.execute(f"PRAGMA user_version = {SCHEMA_VERSION}")

    def _ensure_open(self) -> None:
        if self._closed:
            raise IdentityError("PERSISTENCE_UNAVAILABLE", 503)

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
            except IdentityError:
                if connection is not None and connection.in_transaction:
                    connection.execute("ROLLBACK")
                raise
            except sqlite3.Error as error:
                if connection is not None and connection.in_transaction:
                    connection.execute("ROLLBACK")
                raise IdentityError("PERSISTENCE_UNAVAILABLE", 503) from error
            except Exception:
                if connection is not None and connection.in_transaction:
                    connection.execute("ROLLBACK")
                raise
            finally:
                if connection is not None:
                    connection.close()

    def close(self) -> None:
        with self._lock:
            if not self._closed:
                self._closed = True

    def schema_version(self) -> int:
        with self._lock:
            self._ensure_open()
            connection = self._connect()
            try:
                return int(connection.execute("PRAGMA user_version").fetchone()[0])
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

    def oidc_state_digest(self, transaction_id: str) -> str | None:
        with self._lock:
            self._ensure_open()
            connection = self._connect()
            try:
                row = connection.execute(
                    "SELECT state_digest FROM oidc_transactions WHERE transaction_id = ?",
                    (_checked_text(transaction_id),),
                ).fetchone()
                return None if row is None else str(row[0])
            finally:
                connection.close()

    def _ensure_tenant(self, connection: sqlite3.Connection, tenant_id: str) -> None:
        connection.execute("INSERT OR IGNORE INTO tenants(tenant_id) VALUES (?)", (tenant_id,))
        for action in MINIMUM_STEP_UP_ACTION_GROUPS:
            connection.execute(
                "INSERT OR IGNORE INTO tenant_step_up_actions(tenant_id, action_group, is_mandatory) VALUES (?, ?, 1)",
                (tenant_id, action),
            )

    def required_step_up_actions(self, tenant_id: str) -> frozenset[str]:
        checked = _checked_text(tenant_id)
        with self.transaction() as connection:
            self._ensure_tenant(connection, checked)
            rows = connection.execute(
                "SELECT action_group FROM tenant_step_up_actions WHERE tenant_id = ?",
                (checked,),
            ).fetchall()
            return frozenset(str(row[0]) for row in rows)

    def add_step_up_action(self, tenant_id: str, action_group: str) -> None:
        checked_tenant = _checked_text(tenant_id)
        checked_action = _checked_text(action_group)
        with self.transaction() as connection:
            self._ensure_tenant(connection, checked_tenant)
            connection.execute(
                "INSERT OR IGNORE INTO tenant_step_up_actions(tenant_id, action_group, is_mandatory) VALUES (?, ?, 0)",
                (checked_tenant, checked_action),
            )

    def remove_step_up_action(self, tenant_id: str, action_group: str) -> None:
        _checked_text(tenant_id)
        _checked_text(action_group)
        raise IdentityError("STEP_UP_ACTION_REMOVE_DENIED", 403)

    def device_state(self, device_id: str) -> str | None:
        with self._lock:
            self._ensure_open()
            connection = self._connect()
            try:
                row = connection.execute(
                    "SELECT state FROM devices WHERE device_id = ?", (_checked_text(device_id),)
                ).fetchone()
                return None if row is None else str(row[0])
            finally:
                connection.close()

    def device_last_seen(self, device_id: str) -> datetime | None:
        with self._lock:
            self._ensure_open()
            connection = self._connect()
            try:
                row = connection.execute(
                    "SELECT last_seen_at FROM devices WHERE device_id = ?",
                    (_checked_text(device_id),),
                ).fetchone()
                return None if row is None else _dt(str(row[0]))
            finally:
                connection.close()

    def entity_counts(self) -> dict[str, int]:
        tables = (
            "users", "tenants", "memberships", "sessions", "refresh_families",
            "refresh_tokens", "devices", "oidc_transactions", "step_up_authorizations",
        )
        with self._lock:
            self._ensure_open()
            connection = self._connect()
            try:
                return {
                    table: int(connection.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0])
                    for table in tables
                }
            finally:
                connection.close()


class IdentityService:
    def __init__(
        self,
        *,
        repository: SqliteIdentityRepository,
        audit_store: object,
        oidc_policies: tuple[OidcClientPolicy, ...],
        clock: Callable[[], datetime],
        email_sender: EmailSender | None = None,
    ) -> None:
        self._repository = repository
        self._audit_store = audit_store
        self._clock = clock
        self._lock = RLock()
        self._policies = tuple(oidc_policies)
        self._email_sender = email_sender or SmtpEmailSender.from_env()

    def _now(self) -> datetime:
        return _checked_utc(self._clock())

    def _policy(
        self, issuer: str, client_id: str, audience: str, redirect_uri: str,
        client_kind: ClientKind, tenant_id: str,
    ) -> OidcClientPolicy:
        if not isinstance(client_kind, ClientKind):
            raise IdentityError("INVALID_INPUT")
        for policy in self._policies:
            if (
                policy.issuer == issuer and policy.client_id == client_id
                and policy.audience == audience and redirect_uri in policy.redirect_uris
                and policy.client_kind is client_kind and policy.tenant_id == tenant_id
            ):
                return policy
        raise IdentityError("OIDC_POLICY_DENIED", 403)

    def _audit(
        self, *, action: str, outcome: AuditOutcome, trace_id: str,
        policy_version: str, tenant_id: str, actor_id: str = "anonymous",
        target_type: str = "identity", target_id: str = "identity-public",
        metadata: dict[str, object] | None = None,
    ) -> None:
        draft = AuditEventDraft(
            event_id=_id("audit"), occurred_at=self._now(), actor_id=actor_id,
            actor_type=ActorType.USER if actor_id != "anonymous" else ActorType.SYSTEM,
            tenant_id=tenant_id, workspace_id=None, action=action,
            target_type=target_type, target_id=target_id, outcome=outcome,
            trace_id=_checked_text(trace_id), policy_version=_checked_text(policy_version),
            metadata=metadata or {},
        )
        try:
            self._audit_store.append(draft)  # type: ignore[attr-defined]
        except Exception as error:
            raise IdentityError("AUDIT_WRITE_FAILED", 503) from error

    def _issue_token(self, connection: sqlite3.Connection, *, table: str, user_id: str,
                     now: datetime, ttl: timedelta) -> str:
        token = _opaque()
        connection.execute(
            f"INSERT INTO {table}(token_id,user_id,token_digest,expires_at,used_at,attempts,created_at) VALUES (?,?,?,?,?,?,?)",
            (_id("tok"), user_id, _digest(token), _iso(now + ttl), None, 0, _iso(now)),
        )
        return token

    def signup(self, *, login_id: str, email: str, password: str,
               trace_id: str, policy_version: str) -> None:
        login = _checked_text(login_id).lower()
        address, secret = _email(email), _password(password)
        _checked_text(trace_id); _checked_text(policy_version)
        now, user_id, tenant_id = self._now(), _id("usr"), _id("tenant")
        with self._lock, self._repository.transaction() as connection:
            if connection.execute("SELECT 1 FROM users WHERE login_id=? OR email=?", (login, address)).fetchone():
                raise IdentityError("SIGNUP_ALREADY_EXISTS", 409)
            connection.execute(
                "INSERT INTO users(user_id,issuer,subject,login_id,email,password_digest,state) VALUES (?,?,?,?,?,?,?)",
                (user_id, "local", login, login, address, PASSWORD_HASHER.hash(secret), "pending_email"),
            )
            self._repository._ensure_tenant(connection, tenant_id)
            connection.execute("INSERT INTO memberships(tenant_id,user_id,role) VALUES (?,?,?)", (tenant_id, user_id, "personal_owner"))
            token = self._issue_token(connection, table="email_verification_tokens", user_id=user_id, now=now, ttl=timedelta(hours=24))
            self._email_sender.send(recipient=address, subject="Daon 이메일 인증", body=f"Daon 이메일 인증 토큰: {token}\n24시간 내 인증하세요.")
            self._audit(action="identity.signup.accepted", outcome=AuditOutcome.SUCCEEDED, trace_id=trace_id,
                        policy_version=policy_version, tenant_id=tenant_id, actor_id=user_id,
                        target_type="user", target_id=user_id)

    def verify_email(self, *, token: str, trace_id: str, policy_version: str) -> None:
        _checked_text(token, opaque=True); _checked_text(trace_id); _checked_text(policy_version)
        now = self._now()
        with self._lock, self._repository.transaction() as connection:
            row = connection.execute("SELECT * FROM email_verification_tokens WHERE token_digest=?", (_digest(token),)).fetchone()
            if row is None or row["used_at"] is not None or _dt(str(row["expires_at"])) <= now:
                raise IdentityError("EMAIL_TOKEN_INVALID", 400)
            connection.execute("UPDATE email_verification_tokens SET used_at=?,attempts=attempts+1 WHERE token_id=?", (_iso(now), str(row["token_id"])))
            connection.execute("UPDATE users SET email_verified_at=?,state='active' WHERE user_id=?", (_iso(now), str(row["user_id"])))
            self._audit(action="identity.email_verified", outcome=AuditOutcome.SUCCEEDED, trace_id=trace_id,
                        policy_version=policy_version, tenant_id="identity-public", actor_id=str(row["user_id"]), target_type="user", target_id=str(row["user_id"]))

    def resend_verification(self, *, identifier: str, trace_id: str, policy_version: str) -> None:
        value = _checked_text(identifier).lower(); _checked_text(trace_id); _checked_text(policy_version)
        now = self._now()
        with self._lock, self._repository.transaction() as connection:
            row = connection.execute("SELECT * FROM users WHERE login_id=? OR email=?", (value, value)).fetchone()
            if row is None or row["state"] != "pending_email":
                return
            token = self._issue_token(connection, table="email_verification_tokens", user_id=str(row["user_id"]), now=now, ttl=timedelta(hours=24))
            self._email_sender.send(recipient=str(row["email"]), subject="Daon 이메일 인증 재전송", body=f"Daon 이메일 인증 토큰: {token}")

    def request_password_reset(self, *, identifier: str, trace_id: str, policy_version: str) -> None:
        value = _checked_text(identifier).lower(); _checked_text(trace_id); _checked_text(policy_version)
        now = self._now()
        with self._lock, self._repository.transaction() as connection:
            row = connection.execute("SELECT * FROM users WHERE (login_id=? OR email=?) AND issuer='local' AND email_verified_at IS NOT NULL", (value, value)).fetchone()
            if row is None:
                return
            token = self._issue_token(connection, table="password_reset_tokens", user_id=str(row["user_id"]), now=now, ttl=timedelta(minutes=30))
            self._email_sender.send(recipient=str(row["email"]), subject="Daon 비밀번호 재설정", body=f"Daon 비밀번호 재설정 토큰: {token}")

    def confirm_password_reset(self, *, token: str, new_password: str, trace_id: str, policy_version: str) -> None:
        _checked_text(token, opaque=True); secret = _password(new_password); _checked_text(trace_id); _checked_text(policy_version)
        now = self._now()
        with self._lock, self._repository.transaction() as connection:
            row = connection.execute("SELECT * FROM password_reset_tokens WHERE token_digest=?", (_digest(token),)).fetchone()
            if row is None or row["used_at"] is not None or _dt(str(row["expires_at"])) <= now:
                raise IdentityError("PASSWORD_RESET_TOKEN_INVALID", 400)
            connection.execute("UPDATE users SET password_digest=? WHERE user_id=?", (PASSWORD_HASHER.hash(secret), str(row["user_id"])))
            connection.execute("UPDATE password_reset_tokens SET used_at=?,attempts=attempts+1 WHERE token_id=?", (_iso(now), str(row["token_id"])))
            connection.execute("UPDATE sessions SET state='revoked',updated_at=? WHERE user_id=?", (_iso(now), str(row["user_id"])))
            connection.execute("UPDATE refresh_families SET state='revoked',updated_at=? WHERE session_id IN (SELECT session_id FROM sessions WHERE user_id=?)", (_iso(now), str(row["user_id"])))

    def local_login(self, *, login_id: str, password: str, platform: DevicePlatform,
                    trace_id: str, policy_version: str) -> SessionCredentials:
        login = _checked_text(login_id).lower(); secret = _password(password)
        _checked_text(trace_id); _checked_text(policy_version)
        now = self._now()
        with self._lock, self._repository.transaction() as connection:
            row = connection.execute("SELECT * FROM users WHERE login_id=? AND issuer='local'", (login,)).fetchone()
            try:
                if row is None or row["password_digest"] is None or row["email_verified_at"] is None or row["state"] != "active":
                    raise VerifyMismatchError()
                PASSWORD_HASHER.verify(str(row["password_digest"]), secret)
            except Exception as error:
                raise IdentityError("AUTHENTICATION_REQUIRED", 401) from error
            user_id = str(row["user_id"]); tenant = connection.execute("SELECT tenant_id FROM memberships WHERE user_id=? ORDER BY tenant_id LIMIT 1", (user_id,)).fetchone()
            if tenant is None:
                raise IdentityError("AUTHENTICATION_REQUIRED", 401)
            tenant_id = str(tenant[0]); device_id, session_id, access = _id("dev"), _id("ses"), _opaque()
            connection.execute("INSERT INTO devices(device_id,tenant_id,user_id,platform,state,last_seen_at,created_at,updated_at) VALUES (?,?,?,?,?,?,?,?)", (device_id, tenant_id, user_id, platform.value, "registered", _iso(now), _iso(now), _iso(now)))
            connection.execute("INSERT INTO sessions(session_id,tenant_id,user_id,device_id,client_kind,access_digest,access_expires_at,state,created_at,updated_at) VALUES (?,?,?,?,?,?,?,?,?,?)", (session_id, tenant_id, user_id, device_id, "web", _digest(access), _iso(now + ACCESS_TTL), "active", _iso(now), _iso(now)))
            self._audit(action="identity.login.succeeded", outcome=AuditOutcome.SUCCEEDED, trace_id=trace_id, policy_version=policy_version, tenant_id=tenant_id, actor_id=user_id, target_type="session", target_id=session_id, metadata={"client_type": "web", "auth_method": "local"})
        return SessionCredentials(access, None, user_id, session_id, device_id, tenant_id, ClientKind.WEB, "web_session_cookie_boundary_m4_05")

    def begin_oidc_login(
        self, *, issuer: str, client_id: str, audience: str, redirect_uri: str,
        client_kind: ClientKind, tenant_id: str, trace_id: str, policy_version: str,
    ) -> OidcLoginStart:
        for value in (issuer, redirect_uri):
            _checked_text(value, opaque=True)
        for value in (client_id, audience, tenant_id, trace_id, policy_version):
            _checked_text(value)
        policy = self._policy(issuer, client_id, audience, redirect_uri, client_kind, tenant_id)
        now = self._now()
        state, nonce, verifier = _opaque(), _opaque(), _opaque()
        challenge = base64.urlsafe_b64encode(
            hashlib.sha256(verifier.encode("ascii")).digest()
        ).decode("ascii").rstrip("=")
        transaction_id = _id("oidc")
        expires_at = now + OIDC_TRANSACTION_TTL
        with self._repository.transaction() as connection:
            self._repository._ensure_tenant(connection, tenant_id)
            connection.execute(
                """INSERT INTO oidc_transactions(
                transaction_id,state_digest,nonce_digest,code_challenge,issuer,client_id,
                audience,redirect_uri,client_kind,tenant_id,expires_at,used_at,created_at
                ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                (transaction_id, _digest(state), _digest(nonce), challenge, policy.issuer,
                 policy.client_id, policy.audience, redirect_uri, client_kind.value,
                 tenant_id, _iso(expires_at), None, _iso(now)),
            )
            self._audit(
                action="identity.login.started", outcome=AuditOutcome.SUCCEEDED,
                trace_id=trace_id, policy_version=policy_version, tenant_id=tenant_id,
                target_type="oidc_transaction", target_id=transaction_id,
                metadata={"client_type": client_kind.value},
            )
        return OidcLoginStart(
            transaction_id, state, nonce, verifier, challenge, "S256", expires_at
        )

    def complete_oidc_login(
        self, *, state: str, authorization_code: str, code_verifier: str,
        client_id: str, redirect_uri: str, provider: VerifiedOidcProvider,
        platform: DevicePlatform, trace_id: str, policy_version: str,
    ) -> SessionCredentials:
        for value in (state, authorization_code, code_verifier, redirect_uri):
            _checked_text(value, opaque=True)
        for value in (client_id, trace_id, policy_version):
            _checked_text(value)
        if not isinstance(platform, DevicePlatform):
            raise IdentityError("INVALID_INPUT")
        now = self._now()
        state_digest = _digest(state)
        with self._lock, self._repository.transaction() as connection:
            row = connection.execute(
                "SELECT * FROM oidc_transactions WHERE state_digest = ?", (state_digest,)
            ).fetchone()
            if row is None or row["used_at"] is not None:
                self._audit(
                    action="identity.login.denied", outcome=AuditOutcome.DENIED,
                    trace_id=trace_id, policy_version=policy_version,
                    tenant_id="identity-public", metadata={"reason_code": "OIDC_STATE_INVALID"},
                )
                raise IdentityError("OIDC_STATE_INVALID", 401)
            tenant_id = str(row["tenant_id"])
            transaction_id = str(row["transaction_id"])
            if _dt(str(row["expires_at"])) <= now:
                self._audit(
                    action="identity.login.denied", outcome=AuditOutcome.DENIED,
                    trace_id=trace_id, policy_version=policy_version, tenant_id=tenant_id,
                    target_type="oidc_transaction", target_id=transaction_id,
                    metadata={"reason_code": "OIDC_TRANSACTION_EXPIRED"},
                )
                raise IdentityError("OIDC_TRANSACTION_EXPIRED", 401)
            if client_id != row["client_id"] or redirect_uri != row["redirect_uri"]:
                self._audit(
                    action="identity.login.denied", outcome=AuditOutcome.DENIED,
                    trace_id=trace_id, policy_version=policy_version, tenant_id=tenant_id,
                    target_type="oidc_transaction", target_id=transaction_id,
                    metadata={"reason_code": "OIDC_POLICY_DENIED"},
                )
                raise IdentityError("OIDC_POLICY_DENIED", 403)
            computed = base64.urlsafe_b64encode(
                hashlib.sha256(code_verifier.encode("ascii")).digest()
            ).decode("ascii").rstrip("=")
            if not secrets.compare_digest(computed, str(row["code_challenge"])):
                self._audit(
                    action="identity.login.denied", outcome=AuditOutcome.DENIED,
                    trace_id=trace_id, policy_version=policy_version, tenant_id=tenant_id,
                    target_type="oidc_transaction", target_id=transaction_id,
                    metadata={"reason_code": "OIDC_PKCE_INVALID"},
                )
                raise IdentityError("OIDC_PKCE_INVALID", 401)
            try:
                claims = provider.exchange_verified(
                    authorization_code=authorization_code, code_verifier=code_verifier,
                    expected_code_challenge=str(row["code_challenge"]),
                    expected_issuer=str(row["issuer"]), expected_audience=str(row["audience"]),
                    expected_nonce_digest=str(row["nonce_digest"]), now=now,
                )
            except Exception as error:
                self._audit(
                    action="identity.login.denied", outcome=AuditOutcome.DENIED,
                    trace_id=trace_id, policy_version=policy_version, tenant_id=tenant_id,
                    target_type="oidc_transaction", target_id=transaction_id,
                    metadata={"reason_code": "OIDC_PROVIDER_REJECTED"},
                )
                raise IdentityError("OIDC_PROVIDER_REJECTED", 401) from error
            if (
                not isinstance(claims, VerifiedOidcClaims) or not claims.verification_complete
                or claims.issuer != row["issuer"] or claims.audience != row["audience"]
                or _digest(claims.nonce) != row["nonce_digest"]
            ):
                self._audit(
                    action="identity.login.denied", outcome=AuditOutcome.DENIED,
                    trace_id=trace_id, policy_version=policy_version, tenant_id=tenant_id,
                    target_type="oidc_transaction", target_id=transaction_id,
                    metadata={"reason_code": "OIDC_CLAIMS_INVALID"},
                )
                raise IdentityError("OIDC_CLAIMS_INVALID", 401)
            if _checked_utc(claims.expires_at) <= now:
                self._audit(
                    action="identity.login.denied", outcome=AuditOutcome.DENIED,
                    trace_id=trace_id, policy_version=policy_version, tenant_id=tenant_id,
                    target_type="oidc_transaction", target_id=transaction_id,
                    metadata={"reason_code": "OIDC_CLAIMS_EXPIRED"},
                )
                raise IdentityError("OIDC_CLAIMS_EXPIRED", 401)
            subject = _checked_text(claims.subject, opaque=True)
            connection.execute(
                "UPDATE oidc_transactions SET used_at = ? WHERE transaction_id = ? AND used_at IS NULL",
                (_iso(now), transaction_id),
            )
            existing = connection.execute(
                "SELECT user_id FROM users WHERE issuer = ? AND subject = ?",
                (claims.issuer, subject),
            ).fetchone()
            user_id = str(existing[0]) if existing else _id("usr")
            if existing is None:
                connection.execute(
                    "INSERT INTO users(user_id,issuer,subject) VALUES (?,?,?)",
                    (user_id, claims.issuer, subject),
                )
            connection.execute(
                "INSERT OR IGNORE INTO memberships(tenant_id,user_id,role) VALUES (?,?,?)",
                (tenant_id, user_id, "member"),
            )
            device_id, session_id = _id("dev"), _id("ses")
            access = _opaque()
            connection.execute(
                "INSERT INTO devices(device_id,tenant_id,user_id,platform,state,last_seen_at,created_at,updated_at) VALUES (?,?,?,?,?,?,?,?)",
                (device_id, tenant_id, user_id, platform.value, "registered", _iso(now), _iso(now), _iso(now)),
            )
            client_kind = ClientKind(str(row["client_kind"]))
            connection.execute(
                """INSERT INTO sessions(session_id,tenant_id,user_id,device_id,client_kind,
                access_digest,access_expires_at,state,created_at,updated_at) VALUES (?,?,?,?,?,?,?,?,?,?)""",
                (session_id, tenant_id, user_id, device_id, client_kind.value,
                 _digest(access), _iso(now + ACCESS_TTL), "active", _iso(now), _iso(now)),
            )
            refresh: str | None = None
            if client_kind is ClientKind.NATIVE:
                refresh, family_id = _opaque(), _id("fam")
                connection.execute(
                    "INSERT INTO refresh_families(family_id,session_id,state,created_at,updated_at) VALUES (?,?,?,?,?)",
                    (family_id, session_id, "active", _iso(now), _iso(now)),
                )
                connection.execute(
                    "INSERT INTO refresh_tokens(refresh_id,family_id,refresh_digest,expires_at,used_at,created_at) VALUES (?,?,?,?,?,?)",
                    (_id("ref"), family_id, _digest(refresh), _iso(now + REFRESH_TTL), None, _iso(now)),
                )
            self._audit(
                action="identity.login.succeeded", outcome=AuditOutcome.SUCCEEDED,
                trace_id=trace_id, policy_version=policy_version, tenant_id=tenant_id,
                actor_id=user_id, target_type="session", target_id=session_id,
                metadata={"client_type": client_kind.value},
            )
        delivery = (
            "web_session_cookie_boundary_m4_05" if client_kind is ClientKind.WEB
            else "native_opaque_refresh_rotation"
        )
        return SessionCredentials(access, refresh, user_id, session_id, device_id, tenant_id, client_kind, delivery)

    def _principal(self, connection: sqlite3.Connection, access_token: str, now: datetime) -> sqlite3.Row:
        _checked_text(access_token, opaque=True)
        row = connection.execute(
            "SELECT * FROM sessions WHERE access_digest = ?", (_digest(access_token),)
        ).fetchone()
        if row is None:
            raise IdentityError("ACCESS_INVALID", 401)
        if row["state"] != "active":
            raise IdentityError("SESSION_REVOKED", 401)
        if _dt(str(row["access_expires_at"])) <= now:
            raise IdentityError("ACCESS_EXPIRED", 401)
        connection.execute(
            "UPDATE devices SET last_seen_at = ?, updated_at = ? WHERE device_id = ?",
            (_iso(now), _iso(now), str(row["device_id"])),
        )
        return row

    def validate_access(self, access_token: str, *, trace_id: str, policy_version: str) -> IdentityPrincipal:
        _checked_text(trace_id); _checked_text(policy_version)
        with self._repository.transaction() as connection:
            now = self._now()
            try:
                row = self._principal(connection, access_token, now)
            except IdentityError as error:
                known = None
                if isinstance(access_token, str):
                    known = connection.execute(
                        "SELECT * FROM sessions WHERE access_digest = ?", (_digest(access_token),)
                    ).fetchone()
                reason_code = error.code if error.code in {"ACCESS_EXPIRED", "SESSION_REVOKED"} else "ACCESS_INVALID"
                action = "identity.access.expired" if reason_code == "ACCESS_EXPIRED" else "identity.access.denied"
                self._audit(
                    action=action,
                    outcome=AuditOutcome.DENIED,
                    trace_id=trace_id,
                    policy_version=policy_version,
                    tenant_id="identity-public" if known is None else str(known["tenant_id"]),
                    actor_id="anonymous" if known is None else str(known["user_id"]),
                    target_type="access_credential" if known is None else "session",
                    target_id="unknown-access" if known is None else str(known["session_id"]),
                    metadata={"reason_code": reason_code},
                )
                if reason_code != error.code:
                    raise IdentityError(reason_code, 401) from error
                raise
            return IdentityPrincipal(str(row["user_id"]), str(row["session_id"]), str(row["device_id"]), str(row["tenant_id"]))

    def describe_access(
        self, access_token: str, *, trace_id: str, policy_version: str
    ) -> IdentitySessionView:
        """Return the safe public session projection after normal credential validation."""
        principal = self.validate_access(
            access_token, trace_id=trace_id, policy_version=policy_version
        )
        with self._repository.transaction() as connection:
            row = connection.execute(
                "SELECT client_kind, access_expires_at FROM sessions WHERE session_id = ?",
                (principal.session_id,),
            ).fetchone()
            if row is None:
                raise IdentityError("ACCESS_INVALID", 401)
            return IdentitySessionView(
                principal=principal,
                client_kind=ClientKind(str(row["client_kind"])),
                expires_at=_dt(str(row["access_expires_at"])),
            )

    def rotate_refresh(self, refresh_token: str | None, *, trace_id: str, policy_version: str) -> SessionCredentials:
        _checked_text(trace_id); _checked_text(policy_version)
        now = self._now()
        replay = False
        with self._lock, self._repository.transaction() as connection:
            row = None
            if refresh_token is not None:
                try:
                    _checked_text(refresh_token, opaque=True)
                except IdentityError:
                    refresh_token = None
                else:
                    row = connection.execute(
                        """SELECT rt.*,rf.session_id,rf.state AS family_state,s.*
                        FROM refresh_tokens rt JOIN refresh_families rf ON rf.family_id=rt.family_id
                        JOIN sessions s ON s.session_id=rf.session_id WHERE rt.refresh_digest=?""",
                        (_digest(refresh_token),),
                    ).fetchone()
            if row is None:
                self._audit(
                    action="identity.refresh.denied", outcome=AuditOutcome.DENIED,
                    trace_id=trace_id, policy_version=policy_version, tenant_id="identity-public",
                    target_type="refresh_credential", target_id="unknown-refresh",
                    metadata={"reason_code": "REFRESH_INVALID"},
                )
                raise IdentityError("REFRESH_INVALID", 401)
            family_id, session_id = str(row["family_id"]), str(row["session_id"])
            if row["used_at"] is not None:
                connection.execute("UPDATE refresh_families SET state='revoked',updated_at=? WHERE family_id=?", (_iso(now), family_id))
                connection.execute("UPDATE sessions SET state='revoked',updated_at=? WHERE session_id=?", (_iso(now), session_id))
                self._audit(
                    action="identity.refresh.replay_denied", outcome=AuditOutcome.DENIED,
                    trace_id=trace_id, policy_version=policy_version, tenant_id=str(row["tenant_id"]),
                    actor_id=str(row["user_id"]), target_type="refresh_family", target_id=family_id,
                    metadata={"reason_code": "REFRESH_REPLAYED"},
                )
                self._audit(
                    action="identity.session.revoked", outcome=AuditOutcome.SUCCEEDED,
                    trace_id=trace_id, policy_version=policy_version, tenant_id=str(row["tenant_id"]),
                    actor_id=str(row["user_id"]), target_type="session", target_id=session_id,
                    metadata={"reason_code": "REFRESH_REPLAYED"},
                )
                replay = True
            elif row["family_state"] != "active" or row["state"] != "active":
                self._audit(
                    action="identity.refresh.denied", outcome=AuditOutcome.DENIED,
                    trace_id=trace_id, policy_version=policy_version, tenant_id=str(row["tenant_id"]),
                    actor_id=str(row["user_id"]), target_type="refresh_family", target_id=family_id,
                    metadata={"reason_code": "SESSION_REVOKED", "session_id": session_id},
                )
                raise IdentityError("SESSION_REVOKED", 401)
            elif _dt(str(row["expires_at"])) <= now:
                self._audit(
                    action="identity.refresh.expired", outcome=AuditOutcome.DENIED,
                    trace_id=trace_id, policy_version=policy_version, tenant_id=str(row["tenant_id"]),
                    actor_id=str(row["user_id"]), target_type="refresh_family", target_id=family_id,
                    metadata={"reason_code": "REFRESH_EXPIRED", "session_id": session_id},
                )
                raise IdentityError("REFRESH_EXPIRED", 401)
            else:
                access, refresh = _opaque(), _opaque()
                connection.execute("UPDATE refresh_tokens SET used_at=? WHERE refresh_id=? AND used_at IS NULL", (_iso(now), str(row["refresh_id"])))
                connection.execute("UPDATE sessions SET access_digest=?,access_expires_at=?,updated_at=? WHERE session_id=?", (_digest(access), _iso(now + ACCESS_TTL), _iso(now), session_id))
                connection.execute("INSERT INTO refresh_tokens(refresh_id,family_id,refresh_digest,expires_at,used_at,created_at) VALUES (?,?,?,?,?,?)", (_id("ref"), family_id, _digest(refresh), _iso(now + REFRESH_TTL), None, _iso(now)))
                self._audit(
                    action="identity.refresh.rotated", outcome=AuditOutcome.SUCCEEDED,
                    trace_id=trace_id, policy_version=policy_version, tenant_id=str(row["tenant_id"]),
                    actor_id=str(row["user_id"]), target_type="session", target_id=session_id,
                )
                result = SessionCredentials(access, refresh, str(row["user_id"]), session_id, str(row["device_id"]), str(row["tenant_id"]), ClientKind.NATIVE, "native_opaque_refresh_rotation")
        if replay:
            raise IdentityError("REFRESH_REPLAYED", 401)
        return result

    def requires_step_up(self, tenant_id: str, action_group: str) -> bool:
        return _checked_text(action_group) in self._repository.required_step_up_actions(_checked_text(tenant_id))

    def issue_step_up(self, *, access_token: str, action_group: str, target_id: str,
                      policy_version: str, trace_id: str, ttl_seconds: int = DEFAULT_STEP_UP_TTL_SECONDS) -> StepUpGrant:
        action_group = _checked_text(action_group); target_id = _checked_text(target_id)
        _checked_text(policy_version); _checked_text(trace_id)
        if not isinstance(ttl_seconds, int) or isinstance(ttl_seconds, bool) or not 1 <= ttl_seconds <= MAX_STEP_UP_TTL_SECONDS:
            raise IdentityError("STEP_UP_TTL_INVALID")
        now = self._now(); raw = _opaque()
        with self._lock, self._repository.transaction() as connection:
            principal = self._principal(connection, access_token, now)
            tenant_id = str(principal["tenant_id"])
            actions = connection.execute("SELECT 1 FROM tenant_step_up_actions WHERE tenant_id=? AND action_group=?", (tenant_id, action_group)).fetchone()
            if actions is None:
                raise IdentityError("STEP_UP_ACTION_NOT_REQUIRED", 403)
            expires = now + timedelta(seconds=ttl_seconds)
            step_id = _id("sup")
            connection.execute(
                """INSERT INTO step_up_authorizations(step_up_id,authorization_digest,tenant_id,
                actor_id,session_id,device_id,action_group,target_id,policy_version,issued_at,expires_at,used_at)
                VALUES (?,?,?,?,?,?,?,?,?,?,?,?)""",
                (step_id, _digest(raw), tenant_id, str(principal["user_id"]), str(principal["session_id"]),
                 str(principal["device_id"]), action_group, target_id, policy_version, _iso(now), _iso(expires), None),
            )
            self._audit(action="identity.step_up.issued", outcome=AuditOutcome.SUCCEEDED,
                        trace_id=trace_id, policy_version=policy_version, tenant_id=tenant_id,
                        actor_id=str(principal["user_id"]), target_type="step_up", target_id=step_id,
                        metadata={"action_group": action_group})
        return StepUpGrant(raw, now, expires)

    def _consume_step_up(self, connection: sqlite3.Connection, *, raw: str | None,
                         principal: sqlite3.Row, action_group: str, target_id: str,
                         policy_version: str, trace_id: str, now: datetime) -> None:
        tenant_id, actor_id = str(principal["tenant_id"]), str(principal["user_id"])
        if raw is None:
            raise IdentityError("STEP_UP_REQUIRED", 403)
        _checked_text(raw, opaque=True)
        row = connection.execute("SELECT * FROM step_up_authorizations WHERE authorization_digest=?", (_digest(raw),)).fetchone()
        if row is None:
            raise IdentityError("STEP_UP_REQUIRED", 403)
        if row["used_at"] is not None:
            self._audit(action="identity.step_up.reuse_denied", outcome=AuditOutcome.DENIED,
                        trace_id=trace_id, policy_version=policy_version, tenant_id=tenant_id,
                        actor_id=actor_id, target_type="step_up", target_id=str(row["step_up_id"]),
                        metadata={"reason_code": "STEP_UP_REUSED"})
            raise IdentityError("STEP_UP_REUSED", 403)
        if _dt(str(row["expires_at"])) <= now:
            self._audit(action="identity.step_up.expired", outcome=AuditOutcome.DENIED,
                        trace_id=trace_id, policy_version=policy_version, tenant_id=tenant_id,
                        actor_id=actor_id, target_type="step_up", target_id=str(row["step_up_id"]),
                        metadata={"reason_code": "STEP_UP_EXPIRED"})
            raise IdentityError("STEP_UP_EXPIRED", 403)
        binding = (actor_id, str(principal["session_id"]), str(principal["device_id"]), tenant_id, action_group, target_id, policy_version)
        expected = (str(row["actor_id"]), str(row["session_id"]), str(row["device_id"]), str(row["tenant_id"]), str(row["action_group"]), str(row["target_id"]), str(row["policy_version"]))
        if binding != expected:
            self._audit(action="identity.step_up.binding_denied", outcome=AuditOutcome.DENIED,
                        trace_id=trace_id, policy_version=policy_version, tenant_id=tenant_id,
                        actor_id=actor_id, target_type="step_up", target_id=str(row["step_up_id"]),
                        metadata={"reason_code": "STEP_UP_BINDING_DENIED"})
            raise IdentityError("STEP_UP_BINDING_DENIED", 403)
        connection.execute("UPDATE step_up_authorizations SET used_at=? WHERE step_up_id=? AND used_at IS NULL", (_iso(now), str(row["step_up_id"])))
        self._audit(action="identity.step_up.used", outcome=AuditOutcome.SUCCEEDED,
                    trace_id=trace_id, policy_version=policy_version, tenant_id=tenant_id,
                    actor_id=actor_id, target_type="step_up", target_id=str(row["step_up_id"]),
                    metadata={"action_group": action_group})

    def consume_step_up(self, *, step_up_authorization: str | None, access_token: str,
                        action_group: str, target_id: str, policy_version: str, trace_id: str) -> None:
        action_group = _checked_text(action_group); target_id = _checked_text(target_id)
        _checked_text(policy_version); _checked_text(trace_id)
        now = self._now()
        with self._lock, self._repository.transaction() as connection:
            principal = self._principal(connection, access_token, now)
            self._consume_step_up(connection, raw=step_up_authorization, principal=principal,
                                  action_group=action_group, target_id=target_id,
                                  policy_version=policy_version, trace_id=trace_id, now=now)

    def trust_device(self, *, access_token: str, device_id: str, trace_id: str, policy_version: str) -> None:
        device_id = _checked_text(device_id); _checked_text(trace_id); _checked_text(policy_version)
        now = self._now()
        with self._repository.transaction() as connection:
            principal = self._principal(connection, access_token, now)
            if str(principal["device_id"]) != device_id:
                self._audit(
                    action="identity.device.trust_denied", outcome=AuditOutcome.DENIED,
                    trace_id=trace_id, policy_version=policy_version,
                    tenant_id=str(principal["tenant_id"]), actor_id=str(principal["user_id"]),
                    target_type="device", target_id=device_id,
                    metadata={"reason_code": "DEVICE_BINDING_DENIED"},
                )
                raise IdentityError("DEVICE_BINDING_DENIED", 403)
            connection.execute("UPDATE devices SET state='trusted',updated_at=? WHERE device_id=?", (_iso(now), device_id))
            self._audit(
                action="identity.device.trusted", outcome=AuditOutcome.SUCCEEDED,
                trace_id=trace_id, policy_version=policy_version,
                tenant_id=str(principal["tenant_id"]), actor_id=str(principal["user_id"]),
                target_type="device", target_id=device_id,
            )

    def revoke_session(self, *, access_token: str, session_id: str,
                       step_up_authorization: str | None, policy_version: str,
                       trace_id: str) -> SessionRevocationEvent:
        session_id = _checked_text(session_id); _checked_text(policy_version); _checked_text(trace_id)
        now = self._now()
        with self._lock, self._repository.transaction() as connection:
            principal = self._principal(connection, access_token, now)
            target = connection.execute(
                """SELECT session_id FROM sessions
                WHERE session_id=? AND tenant_id=? AND user_id=? AND state='active'""",
                (session_id, str(principal["tenant_id"]), str(principal["user_id"])),
            ).fetchone()
            if target is None:
                self._audit(
                    action="identity.session.revoke_denied", outcome=AuditOutcome.DENIED,
                    trace_id=trace_id, policy_version=policy_version,
                    tenant_id=str(principal["tenant_id"]), actor_id=str(principal["user_id"]),
                    target_type="session", target_id="unavailable-session",
                    metadata={"reason_code": "SESSION_TARGET_UNAVAILABLE"},
                )
                raise IdentityError("SESSION_TARGET_UNAVAILABLE", 404)
            self._consume_step_up(
                connection, raw=step_up_authorization, principal=principal,
                action_group="device_session_or_sync_key_revoke", target_id=session_id,
                policy_version=policy_version, trace_id=trace_id, now=now,
            )
            connection.execute(
                "UPDATE sessions SET state='revoked',updated_at=? WHERE session_id=? AND state='active'",
                (_iso(now), session_id),
            )
            connection.execute(
                "UPDATE refresh_families SET state='revoked',updated_at=? WHERE session_id=?",
                (_iso(now), session_id),
            )
            self._audit(
                action="identity.session.revoked", outcome=AuditOutcome.SUCCEEDED,
                trace_id=trace_id, policy_version=policy_version,
                tenant_id=str(principal["tenant_id"]), actor_id=str(principal["user_id"]),
                target_type="session", target_id=session_id,
                metadata={"reason_code": "USER_REQUESTED"},
            )
        return SessionRevocationEvent(session_id)

    def revoke_device(self, *, access_token: str, device_id: str,
                      step_up_authorization: str | None, policy_version: str,
                      trace_id: str) -> DeviceRevocationEvent:
        device_id = _checked_text(device_id); _checked_text(policy_version); _checked_text(trace_id)
        now = self._now()
        with self._lock, self._repository.transaction() as connection:
            principal = self._principal(connection, access_token, now)
            self._consume_step_up(connection, raw=step_up_authorization, principal=principal,
                                  action_group="device_session_or_sync_key_revoke", target_id=device_id,
                                  policy_version=policy_version, trace_id=trace_id, now=now)
            connection.execute("UPDATE devices SET state='revoked',updated_at=? WHERE device_id=? AND tenant_id=?", (_iso(now), device_id, str(principal["tenant_id"])))
            connection.execute("UPDATE sessions SET state='revoked',updated_at=? WHERE device_id=?", (_iso(now), device_id))
            connection.execute("UPDATE refresh_families SET state='revoked',updated_at=? WHERE session_id IN (SELECT session_id FROM sessions WHERE device_id=?)", (_iso(now), device_id))
            self._audit(action="identity.session.revoked", outcome=AuditOutcome.SUCCEEDED,
                        trace_id=trace_id, policy_version=policy_version,
                        tenant_id=str(principal["tenant_id"]), actor_id=str(principal["user_id"]),
                        target_type="session", target_id=str(principal["session_id"]),
                        metadata={"reason_code": "DEVICE_REVOKED"})
            self._audit(action="identity.device.revoked", outcome=AuditOutcome.SUCCEEDED,
                        trace_id=trace_id, policy_version=policy_version,
                        tenant_id=str(principal["tenant_id"]), actor_id=str(principal["user_id"]),
                        target_type="device", target_id=device_id,
                        metadata={"sync_key_revoke_required": True})
        return DeviceRevocationEvent(device_id, True)


def identity_contract_summary() -> dict[str, object]:
    """Public deterministic capability summary for repository verification."""
    return {
        "schema_version": str(SCHEMA_VERSION),
        "oidc_flow": "authorization_code_pkce_s256",
        "credential_storage": "sha256_digest_only",
        "web_delivery_boundary": "m4_05_same_origin_cookie",
        "native_delivery": "opaque_access_refresh_rotation",
        "sqlite": {"foreign_keys": True, "journal_mode": "WAL"},
        "step_up_default_ttl_seconds": DEFAULT_STEP_UP_TTL_SECONDS,
        "step_up_max_ttl_seconds": MAX_STEP_UP_TTL_SECONDS,
        "minimum_step_up_actions": sorted(MINIMUM_STEP_UP_ACTION_GROUPS),
        "explicit_session_revoke": True,
        "denied_credential_audit": True,
        "external_provider_verified": False,
        "http_runtime_implemented": False,
    }
