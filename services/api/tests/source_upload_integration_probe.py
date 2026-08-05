"""Safe production-like CP3 Source upload probe; never prints environment values."""

from __future__ import annotations

import argparse
import json
import re

from daon_user_api.runtime import RuntimeSettings, build_dependencies


SAFE_SUFFIX = re.compile(r"^[a-z0-9][a-z0-9-]{5,63}$")


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--suffix", required=True)
    arguments = parser.parse_args()
    if SAFE_SUFFIX.fullmatch(arguments.suffix) is None:
        raise SystemExit("PROBE_SUFFIX_INVALID")

    dependencies = build_dependencies(RuntimeSettings.from_env())
    try:
        service = dependencies.source_upload_service
        if service is None:
            raise SystemExit("SOURCE_UPLOAD_SERVICE_UNAVAILABLE")
        result = service.register_pdf(
            tenant_id=f"tenant-{arguments.suffix}",
            workspace_id=f"workspace-{arguments.suffix}",
            actor_id=f"actor-{arguments.suffix}",
            filename="cp3.pdf",
            content=b"%PDF-1.7\nCP3 fixture\n%%EOF",
            idempotency_key=f"upload-{arguments.suffix}",
            trace_id=f"trace-{arguments.suffix}",
        )
        print(json.dumps({
            "source_id": result.source_id,
            "source_version_id": result.source_version_id,
            "object_id": result.object_id,
            "digest_sha256": result.digest_sha256,
            "byte_size": result.byte_size,
            "status": result.status,
            "replayed": result.replayed,
        }, sort_keys=True))
    finally:
        dependencies.close()


if __name__ == "__main__":
    main()
