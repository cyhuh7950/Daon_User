from __future__ import annotations

import json
import sys


request = json.loads(sys.stdin.buffer.read())
required = {"schema_version", "selection", "context", "request"}
if set(request) != required:
    raise SystemExit(2)
response = {
    "schema_version": 1,
    "sections": [{"title": "Summary", "body": "Offline deterministic draft", "unverified": True}],
}
sys.stdout.write(json.dumps(response, sort_keys=True, separators=(",", ":")))
