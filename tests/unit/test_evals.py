from __future__ import annotations

import json
from pathlib import Path

import pytest

from schemasentinel.evals.golden import GoldenCase, load_cases, parse_columns
from schemasentinel.evals.metrics import check_thresholds, evaluate
from schemasentinel.evals.runner import main

ROOT = Path(__file__).resolve().parents[2]
GOLDEN_DIR = ROOT / "tests" / "golden" / "cases"
THRESHOLDS = ROOT / "evals" / "thresholds.json"


def case(case_id, baseline, current, expected, verdict, fmt="parquet"):
    return GoldenCase.model_validate(
        {
            "id": case_id,
            "description": "",
            "format": fmt,
            "baseline": baseline,
            "current": current,
            "expected": {"verdict": verdict, "changes": expected},
        }
    )


REMOVE = {
    "change_type": "column_removed",
    "path": "b",
    "severity": "breaking",
    "rule_id": "REMOVE",
}
ADD = {
    "change_type": "column_added",
    "path": "b",
    "severity": "non_breaking",
    "rule_id": "ADD-NULLABLE",
}


def test_parse_columns_shorthand():
    cols = parse_columns(["id:BIGINT!", "note:VARCHAR", "n:INTEGER!=0"])
    assert [c.name for c in cols] == ["id", "note", "n"]
    assert [c.position for c in cols] == [0, 1, 2]
    assert [c.nullable for c in cols] == [False, True, False]
    assert cols[2].default == "0"


def test_parse_columns_nested_objects():
    (col,) = parse_columns(
        [{"name": "s", "type": "STRUCT(a INTEGER)", "children": ["a:INTEGER"]}]
    )
    assert col.children[0].name == "a"


def test_perfect_run_has_full_scores():
    cases = [
        case("rm", ["a:INTEGER", "b:INTEGER"], ["a:INTEGER"], [REMOVE], "breaking"),
        case("add", ["a:INTEGER"], ["a:INTEGER", "b:INTEGER"], [ADD], "non_breaking"),
    ]
    result = evaluate(cases)
    assert result.metrics["classification_accuracy"] == 1.0
    assert result.metrics["breaking_recall"] == 1.0
    assert result.failures == []


def test_missed_breaking_change_lowers_recall_and_is_reported():
    wrong = dict(REMOVE, severity="non_breaking")  # expectation the engine will not meet
    cases = [case("rm", ["a:INTEGER", "b:INTEGER"], ["a:INTEGER"], [wrong], "breaking")]
    result = evaluate(cases)
    assert result.metrics["classification_accuracy"] == 0.0
    assert [f.case_id for f in result.failures] == ["rm"]


def test_recall_counts_expected_breaking_changes_only():
    # Expected says breaking but engine says non-breaking: a missed breaking change.
    exp = dict(ADD, severity="breaking", rule_id="ADD-NOT-NULL")
    cases = [case("miss", ["a:INTEGER"], ["a:INTEGER", "b:INTEGER"], [exp], "breaking")]
    assert evaluate(cases).metrics["breaking_recall"] == 0.0


def test_unimplemented_metrics_are_none():
    metrics = evaluate([case("n", ["a:INTEGER"], ["a:INTEGER"], [], "none")]).metrics
    assert metrics["rename_resolution_accuracy"] is None
    assert metrics["ddl_validity_rate"] is None
    assert metrics["invalid_output_rate"] is None


def test_check_thresholds_min_max_and_skips_none():
    metrics = {"a": 0.9, "b": None, "c": 0.2}
    thresholds = {"min": {"a": 1.0, "b": 1.0}, "max": {"c": 0.1}}
    violations = check_thresholds(metrics, thresholds, case_count=40)
    assert len(violations) == 2
    assert any("a" in v for v in violations) and any("c" in v for v in violations)


def test_check_thresholds_min_cases():
    assert check_thresholds({}, {"min_cases": 30}, case_count=5)
    assert not check_thresholds({}, {"min_cases": 30}, case_count=30)


def test_real_golden_set_meets_thresholds():
    cases = load_cases(GOLDEN_DIR)
    assert len(cases) >= 30
    thresholds = json.loads(THRESHOLDS.read_text(encoding="utf-8"))
    result = evaluate(cases)
    assert result.failures == []
    assert check_thresholds(result.metrics, thresholds, case_count=len(cases)) == []
    assert result.metrics["breaking_recall"] == 1.0


def test_golden_case_ids_are_unique():
    ids = [c.id for c in load_cases(GOLDEN_DIR)]
    assert len(ids) == len(set(ids))


def test_main_passes_and_prints_table(capsys):
    assert main([]) == 0
    out = capsys.readouterr().out
    assert "breaking_recall" in out and "classification_accuracy" in out


def test_main_fails_on_regression(tmp_path, capsys):
    thresholds = tmp_path / "t.json"
    thresholds.write_text(json.dumps({"min": {"classification_accuracy": 1.0}}))
    bad = tmp_path / "cases"
    bad.mkdir()
    wrong = dict(REMOVE, severity="non_breaking")
    (bad / "x.json").write_text(
        json.dumps(
            {
                "id": "x",
                "format": "parquet",
                "baseline": ["a:INTEGER", "b:INTEGER"],
                "current": ["a:INTEGER"],
                "expected": {"verdict": "non_breaking", "changes": [wrong]},
            }
        )
    )
    code = main(["--golden-dir", str(bad), "--thresholds", str(thresholds)])
    assert code == 1
    assert "FAIL" in capsys.readouterr().out


def test_main_errors_on_missing_dir(tmp_path):
    assert main(["--golden-dir", str(tmp_path / "nope")]) == 2


@pytest.mark.parametrize("bad", ["", "nocolon"])
def test_parse_columns_rejects_bad_shorthand(bad):
    with pytest.raises(ValueError):
        parse_columns([bad])
