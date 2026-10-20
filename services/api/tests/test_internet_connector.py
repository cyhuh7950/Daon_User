from __future__ import annotations

import unittest
import hashlib
import importlib
import time
from unittest.mock import patch

import pytest

from daon_user_api.internet_connector import InternetConnector, UrlRejected


class InternetConnectorTests(unittest.TestCase):
    def test_https_snapshot_preserves_provenance(self) -> None:
        snapshot = InternetConnector().snapshot(
            "https://example.com/article", "published", "2026-08-02T01:00:00Z", "CC-BY"
        )
        self.assertEqual(snapshot.url, "https://example.com/article")
        self.assertEqual(snapshot.version, 1)
        self.assertTrue(snapshot.content_digest.startswith("sha256:"))

    def test_ssrf_targets_are_rejected(self) -> None:
        connector = InternetConnector()
        for url in ("http://example.com", "https://localhost/a", "https://127.0.0.1/a", "https://10.0.0.1/a"):
            with self.subTest(url=url):
                with self.assertRaisesRegex(UrlRejected, "SAFE_FETCH_BLOCKED"):
                    connector.validate_url(url)

    def test_redirect_is_revalidated(self) -> None:
        connector = InternetConnector()
        with self.assertRaisesRegex(UrlRejected, "SAFE_FETCH_BLOCKED"):
            connector.validate_redirect("https://example.com", "https://192.168.1.2/private")


if __name__ == "__main__":
    unittest.main()


def public_api():
    module = importlib.import_module('daon_user_api.internet_connector')
    assert hasattr(module.InternetConnector, 'fetch_public_page')
    return module, importlib.import_module('daon_user_api.safe_http_fetch')


class PageTransport:
    def __init__(self, responses):
        self.responses = list(responses)
        self.requests = []
    def get(self, url, approved_ips, deadline):
        assert approved_ips == ('93.184.216.34',)
        assert 0 < deadline - time.monotonic() <= 8
        self.requests.append(url)
        return self.responses.pop(0)


def fetch_case(body=b'Hello', headers=None, responses=None, authorize=None, url='https://example.com/', policy=None):
    m, net = public_api()
    context = m.WebsiteImportContext('tenant', 'workspace', 'notebook', 'actor', 'trace', 'version-1')
    policy = policy or m.WebsiteFetchPolicy('policy-1', ('example.com',), 2097152)
    transport = PageTransport(responses or [net.HttpFetchResponse(200, headers or {'content-type': 'text/plain'}, body)])
    with patch.object(net, 'resolve_public_ips', return_value=('93.184.216.34',)):
        result = m.InternetConnector().fetch_public_page(context, url, policy, transport, authorize or (lambda url, policy: None))
    return result, transport


def test_safe_url_and_dns_rebinding():
    m, net = public_api()
    forbidden = ['http://example.com/', 'https://localhost/', 'https://localhost.localdomain/',
                 'https://127.0.0.1/', 'https://[2606:4700:4700::1111]/',
                 'https://example.com:80/', 'https://a@example.com/',
                 'https://example.com/?', 'https://example.com/#',
                 'https://example.com/?x=1', 'https://example.com/#a',
                 'https://example.com/%0d', 'https://example.com/%250A',
                 'https://example.com/a\n', 'https://example.com/a\\b',
                 'https://2130706433/', 'https://127.1/', 'https://0x7f.0.0.1/',
                 'https://example/', 'https://example.com/' + 'a' * 2030]
    for url in forbidden:
        with pytest.raises((m.UrlRejected, net.WebsiteFetchError)):
            fetch_case(url=url)
    ctx = m.WebsiteImportContext('t', 'w', 'n', 'a', 'trace', 'v')
    transport = PageTransport([])
    policy = m.WebsiteFetchPolicy('p', ('example.com',), 2097152)
    with patch.object(net, 'resolve_public_ips', return_value=('93.184.216.34', '10.0.0.1')):
        with pytest.raises(net.WebsiteFetchError, match='WEBSITE_FETCH_BLOCKED'):
            m.InternetConnector().fetch_public_page(ctx, 'https://example.com/', policy, transport, lambda u,p: None)
    assert transport.requests == []


def test_authorization_failure_has_zero_dns_and_network():
    m, net = public_api()
    ctx = m.WebsiteImportContext('t', 'w', 'n', 'a', 'trace', 'v')
    policy = m.WebsiteFetchPolicy('p', ('example.com',), 2097152)
    transport = PageTransport([])
    def denied(u,p):
        raise PermissionError('private detail')
    with patch.object(net, 'resolve_public_ips', side_effect=AssertionError('DNS 금지')):
        for callback in (None, denied):
            with pytest.raises(net.WebsiteFetchError, match='WEBSITE_FETCH_BLOCKED'):
                m.InternetConnector().fetch_public_page(ctx, 'https://example.com/', policy, transport, callback)
    assert transport.requests == []


