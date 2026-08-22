from __future__ import annotations

from dataclasses import dataclass
import hashlib
import re
from typing import ClassVar
from uuid import uuid4


class SourceRejected(ValueError):
    """Raised when a Source fails the registration security gate."""


@dataclass(frozen=True)
class SourceRecord:
    source_id: str
    tenant_id: str
    version: int
    digest_sha256: str
    status: str
    flags: tuple[str, ...] = ()
    notebook_id: str | None = None
    content_type: str | None = None
    deletion_policy: str = "delete_with_notebook"

    def __post_init__(self) -> None:
        if self.status not in {"accepted", "unavailable"}:
            raise SourceRejected("INVALID_SOURCE_STATUS")
        if self.deletion_policy not in {
            "delete_with_notebook", "retain_after_notebook_delete"
        }:
            raise SourceRejected("INVALID_DELETION_POLICY")

    @property
    def usable(self) -> bool:
        return self.status != "unavailable"


class SourceIngestor:
    _MIME_BY_EXTENSION: ClassVar[dict[str, str]] = {
        ".pdf": "application/pdf",
        ".docx": "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
        ".pptx": "application/vnd.openxmlformats-officedocument.presentationml.presentation",
        ".xlsx": "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        ".csv": "text/csv",
        ".txt": "text/plain",
        ".md": "text/markdown",
        ".png": "image/png",
        ".jpg": "image/jpeg",
        ".jpeg": "image/jpeg",
        ".m4a": "audio/mp4",
        ".wav": "audio/wav",
        ".mp3": "audio/mpeg",
    }
    _INJECTION = re.compile(r"ignore\s+(?:all\s+)?previous instructions|system prompt|reveal password", re.I)

    def __init__(self) -> None:
        self._direct_versions: dict[str, list[SourceRecord]] = {}

    @staticmethod
    def _digest(content: bytes | str) -> str:
        raw = content.encode("utf-8") if isinstance(content, str) else content
        return "sha256:" + hashlib.sha256(raw).hexdigest()

    @staticmethod
    def _extension(filename: str) -> str:
        name = filename.lower().strip()
        if "." not in name or name.endswith("."):
            raise SourceRejected("UNSUPPORTED_FORMAT")
        return "." + name.rsplit(".", 1)[1]

    @classmethod
    def expected_mime_for_filename(cls, filename: str) -> str | None:
        """Return the registered upload MIME for a safe supported filename."""
        if not isinstance(filename, str) or any(char in filename for char in "/\\\x00"):
            return None
        try:
            return cls._MIME_BY_EXTENSION.get(cls._extension(filename))
        except SourceRejected:
            return None

    @staticmethod
    def _signature_matches(extension: str, content: bytes) -> bool:
        if not content:
            return False
        if extension == ".pdf":
            return content.startswith(b"%PDF-")
        if extension in {".docx", ".pptx", ".xlsx"}:
            return content.startswith(b"PK\x03\x04")
        if extension == ".png":
            return content.startswith(b"\x89PNG\r\n\x1a\n")
        if extension in {".jpg", ".jpeg"}:
            return content.startswith(b"\xff\xd8\xff")
        if extension == ".wav":
            return content.startswith(b"RIFF") and content[8:12] == b"WAVE"
        if extension == ".mp3":
            return content.startswith(b"ID3") or content[:2] in {b"\xff\xfb", b"\xff\xf3"}
        if extension == ".m4a":
            return len(content) >= 12 and content[4:8] == b"ftyp"
        return True

    def register_file(
        self,
        filename: str,
        declared_mime: str,
        content: bytes,
        *,
        encrypted: bool = False,
        corrupted: bool = False,
        compression_ratio: float | None = None,
        malware_signature: bool = False,
        notebook_id: str | None = None,
        deletion_policy: str = "delete_with_notebook",
    ) -> SourceRecord:
        extension = self._extension(filename)
        expected_mime = self._MIME_BY_EXTENSION.get(extension)
        if expected_mime is None:
            raise SourceRejected("UNSUPPORTED_FORMAT")
        if declared_mime != expected_mime:
            raise SourceRejected("MIME_MISMATCH")
        if corrupted:
            raise SourceRejected("CORRUPTED_SOURCE")
        if not self._signature_matches(extension, content):
            raise SourceRejected("CORRUPTED_SOURCE")
        if encrypted:
            raise SourceRejected("ENCRYPTED_SOURCE")
        if compression_ratio is not None and compression_ratio > 100:
            raise SourceRejected("COMPRESSION_BOMB")
        if malware_signature:
            raise SourceRejected("MALWARE_DETECTED")
        return SourceRecord(
            source_id=f"src-{uuid4().hex}",
            tenant_id="file-upload",
            version=1,
            digest_sha256=self._digest(content),
            status="accepted",
            notebook_id=notebook_id,
            content_type=declared_mime,
            deletion_policy=deletion_policy,
        )

    @staticmethod
    def unavailable(record: SourceRecord) -> SourceRecord:
        """Keep a missing external source visible without making it usable."""
        return SourceRecord(
            source_id=record.source_id,
            tenant_id=record.tenant_id,
            version=record.version,
            digest_sha256=record.digest_sha256,
            status="unavailable",
            flags=record.flags,
            notebook_id=record.notebook_id,
            content_type=record.content_type,
            deletion_policy=record.deletion_policy,
        )

    def create_direct_input(self, tenant_id: str, text: str) -> SourceRecord:
        if not tenant_id or not text.strip():
            raise SourceRejected("EMPTY_SOURCE")
        flags: list[str] = []
        if self._INJECTION.search(text):
            flags.append("prompt_injection")
        source_id = f"src-{uuid4().hex}"
        record = SourceRecord(source_id, tenant_id, 1, self._digest(text), "accepted", tuple(flags))
        self._direct_versions[source_id] = [record]
        return record

    def edit_direct_input(self, source_id: str, text: str) -> SourceRecord:
        if not text.strip():
            raise SourceRejected("EMPTY_SOURCE")
        versions = self._direct_versions.get(source_id)
        if not versions:
            raise SourceRejected("SOURCE_NOT_FOUND")
        flags = ("prompt_injection",) if self._INJECTION.search(text) else ()
        record = SourceRecord(source_id, versions[0].tenant_id, len(versions) + 1, self._digest(text), "accepted", flags)
        versions.append(record)
        return record

    def reindex(self, source_id: str) -> tuple[str, int]:
        versions = self._direct_versions.get(source_id)
        if not versions:
            raise SourceRejected("SOURCE_NOT_FOUND")
        return source_id, versions[-1].version
