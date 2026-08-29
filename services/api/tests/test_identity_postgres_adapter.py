from daon_user_api.identity_postgres import _PostgresCompatConnection
from daon_user_api.postgres_adapters import PostgresCompatConnection


def test_sql_maps_identity_tables_and_placeholders() -> None:
    statement = "INSERT OR IGNORE INTO users(user_id) VALUES (?)"
    mapped = _PostgresCompatConnection._sql(statement)
    assert "identity_users" in mapped
    assert "%s" in mapped
    assert "ON CONFLICT DO NOTHING" in mapped


def test_sql_maps_begin_immediate() -> None:
    assert _PostgresCompatConnection._sql("BEGIN IMMEDIATE") == "BEGIN"


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
