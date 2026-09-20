import sys

import pytest
from pydantic import ValidationError

import schemasentinel.application.propose_migration as pm
from schemasentinel.adapters.llm.fake import FakeLLM
from schemasentinel.application.detect_drift import build_report
from schemasentinel.application.propose_migration import ProposeMigration
from schemasentinel.application.resolve import Resolve
from schemasentinel.domain.migration import (
    Dialect,
    MigrationPlan,
    MigrationStatement,
    Safety,
    generate_migration,
    quote,
)
from schemasentinel.domain.models import Decision, Resolution, SchemaSnapshot
from schemasentinel.evals.golden import parse_columns

ALL = [Dialect.DUCKDB, Dialect.SPARK, Dialect.TSQL]


def _snap(cols, fmt="parquet"):
    return SchemaSnapshot(source="s", format=fmt, columns=parse_columns(list(cols)))


def _report(base, cur, fmt="parquet"):
    return build_report(_snap(base, fmt), _snap(cur, fmt))


@pytest.mark.parametrize("dialect", ALL)
def test_identical_schemas_give_empty_valid_plan(dialect):
    r = _report(["a:INTEGER", "b:VARCHAR"], ["a:INTEGER", "b:VARCHAR"])
    out = ProposeMigration().run(r, dialect)
    assert out.plan.statements == () and out.plan.skipped == ()
    assert out.valid and out.errors == ()


@pytest.mark.parametrize("dialect", ALL)
def test_run_is_idempotent(dialect):
    r = _report(["a:INTEGER", "c:VARCHAR"], ["a:BIGINT", "d:DOUBLE!"])
    assert ProposeMigration().run(r, dialect) == ProposeMigration().run(r, dialect)


@pytest.mark.parametrize("dialect", ALL)
def test_every_statement_ends_with_semicolon_and_phases_sorted(dialect):
    r = _report(["a:INTEGER", "b:INTEGER", "c:VARCHAR"], ["a:BIGINT", "b:INTEGER", "d:DOUBLE"])
    plan = ProposeMigration().run(r, dialect).plan
    assert all(s.sql.endswith(";") and not s.sql.endswith(";;") for s in plan.statements)
    phases = [s.phase for s in plan.statements]
    assert phases == sorted(phases)


def test_not_null_add_is_safe_add_then_destructive_constraint():
    r = _report(["a:INTEGER"], ["a:INTEGER", "b:INTEGER!"])
    out = ProposeMigration().run(r)
    assert out.valid, out.errors
    kinds = [(s.safety, s.phase) for s in out.plan.statements]
    assert kinds[0] == (Safety.SAFE, 0)
    assert kinds[1][0] is Safety.DESTRUCTIVE


def test_tsql_not_null_add_adds_nullable_then_alters():
    r = _report(["a:INTEGER"], ["a:INTEGER", "b:INTEGER!"])
    sqls = [s.sql for s in ProposeMigration().run(r, Dialect.TSQL, "dbo.t").plan.statements]
    assert "ADD [b] INT NULL" in sqls[0]
    assert "ALTER COLUMN [b] INT NOT NULL" in sqls[1]


def test_nullable_to_not_null_and_back_valid_in_duckdb():
    tighten = ProposeMigration().run(_report(["a:INTEGER"], ["a:INTEGER!"]))
    loosen = ProposeMigration().run(_report(["a:INTEGER!"], ["a:INTEGER"]))
    assert tighten.valid, tighten.errors
    assert loosen.valid, loosen.errors
    assert tighten.plan.statements[0].safety is Safety.DESTRUCTIVE
    assert loosen.plan.statements[0].safety is Safety.SAFE


@pytest.mark.parametrize("dialect", ALL)
def test_position_only_change_emits_nothing(dialect):
    r = _report(["a:INTEGER", "b:INTEGER"], ["b:INTEGER", "a:INTEGER"])
    out = ProposeMigration().run(r, dialect)
    assert out.plan.statements == ()
    assert out.valid


@pytest.mark.parametrize(
    "name", ['we"ird', "sp ace", "semi;colon", "it's", "Ünï", "select", "a.b"]
)
def test_awkward_column_names_execute_in_duckdb(name):
    r = _report(["id:INTEGER"], ["id:INTEGER", {"name": name, "type": "VARCHAR"}])
    out = ProposeMigration().run(r)
    assert out.valid, (out.errors, [s.sql for s in out.plan.statements])


