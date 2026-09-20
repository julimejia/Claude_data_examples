from __future__ import annotations

from schemasentinel.domain.models import (
    ChangeImpact,
    DriftReport,
    Explanation,
    ImpactText,
    SchemaChange,
    Severity,
    SummaryText,
)
from schemasentinel.ports.llm import LLMError, LLMPort

IMPACT_SYSTEM = (
    "You are a data-engineering assistant. Explain in one or two plain sentences what a "
    "breaking schema change would break for downstream consumers. "
    'Answer with a JSON object: {"impact": "..."}.'
)
SUMMARY_SYSTEM = (
    "You are a data-engineering assistant. Write a short executive summary (2-3 sentences, "
    "no jargon) of a schema drift report for a non-technical reader. "
    'Answer with a JSON object: {"summary": "..."}.'
)


def _describe(change: SchemaChange) -> str:
    return (
        f"change_type: {change.change_type.value}\n"
        f"path: {change.path}\n"
        f"baseline: {change.baseline.data_type if change.baseline else 'none'}\n"
        f"current: {change.current.data_type if change.current else 'none'}\n"
        f"rule: {change.rule_id}\n"
        f"reason: {change.reason}"
    )


def _fallback_summary(report: DriftReport) -> str:
    breaking = sum(1 for c in report.changes if c.severity is Severity.BREAKING)
    return (
        f"Verdict: {report.verdict.value}. {len(report.changes)} change(s) detected, "
        f"{breaking} breaking."
    )


class Explain:
    """Use case: per-change impact for breaking changes plus an executive summary.

    Never raises on LLM failure: falls back to deterministic text and marks `degraded`.
    """

    def __init__(self, llm: LLMPort) -> None:
        self._llm = llm

    def run(self, report: DriftReport) -> Explanation:
        degraded = False
        impacts: list[ChangeImpact] = []
        for change in report.changes:
            if change.severity is not Severity.BREAKING:
                continue
            try:
                text = self._llm.complete_structured(
                    system=IMPACT_SYSTEM, prompt=_describe(change), schema=ImpactText
                ).impact
            except LLMError:
                text, degraded = change.reason, True
            impacts.append(ChangeImpact(path=change.path, impact=text))
        lines = "\n".join(f"- {c.path}: {c.severity.value} - {c.reason}" for c in report.changes)
        prompt = f"verdict: {report.verdict.value}\nchanges:\n{lines or '(none)'}"
        try:
            summary = self._llm.complete_structured(
                system=SUMMARY_SYSTEM, prompt=prompt, schema=SummaryText
            ).summary
        except LLMError:
            summary, degraded = _fallback_summary(report), True
        return Explanation(summary=summary, impacts=tuple(impacts), degraded=degraded)
