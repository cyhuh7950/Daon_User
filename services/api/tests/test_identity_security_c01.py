from __future__ import annotations

import hashlib
import sqlite3
import tempfile
import threading
import unittest
from contextlib import contextmanager
from pathlib import Path

from test_identity_support import (
    FakeVerifiedOidcProvider,
    POLICY_VERSION,
    TRACE_ID,
    create_service,
    native_login,
    policy,
)
from daon_user_api.audit import AuditEventStore
from daon_user_api.identity import (
    ClientKind,
    DevicePlatform,
    IdentityError,
    OidcClientPolicy,
)


class FailOnActionAuditStore:
    def __init__(self) -> None:
        self.backing = AuditEventStore()
        self.fail_actions: set[str] = set()

    def append(self, draft: object) -> object:
        if getattr(draft, "action", None) in self.fail_actions:
            raise RuntimeError("sensitive raw failure")
        return self.backing.append(draft)  # type: ignore[arg-type]


def event_actions(audit: AuditEventStore, tenant_id: str) -> list[str]:
    return [event.action for event in audit.list(tenant_id=tenant_id, limit=200).items]


def tenant_two_policy() -> OidcClientPolicy:
    return OidcClientPolicy(
        issuer="https://login.example.com",
        client_id="daon-native-tenant-two",
        audience="daon-user-api",
        redirect_uris=frozenset({"com.sinsan.daon:/oidc/callback-two"}),
        client_kind=ClientKind.NATIVE,
        tenant_id="tenant-002",
    )


def tenant_two_login(service):
    start = service.begin_oidc_login(
        issuer="https://login.example.com",
        client_id="daon-native-tenant-two",
        audience="daon-user-api",
        redirect_uri="com.sinsan.daon:/oidc/callback-two",
        client_kind=ClientKind.NATIVE,
        tenant_id="tenant-002",
        trace_id=TRACE_ID,
        policy_version=POLICY_VERSION,
    )
    provider = FakeVerifiedOidcProvider(subject="tenant-two-subject")
    provider.expected_nonce = start.nonce
    return service.complete_oidc_login(
        state=start.state,
        authorization_code=provider.authorization_code,
        code_verifier=start.code_verifier,
        client_id="daon-native-tenant-two",
        redirect_uri="com.sinsan.daon:/oidc/callback-two",
        provider=provider,
        platform=DevicePlatform.ANDROID,
        trace_id=TRACE_ID,
        policy_version=POLICY_VERSION,
    )


