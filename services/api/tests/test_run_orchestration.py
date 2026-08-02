from __future__ import annotations

import unittest

from daon_user_api.run_orchestration import RunOrchestrator, RunTransitionError


class RunOrchestrationTests(unittest.TestCase):
    def test_pdf_vertical_run_reaches_completed_with_lineage(self) -> None:
        run = RunOrchestrator().start("run-1", "src-1", 2)
        for state in ("planning", "retrieving", "generating", "validating", "completed"):
            run.transition(state)
        snapshot = run.snapshot()
        self.assertEqual(snapshot.status, "completed")
        self.assertEqual(snapshot.source_version, 2)
        self.assertEqual(snapshot.history, ("accepted", "planning", "retrieving", "generating", "validating", "completed"))

    def test_invalid_transition_and_completed_reversal_are_rejected(self) -> None:
        run = RunOrchestrator().start("run-2", "src-1", 1)
        with self.assertRaisesRegex(RunTransitionError, "INVALID_TRANSITION"):
            run.transition("completed")
        for state in ("planning", "retrieving", "generating", "validating", "completed"):
            run.transition(state)
        with self.assertRaisesRegex(RunTransitionError, "RUN_TERMINAL"):
            run.transition("failed")

    def test_failure_is_terminal_and_keeps_trace(self) -> None:
        run = RunOrchestrator().start("run-3", "src-1", 1)
        run.transition("planning")
        run.fail("MODEL_UNAVAILABLE")
        self.assertEqual(run.snapshot().status, "failed")
        self.assertEqual(run.snapshot().failure_code, "MODEL_UNAVAILABLE")


if __name__ == "__main__":
    unittest.main()
