from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
import hashlib
import ipaddress
import re
import time
from html.parser import HTMLParser
from typing import Callable
from urllib.parse import urlparse, urlsplit, urljoin

from . import safe_http_fetch
from .safe_http_fetch import SafeHttpTransport, WebsiteFetchError


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


@dataclass(frozen=True)
class WebsiteImportContext:
    tenant_id: str
    workspace_id: str
    notebook_id: str
    actor_id: str
    trace_id: str
    policy_version: str


@dataclass(frozen=True)
class WebsiteFetchPolicy:
    fingerprint: str
    allowed_hosts: tuple[str, ...]
    max_bytes: int

    def __post_init__(self):
        # mutable allowlist를 frozen 객체에 남기지 않는다.
        if not isinstance(self.allowed_hosts, tuple):
            raise WebsiteFetchError('WEBSITE_FETCH_BLOCKED')


@dataclass(frozen=True)
class FetchedWebsite:
    requested_url: str
    final_url: str
    redirect_urls: tuple[str, ...]
    title: str
    published_at: str | None
    license_status: str
    license_url: str | None
    fetched_at: str
    raw_content: bytes
    text_content: bytes
    raw_sha256: str
    text_sha256: str
    extraction_version: str


class _WebsiteText(HTMLParser):
    _removed = {'script', 'style', 'noscript', 'svg', 'canvas', 'form'}
    _blocks = {'p', 'div', 'section', 'article', 'header', 'footer', 'aside', 'main',
               'h1', 'h2', 'h3', 'h4', 'h5', 'h6', 'br', 'li', 'tr', 'td', 'th', 'pre'}

    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.ignored: list[str] = []
        self.in_head = False
        self.in_title = False
        self.text = bytearray()
        self.title: list[str] = []
        self.pending = ''

    def _add(self, value):
        # separator/공백은 실제 다음 글자가 있을 때만 확정한다. 상한에 있는
        # 마지막 문단의 닫는 tag가 불필요한 newline 한 byte로 거부되지 않는다.
        for char in value:
            if char.isspace():
                if self.text and self.pending != '\n':
                    self.pending = '\n' if char in '\r\n' else ' '
                continue
            piece = (self.pending + char).encode('utf-8')
            if len(piece) > safe_http_fetch.MAX_TEXT - len(self.text):
                raise WebsiteFetchError('WEBSITE_CONTENT_TOO_LARGE')
            self.text.extend(piece)
            self.pending = ''

    def handle_starttag(self, tag, attrs):
        if tag in self._removed:
            self.ignored.append(tag)
        if self.ignored:
            return
        if tag == 'head':
            self.in_head = True
        if tag == 'title':
            self.in_title = True
        if tag in self._blocks and self.text:
            self.pending = '\n'

    def handle_startendtag(self, tag, attrs):
        self.handle_starttag(tag, attrs)
        self.handle_endtag(tag)

    def handle_endtag(self, tag):
        if self.ignored:
            if tag == self.ignored[-1]:
                self.ignored.pop()
            return
        if tag == 'head':
            self.in_head = False
        if tag == 'title':
            self.in_title = False
        if tag in self._blocks and self.text:
            self.pending = '\n'

    def handle_data(self, data):
        if self.ignored:
            return
        if self.in_title:
            self.title.append(data)
        elif not self.in_head:
            self._add(data)


