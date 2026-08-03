import unittest

from daon_user_api.comparison_table import ComparisonTable


class ComparisonTableTests(unittest.TestCase):
    def test_changed_and_missing_rows_preserve_versions_and_evidence(self):
        result = ComparisonTable().compare(
            baseline={"a": 1, "b": 2}, current={"a": 3},
            baseline_version="v1", current_version="v2",
            evidence={"a": ("cell:A1", "cell:A2"), "b": ("cell:B1", "")},
        )
        rows = {row["key"]: row for row in result.rows}
        self.assertEqual(rows["a"]["state"], "changed")
        self.assertEqual(rows["a"]["baseline_version"], "v1")
        self.assertEqual(rows["a"]["evidence"], ("cell:A1", "cell:A2"))
        self.assertEqual(rows["b"]["state"], "missing")

    def test_equal_values_are_same(self):
        result = ComparisonTable().compare({"a": 1}, {"a": 1}, "v1", "v2", {"a": ("x", "y")})
        self.assertEqual(result.rows[0]["state"], "same")

    def test_missing_evidence_is_rejected(self):
        with self.assertRaisesRegex(ValueError, "EVIDENCE_REQUIRED"):
            ComparisonTable().compare({"a": 1}, {"a": 2}, "v1", "v2", {"a": ("", "")})
