from __future__ import annotations


class OperationsError(ValueError):
    pass


class OperationsState:
    def __init__(self) -> None:
        self.status = "healthy"

    def source_expired(self) -> str:
        self.status = "source_expired"
        return self.status

    def index_outage(self) -> str:
        self.status = "retrieval_degraded"
        return self.status

    def model_outage(self) -> str:
        self.status = "model_unavailable"
        return self.status

    def grounded_result(self, *, evidence_available: bool) -> str:
        if not evidence_available:
            self.status = "evidence_blocked"
            raise OperationsError("EVIDENCE_BLOCKED")
        return "grounded"

    def disconnect(self) -> str:
        self.status = "recovery_pending"
        return self.status

    def reconnect(self) -> str:
        if self.status != "recovery_pending":
            return "nothing_to_recover"
        self.status = "recovered"
        return self.status
