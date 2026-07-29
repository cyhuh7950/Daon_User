"""Uvicorn entry point with bounded graceful shutdown."""

from __future__ import annotations

import signal
import threading
from contextlib import contextmanager
from collections.abc import Callable, Generator
from types import FrameType

import uvicorn
from uvicorn.server import HANDLED_SIGNALS

from .runtime import RuntimeSettings, build_dependencies, create_app


class DaonServer(uvicorn.Server):
    def __init__(self, config: uvicorn.Config, begin_shutdown: Callable[[], None]) -> None:
        super().__init__(config)
        self._begin_shutdown = begin_shutdown

    def handle_exit(self, sig: int, frame: FrameType | None) -> None:
        self._begin_shutdown()
        super().handle_exit(sig, frame)

    @contextmanager
    def capture_signals(self) -> Generator[None]:
        """Handle OS signals once and return zero after the graceful lifecycle.

        Uvicorn's default context re-raises captured signals after application
        shutdown.  On Windows SIGBREAK that converts a completed graceful
        lifecycle into process exit code 3, which violates the runtime contract.
        """
        if threading.current_thread() is not threading.main_thread():
            yield
            return
        original_handlers = {
            handled_signal: signal.signal(handled_signal, self.handle_exit)
            for handled_signal in HANDLED_SIGNALS
        }
        try:
            yield
        finally:
            for handled_signal, handler in original_handlers.items():
                signal.signal(handled_signal, handler)


def run() -> None:
    settings = RuntimeSettings.from_env()
    dependencies = build_dependencies(settings)
    app = create_app(dependencies)
    forwarded_allow_ips = (
        ",".join(settings.trusted_proxy_ips)
        if settings.profile == "production"
        else ""
    )
    config = uvicorn.Config(
        app,
        host=settings.bind_host,
        port=settings.port,
        proxy_headers=settings.profile == "production",
        forwarded_allow_ips=forwarded_allow_ips,
        access_log=False,
        timeout_graceful_shutdown=int(settings.drain_timeout_seconds),
    )
    DaonServer(config, dependencies.state.begin_shutdown).run()


if __name__ == "__main__":
    run()
