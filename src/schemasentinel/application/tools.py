from __future__ import annotations

from collections.abc import Callable, Sequence
from typing import Any

from schemasentinel.application.propose_migration import ProposeMigration
from schemasentinel.domain.migration import Dialect
from schemasentinel.domain.models import DriftReport
from schemasentinel.ports.llm import Tool

MAX_SAMPLE_VALUES = 20
MAX_VALUE_CHARS = 200

# (column path, limit) -> raw values; supplied by the caller so the tools never own a data connection.
ValueSampler = Callable[[str, int], Sequence[Any]]


def build_tools(report: DriftReport, sampler: ValueSampler | None = None) -> list[Tool]:
    """Read-only, bounded tools for the LLM (FR-4.4), closed over one drift report."""

    def get_snapshot(which: str = "current") -> dict[str, Any]:
        if which not in ("baseline", "current"):
            return {"error": "which must be 'baseline' or 'current'"}
        return getattr(report, which).model_dump(mode="json")

    def get_diff() -> dict[str, Any]:
        return {
            "verdict": report.verdict.value,
            "changes": [c.model_dump(mode="json") for c in report.changes],
        }

    def sample_column_values(column: str, limit: int = MAX_SAMPLE_VALUES) -> dict[str, Any]:
        if column not in report.current.flatten():
            return {"error": f"unknown column: {column}"}
        if sampler is None:
            return {"error": "no data access configured"}
        try:
            n = max(0, min(int(limit), MAX_SAMPLE_VALUES))
        except (TypeError, ValueError):
            return {"error": "limit must be an integer"}
        try:
            raw = list(sampler(column, n))[:n]
        except Exception as exc:  # a broken sampler must not crash the LLM loop
            return {"error": f"sampling failed: {exc}"}
        return {"column": column, "values": [str(v)[:MAX_VALUE_CHARS] for v in raw]}

    def validate_ddl(dialect: str = "duckdb", table: str = "dataset") -> dict[str, Any]:
        try:
            d = Dialect(dialect)
        except ValueError:
            return {"error": f"dialect must be one of {[x.value for x in Dialect]}"}
        result = ProposeMigration().run(report, d, table)
        return {
            "valid": result.valid,
            "validation": result.validation,
            "errors": list(result.errors),
            "statements": [s.sql for s in result.plan.statements],
        }

    return [
        Tool("get_snapshot", "Return the 'baseline' or 'current' schema snapshot.", get_snapshot),
        Tool("get_diff", "Return the verdict and all detected schema changes.", get_diff),
        Tool(
            "sample_column_values",
            f"Return at most {MAX_SAMPLE_VALUES} sample values of a column (dotted path).",
            sample_column_values,
        ),
        Tool(
            "validate_ddl",
            "Generate and validate migration DDL for the report in the given dialect.",
            validate_ddl,
        ),
    ]