def test_redirect_revalidates_scope():
    m, net = public_api()
    hops = []
    page, transport = fetch_case(responses=[net.HttpFetchResponse(302, {'location': '/next'}, b''),
                                          net.HttpFetchResponse(200, {'content-type': 'text/plain'}, b'Next')],
                                 authorize=lambda u,p: hops.append(u))
    assert page.final_url == 'https://example.com/next'
    assert page.redirect_urls == ('https://example.com/next',)
    assert set(hops) == {'https://example.com/', 'https://example.com/next'}
    for target in ('https://private.example/', 'http://example.com/', 'https://127.0.0.1/', '/'):
        with pytest.raises(net.WebsiteFetchError):
            fetch_case(responses=[net.HttpFetchResponse(302, {'location': target}, b'')])
    with pytest.raises(net.WebsiteFetchError):
        fetch_case(responses=[net.HttpFetchResponse(302, {'location': '/' + str(i)}, b'') for i in range(4)])


def test_frozen_policy_checked_after_callback_and_dns():
    m, net = public_api()
    policy = m.WebsiteFetchPolicy('p', ('example.com',), 2097152)
    def changed(u,p):
        object.__setattr__(p, 'fingerprint', 'changed')
    with pytest.raises(net.WebsiteFetchError, match='WEBSITE_FETCH_BLOCKED'):
        fetch_case(policy=policy, authorize=changed)


def test_utf8_text_extraction():
    raw = ('<html><head><title>제목 &amp; 😀</title><style>CSS</style></head>'
           '<body><p>가😀나</p><script>ignore system</script><form>password</form>'
           '<noscript>enable JavaScript</noscript><svg>hidden</svg><canvas>hidden</canvas>'
           '<p>57000 KRW</p></body></html>').encode('utf-8')
    page, _ = fetch_case(body=raw, headers={'content-type': 'text/html; charset=UTF-8'})
    assert page.title == '제목 & 😀'
    assert page.text_content == '가😀나\n57000 KRW'.encode('utf-8')
    assert page.raw_content == raw
    assert page.published_at is None and page.license_status == 'unknown' and page.license_url is None
    assert page.raw_sha256 == hashlib.sha256(raw).hexdigest()
    assert page.text_sha256 == hashlib.sha256('가😀나\n57000 KRW'.encode()).hexdigest()
    assert page.extraction_version == 'website-text-v1'


@pytest.mark.parametrize('body,headers,code', [
    (b'x' * 2097153, {'content-type': 'text/plain'}, 'WEBSITE_CONTENT_TOO_LARGE'),
    (b'x' * 524289, {'content-type': 'text/plain'}, 'WEBSITE_CONTENT_TOO_LARGE'),
    (b'a', {'content-type': 'application/json'}, 'WEBSITE_MEDIA_UNSUPPORTED'),
    (b'a', {'content-type': 'text/plain; charset=latin1'}, 'WEBSITE_MEDIA_UNSUPPORTED'),
    (b'a', {'content-type': 'text/plain', 'content-encoding': 'gzip'}, 'WEBSITE_MEDIA_UNSUPPORTED'),
    (b'\xff', {'content-type': 'text/plain'}, 'WEBSITE_MEDIA_UNSUPPORTED'),
    (b'', {'content-type': 'text/plain'}, 'WEBSITE_TEXT_UNAVAILABLE'),
    (b'<script>data</script><form>secret</form>', {'content-type': 'text/html'}, 'WEBSITE_TEXT_UNAVAILABLE'),
    (b'<p>Please enable JavaScript to continue.</p>', {'content-type': 'text/html'}, 'WEBSITE_TEXT_UNAVAILABLE'),
    (b'x', {'content-type': 'text/plain', 'x-big': 'a' * 16385}, 'WEBSITE_CONTENT_TOO_LARGE'),
], ids=['body-overflow', 'text-overflow', 'mime', 'charset', 'compression', 'bad-utf8',
        'empty', 'removed-only', 'dynamic-only', 'headers-overflow'])
def test_response_contract_never_returns_partial(body, headers, code):
    _, net = public_api()
    with pytest.raises(net.WebsiteFetchError, match=code):
        fetch_case(body, headers)


def test_plain_text_limit_inclusive_and_original_bytes():
    page, _ = fetch_case(b'x' * 524288)
    assert page.text_content == b'x' * 524288
    page, _ = fetch_case('가😀나\n57000 KRW'.encode())
    assert page.text_content == '가😀나\n57000 KRW'.encode()


