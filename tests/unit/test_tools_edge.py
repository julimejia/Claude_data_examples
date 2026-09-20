import json

from schemasentinel.application.detect_drift import build_report
from schemasentinel.application.tools import build_tools
from schemasentinel.domain.models import SchemaSnapshot
from schemasentinel.evals.golden import parse_columns


def _snap(cols):
    return SchemaSnapshot(source="s", format="parquet", columns=parse_columns(list(cols)))


def _tools(
    sampler=None,
    base=("a:INTEGER", "b:VARCHAR"),
    cur=("a:BIGINT", "b:VARCHAR", "c:DOUBLE"),
):
    report = build_report(_snap(base), _snap(cur))
    return {t.name: t.handler for t in build_tools(report, sampler)}


def test_tool_metadata_present():
    report = build_report(_snap(["a:INTEGER"]), _snap(["a:INTEGER"]))
    for t in build_tools(report):
        assert t.name and t.description
    desc = {t.name: t.description for t in build_tools(report)}
    assert "20" in desc["sample_column_values"]


def test_outputs_are_json_serializable():
    tools = _tools(lambda c, n: [1, None, object()])
    for out in (
        tools["get_snapshot"](),
        tools["get_snapshot"]("baseline"),
        tools["get_diff"](),
        tools["sample_column_values"]("a"),
        tools["validate_ddl"](),
    ):
        json.dumps(out)


def test_get_snapshot_empty_and_case_sensitive():
    tools = _tools()
    assert "error" in tools["get_snapshot"]("")
    assert "error" in tools["get_snapshot"]("Current")


def test_get_diff_no_changes():
    out = _tools(base=("a:INTEGER",), cur=("a:INTEGER",))["get_diff"]()
    assert out["verdict"] == "none"
    assert out["changes"] == []


def test_tools_do_not_mutate_report():
    report = build_report(_snap(["a:INTEGER"]), _snap(["a:BIGINT", "c:DOUBLE"]))
    tools = {t.name: t.handler for t in build_tools(report, lambda c, n: [1])}
    before = report.model_dump_json()
    for _ in range(2):
        tools["get_snapshot"]()
        tools["get_diff"]()
        tools["sample_column_values"]("a")
        tools["validate_ddl"]("duckdb")
    assert report.model_dump_json() == before


def test_get_diff_idempotent():
    tools = _tools()
    assert tools["get_diff"]() == tools["get_diff"]()


def test_sampler_receives_capped_limit():
    seen = []

    def sampler(col, n):
        seen.append((col, n))
        return []

    t = _tools(sampler)["sample_column_values"]
    t("a", 10_000)
    t("a", 0)
    t("a", -5)
    t("a")
    assert seen == [("a", 20), ("a", 0), ("a", 0), ("a", 20)]


def test_sample_limit_boundaries():
    t = _tools(lambda c, n: range(1000))["sample_column_values"]
    assert len(t("a", 20)["values"]) == 20
    assert len(t("a", 21)["values"]) == 20
    assert len(t("a", 19)["values"]) == 19
    assert len(t("a", 1)["values"]) == 1
    assert t("a", 0)["values"] == []


def test_sample_limit_string_and_invalid():
    t = _tools(lambda c, n: range(1000))["sample_column_values"]
    assert len(t("a", "5")["values"]) == 5
    assert "error" in t("a", "abc")
    assert "error" in t("a", None)


def test_sample_sampler_returning_too_many_is_clamped():
    # sampler ignores the requested limit
    t = _tools(lambda c, n: list(range(500)))["sample_column_values"]
    assert len(t("a", 3)["values"]) == 3


def test_sample_fewer_than_limit_and_empty():
    assert _tools(lambda c, n: [1, 2])["sample_column_values"]("a")["values"] == ["1", "2"]
    assert _tools(lambda c, n: [])["sample_column_values"]("a")["values"] == []


def test_sample_values_stringified_and_none():
    out = _tools(lambda c, n: [1, None, 2.5, True])["sample_column_values"]("a")
    assert out["values"] == ["1", "None", "2.5", "True"]
    assert out["column"] == "a"


def test_sample_truncation_boundary():
    out = _tools(lambda c, n: ["x" * 200, "y" * 201, ""])["sample_column_values"]("b")
    assert [len(v) for v in out["values"]] == [200, 200, 0]


def test_sample_sampler_exception_becomes_error():
    def boom(c, n):
        raise RuntimeError("db down")

    out = _tools(boom)["sample_column_values"]("a")
    assert "error" in out and "db down" in out["error"]


def test_sample_unknown_column_does_not_call_sampler():
    called = []
    t = _tools(lambda c, n: called.append(c) or [])["sample_column_values"]
    assert "error" in t("nope")
    assert "error" in t("")
    assert "error" in t("A")  # case-sensitive
    assert called == []


def test_sample_column_only_in_baseline_rejected():
    # column dropped in current is not sampleable
    t = _tools(lambda c, n: [1], base=("a:INTEGER", "gone:INTEGER"), cur=("a:INTEGER",))
    assert "error" in t["sample_column_values"]("gone")


def test_validate_ddl_no_changes():
    out = _tools(base=("a:INTEGER",), cur=("a:INTEGER",))["validate_ddl"]("duckdb")
    assert out["valid"] is True
    assert out["errors"] == []


def test_validate_ddl_all_dialects_and_bad_input():
    t = _tools()["validate_ddl"]
    for d in ("duckdb", "postgres", "snowflake", "tsql"):
        out = t(d)
        if "error" in out:
            continue
        assert isinstance(out["statements"], list) and isinstance(out["errors"], list)
    assert "error" in t("")
    assert "error" in t("DuckDB")
    assert "error" in t(None)


def test_validate_ddl_custom_table_used():
    out = _tools()["validate_ddl"]("duckdb", "my_table")
    assert any("my_table" in s for s in out["statements"])


def test_validate_ddl_deterministic():
    t = _tools()["validate_ddl"]
    assert t("duckdb") == t("duckdb")
