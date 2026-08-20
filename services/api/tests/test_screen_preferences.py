from __future__ import annotations

import unittest

from daon_user_api.screen_preferences import (
    DEFAULT_SCREEN_PREFERENCES,
    ReferenceScreenPreferenceRepository,
    ScreenPreferenceContext,
    ScreenPreferenceError,
    ScreenPreferenceService,
)


class ScreenPreferenceDomainTests(unittest.TestCase):
    def setUp(self) -> None:
        self.service = ScreenPreferenceService(ReferenceScreenPreferenceRepository())
        self.first = ScreenPreferenceContext("tenant-screen-001", "user-screen-001")
        self.second = ScreenPreferenceContext("tenant-screen-001", "user-screen-002")

    def test_default_save_and_reset_are_user_scoped_screen_only_state(self) -> None:
        self.assertEqual(self.service.get(self.first), DEFAULT_SCREEN_PREFERENCES)
        saved = self.service.save(self.first, {"theme": "dark"})
        self.assertEqual(saved, {"theme": "dark"})
        self.assertEqual(self.service.get(self.second), DEFAULT_SCREEN_PREFERENCES)

    def test_rejects_non_exact_theme_dto(self) -> None:
        for payload in ({}, {"theme": "midnight"}, {"theme": "dark", "extra": True}):
            with self.assertRaises(ScreenPreferenceError) as raised:
                self.service.save(self.first, payload)
            self.assertEqual(raised.exception.code, "SCREEN_PREFERENCE_INVALID")


if __name__ == "__main__":
    unittest.main()
