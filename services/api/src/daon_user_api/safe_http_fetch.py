from __future__ import annotations

from dataclasses import dataclass
import ipaddress
import json
import math
import re
import socket
import ssl
import subprocess
import sys
import time
import unicodedata
from types import MappingProxyType
from typing import Mapping
from urllib.parse import quote, unquote, urlsplit, urlunsplit


MAX_HEADERS = 16384
MAX_BODY = 2097152
MAX_TEXT = 524288
REDIRECTS = {301, 302, 303, 307, 308}


class WebsiteFetchError(ValueError):
    """외부 URL/헤더/본문/내부 예외를 포함하지 않는 안전 오류."""


def remaining(deadline: float) -> float:
    if isinstance(deadline, bool) or not isinstance(deadline, (float, int)) or not math.isfinite(deadline):
        raise WebsiteFetchError('WEBSITE_FETCH_TIMEOUT')
    value = deadline - time.monotonic()
    if value <= 0:
        raise WebsiteFetchError('WEBSITE_FETCH_TIMEOUT')
    return min(value, 8.0)


def normalize_public_url(url: str) -> str:
    if not isinstance(url, str) or not url or len(url) > 2048:
        raise WebsiteFetchError('WEBSITE_URL_INVALID')
    if any(ord(c) < 33 for c in url):
        raise WebsiteFetchError('WEBSITE_URL_INVALID')
    # urlsplit은 개행 등을 제거하므로 파싱 전에 원문과 반복 인코딩을 검사한다.
    decoded = url
    for _ in range(len(url) + 1):
        if any(unicodedata.category(c) == 'Cc' or c == '\\' for c in decoded):
            raise WebsiteFetchError('WEBSITE_URL_INVALID')
        next_value = unquote(decoded)
        if next_value == decoded:
            break
        decoded = next_value
    if '?' in url or '#' in url:
        raise WebsiteFetchError('WEBSITE_URL_INVALID')
    try:
        parsed = urlsplit(url)
        if parsed.scheme.lower() != 'https' or not parsed.hostname or parsed.username is not None or parsed.password is not None:
            raise ValueError
        if parsed.port not in (None, 443) or '%' in parsed.netloc or '[' in parsed.netloc:
            raise ValueError
        # ASCII IDNA로 목적지/Host/SNI를 하나로 결속한다. path의 escape는 유지한다.
        host = parsed.hostname.encode('idna').decode('ascii').lower()
        labels = host.split('.')
        if len(labels) < 2 or len(host) > 253 or any(not re.fullmatch(r'[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?', label) for label in labels):
            raise ValueError
        if not re.search(r'[a-z]', labels[-1]) or host == 'localhost.localdomain' or host.endswith('.localhost'):
            raise ValueError
        try:
            ipaddress.ip_address(host)
        except ValueError:
            pass
        else:
            raise ValueError
        authority = parsed.netloc.rsplit(':', 1)
        if len(authority) == 2 and authority[1] != '443':
            raise ValueError
        path = quote(parsed.path or '/', safe="/%:@!$&'()*+,;=-._~")
        if re.search(r'%(?![0-9a-fA-F]{2})', path):
            raise ValueError
        normalized = urlunsplit(('https', host, path, '', ''))
        if len(normalized) > 2048:
            raise ValueError
        return normalized
    except (ValueError, UnicodeError):
        raise WebsiteFetchError('WEBSITE_URL_INVALID') from None


def validate_public_ips(values: tuple[str, ...]) -> tuple[str, ...]:
    if not isinstance(values, tuple) or not values:
        raise WebsiteFetchError('WEBSITE_FETCH_BLOCKED')
    result = []
    for value in values:
        try:
            if not isinstance(value, str) or '%' in value:
                raise ValueError
            address = ipaddress.ip_address(value)
            if (not address.is_global or address.is_multicast or address.is_reserved or address.is_unspecified
                    or (isinstance(address, ipaddress.IPv6Address)
                        and (address.ipv4_mapped is not None or address.sixtofour is not None or address.teredo is not None))):
                raise ValueError
        except ValueError:
            raise WebsiteFetchError('WEBSITE_FETCH_BLOCKED') from None
        canonical = str(address)
        if canonical not in result:
            result.append(canonical)
    return tuple(result)


