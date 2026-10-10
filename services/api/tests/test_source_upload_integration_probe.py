"""CLI contract for the existing Source upload integration probe."""

from __future__ import annotations

import json
import sys
from contextlib import contextmanager
from datetime import datetime, timezone
from types import SimpleNamespace
from unittest.mock import Mock, patch

import pytest

import source_upload_integration_probe as probe
from daon_user_api.notebook import NotebookError


def _dependencies_with_notebook(*, exists: bool) -> tuple[SimpleNamespace, Mock, list[object], list[tuple[str, object]]]:
    service = Mock()
    service.register_pdf.return_value = SimpleNamespace(
        source_id="source-1", source_version_id="version-1", object_id="object-1",
        digest_sha256="digest-1", byte_size=25, status="accepted", replayed=False,
    )
    queries: list[tuple[str, object]] = []
    contexts: list[object] = []
    row = ("notebook-123", "Notebook", datetime.now(timezone.utc), 0, 0, 1) if exists else None
    connection = Mock()
    connection.execute.side_effect = lambda sql, params: (
        queries.append((sql, params)) or SimpleNamespace(fetchone=lambda: row)
    )

    @contextmanager
    def transaction(context: object):
        contexts.append(context)
        yield connection

    cloud_store = SimpleNamespace(_transaction=transaction)
    dependencies = SimpleNamespace(
        source_upload_service=service,
        object_storage=SimpleNamespace(health=lambda: True),
        cloud_store=cloud_store,
        close=Mock(),
    )
    return dependencies, service, contexts, queries


def test_probe_requires_notebook_id_before_building_dependencies(capsys: pytest.CaptureFixture[str]) -> None:
    with patch.object(sys, "argv", ["source_upload_integration_probe.py", "--suffix", "fixture-123"]), patch.object(
        probe, "build_dependencies", side_effect=AssertionError("dependencies must not be built")
    ) as build_dependencies:
        with pytest.raises(SystemExit) as error:
            probe.main()

    assert error.value.code == 2
    assert "--notebook-id" in capsys.readouterr().err
    build_dependencies.assert_not_called()


def test_probe_rejects_empty_notebook_id_before_building_dependencies() -> None:
    with patch.object(sys, "argv", ["source_upload_integration_probe.py", "--suffix", "fixture-123", "--notebook-id", ""]), patch.object(
        probe, "build_dependencies", side_effect=AssertionError("dependencies must not be built")
    ) as build_dependencies:
        with pytest.raises(SystemExit):
            probe.main()

    build_dependencies.assert_not_called()


def test_probe_rejects_cross_scope_notebook_before_object_write() -> None:
    dependencies, service, contexts, queries = _dependencies_with_notebook(exists=False)
    with patch.object(sys, "argv", ["source_upload_integration_probe.py", "--suffix", "fixture-123", "--notebook-id", "other-notebook"]), patch.object(
        probe.RuntimeSettings, "from_env", return_value=object()
    ), patch.object(probe, "build_dependencies", return_value=dependencies):
        with pytest.raises(NotebookError) as error:
            probe.main()

    assert error.value.code == "NOTEBOOK_NOT_FOUND"
    assert [(context.tenant_id, context.workspace_id, context.actor_id) for context in contexts] == [
        ("tenant-fixture-123", "workspace-fixture-123", "actor-fixture-123")
    ]
    assert len(queries) == 1
    assert "WHERE n.tenant_id=%s AND n.workspace_id=%s AND n.notebook_id=%s" in queries[0][0]
    assert queries[0][1] == ("tenant-fixture-123", "workspace-fixture-123", "other-notebook")
    service.register_pdf.assert_not_called()
    dependencies.close.assert_called_once_with()


def test_probe_forwards_existing_notebook_and_synthetic_scope(capsys: pytest.CaptureFixture[str]) -> None:
    dependencies, service, contexts, queries = _dependencies_with_notebook(exists=True)
    with patch.object(
        sys,
        "argv",
        ["source_upload_integration_probe.py", "--suffix", "fixture-123", "--notebook-id", "notebook-123"],
    ), patch.object(probe.RuntimeSettings, "from_env", return_value=object()), patch.object(
        probe, "build_dependencies", return_value=dependencies
    ):
        probe.main()

    assert service.register_pdf.call_args.kwargs == {
        "tenant_id": "tenant-fixture-123",
        "workspace_id": "workspace-fixture-123",
        "notebook_id": "notebook-123",
        "actor_id": "actor-fixture-123",
        "filename": "cp3.pdf",
        "content": b"%PDF-1.7\nCP3 fixture\n%%EOF",
        "idempotency_key": "upload-fixture-123",
        "trace_id": "trace-fixture-123",
    }
    assert [(context.tenant_id, context.workspace_id, context.actor_id) for context in contexts] == [
        ("tenant-fixture-123", "workspace-fixture-123", "actor-fixture-123")
    ]
    assert queries[0][1] == ("tenant-fixture-123", "workspace-fixture-123", "notebook-123")
    assert [json.loads(line) for line in capsys.readouterr().out.splitlines()] == [
        {"stage": "preflight", "object_storage_ready": True},
        {
            "source_id": "source-1",
            "source_version_id": "version-1",
            "object_id": "object-1",
            "digest_sha256": "digest-1",
            "byte_size": 25,
            "status": "accepted",
            "replayed": False,
        },
    ]
    dependencies.close.assert_called_once_with()
