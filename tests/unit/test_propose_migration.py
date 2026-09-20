from pathlib import Path

import pytest

from schemasentinel.adapters.llm.fake import FakeLLM
from schemasentinel.application.detect_drift import build_report
from schemasentinel.application.propose_migration import ProposeMigration
from schemasentinel.application.resolve import Resolve
from schemasentinel.domain.migration import Dialect, Safety
from schemasentinel.domain.models import Decision, Resolution, SchemaSnapshot
from schemasentinel.evals.golden import load_cases, parse_columns

CASES = load_cases(Path(__file__).parents[1] / "golden" / "cases")


def _snap(cols, fmt="parquet"):
    return SchemaSnapshot(source="s", format=fmt, columns=parse_columns(list(cols)))


def _report(base, cur):
    return build_report(_snap(base), _snap(cur))


def test_order_adds_before_type_changes_before_drops():
    r = _report(["a:INTEGER", "b:INTEGER", "c:VARCHAR"], ["a:BIGINT", "b:INTEGER", "d:DOUBLE"])
    plan = ProposeMigration().run(r).plan
    phases = [s.phase for s in plan.statements]
    assert phases == sorted(phases)
    assert "ADD COLUMN" in plan.statements[0].sql
    assert "DROP COLUMN" in plan.statements[-1].sql
    safety = {s.path: s.safety for s in plan.statements}
    assert safety["d"] is Safety.SAFE
    assert safety["c"] is Safety.DESTRUCTIVE
    assert safety["a"] is Safety.SAFE  # widening


def test_narrowing_is_destructive():
    plan = ProposeMigration().run(_report(["a:BIGINT"], ["a:INTEGER"])).plan
    assert plan.statements[0].safety is Safety.DESTRUCTIVE


def test_other_dialects_generate_and_pass_structural_check():
    r = _report(["a:INTEGER", "c:VARCHAR"], ["a:BIGINT", "d:DOUBLE!"])
    spark = ProposeMigration().run(r, Dialect.SPARK, "db.t")
    tsql = ProposeMigration().run(r, Dialect.TSQL, "dbo.t")
    assert spark.valid and tsql.valid
    assert spark.validation == tsql.validation == "structural"
    assert any("ADD COLUMNS (`d` DOUBLE)" in s.sql for s in spark.plan.statements)
    assert any("ADD [d] FLOAT NULL" in s.sql for s in tsql.plan.statements)
    assert any("DROP COLUMN [c]" in s.sql for s in tsql.plan.statements)


def test_resolved_rename_and_drop_and_add():
    r = _report(["id:BIGINT", "customer_name:VARCHAR"], ["id:BIGINT", "cust_name:VARCHAR"])
    rename = Resolution(decision=Decision.RENAME, confidence=0.9, rationale="x")
    renamed = Resolve(FakeLLM([rename]))
    out = ProposeMigration().run(renamed.run(r))
    assert out.valid, out.errors
    assert "RENAME COLUMN" in out.plan.statements[0].sql
    dropped = Resolve(
        FakeLLM([Resolution(decision=Decision.DROP_AND_ADD, confidence=0.9, rationale="x")])
    )
    out = ProposeMigration().run(dropped.run(r))
    assert out.valid, out.errors
    assert [s.phase for s in out.plan.statements] == [0, 3]


def test_unresolved_rename_is_skipped():
    r = _report(["id:BIGINT", "customer_name:VARCHAR"], ["id:BIGINT", "cust_name:VARCHAR"])
    plan = ProposeMigration().run(r).plan
    assert plan.statements == ()
    assert plan.skipped[0].reason == "unresolved rename candidate"


def test_ddl_that_does_not_reach_current_schema_is_invalid():
    r = _report(["a:INTEGER"], ["a:INTEGER", "b:INTEGER"])
    r = r.model_copy(update={"changes": ()})  # DDL omits the add
    out = ProposeMigration().run(r)
    assert not out.valid and out.errors


@pytest.mark.parametrize("case", CASES, ids=[c.id for c in CASES])
def test_golden_duckdb_ddl_yields_current_schema(case):
    report = build_report(
        _snap(case.baseline, case.format), _snap(case.current, case.format)
    )
    cols = report.baseline.columns + report.current.columns
    if any(c.children for c in cols):
        pytest.skip("nested columns are not migrated")
    if any(c.severity.value == "needs_review" for c in report.changes):
        pytest.skip("unresolved changes are not migrated")
    out = ProposeMigration().run(report)
    assert out.valid, (out.errors, [s.sql for s in out.plan.statements])
