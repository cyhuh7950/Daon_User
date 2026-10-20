"""Persisted Android browser authorization code bridge (no HTTP boundary)."""

from __future__ import annotations

import base64
from dataclasses import dataclass
from datetime import datetime, timedelta
import hashlib
import hmac
from ipaddress import ip_address
import re
from typing import Callable, Mapping
from urllib.parse import urlencode, urlsplit

from .identity import (
    ACCESS_TTL, REFRESH_TTL, ClientKind, DevicePlatform, IdentityError,
    IdentityService, SessionCredentials, SqliteIdentityRepository,
    _checked_text, _checked_utc, _digest, _dt, _id, _iso, _opaque,
)


TRANSACTION_TTL = timedelta(minutes=5)
CODE_TTL = timedelta(seconds=60)
_PKCE_TEXT = re.compile(r"^[A-Za-z0-9_-]{43}$")
_VERIFIER_TEXT = re.compile(r"^[A-Za-z0-9._~-]{43,128}$")


@dataclass(frozen=True, slots=True)
class AndroidAuthStart:
    transaction_id: str
    authorization_url: str
    expires_at: datetime


@dataclass(frozen=True, slots=True)
class AndroidAuthRedirect:
    redirect_uri: str
    code: str
    state: str
    url: str


@dataclass(frozen=True, slots=True)
class AndroidAuthExchange:
    credentials: SessionCredentials
    workspace_id: str
    expires_at: datetime


def _https_uri(uri: str) -> bool:
    if not isinstance(uri, str) or any(ord(char) < 33 or ord(char) > 126 for char in uri):
        return False
    try:
        parsed = urlsplit(uri)
        host = parsed.hostname
        if host is None or host.lower() == "localhost" or host.lower().endswith(".localhost"):
            return False
        try:
            ip_address(host)
        except ValueError:
            pass
        else:
            return False
        return (parsed.scheme == "https" and bool(parsed.hostname) and
                parsed.username is None and parsed.password is None and
                parsed.fragment == "" and parsed.port in (None, 443))
    except ValueError:
        return False


