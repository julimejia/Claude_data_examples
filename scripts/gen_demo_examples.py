"""Regenerate public/examples.json from the golden set (FR-9.3, FR-9.4).

Every field is derived, never hand-written: the snapshots come from the golden cases, the
result from the same `api/diff.py:handle` the deployed function uses, and the explanation
and DDL from the deterministic fallbacks in the application/domain layers. No network, no
timestamps, no randomness, so two runs on the same tree produce the same bytes.

Run from anywhere: python scripts/gen_demo_examples.py [--out PATH]
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
for _path in (ROOT / "src", ROOT / "api"):
    if str(_path) not in sys.path:
        sys.path.insert(0, str(_path))

import diff as api  # noqa: E402  (api/diff.py, the Vercel function)

from schemasentinel.application.detect_drift import build_report  # noqa: E402
from schemasentinel.application.explain import Explain  # noqa: E402
from schemasentinel.domain.migration import Dialect, generate_migration  # noqa: E402
from schemasentinel.domain.models import SchemaSnapshot  # noqa: E402
from schemasentinel.evals.golden import GoldenCase, load_cases, parse_columns  # noqa: E402
from schemasentinel.ports.llm import LLMError, LLMPort, Tool  # noqa: E402

CASES_DIR = ROOT / "tests" / "golden" / "cases"
DEFAULT_OUT = ROOT / "public" / "examples.json"

# The demo has no table name of its own; the DDL is illustrative.
DEMO_TABLE = "my_table"
DEMO_DIALECT = Dialect.DUCKDB


class _NoLLM:
    """An LLM port that is never available.

    The demo is generated offline and must never call Claude, so `Explain` takes its
    deterministic fallback path. That is exactly why the output is labelled "sample".
    """

    def complete_structured(
        self,
        *,
        system: str,
        prompt: str,
        schema: type,
        tools: list[Tool] | None = None,
    ) -> Any:
        raise LLMError("the demo generator never calls an LLM")


def _snapshot(case: GoldenCase, which: str) -> SchemaSnapshot:
    return SchemaSnapshot(
        source=f"{case.id}/{which}",
        format=case.format,
        columns=parse_columns(getattr(case, which)),
    )


def _label() -> str:
    """AI output is "recorded" only when it truly came from Claude (FR-9.4).

    Nothing here does: the text below is the deterministic fallback, so it is always a
    sample. Replaying `tests/recordings/` into the demo would be a separate change.
    """
    return "sample"


def build_example(case: GoldenCase, llm: LLMPort) -> dict[str, Any]:
    baseline, current = _snapshot(case, "baseline"), _snapshot(case, "current")
    payload = {
        "baseline": baseline.model_dump(mode="json"),
        "current": current.model_dump(mode="json"),
    }
    status, result = api.handle(json.dumps(payload).encode("utf-8"))
    if status != 200:
        raise SystemExit(f"{case.id}: the diff function rejected the golden case: {result}")

    report = build_report(baseline, current, {"llm_calls": 0})
    explanation = Explain(llm).run(report)
    plan = generate_migration(report, DEMO_DIALECT, DEMO_TABLE)
    return {
        "id": case.id,
        "description": case.description,
        "baseline": payload["baseline"],
        "current": payload["current"],
        "result": result,
        "explanation": {
            "label": _label(),
            "summary": explanation.summary,
            "impacts": [{"path": i.path, "impact": i.impact} for i in explanation.impacts],
        },
        "ddl": {
            "label": _label(),
            "dialect": plan.dialect.value,
            "statements": [s.sql for s in plan.statements],
            "skipped": [{"path": s.path, "reason": s.reason} for s in plan.skipped],
        },
    }


def build_payload() -> dict[str, Any]:
    llm = _NoLLM()
    cases = sorted(load_cases(CASES_DIR), key=lambda c: c.id)  # canonical order: by case id
    return {"examples": [build_example(case, llm) for case in cases]}


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--out",
        type=Path,
        default=DEFAULT_OUT,
        help="destination file (default: public/examples.json)",
    )
    args = parser.parse_args(argv)
    payload = build_payload()
    text = json.dumps(payload, indent=2, ensure_ascii=False) + "\n"
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(text, encoding="utf-8")
    print(f"wrote {len(payload['examples'])} examples to {args.out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
