from __future__ import annotations

import sqlite3
import tempfile
import unittest
from pathlib import Path

import pytest

from daon_user_api.identity import DevicePlatform, IdentityError, PASSWORD_HASHER
from test_identity_support import POLICY_VERSION, TRACE_ID, create_service


def _sqlite_upgrade_persists_password_change_required(tmp_path: Path) -> None:
    database_path = tmp_path / "identity.sqlite3"
    connection = sqlite3.connect(database_path)
    connection.execute(
        "CREATE TABLE users (user_id TEXT PRIMARY KEY, issuer TEXT NOT NULL, "
        "subject TEXT NOT NULL, UNIQUE(issuer, subject))"
    )
    connection.commit()
    connection.close()

    _service, repository, _audit, _clock = create_service(database_path)
    with repository.transaction() as upgraded:
        columns = {
            str(row[1]): (str(row[2]), str(row[4]))
            for row in upgraded.execute("PRAGMA table_info(users)")
        }

    assert columns["password_change_required"] == ("INTEGER", "0")


def _initial_admin_is_email_less_idempotent_and_changed_password_is_preserved(tmp_path: Path) -> None:
    identity, repository, _audit, _clock = create_service(tmp_path / "identity.sqlite3")

    identity.ensure_initial_admin()
    identity.ensure_initial_admin()

    with repository.transaction() as connection:
        users = connection.execute(
            "SELECT user_id,subject,login_id,email,issuer,state,password_change_required "
            "FROM users WHERE user_id=?",
            ("admin",),
        ).fetchall()
        memberships = connection.execute(
            "SELECT tenant_id,role FROM memberships WHERE user_id=?",
            ("admin",),
        ).fetchall()
    assert [tuple(row) for row in users] == [
        ("admin", "admin", "admin", None, "local", "active", 1)
    ]
    assert [tuple(row) for row in memberships] == [("admin", "personal_owner")]

    first = identity.local_login(
        login_id="admin", password="admin", platform=DevicePlatform.WEB,
        trace_id=TRACE_ID, policy_version=POLICY_VERSION,
    )
    identity.change_current_password(
        access_token=first.access_token,
        current_password="admin",
        new_password="a strong replacement password",
        trace_id=TRACE_ID,
        policy_version=POLICY_VERSION,
    )
    identity.ensure_initial_admin()

    with pytest.raises(IdentityError) as old_password:
        identity.local_login(
            login_id="admin", password="admin", platform=DevicePlatform.WEB,
            trace_id=TRACE_ID, policy_version=POLICY_VERSION,
        )
    assert old_password.value.code == "PASSWORD_POLICY_FAILED"
    replacement = identity.local_login(
        login_id="admin", password="a strong replacement password",
        platform=DevicePlatform.WEB, trace_id=TRACE_ID,
        policy_version=POLICY_VERSION,
    )
    assert replacement.user_id == "admin"
    with repository.transaction() as connection:
        row = connection.execute(
            "SELECT password_digest,password_change_required FROM users WHERE user_id=?",
            ("admin",),
        ).fetchone()
    assert row is not None
    assert PASSWORD_HASHER.verify(str(row["password_digest"]), "a strong replacement password")
    assert row["password_change_required"] == 0


def _password_change_requires_current_password_and_revokes_every_session(tmp_path: Path) -> None:
    identity, _repository, audit, _clock = create_service(tmp_path / "identity.sqlite3")
    identity.ensure_initial_admin()
    first = identity.local_login(
        login_id="admin", password="admin", platform=DevicePlatform.WEB,
        trace_id=TRACE_ID, policy_version=POLICY_VERSION,
    )
    second = identity.local_login(
        login_id="admin", password="admin", platform=DevicePlatform.WEB,
        trace_id=TRACE_ID, policy_version=POLICY_VERSION,
    )

    with pytest.raises(IdentityError) as denied:
        identity.change_current_password(
            access_token=first.access_token,
            current_password="incorrect current password",
            new_password="a strong replacement password",
            trace_id=TRACE_ID,
            policy_version=POLICY_VERSION,
        )
    assert denied.value.code == "AUTHENTICATION_REQUIRED"
    assert identity.describe_access(
        second.access_token, trace_id=TRACE_ID, policy_version=POLICY_VERSION
    ).password_change_required is True

    identity.change_current_password(
        access_token=first.access_token,
        current_password="admin",
        new_password="a strong replacement password",
        trace_id=TRACE_ID,
        policy_version=POLICY_VERSION,
    )

    for credential in (first.access_token, second.access_token):
        with pytest.raises(IdentityError) as revoked:
            identity.validate_access(
                credential, trace_id=TRACE_ID, policy_version=POLICY_VERSION
            )
        assert revoked.value.code == "SESSION_REVOKED"
    events = audit.list(tenant_id="admin", action="identity.password.changed").items
    assert len(events) == 1
    assert events[0].metadata == {"reason_code": "CURRENT_PASSWORD_CHANGED"}


class IdentityAdminTests(unittest.TestCase):
    def test_sqlite_upgrade_persists_password_change_required(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            _sqlite_upgrade_persists_password_change_required(Path(directory))

    def test_initial_admin_is_email_less_idempotent_and_changed_password_is_preserved(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            _initial_admin_is_email_less_idempotent_and_changed_password_is_preserved(Path(directory))

    def test_password_change_requires_current_password_and_revokes_every_session(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            _password_change_requires_current_password_and_revokes_every_session(Path(directory))
