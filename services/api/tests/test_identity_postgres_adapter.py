from daon_user_api.identity_postgres import _CursorProxy, _PostgresCompatConnection
from daon_user_api.postgres_adapters import PostgresCompatConnection, _compat_row
import psycopg


def test_sql_maps_identity_tables_and_placeholders() -> None:
    statement = "INSERT OR IGNORE INTO users(user_id) VALUES (?)"
    mapped = _PostgresCompatConnection._sql(statement)
    assert "identity_users" in mapped
    assert "%s" in mapped
    assert "ON CONFLICT DO NOTHING" in mapped


def test_sql_maps_begin_immediate() -> None:
    assert _PostgresCompatConnection._sql("BEGIN IMMEDIATE") == "BEGIN"


def test_identity_connection_exposes_sqlite_transaction_state() -> None:
    connection = _PostgresCompatConnection.__new__(_PostgresCompatConnection)

    class Info:
        transaction_status = psycopg.pq.TransactionStatus.IDLE

    class FakeConnection:
        info = Info()

    connection._connection = FakeConnection()
    assert connection.in_transaction is False


def test_postgres_rows_support_mapping_and_positional_access() -> None:
    class Cursor:
        def fetchone(self):
            return {"tenant_id": "tenant-1", "state": "active"}

        def fetchall(self):
            return [{"tenant_id": "tenant-1"}]

        def __iter__(self):
            return iter(self.fetchall())

    cursor = _CursorProxy(Cursor())
    row = cursor.fetchone()
    assert row["tenant_id"] == "tenant-1"
    assert row[0] == "tenant-1"
    assert cursor.fetchall()[0][0] == "tenant-1"


def test_authorization_sql_uses_isolated_tables() -> None:
    connection = PostgresCompatConnection.__new__(PostgresCompatConnection)
    connection._prefixes = ("auth_",)
    mapped = connection._sql("SELECT * FROM auth_workspaces WHERE workspace_id=?")
    assert "identity_auth_workspaces" in mapped
    assert "%s" in mapped


def test_organization_sql_uses_isolated_tables() -> None:
    connection = PostgresCompatConnection.__new__(PostgresCompatConnection)
    connection._prefixes = ("organization_",)
    mapped = connection._sql("INSERT INTO organization_idempotency(operation) VALUES (?)")
    assert "identity_org_idempotency" in mapped
    assert "%s" in mapped


def test_shared_postgres_rows_support_positional_access() -> None:
    row = _compat_row({"workspace_id": "workspace-1", "state": "active"})
    assert row["workspace_id"] == "workspace-1"
    assert row[0] == "workspace-1"