def test_quote_escapes_delimiters():
    assert quote('a"b', Dialect.DUCKDB) == '"a""b"'
    assert quote("a`b", Dialect.SPARK) == "`a``b`"
    assert quote("a]b", Dialect.TSQL) == "[a]]b]"


def test_dotted_table_is_quoted_per_part():
    r = _report(["a:INTEGER"], ["a:INTEGER", "b:INTEGER"])
    assert '"main"."t"' in generate_migration(r, Dialect.DUCKDB, "main.t").statements[0].sql
    assert "`db`.`t`" in generate_migration(r, Dialect.SPARK, "db.t").statements[0].sql
    assert "[dbo].[t]" in generate_migration(r, Dialect.TSQL, "dbo.t").statements[0].sql


def test_table_name_with_quote_does_not_break_duckdb_validation():
    r = _report(["a:INTEGER"], ["a:INTEGER", "b:INTEGER"])
    out = ProposeMigration().run(r, Dialect.DUCKDB, 'we"ird')
    assert out.valid, out.errors


def test_tsql_rename_escapes_single_quotes():
    r = _report(["id:BIGINT", "customer_name:VARCHAR"], ["id:BIGINT", "cust_name:VARCHAR"])
    res = Resolution(decision=Decision.RENAME, confidence=0.9, rationale="x")
    out = ProposeMigration().run(Resolve(FakeLLM([res])).run(r), Dialect.TSQL, "dbo.t")
    assert out.valid, out.errors
    assert "sp_rename N'[dbo].[t].[customer_name]', N'cust_name', N'COLUMN'" in (
        out.plan.statements[0].sql
    )


def test_rename_with_type_change_and_nullability_is_valid_in_duckdb():
    r = _report(["id:BIGINT", "customer_name:INTEGER"], ["id:BIGINT", "cust_name:BIGINT!"])
    res = Resolution(decision=Decision.RENAME, confidence=0.9, rationale="x")
    resolved = Resolve(FakeLLM([res])).run(r)
    out = ProposeMigration().run(resolved)
    assert out.valid, (out.errors, [s.sql for s in out.plan.statements])
    phases = [s.phase for s in out.plan.statements]
    assert phases == sorted(phases) and phases[0] == 1


def test_low_confidence_rename_is_skipped_not_emitted():
    r = _report(["id:BIGINT", "customer_name:VARCHAR"], ["id:BIGINT", "cust_name:VARCHAR"])
    low = Resolution(decision=Decision.RENAME, confidence=0.2, rationale="x")
    out = ProposeMigration().run(Resolve(FakeLLM([low])).run(r))
    assert out.plan.statements == ()
    assert len(out.plan.skipped) == 1


def test_unknown_resolution_is_skipped():
    r = _report(["id:BIGINT", "customer_name:VARCHAR"], ["id:BIGINT", "cust_name:VARCHAR"])
    unk = Resolution(decision=Decision.UNKNOWN, confidence=0.99, rationale="x")
    out = ProposeMigration().run(Resolve(FakeLLM([unk])).run(r))
    assert out.plan.statements == ()
    assert out.plan.skipped[0].path


def test_nested_changes_are_skipped_with_reason():
    base = [{"name": "s", "type": "STRUCT", "children": ["x:INTEGER"]}]
    cur = [{"name": "s", "type": "STRUCT", "children": ["x:INTEGER", "y:INTEGER"]}]
    r = _report(base, cur)
    plan = ProposeMigration().run(r).plan
    assert plan.statements == ()
    assert any("." in s.path and "nested" in s.reason for s in plan.skipped)


def test_default_is_omitted_for_spark_and_emitted_elsewhere():
    r = _report(["a:INTEGER"], ["a:INTEGER", {"name": "b", "type": "INTEGER", "default": "0"}])
    assert "DEFAULT" not in generate_migration(r, Dialect.SPARK, "t").statements[0].sql
    assert "DEFAULT 0" in generate_migration(r, Dialect.DUCKDB, "t").statements[0].sql
    assert "DEFAULT 0" in generate_migration(r, Dialect.TSQL, "t").statements[0].sql


