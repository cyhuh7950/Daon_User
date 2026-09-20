"""One-time, idempotent import of legacy Provider environment credentials.

The command intentionally imports only into already-migrated connections. It
never creates endpoints and never prints credential material.
"""

from __future__ import annotations

import hashlib
import os
from pathlib import Path

import psycopg
from psycopg.types.json import Jsonb

from daon_user_api.provider_credentials import ProviderCredentialCipher


_LEGACY_ENV_BY_PROVIDER = {
    "ANTHROPIC": "ANTHROPIC_API_KEY",
    "CEREBRAS": "CEREBRAS_API_KEY",
    "GEMINI": "GEMINI_API_KEY",
    "GROQ": "GROQ_API_KEY",
    "MISTRAL": "MISTRAL_API_KEY",
    "OPENAI": "OPENAI_API_KEY",
    "OPENROUTER": "OPENROUTER_API_KEY",
    "UPSTAGE": "UPSTAGE_API_KEY",
}


def _required_env(name: str) -> str:
    value = os.environ.get(name, "").strip()
    if not value:
        raise SystemExit(f"{name}_REQUIRED")
    return value


def _collect_credentials() -> dict[str, str]:
    collected: dict[str, str] = {}
    for provider_code, env_name in _LEGACY_ENV_BY_PROVIDER.items():
        value = os.environ.get(env_name, "").strip()
        if value:
            collected[provider_code] = value
        os.environ.pop(env_name, None)
    return collected


def main() -> None:
    dsn = _required_env("DAON_CLOUD_DATABASE_DSN")
    key_path = Path(_required_env("DAON_PROVIDER_CREDENTIAL_KEY_FILE"))
    try:
        master_key = key_path.read_bytes()
    except OSError:
        raise SystemExit("PROVIDER_CREDENTIAL_KEY_REFERENCE_UNAVAILABLE") from None
    cipher = ProviderCredentialCipher(master_key, encryption_key_version=1)
    credentials = _collect_credentials()
    imported = 0
    skipped = 0

    if not credentials:
        print(
            "PROVIDER_CREDENTIAL_IMPORT_COMPLETED "
            "imported=0 skipped=0 unused_provider_types=0"
        )
        return

    with psycopg.connect(dsn) as connection:
        rows = connection.execute(
            "SELECT connection_id,provider_code,credential_version "
            "FROM system_provider_connections "
            "WHERE provider_code = ANY(%s) ORDER BY connection_id FOR UPDATE",
            (list(credentials),),
        ).fetchall()
        seen_provider_codes = {str(row[1]) for row in rows}
        for connection_id_value, provider_code_value, version_value in rows:
            connection_id = str(connection_id_value)
            provider_code = str(provider_code_value)
            event_id = "provider-import-" + hashlib.sha256(
                f"audit|{connection_id}".encode("utf-8")
            ).hexdigest()[:32]
            if connection.execute(
                "SELECT 1 FROM system_provider_audit_outbox WHERE event_id=%s",
                (event_id,),
            ).fetchone() is not None:
                skipped += 1
                continue
            raw = credentials[provider_code]
            next_version = int(version_value) + 1
            sealed = cipher.encrypt(
                connection_id, provider_code, next_version, raw.encode("utf-8"),
            )
            trace_id = "provider-import-" + hashlib.sha256(
                connection_id.encode("utf-8")
            ).hexdigest()[:32]
            updated = connection.execute(
                "UPDATE system_provider_connections SET "
                "encrypted_credential=%s,credential_nonce=%s,encryption_key_version=%s,"
                "credential_schema_version=%s,credential_version=%s,"
                "verification_status='unverified',verified_at=NULL,version=version+1,"
                "updated_at=now(),updated_by='operator:legacy-provider-import',"
                "trace_id=%s,policy_version='provider-credential-import-v1' "
                "WHERE connection_id=%s AND encrypted_credential IS NULL RETURNING version",
                (
                    sealed.ciphertext, sealed.nonce, sealed.encryption_key_version,
                    sealed.schema_version, sealed.credential_version, trace_id, connection_id,
                ),
            ).fetchone()
            if updated is None:
                skipped += 1
                continue
            connection.execute(
                "INSERT INTO system_provider_audit_outbox "
                "(event_id,tenant_id,actor_id,action,target_type,target_id,trace_id,"
                "policy_version,audit_payload) VALUES (%s,'system',%s,%s,%s,%s,%s,%s,%s) "
                "ON CONFLICT (event_id) DO NOTHING",
                (
                    event_id, "operator:legacy-provider-import",
                    "provider_connection.credential_imported", "provider_connection",
                    connection_id, trace_id, "provider-credential-import-v1",
                    Jsonb({"version": int(updated[0]), "reason_code": "LEGACY_ENV_IMPORT"}),
                ),
            )
            imported += 1

    unused = len(set(credentials) - seen_provider_codes)
    print(
        "PROVIDER_CREDENTIAL_IMPORT_COMPLETED "
        f"imported={imported} skipped={skipped} unused_provider_types={unused}"
    )


if __name__ == "__main__":
    main()
