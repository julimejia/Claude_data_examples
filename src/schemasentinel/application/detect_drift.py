from __future__ import annotations

import time
from collections import Counter
from collections.abc import Callable
from datetime import UTC, datetime
from typing import Any
from uuid import uuid4

from schemasentinel.domain.diff import diff
from schemasentinel.domain.models import (
    DriftReport,
    SchemaChange,
    SchemaSnapshot,
    Severity,
    Verdict,
)
from schemasentinel.domain.rules import classify_all
from schemasentinel.ports.schema_source import SchemaSource


def overall_verdict(changes: tuple[SchemaChange, ...]) -> Verdict:
    """Any breaking change -> breaking; any other change -> non_breaking; else none."""
    if any(c.severity is Severity.BREAKING for c in changes):
        return Verdict.BREAKING
    return Verdict.NON_BREAKING if changes else Verdict.NONE


def build_report(
    baseline: SchemaSnapshot,
    current: SchemaSnapshot,
    run_metadata: dict[str, Any] | None = None,
) -> DriftReport:
    """Pure: diff + classify two snapshots into a DriftReport."""
    changes = classify_all(diff(baseline, current), source_format=current.format)
    counts = Counter(c.severity.value for c in changes)
    metadata = {"change_counts": dict(sorted(counts.items())), **(run_metadata or {})}
    return DriftReport(
        baseline=baseline,
        current=current,
        verdict=overall_verdict(changes),
        changes=changes,
        run_metadata=metadata,
    )


class DetectDrift:
    """Use case: read two snapshots through the SchemaSource port and report the drift."""

    def __init__(
        self,
        source: SchemaSource,
        *,
        clock: Callable[[], datetime] = lambda: datetime.now(UTC),
        run_id: Callable[[], str] = lambda: uuid4().hex,
    ) -> None:
        self._source = source
        self._clock = clock
        self._run_id = run_id

    def run(
        self,
        baseline_ref: str,
        current_ref: str,
        *,
        baseline_version: str | None = None,
        current_version: str | None = None,
    ) -> DriftReport:
        started = time.perf_counter()
        baseline = self._source.snapshot(baseline_ref, version=baseline_version)
        current = self._source.snapshot(current_ref, version=current_version)
        latency_ms = round((time.perf_counter() - started) * 1000, 1)
        return build_report(
            baseline,
            current,
            {
                "run_id": self._run_id(),
                "generated_at": self._clock().isoformat(),
                "baseline_source": baseline.source,
                "current_source": current.source,
                "llm_calls": 0,
                "latency_ms": latency_ms,
            },
        )
