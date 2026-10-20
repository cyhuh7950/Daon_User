"""0064 SQL authorization only; receipts are synthetic, not Identity/BFF proof."""
from dataclasses import replace
import os
from types import SimpleNamespace
from uuid import uuid4

import psycopg
from psycopg.conninfo import make_conninfo
import pytest

from daon_user_api.retention import RetentionError
from test_source_deletion_postgres_s16 import seed, ObjectPort, service, request


@pytest.fixture
def pg():
    dsn = os.environ.get("DAON_S16_ISOLATED_POSTGRES_DSN")
    if not dsn:
        pytest.skip("NOT RUN: explicit S16 isolated PostgreSQL DSN required")
    with psycopg.connect(dsn) as connection:
        assert connection.execute("SELECT current_database()").fetchone()[0] == "daon_user_s16_qa_20261016"
        assert connection.execute("SELECT version_num FROM alembic_version").fetchone()[0] in ("0063", "0064")
    app = make_conninfo(dsn, options="-c role=daon_app")
    with psycopg.connect(app) as connection:
        assert connection.execute("SELECT current_user").fetchone()[0] == "daon_app"
    return SimpleNamespace(admin=dsn, app=app)


@pytest.mark.parametrize("device_state", ["registered", "trusted"])
def test_s16_pg_normal_device_receipt_allows_scoped_cleanup(pg, device_state):
    f = seed(pg)
    with psycopg.connect(pg.admin) as connection:
        connection.execute("UPDATE identity_devices SET state=%s WHERE device_id=%s", (device_state, f.device))
    storage = ObjectPort(pg, f)
    store, svc = service(pg, storage)
    try:
        result = request(svc, f)
        assert result.state == "purged" and result.source_active is False
        assert len(storage.calls) == 2 and storage.keys == set()
    finally:
        store.close()


@pytest.mark.parametrize("device_state", ["active", "revoked", "unknown"])
def test_s16_pg_invalid_device_state_preserves_source_and_objects(pg, device_state):
    f = seed(pg)
    with psycopg.connect(pg.admin) as connection:
        connection.execute("UPDATE identity_devices SET state=%s WHERE device_id=%s", (device_state, f.device))
    assert_denied_without_cleanup(pg, f)


@pytest.mark.parametrize("fault", ["revoked_session", "expired_session", "missing_consumption", "different_policy"])
def test_s16_pg_normal_device_does_not_bypass_session_receipt_or_policy(pg, fault):
    f = seed(pg)
    with psycopg.connect(pg.admin) as connection:
        connection.execute("UPDATE identity_devices SET state='registered' WHERE device_id=%s", (f.device,))
        if fault == "revoked_session":
            connection.execute("UPDATE identity_sessions SET state='revoked' WHERE session_id=%s", (f.session,))
        elif fault == "expired_session":
            connection.execute("UPDATE identity_sessions SET access_expires_at=now()-interval '1 second' WHERE session_id=%s", (f.session,))
    if fault == "different_policy":
        f.ctx = replace(f.ctx, policy_version="s16-other-policy")
    assert_denied_without_cleanup(pg, f,
        authorization_key=uuid4().hex if fault == "missing_consumption" else f.auth)


def assert_denied_without_cleanup(pg, f, **kwargs):
    storage = ObjectPort(pg, f)
    store, svc = service(pg, storage)
    try:
        with pytest.raises(RetentionError, match="STEP_UP_REQUIRED") as denied:
            request(svc, f, **kwargs)
        assert denied.value.status == 403
        assert storage.calls == [] and storage.keys == set(f.keys)
        with psycopg.connect(pg.admin) as connection:
            assert connection.execute("SELECT count(*) FROM sources WHERE record_id=%s", (f.source,)).fetchone()[0] == 1
            assert connection.execute("SELECT count(*) FROM source_cleanup_requests WHERE source_id=%s", (f.source,)).fetchone()[0] == 0
    finally:
        store.close()
