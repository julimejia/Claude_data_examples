from __future__ import annotations

import json
from pathlib import Path

import duckdb
import pytest

from schemasentinel.adapters.llm.fake import FakeLLM
from schemasentinel.cli import main
from schemasentinel.domain.models import ImpactText, SummaryText
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


def breaking_pair(tmp_path):
    base = make_parquet(tmp_path / "a.parquet", "SELECT 1 AS id, 'x' AS name")
    cur = make_parquet(tmp_path / "b.parquet", "SELECT 1 AS id")
    return base, cur


def same_pair(tmp_path):
    base = make_parquet(tmp_path / "a.parquet", "SELECT 1 AS id")
    cur = make_parquet(tmp_path / "b.parquet", "SELECT 1 AS id")
    return base, cur


def additive_pair(tmp_path):
    base = make_parquet(tmp_path / "a.parquet", "SELECT 1 AS id")
    cur = make_parquet(tmp_path / "b.parquet", "SELECT 1 AS id, 'x' AS extra")
    return base, cur


@pytest.mark.parametrize("flag,value", [("--dialect", "oracle"), ("--llm", "gpt"),
                                        ("--notify", "email"), ("--format", "xml")])
def test_invalid_choice_exits_2(tmp_path, capsys, flag, value):
    base, cur = breaking_pair(tmp_path)
    assert main(["diff", base, cur, flag, value]) == 2
    assert capsys.readouterr().out == ""


def test_dialect_alone_adds_migration_without_explanation(tmp_path, capsys):
    base, cur = breaking_pair(tmp_path)
    assert main(["diff", base, cur, "--dialect", "spark"]) == 1
    data = json.loads(capsys.readouterr().out)
    assert "explanation" not in data
    assert data["migration"]["plan"]["dialect"] == "spark"


def test_markdown_dialect_alone_has_no_explanation_section(tmp_path, capsys):
    base, cur = breaking_pair(tmp_path)
    main(["diff", base, cur, "--dialect", "duckdb", "--format", "md"])
    out = capsys.readouterr().out
    assert "## Explanation" not in out
    assert "## Proposed migration (duckdb)" in out


def test_llm_without_dialect_defaults_to_duckdb(tmp_path, capsys):
    base, cur = breaking_pair(tmp_path)
    llm = FakeLLM([ImpactText(impact="i"), SummaryText(summary="s")])
    main(["diff", base, cur], llm=llm)
    assert json.loads(capsys.readouterr().out)["migration"]["plan"]["dialect"] == "duckdb"


def test_table_name_used_in_ddl(tmp_path, capsys):
    base, cur = breaking_pair(tmp_path)
    main(["diff", base, cur, "--dialect", "duckdb", "--table", "orders"])
    sql = json.loads(capsys.readouterr().out)["migration"]["plan"]["statements"][0]["sql"]
    assert "orders" in sql


def test_no_drift_with_llm_exits_0_and_is_valid_json(tmp_path, capsys):
    base, cur = same_pair(tmp_path)
    llm = FakeLLM([SummaryText(summary="Nothing changed.")] * 3)
    assert main(["diff", base, cur, "--dialect", "tsql"], llm=llm) == 0
    data = json.loads(capsys.readouterr().out)
    assert data["verdict"] == "none"
    assert data["migration"]["plan"]["statements"] == []


def test_no_drift_markdown_says_no_ddl_needed(tmp_path, capsys):
    base, cur = same_pair(tmp_path)
    main(["diff", base, cur, "--dialect", "tsql", "--format", "md"])
    assert "No DDL statements needed." in capsys.readouterr().out


def test_non_breaking_with_llm_exits_0(tmp_path, capsys):
    base, cur = additive_pair(tmp_path)
    llm = FakeLLM([SummaryText(summary="Column added.")] * 3)
    assert main(["diff", base, cur, "--dialect", "spark"], llm=llm) == 0
    assert json.loads(capsys.readouterr().out)["verdict"] == "non_breaking"


def test_unexpected_exception_type_from_llm_does_not_fail_run(tmp_path, capsys):
    base, cur = breaking_pair(tmp_path)
    llm = FakeLLM([RuntimeError("weird")] * 5)
    assert main(["diff", base, cur], llm=llm) == 1
    captured = capsys.readouterr()
    json.loads(captured.out)  # stdout is still a clean report


def test_failing_llm_does_not_change_verdict(tmp_path, capsys):
    base, cur = breaking_pair(tmp_path)
    assert main(["diff", base, cur], llm=FakeLLM([])) == 1
    assert json.loads(capsys.readouterr().out)["verdict"] == "breaking"


