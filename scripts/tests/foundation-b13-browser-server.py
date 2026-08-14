from __future__ import annotations

import importlib.util
import sys
from http.server import ThreadingHTTPServer
from pathlib import Path
from urllib.parse import urlparse

MODULE_PATH = Path(__file__).with_name("foundation-b12-browser-server.py")
SPEC = importlib.util.spec_from_file_location("foundation_b12_browser_server", MODULE_PATH)
if SPEC is None or SPEC.loader is None:
    raise RuntimeError("B13_BROWSER_FIXTURE_UNAVAILABLE")
B12 = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(B12)
B12.B11.B10.B9.B8.B4.WORKSPACE_ID = "workspace-b13"
B12.B11.B10.B9.B8.B4.TRACE_ID = "trace-b13-browser"


def policy_payload(*, max_bytes: int = 4096) -> dict[str, object]:
    return {
        "mode": "allow_approved_external", "allowed_provider_kinds": ["external_api"],
        "allowed_destinations": ["api.example.com"], "classification": "internal",
        "max_bytes": max_bytes, "masking_required": True, "redaction_required": False,
        "required_approver": "organization_admin",
    }


def effective_policy() -> dict[str, object]:
    organization = policy_payload()
    workspace = policy_payload(max_bytes=2048)
    return {
        "organization_policy_version_id": "org-policy-b13", "organization_binding_id": "org-binding-b13",
        "workspace_policy_version_id": "workspace-policy-b13", "workspace_binding_id": "workspace-binding-b13",
        **workspace, "fingerprint": "sha256:" + "a" * 64, "parent_locked": False,
        "organization_etag": '"org-b13:1"', "workspace_etag": '"workspace-b13:1"',
        "organization_policy": organization, "workspace_policy": workspace,
    }


class Handler(B12.Handler):
    server_version = "DaonB13Evidence/1"

    def do_GET(self) -> None:  # noqa: N802
        if urlparse(self.path).path == "/api/v1/workspaces/workspace-b13/egress-policy":
            self._send_json(B12.B11.B10.B9.B8.B4.envelope(effective_policy()))
            print("EGRESS_POLICY_VIEWED=organization-read-only", flush=True)
            return
        super().do_GET()


if __name__ == "__main__":
    port = int(sys.argv[1]) if len(sys.argv) > 1 else 18493
    print(f"READY {port}", flush=True)
    ThreadingHTTPServer(("127.0.0.1", port), Handler).serve_forever()
