from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from schemasentinel.application.detect_drift import build_report
from schemasentinel.domain.models import SchemaSnapshot, Severity
from schemasentinel.evals.golden import GoldenCase, parse_columns

# Metrics that need the LLM stages (FR-4, FR-5); reported as n/a until those exist.
PENDING_METRICS = ("rename_resolution_accuracy", "ddl_validity_rate", "invalid_output_rate")


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


def evaluate(cases: list[GoldenCase]) -> EvalResult:
    """Run the deterministic pipeline on each case and score it against the expectation."""
    correct = 0
    breaking_expected = 0
    breaking_found = 0
    failures: list[CaseFailure] = []

    for case in cases:
        report = build_report(
            _snapshot(f"{case.id}/baseline", case.format, case.baseline),
            _snapshot(f"{case.id}/current", case.format, case.current),
        )
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
    metrics.update(dict.fromkeys(PENDING_METRICS))
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
