from __future__ import annotations

import pytest

from daon_user_api.provider_health_settings import (
    DEFAULT_PROVIDER_HEALTH_CHECK_INTERVAL_MINUTES,
    ProviderHealthCheckSettingsService,
    ProviderHealthSettingsError,
    ReferenceProviderHealthSettingsRepository,
)


def test_default_interval_is_sixty_minutes() -> None:
    service = ProviderHealthCheckSettingsService(ReferenceProviderHealthSettingsRepository())

    settings = service.get()

    assert settings.interval_minutes == 60
    assert settings.version == 0
    assert DEFAULT_PROVIDER_HEALTH_CHECK_INTERVAL_MINUTES == 60


def test_valid_interval_can_be_saved_with_optimistic_version() -> None:
    service = ProviderHealthCheckSettingsService(ReferenceProviderHealthSettingsRepository())

    saved = service.save(interval_minutes=120, expected_version=0)

    assert saved.interval_minutes == 120
    assert saved.version == 1


@pytest.mark.parametrize("value", [0, -1, 1441, 1.5, True, "60"])
def test_interval_rejects_values_outside_integer_minute_range(value: object) -> None:
    service = ProviderHealthCheckSettingsService(ReferenceProviderHealthSettingsRepository())

    with pytest.raises(ProviderHealthSettingsError, match="PROVIDER_HEALTH_INTERVAL_INVALID"):
        service.save(interval_minutes=value, expected_version=0)  # type: ignore[arg-type]


def test_interval_save_rejects_stale_version() -> None:
    service = ProviderHealthCheckSettingsService(ReferenceProviderHealthSettingsRepository())
    service.save(interval_minutes=60, expected_version=0)

    with pytest.raises(ProviderHealthSettingsError, match="VERSION_CONFLICT"):
        service.save(interval_minutes=120, expected_version=0)
