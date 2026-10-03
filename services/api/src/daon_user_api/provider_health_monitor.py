"""Active-only provider health checks with per-connection failure isolation."""

from __future__ import annotations

from dataclasses import dataclass
import asyncio
from typing import Callable, Iterable, Protocol, TypeVar


class _Connection(Protocol):
    connection_id: str
    enabled: bool


class _HealthSettings(Protocol):
    interval_minutes: int
    version: int


@dataclass(frozen=True, slots=True)
class ProviderHealthCheckResult:
    connection_id: str
    status: str


@dataclass(frozen=True, slots=True)
class ProviderHealthConnection:
    connection_id: str
    enabled: bool = True


_ConnectionT = TypeVar("_ConnectionT", bound=_Connection)


class ProviderHealthMonitor:
    def __init__(
        self,
        *,
        list_connections: Callable[[], Iterable[_ConnectionT]],
        check_connection: Callable[[_ConnectionT], str],
    ) -> None:
        self._list_connections = list_connections
        self._check_connection = check_connection

    def run_once(self) -> tuple[ProviderHealthCheckResult, ...]:
        results: list[ProviderHealthCheckResult] = []
        for connection in self._list_connections():
            if not connection.enabled:
                continue
            try:
                status = self._check_connection(connection)
            except Exception:
                status = "failed"
            results.append(ProviderHealthCheckResult(connection.connection_id, status))
        return tuple(results)

    async def run_forever(
        self, stop_event: asyncio.Event, interval_seconds: float,
        *, first_run_immediately: bool = False,
    ) -> None:
        first = first_run_immediately
        while True:
            if not first:
                try:
                    await asyncio.wait_for(stop_event.wait(), timeout=interval_seconds)
                except TimeoutError:
                    pass
                if stop_event.is_set():
                    return
            await asyncio.to_thread(self.run_once)
            first = False

    async def run_with_settings(
        self, stop_event: asyncio.Event, get_settings: Callable[[], _HealthSettings],
        *, poll_seconds: float = 60.0, seconds_per_minute: float = 60.0,
    ) -> None:
        loop = asyncio.get_running_loop()
        last_setting: tuple[int, int] | None = None
        next_check_at: float | None = None
        while not stop_event.is_set():
            setting = await asyncio.to_thread(get_settings)
            setting_key = (setting.version, setting.interval_minutes)
            now = loop.time()
            if setting_key != last_setting:
                last_setting = setting_key
                next_check_at = (
                    None if setting.interval_minutes == 0
                    else now + setting.interval_minutes * seconds_per_minute
                )
            if next_check_at is not None and now >= next_check_at:
                await asyncio.to_thread(self.run_once)
                next_check_at = loop.time() + setting.interval_minutes * seconds_per_minute
            wait_seconds = (
                poll_seconds if next_check_at is None
                else min(poll_seconds, max(0.0, next_check_at - loop.time()))
            )
            try:
                await asyncio.wait_for(stop_event.wait(), timeout=wait_seconds)
            except TimeoutError:
                pass
