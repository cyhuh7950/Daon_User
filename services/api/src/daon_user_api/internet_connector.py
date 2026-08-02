from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
import hashlib
import ipaddress
from urllib.parse import urlparse


class UrlRejected(ValueError):
    pass


@dataclass(frozen=True)
class SafeFetchSnapshot:
    url: str
    published_at: str
    fetched_at: str
    license: str
    content_digest: str
    version: int


class InternetConnector:
    def __init__(self) -> None:
        self._versions: dict[str, int] = {}

    def validate_url(self, url: str) -> str:
        parsed = urlparse(url)
        if parsed.scheme != "https" or not parsed.hostname:
            raise UrlRejected("SAFE_FETCH_BLOCKED")
        host = parsed.hostname.lower()
        if host in {"localhost", "localhost.localdomain"}:
            raise UrlRejected("SAFE_FETCH_BLOCKED")
        try:
            address = ipaddress.ip_address(host)
        except ValueError:
            address = None
        if address is not None and (address.is_private or address.is_loopback or address.is_link_local or address.is_reserved):
            raise UrlRejected("SAFE_FETCH_BLOCKED")
        return url

    def validate_redirect(self, original_url: str, redirect_url: str) -> str:
        self.validate_url(original_url)
        return self.validate_url(redirect_url)

    def snapshot(self, url: str, title: str, published_at: str, license: str) -> SafeFetchSnapshot:
        self.validate_url(url)
        if not title or not published_at or not license:
            raise ValueError("SNAPSHOT_METADATA_REQUIRED")
        version = self._versions.get(url, 0) + 1
        self._versions[url] = version
        digest = "sha256:" + hashlib.sha256(f"{url}|{title}|{published_at}|{license}".encode()).hexdigest()
        return SafeFetchSnapshot(url, published_at, datetime.now(timezone.utc).isoformat(), license, digest, version)
