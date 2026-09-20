from __future__ import annotations

import json
from pathlib import Path

from schemasentinel.adapters.llm.fake import FakeLLM
from schemasentinel.adapters.llm.replay import ReplayAdapter
from schemasentinel.evals.golden import GoldenCase, load_cases
from schemasentinel.evals.metrics import evaluate, record_replay
from schemasentinel.evals.runner import main
from schemasentinel.ports.llm import LLMError

ROOT = Path(__file__).resolve().parents[2]
GOLDEN_DIR = ROOT / "tests" / "golden" / "cases"


def _rename_case(resolution: str = "rename") -> GoldenCase:
    return GoldenCase.model_validate(
        {
            "id": "rn",
            "baseline": ["id:BIGINT!", "customer_name:VARCHAR"],
            "current": ["id:BIGINT!", "customer_nm:VARCHAR"],
            "expected": {
                "verdict": "non_breaking",
                "changes": [
                    {
                        "change_type": "rename_candidate",
                        "path": "customer_name",
                        "severity": "needs_review",
                        "rule_id": "RENAME-CANDIDATE",
                    }
                ],
            },
            "expected_resolution": resolution,
        }
    )


def test_record_then_replay_scores_llm_metrics(tmp_path):
    cases = [_rename_case()]
    record_replay(cases, tmp_path)
    assert list(tmp_path.glob("*.json"))
    metrics = evaluate(cases, ReplayAdapter(tmp_path)).metrics
    assert metrics["rename_resolution_accuracy"] == 1.0
    assert metrics["ddl_validity_rate"] == 1.0
    assert metrics["invalid_output_rate"] == 0.0


def test_wrong_resolution_lowers_accuracy(tmp_path):
    record_replay([_rename_case("rename")], tmp_path)
    metrics = evaluate([_rename_case("drop_and_add")], ReplayAdapter(tmp_path)).metrics
    assert metrics["rename_resolution_accuracy"] == 0.0


def test_llm_errors_count_as_invalid_output():
    llm = FakeLLM([LLMError("bad json")])
    metrics = evaluate([_rename_case()], llm).metrics
    assert metrics["invalid_output_rate"] == 1.0
    assert metrics["rename_resolution_accuracy"] == 0.0


def test_replay_run_is_reproducible(tmp_path):
    cases = load_cases(GOLDEN_DIR)
    record_replay(cases, tmp_path)
    first = evaluate(cases, ReplayAdapter(tmp_path)).metrics
    second = evaluate(cases, ReplayAdapter(tmp_path)).metrics
    assert first == second
    assert first["rename_resolution_accuracy"] == 1.0
    assert first["invalid_output_rate"] == 0.0


def test_thresholds_cover_llm_metrics():
    thresholds = json.loads((ROOT / "evals" / "thresholds.json").read_text(encoding="utf-8"))
    assert "rename_resolution_accuracy" in thresholds["min"]
    assert "ddl_validity_rate" in thresholds["min"]
    assert "invalid_output_rate" in thresholds["max"]


def test_main_with_replay_dir(tmp_path, capsys):
    replay = tmp_path / "replay"
    assert main(["--record", "--replay-dir", str(replay)]) == 0
    assert main(["--replay-dir", str(replay)]) == 0
    out = capsys.readouterr().out
    assert "rename_resolution_accuracy" in out and "RESULT: PASS" in out
