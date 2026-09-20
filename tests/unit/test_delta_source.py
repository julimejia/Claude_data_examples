from datetime import UTC, datetime
from pathlib import Path

import pytest
from deltalake import DeltaTable, Field, Schema
from deltalake.schema import ArrayType, PrimitiveType, StructType

from schemasentinel.adapters.sources.delta import DeltaSource


@pytest.fixture
def table(tmp_path: Path) -> str:
    path = str(tmp_path / "t")
    dt = DeltaTable.create(
        path,
        Schema(
            [
                Field("id", PrimitiveType("long"), nullable=False),
                Field("name", PrimitiveType("string")),
            ]
        ),
    )
    dt.alter.add_columns(
        [
            Field("tags", ArrayType(PrimitiveType("string"))),
            Field("addr", StructType([Field("zip", PrimitiveType("string"))])),
        ]
    )
    return path


def test_latest_version(table: str) -> None:
    snap = DeltaSource().snapshot(table)
    assert snap.format == "delta"
    assert snap.metadata["version"] == 1
    assert [c.name for c in snap.columns] == ["id", "name", "tags", "addr"]
    assert snap.columns[0].data_type == "long"
    assert snap.columns[0].nullable is False
    assert snap.columns[2].data_type == "array<string>"
    assert snap.columns[3].children[0].name == "zip"


def test_specific_version(table: str) -> None:
    snap = DeltaSource().snapshot(table, version="0")
    assert snap.metadata["version"] == 0
    assert [c.name for c in snap.columns] == ["id", "name"]


def test_timestamp_version(table: str) -> None:
    future = datetime.now(UTC).isoformat()
    snap = DeltaSource().snapshot(table, version=future)
    assert snap.metadata["version"] == 1


def test_missing_table(tmp_path: Path) -> None:
    with pytest.raises(FileNotFoundError):
        DeltaSource().snapshot(str(tmp_path / "nope"))
