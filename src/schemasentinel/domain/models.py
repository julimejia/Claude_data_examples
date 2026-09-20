from __future__ import annotations

from enum import StrEnum
from typing import Any

from pydantic import BaseModel, ConfigDict, Field


class _Frozen(BaseModel):
    model_config = ConfigDict(frozen=True)


class Severity(StrEnum):
    BREAKING = "breaking"
    NON_BREAKING = "non_breaking"
    WARNING = "warning"
    NEEDS_REVIEW = "needs_review"


class Verdict(StrEnum):
    BREAKING = "breaking"
    NON_BREAKING = "non_breaking"
    NONE = "none"


class ChangeType(StrEnum):
    COLUMN_ADDED = "column_added"
    COLUMN_REMOVED = "column_removed"
    TYPE_CHANGED = "type_changed"
    NULLABILITY_CHANGED = "nullability_changed"
    POSITION_CHANGED = "position_changed"
    RENAME_CANDIDATE = "rename_candidate"


class Decision(StrEnum):
    RENAME = "rename"
    DROP_AND_ADD = "drop_and_add"
    UNKNOWN = "unknown"


class Resolution(_Frozen):
    """LLM verdict on an ambiguous change (FR-4.1)."""

    decision: Decision
    confidence: float = Field(ge=0.0, le=1.0)
    rationale: str


class Column(_Frozen):
    """A column or nested field; struct fields and list elements live in ``children``."""

    name: str
    data_type: str
    nullable: bool = True
    position: int
    default: str | None = None
    children: tuple[Column, ...] = ()


class SchemaSnapshot(_Frozen):
    source: str
    format: str
    columns: tuple[Column, ...]
    metadata: dict[str, Any] = Field(default_factory=dict)

    def flatten(self) -> dict[str, Column]:
        """Map dotted path -> column, depth-first in declaration order."""
        out: dict[str, Column] = {}

        def walk(cols: tuple[Column, ...], prefix: str) -> None:
            for col in cols:
                path = f"{prefix}{col.name}"
                out[path] = col
                walk(col.children, f"{path}.")

        walk(self.columns, "")
        return out


class SchemaChange(_Frozen):
    change_type: ChangeType
    path: str
    baseline: Column | None = None
    current: Column | None = None
    severity: Severity
    rule_id: str
    reason: str
    confidence: float = Field(default=1.0, ge=0.0, le=1.0)
    resolution: Resolution | None = None
    needs_human_review: bool = False


class DriftReport(_Frozen):
    schema_version: int = 1
    baseline: SchemaSnapshot
    current: SchemaSnapshot
    verdict: Verdict
    changes: tuple[SchemaChange, ...] = ()
    run_metadata: dict[str, Any] = Field(default_factory=dict)


class ChangeImpact(_Frozen):
    """LLM-written (or fallback) impact of one breaking change (FR-4.2)."""

    path: str
    impact: str


class ImpactText(_Frozen):
    """Structured LLM answer for a single change."""

    impact: str = Field(min_length=1)


class SummaryText(_Frozen):
    """Structured LLM answer for the executive summary."""

    summary: str = Field(min_length=1)


class Explanation(_Frozen):
    summary: str
    impacts: tuple[ChangeImpact, ...] = ()
    degraded: bool = False
