"""Active-only provider health checks with per-connection failure isolation."""

from __future__ import annotations

from dataclasses import dataclass
import asyncio
from typing import Callable, Iterable, Protocol, TypeVar


class _Connection(Protocol):
    connection_id: str
    enabled: bool


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
