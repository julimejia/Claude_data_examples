from __future__ import annotations

import json

from schemasentinel.application.propose_migration import ProposedMigration
from schemasentinel.domain.models import Column, DriftReport, Explanation, SchemaChange

_VERDICT_TEXT = {
    "breaking": "BREAKING",
    "non_breaking": "NON-BREAKING",
    "none": "NO DRIFT",
}


def render_json(
    report: DriftReport,
    explanation: Explanation | None = None,
    migration: ProposedMigration | None = None,
) -> str:
    if explanation is None and migration is None:
        return report.model_dump_json(indent=2)
    data = report.model_dump(mode="json")
    if explanation is not None:
        data["explanation"] = explanation.model_dump(mode="json")
    if migration is not None:
        data["migration"] = migration.model_dump(mode="json")
    return json.dumps(data, indent=2)


def _cell(text: str) -> str:
    return text.replace("|", "\\|").replace("\n", " ")


def _type(col: Column | None) -> str:
    if col is None:
        return "-"
    return col.data_type + ("" if col.nullable else " NOT NULL")


def _row(c: SchemaChange) -> str:
    cells = [
        f"`{c.path}`",
        c.change_type.value,
        c.severity.value,
        _type(c.baseline),
        _type(c.current),
        c.rule_id,
        f"{c.confidence:.2f}",
        c.reason,
    ]
    return "| " + " | ".join(_cell(x) for x in cells) + " |"


def _explanation_lines(explanation: Explanation) -> list[str]:
    lines = ["", "## Explanation", "", explanation.summary]
    if explanation.degraded:
        lines += ["", "_Some text is a deterministic fallback: the LLM was unavailable._"]
    if explanation.impacts:
        lines.append("")
        lines += [f"- `{i.path}`: {i.impact}" for i in explanation.impacts]
    return lines


def _migration_lines(migration: ProposedMigration) -> list[str]:
    plan = migration.plan
    status = "valid" if migration.valid else "INVALID"
    lines = ["", f"## Proposed migration ({plan.dialect.value})", ""]
    lines.append(f"Validation: {status} ({migration.validation})")
    lines += [f"- {e}" for e in migration.errors]
    if plan.statements:
        lines += ["", "```sql"]
        lines += [
            f"{s.sql}{'  -- destructive' if s.safety.value == 'destructive' else ''}"
            for s in plan.statements
        ]
        lines.append("```")
    else:
        lines += ["", "No DDL statements needed."]
    if plan.skipped:
        lines += ["", "Skipped:"]
        lines += [f"- `{s.path}`: {s.reason}" for s in plan.skipped]
    return lines


def render_markdown(
    report: DriftReport,
    explanation: Explanation | None = None,
    migration: ProposedMigration | None = None,
) -> str:
    lines = [
        "# Schema drift report",
        "",
        f"**Verdict:** {_VERDICT_TEXT[report.verdict.value]}",
        "",
        f"- Baseline: `{report.baseline.source}` ({report.baseline.format})",
        f"- Current: `{report.current.source}` ({report.current.format})",
        f"- Changes: {len(report.changes)}",
        "",
        "## Changes",
        "",
    ]
    if report.changes:
        lines += [
            "| Path | Change | Severity | Baseline | Current | Rule | Confidence | Reason |",
            "|---|---|---|---|---|---|---|---|",
            *(_row(c) for c in report.changes),
        ]
    else:
        lines.append("No schema changes detected.")
    if explanation is not None:
        lines += _explanation_lines(explanation)
    if migration is not None:
        lines += _migration_lines(migration)
    lines += ["", "## Run metadata", "", f"- schema_version: {report.schema_version}"]
    for key in sorted(report.run_metadata):
        lines.append(f"- {key}: {report.run_metadata[key]}")
    return "\n".join(lines) + "\n"
