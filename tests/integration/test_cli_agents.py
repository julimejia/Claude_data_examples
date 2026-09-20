from __future__ import annotations

import json
from pathlib import Path

import duckdb

from schemasentinel.adapters.llm.fake import FakeLLM
from schemasentinel.cli import main
from schemasentinel.domain.models import ImpactText, SummaryText
from schemasentinel.ports.llm import LLMError
from schemasentinel.ports.notifier import Notification


def make_parquet(path: Path, select: str) -> str:
    con = duckdb.connect()
    con.execute(f"COPY ({select}) TO '{path.as_posix()}' (FORMAT PARQUET)")
    con.close()
    return str(path)


class Recorder:
    def __init__(self) -> None:
        self.sent: list[Notification] = []

    def send(self, message: Notification) -> None:
        self.sent.append(message)


class Broken:
    def send(self, message: Notification) -> None:
        raise RuntimeError("boom")


def pair(tmp_path):
    base = make_parquet(tmp_path / "a.parquet", "SELECT 1 AS id, 'x' AS name")
    cur = make_parquet(tmp_path / "b.parquet", "SELECT 1 AS id")  # name dropped: breaking
    return base, cur


def test_default_is_deterministic_only(tmp_path, capsys):
    base, cur = pair(tmp_path)
    assert main(["diff", base, cur]) == 1
    data = json.loads(capsys.readouterr().out)
    assert "explanation" not in data and "migration" not in data


def test_llm_adds_explanation_and_ddl_json(tmp_path, capsys):
    base, cur = pair(tmp_path)
    llm = FakeLLM([ImpactText(impact="Readers of name fail."), SummaryText(summary="One break.")])
    assert main(["diff", base, cur, "--dialect", "tsql"], llm=llm) == 1
    data = json.loads(capsys.readouterr().out)
    assert data["explanation"]["summary"] == "One break."
    assert data["explanation"]["impacts"][0]["impact"] == "Readers of name fail."
    assert data["migration"]["plan"]["dialect"] == "tsql"
    assert "DROP COLUMN [name]" in data["migration"]["plan"]["statements"][0]["sql"]


def test_markdown_has_explanation_and_ddl(tmp_path, capsys):
    base, cur = pair(tmp_path)
    llm = FakeLLM([ImpactText(impact="Readers fail."), SummaryText(summary="One break.")])
    main(["diff", base, cur, "--format", "md", "--dialect", "spark"], llm=llm)
    out = capsys.readouterr().out
    assert "## Explanation" in out and "One break." in out
    assert "## Proposed migration (spark)" in out and "DROP COLUMN `name`" in out


def test_llm_failure_never_fails_the_run(tmp_path, capsys):
    base, cur = pair(tmp_path)
    llm = FakeLLM([LLMError("down")] * 5)
    assert main(["diff", base, cur, "--format", "md"], llm=llm) == 1
    assert "deterministic fallback" in capsys.readouterr().out


def test_replay_without_recordings_still_succeeds(tmp_path, capsys):
    base, cur = pair(tmp_path)
    code = main(["diff", base, cur, "--llm", "replay", "--replay-dir", str(tmp_path / "none")])
    assert code == 1
    assert json.loads(capsys.readouterr().out)["explanation"]["degraded"] is True


def test_notify_sends_summary(tmp_path, capsys):
    base, cur = pair(tmp_path)
    rec = Recorder()
    out = tmp_path / "r.json"
    main(["diff", base, cur, "--notify", "console", "-o", str(out)], notifier=rec)
    assert len(rec.sent) == 1
    assert rec.sent[0].verdict.value == "breaking"
    assert rec.sent[0].report_location == str(out)


def test_console_notify_prints(tmp_path, capsys):
    base, cur = pair(tmp_path)
    main(["diff", base, cur, "--notify", "console"])
    assert "SchemaSentinel verdict: breaking" in capsys.readouterr().err


def test_notifier_failure_does_not_change_exit_code(tmp_path, capsys):
    base, cur = pair(tmp_path)
    assert main(["diff", base, cur], notifier=Broken()) == 1
    assert "notification not sent" in capsys.readouterr().err