# getaddrinfo는 thread timeout으로 취소할 수 없다. 독립 Python child만 사용하고
# Windows CREATE_NO_WINDOW / Linux argv, timeout 뒤 kill+reap으로 resolver를 회수한다.
# child는 응답 수/출력 크기를 제한하며 소스/Secret/정책은 전달하지 않는다.
_DNS_PROGRAM = '''import socket,json,sys
try:
    rows=socket.getaddrinfo(sys.argv[1],443,socket.AF_UNSPEC,socket.SOCK_STREAM)
    if not rows or len(rows)>256: sys.exit(1)
    ips=list(dict.fromkeys(row[4][0] for row in rows))
    data=json.dumps(ips,separators=(',',':')).encode('ascii')
    if len(data)>16384: sys.exit(1)
    sys.stdout.buffer.write(data)
except Exception:
    sys.exit(1)
'''


def resolve_public_ips(host: str, deadline: float) -> tuple[str, ...]:
    # 동일 정규 URL 규칙을 적용해 직접 resolver 사용도 임의 인자를 허용하지 않는다.
    if urlsplit(normalize_public_url('https://' + host + '/')).hostname != host:
        raise WebsiteFetchError('WEBSITE_URL_INVALID')
    dns_deadline = min(deadline, time.monotonic() + 2.0)
    remaining(dns_deadline)
    process = None
    try:
        process = subprocess.Popen(
            [sys.executable, '-I', '-S', '-B', '-c', _DNS_PROGRAM, host],
            stdin=subprocess.DEVNULL, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
            creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0), shell=False,
        )
        try:
            data, _ = process.communicate(timeout=remaining(dns_deadline))
        except (subprocess.TimeoutExpired, WebsiteFetchError):
            process.kill()
            # 요청 deadline 만료와 child 회수는 별개다. kill 뒤 반드시 wait한다.
            process.communicate(timeout=2.0)
            raise WebsiteFetchError('WEBSITE_FETCH_TIMEOUT') from None
        remaining(dns_deadline)
        if process.returncode or len(data) > MAX_HEADERS:
            raise WebsiteFetchError('WEBSITE_FETCH_FAILED')
        values = json.loads(data)
        if not isinstance(values, list) or len(values) > 256:
            raise WebsiteFetchError('WEBSITE_FETCH_BLOCKED')
        return validate_public_ips(tuple(values))
    except WebsiteFetchError:
        raise
    except (OSError, ValueError, subprocess.SubprocessError):
        raise WebsiteFetchError('WEBSITE_FETCH_FAILED') from None


@dataclass(frozen=True)
class HttpFetchResponse:
    status: int
    headers: Mapping[str, str]
    body: bytes


def checked_headers(headers: Mapping[str, str]) -> dict[str, str]:
    result: dict[str, str] = {}
    size = 2
    for name, value in headers.items():
        if not isinstance(name, str) or not isinstance(value, str):
            raise WebsiteFetchError('WEBSITE_FETCH_FAILED')
        # len 검사부터 수행해 거대한 header의 encode 복사 할당을 피한다.
        size += len(name) + len(value) + 4
        if size > MAX_HEADERS:
            raise WebsiteFetchError('WEBSITE_CONTENT_TOO_LARGE')
        if not re.fullmatch(r"[!#$%&'*+.^_`|~0-9A-Za-z-]+", name) or any(ord(c) < 32 or ord(c) > 126 for c in value):
            raise WebsiteFetchError('WEBSITE_FETCH_FAILED')
        key = name.lower()
        if key in result:
            raise WebsiteFetchError('WEBSITE_FETCH_FAILED')
        result[key] = value.strip(' ')
    return result