class IdentitySecurityC01Tests(unittest.TestCase):
    def test_web_self_logout_revokes_only_current_session_and_replays_safely(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            service, _, audit, _ = create_service(Path(directory) / "identity.sqlite3")
            start = service.begin_oidc_login(
                issuer="https://login.example.com", client_id="daon-web",
                audience="daon-user-api", redirect_uri="https://app.example.com/auth/callback",
                client_kind=ClientKind.WEB, tenant_id="tenant-001",
                trace_id=TRACE_ID, policy_version=POLICY_VERSION,
            )
            provider = FakeVerifiedOidcProvider()
            provider.expected_nonce = start.nonce
            current = service.complete_oidc_login(
                state=start.state, authorization_code=provider.authorization_code,
                code_verifier=start.code_verifier, client_id="daon-web",
                redirect_uri="https://app.example.com/auth/callback", provider=provider,
                platform=DevicePlatform.WEB, trace_id=TRACE_ID,
                policy_version=POLICY_VERSION,
            )
            other = native_login(service)

            first = service.revoke_current_web_session(
                access_token=current.access_token, trace_id="trace-self-logout-1",
                policy_version=POLICY_VERSION,
            )
            replay = service.revoke_current_web_session(
                access_token=current.access_token, trace_id="trace-self-logout-2",
                policy_version=POLICY_VERSION,
            )

            self.assertFalse(first.replayed)
            self.assertTrue(replay.replayed)
            with self.assertRaises(IdentityError) as revoked:
                service.validate_access(current.access_token, trace_id=TRACE_ID, policy_version=POLICY_VERSION)
            self.assertEqual(revoked.exception.code, "SESSION_REVOKED")
            service.validate_access(other.access_token, trace_id=TRACE_ID, policy_version=POLICY_VERSION)
            self.assertEqual(event_actions(audit, "tenant-001").count("identity.session.self_revoked"), 1)

    def test_web_self_logout_central_audit_failure_keeps_durable_intent_and_restart_retries(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            db_path = Path(directory) / "identity.sqlite3"
            controlled_audit = FailOnActionAuditStore()
            service, _, _, clock = create_service(db_path, audit_store=controlled_audit)
            start = service.begin_oidc_login(
                issuer="https://login.example.com", client_id="daon-web",
                audience="daon-user-api", redirect_uri="https://app.example.com/auth/callback",
                client_kind=ClientKind.WEB, tenant_id="tenant-001",
                trace_id=TRACE_ID, policy_version=POLICY_VERSION,
            )
            provider = FakeVerifiedOidcProvider()
            provider.expected_nonce = start.nonce
            current = service.complete_oidc_login(
                state=start.state, authorization_code=provider.authorization_code,
                code_verifier=start.code_verifier, client_id="daon-web",
                redirect_uri="https://app.example.com/auth/callback", provider=provider,
                platform=DevicePlatform.WEB, trace_id=TRACE_ID,
                policy_version=POLICY_VERSION,
            )
            controlled_audit.fail_actions.add("identity.session.self_revoked")
            event = service.revoke_current_web_session(
                access_token=current.access_token, trace_id="trace-self-logout-failed",
                policy_version=POLICY_VERSION,
            )
            self.assertFalse(event.replayed)
            with self.assertRaises(IdentityError) as revoked:
                service.validate_access(current.access_token, trace_id=TRACE_ID, policy_version=POLICY_VERSION)
            self.assertEqual(revoked.exception.code, "SESSION_REVOKED")
            connection = sqlite3.connect(db_path)
            try:
                self.assertEqual(connection.execute("SELECT COUNT(*) FROM session_audit_outbox WHERE delivered_at IS NULL").fetchone()[0], 1)
                columns = {str(row[1]) for row in connection.execute("PRAGMA table_info(session_audit_outbox)")}
                self.assertTrue(columns.isdisjoint({"access_digest", "cookie", "token", "credential", "password"}))
                outbox_text = repr(connection.execute("SELECT * FROM session_audit_outbox").fetchall())
                self.assertNotIn(current.access_token, outbox_text)
                self.assertNotIn(hashlib.sha256(current.access_token.encode()).hexdigest(), outbox_text)
            finally:
                connection.close()
            controlled_audit.fail_actions.clear()
            restarted, _, _, _ = create_service(db_path, clock=clock, audit_store=controlled_audit)
            self.assertEqual(event_actions(controlled_audit.backing, "tenant-001").count("identity.session.self_revoked"), 1)
            connection = sqlite3.connect(db_path)
            try:
                self.assertEqual(connection.execute("SELECT COUNT(*) FROM session_audit_outbox").fetchone()[0], 1)
                self.assertEqual(connection.execute("SELECT COUNT(*) FROM session_audit_outbox WHERE delivered_at IS NOT NULL").fetchone()[0], 1)
            finally:
                connection.close()

    def test_web_self_logout_outbox_is_immutable_and_delivery_is_one_way(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            db_path = Path(directory) / "identity.sqlite3"
            service, _, _, _ = create_service(db_path)
            current = self._web_login(service)
            service.revoke_current_web_session(
                access_token=current.access_token, trace_id=TRACE_ID, policy_version=POLICY_VERSION,
            )
            connection = sqlite3.connect(db_path)
            try:
                with self.assertRaises(sqlite3.IntegrityError):
                    connection.execute("UPDATE session_audit_outbox SET trace_id='changed'")
                with self.assertRaises(sqlite3.IntegrityError):
                    connection.execute("UPDATE session_audit_outbox SET delivered_at=NULL")
                with self.assertRaises(sqlite3.IntegrityError):
                    connection.execute("DELETE FROM session_audit_outbox")
                self.assertEqual(connection.execute("SELECT COUNT(*) FROM session_audit_outbox").fetchone()[0], 1)
            finally:
                connection.close()

    def test_web_self_logout_dispatcher_is_bounded_concurrent_and_poison_safe(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            db_path = Path(directory) / "identity.sqlite3"
            controlled_audit = FailOnActionAuditStore()
            service, _, _, clock = create_service(db_path, audit_store=controlled_audit)
            current = self._web_login(service)
            self._web_login(service)
            controlled_audit.fail_actions.add("identity.session.self_revoked")
            service.revoke_current_web_session(
                access_token=current.access_token, trace_id=TRACE_ID, policy_version=POLICY_VERSION,
            )
            restarted, _, _, _ = create_service(db_path, clock=clock, audit_store=controlled_audit)
            concurrent, _, _, _ = create_service(db_path, clock=clock, audit_store=controlled_audit)
            controlled_audit.fail_actions.clear()
            threads = [
                threading.Thread(target=restarted.dispatch_pending_self_logout_audits),
                threading.Thread(target=concurrent.dispatch_pending_self_logout_audits),
            ]
            for thread in threads:
                thread.start()
            for thread in threads:
                thread.join(timeout=2)
                self.assertFalse(thread.is_alive())
            self.assertEqual(event_actions(controlled_audit.backing, "tenant-001").count("identity.session.self_revoked"), 1)
            connection = sqlite3.connect(db_path)
            try:
                session_id, tenant_id, actor_id = connection.execute(
                    "SELECT session_id,tenant_id,user_id FROM sessions WHERE session_id<>? LIMIT 1",
                    (current.session_id,),
                ).fetchone()
                connection.execute(
                    "INSERT INTO session_audit_outbox(event_id,session_id,action,tenant_id,actor_id,occurred_at,trace_id,policy_version,delivered_at,created_at) "
                    "VALUES (?,?,?,?,?,?,?,?,NULL,?)",
                    ("audit-poison", session_id, "identity.session.self_revoked", tenant_id, actor_id,
                     "not-a-timestamp", TRACE_ID, POLICY_VERSION, "2026-08-20T00:00:00Z"),
                )
                connection.commit()
            finally:
                connection.close()
            restarted.dispatch_pending_self_logout_audits()
            connection = sqlite3.connect(db_path)
            try:
                self.assertEqual(connection.execute(
                    "SELECT COUNT(*) FROM session_audit_outbox WHERE event_id='audit-poison' AND delivered_at IS NULL"
                ).fetchone()[0], 1)
            finally:
                connection.close()

    def test_web_self_logout_outbox_insert_failure_rolls_back_session_and_refresh(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            db_path = Path(directory) / "identity.sqlite3"
            service, _, audit, _ = create_service(db_path)
            current = self._web_login(service)
            connection = sqlite3.connect(db_path)
            try:
                connection.execute("CREATE TRIGGER fail_self_logout_outbox BEFORE INSERT ON session_audit_outbox BEGIN SELECT RAISE(ABORT, 'blocked'); END")
                connection.commit()
            finally:
                connection.close()
            with self.assertRaises(IdentityError) as failed:
                service.revoke_current_web_session(access_token=current.access_token, trace_id=TRACE_ID, policy_version=POLICY_VERSION)
            self.assertEqual(failed.exception.code, "PERSISTENCE_UNAVAILABLE")
            service.validate_access(current.access_token, trace_id=TRACE_ID, policy_version=POLICY_VERSION)
            self.assertEqual(event_actions(audit, "tenant-001").count("identity.session.self_revoked"), 0)

    def test_web_self_logout_commit_failure_has_session_outbox_and_central_write0(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            db_path = Path(directory) / "identity.sqlite3"
            service, repository, audit, _ = create_service(db_path)
            current = self._web_login(service)
            original_transaction = repository.transaction

            @contextmanager
            def failing_transaction():
                with original_transaction() as connection:
                    yield connection
                    raise sqlite3.OperationalError("forced pre-commit failure")

            repository.transaction = failing_transaction  # type: ignore[method-assign]
            with self.assertRaises(IdentityError) as failed:
                service.revoke_current_web_session(access_token=current.access_token, trace_id=TRACE_ID, policy_version=POLICY_VERSION)
            self.assertEqual(failed.exception.code, "PERSISTENCE_UNAVAILABLE")
            repository.transaction = original_transaction  # type: ignore[method-assign]
            service.validate_access(current.access_token, trace_id=TRACE_ID, policy_version=POLICY_VERSION)
            connection = sqlite3.connect(db_path)
            try:
                self.assertEqual(connection.execute("SELECT COUNT(*) FROM session_audit_outbox").fetchone()[0], 0)
            finally:
                connection.close()
            self.assertEqual(event_actions(audit, "tenant-001").count("identity.session.self_revoked"), 0)

    @staticmethod
    def _web_login(service):
        start = service.begin_oidc_login(
            issuer="https://login.example.com", client_id="daon-web",
            audience="daon-user-api", redirect_uri="https://app.example.com/auth/callback",
            client_kind=ClientKind.WEB, tenant_id="tenant-001",
            trace_id=TRACE_ID, policy_version=POLICY_VERSION,
        )
        provider = FakeVerifiedOidcProvider()
        provider.expected_nonce = start.nonce
        return service.complete_oidc_login(
            state=start.state, authorization_code=provider.authorization_code,
            code_verifier=start.code_verifier, client_id="daon-web",
            redirect_uri="https://app.example.com/auth/callback", provider=provider,
            platform=DevicePlatform.WEB, trace_id=TRACE_ID, policy_version=POLICY_VERSION,
        )

    def test_session_revoke_requires_bound_step_up_and_revokes_access_refresh(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            service, _, audit, _ = create_service(Path(directory) / "identity.sqlite3")
            actor = native_login(service)
            target = native_login(service)

            with self.assertRaises(IdentityError) as missing:
                service.revoke_session(
                    access_token=actor.access_token,
                    session_id=target.session_id,
                    step_up_authorization=None,
                    trace_id=TRACE_ID,
                    policy_version=POLICY_VERSION,
                )
            self.assertEqual(missing.exception.code, "STEP_UP_REQUIRED")
            service.validate_access(target.access_token, trace_id=TRACE_ID, policy_version=POLICY_VERSION)

            wrong = service.issue_step_up(
                access_token=actor.access_token,
                action_group="device_session_or_sync_key_revoke",
                target_id=actor.session_id,
                trace_id=TRACE_ID,
                policy_version=POLICY_VERSION,
            )
            with self.assertRaises(IdentityError) as binding:
                service.revoke_session(
                    access_token=actor.access_token,
                    session_id=target.session_id,
                    step_up_authorization=wrong.authorization,
                    trace_id=TRACE_ID,
                    policy_version=POLICY_VERSION,
                )
            self.assertEqual(binding.exception.code, "STEP_UP_BINDING_DENIED")

            grant = service.issue_step_up(
                access_token=actor.access_token,
                action_group="device_session_or_sync_key_revoke",
                target_id=target.session_id,
                trace_id=TRACE_ID,
                policy_version=POLICY_VERSION,
            )
            event = service.revoke_session(
                access_token=actor.access_token,
                session_id=target.session_id,
                step_up_authorization=grant.authorization,
                trace_id=TRACE_ID,
                policy_version=POLICY_VERSION,
            )
            self.assertEqual(event.session_id, target.session_id)
            with self.assertRaises(IdentityError) as access_revoked:
                service.validate_access(target.access_token, trace_id=TRACE_ID, policy_version=POLICY_VERSION)
            self.assertEqual((access_revoked.exception.code, access_revoked.exception.http_status), ("SESSION_REVOKED", 401))
            with self.assertRaises(IdentityError) as refresh_revoked:
                service.rotate_refresh(target.refresh_token, trace_id=TRACE_ID, policy_version=POLICY_VERSION)
            self.assertEqual((refresh_revoked.exception.code, refresh_revoked.exception.http_status), ("SESSION_REVOKED", 401))
            self.assertIn("identity.session.revoked", event_actions(audit, "tenant-001"))

    def test_session_revoke_hides_cross_tenant_and_missing_targets(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            service, _, _, _ = create_service(
                Path(directory) / "identity.sqlite3",
                policies=(policy(), tenant_two_policy()),
            )
            actor = native_login(service)
            foreign = tenant_two_login(service)
            for target_id in (foreign.session_id, "ses-missing-safe-target"):
                grant = service.issue_step_up(
                    access_token=actor.access_token,
                    action_group="device_session_or_sync_key_revoke",
                    target_id=target_id,
                    trace_id=TRACE_ID,
                    policy_version=POLICY_VERSION,
                )
                with self.assertRaises(IdentityError) as denied:
                    service.revoke_session(
                        access_token=actor.access_token,
                        session_id=target_id,
                        step_up_authorization=grant.authorization,
                        trace_id=TRACE_ID,
                        policy_version=POLICY_VERSION,
                    )
                self.assertEqual((denied.exception.code, denied.exception.http_status), ("SESSION_TARGET_UNAVAILABLE", 404))
                self.assertNotIn(target_id, str(denied.exception))
            service.validate_access(foreign.access_token, trace_id=TRACE_ID, policy_version=POLICY_VERSION)

    def test_access_refresh_invalid_expired_revoked_are_audited_without_credential_leak(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            db_path = Path(directory) / "identity.sqlite3"
            service, _, audit, clock = create_service(db_path)
            credentials = native_login(service)
            invalid_access = "invalid-access-material-that-must-not-be-recorded-001"
            invalid_refresh = "invalid-refresh-material-that-must-not-be-recorded-001"

            with self.assertRaises(IdentityError) as invalid_access_error:
                service.validate_access(invalid_access, trace_id="trace-invalid-access", policy_version=POLICY_VERSION)
            self.assertEqual(invalid_access_error.exception.code, "ACCESS_INVALID")
            with self.assertRaises(IdentityError) as invalid_refresh_error:
                service.rotate_refresh(invalid_refresh, trace_id="trace-invalid-refresh", policy_version=POLICY_VERSION)
            self.assertEqual(invalid_refresh_error.exception.code, "REFRESH_INVALID")
            with self.assertRaises(IdentityError) as malformed_access_error:
                service.validate_access(None, trace_id="trace-malformed-access", policy_version=POLICY_VERSION)  # type: ignore[arg-type]
            self.assertEqual((malformed_access_error.exception.code, malformed_access_error.exception.http_status), ("ACCESS_INVALID", 401))
            with self.assertRaises(IdentityError) as missing_refresh_error:
                service.rotate_refresh(None, trace_id="trace-missing-refresh", policy_version=POLICY_VERSION)
            self.assertEqual((missing_refresh_error.exception.code, missing_refresh_error.exception.http_status), ("REFRESH_INVALID", 401))

            clock.advance(hours=2)
            with self.assertRaises(IdentityError) as expired_access:
                service.validate_access(credentials.access_token, trace_id="trace-expired-access", policy_version=POLICY_VERSION)
            self.assertEqual(expired_access.exception.code, "ACCESS_EXPIRED")
            clock.advance(days=31)
            with self.assertRaises(IdentityError) as expired_refresh:
                service.rotate_refresh(credentials.refresh_token, trace_id="trace-expired-refresh", policy_version=POLICY_VERSION)
            self.assertEqual(expired_refresh.exception.code, "REFRESH_EXPIRED")

            public_events = audit.list(tenant_id="identity-public", limit=200).items
            tenant_events = audit.list(tenant_id="tenant-001", limit=200).items
            self.assertIn("identity.access.denied", {event.action for event in public_events})
            self.assertIn("identity.refresh.denied", {event.action for event in public_events})
            self.assertIn("identity.access.expired", {event.action for event in tenant_events})
            self.assertIn("identity.refresh.expired", {event.action for event in tenant_events})
            audit_text = repr(public_events + tenant_events)
            for raw in (invalid_access, invalid_refresh, credentials.access_token, credentials.refresh_token):
                self.assertNotIn(raw, audit_text)
                self.assertNotIn(hashlib.sha256(raw.encode()).hexdigest(), audit_text)
                self.assertNotIn(raw.encode(), db_path.read_bytes())
            self.assertNotIn(invalid_access.encode(), db_path.read_bytes())
            self.assertNotIn(hashlib.sha256(invalid_access.encode()).hexdigest().encode(), db_path.read_bytes())

    def test_trust_success_binding_denied_and_audit_failure_rollback(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            db_path = Path(directory) / "identity.sqlite3"
            controlled_audit = FailOnActionAuditStore()
            service, repository, _, _ = create_service(db_path, audit_store=controlled_audit)
            actor = native_login(service)
            other = native_login(service)
            service.trust_device(
                access_token=actor.access_token,
                device_id=actor.device_id,
                trace_id=TRACE_ID,
                policy_version=POLICY_VERSION,
            )
            self.assertEqual(repository.device_state(actor.device_id), "trusted")
            with self.assertRaises(IdentityError) as binding:
                service.trust_device(
                    access_token=actor.access_token,
                    device_id=other.device_id,
                    trace_id=TRACE_ID,
                    policy_version=POLICY_VERSION,
                )
            self.assertEqual(binding.exception.code, "DEVICE_BINDING_DENIED")
            actions = event_actions(controlled_audit.backing, "tenant-001")
            self.assertIn("identity.device.trusted", actions)
            self.assertIn("identity.device.trust_denied", actions)

            controlled_audit.fail_actions.add("identity.device.trusted")
            with self.assertRaises(IdentityError) as audit_failure:
                service.trust_device(
                    access_token=other.access_token,
                    device_id=other.device_id,
                    trace_id=TRACE_ID,
                    policy_version=POLICY_VERSION,
                )
            self.assertEqual(audit_failure.exception.code, "AUDIT_WRITE_FAILED")
            self.assertEqual(repository.device_state(other.device_id), "registered")

    def test_session_revoke_audit_failure_rolls_back_and_authorization_is_reusable(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            controlled_audit = FailOnActionAuditStore()
            service, _, _, _ = create_service(
                Path(directory) / "identity.sqlite3", audit_store=controlled_audit
            )
            actor = native_login(service)
            target = native_login(service)
            grant = service.issue_step_up(
                access_token=actor.access_token,
                action_group="device_session_or_sync_key_revoke",
                target_id=target.session_id,
                trace_id=TRACE_ID,
                policy_version=POLICY_VERSION,
            )
            controlled_audit.fail_actions.add("identity.session.revoked")
            with self.assertRaises(IdentityError) as failed:
                service.revoke_session(
                    access_token=actor.access_token,
                    session_id=target.session_id,
                    step_up_authorization=grant.authorization,
                    trace_id=TRACE_ID,
                    policy_version=POLICY_VERSION,
                )
            self.assertEqual(failed.exception.code, "AUDIT_WRITE_FAILED")
            service.validate_access(target.access_token, trace_id=TRACE_ID, policy_version=POLICY_VERSION)
            controlled_audit.fail_actions.clear()
            service.revoke_session(
                access_token=actor.access_token,
                session_id=target.session_id,
                step_up_authorization=grant.authorization,
                trace_id=TRACE_ID,
                policy_version=POLICY_VERSION,
            )

    def test_invalid_access_audit_failure_is_fail_closed_without_state_change(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            controlled_audit = FailOnActionAuditStore()
            service, _, _, _ = create_service(
                Path(directory) / "identity.sqlite3", audit_store=controlled_audit
            )
            credentials = native_login(service)
            controlled_audit.fail_actions.add("identity.access.denied")
            with self.assertRaises(IdentityError) as failure:
                service.validate_access(
                    "invalid-access-for-audit-failure-001",
                    trace_id=TRACE_ID,
                    policy_version=POLICY_VERSION,
                )
            self.assertEqual((failure.exception.code, failure.exception.http_status), ("AUDIT_WRITE_FAILED", 503))
            controlled_audit.fail_actions.clear()
            service.validate_access(credentials.access_token, trace_id=TRACE_ID, policy_version=POLICY_VERSION)

    def test_concurrent_target_refresh_and_session_revoke_converge_revoked(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            service, _, _, _ = create_service(Path(directory) / "identity.sqlite3")
            actor = native_login(service)
            target = native_login(service)
            grant = service.issue_step_up(
                access_token=actor.access_token,
                action_group="device_session_or_sync_key_revoke",
                target_id=target.session_id,
                trace_id=TRACE_ID,
                policy_version=POLICY_VERSION,
            )
            barrier = threading.Barrier(3)
            rotated = []
            outcomes: list[str] = []

            def rotate() -> None:
                barrier.wait()
                try:
                    rotated.append(service.rotate_refresh(target.refresh_token, trace_id="trace-race-refresh", policy_version=POLICY_VERSION))
                    outcomes.append("rotated")
                except IdentityError as error:
                    outcomes.append(error.code)

            def revoke() -> None:
                barrier.wait()
                try:
                    service.revoke_session(
                        access_token=actor.access_token,
                        session_id=target.session_id,
                        step_up_authorization=grant.authorization,
                        trace_id="trace-race-revoke",
                        policy_version=POLICY_VERSION,
                    )
                    outcomes.append("revoked")
                except IdentityError as error:
                    outcomes.append(error.code)

            threads = [threading.Thread(target=rotate), threading.Thread(target=revoke)]
            for thread in threads:
                thread.start()
            barrier.wait()
            for thread in threads:
                thread.join(timeout=10)
            self.assertIn("revoked", outcomes)
            self.assertTrue(set(outcomes).issubset({"revoked", "rotated", "SESSION_REVOKED"}))
            credentials_to_check = [target, *rotated]
            for credentials in credentials_to_check:
                with self.assertRaises(IdentityError) as access:
                    service.validate_access(credentials.access_token, trace_id=TRACE_ID, policy_version=POLICY_VERSION)
                self.assertEqual(access.exception.http_status, 401)
                self.assertIn(access.exception.code, {"SESSION_REVOKED", "ACCESS_INVALID"})
                with self.assertRaises(IdentityError) as refresh:
                    service.rotate_refresh(credentials.refresh_token, trace_id=TRACE_ID, policy_version=POLICY_VERSION)
                self.assertEqual(refresh.exception.http_status, 401)
                self.assertIn(refresh.exception.code, {"SESSION_REVOKED", "REFRESH_REPLAYED"})


if __name__ == "__main__":
    unittest.main()
