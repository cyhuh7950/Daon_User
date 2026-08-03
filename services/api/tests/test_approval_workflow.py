import unittest

from daon_user_api.approval_workflow import ApprovalWorkflow


class ApprovalWorkflowTests(unittest.TestCase):
    def test_default_expiry_and_approval(self):
        request = ApprovalWorkflow().request("out-1", "actor-1")
        self.assertEqual(request.expires_in_days, 7)
        self.assertEqual(ApprovalWorkflow().approve(request), "approved")

    def test_withdrawn_request_cannot_be_approved(self):
        workflow = ApprovalWorkflow()
        request = workflow.request("out-2", "actor-1")
        workflow.withdraw(request)
        with self.assertRaisesRegex(ValueError, "STATE_INVALID"):
            workflow.approve(request)

    def test_external_delivery_requires_step_up(self):
        workflow = ApprovalWorkflow()
        request = workflow.request("out-3", "actor-1")
        workflow.approve(request)
        with self.assertRaisesRegex(ValueError, "STEP_UP_REQUIRED"):
            workflow.deliver(request, external=True, step_up=False)
