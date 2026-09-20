from pathlib import Path

import yaml

CI = Path(__file__).resolve().parents[2] / ".github" / "workflows" / "ci.yml"


def _load() -> dict:
    return yaml.safe_load(CI.read_text(encoding="utf-8"))


def test_ci_is_valid_yaml_and_triggers_on_push_to_dev():
    doc = _load()
    triggers = doc.get("on", doc.get(True))  # PyYAML parses bare `on` as True
    assert triggers["push"]["branches"] == ["dev"]


def test_ci_runs_ruff_pytest_and_evals():
    doc = _load()
    runs = " ".join(s.get("run", "") for j in doc["jobs"].values() for s in j["steps"])
    assert "ruff check" in runs
    assert "pytest" in runs
    assert "schemasentinel.evals" in runs
    assert "--record" not in runs
