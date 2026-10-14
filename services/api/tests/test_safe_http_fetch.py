from __future__ import annotations

import importlib
import importlib.util
import io
import socket
import ssl
import subprocess
import time
from unittest.mock import patch

import pytest


def api():
    # 없는 모듈은 collection 오류 대신 누락 기능의 RED로 보고한다.
    assert importlib.util.find_spec('daon_user_api.safe_http_fetch') is not None
    return importlib.import_module('daon_user_api.safe_http_fetch')


class WireSocket:
    def __init__(self, wire, peer='93.184.216.34', tick=None):
        self.wire = io.BytesIO(wire)
        self.peer = peer
        self.tick = tick
        self.closed = False
        self.sent = b''
        self.connected = None
        self.read_sizes = []

    def settimeout(self, timeout):
        assert 0 < timeout <= 8

    def connect(self, target):
        self.connected = target

    def getpeername(self):
        return (self.peer, 443)

    def sendall(self, value):
        self.sent += value

    def recv(self, size):
        self.read_sizes.append(size)
        if self.tick:
            self.tick()
        return self.wire.read(size)

    def close(self):
        self.closed = True


class VerifiedTLS:
    check_hostname = True
    verify_mode = ssl.CERT_REQUIRED

    def __init__(self, sock, error=None):
        self.sock = sock
        self.error = error
        self.hostname = None

    def wrap_socket(self, raw, *, server_hostname):
        assert raw is self.sock
        self.hostname = server_hostname
        if self.error:
            raise self.error
        return raw


def wire_get(module, wire, *, peer='93.184.216.34', tick=None, error=None):
    sock = WireSocket(wire, peer, tick)
    tls = VerifiedTLS(sock, error)
    with patch.object(module.socket, 'socket', return_value=sock), \
            patch.object(module.ssl, 'create_default_context', return_value=tls), \
            patch.object(module.socket, 'getaddrinfo', side_effect=AssertionError('재해석 금지')):
        response = module.SafeHttpTransport().get(
            'https://example.com/a%20b', ('93.184.216.34',), time.monotonic() + 8)
    return response, sock, tls


def test_pinned_tls_hostname():
    m = api()
    response, sock, tls = wire_get(m, b'HTTP/1.1 200 OK\r\nContent-Type: text/plain\r\nContent-Length: 3\r\n\r\nabc')
    assert response.status == 200 and response.body == b'abc'
    assert sock.connected == ('93.184.216.34', 443)
    assert tls.hostname == 'example.com'
    assert sock.sent == (b'GET /a%20b HTTP/1.1\r\nHost: example.com\r\n'
                         b'User-Agent: Daon-User-Safe-Fetch/1.0\r\n'
                         b'Accept: text/html, text/plain\r\nAccept-Encoding: identity\r\n\r\n')
    assert sock.closed


@pytest.mark.parametrize('ips', [(), ('127.0.0.1',), ('93.184.216.34', '10.0.0.1'),
                                ('::ffff:93.184.216.34',), ('2002:5db8:d822::1',),
                                ('2001:0:4136:e378:8000:63bf:3fff:fdd2',)])
def test_transport_rejects_unapproved_addresses_before_socket(ips):
    m = api()
    with patch.object(m.socket, 'socket', side_effect=AssertionError('연결 금지')):
        with pytest.raises(m.WebsiteFetchError, match='WEBSITE_FETCH_BLOCKED'):
            m.SafeHttpTransport().get('https://example.com/', ips, time.monotonic() + 8)


@pytest.mark.parametrize('peer,error', [('8.8.8.8', None),
                                     ('93.184.216.34', ssl.SSLCertVerificationError('private detail'))])
def test_tls_peer_and_certificate_fail_closed(peer, error):
    m = api()
    with pytest.raises(m.WebsiteFetchError) as caught:
        wire_get(m, b'HTTP/1.1 200 OK\r\n\r\nabc', peer=peer, error=error)
    assert str(caught.value) in {'WEBSITE_FETCH_BLOCKED', 'WEBSITE_FETCH_FAILED'}
    assert 'private detail' not in str(caught.value)


def test_proxy_environment_is_not_consumed(monkeypatch):
    m = api()
    monkeypatch.setenv('HTTPS_PROXY', 'http://127.0.0.1:9')
    response, sock, _ = wire_get(m, b'HTTP/1.1 200 OK\r\nContent-Length: 1\r\n\r\na')
    assert response.body == b'a' and sock.connected == ('93.184.216.34', 443)


