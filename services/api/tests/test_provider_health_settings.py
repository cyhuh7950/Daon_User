from __future__ import annotations

import pytest

from daon_user_api.provider_health_settings import (
    DEFAULT_PROVIDER_HEALTH_CHECK_INTERVAL_MINUTES,
    ProviderHealthCheckSettingsService,
    ProviderHealthSettingsError,
    ReferenceProviderHealthSettingsRepository,
)
from daon_user_api.runtime import ProviderHealthSettingsBody


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


def test_zero_disables_periodic_checks_and_can_be_reenabled() -> None:
    service = ProviderHealthCheckSettingsService(ReferenceProviderHealthSettingsRepository())

    disabled = service.save(interval_minutes=0, expected_version=0)
    resumed = service.save(interval_minutes=60, expected_version=disabled.version)

    assert (disabled.interval_minutes, disabled.version) == (0, 1)
    assert (resumed.interval_minutes, resumed.version) == (60, 2)


def test_http_body_accepts_zero_interval() -> None:
    body = ProviderHealthSettingsBody.model_validate({
        "interval_minutes": 0, "expected_version": 1,
    })

    assert body.interval_minutes == 0


@pytest.mark.parametrize("value", [-1, 1441, 1.5, True, "60"])
def test_interval_rejects_values_outside_integer_minute_range(value: object) -> None:
    service = ProviderHealthCheckSettingsService(ReferenceProviderHealthSettingsRepository())

    with pytest.raises(ProviderHealthSettingsError, match="PROVIDER_HEALTH_INTERVAL_INVALID"):
        service.save(interval_minutes=value, expected_version=0)  # type: ignore[arg-type]


def test_interval_save_rejects_stale_version() -> None:
    service = ProviderHealthCheckSettingsService(ReferenceProviderHealthSettingsRepository())
    service.save(interval_minutes=60, expected_version=0)

    with pytest.raises(ProviderHealthSettingsError, match="VERSION_CONFLICT"):
        service.save(interval_minutes=120, expected_version=0)
