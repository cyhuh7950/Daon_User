from dataclasses import dataclass


@dataclass(frozen=True)
class RegistrationResult:
    source_version: str
    lineage: dict[str, str]


class KnowledgeRegistration:
    def register(self, source_version, run_id, model_id, actor_id, *, explicit, step_up):
        if not explicit:
            raise ValueError("EXPLICIT_CONFIRMATION_REQUIRED")
        if not step_up:
            raise ValueError("STEP_UP_REQUIRED")
        if not all((source_version, run_id, model_id, actor_id)):
            raise ValueError("LINEAGE_INCOMPLETE")
        return RegistrationResult(source_version, {"run_id": run_id, "model_id": model_id, "actor_id": actor_id})
