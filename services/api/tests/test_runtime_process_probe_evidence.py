from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from runtime_process_probe import process_evidence


class RuntimeProcessProbeEvidenceTests(unittest.TestCase):
    def test_check_only_skips_private_evidence_read_and_write(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            evidence = Path(directory) / "missing"
            process_evidence({"runtime": "ok"}, {"bff": "ok"}, write=False, check_only=True, evidence_dir=evidence)
            self.assertFalse(evidence.exists())

    def test_write_and_compare_modes_keep_deterministic_evidence_contract(self) -> None:
        runtime = {"runtime": "ok"}
        bff = {"bff": "ok"}
        with tempfile.TemporaryDirectory() as directory:
            evidence = Path(directory) / "evidence"
            process_evidence(runtime, bff, write=True, check_only=False, evidence_dir=evidence)
            self.assertEqual(json.loads((evidence / "runtime-process-summary.json").read_text("utf-8")), runtime)
            self.assertEqual(json.loads((evidence / "bff-network-summary.json").read_text("utf-8")), bff)
            process_evidence(runtime, bff, write=False, check_only=False, evidence_dir=evidence)

            (evidence / "runtime-process-summary.json").write_text('{"runtime":"changed"}\n', encoding="utf-8")
            with self.assertRaisesRegex(RuntimeError, "EVIDENCE_MISMATCH:runtime-process-summary.json"):
                process_evidence(runtime, bff, write=False, check_only=False, evidence_dir=evidence)


if __name__ == "__main__":
    unittest.main()
