from __future__ import annotations

from dataclasses import dataclass
import asyncio

from daon_user_api.provider_health_monitor import ProviderHealthMonitor
from daon_user_api.provider_health_settings import ProviderHealthCheckSettings


@dataclass(frozen=True)
class Connection:
    connection_id: str
    enabled: bool


def test_readiness_consumer_is_bounded_hook_after_existing_checks_and_failure_is_isolated():
    for failed in (False, True):
        events = []
        def consume():
            events.append("consume")
            if failed:
                raise RuntimeError("PRIVATE_DATABASE_DETAIL")
        monitor = ProviderHealthMonitor(
            list_connections=lambda: [Connection("active", True), Connection("off", False)],
            check_connection=lambda item: events.append("verify") or "verified",
            readiness_consumer=consume)
        result = monitor.run_once()
        assert events == ["verify", "consume"]
        assert [(item.connection_id, item.status) for item in result] == [("active", "verified")]
        assert monitor.readiness_consumer_status == ("unavailable" if failed else "completed")
        assert "PRIVATE_DATABASE_DETAIL" not in repr(result)


def test_zero_health_interval_consumes_existing_events_without_new_provider_checks():
    async def scenario():
        stop = asyncio.Event()
        checks, consumes = [], []
        def consume():
            consumes.append("existing_event_poll")
            stop.set()
        monitor = ProviderHealthMonitor(
            list_connections=lambda: checks.append("list") or [],
            check_connection=lambda item: checks.append("verify") or "verified",
            readiness_consumer=consume)
        await asyncio.wait_for(monitor.run_with_settings(stop, lambda: ProviderHealthCheckSettings(0, 1),
            poll_seconds=0.01), 1)
        assert checks == [] and consumes == ["existing_event_poll"]
    asyncio.run(scenario())


def test_monitor_checks_only_active_connections() -> None:
    checked: list[str] = []
    monitor = ProviderHealthMonitor(
        list_connections=lambda: [Connection("active", True), Connection("off", False)],
        check_connection=lambda item: checked.append(item.connection_id) or "verified",
    )

    result = monitor.run_once()

    assert checked == ["active"]
    assert [item.connection_id for item in result] == ["active"]


def test_monitor_isolates_provider_failures() -> None:
    checked: list[str] = []

    def check(item: Connection) -> str:
        checked.append(item.connection_id)
        if item.connection_id == "broken":
            raise RuntimeError("upstream unavailable")
        return "verified"

    monitor = ProviderHealthMonitor(
        list_connections=lambda: [Connection("broken", True), Connection("healthy", True)],
        check_connection=check,
    )

    result = monitor.run_once()

    assert checked == ["broken", "healthy"]
    assert [(item.connection_id, item.status) for item in result] == [
        ("broken", "failed"), ("healthy", "verified")
    ]


def test_monitor_waits_for_interval_before_first_check() -> None:
    async def scenario() -> None:
        calls: list[str] = []
        stop_event = asyncio.Event()
        monitor = ProviderHealthMonitor(
            list_connections=lambda: [Connection("active", True)],
            check_connection=lambda item: calls.append(item.connection_id) or "verified",
        )
        task = asyncio.create_task(monitor.run_forever(stop_event, 0.05))

        await asyncio.sleep(0.01)
        assert calls == []
        await asyncio.sleep(0.06)
        stop_event.set()
        await task
        assert calls == ["active"]

    asyncio.run(scenario())


def test_dynamic_schedule_skips_zero_and_resumes_after_positive_setting() -> None:
    async def scenario() -> None:
        calls: list[str] = []
        stop_event = asyncio.Event()
        current = ProviderHealthCheckSettings(1, 1)
        monitor = ProviderHealthMonitor(
            list_connections=lambda: [Connection("active", True)],
            check_connection=lambda item: calls.append(item.connection_id) or "verified",
        )
        task = asyncio.create_task(monitor.run_with_settings(
            stop_event, lambda: current, poll_seconds=0.01, seconds_per_minute=0.08,
        ))
        try:
            await asyncio.sleep(0.02)
            current = ProviderHealthCheckSettings(0, 2)
            await asyncio.sleep(0.11)
            assert calls == []
            current = ProviderHealthCheckSettings(1, 3)
            await asyncio.sleep(0.12)
            assert calls, "positive interval should restart periodic checks"
            current = ProviderHealthCheckSettings(0, 4)
            await asyncio.sleep(0.03)
            count_after_disable = len(calls)
            await asyncio.sleep(0.11)
            assert len(calls) == count_after_disable
        finally:
            stop_event.set()
            await asyncio.wait_for(task, 1)

    asyncio.run(scenario())
