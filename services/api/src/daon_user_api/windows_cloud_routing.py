from __future__ import annotations

from dataclasses import dataclass


class CloudRoutingError(ValueError):
    pass


@dataclass(frozen=True)
class CloudRouteDecision:
    deployment_id: str
    role: str
    network: str
    egress: str
    audit_reason: str


class CloudRoute:
    _APPROVED = {"local-v": ("local", "none"), "internal-v": ("internal", "internal"), "external-v": ("external", "external"), "daon-v": ("daon", "daon")}

    def select(self, deployment_id: str, *, data_realm: str, role: str, mode: str) -> CloudRouteDecision:
        if mode not in {"auto", "pinned"}:
            raise CloudRoutingError("ROUTING_MODE_INVALID")
        if data_realm not in {"cloud_sync", "local_private"}:
            raise CloudRoutingError("DATA_REALM_INVALID")
        candidate = self._APPROVED.get(deployment_id)
        if candidate is None:
            raise CloudRoutingError("NO_APPROVED_CANDIDATE")
        network, egress = candidate
        if data_realm == "local_private" and egress != "none":
            raise CloudRoutingError("LOCAL_PRIVATE_EGRESS_BLOCKED")
        return CloudRouteDecision(deployment_id, role, network, egress, "PINNED_SELECTION" if mode == "pinned" else "AUTO_SELECTION")
