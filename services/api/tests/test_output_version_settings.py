from __future__ import annotations

import pytest

from daon_user_api.output_version_settings import (
    DEFAULT_OUTPUT_FORMATS,
    OutputVersionSettingsContext,
    OutputVersionSettingsError,
    OutputVersionSettingsService,
    OutputVersionSettingsView,
    ReferenceOutputVersionSettingsRepository,
)


class Repository:
    def __init__(self) -> None:
        self.saved = None

    def read(self, context):
        return None

    def save(self, context, formats, expected_version, idempotency_key):
        self.saved = (context, formats, expected_version, idempotency_key)
        return OutputVersionSettingsView(context.workspace_id, formats, "append_only", expected_version + 1)


def test_output_version_settings_defaults_are_type_specific_and_append_only() -> None:
    view = OutputVersionSettingsService(Repository()).get(
        OutputVersionSettingsContext("tenant-1", "workspace-1", "actor-1")
    )
    assert view.default_formats == DEFAULT_OUTPUT_FORMATS
    assert view.version_save_mode == "append_only"
    assert view.version == 0


def test_output_version_settings_rejects_unsupported_type_format_pair() -> None:
    service = OutputVersionSettingsService(Repository())
    invalid = {**DEFAULT_OUTPUT_FORMATS, "knowledge_graph": "docx"}
    with pytest.raises(OutputVersionSettingsError, match="OUTPUT_VERSION_SETTINGS_INVALID"):
        service.save(
            OutputVersionSettingsContext("tenant-1", "workspace-1", "actor-1"),
            invalid, expected_version=0, idempotency_key="idem-output-settings-1",
        )


def test_output_version_settings_idempotency_binds_formats_and_expected_version() -> None:
    service = OutputVersionSettingsService(ReferenceOutputVersionSettingsRepository())
    context = OutputVersionSettingsContext("tenant-1", "workspace-1", "actor-1")
    first = service.save(
        context, DEFAULT_OUTPUT_FORMATS,
        expected_version=0, idempotency_key="idem-output-settings-1",
    )
    assert first.version == 1
    assert service.save(
        context, DEFAULT_OUTPUT_FORMATS,
        expected_version=0, idempotency_key="idem-output-settings-1",
    ) == first
    with pytest.raises(OutputVersionSettingsError, match="IDEMPOTENCY_KEY_REUSED"):
        service.save(
            context, DEFAULT_OUTPUT_FORMATS,
            expected_version=1, idempotency_key="idem-output-settings-1",
        )
