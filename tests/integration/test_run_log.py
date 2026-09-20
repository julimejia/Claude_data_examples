from __future__ import annotations

import io
import json

import duckdb

from schemasentinel.adapters.llm.fake import FakeLLM
from schemasentinel.application.run_log import CountingLLM, RunLog, safe_source
from schemasentinel.cli import main
from schemasentinel.domain.models import ImpactText, SummaryText


def _parquet(path, select):
    con = duckdb.connect()
    con.execute(f"COPY ({select}) TO '{path.as_posix()}' (FORMAT PARQUET)")
    con.close()
    return str(path)


def _log_lines(err: str) -> list[dict]:
    out = []
    for line in err.splitlines():
        if line.startswith("{"):
            out.append(json.loads(line))
    return out


def test_cli_diff_emits_one_json_line(tmp_path, capsys):
    base = _parquet(tmp_path / "a.parquet", "SELECT 1 AS id, 'secret-value' AS name")
    cur = _parquet(tmp_path / "b.parquet", "SELECT 1 AS id")
    llm = FakeLLM({ImpactText: ImpactText(impact="x"), SummaryText: SummaryText(summary="y")})
    assert main(["diff", base, cur], llm=llm) == 1
    err = capsys.readouterr().err
    (rec,) = _log_lines(err)
    assert rec["run_id"] and rec["command"] == "diff"
    assert rec["source"] == [base, cur]
    assert rec["changes"]["total"] == 1 and rec["changes"]["breaking"] == 1
    assert rec["llm_calls"] >= 1
    assert rec["latency_ms"] >= 0
    assert rec["outcome"] == "breaking"
    assert "secret-value" not in err


def test_error_run_is_logged(tmp_path, capsys):
    assert main(["diff", str(tmp_path / "no.parquet"), str(tmp_path / "no2.parquet")]) == 2
    (rec,) = _log_lines(capsys.readouterr().err)
    assert rec["outcome"] == "error" and rec["llm_calls"] == 0


def test_runlog_counts_calls_and_strips_credentials():
    class Boom:
        def complete_structured(self, **kw):
            raise RuntimeError("nope")

    counting = CountingLLM(Boom())
    try:
        counting.complete_structured(system="s", prompt="p", schema=SummaryText)
    except RuntimeError:
        pass
    buf = io.StringIO()
    rec = RunLog(buf, run_id="r1").emit(
        command="diff",
        sources=["https://user:tok3n@host.example/data?sig=abc"],
        outcome="ok",
        llm=counting,
    )
    assert rec["source"] == ["https://host.example/data"]
    assert rec["llm_calls"] == 1 and rec["llm_failures"] == 1
    assert "tok3n" not in buf.getvalue() and "sig=abc" not in buf.getvalue()
    assert safe_source("local/file.csv") == "local/file.csv"