class AndroidBrowserAuthService:
    """The caller must supply a fixed, trusted client/redirect policy."""

    def __init__(
        self, *, repository: SqliteIdentityRepository, identity: IdentityService,
        clock: Callable[[], datetime],
        clients: Mapping[str, frozenset[str]] | None = None,
        authorization_origin: str | None = None,
    ) -> None:
        self._repository = repository
        self._identity = identity
        self._clock = clock
        self._clients = {client: frozenset(redirects) for client, redirects in (clients or {}).items()}
        self._origin = authorization_origin
        if self._origin is not None and (
            not _https_uri(self._origin) or urlsplit(self._origin).path not in ("", "/")
            or urlsplit(self._origin).query
        ):
            raise IdentityError("ANDROID_AUTH_POLICY_INVALID", 503)
        for client, redirects in self._clients.items():
            if not isinstance(client, str) or not client or not redirects or any(
                not isinstance(uri, str) or not _https_uri(uri) for uri in redirects
            ):
                raise IdentityError("ANDROID_AUTH_POLICY_INVALID", 503)

    def _now(self) -> datetime:
        return _checked_utc(self._clock())

    def begin(
        self, client_id: str, redirect_uri: str, state: str,
        code_challenge: str, code_challenge_method: str, trace_id: str,
    ) -> AndroidAuthStart:
        _checked_text(trace_id)
        if (
            self._origin is None or not isinstance(client_id, str)
            or not isinstance(redirect_uri, str) or not _https_uri(redirect_uri)
            or redirect_uri not in self._clients.get(client_id, ())
        ):
            raise IdentityError("ANDROID_AUTH_CLIENT_DENIED", 403)
        if (
            not isinstance(state, str) or len(state) < 16 or len(state) > 512
            or any(ord(char) < 33 or ord(char) > 126 for char in state)
            or code_challenge_method != "S256"
            or not isinstance(code_challenge, str)
            or _PKCE_TEXT.fullmatch(code_challenge) is None
        ):
            raise IdentityError("INVALID_INPUT", 422)
        transaction_id = _id("and")
        with self._repository.transaction() as connection:
            now = self._now()
            expires_at = now + TRANSACTION_TTL
            connection.execute(
                "DELETE FROM android_auth_transactions WHERE transaction_id IN "
                "(SELECT transaction_id FROM android_auth_transactions "
                "WHERE expires_at < ? ORDER BY expires_at LIMIT 32)", (_iso(now),),
            )
            connection.execute(
                "INSERT INTO android_auth_transactions "
                "(transaction_id,client_id,redirect_uri,state,code_challenge,code_challenge_method,expires_at,created_at) "
                "VALUES (?,?,?,?,?,?,?,?)",
                (transaction_id, client_id, redirect_uri, state, code_challenge,
                 "S256", _iso(expires_at), _iso(now)),
            )
            now = self._now()
            expires_at = now + TRANSACTION_TTL
            connection.execute(
                "UPDATE android_auth_transactions SET expires_at=?,created_at=? WHERE transaction_id=?",
                (_iso(expires_at), _iso(now), transaction_id),
            )
        return AndroidAuthStart(
            transaction_id,
            f"{self._origin.rstrip('/')}/auth/android/authorize?{urlencode({'transaction_id': transaction_id})}",
            expires_at,
        )

    def authorize(
        self, transaction_id: str, web_session: str,
        selected_tenant_id: str, trace_id: str,
    ) -> AndroidAuthRedirect:
        transaction_id = _checked_text(transaction_id)
        selected_tenant_id = _checked_text(selected_tenant_id)
        _checked_text(trace_id)
        with self._repository.transaction() as connection:
            connection.execute(
                "UPDATE android_auth_transactions SET transaction_id=transaction_id WHERE transaction_id=?",
                (transaction_id,),
            )
            now = self._now()
            row = connection.execute(
                "SELECT * FROM android_auth_transactions WHERE transaction_id=?",
                (transaction_id,),
            ).fetchone()
            if row is None or _dt(str(row["expires_at"])) <= now or row["code_digest"] is not None:
                raise IdentityError("ANDROID_AUTH_TRANSACTION_INVALID", 400)
            principal = self._identity._principal(connection, web_session, now)
            if row["client_id"] not in self._clients or row["redirect_uri"] not in self._clients[row["client_id"]]:
                raise IdentityError("ANDROID_AUTH_CLIENT_DENIED", 403)
            session = connection.execute(
                "SELECT client_kind FROM sessions WHERE session_id=?", (principal["session_id"],)
            ).fetchone()
            if session is None or session["client_kind"] != ClientKind.WEB.value:
                raise IdentityError("AUTHENTICATION_REQUIRED", 401)
            user_id = str(principal["user_id"])
            self._require_user_and_tenant(connection, user_id, selected_tenant_id)
            now = self._now()
            code = _opaque()
            code_expiry = min(now + CODE_TTL, _dt(str(row["expires_at"])))
            updated = connection.execute(
                "UPDATE android_auth_transactions SET code_digest=?,code_expires_at=?,user_id=?,"
                "tenant_id=?,authorized_at=? WHERE transaction_id=? AND code_digest IS NULL "
                "AND expires_at>?",
                (_digest(code), _iso(code_expiry), user_id, selected_tenant_id,
                 _iso(now), transaction_id, _iso(now)),
            )
            if updated.rowcount != 1:
                raise IdentityError("ANDROID_AUTH_TRANSACTION_INVALID", 400)
            redirect_uri, state = str(row["redirect_uri"]), str(row["state"])
        separator = "&" if urlsplit(redirect_uri).query else "?"
        url = f"{redirect_uri}{separator}{urlencode({'code': code, 'state': state})}"
        return AndroidAuthRedirect(redirect_uri, code, state, url)

    def exchange(
        self, transaction_id: str, code: str, code_verifier: str,
        redirect_uri: str, trace_id: str,
    ) -> SessionCredentials:
        return self.exchange_with_projection(
            transaction_id, code, code_verifier, redirect_uri, trace_id,
        ).credentials

    def exchange_with_projection(
        self, transaction_id: str, code: str, code_verifier: str,
        redirect_uri: str, trace_id: str,
    ) -> AndroidAuthExchange:
        transaction_id = _checked_text(transaction_id)
        _checked_text(trace_id)
        if (
            not isinstance(code, str) or not code or len(code) > 512
            or not isinstance(code_verifier, str)
            or _VERIFIER_TEXT.fullmatch(code_verifier) is None
            or not isinstance(redirect_uri, str)
        ):
            raise IdentityError("INVALID_INPUT", 422)
        with self._repository.transaction() as connection:
            connection.execute(
                "UPDATE android_auth_transactions SET transaction_id=transaction_id WHERE transaction_id=?",
                (transaction_id,),
            )
            now = self._now()
            row = connection.execute(
                "SELECT * FROM android_auth_transactions WHERE transaction_id=?",
                (transaction_id,),
            ).fetchone()
            if (
                row is None or row["used_at"] is not None or row["code_digest"] is None
                or _dt(str(row["expires_at"])) <= now
                or _dt(str(row["code_expires_at"])) <= now
                or redirect_uri != row["redirect_uri"]
                or row["client_id"] not in self._clients
                or redirect_uri not in self._clients[row["client_id"]]
                or not hmac.compare_digest(str(row["code_digest"]), _digest(code))
                or not hmac.compare_digest(
                    str(row["code_challenge"]),
                    base64.urlsafe_b64encode(hashlib.sha256(code_verifier.encode()).digest()).rstrip(b"=").decode(),
                )
            ):
                raise IdentityError("ANDROID_AUTH_CODE_INVALID", 400)
            user_id, tenant_id = str(row["user_id"]), str(row["tenant_id"])
            self._require_user_and_tenant(connection, user_id, tenant_id)
            now = self._now()
            updated = connection.execute(
                "UPDATE android_auth_transactions SET used_at=? WHERE transaction_id=? AND used_at IS NULL "
                "AND expires_at>? AND code_expires_at>?",
                (_iso(now), transaction_id, _iso(now), _iso(now)),
            )
            if updated.rowcount != 1:
                raise IdentityError("ANDROID_AUTH_CODE_INVALID", 400)
            workspace_id = next(
                (workspace for tenant, _, workspace in
                 self._identity._selectable_tenant_workspaces(connection, user_id)
                 if tenant == tenant_id),
                None,
            )
            if workspace_id is None:
                raise IdentityError("TENANT_UNAVAILABLE", 403)
            device_id, session_id, family_id = _id("dev"), _id("ses"), _id("fam")
            access, refresh = _opaque(), _opaque()
            access_expires_at = now + ACCESS_TTL
            connection.execute(
                "INSERT INTO devices(device_id,tenant_id,user_id,platform,state,last_seen_at,created_at,updated_at) "
                "VALUES (?,?,?,?,?,?,?,?)",
                (device_id, tenant_id, user_id, DevicePlatform.ANDROID.value, "registered",
                 _iso(now), _iso(now), _iso(now)),
            )
            connection.execute(
                "INSERT INTO sessions(session_id,tenant_id,user_id,device_id,client_kind,access_digest,"
                "access_expires_at,state,created_at,updated_at) VALUES (?,?,?,?,?,?,?,?,?,?)",
                (session_id, tenant_id, user_id, device_id, ClientKind.NATIVE.value,
                 _digest(access), _iso(access_expires_at), "active", _iso(now), _iso(now)),
            )
            connection.execute(
                "INSERT INTO refresh_families(family_id,session_id,state,created_at,updated_at) VALUES (?,?,?,?,?)",
                (family_id, session_id, "active", _iso(now), _iso(now)),
            )
            connection.execute(
                "INSERT INTO refresh_tokens(refresh_id,family_id,refresh_digest,expires_at,used_at,created_at) "
                "VALUES (?,?,?,?,?,?)",
                (_id("ref"), family_id, _digest(refresh), _iso(now + REFRESH_TTL), None, _iso(now)),
            )
        return AndroidAuthExchange(
            SessionCredentials(access, refresh, user_id, session_id, device_id,
                               tenant_id, ClientKind.NATIVE, "native_opaque_refresh_rotation"),
            workspace_id, access_expires_at,
        )

    def _require_user_and_tenant(self, connection, user_id: str, tenant_id: str) -> None:
        user = connection.execute(
            "SELECT state,email_verified_at,password_change_required,issuer FROM users WHERE user_id=?",
            (user_id,),
        ).fetchone()
        if (
            user is None or self._repository.user_state_for_api(str(user["state"])) != "active"
            or (user["issuer"] == "local" and user["email_verified_at"] is None)
            or bool(user["password_change_required"])
        ):
            raise IdentityError("AUTHENTICATION_REQUIRED", 401)
        if tenant_id not in {tenant for tenant, _, _ in self._identity._selectable_tenant_workspaces(connection, user_id)}:
            raise IdentityError("TENANT_UNAVAILABLE", 403)
