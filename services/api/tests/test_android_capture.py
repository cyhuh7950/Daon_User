from __future__ import annotations

import unittest

from daon_user_api.android_capture import AndroidCapture, CaptureError


class AndroidCaptureTests(unittest.TestCase):
    def test_file_and_photo_capture_create_source(self) -> None:
        capture = AndroidCapture()
        for kind in ("file", "photo"):
            result = capture.capture(kind, permission=True, source_version=1)
            self.assertEqual(result.status, "captured")
            self.assertEqual(result.source_version, 1)

    def test_audio_requires_asr_llm_time_segment(self) -> None:
        capture = AndroidCapture()
        with self.assertRaisesRegex(CaptureError, "AUDIO_NOT_READY"):
            capture.capture("audio", permission=True, source_version=1)
        result = capture.capture("audio", permission=True, source_version=1, asr_ready=True, time_segment="00:00-00:10")
        self.assertEqual(result.status, "captured")

    def test_permission_is_required(self) -> None:
        with self.assertRaisesRegex(CaptureError, "PERMISSION_REQUIRED"):
            AndroidCapture().capture("photo", permission=False, source_version=1)


if __name__ == "__main__":
    unittest.main()