@pytest.mark.parametrize("dialect", [Dialect.SPARK, Dialect.TSQL])
def test_malformed_statement_makes_structural_validation_fail(dialect, monkeypatch):
    bad = MigrationPlan(
        dialect=dialect,
        table="t",
        statements=(
            MigrationStatement(sql="DROP TABLE t;", safety=Safety.DESTRUCTIVE, path="a", phase=3),
        ),
    )
    monkeypatch.setattr(pm, "generate_migration", lambda *_a, **_k: bad)
    out = ProposeMigration().run(_report(["a:INTEGER"], ["a:INTEGER"]), dialect)
    assert not out.valid and out.errors


@pytest.mark.parametrize(
    "sql",
    [
        "ALTER TABLE t ADD COLUMN (a INT;",
        "ALTER TABLE t ADD COLUMN a INT",  # no semicolon
        "ALTER TABLE `t ADD COLUMN a INT;",  # open quote
        "ALTER TABLE t ADD COLUMN a INT);",  # extra close paren
    ],
)
def test_structural_check_rejects_malformed_sql(sql, monkeypatch):
    bad = MigrationPlan(
        dialect=Dialect.SPARK,
        table="t",
        statements=(MigrationStatement(sql=sql, safety=Safety.SAFE, path="a", phase=0),),
    )
    monkeypatch.setattr(pm, "generate_migration", lambda *_a, **_k: bad)
    out = ProposeMigration().run(_report(["a:INTEGER"], ["a:INTEGER"]), Dialect.SPARK)
    assert not out.valid


def test_works_without_sqlglot_installed(monkeypatch):
    monkeypatch.setitem(sys.modules, "sqlglot", None)
    r = _report(["a:INTEGER"], ["a:INTEGER", "b:INTEGER"])
    out = ProposeMigration().run(r, Dialect.SPARK)
    assert out.valid and out.parsed is False and out.validation == "structural"


def test_sqlglot_rejects_garbage_when_installed(monkeypatch):
    pytest.importorskip("sqlglot")
    # Passes the structural check (ALTER TABLE ... ;) but sqlglot raises ParseError on it in every dialect.
    # sqlglot is lenient elsewhere: e.g. "ADD ADD ADD" parses as a valid T-SQL Alter, and unsupported
    # syntax falls back to a generic Command instead of raising, so those cases prove nothing here.
    bad = MigrationPlan(
        dialect=Dialect.TSQL,
        table="t",
        statements=(
            MigrationStatement(
                sql="ALTER TABLE t ADD b INT ==== ;", safety=Safety.SAFE, path="a", phase=0
            ),
        ),
    )
    monkeypatch.setattr(pm, "generate_migration", lambda *_a, **_k: bad)
    out = ProposeMigration().run(_report(["a:INTEGER"], ["a:INTEGER"]), Dialect.TSQL)
    assert out.parsed and not out.valid


def test_duckdb_validation_does_not_leak_between_runs():
    r = _report(["a:INTEGER"], ["a:INTEGER", "b:INTEGER"])
    assert ProposeMigration().run(r).valid
    assert ProposeMigration().run(r).valid  # fresh in-memory db each time


def test_models_are_frozen():
    out = ProposeMigration().run(_report(["a:INTEGER"], ["a:INTEGER"]))
    with pytest.raises(ValidationError):
        out.valid = False  # type: ignore[misc]


def test_type_change_across_categories_is_destructive_and_valid_in_duckdb():
    out = ProposeMigration().run(_report(["a:INTEGER"], ["a:VARCHAR"]))
    assert out.plan.statements[0].safety is Safety.DESTRUCTIVE
    assert out.valid, out.errors


@pytest.mark.parametrize("dialect", [Dialect.SPARK, Dialect.TSQL])
def test_wide_column_set_all_dialects_structurally_valid(dialect):
    base = [f"c{i}:INTEGER" for i in range(50)]
    cur = [f"c{i}:BIGINT" for i in range(25)] + [f"n{i}:VARCHAR" for i in range(25)]
    out = ProposeMigration().run(_report(base, cur), dialect, "dbo.t")
    assert out.valid, out.errors
    phases = [s.phase for s in out.plan.statements]
    assert phases == sorted(phases)