def test_response_limits_and_deadline():
    m = api()
    # 한도 전에 Content-Length를 검사하고 body 읽기/큰 할당을 하지 않는다.
    with pytest.raises(m.WebsiteFetchError, match='WEBSITE_CONTENT_TOO_LARGE'):
        wire_get(m, b'HTTP/1.1 200 OK\r\nContent-Length: 2097153\r\n\r\n')
    for wire in [b'HTTP/1.1 200 OK\r\nX: ' + b'x' * 16385 + b'\r\n\r\n',
                 b'HTTP/1.1 200 OK\r\n\r\n' + b'x' * 2097153,
                 b'HTTP/1.1 200 OK\r\nTransfer-Encoding: chunked\r\n\r\n200001\r\n']:
        with pytest.raises(m.WebsiteFetchError, match='WEBSITE_CONTENT_TOO_LARGE'):
            wire_get(m, wire)
    clock = [100.0]
    sock = WireSocket(b'HTTP/1.1 200 OK\r\nContent-Length: 1\r\n\r\na',
                      tick=lambda: clock.__setitem__(0, clock[0] + 9))
    with patch.object(m.time, 'monotonic', side_effect=lambda: clock[0]), \
            patch.object(m.socket, 'socket', return_value=sock), \
            patch.object(m.ssl, 'create_default_context', return_value=VerifiedTLS(sock)):
        with pytest.raises(m.WebsiteFetchError, match='WEBSITE_FETCH_TIMEOUT'):
            m.SafeHttpTransport().get('https://example.com/', ('93.184.216.34',), 108.0)
    assert sock.closed


def test_streaming_body_limit_inclusive_and_chunked():
    m = api()
    response, sock, _ = wire_get(m, b'HTTP/1.1 200 OK\r\nContent-Length: 2097152\r\n\r\n' + b'x' * 2097152)
    assert len(response.body) == 2097152
    assert max(sock.read_sizes) <= 4096
    response, _, _ = wire_get(m, b'HTTP/1.1 200 OK\r\nTransfer-Encoding: chunked\r\n\r\n3\r\nabc\r\n2\r\nde\r\n0\r\n\r\n')
    assert response.body == b'abcde'


@pytest.mark.parametrize('header', [b'Content-Encoding: gzip', b'Content-Encoding: br',
                                  b'Content-Length: 1\r\nContent-Length: 2',
                                  b'Content-Length: -1', b'Content-Length: +1',
                                  b'Content-Length: 1\r\nTransfer-Encoding: chunked',
                                  b'Transfer-Encoding: gzip, chunked', b'Bad header',
                                  b' X-Fold: secret', b'X: a\x00b'])
def test_wire_rejects_ambiguous_or_unsupported_headers(header):
    m = api()
    with pytest.raises(m.WebsiteFetchError):
        wire_get(m, b'HTTP/1.1 200 OK\r\n' + header + b'\r\n\r\na')


@pytest.mark.parametrize('wire', [b'HTTP/1.1 200 OK\r\nContent-Length: 3\r\n\r\na',
                                b'HTTP/1.1 200 OK\r\nTransfer-Encoding: chunked\r\n\r\nZ\r\n',
                                b'HTTP/1.1 200 OK\r\nTransfer-Encoding: chunked\r\n\r\n1\r\naXX',
                                b'HTTP/1.1 200 OK\r\nX: x\r\n'])
def test_truncated_or_invalid_framing_never_returns_partial(wire):
    m = api()
    with pytest.raises(m.WebsiteFetchError, match='WEBSITE_FETCH_FAILED'):
        wire_get(m, wire)


def test_redirect_body_is_not_read():
    m = api()
    response, sock, _ = wire_get(m, b'HTTP/1.1 302 Found\r\nLocation: /next\r\nContent-Length: 999999999\r\n\r\n')
    assert response.status == 302 and response.body == b'' and sock.closed


