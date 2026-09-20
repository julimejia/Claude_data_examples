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


class DriftReport(_Frozen):
    schema_version: int = 1
    baseline: SchemaSnapshot
    current: SchemaSnapshot
    verdict: Verdict
    changes: tuple[SchemaChange, ...] = ()
    run_metadata: dict[str, Any] = Field(default_factory=dict)
