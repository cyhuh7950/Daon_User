from __future__ import annotations

from dataclasses import dataclass
from uuid import uuid4


class IOSCaptureError(ValueError):
    pass


@dataclass(frozen=True)
class IOSCaptureResult:
    source_id: str
    kind: str
    status: str
    time_segment: str | None = None


class IOSCapture:
    def __init__(self) -> None:
        self._pending = 0

    def capture(
        self,
        kind: str,
        *,
        permission: bool,
        online: bool,
        asr_ready: bool = False,
        time_segment: str | None = None,
    ) -> IOSCaptureResult:
        if kind not in {"file", "photo", "audio"}:
            raise IOSCaptureError("CAPTURE_KIND_INVALID")
        if not permission:
            raise IOSCaptureError("PERMISSION_REQUIRED")
        if kind == "audio" and (not asr_ready or not time_segment):
            raise IOSCaptureError("AUDIO_NOT_READY")
        status = "captured" if online else "queued_offline"
        if not online:
            self._pending += 1
        return IOSCaptureResult(f"src-ios-{uuid4().hex}", kind, status, time_segment)

    def reconnect(self) -> str:
        if self._pending:
            self._pending = 0
            return "sync_pending"
        return "nothing_to_sync"
