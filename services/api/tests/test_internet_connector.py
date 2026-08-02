from __future__ import annotations

import unittest

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
