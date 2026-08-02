from __future__ import annotations

import unittest

from daon_user_api.generation_settings import GenerationRequest, GenerationSettingsError


class GenerationSettingsTests(unittest.TestCase):
    def test_configure_confirm_submit_preserves_snapshot(self) -> None:
        request = GenerationRequest("report")
        request.configure("목적", "경영진", ["src-1:v2"], "rs-1:v4", "pdf", "review")
        snapshot = request.confirm()
        self.assertEqual(request.submit(), "submitted")
        self.assertEqual(snapshot.format, "pdf")

    def test_confirmed_request_is_locked_and_requires_revision(self) -> None:
        request = GenerationRequest("report")
        request.configure("목적", "독자", ["src-1:v1"], None, "docx", "review")
        request.confirm()
        with self.assertRaisesRegex(GenerationSettingsError, "REQUEST_LOCKED"):
            request.configure("변경", "독자", ["src-1:v1"], None, "docx", "review")

    def test_missing_required_settings_cannot_confirm(self) -> None:
        with self.assertRaisesRegex(GenerationSettingsError, "SETTINGS_INCOMPLETE"):
            GenerationRequest("report").confirm()


if __name__ == "__main__":
    unittest.main()
