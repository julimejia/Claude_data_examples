from __future__ import annotations

from schemasentinel.domain.models import Column, DriftReport, SchemaChange

_VERDICT_TEXT = {
    "breaking": "BREAKING",
    "non_breaking": "NON-BREAKING",
    "none": "NO DRIFT",
}


def render_json(report: DriftReport) -> str:
    return report.model_dump_json(indent=2)


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


def render_markdown(report: DriftReport) -> str:
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
    lines += ["", "## Run metadata", "", f"- schema_version: {report.schema_version}"]
    for key in sorted(report.run_metadata):
        lines.append(f"- {key}: {report.run_metadata[key]}")
    return "\n".join(lines) + "\n"
