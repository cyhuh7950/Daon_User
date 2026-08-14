from __future__ import annotations

import os

import psycopg

from daon_user_api.cloud_storage import PostgresCloudStore
from daon_user_api.output_version_settings import (
    DEFAULT_OUTPUT_FORMATS,
    OutputVersionSettingsContext,
    OutputVersionSettingsError,
    OutputVersionSettingsService,
)
from daon_user_api.output_version_settings_postgres import PostgresOutputVersionSettingsRepository


def main() -> None:
    dsn = os.environ["DAON_OUTPUT_SETTINGS_TEST_DSN"]
    with psycopg.connect(dsn) as connection:
        connection.execute("INSERT INTO tenants (tenant_id,display_name) VALUES ('tenant-output','Output'),('tenant-output-other','Other')")
        connection.execute("INSERT INTO workspaces (tenant_id,workspace_id,display_name) VALUES ('tenant-output','workspace-output','Output'),('tenant-output-other','workspace-output-other','Other')")
    store = PostgresCloudStore(dsn)
    try:
        service = OutputVersionSettingsService(PostgresOutputVersionSettingsRepository(store))
        context = OutputVersionSettingsContext("tenant-output", "workspace-output", "actor-output")
        assert service.get(context).version == 0
        formats = {**DEFAULT_OUTPUT_FORMATS, "evidence_report": "docx"}
        first = service.save(context, formats, expected_version=0, idempotency_key="output-settings-gate-0001")
        assert first.version == 1 and first.default_formats == formats
        assert service.save(context, formats, expected_version=0, idempotency_key="output-settings-gate-0001") == first
        try:
            service.save(context, formats, expected_version=1, idempotency_key="output-settings-gate-0001")
        except OutputVersionSettingsError as error:
            assert error.code == "IDEMPOTENCY_KEY_REUSED"
        else:
            raise AssertionError("changed expected version replay unexpectedly succeeded")
    finally:
        store.close()

    with psycopg.connect(dsn) as connection:
        connection.execute(
            "INSERT INTO workspace_output_version_settings (tenant_id,workspace_id,default_formats,version_save_mode,version,updated_by,updated_at) VALUES ('tenant-output-other','workspace-output-other',%s::jsonb,'append_only',1,'actor-other',now())",
            ("{\"evidence_report\":\"pdf\",\"compliance_checklist\":\"xlsx\",\"comparison_table\":\"xlsx\",\"knowledge_graph\":\"json\",\"business_draft\":\"docx\"}",),
        )
        with connection.transaction():
            connection.execute("SET LOCAL ROLE daon_app")
            connection.execute("SELECT set_config('app.tenant_id','tenant-output',true)")
            connection.execute("SELECT set_config('app.workspace_id','workspace-output',true)")
            own = connection.execute("SELECT count(*) FROM workspace_output_version_settings").fetchone()[0]
            assert own == 1
        with connection.transaction():
            connection.execute("SET LOCAL ROLE daon_app")
            connection.execute("SELECT set_config('app.tenant_id','tenant-output',true)")
            connection.execute("SELECT set_config('app.workspace_id','workspace-output',true)")
            cross = connection.execute(
                "SELECT count(*) FROM workspace_output_version_settings WHERE workspace_id='workspace-output-other'"
            ).fetchone()[0]
            assert cross == 0
    print("OUTPUT_VERSION_SETTINGS_PG_GATE PASS version=1 replay=exact rls_own=1 rls_cross=0")


if __name__ == "__main__":
    main()
