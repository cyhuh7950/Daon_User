from daon_user_api.identity_postgres import _PostgresCompatConnection


def test_sql_maps_identity_tables_and_placeholders() -> None:
    statement = "INSERT OR IGNORE INTO users(user_id) VALUES (?)"
    mapped = _PostgresCompatConnection._sql(statement)
    assert "identity_users" in mapped
    assert "%s" in mapped
    assert "ON CONFLICT DO NOTHING" in mapped


def test_sql_maps_begin_immediate() -> None:
    assert _PostgresCompatConnection._sql("BEGIN IMMEDIATE") == "BEGIN"
