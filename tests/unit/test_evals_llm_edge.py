from __future__ import annotations

import json

from schemasentinel.adapters.llm.fake import FakeLLM
from schemasentinel.adapters.llm.replay import ReplayAdapter
from schemasentinel.evals.golden import GoldenCase
from schemasentinel.evals.metrics import LLM_METRICS, check_thresholds, evaluate, record_replay
from schemasentinel.evals.runner import main
from schemasentinel.ports.llm import LLMError


def _case(cid, baseline, current, changes, verdict, resolution=None):
    return GoldenCase.model_validate(
        {
            "id": cid,
            "baseline": baseline,
            "current": current,
            "expected": {"verdict": verdict, "changes": changes},
            "expected_resolution": resolution,
        }
    )


def _rename(resolution="rename"):
    return _case(
        "rn",
        ["id:BIGINT!", "customer_name:VARCHAR"],
        ["id:BIGINT!", "customer_nm:VARCHAR"],
        [
            {
                "change_type": "rename_candidate",
                "path": "customer_name",
                "severity": "needs_review",
                "rule_id": "RENAME-CANDIDATE",
            }
        ],
        "non_breaking",
        resolution,
    )


def _identical():
    return _case("same", ["id:BIGINT!"], ["id:BIGINT!"], [], "non_breaking")


def test_no_llm_gives_none_metrics():
    m = evaluate([_identical()]).metrics
    for name in LLM_METRICS:
        assert m[name] is None


def test_empty_cases_with_llm():
    m = evaluate([], FakeLLM([])).metrics
    assert m["invalid_output_rate"] == 0.0
    assert m["rename_resolution_accuracy"] is None
    assert m["ddl_validity_rate"] is None


def test_case_without_expected_resolution_not_scored(tmp_path):
    m = evaluate([_identical()], ReplayAdapter(tmp_path)).metrics
    assert m["rename_resolution_accuracy"] is None
    assert m["invalid_output_rate"] == 0.0


def test_missing_transcript_counts_invalid(tmp_path):
    m = evaluate([_rename()], ReplayAdapter(tmp_path)).metrics
    assert m["invalid_output_rate"] == 1.0
    assert m["rename_resolution_accuracy"] == 0.0


def test_invalid_rate_is_fraction_of_calls():
    m = evaluate([_rename(), _identical()], FakeLLM([LLMError("bad")])).metrics
    assert 0.0 < m["invalid_output_rate"] <= 1.0


def test_record_replay_case_without_resolution_does_not_crash(tmp_path):
    record_replay([_rename(None)], tmp_path)
    m = evaluate([_rename(None)], ReplayAdapter(tmp_path)).metrics
    assert m["rename_resolution_accuracy"] is None


def test_record_replay_idempotent(tmp_path):
    cases = [_rename()]
    record_replay(cases, tmp_path)
    before = {p.name: p.read_text(encoding="utf-8") for p in tmp_path.glob("*.json")}
    record_replay(cases, tmp_path)
    after = {p.name: p.read_text(encoding="utf-8") for p in tmp_path.glob("*.json")}
    assert before == after


def test_thresholds_boundary_is_inclusive():
    th = {"min": {"a": 0.9}, "max": {"b": 0.1}}
    assert check_thresholds({"a": 0.9, "b": 0.1}, th, case_count=0) == []


def test_thresholds_violations_reported():
    th = {"min_cases": 5, "min": {"a": 0.9}, "max": {"b": 0.0}}
    v = check_thresholds({"a": 0.5, "b": 0.2}, th, case_count=1)
    assert len(v) == 3


def test_thresholds_none_and_missing_metrics_skipped():
    th = {"min": {"a": 1.0, "zz": 1.0}, "max": {"b": 0.0}}
    assert check_thresholds({"a": None, "b": None}, th, case_count=0) == []


def test_main_missing_thresholds_exit_2(tmp_path):
    assert main(["--thresholds", str(tmp_path / "nope.json")]) == 2


def test_main_malformed_thresholds_exit_2(tmp_path):
    bad = tmp_path / "t.json"
    bad.write_text("{not json", encoding="utf-8")
    assert main(["--thresholds", str(bad)]) == 2


def test_main_without_replay_dir_shows_na(tmp_path, capsys):
    rc = main(["--replay-dir", str(tmp_path / "absent")])
    out = capsys.readouterr().out
    assert rc == 0 and "n/a" in out


def test_main_regression_exits_1(tmp_path, capsys):
    replay = tmp_path / "r"
    main(["--record", "--replay-dir", str(replay)])
    th = tmp_path / "t.json"
    th.write_text(json.dumps({"min": {"ddl_validity_rate": 2}}), encoding="utf-8")
    capsys.readouterr()
    assert main(["--replay-dir", str(replay), "--thresholds", str(th)]) == 1
    assert "RESULT: FAIL" in capsys.readouterr().out


def test_main_empty_replay_dir_fails_thresholds(tmp_path):
    empty = tmp_path / "e"
    empty.mkdir()
    assert main(["--replay-dir", str(empty)]) == 1
