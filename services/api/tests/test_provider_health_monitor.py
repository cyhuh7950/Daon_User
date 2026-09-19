from __future__ import annotations

from dataclasses import dataclass
import asyncio

from daon_user_api.provider_health_monitor import ProviderHealthMonitor


@dataclass(frozen=True)
class Connection:
    connection_id: str
    enabled: bool


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
