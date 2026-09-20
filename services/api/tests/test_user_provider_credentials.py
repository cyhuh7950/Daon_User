from __future__ import annotations

from daon_user_api.user_provider_credentials import resolve_credential_candidates


def test_system_credential_is_preferred_when_available() -> None:
    result = resolve_credential_candidates(b"system-key", b"user-key")

    assert result.credential == b"system-key"
    assert result.source == "system"


def test_user_credential_is_used_only_when_system_credential_is_unavailable() -> None:
    result = resolve_credential_candidates(None, b"user-key")

    assert result.credential == b"user-key"
    assert result.source == "user"


def test_no_credential_is_reported_without_exposing_secret_material() -> None:
    result = resolve_credential_candidates(None, None)

    assert result.credential is None
    assert result.source == "none"
    assert "key" not in repr(result).lower()