def _extract_website(response, final_url):
    headers = safe_http_fetch.checked_headers(response.headers)
    if headers.get('content-encoding', 'identity').lower() != 'identity':
        raise WebsiteFetchError('WEBSITE_MEDIA_UNSUPPORTED')
    content_type = headers.get('content-type', '')
    match = re.fullmatch(r'(text/html|text/plain)(?:\s*;\s*charset\s*=\s*(?:"(utf-8)"|(utf-8)))?\s*', content_type, re.I)
    if match is None:
        raise WebsiteFetchError('WEBSITE_MEDIA_UNSUPPORTED')
    if match[1].lower() == 'text/plain' and len(response.body) > safe_http_fetch.MAX_TEXT:
        raise WebsiteFetchError('WEBSITE_CONTENT_TOO_LARGE')
    try:
        decoded = response.body.decode('utf-8', errors='strict')
    except UnicodeError:
        raise WebsiteFetchError('WEBSITE_MEDIA_UNSUPPORTED') from None
    if match[1].lower() == 'text/html':
        parser = _WebsiteText()
        parser.feed(decoded)
        parser.close()
        text = parser.text.decode('utf-8')
        title = ' '.join(''.join(parser.title).split()) or final_url
    else:
        text = decoded
        title = final_url
    if len(text) > safe_http_fetch.MAX_TEXT:
        raise WebsiteFetchError('WEBSITE_CONTENT_TOO_LARGE')
    content = text.encode('utf-8')
    if len(content) > safe_http_fetch.MAX_TEXT:
        raise WebsiteFetchError('WEBSITE_CONTENT_TOO_LARGE')
    dynamic_shell = (r'(?:(?:please\s+)?(?:enable|turn on)\s+javascript(?:\s+to\s+(?:continue|view (?:this )?(?:page|site)))?'
                     r'|you need to enable javascript to run this app'
                     r'|이 페이지를 보려면\s*javascript를 활성화하세요)[.!]?')
    if not text.strip() or re.fullmatch(dynamic_shell, text.strip(), re.I):
        raise WebsiteFetchError('WEBSITE_TEXT_UNAVAILABLE')
    # 발행/License 정보는 검증할 수 없는 값을 추정하거나 수집 시각으로 채우지 않는다.
    return title, content


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

    def fetch_public_page(
        self, context: WebsiteImportContext, url: str, policy: WebsiteFetchPolicy,
        transport: SafeHttpTransport, authorize_hop: Callable[[str, WebsiteFetchPolicy], None],
    ) -> FetchedWebsite:
        deadline = time.monotonic() + 8.0
        if (not isinstance(context, WebsiteImportContext)
                or any(not isinstance(value, str) or not value.strip() for value in vars(context).values())
                or not isinstance(policy, WebsiteFetchPolicy)
                or not isinstance(policy.fingerprint, str) or not policy.fingerprint.strip()
                or not policy.allowed_hosts or not isinstance(policy.allowed_hosts, tuple)
                or type(policy.max_bytes) is not int or not 0 < policy.max_bytes <= safe_http_fetch.MAX_BODY
                or not callable(authorize_hop)):
            raise WebsiteFetchError('WEBSITE_FETCH_BLOCKED')
        frozen = (policy.fingerprint, policy.allowed_hosts, policy.max_bytes)
        if isinstance(transport, SafeHttpTransport):
            transport = transport._with_max_bytes(frozen[2])
        requested_url = safe_http_fetch.normalize_public_url(url)
        current = requested_url
        visited = set()
        redirects = []

        def authorize(target):
            safe_http_fetch.remaining(deadline)
            if (policy.fingerprint, policy.allowed_hosts, policy.max_bytes) != frozen:
                raise WebsiteFetchError('WEBSITE_FETCH_BLOCKED')
            if urlsplit(target).hostname not in frozen[1]:
                raise WebsiteFetchError('WEBSITE_FETCH_BLOCKED')
            try:
                outcome = authorize_hop(target, policy)
                if outcome is not None:
                    raise WebsiteFetchError('WEBSITE_FETCH_BLOCKED')
            except Exception:
                raise WebsiteFetchError('WEBSITE_FETCH_BLOCKED') from None
            if (policy.fingerprint, policy.allowed_hosts, policy.max_bytes) != frozen:
                raise WebsiteFetchError('WEBSITE_FETCH_BLOCKED')
            safe_http_fetch.remaining(deadline)

        while True:
            if current in visited:
                raise WebsiteFetchError('WEBSITE_FETCH_BLOCKED')
            visited.add(current)
            authorize(current)
            ips = safe_http_fetch.validate_public_ips(safe_http_fetch.resolve_public_ips(urlsplit(current).hostname, deadline))
            # DNS 사이의 권한 회수도 실제 TCP/전송 전에 닫는다.
            authorize(current)
            try:
                response = transport.get(current, ips, deadline)
            except WebsiteFetchError:
                raise
            except TimeoutError:
                raise WebsiteFetchError('WEBSITE_FETCH_TIMEOUT') from None
            except Exception:
                raise WebsiteFetchError('WEBSITE_FETCH_FAILED') from None
            safe_http_fetch.remaining(deadline)
            if (policy.fingerprint, policy.allowed_hosts, policy.max_bytes) != frozen:
                raise WebsiteFetchError('WEBSITE_FETCH_BLOCKED')
            headers = safe_http_fetch.checked_headers(response.headers)
            if type(response.status) is not int:
                raise WebsiteFetchError('WEBSITE_FETCH_FAILED')
            if response.status in safe_http_fetch.REDIRECTS:
                if len(redirects) >= 3 or not headers.get('location'):
                    raise WebsiteFetchError('WEBSITE_FETCH_BLOCKED')
                try:
                    current = safe_http_fetch.normalize_public_url(urljoin(current, headers['location']))
                except (ValueError, UnicodeError):
                    # urljoin 자체의 malformed authority 예외도 외부 값을 싣지 않는다.
                    raise WebsiteFetchError('WEBSITE_URL_INVALID') from None
                redirects.append(current)
                continue
            if response.status != 200 or not isinstance(response.body, bytes):
                raise WebsiteFetchError('WEBSITE_FETCH_FAILED')
            if len(response.body) > frozen[2]:
                raise WebsiteFetchError('WEBSITE_CONTENT_TOO_LARGE')
            title, text = _extract_website(response, current)
            safe_http_fetch.remaining(deadline)
            return FetchedWebsite(requested_url, current, tuple(redirects), title, None, 'unknown', None,
                                  datetime.now(timezone.utc).isoformat(), response.body, text,
                                  hashlib.sha256(response.body).hexdigest(), hashlib.sha256(text).hexdigest(),
                                  'website-text-v1')