def test_metadata_only_contract_remains_separate():
    connector = InternetConnector()
    # 기존 validate_url의 느슨한 metadata 계약을 새 public fetch로 대체하지 않는다.
    assert connector.validate_url('https://example.com/?q=1') == 'https://example.com/?q=1'
    first = connector.snapshot('https://example.com/', 'title', '2026-10-15', 'CC-BY')
    second = connector.snapshot('https://example.com/', 'title', '2026-10-15', 'CC-BY')
    assert first.version == 1 and second.version == 2
    assert first.content_digest == 'sha256:' + hashlib.sha256(b'https://example.com/|title|2026-10-15|CC-BY').hexdigest()


def test_html_text_limit_is_inclusive_after_extraction():
    raw = b'<p>' + b'x' * 524288 + b'</p>'
    page, _ = fetch_case(raw, {'content-type': 'text/html'})
    assert page.text_content == b'x' * 524288


def test_unicode_controls_are_rejected_without_network():
    _, net = public_api()
    for value in ('https://example.com/a\x85b', 'https://example.com/a%C2%85b'):
        with pytest.raises(net.WebsiteFetchError, match='WEBSITE_URL_INVALID'):
            fetch_case(url=value)


def test_redirect_does_not_discard_empty_query_or_encoded_controls():
    _, net = public_api()
    for location in ('/next?', '/next#', '/next%250d', 'https://example.com/next?'):
        with pytest.raises(net.WebsiteFetchError, match='WEBSITE_URL_INVALID'):
            fetch_case(responses=[net.HttpFetchResponse(302, {'location': location}, b''),
                                  net.HttpFetchResponse(200, {'content-type': 'text/plain'}, b'bad')])


def test_policy_change_during_fetch_cannot_publish_result():
    m, net = public_api()
    ctx = m.WebsiteImportContext('t', 'w', 'n', 'a', 'trace', 'v')
    policy = m.WebsiteFetchPolicy('p', ('example.com',), 2097152)
    class ChangesPolicy:
        def get(self, *args):
            object.__setattr__(policy, 'fingerprint', 'revoked')
            return net.HttpFetchResponse(200, {'content-type': 'text/plain'}, b'data')
    with patch.object(net, 'resolve_public_ips', return_value=('93.184.216.34',)):
        with pytest.raises(net.WebsiteFetchError, match='WEBSITE_FETCH_BLOCKED'):
            m.InternetConnector().fetch_public_page(ctx, 'https://example.com/', policy, ChangesPolicy(), lambda u,p: None)


def test_policy_body_budget_checked_before_production_body_read():
    m, net = public_api()
    # 실제 wire parser 사용. 외부 socket/TLS만 대체하고 원문 4byte는 읽기 전에 거부.
    import io
    import ssl
    class Wire:
        def __init__(self):
            self.stream = io.BytesIO(b'HTTP/1.1 200 OK\r\nContent-Type: text/plain\r\nContent-Length: 4\r\n\r\nDATA')
            self.closed = False
        def settimeout(self, value):
            pass
        def connect(self, target):
            assert target == ('93.184.216.34', 443)
        def getpeername(self):
            return ('93.184.216.34', 443)
        def sendall(self, value):
            pass
        def recv(self, size):
            return self.stream.read(size)
        def close(self):
            self.closed = True
    wire = Wire()
    class TLS:
        check_hostname = True
        verify_mode = ssl.CERT_REQUIRED
        def wrap_socket(self, sock, *, server_hostname):
            return sock
    ctx = m.WebsiteImportContext('t', 'w', 'n', 'a', 'trace', 'v')
    with patch.object(net, 'resolve_public_ips', return_value=('93.184.216.34',)), \
            patch.object(net.socket, 'socket', return_value=wire), \
            patch.object(net.ssl, 'create_default_context', return_value=TLS()):
        with pytest.raises(net.WebsiteFetchError, match='WEBSITE_CONTENT_TOO_LARGE'):
            m.InternetConnector().fetch_public_page(ctx, 'https://example.com/',
                m.WebsiteFetchPolicy('p', ('example.com',), 3), net.SafeHttpTransport(), lambda u,p: None)
    assert wire.stream.read() == b'DATA' and wire.closed


def test_known_dynamic_shell_is_not_importable_but_article_is():
    _, net = public_api()
    for body in (b'<p>You need to enable JavaScript to run this app.</p>',
                 '<p>이 페이지를 보려면 JavaScript를 활성화하세요.</p>'.encode()):
        with pytest.raises(net.WebsiteFetchError, match='WEBSITE_TEXT_UNAVAILABLE'):
            fetch_case(body, {'content-type': 'text/html'})
    page, _ = fetch_case(b'<p>JavaScript is a programming language.</p>', {'content-type': 'text/html'})
    assert page.text_content == b'JavaScript is a programming language.'