class _WireReader:
    """readline/read의 큰 선할당·buffered read-ahead 없이 deadline을 매 recv 확인."""
    def __init__(self, sock, deadline, max_bytes):
        self.sock = sock
        self.deadline = deadline
        self.max_bytes = max_bytes
        self.header_bytes = 0

    def recv(self, size):
        self.sock.settimeout(remaining(self.deadline))
        data = self.sock.recv(size)
        remaining(self.deadline)
        return data

    def line(self, *, header=True, max_line_bytes=MAX_HEADERS):
        result = bytearray()
        while True:
            if len(result) >= max_line_bytes or header and self.header_bytes >= MAX_HEADERS:
                raise WebsiteFetchError('WEBSITE_CONTENT_TOO_LARGE')
            piece = self.recv(1)
            if not piece:
                raise WebsiteFetchError('WEBSITE_FETCH_FAILED')
            if header:
                self.header_bytes += 1
            result.extend(piece)
            if piece == b'\n':
                if not result.endswith(b'\r\n'):
                    raise WebsiteFetchError('WEBSITE_FETCH_FAILED')
                return bytes(result)

    def exact(self, count):
        result = bytearray()
        while len(result) < count:
            piece = self.recv(min(4096, count - len(result)))
            if not piece:
                raise WebsiteFetchError('WEBSITE_FETCH_FAILED')
            result.extend(piece)
        return bytes(result)

    def body(self, headers):
        if headers.get('content-encoding', 'identity').lower() != 'identity':
            raise WebsiteFetchError('WEBSITE_MEDIA_UNSUPPORTED')
        length = headers.get('content-length')
        encoding = headers.get('transfer-encoding')
        if encoding is not None and (encoding.lower() != 'chunked' or length is not None):
            raise WebsiteFetchError('WEBSITE_FETCH_FAILED')
        if length is not None:
            if not re.fullmatch(r'[0-9]+', length):
                raise WebsiteFetchError('WEBSITE_FETCH_FAILED')
            if len(length) > 7 or int(length) > self.max_bytes:
                raise WebsiteFetchError('WEBSITE_CONTENT_TOO_LARGE')
            return self.exact(int(length))
        result = bytearray()
        if encoding:
            while True:
                # chunk framing은 HTTP header 누적 예산에 참여하지 않는다.
                # 기존 최대6hex를 CRLF 포함8byte로 읽기 전 제한한다.
                line = self.line(header=False, max_line_bytes=8)[:-2]
                # 첫 지원은 extension을 실행/해석하지 않으며 모호한 framing은 거부한다.
                if not re.fullmatch(rb'[0-9A-Fa-f]+', line):
                    raise WebsiteFetchError('WEBSITE_FETCH_FAILED')
                if len(line) > 6:
                    raise WebsiteFetchError('WEBSITE_CONTENT_TOO_LARGE')
                count = int(line, 16)
                if count > self.max_bytes - len(result):
                    raise WebsiteFetchError('WEBSITE_CONTENT_TOO_LARGE')
                if count == 0:
                    # 지원하지 않는 trailer는 body의 일부로 섞지 않는다.
                    if self.line(header=False) != b'\r\n':
                        raise WebsiteFetchError('WEBSITE_FETCH_FAILED')
                    break
                result.extend(self.exact(count))
                if self.exact(2) != b'\r\n':
                    raise WebsiteFetchError('WEBSITE_FETCH_FAILED')
        else:
            while True:
                piece = self.recv(min(4096, self.max_bytes - len(result) + 1))
                if not piece:
                    break
                if len(piece) > self.max_bytes - len(result):
                    raise WebsiteFetchError('WEBSITE_CONTENT_TOO_LARGE')
                result.extend(piece)
        return bytes(result)


