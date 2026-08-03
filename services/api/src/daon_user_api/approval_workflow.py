from dataclasses import dataclass


@dataclass
class ApprovalRequest:
    output_id: str
    requester_id: str
    expires_in_days: int = 7
    state: str = "pending"


class ApprovalWorkflow:
    def request(self, output_id, requester_id, expires_in_days=7):
        if not 1 <= expires_in_days <= 30:
            raise ValueError("EXPIRY_INVALID")
        return ApprovalRequest(output_id, requester_id, expires_in_days)

    def approve(self, request):
        if request.state != "pending":
            raise ValueError("STATE_INVALID")
        request.state = "approved"
        return request.state

    def withdraw(self, request):
        if request.state != "pending":
            raise ValueError("STATE_INVALID")
        request.state = "withdrawn"

    def deliver(self, request, external=False, step_up=False):
        if request.state != "approved":
            raise ValueError("STATE_INVALID")
        if external and not step_up:
            raise ValueError("STEP_UP_REQUIRED")
        request.state = "delivered"
        return request.state