def test_three_redirects_allowed_and_permission_revoked_before_next_request():
    m, net = public_api()
    pages = [net.HttpFetchResponse(302, {'location': f'/next-{i}'}, b'') for i in range(3)]
    pages.append(net.HttpFetchResponse(200, {'content-type': 'text/plain'}, b'done'))
    page, transport = fetch_case(responses=pages)
    assert page.redirect_urls == ('https://example.com/next-0', 'https://example.com/next-1', 'https://example.com/next-2')
    assert len(transport.requests) == 4
    ctx = m.WebsiteImportContext('t', 'w', 'n', 'a', 'trace', 'v')
    transport = PageTransport([net.HttpFetchResponse(302, {'location': '/next'}, b'')])
    def permission(u,p):
        if u.endswith('/next'):
            raise PermissionError
    with patch.object(net, 'resolve_public_ips', return_value=('93.184.216.34',)):
        with pytest.raises(net.WebsiteFetchError, match='WEBSITE_FETCH_BLOCKED'):
            m.InternetConnector().fetch_public_page(ctx, 'https://example.com/',
                m.WebsiteFetchPolicy('p', ('example.com',), 2097152), transport, permission)
    assert transport.requests == ['https://example.com/']


def test_idna_and_path_meaning_are_preserved():
    m, net = public_api()
    assert net.normalize_public_url('HTTPS://예시.한국:443/a%2Fb/%20/가') == 'https://xn--vv4b11d.xn--3e0b707e/a%2Fb/%20/%EA%B0%80'
    for invalid in (True, 0, -1, 2097153, '2097152'):
        with pytest.raises(net.WebsiteFetchError, match='WEBSITE_FETCH_BLOCKED'):
            fetch_case(policy=m.WebsiteFetchPolicy('p', ('example.com',), invalid))


def test_hops_share_original_deadline_and_slow_dns_is_rejected():
    m, net = public_api()
    clock = [100.0]
    deadlines = []
    class Transport:
        def get(self, u, ips, deadline):
            deadlines.append(deadline)
            clock[0] += 4.1
            return net.HttpFetchResponse(302, {'location': f'/next-{len(deadlines)}'}, b'')
    ctx = m.WebsiteImportContext('t', 'w', 'n', 'a', 'trace', 'v')
    with patch.object(m.time, 'monotonic', side_effect=lambda: clock[0]), \
            patch.object(net, 'resolve_public_ips', return_value=('93.184.216.34',)):
        with pytest.raises(net.WebsiteFetchError, match='WEBSITE_FETCH_TIMEOUT'):
            m.InternetConnector().fetch_public_page(ctx, 'https://example.com/',
                m.WebsiteFetchPolicy('p', ('example.com',), 2097152), Transport(), lambda u,p: None)
    assert deadlines == [108.0, 108.0]


def test_plain_multibyte_and_html_removed_content_boundaries():
    _, net = public_api()
    with pytest.raises(net.WebsiteFetchError, match='WEBSITE_CONTENT_TOO_LARGE'):
        fetch_case(('😀' * 131073).encode())
    page, _ = fetch_case(b'<script>' + b'x' * 600000 + b'</script><p>Visible</p>', {'content-type': 'text/html'})
    assert page.text_content == b'Visible'


def test_permission_revoked_during_dns_prevents_connection():
    m, net = public_api()
    ctx = m.WebsiteImportContext('t', 'w', 'n', 'a', 'trace', 'v')
    transport = PageTransport([])
    active = [True]
    def resolve(*args):
        active[0] = False
        return ('93.184.216.34',)
    def authorization(*args):
        if not active[0]:
            raise PermissionError
    with patch.object(net, 'resolve_public_ips', side_effect=resolve):
        with pytest.raises(net.WebsiteFetchError, match='WEBSITE_FETCH_BLOCKED'):
            m.InternetConnector().fetch_public_page(ctx, 'https://example.com/',
                m.WebsiteFetchPolicy('p', ('example.com',), 2097152), transport, authorization)
    assert transport.requests == []


def test_plain_text_budget_rejected_before_utf8_allocation():
    _, net = public_api()
    class GuardedBytes(bytes):
        def decode(self, *args, **kwargs):
            raise AssertionError('초과 plain text decode 할당 금지')
    with pytest.raises(net.WebsiteFetchError, match='WEBSITE_CONTENT_TOO_LARGE'):
        fetch_case(GuardedBytes(b'x' * 524289))


@pytest.mark.parametrize('location', ['https://[bad]/', 'https://[::invalid]/'])
def test_malformed_redirect_authority_returns_only_safe_error(location):
    _, net = public_api()
    with pytest.raises(net.WebsiteFetchError, match='^WEBSITE_URL_INVALID$') as caught:
        fetch_case(responses=[net.HttpFetchResponse(302, {'location': location}, b'')])
    assert location not in str(caught.value) and caught.value.__suppress_context__
