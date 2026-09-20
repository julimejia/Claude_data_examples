from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from schemasentinel.adapters.llm.replay import ReplayAdapter
from schemasentinel.application.detect_drift import build_report
from schemasentinel.application.propose_migration import ProposeMigration
from schemasentinel.application.resolve import Resolve
from schemasentinel.domain.migration import Dialect, generate_migration
from schemasentinel.domain.models import Decision, DriftReport, SchemaSnapshot, Severity
from schemasentinel.evals.golden import GoldenCase, parse_columns
from schemasentinel.ports.llm import LLMError, LLMPort

# Metrics that need the LLM stages (FR-4, FR-5); n/a when evaluating without an LLM.
LLM_METRICS =("rename_resolution_accuracy", "ddl_validity_rate", "invalid_output_rate")


@dataclass(frozen=True)
class CaseFailure:
    case_id: str
    detail: str


@dataclass
class EvalResult:
    metrics: dict[str, float | None]
    case_count: int
    failures: list[CaseFailure] = field(default_factory=list)


def _snapshot(name: str, fmt: str, specs: list[Any]) -> SchemaSnapshot:
    return SchemaSnapshot(source=name, format=fmt, columns=parse_columns(specs))


class _CountingLLM:
    """Wraps an LLMPort and counts calls and invalid outputs (LLMError)."""

    def __init__(self, inner: LLMPort) -> None:
        self._inner = inner
        self.calls = 0
        self.invalid = 0

    def complete_structured(self, **kwargs: Any) -> Any:
        self.calls += 1
        try:
            return self._inner.complete_structured(**kwargs)
        except LLMError:
            self.invalid += 1
            raise


class _OracleLLM:
    """Answers with a case's expected resolution; only used to record Replay transcripts."""

    def __init__(self, decision: str | None) -> None:
        self.decision = decision

    def complete_structured(self, *, schema: Any, **_: Any) -> Any:
        if self.decision is None:
            raise LLMError("no expected_resolution for this case")
        return schema(
            decision=Decision(self.decision),
            confidence=0.9,
            rationale="Golden-set expectation.",
        )


def _base_report(case: GoldenCase) -> DriftReport:
    return build_report(
        _snapshot(f"{case.id}/baseline", case.format, case.baseline),
        _snapshot(f"{case.id}/current", case.format, case.current),
    )


def record_replay(cases: list[GoldenCase], directory: Path) -> None:
    """Record transcripts for every resolvable case so evals can run in Replay mode."""
    for case in cases:
        Resolve(ReplayAdapter(directory, record_with=_OracleLLM(case.expected_resolution))).run(
            _base_report(case)
        )


def _actual_resolution(report: DriftReport) -> str:
    """Decision the pipeline reached: LLM verdict on a candidate, else a plain drop and add."""
    for change in report.changes:
        if change.resolution is not None or change.needs_human_review:
            if change.needs_human_review or change.resolution is None:
                return Decision.UNKNOWN.value
            return change.resolution.decision.value
    return Decision.DROP_AND_ADD.value


def _ddl_applicable(report: DriftReport) -> bool:
    """Nested-field changes are skipped by the generator by design (FR-5), so not scored."""
    plan = generate_migration(report, Dialect.DUCKDB, "dataset")
    return not any("nested" in s.reason for s in plan.skipped)


def _ddl_errors(report: DriftReport) -> list[str]:
    """Validation errors across all dialects; empty when the DDL is valid everywhere."""
    proposer = ProposeMigration()
    errors: list[str] = []
    for dialect in Dialect:
        try:
            errors += [f"{dialect.value}: {e}" for e in proposer.run(report, dialect).errors]
        except Exception as exc:  # noqa: BLE001 - a generator crash counts as invalid DDL
            errors.append(f"{dialect.value}: {exc!r}")
    return errors


def evaluate(cases: list[GoldenCase], llm: LLMPort | None = None) -> EvalResult:
    """Run the pipeline on each case and score it against the expectation.

    Without `llm` the LLM-dependent metrics are None; with one (e.g. a ReplayAdapter) the
    resolve and migration stages run too.
    """
    counting = _CountingLLM(llm) if llm is not None else None
    resolution_total = resolution_correct = ddl_total = ddl_valid = 0
    correct = 0
    breaking_expected = 0
    breaking_found = 0
    failures: list[CaseFailure] = []

    for case in cases:
        report = _base_report(case)
        if counting is not None:
            resolved = Resolve(counting).run(report)
            if _ddl_applicable(resolved):
                ddl_total += 1
                if ddl_errors := _ddl_errors(resolved):
                    failures.append(CaseFailure(case.id, "invalid DDL: " + " | ".join(ddl_errors)))
                else:
                    ddl_valid += 1
            if case.expected_resolution is not None:
                resolution_total += 1
                resolution_correct += _actual_resolution(resolved) == case.expected_resolution
        actual = {
            (c.change_type.value, c.path, c.severity.value, c.rule_id) for c in report.changes
        }
        expected = {c.key() for c in case.expected.changes}
        actual_breaking = {(t, p) for t, p, s, _ in actual if s == Severity.BREAKING.value}
        expected_breaking = {(t, p) for t, p, s, _ in expected if s == Severity.BREAKING.value}
        breaking_expected += len(expected_breaking)
        breaking_found += len(expected_breaking & actual_breaking)

        if actual == expected and report.verdict == case.expected.verdict:
            correct += 1
            continue
        parts = []
        if report.verdict != case.expected.verdict:
            parts.append(f"verdict {report.verdict.value} != {case.expected.verdict.value}")
        if missing := sorted(expected - actual):
            parts.append(f"missing {missing}")
        if extra := sorted(actual - expected):
            parts.append(f"unexpected {extra}")
        failures.append(CaseFailure(case.id, "; ".join(parts)))

    metrics: dict[str, float | None] = {
        "classification_accuracy": correct / len(cases) if cases else 0.0,
        "breaking_recall": breaking_found / breaking_expected if breaking_expected else 1.0,
    }
    if counting is None:
        metrics.update(dict.fromkeys(LLM_METRICS))
    else:
        metrics["rename_resolution_accuracy"] = (
            resolution_correct / resolution_total if resolution_total else None
        )
        metrics["ddl_validity_rate"] = ddl_valid / ddl_total if ddl_total else None
        metrics["invalid_output_rate"] = (
            counting.invalid / counting.calls if counting.calls else 0.0
        )
    return EvalResult(metrics=metrics, case_count=len(cases), failures=failures)


def check_thresholds(
    metrics: dict[str, float | None], thresholds: dict[str, Any], *, case_count: int
) -> list[str]:
    """Return one message per violated threshold; metrics that are n/a are skipped."""
    violations: list[str] = []
    if case_count < thresholds.get("min_cases", 0):
        violations.append(f"golden cases {case_count} < required {thresholds['min_cases']}")
    for name, floor in thresholds.get("min", {}).items():
        value = metrics.get(name)
        if value is not None and value < floor:
            violations.append(f"{name} {value:.3f} < min {floor}")
    for name, ceiling in thresholds.get("max", {}).items():
        value = metrics.get(name)
        if value is not None and value > ceiling:
            violations.append(f"{name} {value:.3f} > max {ceiling}")
    return violations
