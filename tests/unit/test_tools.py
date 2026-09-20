from schemasentinel.application.detect_drift import build_report
from schemasentinel.application.tools import MAX_SAMPLE_VALUES, build_tools
from schemasentinel.domain.models import SchemaSnapshot
from schemasentinel.evals.golden import parse_columns


def _snap(cols):
    return SchemaSnapshot(source="s", format="parquet", columns=parse_columns(list(cols)))


def _tools(sampler=None):
    report = build_report(
        _snap(["a:INTEGER", "b:VARCHAR"]),
        _snap(["a:BIGINT", "b:VARCHAR", "c:DOUBLE"]),
    )
    return {t.name: t.handler for t in build_tools(report, sampler)}


def test_exposes_four_tools():
    assert set(_tools()) == {"get_snapshot", "get_diff", "sample_column_values", "validate_ddl"}


def test_get_snapshot():
    tools = _tools()
    assert [c["name"] for c in tools["get_snapshot"]("baseline")["columns"]] == ["a", "b"]
    assert len(tools["get_snapshot"]()["columns"]) == 3
    assert "error" in tools["get_snapshot"]("other")


def test_get_diff():
    out = _tools()["get_diff"]()
    assert out["verdict"] in {"breaking", "non_breaking", "none"}
    assert {c["path"] for c in out["changes"]} == {"a", "c"}


def test_sample_values_capped_at_20():
    tools = _tools(lambda col, n: range(1000))
    assert len(tools["sample_column_values"]("a")["values"]) == MAX_SAMPLE_VALUES
    assert len(tools["sample_column_values"]("a", 500)["values"]) == 20
    assert len(tools["sample_column_values"]("a", 3)["values"]) == 3
    assert tools["sample_column_values"]("a", -1)["values"] == []


def test_sample_values_errors():
    assert "error" in _tools()["sample_column_values"]("a")
    assert "error" in _tools(lambda c, n: [])["sample_column_values"]("zzz")


def test_sample_values_truncates_long_strings():
    out = _tools(lambda c, n: ["x" * 5000])["sample_column_values"]("b")
    assert len(out["values"][0]) == 200


def test_validate_ddl():
    tools = _tools()
    out = tools["validate_ddl"]("duckdb")
    assert out["valid"] is True and out["validation"] == "executed"
    assert any("ADD COLUMN" in s for s in out["statements"])
    assert tools["validate_ddl"]("tsql")["validation"] == "structural"
    assert "error" in tools["validate_ddl"]("oracle")
