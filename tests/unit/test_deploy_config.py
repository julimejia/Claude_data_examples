import importlib.util
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]


def _load():
    spec = importlib.util.spec_from_file_location(
        "check_demo_size", ROOT / "scripts" / "check_demo_size.py"
    )
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def test_vercel_json():
    cfg = json.loads((ROOT / "vercel.json").read_text(encoding="utf-8"))
    assert cfg["outputDirectory"] == "public"
    assert "src" in cfg["functions"]["api/diff.py"]["includeFiles"]
    assert any(r.get("dest") == "/api/diff.py" for r in cfg["routes"])


def test_requirements_only_pydantic():
    lines = (ROOT / "requirements.txt").read_text(encoding="utf-8").split()
    assert len(lines) == 1 and lines[0].startswith("pydantic")


def test_size_check_passes_and_fails():
    mod = _load()
    assert mod.main([]) == 0
    assert mod.main(["--limit-mb", "0.0001"]) == 1


def test_docs_and_readme():
    assert "Framework Preset: Other" in (ROOT / "docs" / "deploy-vercel.md").read_text(
        encoding="utf-8"
    )
    assert "docs/deploy-vercel.md" in (ROOT / "README.md").read_text(encoding="utf-8")
