from __future__ import annotations

import io
import json

import duckdb
import pytest

from schemasentinel.adapters.llm.fake import FakeLLM
from schemasentinel.application.run_log import CountingLLM, RunLog, safe_source
from schemasentinel.cli import main
from schemasentinel.domain.models import SummaryText


def _parquet(path, select):
    con = duckdb.connect()
    con.execute(f"COPY ({select}) TO '{path.as_posix()}' (FORMAT PARQUET)")
    con.close()
    return str(path)


def _json_lines(err: str) -> list[dict]:
    return [json.loads(ln) for ln in err.splitlines() if ln.startswith("{")]


@pytest.mark.parametrize(
    "ref, expected",
    [
        ("", ""),
        ("plain.parquet", "plain.parquet"),
        (r"C:\data\file.parquet", r"C:\data\file.parquet"),
        ("s3://bucket/key.parquet", "s3://bucket/key.parquet"),
        ("s3://AK:SECRET@bucket/key?X-Amz-Signature=zzz", "s3://bucket/key"),
        ("postgres://u:p%40ss@db:5432/app?password=x#frag", "postgres://db:5432/app"),
        ("https://user:pa@ss@host/p", "https://host/p"),
        ("https://tok@host/p", "https://host/p"),
    ],
)
def test_safe_source_cases(ref, expected):
    out = safe_source(ref)
    assert out == expected


def test_safe_source_never_leaks_secrets():
    out = safe_source("https://user:pa@ss@host/p?token=abc#sec")
    for leak in ("user", "pa@ss", "ss@", "token", "abc", "sec"):
        assert leak not in out.replace("host", "")


def test_emit_is_single_line_valid_json_with_required_keys():
    buf = io.StringIO()
    RunLog(buf, run_id="r").emit(command="diff", sources=["a", "b"], outcome="none")
    text = buf.getvalue()
    assert text.endswith("\n") and text.count("\n") == 1
    rec = json.loads(text)
    assert {
        "run_id", "command", "source", "changes", "llm_calls", "latency_ms", "outcome"
    } <= rec.keys()
    assert rec["changes"] == {"total": 0}
    assert rec["llm_calls"] == 0 and rec["run_id"] == "r"


def test_run_ids_are_unique_and_explicit_id_respected():
    ids = {RunLog(io.StringIO()).run_id for _ in range(50)}
    assert len(ids) == 50
    assert all(ids)


def test_source_with_newline_stays_one_line():
    buf = io.StringIO()
    RunLog(buf).emit(command="diff", sources=["a\nb"], outcome="ok")
    assert buf.getvalue().count("\n") == 1
    assert json.loads(buf.getvalue())["source"] == ["a\nb"]


def test_counting_llm_counts_success_and_passes_result():
    llm = CountingLLM(FakeLLM([SummaryText(summary="s"), SummaryText(summary="t")]))
    assert llm.complete_structured(system="", prompt="", schema=SummaryText).summary == "s"
    assert llm.complete_structured(system="", prompt="", schema=SummaryText).summary == "t"
    assert (llm.calls, llm.failures) == (2, 0)


def test_counting_llm_reraises_original_exception_and_counts_it():
    err = ValueError("boom")
    llm = CountingLLM(FakeLLM([err]))
    with pytest.raises(ValueError) as ei:
        llm.complete_structured(system="", prompt="", schema=SummaryText)
    assert ei.value is err
    assert (llm.calls, llm.failures) == (1, 1)


def test_counting_llm_does_not_expose_prompts():
    llm = CountingLLM(FakeLLM([SummaryText(summary="hush")]))
    llm.complete_structured(system="SYS-SECRET", prompt="PROMPT-SECRET", schema=SummaryText)
    buf = io.StringIO()
    RunLog(buf).emit(command="diff", sources=["a", "b"], outcome="ok", llm=llm)
    assert "SECRET" not in buf.getvalue() and "hush" not in buf.getvalue()


def test_cli_snapshot_is_logged(tmp_path, capsys):
    p = _parquet(tmp_path / "s.parquet", "SELECT 1 AS id, 'sampled-val' AS name")
    assert main(["snapshot", p]) == 0
    cap = capsys.readouterr()
    (rec,) = _json_lines(cap.err)
    assert rec["command"] == "snapshot" and rec["outcome"] == "ok"
    assert rec["source"] == [p] and rec["llm_calls"] == 0
    assert "sampled-val" not in cap.err


def test_cli_no_drift_logs_none_outcome_and_zero_changes(tmp_path, capsys):
    a = _parquet(tmp_path / "a.parquet", "SELECT 1 AS id")
    b = _parquet(tmp_path / "b.parquet", "SELECT 2 AS id")
    assert main(["diff", a, b]) == 0
    (rec,) = _json_lines(capsys.readouterr().err)
    assert rec["outcome"] == "none"
    assert rec["changes"] == {"total": 0}
    assert rec["llm_calls"] == 0


def test_cli_non_breaking_outcome(tmp_path, capsys):
    a = _parquet(tmp_path / "a.parquet", "SELECT 1 AS id")
    b = _parquet(tmp_path / "b.parquet", "SELECT 1 AS id, 2 AS extra")
    assert main(["diff", a, b]) == 0
    (rec,) = _json_lines(capsys.readouterr().err)
    assert rec["outcome"] == "non_breaking"
    assert rec["changes"]["total"] == sum(v for k, v in rec["changes"].items() if k != "total")


def test_cli_two_runs_have_different_run_ids(tmp_path, capsys):
    a = _parquet(tmp_path / "a.parquet", "SELECT 1 AS id")
    main(["diff", a, a])
    main(["diff", a, a])
    recs = _json_lines(capsys.readouterr().err)
    assert len(recs) == 2 and recs[0]["run_id"] != recs[1]["run_id"]


def test_cli_llm_failure_does_not_fail_run_and_is_counted(tmp_path, capsys):
    a = _parquet(tmp_path / "a.parquet", "SELECT 1 AS id, 'v' AS name")
    b = _parquet(tmp_path / "b.parquet", "SELECT 1 AS id")
    assert main(["diff", a, b], llm=FakeLLM([])) == 1
    (rec,) = _json_lines(capsys.readouterr().err)
    assert rec["outcome"] == "breaking"
    assert rec["llm_calls"] >= 1 and rec["llm_failures"] >= 1


def test_cli_error_log_has_no_change_counts_and_stdout_is_clean(tmp_path, capsys):
    assert main(["diff", str(tmp_path / "x"), str(tmp_path / "y")]) == 2
    cap = capsys.readouterr()
    (rec,) = _json_lines(cap.err)
    assert rec["changes"] == {"total": 0}
    assert not any(ln.startswith("{") and "run_id" in ln for ln in cap.out.splitlines())


def test_cli_bad_arguments_return_without_log(capsys):
    assert main(["diff"]) != 0
    assert _json_lines(capsys.readouterr().err) == []
