from __future__ import annotations

import json
from collections.abc import Callable
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from deltalake import DeltaTable
from deltalake.exceptions import TableNotFoundError

from schemasentinel.domain.models import Column, SchemaSnapshot


class DeltaSource:
    """SchemaSource for local Delta tables, read from the transaction log only.

    ``version`` is a table version number (``"3"``) or an ISO 8601 timestamp
    (``"2024-01-01T00:00:00Z"``); ``None`` means the latest version. No data files are read.
    """

    def __init__(
        self,
        storage_options: dict[str, str] | None = None,
        opener: Callable[..., DeltaTable] = DeltaTable,
    ) -> None:
        self._storage_options = storage_options
        self._opener = opener

    def snapshot(self, ref: str, *, version: str | None = None) -> SchemaSnapshot:
        if "://" not in ref and not Path(ref).exists():
            raise FileNotFoundError(ref)
        try:
            table = (self._opener(ref, storage_options=self._storage_options)
                     if self._storage_options else self._opener(ref))
            if version is not None:
                text = version.strip()
                if text.isdigit():
                    table.load_as_version(int(text))
                else:
                    _check_not_before_first_commit(table, text)
                    table.load_as_version(text)
        except TableNotFoundError as exc:
            raise FileNotFoundError(ref) from exc
        schema = json.loads(table.schema().to_json())
        columns = _columns(schema["fields"])
        metadata = {
            "version": table.version(),
            "partition_columns": list(table.metadata().partition_columns),
        }
        return SchemaSnapshot(source=ref, format="delta", columns=columns, metadata=metadata)


def _check_not_before_first_commit(table: DeltaTable, text: str) -> None:
    """deltalake silently falls back to version 0 for early timestamps; reject them."""
    try:
        when = datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError:
        return  # let deltalake report the unparseable value
    if when.tzinfo is None:
        when = when.replace(tzinfo=UTC)
    first = min(h["timestamp"] for h in table.history() if "timestamp" in h)
    if when.timestamp() * 1000 < first:
        raise ValueError(f"timestamp {text} is before the first commit of the table")


def _columns(fields: list[dict[str, Any]]) -> tuple[Column, ...]:
    return tuple(_column(f["name"], f["type"], f.get("nullable", True), i)
                 for i, f in enumerate(fields))


def _column(name: str, dtype: Any, nullable: bool, position: int) -> Column:
    if isinstance(dtype, str):
        return Column(name=name, data_type=dtype, nullable=nullable, position=position)
    kind = dtype["type"]
    if kind == "struct":
        return Column(name=name, data_type="struct", nullable=nullable, position=position,
                      children=_columns(dtype["fields"]))
    if kind == "array":
        element = _column("element", dtype["elementType"], dtype.get("containsNull", True), 0)
        return Column(name=name, data_type=f"array<{_label(dtype['elementType'])}>",
                      nullable=nullable, position=position, children=(element,))
    if kind == "map":
        label = f"map<{_label(dtype['keyType'])},{_label(dtype['valueType'])}>"
        return Column(name=name, data_type=label, nullable=nullable, position=position)
    return Column(name=name, data_type=str(kind), nullable=nullable, position=position)


def _label(dtype: Any) -> str:
    """Compact type name used inside array<...> / map<...> labels."""
    if isinstance(dtype, str):
        return dtype
    kind = dtype["type"]
    if kind == "array":
        return f"array<{_label(dtype['elementType'])}>"
    if kind == "map":
        return f"map<{_label(dtype['keyType'])},{_label(dtype['valueType'])}>"
    return str(kind)
