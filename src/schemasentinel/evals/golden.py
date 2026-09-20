from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from pydantic import BaseModel, ConfigDict, Field

from schemasentinel.domain.models import ChangeType, Column, Severity, Verdict


class ExpectedChange(BaseModel):
    model_config = ConfigDict(frozen=True)

    change_type: ChangeType
    path: str
    severity: Severity
    rule_id: str

    def key(self) -> tuple[str, str, str, str]:
        return (self.change_type.value, self.path, self.severity.value, self.rule_id)


class Expected(BaseModel):
    verdict: Verdict
    changes: list[ExpectedChange] = Field(default_factory=list)


class GoldenCase(BaseModel):
    """One baseline/current pair with the expected deterministic classification.

    Columns use a shorthand: ``"name:TYPE"``, ``"name:TYPE!"`` for NOT NULL and
    ``"name:TYPE!=default"`` for a default; nested columns use objects with ``children``.
    """

    id: str
    description: str = ""
    format: str = "parquet"
    baseline: list[str | dict[str, Any]]
    current: list[str | dict[str, Any]]
    expected: Expected
    # Reserved for the LLM stage (FR-4.1): "rename" or "drop_and_add" for rename candidates.
    expected_resolution: str | None = None


def _parse_column(spec: str | dict[str, Any], position: int) -> Column:
    if isinstance(spec, dict):
        return Column(
            name=spec["name"],
            data_type=spec["type"],
            nullable=spec.get("nullable", True),
            position=position,
            default=spec.get("default"),
            children=parse_columns(spec.get("children", [])),
        )
    name, sep, rest = spec.partition(":")
    if not sep or not name.strip() or not rest.strip():
        raise ValueError(f"invalid column shorthand: {spec!r}")
    rest, eq, default = rest.partition("=")
    nullable = not rest.endswith("!")
    return Column(
        name=name.strip(),
        data_type=rest.rstrip("!").strip(),
        nullable=nullable,
        position=position,
        default=default if eq else None,
    )


def parse_columns(specs: list[str | dict[str, Any]]) -> tuple[Column, ...]:
    return tuple(_parse_column(spec, i) for i, spec in enumerate(specs))


def load_cases(directory: Path) -> list[GoldenCase]:
    """Load every ``*.json`` case in a directory, sorted by file name."""
    if not directory.is_dir():
        raise FileNotFoundError(f"golden directory not found: {directory}")
    return [
        GoldenCase.model_validate(json.loads(path.read_text(encoding="utf-8")))
        for path in sorted(directory.glob("*.json"))
    ]
