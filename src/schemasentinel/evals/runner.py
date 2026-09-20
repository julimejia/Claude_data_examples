from __future__ import annotations

import argparse
import json
from pathlib import Path

from schemasentinel.adapters.llm.replay import ReplayAdapter
from schemasentinel.evals.golden import load_cases
from schemasentinel.evals.metrics import check_thresholds, evaluate, record_replay

ROOT = Path(__file__).resolve().parents[3]
DEFAULT_REPLAY_DIR = ROOT / "evals" / "replay"
DEFAULT_GOLDEN_DIR = ROOT / "tests" / "golden" / "cases"
DEFAULT_THRESHOLDS = ROOT / "evals" / "thresholds.json"


def main(argv: list[str] | None = None) -> int:
    """Print the metric table; exit 0 if all thresholds hold, 1 on regression, 2 on error."""
    parser = argparse.ArgumentParser(prog="python -m schemasentinel.evals")
    parser.add_argument("--golden-dir", type=Path, default=DEFAULT_GOLDEN_DIR)
    parser.add_argument("--thresholds", type=Path, default=DEFAULT_THRESHOLDS)
    parser.add_argument(
        "--replay-dir",
        type=Path,
        default=DEFAULT_REPLAY_DIR,
        help="recorded LLM transcripts; without them the LLM metrics are n/a",
    )
    parser.add_argument(
        "--record", action="store_true", help="(re)write the Replay transcripts and exit"
    )
    args = parser.parse_args(argv)

    try:
        cases = load_cases(args.golden_dir)
        thresholds = json.loads(args.thresholds.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        print(f"error: {exc}")
        return 2

    if args.record:
        record_replay(cases, args.replay_dir)
        print(f"recorded transcripts in {args.replay_dir}")
        return 0

    llm = ReplayAdapter(args.replay_dir) if args.replay_dir.is_dir() else None
    result = evaluate(cases, llm)
    violations = check_thresholds(result.metrics, thresholds, case_count=result.case_count)

    print(f"Golden cases: {result.case_count}")
    print(f"{'metric':<30}{'value':>8}{'threshold':>14}")
    for name, value in result.metrics.items():
        if name in thresholds.get("min", {}):
            limit = f">= {thresholds['min'][name]}"
        elif name in thresholds.get("max", {}):
            limit = f"<= {thresholds['max'][name]}"
        else:
            limit = "-"
        shown = "n/a" if value is None else f"{value:.3f}"
        print(f"{name:<30}{shown:>8}{limit:>14}")
    for failure in result.failures:
        print(f"  case {failure.case_id}: {failure.detail}")
    for violation in violations:
        print(f"FAIL: {violation}")
    print("RESULT: FAIL" if violations else "RESULT: PASS")
    return 1 if violations else 0
