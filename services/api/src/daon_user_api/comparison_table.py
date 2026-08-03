from dataclasses import dataclass
from typing import Any


@dataclass(frozen=True)
class ComparisonResult:
    rows: tuple[dict[str, Any], ...]


class ComparisonTable:
    _STATES = {"same", "changed", "missing", "conflict"}

    def compare(self, baseline, current, baseline_version, current_version, evidence):
        rows = []
        for key in sorted(set(baseline) | set(current)):
            has_baseline = key in baseline
            has_current = key in current
            base_value = baseline.get(key)
            current_value = current.get(key)
            base_evidence, current_evidence = evidence.get(key, ("", ""))
            if has_baseline and has_current:
                if not base_evidence or not current_evidence:
                    raise ValueError("EVIDENCE_REQUIRED")
                state = "same" if base_value == current_value else "changed"
            else:
                state = "missing"
            rows.append({
                "key": key,
                "baseline": base_value,
                "current": current_value,
                "difference": None if state == "same" else (base_value, current_value),
                "state": state,
                "evidence": (base_evidence, current_evidence),
                "baseline_version": baseline_version,
                "current_version": current_version,
            })
        return ComparisonResult(tuple(rows))
