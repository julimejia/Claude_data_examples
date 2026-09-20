from __future__ import annotations

import pytest

from schemasentinel.domain.diff import diff
from schemasentinel.domain.models import (
    ChangeType,
    Column,
    SchemaChange,
    SchemaSnapshot,
    Severity,
)
from schemasentinel.domain.rules import classify, classify_all


def col(name: str, data_type: str = "BIGINT", nullable: bool = True, position: int = 0, **kw):
    return Column(name=name, data_type=data_type, nullable=nullable, position=position, **kw)


def raw(change_type, baseline=None, current=None, confidence=1.0) -> SchemaChange:
    return SchemaChange(
        change_type=change_type,
        path="c",
        baseline=baseline,
        current=current,
        severity=Severity.NEEDS_REVIEW,
        rule_id="DIFF-UNCLASSIFIED",
        reason="raw",
        confidence=confidence,
    )


CASES = [
    ("added nullable", ChangeType.COLUMN_ADDED, None, col("c"), "parquet",
     "ADD-NULLABLE", Severity.NON_BREAKING),
    ("added not null no default", ChangeType.COLUMN_ADDED, None, col("c", nullable=False),
     "parquet", "ADD-NOT-NULL", Severity.BREAKING),
    ("added not null with default", ChangeType.COLUMN_ADDED, None,
     col("c", nullable=False, default="0"), "parquet", "ADD-NOT-NULL-DEFAULT",
     Severity.NON_BREAKING),
    ("removed", ChangeType.COLUMN_REMOVED, col("c"), None, "parquet",
     "REMOVE", Severity.BREAKING),
    ("int widened", ChangeType.TYPE_CHANGED, col("c", "INTEGER"), col("c", "BIGINT"),
     "parquet", "TYPE-WIDENED", Severity.NON_BREAKING),
    ("float widened", ChangeType.TYPE_CHANGED, col("c", "FLOAT"), col("c", "DOUBLE"),
     "parquet", "TYPE-WIDENED", Severity.NON_BREAKING),
    ("varchar length up", ChangeType.TYPE_CHANGED, col("c", "VARCHAR(10)"),
     col("c", "VARCHAR(20)"), "parquet", "TYPE-WIDENED", Severity.NON_BREAKING),
    ("date to timestamp", ChangeType.TYPE_CHANGED, col("c", "DATE"), col("c", "TIMESTAMP"),
     "parquet", "TYPE-WIDENED", Severity.NON_BREAKING),
    ("int narrowed", ChangeType.TYPE_CHANGED, col("c", "BIGINT"), col("c", "INTEGER"),
     "parquet", "TYPE-NARROWED", Severity.BREAKING),
    ("timestamp to date", ChangeType.TYPE_CHANGED, col("c", "TIMESTAMP"), col("c", "DATE"),
     "parquet", "TYPE-NARROWED", Severity.BREAKING),
    ("numeric to string", ChangeType.TYPE_CHANGED, col("c", "INTEGER"), col("c", "VARCHAR"),
     "parquet", "TYPE-CATEGORY", Severity.BREAKING),
    ("string to date", ChangeType.TYPE_CHANGED, col("c", "VARCHAR"), col("c", "DATE"),
     "parquet", "TYPE-CATEGORY", Severity.BREAKING),
    ("nullable to not null", ChangeType.NULLABILITY_CHANGED, col("c"),
     col("c", nullable=False), "parquet", "NULL-TO-NOT-NULL", Severity.BREAKING),
    ("not null to nullable", ChangeType.NULLABILITY_CHANGED, col("c", nullable=False),
     col("c"), "parquet", "NOT-NULL-TO-NULL", Severity.WARNING),
    ("reorder parquet", ChangeType.POSITION_CHANGED, col("c"), col("c", position=1),
     "parquet", "REORDER-NAMED", Severity.NON_BREAKING),
    ("reorder delta", ChangeType.POSITION_CHANGED, col("c"), col("c", position=1),
     "delta", "REORDER-NAMED", Severity.NON_BREAKING),
    ("reorder csv", ChangeType.POSITION_CHANGED, col("c"), col("c", position=1),
     "csv", "REORDER-POSITIONAL", Severity.BREAKING),
    ("reorder CSV uppercase", ChangeType.POSITION_CHANGED, col("c"), col("c", position=1),
     "CSV", "REORDER-POSITIONAL", Severity.BREAKING),
    ("rename candidate", ChangeType.RENAME_CANDIDATE, col("c"), col("d"), "parquet",
     "RENAME-CANDIDATE", Severity.NEEDS_REVIEW),
]  # fmt: skip


@pytest.mark.parametrize(
    "case_id,change_type,baseline,current,fmt,rule_id,severity",
    CASES,
    ids=[c[0] for c in CASES],
)
def test_rule_table(case_id, change_type, baseline, current, fmt, rule_id, severity):
    result = classify(raw(change_type, baseline, current), source_format=fmt)
    assert result.rule_id == rule_id
    assert result.severity is severity
    assert result.reason
    if change_type is not ChangeType.RENAME_CANDIDATE:  # rename keeps the diff's own reason
        assert result.reason != "raw"
    assert 0.0 <= result.confidence <= 1.0


def test_deterministic_rules_have_full_confidence():
    for _, ct, b, c, fmt, _, _ in CASES:
        if ct is not ChangeType.RENAME_CANDIDATE:
            assert classify(raw(ct, b, c), source_format=fmt).confidence == 1.0


def test_rename_keeps_similarity_confidence():
    result = classify(
        raw(ChangeType.RENAME_CANDIDATE, col("c"), col("d"), confidence=0.72),
        source_format="parquet",
    )
    assert result.confidence == 0.72


def test_classify_does_not_touch_other_fields():
    change = raw(ChangeType.COLUMN_REMOVED, col("c"), None)
    result = classify(change, source_format="parquet")
    assert (result.path, result.baseline, result.current, result.change_type) == (
        change.path,
        change.baseline,
        change.current,
        change.change_type,
    )


def test_classify_all_on_real_diff():
    base = SchemaSnapshot(
        source="a", format="csv", columns=(col("id", "INTEGER", position=0), col("x", position=1))
    )
    cur = SchemaSnapshot(
        source="b",
        format="csv",
        columns=(col("x", position=0), col("id", "BIGINT", position=1), col("new", position=2)),
    )
    changes = classify_all(diff(base, cur), source_format=cur.format)
    by_rule = {c.rule_id for c in changes}
    assert {"TYPE-WIDENED", "ADD-NULLABLE", "REORDER-POSITIONAL"} <= by_rule
    assert all(c.rule_id != "DIFF-UNCLASSIFIED" for c in changes)
