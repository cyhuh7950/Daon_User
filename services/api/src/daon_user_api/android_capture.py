from __future__ import annotations

from dataclasses import dataclass
from uuid import uuid4


class CaptureError(ValueError):
    pass


@dataclass(frozen=True)
class CaptureResult:
    source_id: str
    kind: str
    source_version: int
    status: str
    time_segment: str | None = None


class AndroidCapture:
    def capture(
        self,
        kind: str,
        *,
        permission: bool,
        source_version: int,
        asr_ready: bool = False,
        time_segment: str | None = None,
    ) -> CaptureResult:
        if kind not in {"file", "photo", "audio"}:
            raise CaptureError("CAPTURE_KIND_INVALID")
        if not permission:
            raise CaptureError("PERMISSION_REQUIRED")
        if source_version < 1:
            raise CaptureError("SOURCE_VERSION_INVALID")
        if kind == "audio" and (not asr_ready or not time_segment):
            raise CaptureError("AUDIO_NOT_READY")
        return CaptureResult(f"src-android-{uuid4().hex}", kind, source_version, "captured", time_segment)
