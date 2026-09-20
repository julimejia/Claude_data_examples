from __future__ import annotations

import json
import re
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
PUBLIC = ROOT / "public"
sys.path.insert(0, str(ROOT / "api"))

import diff as api  # noqa: E402

from schemasentinel.evals.golden import load_cases  # noqa: E402

EXAMPLES = json.loads((PUBLIC / "examples.json").read_text(encoding="utf-8"))["examples"]
CASES = {c.id: c for c in load_cases(ROOT / "tests" / "golden" / "cases")}


def test_static_files_exist_and_are_self_contained():
    for name in ("index.html", "style.css", "app.js"):
        text = (PUBLIC / name).read_text(encoding="utf-8")
        assert not re.search(r"https?://", text), f"{name} references an external URL"
    html = (PUBLIC / "index.html").read_text(encoding="utf-8")
    assert 'href="style.css"' in html and 'src="app.js"' in html
    assert 'name="viewport"' in html
    assert "prefers-color-scheme: dark" in (PUBLIC / "style.css").read_text(encoding="utf-8")
    assert "examples.json" in (PUBLIC / "app.js").read_text(encoding="utf-8")


def test_every_golden_case_is_an_example_and_vice_versa():
    assert {e["id"] for e in EXAMPLES} == set(CASES)


def test_stored_results_match_deterministic_classification():
    for ex in EXAMPLES:
        case = CASES[ex["id"]]
        status, fresh = api.handle(
            json.dumps({"baseline": ex["baseline"], "current": ex["current"]}).encode()
        )
        assert status == 200
        assert ex["result"]["verdict"] == fresh["verdict"] == case.expected.verdict.value
        stored = sorted(
            (c["change_type"], c["path"], c["severity"], c["rule_id"])
            for c in ex["result"]["changes"]
        )
        assert stored == sorted(c.key() for c in case.expected.changes)
        assert ex["result"]["changes"] == fresh["changes"]


def test_stored_ai_output_is_labelled_honestly():
    recorded = any((ROOT / "tests" / "recordings").glob("*.json"))
    for ex in EXAMPLES:
        for part in ("explanation", "ddl"):
            assert ex[part]["label"] in {"sample", "recorded"}
            if not recorded:
                assert ex[part]["label"] == "sample"


def test_generator_output_is_up_to_date(tmp_path):
    """The committed artifact is what the generator produces (ADR-0004).

    The generator writes into tmp_path, never into the working tree, and the comparison is
    on parsed data so the check tracks content rather than JSON formatting.
    """
    committed_before = (PUBLIC / "examples.json").read_bytes()
    out = tmp_path / "examples.json"
    subprocess.run(
        [sys.executable, "scripts/gen_demo_examples.py", "--out", str(out)],
        cwd=ROOT,
        check=True,
    )
    regenerated = json.loads(out.read_text(encoding="utf-8"))["examples"]
    assert {e["id"]: e for e in regenerated} == {e["id"]: e for e in EXAMPLES}
    # canonical order, so regeneration is deterministic
    assert [e["id"] for e in EXAMPLES] == sorted(e["id"] for e in EXAMPLES)
    assert (PUBLIC / "examples.json").read_bytes() == committed_before
