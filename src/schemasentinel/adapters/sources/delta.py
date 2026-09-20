from __future__ import annotations

import json
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

    def snapshot(self, ref: str, *, version: str | None = None) -> SchemaSnapshot:
        if "://" not in ref and not Path(ref).exists():
            raise FileNotFoundError(ref)
        try:
            table = DeltaTable(ref)
            if version is not None:
                text = version.strip()
                table.load_as_version(int(text) if text.isdigit() else text)
        except TableNotFoundError as exc:
            raise FileNotFoundError(ref) from exc
        schema = json.loads(table.schema().to_json())
        columns = _columns(schema["fields"])
        metadata = {
            "version": table.version(),
            "partition_columns": list(table.metadata().partition_columns),
        }
        return SchemaSnapshot(source=ref, format="delta", columns=columns, metadata=metadata)


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
