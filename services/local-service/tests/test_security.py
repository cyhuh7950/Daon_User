from __future__ import annotations

import pytest

from daon_user_local_service.security import (
    MAX_TOKEN_TTL_SECONDS,
    NonceReplayCache,
    TokenError,
    issue_request_token,
    verify_request_token,
)


ROOT_SECRET = "ab" * 32
OTHER_SECRET = "cd" * 32
INSTANCE = "12" * 16
CAPABILITY = "runtime.read"
COMMAND = "runtime.status.read"
NOW = 2_000_000_000


def issue(**overrides: object) -> str:
    values: dict[str, object] = {
        "root_secret": ROOT_SECRET,
        "app_instance_id": INSTANCE,
        "capability": CAPABILITY,
        "command": COMMAND,
        "issued_at": NOW,
        "ttl_seconds": 60,
        "nonce": "34" * 32,
    }
    values.update(overrides)
    return issue_request_token(**values)  # type: ignore[arg-type]


def verify(token: str, **overrides: object) -> None:
    values: dict[str, object] = {
        "token": token,
        "root_secret": ROOT_SECRET,
        "expected_instance_id": INSTANCE,
        "expected_capability": CAPABILITY,
        "expected_command": COMMAND,
        "now": NOW,
        "replay_cache": NonceReplayCache(),
    }
    values.update(overrides)
    verify_request_token(**values)  # type: ignore[arg-type]


def test_bound_token_is_valid_once_within_default_lifetime() -> None:
    token = issue()
    cache = NonceReplayCache()
    verify(token, replay_cache=cache)
    with pytest.raises(TokenError, match="^LOCAL_AUTH_REQUIRED$"):
        verify(token, replay_cache=cache)


@pytest.mark.parametrize(
    ("issued_at", "ttl", "now"),
    [
        (NOW - 60, 60, NOW),
        (NOW + 1, 60, NOW),
        (NOW, MAX_TOKEN_TTL_SECONDS + 1, NOW),
        (NOW, 0, NOW),
    ],
)
def test_expired_future_and_out_of_policy_lifetimes_fail_closed(
    issued_at: int,
    ttl: int,
    now: int,
) -> None:
    if 1 <= ttl <= MAX_TOKEN_TTL_SECONDS:
        token = issue(issued_at=issued_at, ttl_seconds=ttl)
        with pytest.raises(TokenError, match="^LOCAL_AUTH_REQUIRED$"):
            verify(token, now=now)
    else:
        with pytest.raises(TokenError):
            issue(issued_at=issued_at, ttl_seconds=ttl)


@pytest.mark.parametrize(
    "override",
    [
        {"root_secret": OTHER_SECRET},
        {"expected_instance_id": "56" * 16},
        {"expected_capability": "runtime.other"},
        {"expected_command": "runtime.capabilities.read"},
    ],
)
def test_previous_run_and_wrong_claim_bindings_are_indistinguishable(
    override: dict[str, object],
) -> None:
    with pytest.raises(TokenError, match="^LOCAL_AUTH_REQUIRED$"):
        verify(issue(), **override)


def test_tampered_claim_and_signature_fail_closed() -> None:
    token = issue()
    parts = token.split("|")
    claim_tampered = "|".join(parts[:5] + ["runtime.capabilities.read"] + parts[6:])
    signature_tampered = token[:-1] + ("0" if token[-1] != "0" else "1")
    for candidate in (claim_tampered, signature_tampered, "not-a-token"):
        with pytest.raises(TokenError, match="^LOCAL_AUTH_REQUIRED$"):
            verify(candidate)
