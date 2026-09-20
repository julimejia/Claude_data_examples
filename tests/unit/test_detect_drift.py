from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path

import pytest

from schemasentinel.application.detect_drift import DetectDrift, build_report, overall_verdict
from schemasentinel.application.report import render_json, render_markdown
from schemasentinel.domain.models import Column, DriftReport, SchemaSnapshot, Verdict

SNAPSHOT_DIR = Path(__file__).parent / "snapshots"


def col(name, data_type="BIGINT", nullable=True, position=0):
    return Column(name=name, data_type=data_type, nullable=nullable, position=position)


def snap(source, *cols, fmt="parquet"):
    return SchemaSnapshot(source=source, format=fmt, columns=tuple(cols))


BASE = snap("base.parquet", col("id", nullable=False), col("name", "VARCHAR", position=1))
CUR_BREAKING = snap(
    "cur.parquet", col("id", "VARCHAR", nullable=False), col("name", "VARCHAR", position=1)
)
CUR_NONBREAKING = snap(
    "cur.parquet",
    col("id", nullable=False),
    col("name", "VARCHAR", position=1),
    col("note", "VARCHAR", position=2),
)


class FakeSource:
    def __init__(self, snaps):
        self.snaps = snaps
        self.calls = []

    def snapshot(self, ref, *, version=None):
        self.calls.append((ref, version))
        return self.snaps[ref]


def fixed_use_case(source):
    return DetectDrift(
        source,
        clock=lambda: datetime(2026, 1, 2, 3, 4, 5, tzinfo=UTC),
        run_id=lambda: "run-1",
    )


def stable_report(current):
    report = fixed_use_case(FakeSource({"a": BASE, "b": current})).run("a", "b")
    return report.model_copy(update={"run_metadata": {**report.run_metadata, "latency_ms": 0.0}})


def test_verdict_none_for_identical():
    report = build_report(BASE, BASE)
    assert report.verdict is Verdict.NONE and report.changes == ()


def test_verdict_non_breaking():
    assert build_report(BASE, CUR_NONBREAKING).verdict is Verdict.NON_BREAKING


def test_verdict_breaking():
    assert build_report(BASE, CUR_BREAKING).verdict is Verdict.BREAKING


def test_overall_verdict_empty():
    assert overall_verdict(()) is Verdict.NONE


def test_use_case_reads_both_sources_and_records_metadata():
    source = FakeSource({"a": BASE, "b": CUR_BREAKING})
    report = fixed_use_case(source).run("a", "b", baseline_version="1")
    assert source.calls == [("a", "1"), ("b", None)]
    md = report.run_metadata
    assert md["run_id"] == "run-1"
    assert md["generated_at"] == "2026-01-02T03:04:05+00:00"
    assert md["llm_calls"] == 0
    assert md["change_counts"] == {"breaking": 1}
    assert report.verdict is Verdict.BREAKING


def test_json_report_round_trips_and_is_versioned():
    report = stable_report(CUR_BREAKING)
    text = render_json(report)
    assert json.loads(text)["schema_version"] == 1
    assert json.loads(text)["verdict"] == "breaking"
    assert DriftReport.model_validate_json(text) == report


@pytest.mark.parametrize(
    "name,current",
    [("breaking", CUR_BREAKING), ("non_breaking", CUR_NONBREAKING), ("none", BASE)],
)
def test_markdown_snapshot(name, current):
    actual = render_markdown(stable_report(current))
    path = SNAPSHOT_DIR / f"report_{name}.md"
    assert path.exists(), f"missing snapshot; expected content:\n{actual}"
    assert actual == path.read_text(encoding="utf-8")
