from __future__ import annotations

import unittest

from daon_user_api.ios_capture import IOSCapture, IOSCaptureError


class IOSCaptureTests(unittest.TestCase):
    def test_capture_supports_file_photo_and_audio(self) -> None:
        capture = IOSCapture()
        self.assertEqual(capture.capture("file", permission=True, online=True).status, "captured")
        self.assertEqual(capture.capture("photo", permission=True, online=True).status, "captured")
        self.assertEqual(capture.capture("audio", permission=True, online=True, asr_ready=True, time_segment="00:00-00:05").status, "captured")

    def test_offline_capture_is_queued_until_reconnect(self) -> None:
        capture = IOSCapture()
        result = capture.capture("file", permission=True, online=False)
        self.assertEqual(result.status, "queued_offline")
        self.assertEqual(capture.reconnect(), "sync_pending")

    def test_permission_and_audio_ready_are_required(self) -> None:
        with self.assertRaisesRegex(IOSCaptureError, "PERMISSION_REQUIRED"):
            IOSCapture().capture("photo", permission=False, online=True)
        with self.assertRaisesRegex(IOSCaptureError, "AUDIO_NOT_READY"):
            IOSCapture().capture("audio", permission=True, online=True)


if __name__ == "__main__":
    unittest.main()