def test_dns_timeout_kills_and_reaps_child():
    m = api()
    class Child:
        def __init__(self):
            self.killed = False
            self.reaped = False
            self.returncode = 0
        def communicate(self, timeout=None):
            assert 0 < timeout <= 2
            if not self.killed:
                raise subprocess.TimeoutExpired('resolver', timeout)
            self.reaped = True
            return b'', b''
        def kill(self):
            self.killed = True
    child = Child()
    with patch.object(m.subprocess, 'Popen', return_value=child):
        with pytest.raises(m.WebsiteFetchError, match='WEBSITE_FETCH_TIMEOUT'):
            m.resolve_public_ips('example.com', time.monotonic() + 8)
    assert child.killed and child.reaped


@pytest.mark.parametrize('result', [b'["93.184.216.34", "10.0.0.1"]',
                                  b'["::ffff:93.184.216.34"]', b'[]', b'bad', b'"93.184.216.34"'])
def test_dns_result_requires_all_global_unicast(result):
    m = api()
    class Child:
        returncode = 0
        def communicate(self, timeout=None):
            return result, b''
    with patch.object(m.subprocess, 'Popen', return_value=Child()):
        with pytest.raises(m.WebsiteFetchError):
            m.resolve_public_ips('example.com', time.monotonic() + 8)


def test_header_limit_is_exact_and_stops_before_body_read():
    m = api()
    # header 자체 16KiB: status line은 header budget에 포함하지 않는다.
    header = b'X: ' + b'x' * (16384 - 7) + b'\r\n\r\n'
    response, _, _ = wire_get(m, b'HTTP/1.1 200 OK\r\n' + header + b'a')
    assert response.body == b'a'
    sock = WireSocket(b'HTTP/1.1 200 OK\r\nX: ' + b'x' * 16385 + b'\r\n\r\nbody')
    with patch.object(m.socket, 'socket', return_value=sock), \
            patch.object(m.ssl, 'create_default_context', return_value=VerifiedTLS(sock)):
        with pytest.raises(m.WebsiteFetchError, match='WEBSITE_CONTENT_TOO_LARGE'):
            m.SafeHttpTransport().get('https://example.com/', ('93.184.216.34',), time.monotonic() + 8)
    assert sock.wire.tell() == 17 + 16384  # status line17 + header上限, body0


def test_dns_expired_deadline_still_reaps_killed_child():
    m = api()
    clock = [100.0]
    class Child:
        returncode = 0
        killed = False
        reaped = False
        def communicate(self, timeout=None):
            if not self.killed:
                clock[0] = 109.0
                raise subprocess.TimeoutExpired('resolver', timeout)
            self.reaped = True
            return b'', b''
        def kill(self):
            self.killed = True
    child = Child()
    with patch.object(m.subprocess, 'Popen', return_value=child), \
            patch.object(m.time, 'monotonic', side_effect=lambda: clock[0]):
        with pytest.raises(m.WebsiteFetchError, match='WEBSITE_FETCH_TIMEOUT'):
            m.resolve_public_ips('example.com', 108.0)
    assert child.killed and child.reaped


def test_dns_startup_consumes_two_second_budget():
    m = api()
    clock = [100.0]
    class Child:
        returncode = 0
        def communicate(self, timeout=None):
            clock[0] += 1.5
            return b'["93.184.216.34"]', b''
    def launch(*args, **kwargs):
        clock[0] += 1.0
        return Child()
    with patch.object(m.subprocess, 'Popen', side_effect=launch), \
            patch.object(m.time, 'monotonic', side_effect=lambda: clock[0]):
        with pytest.raises(m.WebsiteFetchError, match='WEBSITE_FETCH_TIMEOUT'):
            m.resolve_public_ips('example.com', 108.0)


def test_ipv6_connect_uses_numeric_tuple_and_original_sni():
    m = api()
    sock = WireSocket(b'HTTP/1.1 200 OK\r\nContent-Length: 1\r\n\r\na', peer='2606:4700:4700::1111')
    tls = VerifiedTLS(sock)
    with patch.object(m.socket, 'socket', return_value=sock), \
            patch.object(m.ssl, 'create_default_context', return_value=tls), \
            patch.object(m.socket, 'getaddrinfo', side_effect=AssertionError('재해석 금지')):
        page = m.SafeHttpTransport().get('https://example.com/', ('2606:4700:4700::1111',), time.monotonic() + 8)
    assert page.body == b'a' and sock.connected == ('2606:4700:4700::1111', 443, 0, 0)
    assert tls.hostname == 'example.com' and sock.closed


