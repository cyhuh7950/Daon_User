from __future__ import annotations

import unittest
from contextlib import contextmanager

from daon_user_api.connector_postgres import PostgresConnectorRepository
from daon_user_api.mcp_connector import ConnectorView


class _Result:
    def __init__(self, rows=(), rowcount=0):
        self._rows = list(rows)
        self.rowcount = rowcount

    def fetchone(self):
        return self._rows[0] if self._rows else None

    def fetchall(self):
        return self._rows


class _Connection:
    def __init__(self):
        self.rows = []

    def execute(self, sql, params=()):
        if sql.startswith("SELECT 1 FROM workspace_connectors"):
            return _Result([(1,)] if self.rows else [])
        if sql.startswith("INSERT INTO workspace_connectors"):
            if not any(row[2] == params[2] for row in self.rows):
                self.rows.append((params[0], params[1], params[2], params[3], params[4], params[5], params[6], params[7], params[8], params[9]))
            return _Result(rowcount=1)
        if sql.startswith("SELECT connector_id,kind,name,status"):
            return _Result([(row[2], row[3], row[4], row[6], row[7], row[5], row[8], row[9]) for row in self.rows])
        raise AssertionError(f"unexpected SQL: {sql}")


class _Store:
    def __init__(self):
        self.connection = _Connection()

    @contextmanager
    def _transaction(self, _context):
        yield self.connection


class ConnectorPersistenceTests(unittest.TestCase):
    def test_defaults_are_not_recreated_after_user_deletion(self):
        store = _Store()
        repository = PostgresConnectorRepository(store)
        mcp = ConnectorView("mcp-open-law-go-kr", "mcp", "국가법령정보센터", "unavailable", 0, "open.law.go.kr", None, "CONNECTOR_CREDENTIAL_REQUIRED")
        approved = ConnectorView("daon-approved-knowledge", "daon_approved_knowledge", "Daon 승인 지식", "unavailable", 0, "Daon", None, "CONNECTOR_CREDENTIAL_REQUIRED")

        first = repository.list("tenant", "workspace", "user", (mcp, approved))
        self.assertEqual({item.connector_id for item in first}, {mcp.connector_id, approved.connector_id})
        store.connection.rows = [row for row in store.connection.rows if row[2] != mcp.connector_id]

        second = repository.list("tenant", "workspace", "user", (mcp, approved))
        self.assertEqual([item.connector_id for item in second], [approved.connector_id])


if __name__ == "__main__":
    unittest.main()