def test_llm_failure_with_no_drift_exits_0(tmp_path, capsys):
    base, cur = same_pair(tmp_path)
    assert main(["diff", base, cur], llm=FakeLLM([RuntimeError("x")] * 5)) == 0


def test_notification_goes_to_stderr_not_stdout(tmp_path, capsys):
    base, cur = breaking_pair(tmp_path)
    main(["diff", base, cur, "--notify", "console"])
    captured = capsys.readouterr()
    json.loads(captured.out)  # pure JSON on stdout
    assert "SchemaSentinel verdict" in captured.err


def test_notify_sent_once_for_non_breaking_and_none(tmp_path):
    for maker, verdict in ((additive_pair, "non_breaking"), (same_pair, "none")):
        base, cur = maker(tmp_path)
        rec = Recorder()
        assert main(["diff", base, cur], notifier=rec) == 0
        assert [n.verdict.value for n in rec.sent] == [verdict]


def test_notify_report_location_none_without_output(tmp_path):
    base, cur = breaking_pair(tmp_path)
    rec = Recorder()
    main(["diff", base, cur], notifier=rec)
    assert rec.sent[0].report_location is None


def test_notify_after_llm_uses_resolved_report(tmp_path):
    base, cur = breaking_pair(tmp_path)
    rec = Recorder()
    llm = FakeLLM([ImpactText(impact="i"), SummaryText(summary="s")])
    assert main(["diff", base, cur], llm=llm, notifier=rec) == 1
    assert len(rec.sent) == 1


def test_telegram_without_env_warns_and_keeps_exit_code(tmp_path, capsys, monkeypatch):
    monkeypatch.delenv("TELEGRAM_BOT_TOKEN", raising=False)
    monkeypatch.delenv("TELEGRAM_CHAT_ID", raising=False)
    base, cur = breaking_pair(tmp_path)
    assert main(["diff", base, cur, "--notify", "telegram"]) == 1
    captured = capsys.readouterr()
    assert "notification not sent" in captured.err
    json.loads(captured.out)


def test_telegram_token_never_printed(tmp_path, capsys, monkeypatch):
    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "SECRET-TOKEN-123")
    monkeypatch.setenv("TELEGRAM_CHAT_ID", "42")
    import schemasentinel.adapters.notify.telegram as tg

    def boom(url, payload, timeout):
        raise OSError(f"cannot reach {url}")

    monkeypatch.setattr(tg, "_urllib_post", boom)
    base, cur = breaking_pair(tmp_path)
    assert main(["diff", base, cur, "--notify", "telegram"]) == 1
    captured = capsys.readouterr()
    assert "SECRET-TOKEN-123" not in captured.out + captured.err


def test_notifier_not_called_when_source_fails(tmp_path, capsys):
    rec = Recorder()
    code = main(["diff", str(tmp_path / "nope.parquet"), str(tmp_path / "nope2.parquet")],
                notifier=rec)
    assert code == 2
    assert rec.sent == []


def test_notifier_not_called_when_output_unwritable(tmp_path, capsys):
    base, cur = breaking_pair(tmp_path)
    rec = Recorder()
    bad = tmp_path / "missing_dir" / "r.json"
    assert main(["diff", base, cur, "-o", str(bad)], notifier=rec) == 2
    assert rec.sent == []


def test_output_file_written_with_agent_sections(tmp_path, capsys):
    base, cur = breaking_pair(tmp_path)
    out = tmp_path / "r.md"
    llm = FakeLLM([ImpactText(impact="i"), SummaryText(summary="Sum.")])
    assert main(["diff", base, cur, "--format", "md", "-o", str(out)], llm=llm) == 1
    text = out.read_text(encoding="utf-8")
    assert "## Explanation" in text and "## Proposed migration" in text
    assert capsys.readouterr().out == ""


def test_replay_missing_dir_markdown_flags_fallback(tmp_path, capsys):
    base, cur = breaking_pair(tmp_path)
    main(["diff", base, cur, "--llm", "replay", "--replay-dir", str(tmp_path / "nope"),
          "--format", "md"])
    assert "deterministic fallback" in capsys.readouterr().out


def test_snapshot_command_unaffected_by_agent_flags_absence(tmp_path, capsys):
    base, _ = breaking_pair(tmp_path)
    assert main(["snapshot", base]) == 0
    assert "id" in json.loads(capsys.readouterr().out)["columns"][0]["name"]


def test_deterministic_default_output_is_idempotent(tmp_path, capsys):
    base, cur = breaking_pair(tmp_path)
    main(["diff", base, cur])
    first = json.loads(capsys.readouterr().out)
    main(["diff", base, cur])
    second = json.loads(capsys.readouterr().out)
    assert first["changes"] == second["changes"]
