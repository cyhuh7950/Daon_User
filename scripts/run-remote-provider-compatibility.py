#!/usr/bin/env python3
"""Secret-free, single-call representative Provider compatibility probe."""

from __future__ import annotations

import json
import os
import sys
import urllib.error
import urllib.request
from pathlib import Path


PROVIDERS = (
    ("UPSTAGE", "UPSTAGE_API_KEY", "https://api.upstage.ai/v1/chat/completions", "solar-pro2"),
    ("GROQ", "GROQ_API_KEY", "https://api.groq.com/openai/v1/chat/completions", "llama-3.3-70b-versatile"),
    ("MISTRAL", "MISTRAL_API_KEY", "https://api.mistral.ai/v1/chat/completions", "mistral-small-latest"),
)


def dotenv(path: Path) -> dict[str, str]:
    result: dict[str, str] = {}
    for raw in path.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        name, value = line.split("=", 1)
        result[name.strip()] = value.strip().strip("'\"")
    return result


def main() -> int:
    if len(sys.argv) != 2:
        print("PROVIDER_COMPATIBILITY_INPUT_INVALID")
        return 2
    values = dotenv(Path(sys.argv[1]))
    selected = next((item for item in PROVIDERS if values.get(item[1])), None)
    configured = ",".join(item[0] for item in PROVIDERS if values.get(item[1])) or "none"
    print(f"PROVIDER_COMPATIBILITY_PREFLIGHT configured={configured}")
    if selected is None:
        print("PROVIDER_COMPATIBILITY_NOT_RUN reason=credential_unavailable")
        return 3
    provider, key_name, url, model = selected
    key = values[key_name]
    payload = {
        "model": model,
        "messages": [
            {"role": "system", "content": "Respond briefly to this greeting. Do not claim to use sources."},
            {"role": "user", "content": "안녕하세요!"},
        ],
        "response_format": {
            "type": "json_schema",
            "json_schema": {
                "name": "daon_general_conversation_answer", "strict": True,
                "schema": {
                    "type": "object", "properties": {"answer": {"type": "string"}},
                    "required": ["answer"], "additionalProperties": False,
                },
            },
        },
    }
    request = urllib.request.Request(
        url, data=json.dumps(payload, ensure_ascii=False, separators=(",", ":")).encode("utf-8"),
        headers={"Authorization": f"Bearer {key}", "Content-Type": "application/json"},
        method="POST",
    )
    try:
        with urllib.request.urlopen(request, timeout=60) as response:
            outer = json.loads(response.read(1024 * 1024))
            raw = outer["choices"][0]["message"]["content"]
            answer = json.loads(raw) if isinstance(raw, str) else raw
            valid = isinstance(answer, dict) and set(answer) == {"answer"} and isinstance(answer["answer"], str) and bool(answer["answer"].strip()) and len(answer["answer"]) <= 8_000
            print(f"PROVIDER_COMPATIBILITY_RESULT provider={provider} http={response.status} schema={'valid' if valid else 'invalid'} citations=0 secret_echo=0")
            return 0 if valid else 5
    except urllib.error.HTTPError as error:
        print(f"PROVIDER_COMPATIBILITY_RESULT provider={provider} http={error.code} schema=not_checked citations=0 secret_echo=0")
        return 6
    except (KeyError, IndexError, TypeError, ValueError, json.JSONDecodeError, urllib.error.URLError, TimeoutError):
        print(f"PROVIDER_COMPATIBILITY_RESULT provider={provider} http=unavailable schema=not_checked citations=0 secret_echo=0")
        return 7
    finally:
        key = ""
        values.clear()


if __name__ == "__main__":
    raise SystemExit(main())
