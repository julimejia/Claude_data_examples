from __future__ import annotations

from collections import Counter

from schemasentinel.application.detect_drift import overall_verdict
from schemasentinel.domain.models import (
    Column,
    Decision,
    DriftReport,
    Resolution,
    SchemaChange,
    Severity,
)
from schemasentinel.ports.llm import LLMError, LLMPort

MIN_CONFIDENCE = 0.6

SYSTEM = (
    "You are a data-engineering assistant that reviews schema drift. Given a column that "
    "disappeared and a column that appeared (or a semantic type change), decide whether it "
    "is a `rename` of the same data, a `drop_and_add` of unrelated columns, or `unknown`. "
    "Answer with a JSON object: decision, confidence (0..1), rationale."
)


def _col(c: Column | None) -> str:
    if c is None:
        return "none"
    return f"{c.name} {c.data_type} nullable={c.nullable} position={c.position}"


def _describe(change: SchemaChange) -> str:
    return (
        f"change_type: {change.change_type.value}\n"
        f"path: {change.path}\n"
        f"baseline: {_col(change.baseline)}\n"
        f"current: {_col(change.current)}\n"
        f"similarity: {change.confidence:.2f}\n"
        f"note: {change.reason}"
    )


def _apply(change: SchemaChange, res: Resolution) -> SchemaChange:
    if res.confidence < MIN_CONFIDENCE or res.decision is Decision.UNKNOWN:
        return change.model_copy(update={"resolution": res, "needs_human_review": True})
    if res.decision is Decision.RENAME:
        rule, reason = "RENAME-RESOLVED", f"Resolved as rename: {res.rationale}"
    else:
        rule, reason = "DROP-AND-ADD-RESOLVED", f"Resolved as drop and add: {res.rationale}"
    return change.model_copy(
        update={
            "severity": Severity.BREAKING,
            "rule_id": rule,
            "reason": reason,
            "confidence": res.confidence,
            "resolution": res,
            "needs_human_review": False,
        }
    )


class Resolve:
    """Use case: ask the LLM to settle every `needs_review` change; never raises on LLM failure."""

    def __init__(self, llm: LLMPort) -> None:
        self._llm = llm

    def run(self, report: DriftReport) -> DriftReport:
        calls = 0
        out: list[SchemaChange] = []
        for change in report.changes:
            if change.severity is not Severity.NEEDS_REVIEW:
                out.append(change)
                continue
            calls += 1
            try:
                res = self._llm.complete_structured(
                    system=SYSTEM, prompt=_describe(change), schema=Resolution
                )
                out.append(_apply(change, res))
            except LLMError:
                out.append(change.model_copy(update={"needs_human_review": True}))
        changes = tuple(out)
        counts = Counter(c.severity.value for c in changes)
        meta = {
            **report.run_metadata,
            "change_counts": dict(sorted(counts.items())),
            "llm_calls": report.run_metadata.get("llm_calls", 0) + calls,
        }
        return report.model_copy(
            update={"changes": changes, "verdict": overall_verdict(changes), "run_metadata": meta}
        )
