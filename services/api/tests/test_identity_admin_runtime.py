from __future__ import annotations

import asyncio
import os
import sqlite3
import tempfile
import threading
import unittest
from dataclasses import replace
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import patch

import httpx

from daon_user_api.authorization import Role
from daon_user_api.identity import IdentityError, PASSWORD_HASHER, SqliteIdentityRepository
from daon_user_api.runtime import (
    WEB_SESSION_COOKIE,
    RuntimeSettings,
    build_dependencies,
    create_app,
)


class IdentityAdminRuntimeTests(unittest.IsolatedAsyncioTestCase):
    async def test_switch_audit_backlog_retries_after_recovery_and_stops_at_shutdown(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            dependencies = build_dependencies(RuntimeSettings.for_test(
                database_path=Path(directory) / "runtime.sqlite3",
                policy_version="identity-policy-v1",
            ))
            with dependencies.identity_repository.transaction() as connection:
                for index in range(33):
                    connection.execute(
                        "INSERT INTO session_tenant_switch_outbox "
                        "(event_id,prior_session_id,new_session_id,prior_tenant_id,target_tenant_id,"
                        "actor_id,outcome,reason_code,occurred_at,trace_id,policy_version,created_at) "
                        "VALUES (?,?,?,?,?,?,?,?,?,?,?,?)",
                        (f"switch-{index:02d}", f"old-{index:02d}", f"new-{index:02d}",
                         "tenant-001", "tenant-002", "user-001", "succeeded", "USER_SELECTED",
                         datetime.now(timezone.utc).isoformat(), "switch-retry", "identity-policy-v1",
                         datetime.now(timezone.utc).isoformat()),
                    )
            original_append = dependencies.audit_store.append
            original_mark_delivered = (
                dependencies.identity_repository.mark_tenant_switch_audit_delivered
            )
            available = threading.Event()
            retried_during_outage = threading.Event()
            delivery_mark_failed = threading.Event()
            first_event_outage_attempts = 0
            first_event_recovery_attempts = 0

            def append_after_recovery(draft):
                nonlocal first_event_outage_attempts, first_event_recovery_attempts
                if not available.is_set():
                    if draft.event_id == "switch-00":
                        first_event_outage_attempts += 1
                        if first_event_outage_attempts >= 2:
                            retried_during_outage.set()
                    raise OSError("temporary audit outage")
                if draft.event_id == "switch-00":
                    first_event_recovery_attempts += 1
                return original_append(draft)

            def mark_after_recovery(event_id, delivered_at):
                if event_id == "switch-00" and not delivery_mark_failed.is_set():
                    delivery_mark_failed.set()
                    raise IdentityError("PERSISTENCE_UNAVAILABLE", 503)
                return original_mark_delivered(event_id, delivered_at)

            with patch.object(dependencies.audit_store, "append", side_effect=append_after_recovery), \
                    patch.object(dependencies.identity_repository, "mark_tenant_switch_audit_delivered",
                                 side_effect=mark_after_recovery), \
                    patch("daon_user_api.runtime._ADMIN_AUDIT_RETRY_INTERVAL_SECONDS", 0.02):
                app = create_app(dependencies)
                async with app.router.lifespan_context(app):
                    async def wait_for_outage_retry() -> None:
                        while not retried_during_outage.is_set():
                            await asyncio.sleep(0.01)

                    await asyncio.wait_for(wait_for_outage_retry(), timeout=5.0)
                    self.assertGreaterEqual(first_event_outage_attempts, 2)
                    with dependencies.identity_repository.transaction() as connection:
                        self.assertEqual(connection.execute(
                            "SELECT COUNT(*) FROM session_tenant_switch_outbox WHERE delivered_at IS NULL"
                        ).fetchone()[0], 33)
                    self.assertEqual(dependencies.audit_store.list(
                        tenant_id="tenant-002", action="identity.session.tenant_switched",
                    ).items, ())
                    available.set()

                    async def wait_for_delivery() -> None:
                        while True:
                            with dependencies.identity_repository.transaction() as connection:
                                remaining = connection.execute(
                                    "SELECT COUNT(*) FROM session_tenant_switch_outbox WHERE delivered_at IS NULL"
                                ).fetchone()[0]
                            if remaining == 0:
                                return
                            await asyncio.sleep(0.02)

                    await asyncio.wait_for(wait_for_delivery(), timeout=5.0)
                    self.assertTrue(delivery_mark_failed.is_set())
                    self.assertGreaterEqual(first_event_recovery_attempts, 2)
                    events = dependencies.audit_store.list(
                        tenant_id="tenant-002", action="identity.session.tenant_switched",
                    ).items
                    event_ids = [event.event_id for event in events]
                    self.assertEqual(len(event_ids), 33)
                    self.assertEqual(set(event_ids), {
                        f"switch-{index:02d}" for index in range(33)
                    })
                self.assertFalse(any(
                    thread.name == "admin-role-audit-outbox" and thread.is_alive()
                    for thread in threading.enumerate()
                ))

    @unittest.skipUnless(
        os.environ.get("DAON_C9_AUDIT_ISOLATED_POSTGRES_DSN"),
        "dedicated disposable C9 audit PostgreSQL database is not configured",
    )
    async def test_switch_audit_backlog_retries_with_postgres_storage(self) -> None:
        from urllib.parse import urlsplit

        import psycopg

        from daon_user_api.audit import PostgresSecurityAuditStore
        from daon_user_api.identity import IdentityService
        from daon_user_api.identity_postgres import PostgresIdentityRepository

        dsn = os.environ["DAON_C9_AUDIT_ISOLATED_POSTGRES_DSN"]
        location = urlsplit(dsn)
        self.assertEqual(location.hostname, "127.0.0.1")
        self.assertEqual(location.path, "/daon_user_c9_audit_test")
        with psycopg.connect(dsn) as connection:
            self.assertEqual(connection.execute("SELECT version_num FROM alembic_version").fetchone()[0], "0055")
            self.assertEqual(connection.execute(
                "SELECT COUNT(*) FROM identity_session_tenant_switch_outbox"
            ).fetchone()[0], 0)
            self.assertEqual(connection.execute("SELECT COUNT(*) FROM security_audit_events").fetchone()[0], 0)

        with tempfile.TemporaryDirectory() as directory:
            dependencies = build_dependencies(RuntimeSettings.for_test(
                database_path=Path(directory) / "runtime.sqlite3",
                policy_version="identity-policy-v1",
            ))
            dependencies.identity_repository.close()
            repository = PostgresIdentityRepository(dsn)
            audit_store = PostgresSecurityAuditStore(dsn)
            dependencies.identity_repository = repository
            dependencies.audit_store = audit_store
            dependencies.identity_service = IdentityService(
                repository=repository, audit_store=audit_store, oidc_policies=(),
                clock=lambda: datetime.now(timezone.utc),
            )
            dependencies.admin_membership_role_service = None
            try:
                with repository.transaction() as connection:
                    for index in range(33):
                        connection.execute(
                            "INSERT INTO session_tenant_switch_outbox "
                            "(event_id,prior_session_id,new_session_id,prior_tenant_id,target_tenant_id,"
                            "actor_id,outcome,reason_code,occurred_at,trace_id,policy_version,created_at) "
                            "VALUES (?,?,?,?,?,?,?,?,?,?,?,?)",
                            (f"pg-switch-{index:02d}", f"pg-old-{index:02d}", f"pg-new-{index:02d}",
                             "tenant-001", "tenant-002", "user-001", "succeeded", "USER_SELECTED",
                             datetime.now(timezone.utc).isoformat(), "identity-pg-retry", "identity-policy-v1",
                             datetime.now(timezone.utc).isoformat()),
                        )
                original_append = audit_store.append
                available = False

                def append_after_recovery(draft):
                    if not available:
                        raise OSError("temporary audit outage")
                    return original_append(draft)

                with patch.object(audit_store, "append", side_effect=append_after_recovery), \
                        patch("daon_user_api.runtime._ADMIN_AUDIT_RETRY_INTERVAL_SECONDS", 0.02):
                    app = create_app(dependencies)
                    async with app.router.lifespan_context(app):
                        await asyncio.sleep(0.05)
                        with psycopg.connect(dsn) as connection:
                            self.assertEqual(connection.execute(
                                "SELECT COUNT(*) FROM identity_session_tenant_switch_outbox "
                                "WHERE delivered_at IS NULL"
                            ).fetchone()[0], 33)
                        available = True

                        async def wait_for_delivery() -> None:
                            while True:
                                with psycopg.connect(dsn) as connection:
                                    remaining = connection.execute(
                                        "SELECT COUNT(*) FROM identity_session_tenant_switch_outbox "
                                        "WHERE delivered_at IS NULL"
                                    ).fetchone()[0]
                                if remaining == 0:
                                    return
                                await asyncio.sleep(0.02)

                        await asyncio.wait_for(wait_for_delivery(), timeout=10.0)
                        with psycopg.connect(dsn) as connection:
                            event_ids = [row[0] for row in connection.execute(
                                "SELECT event_id FROM security_audit_events "
                                "WHERE action='identity.session.tenant_switched' ORDER BY event_id"
                            ).fetchall()]
                            self.assertEqual(event_ids, [
                                f"pg-switch-{index:02d}" for index in range(33)
                            ])
                    self.assertFalse(any(
                        thread.name == "admin-role-audit-outbox" and thread.is_alive()
                        for thread in threading.enumerate()
                    ))
            finally:
                dependencies.close()
                audit_store.close()

    async def test_session_tenant_list_and_web_native_switch_use_own_active_scope(self) -> None:
        directory = tempfile.TemporaryDirectory()
        settings = RuntimeSettings.for_test(
            database_path=Path(directory.name) / "runtime.sqlite3",
            policy_version="identity-policy-v1",
        )
        dependencies = build_dependencies(settings)
        class Sender:
            messages: list[dict[str, str]] = []
            def send(self, **message: str) -> None:
                self.messages.append(message)
        sender = Sender()
        dependencies.identity_service._email_sender = sender
        client = httpx.AsyncClient(
            transport=httpx.ASGITransport(app=create_app(dependencies)),
            base_url="https://app.example.com",
        )
        self.addAsyncCleanup(client.aclose)
        self.addCleanup(dependencies.close)
        self.addCleanup(directory.cleanup)
        dependencies.identity_service.signup(
            login_id="alice", email="alice@example.com", password="correct horse battery staple",
            trace_id="t1", policy_version="p1",
        )
        token = sender.messages[-1]["body"].split(": ", 1)[1].splitlines()[0]
        dependencies.identity_service.verify_email(token=token, trace_id="t2", policy_version="p1")
        login = await client.post(
            "/api/v1/auth/login",
            json={"login_id": "alice", "password": "correct horse battery staple"},
        )
        self.assertEqual(login.status_code, 200)
        personal_tenant = login.json()["data"]["tenant_id"]
        user_id = login.json()["data"]["user_id"]
        dependencies.identity_repository.add_tenant_membership(
            tenant_id="organization-001", user_id=user_id, role="member",
        )
        for tenant_id in ("organization-001", "outsider-001"):
            dependencies.authorization_repository.bootstrap_workspace(
                tenant_id=tenant_id, workspace_id=f"workspace-{tenant_id}",
                owner_user_id="admin", owner_role=Role.ORGANIZATION_ADMIN,
                workspace_kind="organization", data_area="cloud_sync",
                cost_limit_cents=1000, now=datetime.now(timezone.utc),
            )
        dependencies.authorization_repository.bootstrap_workspace(
            tenant_id="organization-001", workspace_id="a-inaccessible",
            owner_user_id="admin", owner_role=Role.ORGANIZATION_ADMIN,
            workspace_kind="organization", data_area="cloud_sync",
            cost_limit_cents=1000, now=datetime.now(timezone.utc),
        )
        with dependencies.authorization_repository.transaction() as connection:
            connection.execute(
                "INSERT INTO auth_memberships(tenant_id,workspace_id,user_id,role,state,version,updated_at) "
                "VALUES (?,?,?,?,?,?,?)",
                ("organization-001", "workspace-organization-001", user_id, "viewer", "active", 1,
                 datetime.now(timezone.utc).isoformat()),
            )
        # In this SQLite fixture the identity table stands in for Foundation's
        # same-DB tenants projection; production reads the distinct RLS table.
        with dependencies.identity_repository.transaction() as connection:
            connection.execute("ALTER TABLE tenants ADD COLUMN display_name TEXT")
            connection.execute("UPDATE tenants SET display_name=? WHERE tenant_id=?", ("개인 공간", personal_tenant))
            connection.execute("UPDATE tenants SET display_name=? WHERE tenant_id=?", ("조직 공간", "organization-001"))
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=create_app(dependencies)),
            base_url="https://app.example.com",
        ) as stranger:
            unauthenticated = await stranger.get("/api/v1/session/tenants")
        listed = await client.get("/api/v1/session/tenants")

        invalid_list_query = await client.get("/api/v1/session/tenants?unexpected=1")
        invalid_list_body = await client.request("GET", "/api/v1/session/tenants", content=b"unexpected")
        self.assertEqual(invalid_list_query.status_code, 422)
        self.assertEqual(invalid_list_body.status_code, 422)

        self.assertEqual(unauthenticated.status_code, 401)
        self.assertEqual(listed.status_code, 200)
        self.assertEqual(listed.json()["data"]["current_tenant_id"], personal_tenant)
        listed_tenants = listed.json()["data"]["tenants"]
        self.assertEqual([row["tenant_id"] for row in listed_tenants], sorted([personal_tenant, "organization-001"]))
        self.assertEqual({row["tenant_id"]: row for row in listed_tenants}, {
            personal_tenant: {"tenant_id": personal_tenant, "display_name": "개인 공간",
                              "kind": "personal", "workspace_id": login.json()["data"]["workspace_id"]},
            "organization-001": {"tenant_id": "organization-001", "display_name": "조직 공간",
                                 "kind": "organization", "workspace_id": "workspace-organization-001"},
        })
        old_cookie = login.cookies[WEB_SESSION_COOKIE]
        old_session_id = (await client.get("/api/v1/session")).json()["data"]["session_id"]
        denied_csrf = await client.post(
            "/api/v1/session/tenant", json={"tenant_id": "organization-001"},
        )
        self.assertEqual(denied_csrf.status_code, 403)
        switch_headers = {"X-Daon-Bff-Transport": "internal",
                          "X-Daon-Csrf-Origin": "https://app.example.com",
                          "X-Daon-Csrf-Referer": "https://app.example.com/notebooks"}
        invalid_switch_query = await client.post(
            "/api/v1/session/tenant?unexpected=1", headers=switch_headers,
            json={"tenant_id": "organization-001"},
        )
        invalid_media_type = await client.post(
            "/api/v1/session/tenant", headers={**switch_headers, "Content-Type": "text/plain"},
            content=b'{"tenant_id":"organization-001"}',
        )
        malformed_json = await client.post(
            "/api/v1/session/tenant", headers={**switch_headers, "Content-Type": "application/json"},
            content=b'{"tenant_id":',
        )
        self.assertEqual(invalid_switch_query.status_code, 422)
        self.assertEqual(invalid_media_type.status_code, 422)
        self.assertEqual(malformed_json.status_code, 422)
        malformed = await client.post(
            "/api/v1/session/tenant", headers=switch_headers,
            json={"tenant_id": "organization-001", "unexpected": True},
        )
        unavailable = await client.post(
            "/api/v1/session/tenant", headers=switch_headers,
            json={"tenant_id": "outsider-001"},
        )
        self.assertEqual(malformed.status_code, 422)
        self.assertEqual(unavailable.status_code, 404)
        self.assertEqual((await client.get("/api/v1/session")).json()["data"]["tenant_id"], personal_tenant)
        denied_events = dependencies.audit_store.list(
            tenant_id=personal_tenant, action="identity.session.tenant_switch_denied",
        ).items
        unavailable_event = next(
            event for event in denied_events if event.metadata.get("reason_code") == "TENANT_UNAVAILABLE"
        )
        self.assertEqual(unavailable_event.actor_id, user_id)
        self.assertEqual(unavailable_event.outcome.value, "denied")
        self.assertEqual(unavailable_event.target_id, "outsider-001")
        self.assertEqual(unavailable_event.metadata["prior_tenant_id"], personal_tenant)
        self.assertEqual(unavailable_event.metadata["prior_session_id"], old_session_id)
        self.assertEqual(unavailable_event.metadata["target_tenant_id"], "outsider-001")
        self.assertEqual(unavailable_event.trace_id, unavailable.headers["x-trace-id"])
        self.assertNotIn(old_cookie, str(unavailable_event.metadata))
        self.assertIn("INVALID_INPUT", {event.metadata.get("reason_code") for event in denied_events})
        self.assertIn("CSRF_VALIDATION_FAILED", {event.metadata.get("reason_code") for event in denied_events})
        with dependencies.authorization_repository.transaction() as connection:
            connection.execute(
                "UPDATE auth_memberships SET state='inactive' WHERE tenant_id=? AND user_id=?",
                ("organization-001", user_id),
            )
        revoked_role = await client.post(
            "/api/v1/session/tenant", headers=switch_headers,
            json={"tenant_id": "organization-001"},
        )
        self.assertEqual(revoked_role.status_code, 404)
        self.assertEqual((await client.get("/api/v1/session")).json()["data"]["session_id"], old_session_id)
        self.assertTrue(any(
            event.target_id == "organization-001" and event.metadata.get("reason_code") == "TENANT_UNAVAILABLE"
            for event in dependencies.audit_store.list(
                tenant_id=personal_tenant, action="identity.session.tenant_switch_denied",
            ).items
        ))
        with dependencies.authorization_repository.transaction() as connection:
            connection.execute(
                "UPDATE auth_memberships SET state='active' WHERE tenant_id=? AND user_id=?",
                ("organization-001", user_id),
            )
        original_audit_store = dependencies.identity_service._audit_store
        class FailingAudit:
            def append(self, _draft: object) -> None:
                raise RuntimeError("audit unavailable")
        dependencies.identity_service._audit_store = FailingAudit()
        try:
            audit_unavailable = await client.post(
                "/api/v1/session/tenant", headers=switch_headers,
                json={"tenant_id": "outsider-001"},
            )
        finally:
            dependencies.identity_service._audit_store = original_audit_store
        self.assertEqual(audit_unavailable.status_code, 503)
        self.assertEqual((await client.get("/api/v1/session")).json()["data"]["session_id"], old_session_id)
        switched = await client.post(
            "/api/v1/session/tenant",
            headers=switch_headers,
            json={"tenant_id": "organization-001"},
        )
        self.assertEqual(switched.status_code, 200)
        self.assertEqual(switched.json()["data"]["workspace_id"], "workspace-organization-001")
        self.assertNotIn("access_credential", switched.json()["data"])
        self.assertNotIn("refresh_credential", switched.json()["data"])
        self.assertIn(WEB_SESSION_COOKIE, switched.cookies)
        current = await client.get("/api/v1/session")
        self.assertEqual(current.json()["data"]["tenant_id"], "organization-001")
        self.assertEqual(current.json()["data"]["workspace_id"], "workspace-organization-001")
        same_tenant = await client.post(
            "/api/v1/session/tenant", headers=switch_headers,
            json={"tenant_id": "organization-001"},
        )
        self.assertEqual(same_tenant.status_code, 200)
        self.assertEqual(same_tenant.json()["data"]["session_id"], switched.json()["data"]["session_id"])
        self.assertNotIn(WEB_SESSION_COOKIE, same_tenant.cookies)
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=create_app(dependencies)),
            base_url="https://app.example.com", cookies={WEB_SESSION_COOKIE: old_cookie},
        ) as stale:
            self.assertEqual((await stale.get("/api/v1/session")).status_code, 401)
            self.assertEqual((await stale.get(
                "/api/v1/session/tenants?unexpected=1",
            )).status_code, 401)
            stale_switch = await stale.post(
                "/api/v1/session/tenant", headers=switch_headers,
                json={"tenant_id": "organization-001"},
            )
            self.assertEqual(stale_switch.status_code, 401)
        self.assertTrue(any(
            event.trace_id == stale_switch.headers["x-trace-id"]
            for event in dependencies.audit_store.list(
                tenant_id=personal_tenant, action="identity.access.denied",
            ).items
        ))
        self.assertFalse(any(
            event.trace_id == stale_switch.headers["x-trace-id"]
            for event in dependencies.audit_store.list(
                tenant_id=personal_tenant, action="identity.session.tenant_switch_denied",
            ).items
        ))
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=create_app(dependencies)),
            base_url="https://app.example.com",
        ) as native_client:
            native_login = await native_client.post(
                "/api/v1/auth/native/login",
                json={"login_id": "alice", "password": "correct horse battery staple"},
            )
            self.assertEqual(native_login.status_code, 200)
            old_native_access = native_login.json()["data"]["access_credential"]
            native_switch = await native_client.post(
                "/api/v1/session/tenant",
                headers={"Authorization": f"Bearer {old_native_access}"},
                json={"tenant_id": "organization-001"},
            )
            self.assertEqual(native_switch.status_code, 200)
            self.assertEqual(native_switch.json()["data"]["tenant_id"], "organization-001")
            self.assertIsInstance(native_switch.json()["data"]["access_credential"], str)
            self.assertIsInstance(native_switch.json()["data"]["refresh_credential"], str)
            native_data = native_switch.json()["data"]
            with dependencies.identity_repository.transaction() as connection:
                before_sessions = connection.execute("SELECT COUNT(*) FROM sessions").fetchone()[0]
                before_devices = connection.execute("SELECT COUNT(*) FROM devices").fetchone()[0]
                before_outbox = connection.execute(
                    "SELECT COUNT(*) FROM session_tenant_switch_outbox"
                ).fetchone()[0]
            before_success_audit = len(dependencies.audit_store.list(
                tenant_id="organization-001", action="identity.session.tenant_switched",
            ).items)
            native_same_tenant = await native_client.post(
                "/api/v1/session/tenant",
                headers={"Authorization": f"Bearer {native_data['access_credential']}"},
                json={"tenant_id": "organization-001"},
            )
            self.assertEqual(native_same_tenant.status_code, 200)
            same_data = native_same_tenant.json()["data"]
            self.assertEqual(same_data["session_id"], native_data["session_id"])
            self.assertEqual(same_data["device_id"], native_data["device_id"])
            self.assertEqual(same_data["workspace_id"], native_data["workspace_id"])
            self.assertNotIn("access_credential", same_data)
            self.assertNotIn("refresh_credential", same_data)
            self.assertNotIn(WEB_SESSION_COOKIE, native_same_tenant.cookies)
            with dependencies.identity_repository.transaction() as connection:
                self.assertEqual(connection.execute("SELECT COUNT(*) FROM sessions").fetchone()[0], before_sessions)
                self.assertEqual(connection.execute("SELECT COUNT(*) FROM devices").fetchone()[0], before_devices)
                self.assertEqual(connection.execute(
                    "SELECT COUNT(*) FROM session_tenant_switch_outbox"
                ).fetchone()[0], before_outbox)
            self.assertEqual(len(dependencies.audit_store.list(
                tenant_id="organization-001", action="identity.session.tenant_switched",
            ).items), before_success_audit)
            self.assertEqual((await native_client.get(
                "/api/v1/session", headers={"Authorization": f"Bearer {native_data['access_credential']}"},
            )).status_code, 200)
            self.assertEqual((await native_client.post(
                "/api/v1/session/refresh", json={"refresh_credential": native_data["refresh_credential"]},
            )).status_code, 200)
            self.assertEqual((await native_client.get(
                "/api/v1/session", headers={"Authorization": f"Bearer {old_native_access}"},
            )).status_code, 401)

    async def test_missing_foundation_name_fails_closed_and_keeps_session(self) -> None:
        directory = tempfile.TemporaryDirectory()
        settings = RuntimeSettings.for_test(
            database_path=Path(directory.name) / "runtime.sqlite3",
            policy_version="identity-policy-v1",
        )
        dependencies = build_dependencies(settings)
        class Sender:
            messages: list[dict[str, str]] = []
            def send(self, **message: str) -> None:
                self.messages.append(message)
        sender = Sender()
        dependencies.identity_service._email_sender = sender
        client = httpx.AsyncClient(
            transport=httpx.ASGITransport(app=create_app(dependencies)),
            base_url="https://app.example.com",
        )
        self.addAsyncCleanup(client.aclose)
        self.addCleanup(dependencies.close)
        self.addCleanup(directory.cleanup)
        dependencies.identity_service.signup(
            login_id="alice", email="alice@example.com", password="correct horse battery staple",
            trace_id="t1", policy_version="p1",
        )
        token = sender.messages[-1]["body"].split(": ", 1)[1].splitlines()[0]
        dependencies.identity_service.verify_email(token=token, trace_id="t2", policy_version="p1")
        login = await client.post(
            "/api/v1/auth/login",
            json={"login_id": "alice", "password": "correct horse battery staple"},
        )
        self.assertEqual(login.status_code, 200)

        listed = await client.get("/api/v1/session/tenants")

        self.assertEqual(listed.status_code, 503)
        session = await client.get("/api/v1/session")
        self.assertEqual(session.status_code, 200)
        self.assertEqual(session.json()["data"]["tenant_id"], login.json()["data"]["tenant_id"])

    async def test_local_web_login_uses_personal_workspace_with_earlier_organization_tenant(self) -> None:
        directory = tempfile.TemporaryDirectory()
        settings = RuntimeSettings.for_test(
            database_path=Path(directory.name) / "runtime.sqlite3",
            policy_version="identity-policy-v1",
        )
        dependencies = build_dependencies(settings)
        class Sender:
            messages: list[dict[str, str]] = []
            def send(self, **message: str) -> None:
                self.messages.append(message)
        sender = Sender()
        dependencies.identity_service._email_sender = sender
        client = httpx.AsyncClient(
            transport=httpx.ASGITransport(app=create_app(dependencies)),
            base_url="https://app.example.com",
        )
        self.addAsyncCleanup(client.aclose)
        self.addCleanup(dependencies.close)
        self.addCleanup(directory.cleanup)
        dependencies.identity_service.signup(
            login_id="alice", email="alice@example.com", password="correct horse battery staple",
            trace_id="t1", policy_version="p1",
        )
        token = sender.messages[-1]["body"].split(": ", 1)[1].splitlines()[0]
        dependencies.identity_service.verify_email(token=token, trace_id="t2", policy_version="p1")
        with dependencies.identity_repository.transaction() as connection:
            row = connection.execute(
                "SELECT tenant_id,user_id FROM memberships WHERE role='personal_owner' AND user_id IN "
                "(SELECT user_id FROM users WHERE login_id='alice')",
            ).fetchone()
            personal_tenant, user_id = str(row["tenant_id"]), str(row["user_id"])
        dependencies.identity_repository.add_tenant_membership(
            tenant_id="qa-organization", user_id=user_id, role="member",
        )
        dependencies.authorization_repository.bootstrap_workspace(
            tenant_id="qa-organization", workspace_id="qa-organization-workspace",
            owner_user_id="admin", owner_role=Role.ORGANIZATION_ADMIN,
            workspace_kind="organization", data_area="cloud_sync",
            cost_limit_cents=1000, now=datetime.now(timezone.utc),
        )

        login = await client.post(
            "/api/v1/auth/login",
            json={"login_id": "alice", "password": "correct horse battery staple"},
        )

        assert login.status_code == 200
        assert login.json()["data"]["tenant_id"] == personal_tenant
        assert login.json()["data"]["workspace_id"] == dependencies.authorization_repository.primary_workspace_id(personal_tenant)
        with dependencies.identity_repository.transaction() as connection:
            organization = connection.execute(
                "SELECT role FROM memberships WHERE tenant_id='qa-organization' AND user_id=?", (user_id,),
            ).fetchone()
        assert organization["role"] == "member"

    async def test_startup_rejects_noncanonical_admin_before_workspace_owner_grant(self) -> None:
        directory = tempfile.TemporaryDirectory()
        database_path = Path(directory.name) / "runtime.sqlite3"
        repository = SqliteIdentityRepository(database_path)
        with repository.transaction() as connection:
            connection.execute(
                "INSERT INTO users(user_id,issuer,subject,login_id,email,password_digest,"
                "password_change_required,state) VALUES (?,?,?,?,?,?,?,?)",
                (
                    "admin", "oidc", "admin", "admin", None,
                    PASSWORD_HASHER.hash("a preexisting strong password"), 0, "active",
                ),
            )
        repository.close()
        settings = replace(
            RuntimeSettings.for_test(
                database_path=database_path,
                policy_version="identity-policy-v1",
            ),
            system_admin_user_ids=frozenset({"admin"}),
        )
        self.addCleanup(directory.cleanup)

        with self.assertRaises(IdentityError) as denied:
            build_dependencies(settings)

        self.assertEqual(denied.exception.code, "INITIAL_ADMIN_CONFLICT")
        connection = sqlite3.connect(database_path)
        try:
            owner = connection.execute(
                "SELECT 1 FROM auth_memberships WHERE user_id=?", ("admin",)
            ).fetchone()
        finally:
            connection.close()
        self.assertIsNone(owner)

    async def test_admin_restricted_session_can_only_read_session_change_password_or_logout(self) -> None:
        directory = tempfile.TemporaryDirectory()
        settings = replace(
            RuntimeSettings.for_test(
                database_path=Path(directory.name) / "runtime.sqlite3",
                policy_version="identity-policy-v1",
            ),
            system_admin_user_ids=frozenset({"admin"}),
        )
        dependencies = build_dependencies(settings)
        admin_workspace_id = dependencies.authorization_repository.primary_workspace_id("admin")
        self.assertIsNotNone(admin_workspace_id)
        client = httpx.AsyncClient(
            transport=httpx.ASGITransport(app=create_app(dependencies)),
            base_url="https://app.example.com",
        )
        self.addAsyncCleanup(client.aclose)
        self.addCleanup(dependencies.close)
        self.addCleanup(directory.cleanup)
        login = await client.post(
            "/api/v1/auth/login",
            json={"login_id": "admin", "password": "admin"},
        )
        assert login.status_code == 200
        session_cookie = login.cookies[WEB_SESSION_COOKIE]
        assert session_cookie

        session = await client.get("/api/v1/session")
        assert session.status_code == 200
        assert session.json()["data"]["password_change_required"] is True
        assert session.json()["data"]["is_system_admin"] is True

        blocked = await client.get("/api/v1/screen-preferences")
        assert blocked.status_code == 403
        assert blocked.json()["error"]["code"] == "PASSWORD_CHANGE_REQUIRED"

        changed = await client.post(
            "/api/v1/auth/password/change",
            headers={
                "X-Daon-Bff-Transport": "internal",
                "X-Daon-Csrf-Origin": "https://app.example.com",
                "X-Daon-Csrf-Referer": "https://app.example.com/password-change",
            },
            json={
                "current_password": "admin",
                "new_password": "a strong replacement password",
            },
        )
        assert changed.status_code == 200
        assert changed.json()["data"] == {"status": "password_changed"}
        assert f"{WEB_SESSION_COOKIE}=" in changed.headers["set-cookie"]
        assert "Max-Age=0" in changed.headers["set-cookie"]

        revoked = await client.get("/api/v1/session")
        assert revoked.status_code == 401
        old_login = await client.post(
            "/api/v1/auth/login",
            json={"login_id": "admin", "password": "admin"},
        )
        assert old_login.status_code == 400
        new_login = await client.post(
            "/api/v1/auth/login",
            json={
                "login_id": "admin",
                "password": "a strong replacement password",
            },
        )
        assert new_login.status_code == 200
