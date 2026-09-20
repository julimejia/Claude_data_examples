import pytest

from schemasentinel.application.detect_drift import build_report
from schemasentinel.application.propose_migration import ProposeMigration
from schemasentinel.domain.migration import Dialect
from schemasentinel.domain.models import SchemaSnapshot
from schemasentinel.evals.golden import parse_columns


def _report():
    def snap(cols):
        return SchemaSnapshot(source="s", format="parquet", columns=parse_columns(cols))

    return build_report(snap(["a:INTEGER", "c:VARCHAR"]), snap(["a:BIGINT", "d:DOUBLE!"]))


def test_sqlglot_parses_spark_and_tsql_when_installed():
    pytest.importorskip("sqlglot")
    for dialect in (Dialect.SPARK, Dialect.TSQL):
        out = ProposeMigration().run(_report(), dialect, "t")
        assert out.valid and out.parsed


def test_duckdb_is_executed_not_parsed():
    out = ProposeMigration().run(_report())
    assert out.valid and out.validation == "executed" and not out.parsed
