import json
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
README = (ROOT / "README.md").read_text(encoding="utf-8")


def _mermaid_blocks():
    return re.findall(r"```mermaid\n(.*?)```", README, re.DOTALL)


def test_mermaid_block_is_closed_and_declares_a_diagram_type():
    blocks = _mermaid_blocks()
    assert blocks
    assert re.match(r"\s*(flowchart|graph|sequenceDiagram|classDiagram)\b", blocks[0])
    assert README.count("```") % 2 == 0


def test_mermaid_diagram_names_the_main_components():
    diagram = "\n".join(_mermaid_blocks())
    for node in ("DetectDrift", "domain.rules", "Resolve", "ProposeMigration", "Explain"):
        assert node in diagram


def test_mermaid_edges_reference_defined_nodes_only():
    diagram = _mermaid_blocks()[0]
    defined = set(re.findall(r"\b([A-Z]{2,})(?:\[|\()", diagram))
    used = set(re.findall(r"\b([A-Z]{2,})\b(?=\s*(?:-->|\|))", diagram))
    used |= set(re.findall(r"(?:-->|\|)\s*([A-Z]{2,})\b", diagram))
    assert used - defined == set()


def test_headings_appear_in_order():
    headings = ("## Problem", "## Architecture", "## Quick start", "## Eval results")
    order = [README.index(h) for h in headings]
    assert order == sorted(order)


def test_quick_start_commands_use_real_cli_subcommands():
    assert "schemasentinel snapshot" in README
    assert "schemasentinel diff" in README
    assert "--format md" in README


def test_documented_exit_codes_present():
    assert "`0`" in README and "`1`" in README and "`2`" in README


def test_eval_table_gates_match_thresholds_json():
    thresholds = json.loads((ROOT / "evals" / "thresholds.json").read_text(encoding="utf-8"))
    for name, value in thresholds["min"].items():
        assert re.search(rf"\|\s*{name}\s*\|\s*>=\s*{value:.2f}", README), name
    for name, value in thresholds["max"].items():
        assert re.search(rf"\|\s*{name}\s*\|\s*<=\s*{value:.2f}", README), name


def test_documented_case_count_matches_golden_cases():
    actual = len(list((ROOT / "tests" / "golden" / "cases").glob("*.json")))
    m = re.search(r"(\d+) cases", README)
    assert m and int(m.group(1)) == actual


def test_paths_referenced_in_readme_exist():
    for rel in ("sdd/architecture.md", "tests/golden/cases", "evals/thresholds.json"):
        assert rel in README
        assert (ROOT / rel).exists()


def test_readme_includes_demo_report():
    # AC-57: README "includes a demo report"
    assert re.search(r"^##\s+.*(demo|example).*report", README, re.IGNORECASE | re.MULTILINE)


def test_demo_report_shows_a_breaking_change_verdict():
    m = re.search(r"^##\s+.*demo.*$", README, re.IGNORECASE | re.MULTILINE)
    assert m, "no demo section"
    section = README[m.end():].split("\n## ")[0]
    assert re.search(r"breaking", section, re.IGNORECASE)


def test_no_unfinished_markers():
    assert not re.search(r"\b(TODO|TBD|FIXME|XXX)\b", README)
