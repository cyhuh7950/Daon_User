from dataclasses import dataclass


class ComplianceCheckError(ValueError):
    """Raised when a compliance result violates the output contract."""


@dataclass(frozen=True)
class ComplianceCheckResult:
    items: tuple[dict[str, str], ...]
    warnings: tuple[str, ...]
    lineage: dict[str, str]


class ComplianceChecker:
    _JUDGEMENTS = {"compliant", "non_compliant", "needs_review"}

    def check(
        self,
        items: list[dict[str, str]],
        ruleset_id: str,
        ruleset_version: str,
        request_id: str,
        model_id: str,
    ) -> ComplianceCheckResult:
        if not ruleset_id or not ruleset_version or not request_id or not model_id:
            raise ComplianceCheckError("COMPLIANCE_LINEAGE_INCOMPLETE")
        normalized: list[dict[str, str]] = []
        warnings: set[str] = set()
        for item in items:
            judgement = item.get("judgement", "")
            if judgement not in self._JUDGEMENTS:
                raise ComplianceCheckError("JUDGEMENT_UNSUPPORTED")
            evidence = item.get("evidence", "")
            if judgement == "compliant" and not evidence:
                judgement = "needs_review"
                warnings.add("missing_evidence")
            normalized.append({
                "item_id": item.get("item_id", ""),
                "judgement": judgement,
                "evidence": evidence,
                "ruleset_id": ruleset_id,
                "action": item.get("action", "review"),
            })
        return ComplianceCheckResult(
            tuple(normalized),
            tuple(sorted(warnings)),
            {"ruleset_id": ruleset_id, "ruleset_version": ruleset_version,
             "request_id": request_id, "model_id": model_id},
        )
