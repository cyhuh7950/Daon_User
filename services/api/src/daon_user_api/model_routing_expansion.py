from __future__ import annotations

from dataclasses import dataclass


class RoutingFailure(ValueError):
    pass


@dataclass(frozen=True)
class RoutingSelection:
    deployment_id: str
    role: str
    realm: str
    attempted_roles: tuple[str, ...]


class ExpandedModelRouter:
    _ROLES = {"text", "vision", "audio_understanding", "speech_to_text", "embedding", "reranker"}

    def select(
        self,
        role: str,
        mode: str,
        candidates: list[dict[str, object]],
        *,
        budget: float,
        pinned_id: str | None = None,
    ) -> RoutingSelection:
        if role not in self._ROLES:
            raise RoutingFailure("ROLE_UNSUPPORTED")
        if mode not in {"auto", "local_only", "pinned"}:
            raise RoutingFailure("ROUTING_MODE_UNSUPPORTED")
        if budget < 0:
            raise RoutingFailure("COST_LIMIT_EXCEEDED")
        matching = [candidate for candidate in candidates if candidate.get("role") == role]
        if mode == "local_only":
            matching = [candidate for candidate in matching if candidate.get("realm") == "local"]
        if mode == "pinned":
            if not pinned_id:
                raise RoutingFailure("PINNED_DEPLOYMENT_REQUIRED")
            matching = [candidate for candidate in matching if candidate.get("deployment_id") == pinned_id]
        if not matching:
            raise RoutingFailure("NO_AVAILABLE_DEPLOYMENT")
        available = [candidate for candidate in matching if candidate.get("available") is True]
        if not available:
            raise RoutingFailure("WAITING_MODEL")
        selected = available[0]
        cost = float(selected.get("cost", 0.0))
        if cost > budget:
            raise RoutingFailure("COST_LIMIT_EXCEEDED")
        return RoutingSelection(
            deployment_id=str(selected["deployment_id"]),
            role=role,
            realm=str(selected.get("realm", "unknown")),
            attempted_roles=(role,),
        )
