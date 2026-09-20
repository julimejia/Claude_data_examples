import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
README = (ROOT / "README.md").read_text(encoding="utf-8")


def test_readme_has_required_sections():
    for heading in ("## Problem", "## Architecture", "## Quick start", "## Eval results"):
        assert heading in README


def test_readme_has_mermaid_diagram():
    assert "```mermaid" in README


def test_readme_eval_table_lists_every_gated_metric():
    thresholds = json.loads((ROOT / "evals" / "thresholds.json").read_text(encoding="utf-8"))
    for name in [*thresholds["min"], *thresholds["max"]]:
        assert name in README
