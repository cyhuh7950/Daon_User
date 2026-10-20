"""Own-account settings and a separate atomic request/audit ledger (S36)."""
from __future__ import annotations

import hashlib
import base64
import hmac
import json
import sqlite3
import unicodedata
from contextlib import contextmanager

import psycopg

from .audit import ActorType, AuditDuplicateEventError, AuditEventDraft, AuditOutcome
from .identity import IdentityError, _checked_text, _digest, _dt, _id, _iso


def install_sqlite_account_schema(connection):
    connection.executescript("""
    CREATE TABLE IF NOT EXISTS account_profiles (
      user_id TEXT PRIMARY KEY REFERENCES users(user_id), display_name TEXT NOT NULL CHECK(length(display_name) BETWEEN 1 AND 80),
      version INTEGER NOT NULL CHECK(version>0), updated_at TEXT NOT NULL
    );
    CREATE TABLE IF NOT EXISTS account_requests (
      actor_id TEXT NOT NULL, operation TEXT NOT NULL CHECK(operation IN ('profile.update','device.trust','device.revoke','session.revoke')),
      idempotency_key TEXT NOT NULL, fingerprint TEXT NOT NULL, response_json TEXT NOT NULL,
      tenant_id TEXT NOT NULL, target_id TEXT NOT NULL, event_id TEXT NOT NULL UNIQUE,
      trace_id TEXT NOT NULL, policy_version TEXT NOT NULL, created_at TEXT NOT NULL, delivered_at TEXT,
      audit_events_json TEXT NOT NULL DEFAULT '[]',
      PRIMARY KEY(actor_id,operation,idempotency_key)
    );
    CREATE TRIGGER IF NOT EXISTS account_request_immutable BEFORE UPDATE OF actor_id,operation,idempotency_key,
      fingerprint,response_json,tenant_id,target_id,event_id,trace_id,policy_version,created_at,audit_events_json ON account_requests
      BEGIN SELECT RAISE(ABORT,'ACCOUNT_REQUEST_IMMUTABLE'); END;
    CREATE TRIGGER IF NOT EXISTS account_request_delivery BEFORE UPDATE OF delivered_at ON account_requests
      WHEN OLD.delivered_at IS NOT NULL OR NEW.delivered_at IS NULL
      BEGIN SELECT RAISE(ABORT,'ACCOUNT_DELIVERY_ONE_WAY'); END;
    CREATE TRIGGER IF NOT EXISTS account_request_delete BEFORE DELETE ON account_requests
      BEGIN SELECT RAISE(ABORT,'ACCOUNT_REQUEST_DELETE_BLOCKED'); END;
    """)


def account_actor_scope(connection, actor_id):
    # The identity adapter uses a dedicated connection/transaction. Never accept this scope from a client.
    if hasattr(connection, "_connection"):
        connection.execute("SELECT set_config('daon.account_actor',?,true)", (actor_id,))


def delete_account_profile(connection, user_id):
    """Called only inside the existing authorized administrator account-deletion transaction."""
    if hasattr(connection, "_connection") and connection.execute(
        "SELECT to_regclass(?)", ("public.identity_account_profiles",),
    ).fetchone()[0] is None:
        return  # Preserve administrator deletion on a pre-0067 database.
    account_actor_scope(connection, user_id)
    connection.execute("DELETE FROM account_profiles WHERE user_id=?", (user_id,))
    account_actor_scope(connection, "")