def test_disabled_tls_verification_never_sends_http():
    m = api()
    sock = WireSocket(b'')
    tls = VerifiedTLS(sock)
    tls.check_hostname = False
    with patch.object(m.socket, 'socket', return_value=sock), \
            patch.object(m.ssl, 'create_default_context', return_value=tls):
        with pytest.raises(m.WebsiteFetchError, match='WEBSITE_FETCH_BLOCKED'):
            m.SafeHttpTransport().get('https://example.com/', ('93.184.216.34',), time.monotonic() + 8)
    assert sock.sent == b'' and sock.closed


def test_isolated_dns_child_protocol_without_real_dns():
    m = api()
    # 실제 Windows child 생성/argv/isolated stdlib 확인. DNS/외부 GET은 실행0.
    with patch.object(m, '_DNS_PROGRAM', 'import sys; sys.stdout.buffer.write(b\'["93.184.216.34","2606:4700:4700::1111"]\')'):
        assert m.resolve_public_ips('example.com', time.monotonic() + 8) == ('93.184.216.34', '2606:4700:4700::1111')


def test_real_dns_child_timeout_without_network_leaves_no_child():
    m = api()
    created = []
    original_popen = m.subprocess.Popen
    def launch(*args, **kwargs):
        child = original_popen(*args, **kwargs)
        created.append(child)
        return child
    with patch.object(m, '_DNS_PROGRAM', 'import time; time.sleep(10)'), \
            patch.object(m.subprocess, 'Popen', side_effect=launch):
        with pytest.raises(m.WebsiteFetchError, match='WEBSITE_FETCH_TIMEOUT'):
            m.resolve_public_ips('example.com', time.monotonic() + 8)
    assert len(created) == 1 and created[0].poll() is not None


@pytest.mark.parametrize('wire', [b'HTTP/1.1 401 Unauthorized\r\n\r\nsecret',
                                b'HTTP/1.1 403 Forbidden\r\n\r\nblocked',
                                b'HTTP/1.1 500 Error\r\n\r\nprivate',
                                b'HTTP/1.1 204 No Content\r\n\r\n'])
def test_error_status_body_never_becomes_source(wire):
    m = api()
    with pytest.raises(m.WebsiteFetchError, match='WEBSITE_FETCH_FAILED'):
        wire_get(m, wire)


def test_tls_send_timeout_is_safe_and_closes_socket():
    m = api()
    sock = WireSocket(b'')
    def send_timeout(value):
        raise socket.timeout('private error')
    sock.sendall = send_timeout
    with patch.object(m.socket, 'socket', return_value=sock), \
            patch.object(m.ssl, 'create_default_context', return_value=VerifiedTLS(sock)):
        with pytest.raises(m.WebsiteFetchError, match='^WEBSITE_FETCH_TIMEOUT$'):
            m.SafeHttpTransport().get('https://example.com/', ('93.184.216.34',), time.monotonic() + 8)
    assert sock.closed


def test_many_small_chunks_do_not_consume_header_budget():
    m = api()
    wire = (b'HTTP/1.1 200 OK\r\nTransfer-Encoding: chunked\r\n\r\n'
            + b'1\r\na\r\n' * 5500 + b'0\r\n\r\n')
    response, sock, _ = wire_get(m, wire)
    assert response.body == b'a' * 5500 and sock.closed


def test_chunk_framing_line_bound_is_independent_and_checked_before_body():
    m = api()
    sock = WireSocket(b'HTTP/1.1 200 OK\r\nTransfer-Encoding: chunked\r\n\r\n'
                      + b'0' * 16385 + b'\r\nnever-read')
    with patch.object(m.socket, 'socket', return_value=sock), \
            patch.object(m.ssl, 'create_default_context', return_value=VerifiedTLS(sock)):
        with pytest.raises(m.WebsiteFetchError, match='WEBSITE_CONTENT_TOO_LARGE'):
            m.SafeHttpTransport().get('https://example.com/', ('93.184.216.34',), time.monotonic() + 8)
    # size은 최대6hex+CRLF다. 개별 line 한도 뒤의 본문은 읽지 않는다.
    assert sock.wire.tell() == len(b'HTTP/1.1 200 OK\r\nTransfer-Encoding: chunked\r\n\r\n') + 8
    assert sock.closed
