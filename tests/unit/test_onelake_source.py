from pathlib import Path
from typing import Any

import pytest
from deltalake import DeltaTable, Field, Schema
from deltalake.schema import PrimitiveType

from schemasentinel.adapters.sources.onelake import OneLakeSource, resolve_uri
from schemasentinel.ports.schema_source import SchemaSource

URI = "abfss://ws@onelake.dfs.fabric.microsoft.com/lake.Lakehouse/Tables/orders"


@pytest.fixture
def local_table(tmp_path: Path) -> str:
    path = str(tmp_path / "t")
    DeltaTable.create(path, Schema([Field("id", PrimitiveType("long"), nullable=False)]))
    return path


def make_source(local: str, calls: list[tuple[str, Any]]) -> SchemaSource:
    def opener(uri: str, storage_options: dict[str, str] | None = None) -> DeltaTable:
        calls.append((uri, storage_options))
        return DeltaTable(local)

    return OneLakeSource(token_provider=lambda: "tok", opener=opener)


def test_snapshot_via_fake_storage(local_table: str) -> None:
    calls: list[tuple[str, Any]] = []
    snap = make_source(local_table, calls).snapshot(URI)
    assert snap.format == "delta"
    assert snap.source == URI
    assert [c.name for c in snap.columns] == ["id"]
    assert calls == [(URI, {"bearer_token": "tok", "use_fabric_endpoint": "true"})]


def test_shorthand_reference(local_table: str) -> None:
    calls: list[tuple[str, Any]] = []
    make_source(local_table, calls).snapshot("ws/lake.Lakehouse/Tables/orders", version="0")
    assert calls[0][0] == URI


def test_resolve_uri_rejects_bad_ref() -> None:
    with pytest.raises(ValueError):
        resolve_uri("ws/only")