class AccountSettingsService:
    def __init__(self, identity, *, notification_inbox_enabled=False):
        if type(notification_inbox_enabled) is not bool:
            raise ValueError("NOTIFICATION_INBOX_FLAG_INVALID")
        self.identity = identity
        self.repository = identity._repository
        self._notification_inbox_enabled = notification_inbox_enabled

    @contextmanager
    def _safe_transaction(self):
        try:
            with self.repository.transaction() as connection: yield connection
        except (sqlite3.Error, psycopg.Error) as error:
            raise IdentityError("ACCOUNT_STORAGE_UNAVAILABLE", 503) from error

    @contextmanager
    def _transaction(self, access_token):
        _checked_text(access_token, opaque=True)
        with self._safe_transaction() as connection:
            suffix = " FOR UPDATE" if hasattr(connection, "_connection") else ""
            user = connection.execute(
                "SELECT user_id,state,password_change_required FROM users WHERE user_id="
                "(SELECT user_id FROM sessions WHERE access_digest=?)" + suffix, (_digest(access_token),),
            ).fetchone()
            if user is None or user["state"] != "active": raise IdentityError("AUTHENTICATION_REQUIRED", 401)
            principal = self.identity._principal(connection, access_token, self.identity._now())
            if user["password_change_required"]: raise IdentityError("PASSWORD_CHANGE_REQUIRED", 403)
            account_actor_scope(connection, str(principal["user_id"]))
            yield connection, principal

    def get_profile(self, *, access_token, trace_id, policy_version):
        _checked_text(trace_id); _checked_text(policy_version)
        with self._transaction(access_token) as (connection, principal):
            return self._profile(connection, str(principal["user_id"]))

    def list_devices(self, *, access_token, trace_id, policy_version, limit=20, cursor=None):
        return self._list("devices", access_token, trace_id, policy_version, limit, cursor)

    def list_sessions(self, *, access_token, trace_id, policy_version, limit=20, cursor=None):
        return self._list("sessions", access_token, trace_id, policy_version, limit, cursor)

    def _cursor(self, kind, principal, after=None, encoded=None):
        binding = ["account-page-v1", kind, str(principal["tenant_id"]), str(principal["user_id"]), str(principal["session_id"])]
        secret = self.identity._step_up_token_key
        if encoded is not None:
            try:
                if not isinstance(encoded, str) or len(encoded)>2048: raise ValueError()
                digest, signature = encoded.split(".")
                material = json.dumps(binding+[digest], separators=(",", ":")).encode()
                if not hmac.compare_digest(hmac.new(secret, material, hashlib.sha256).hexdigest(), signature): raise ValueError()
                value = base64.urlsafe_b64decode(digest+"="*(-len(digest)%4)).decode()
                return _checked_text(value)
            except (ValueError, UnicodeError, IdentityError) as error:
                raise IdentityError("ACCOUNT_CURSOR_INVALID", 422) from error
        digest = base64.urlsafe_b64encode(after.encode()).decode().rstrip("=")
        material = json.dumps(binding+[digest], separators=(",", ":")).encode()
        return digest+"."+hmac.new(secret, material, hashlib.sha256).hexdigest()

    def _list(self, kind, access_token, trace_id, policy_version, limit, cursor):
        if type(limit) is not int or not 1<=limit<=100: raise IdentityError("ACCOUNT_INPUT_INVALID", 422)
        _checked_text(trace_id); _checked_text(policy_version)
        key = "device_id" if kind == "devices" else "session_id"
        fields = "device_id,platform,state,last_seen_at" if kind == "devices" else "session_id,device_id,client_kind,state,access_expires_at"
        with self._transaction(access_token) as (connection, principal):
            after = "" if cursor is None else self._cursor(kind, principal, encoded=cursor)
            rows = connection.execute(f"SELECT {fields} FROM {kind} WHERE tenant_id=? AND user_id=? AND {key}>? ORDER BY {key} LIMIT ?",
                (str(principal["tenant_id"]), str(principal["user_id"]), after, limit+1)).fetchall()
            items = []
            for row in rows[:limit]:
                item = {field: str(row[field]) for field in fields.split(",")}
                if kind == "sessions": item["expires_at"] = _iso(_dt(item.pop("access_expires_at")))
                else: item["last_seen_at"] = _iso(_dt(item["last_seen_at"]))
                item["is_current"] = item[key] == str(principal[key])
                items.append(item)
            return {"items": items, "next_cursor": self._cursor(kind, principal, after=items[-1][key]) if len(rows)>limit else None}

    @staticmethod
    def _profile(connection, actor):
        row = connection.execute("SELECT display_name,version FROM account_profiles WHERE user_id=?", (actor,)).fetchone()
        return {"display_name": None, "version": 0} if row is None else {"display_name": str(row["display_name"]), "version": int(row["version"])}

    @staticmethod
    def _replay(connection, actor, operation, key, fingerprint):
        row = connection.execute("SELECT fingerprint,response_json FROM account_requests WHERE actor_id=? AND operation=? AND idempotency_key=?", (actor, operation, key)).fetchone()
        if row is None: return None
        if row["fingerprint"] != fingerprint: raise IdentityError("IDEMPOTENCY_KEY_REUSED", 409)
        return {**json.loads(str(row["response_json"])), "replayed": True}

    def _record(self, connection, principal, operation, key, fingerprint, result, target, trace_id, policy_version, audit_events=None):
        event_id = _id("audit-account")
        connection.execute("INSERT INTO account_requests(actor_id,operation,idempotency_key,fingerprint,response_json,tenant_id,target_id,event_id,trace_id,policy_version,created_at,audit_events_json) VALUES (?,?,?,?,?,?,?,?,?,?,?,?)",
            (str(principal["user_id"]), operation, key, fingerprint, json.dumps(result, ensure_ascii=False, sort_keys=True),
             str(principal["tenant_id"]), target, event_id, trace_id, policy_version, _iso(self.identity._now()), json.dumps(audit_events or [], sort_keys=True)))
        if self._notification_inbox_enabled and operation != "profile.update" and hasattr(connection, "_connection"):
            from .notification_events import enqueue_account_event
            enqueue_account_event(connection, self.identity, principal, request_event_id=event_id)

    def trust_device(self, *, access_token, device_id, idempotency_key, trace_id, policy_version):
        return self._mutate("device.trust", access_token, device_id, None, idempotency_key, trace_id, policy_version)

    def revoke_device(self, *, access_token, device_id, step_up_authorization, idempotency_key, trace_id, policy_version):
        return self._mutate("device.revoke", access_token, device_id, step_up_authorization, idempotency_key, trace_id, policy_version)

    def revoke_session(self, *, access_token, session_id, step_up_authorization, idempotency_key, trace_id, policy_version):
        return self._mutate("session.revoke", access_token, session_id, step_up_authorization, idempotency_key, trace_id, policy_version)

    def _mutate(self, operation, access_token, target, step_up, key, trace_id, policy_version):
        _checked_text(target); _checked_text(key); _checked_text(trace_id); _checked_text(policy_version)
        with self._transaction(access_token) as (connection, principal):
            actor = str(principal["user_id"])
            # Bind replay to tenant/session/device/policy, never to a raw credential.
            fingerprint = hashlib.sha256(json.dumps([operation, target, str(principal["tenant_id"]),
                str(principal["session_id"]), str(principal["device_id"]), policy_version,
                _digest(step_up) if isinstance(step_up, str) else None], separators=(",", ":")).encode()).hexdigest()
            result = self._replay(connection, actor, operation, key, fingerprint)
            if result is None:
                events = []
                def collect(**event):
                    events.append({"action": event["action"], "target_type": event["target_type"], "target_id": event["target_id"], "metadata": event.get("metadata", {})})
                args = (connection, principal, target)
                now = self.identity._now()
                if operation == "device.trust":
                    self.identity._trust_device_in_transaction(*args, now, trace_id, policy_version, success_audit=collect)
                    result = {"device_id": target, "version": _iso(now), "replayed": False}
                elif operation == "device.revoke":
                    self.identity._revoke_device_in_transaction(*args, step_up, now, trace_id, policy_version, success_audit=collect)
                    result = {"device_id": target, "sync_key_revoke_required": True, "replayed": False}
                else:
                    self.identity._revoke_session_in_transaction(*args, step_up, now, trace_id, policy_version, success_audit=collect)
                    result = {"session_id": target, "replayed": False}
                self._record(connection, principal, operation, key, fingerprint, result, target, trace_id, policy_version, audit_events=events)
        self.dispatch_pending_audits(actor)
        return result

    def save_profile(self, *, access_token, display_name, expected_version, idempotency_key, expected_session_id, trace_id, policy_version):
        if not isinstance(display_name, str) or any(unicodedata.category(ch) == "Cc" for ch in display_name):
            raise IdentityError("ACCOUNT_INPUT_INVALID", 422)
        name = display_name.strip()
        if not 1 <= len(name) <= 80 or type(expected_version) is not int or not 0 <= expected_version < 2**53-1:
            raise IdentityError("ACCOUNT_INPUT_INVALID", 422)
        try:
            _checked_text(idempotency_key); _checked_text(trace_id); _checked_text(policy_version)
            _checked_text(expected_session_id)
            if len(expected_session_id)>128: raise IdentityError("INVALID_INPUT")
        except IdentityError as error: raise IdentityError("ACCOUNT_INPUT_INVALID", 422) from error
        fingerprint = hashlib.sha256(json.dumps([name, expected_version, expected_session_id], ensure_ascii=False, separators=(",", ":")).encode()).hexdigest()
        with self._transaction(access_token) as (connection, principal):
            if str(principal["session_id"]) != expected_session_id:
                raise IdentityError("ACCOUNT_SESSION_CHANGED", 409)
            actor = str(principal["user_id"])
            result = self._replay(connection, actor, "profile.update", idempotency_key, fingerprint)
            if result is None:
                previous = self._profile(connection, actor)
                if previous["version"] != expected_version: raise IdentityError("ACCOUNT_VERSION_CONFLICT", 412)
                result = {"display_name": name, "version": expected_version+1, "replayed": False}
                if expected_version == 0:
                    connection.execute("INSERT INTO account_profiles(user_id,display_name,version,updated_at) VALUES (?,?,?,?)", (actor, name, 1, _iso(self.identity._now())))
                else:
                    changed = connection.execute("UPDATE account_profiles SET display_name=?,version=?,updated_at=? WHERE user_id=? AND version=?", (name, expected_version+1, _iso(self.identity._now()), actor, expected_version))
                    if changed.rowcount != 1: raise IdentityError("ACCOUNT_VERSION_CONFLICT", 412)
                self._record(connection, principal, "profile.update", idempotency_key, fingerprint, result, actor, trace_id, policy_version)
        self.dispatch_pending_audits(actor)
        return result

    def dispatch_pending_audits(self, actor_id=None):
        try:
            with self.repository.transaction() as connection:
                # Cross-actor background dispatch is a narrow DB function; no profile or request body is returned.
                if hasattr(connection, "_connection"):
                    rows = connection.execute("SELECT * FROM pending_identity_account_audits(?)", (actor_id,)).fetchall()
                else:
                    rows = connection.execute("SELECT * FROM account_requests WHERE delivered_at IS NULL AND (? IS NULL OR actor_id=?) ORDER BY created_at,event_id LIMIT 32", (actor_id, actor_id)).fetchall()
        except (IdentityError, sqlite3.Error, psycopg.Error): return
        for row in rows:
            try:
                events = json.loads(str(row["audit_events_json"]))
                if not events:
                    events = [{"action": "identity.account.profile_updated", "target_type": "user", "target_id": str(row["target_id"]), "metadata": {}}]
                for index, event in enumerate(events):
                    try:
                        self.identity._audit_store.append(AuditEventDraft(event_id=str(row["event_id"])+"-"+str(index), occurred_at=_dt(str(row["created_at"])),
                    actor_id=str(row["actor_id"]), actor_type=ActorType.USER, tenant_id=str(row["tenant_id"]), workspace_id=None,
                    action=event["action"], target_type=event["target_type"], target_id=event["target_id"],
                    outcome=AuditOutcome.SUCCEEDED, trace_id=str(row["trace_id"]), policy_version=str(row["policy_version"]), metadata=event["metadata"]))
                    except AuditDuplicateEventError: pass
            except Exception: continue
            try:
                with self.repository.transaction() as connection:
                    account_actor_scope(connection, str(row["actor_id"]))
                    connection.execute("UPDATE account_requests SET delivered_at=? WHERE event_id=? AND delivered_at IS NULL", (_iso(self.identity._now()), str(row["event_id"])))
            except (IdentityError, sqlite3.Error, psycopg.Error): continue
