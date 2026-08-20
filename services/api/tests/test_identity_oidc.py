from __future__ import annotations

import hashlib
import sqlite3
import tempfile
import unittest
from datetime import timedelta
from pathlib import Path

from test_identity_support import (
    FakeVerifiedOidcProvider,
    POLICY_VERSION,
    TRACE_ID,
    create_service,
    native_login,
)

from daon_user_api.identity import ClientKind, DevicePlatform, IdentityError


class IdentityOidcTests(unittest.TestCase):
    def test_pkce_state_nonce_login_and_restart_persistence(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            db_path = Path(directory) / "identity.sqlite3"
            service, repository, audit, clock = create_service(db_path)
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
            self.assertEqual(start.code_challenge_method, "S256")
            self.assertGreaterEqual(len(start.state), 40)
            self.assertGreaterEqual(len(start.nonce), 40)
            self.assertGreaterEqual(len(start.code_verifier), 43)
            self.assertEqual(
                hashlib.sha256(start.state.encode()).hexdigest(),
                repository.oidc_state_digest(start.transaction_id),
            )
            raw_db = db_path.read_bytes()
            for raw_value in (start.state, start.nonce, start.code_verifier):
                self.assertNotIn(raw_value.encode(), raw_db)

            provider = FakeVerifiedOidcProvider()
            provider.expected_nonce = start.nonce
            credentials = service.complete_oidc_login(
                state=start.state,
                authorization_code=provider.authorization_code,
                code_verifier=start.code_verifier,
                client_id="daon-native",
                redirect_uri="com.sinsan.daon:/oidc/callback",
                provider=provider,
                platform=DevicePlatform.ANDROID,
                trace_id=TRACE_ID,
                policy_version=POLICY_VERSION,
            )
            self.assertIsNotNone(credentials.refresh_token)
            self.assertNotIn(credentials.access_token.encode(), db_path.read_bytes())
            self.assertNotIn(credentials.refresh_token.encode(), db_path.read_bytes())
            repository.close()

            restarted, restarted_repository, _, _ = create_service(
                db_path, clock=clock, audit_store=audit
            )
            principal = restarted.validate_access(
                credentials.access_token,
                trace_id="trace-restart-001",
                policy_version=POLICY_VERSION,
            )
            self.assertEqual(principal.user_id, credentials.user_id)
            actions = {event.action for event in audit.list(tenant_id="tenant-001").items}
            self.assertTrue({"identity.login.started", "identity.login.succeeded"}.issubset(actions))
            self.assertEqual(restarted_repository.schema_version(), 3)
            self.assertEqual(restarted_repository.foreign_keys_enabled(), 1)
            self.assertEqual(restarted_repository.journal_mode().casefold(), "wal")

    def test_oidc_mismatch_expiry_reuse_and_unverified_claims_fail_close(self) -> None:
        cases = (
            ("wrong_state", "OIDC_STATE_INVALID"),
            ("wrong_verifier", "OIDC_PKCE_INVALID"),
            ("wrong_nonce", "OIDC_PROVIDER_REJECTED"),
            ("wrong_issuer", "OIDC_CLAIMS_INVALID"),
            ("unverified", "OIDC_CLAIMS_INVALID"),
            ("expired_claim", "OIDC_CLAIMS_EXPIRED"),
        )
        for case, expected_code in cases:
            with self.subTest(case=case), tempfile.TemporaryDirectory() as directory:
                service, _, audit, clock = create_service(Path(directory) / "identity.sqlite3")
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
                provider = FakeVerifiedOidcProvider(
                    override_nonce="wrong-nonce" if case == "wrong_nonce" else None,
                    override_issuer="https://wrong.example.com" if case == "wrong_issuer" else None,
                    verification_complete=case != "unverified",
                    expired=case == "expired_claim",
                )
                provider.expected_nonce = start.nonce
                with self.assertRaises(IdentityError) as context:
                    service.complete_oidc_login(
                        state="wrong-state" if case == "wrong_state" else start.state,
                        authorization_code=provider.authorization_code,
                        code_verifier="wrong-verifier" if case == "wrong_verifier" else start.code_verifier,
                        client_id="daon-native",
                        redirect_uri="com.sinsan.daon:/oidc/callback",
                        provider=provider,
                        platform=DevicePlatform.ANDROID,
                        trace_id=TRACE_ID,
                        policy_version=POLICY_VERSION,
                    )
                self.assertEqual(context.exception.code, expected_code)
                self.assertNotIn("wrong", str(context.exception))
                self.assertTrue(audit.verify_integrity().valid)
                self.assertIn(
                    "identity.login.denied",
                    {event.action for event in audit.list(tenant_id="tenant-001").items}
                    | {event.action for event in audit.list(tenant_id="identity-public").items},
                )

        with tempfile.TemporaryDirectory() as directory:
            service, _, _, clock = create_service(Path(directory) / "identity.sqlite3")
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
            clock.advance(minutes=6)
            provider = FakeVerifiedOidcProvider()
            provider.expected_nonce = start.nonce
            with self.assertRaises(IdentityError) as expired:
                service.complete_oidc_login(
                    state=start.state,
                    authorization_code=provider.authorization_code,
                    code_verifier=start.code_verifier,
                    client_id="daon-native",
                    redirect_uri="com.sinsan.daon:/oidc/callback",
                    provider=provider,
                    platform=DevicePlatform.ANDROID,
                    trace_id=TRACE_ID,
                    policy_version=POLICY_VERSION,
                )
            self.assertEqual(expired.exception.code, "OIDC_TRANSACTION_EXPIRED")

    def test_policy_allowlist_and_parameterized_persistence_fail_safely(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            db_path = Path(directory) / "identity.sqlite3"
            service, repository, _, _ = create_service(db_path)
            with self.assertRaises(IdentityError) as policy_error:
                service.begin_oidc_login(
                    issuer="https://login.example.com",
                    client_id="daon-native",
                    audience="daon-user-api",
                    redirect_uri="https://unapproved.example.com/callback",
                    client_kind=ClientKind.NATIVE,
                    tenant_id="tenant-001",
                    trace_id=TRACE_ID,
                    policy_version=POLICY_VERSION,
                )
            self.assertEqual(policy_error.exception.code, "OIDC_POLICY_DENIED")

            with self.assertRaises(IdentityError) as injection:
                service.begin_oidc_login(
                    issuer="https://login.example.com",
                    client_id="daon-native",
                    audience="daon-user-api",
                    redirect_uri="com.sinsan.daon:/oidc/callback",
                    client_kind=ClientKind.NATIVE,
                    tenant_id="tenant-x'); DROP TABLE users;--",
                    trace_id=TRACE_ID,
                    policy_version=POLICY_VERSION,
                )
            self.assertEqual(injection.exception.code, "INVALID_INPUT")
            connection = sqlite3.connect(db_path)
            try:
                self.assertIsNotNone(
                    connection.execute(
                        "SELECT name FROM sqlite_master WHERE type = ? AND name = ?",
                        ("table", "users"),
                    ).fetchone()
                )
            finally:
                connection.close()

            repository.close()
            with self.assertRaises(IdentityError) as persistence:
                service.begin_oidc_login(
                    issuer="https://login.example.com",
                    client_id="daon-native",
                    audience="daon-user-api",
                    redirect_uri="com.sinsan.daon:/oidc/callback",
                    client_kind=ClientKind.NATIVE,
                    tenant_id="tenant-001",
                    trace_id=TRACE_ID,
                    policy_version=POLICY_VERSION,
                )
            self.assertEqual(persistence.exception.code, "PERSISTENCE_UNAVAILABLE")
            self.assertNotIn(str(db_path), str(persistence.exception))

    def test_web_session_has_opaque_server_session_semantics_without_refresh(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            service, _, _, _ = create_service(Path(directory) / "identity.sqlite3")
            start = service.begin_oidc_login(
                issuer="https://login.example.com",
                client_id="daon-web",
                audience="daon-user-api",
                redirect_uri="https://app.example.com/auth/callback",
                client_kind=ClientKind.WEB,
                tenant_id="tenant-001",
                trace_id=TRACE_ID,
                policy_version=POLICY_VERSION,
            )
            provider = FakeVerifiedOidcProvider(subject="subject-web")
            provider.expected_nonce = start.nonce
            credentials = service.complete_oidc_login(
                state=start.state,
                authorization_code=provider.authorization_code,
                code_verifier=start.code_verifier,
                client_id="daon-web",
                redirect_uri="https://app.example.com/auth/callback",
                provider=provider,
                platform=DevicePlatform.WEB,
                trace_id=TRACE_ID,
                policy_version=POLICY_VERSION,
            )
            self.assertEqual(credentials.client_kind, ClientKind.WEB)
            self.assertEqual(credentials.delivery, "web_session_cookie_boundary_m4_05")
            self.assertIsNone(credentials.refresh_token)
            self.assertEqual(
                service.validate_access(
                    credentials.access_token,
                    trace_id=TRACE_ID,
                    policy_version=POLICY_VERSION,
                ).session_id,
                credentials.session_id,
            )


if __name__ == "__main__":
    unittest.main()