class SafeHttpTransport:
    def __init__(self, *, max_bytes: int = MAX_BODY):
        if type(max_bytes) is not int or not 0 < max_bytes <= MAX_BODY:
            raise WebsiteFetchError('WEBSITE_FETCH_BLOCKED')
        self._max_bytes = max_bytes

    def _with_max_bytes(self, max_bytes: int) -> SafeHttpTransport:
        # 공유 transport를 변경하지 않고 현재 frozen policy의 작은 한도를 적용한다.
        return SafeHttpTransport(max_bytes=min(self._max_bytes, max_bytes))

    def get(self, url: str, approved_ips: tuple[str, ...], deadline: float) -> HttpFetchResponse:
        normalized = normalize_public_url(url)
        parsed = urlsplit(normalized)
        ips = validate_public_ips(approved_ips)
        sock = None
        raw = None
        try:
            ip = ips[0]
            address = ipaddress.ip_address(ip)
            family = socket.AF_INET6 if address.version == 6 else socket.AF_INET
            # create_connection(host)나 HTTPSConnection(host)의 DNS 재해석을 사용하지 않는다.
            raw = socket.socket(family, socket.SOCK_STREAM)
            raw.settimeout(remaining(deadline))
            raw.connect((ip, 443, 0, 0) if family == socket.AF_INET6 else (ip, 443))
            if ipaddress.ip_address(raw.getpeername()[0]) != address:
                raise WebsiteFetchError('WEBSITE_FETCH_BLOCKED')
            remaining(deadline)
            tls = ssl.create_default_context()
            if not tls.check_hostname or tls.verify_mode != ssl.CERT_REQUIRED:
                raise WebsiteFetchError('WEBSITE_FETCH_BLOCKED')
            raw.settimeout(remaining(deadline))
            sock = tls.wrap_socket(raw, server_hostname=parsed.hostname)
            if ipaddress.ip_address(sock.getpeername()[0]) != address:
                raise WebsiteFetchError('WEBSITE_FETCH_BLOCKED')
            sock.settimeout(remaining(deadline))
            request = (f'GET {parsed.path} HTTP/1.1\r\nHost: {parsed.hostname}\r\n'
                       'User-Agent: Daon-User-Safe-Fetch/1.0\r\n'
                       'Accept: text/html, text/plain\r\nAccept-Encoding: identity\r\n\r\n')
            sock.sendall(request.encode('ascii'))
            reader = _WireReader(sock, deadline, self._max_bytes)
            status_line = reader.line()
            match = re.fullmatch(rb'HTTP/1\.[01] ([0-9]{3})(?: [\x20-\x7e]*)?\r\n', status_line)
            if match is None:
                raise WebsiteFetchError('WEBSITE_FETCH_FAILED')
            status = int(match[1])
            reader.header_bytes = 0
            headers = {}
            while True:
                line = reader.line()
                if line == b'\r\n':
                    break
                if line[:1] in (b' ', b'\t') or b':' not in line:
                    raise WebsiteFetchError('WEBSITE_FETCH_FAILED')
                name, value = line[:-2].split(b':', 1)
                name = name.decode('ascii')
                key = name.lower()
                if key in headers:
                    raise WebsiteFetchError('WEBSITE_FETCH_FAILED')
                headers[key] = value.decode('ascii').strip(' ')
            headers = checked_headers(headers)
            if status in REDIRECTS:
                body = b''
            elif status == 200:
                body = reader.body(headers)
            else:
                raise WebsiteFetchError('WEBSITE_FETCH_FAILED')
            remaining(deadline)
            return HttpFetchResponse(status, MappingProxyType(headers), body)
        except WebsiteFetchError:
            raise
        except (TimeoutError, socket.timeout):
            raise WebsiteFetchError('WEBSITE_FETCH_TIMEOUT') from None
        except (OSError, ValueError, UnicodeError):
            raise WebsiteFetchError('WEBSITE_FETCH_FAILED') from None
        finally:
            if sock is not None:
                sock.close()
            if raw is not None and raw is not sock:
                raw.close()
