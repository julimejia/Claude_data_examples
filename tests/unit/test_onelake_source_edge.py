import sys
from pathlib import Path
from typing import Any

import pytest
from deltalake import DeltaTable, Field, Schema
from deltalake.exceptions import TableNotFoundError
from deltalake.schema import PrimitiveType

from schemasentinel.adapters.sources import onelake
from schemasentinel.adapters.sources.onelake import OneLakeSource, resolve_uri

HOST = "onelake.dfs.fabric.microsoft.com"


@pytest.fixture
def local_table(tmp_path: Path) -> str:
    path = str(tmp_path / "t")
    DeltaTable.create(path, Schema([Field("id", PrimitiveType("long"), nullable=False)]))
    return path


def test_resolve_uri_abfss_passthrough_unchanged() -> None:
    ref = "abfss://ws@other.host/item/Tables/x"
    assert resolve_uri(ref) == ref


def test_resolve_uri_strips_surrounding_slashes() -> None:
    assert resolve_uri("/ws/lake.Lakehouse/Tables/orders/") == (
        f"abfss://ws@{HOST}/lake.Lakehouse/Tables/orders"
    )


def test_resolve_uri_nested_path_preserved() -> None:
    assert resolve_uri("ws/item/Tables/schema/orders") == (
        f"abfss://ws@{HOST}/item/Tables/schema/orders"
    )


@pytest.mark.parametrize(
    "ref", ["", "/", "ws", "ws/item", "ws/item/", "ws//path", "/item/path", "//a/b"]
)
def test_resolve_uri_rejects_malformed(ref: str) -> None:
    with pytest.raises(ValueError):
        resolve_uri(ref)


def test_invalid_ref_never_fetches_token_or_opens() -> None:
    calls: list[str] = []

    def token() -> str:
        calls.append("token")
        return "t"

    def opener(*a: Any, **k: Any) -> DeltaTable:
        calls.append("open")
        raise AssertionError

    with pytest.raises(ValueError):
        OneLakeSource(token_provider=token, opener=opener).snapshot("ws/only")
    assert calls == []


def test_token_provider_called_per_snapshot(local_table: str) -> None:
    tokens = iter(["a", "b"])
    seen: list[Any] = []

    def opener(uri: str, storage_options: dict[str, str] | None = None) -> DeltaTable:
        seen.append(storage_options)
        return DeltaTable(local_table)

    src = OneLakeSource(token_provider=lambda: next(tokens), opener=opener)
    ref = f"abfss://ws@{HOST}/i/Tables/t"
    src.snapshot(ref)
    src.snapshot(ref)
    assert [s["bearer_token"] for s in seen] == ["a", "b"]


def test_token_provider_error_propagates() -> None:
    def token() -> str:
        raise PermissionError("no creds")

    def opener(*a: Any, **k: Any) -> DeltaTable:
        raise AssertionError("must not open")

    with pytest.raises(PermissionError):
        OneLakeSource(token_provider=token, opener=opener).snapshot(
            f"abfss://ws@{HOST}/i/Tables/t"
        )


def test_table_not_found_maps_to_file_not_found() -> None:
    def opener(*a: Any, **k: Any) -> DeltaTable:
        raise TableNotFoundError("nope")

    src = OneLakeSource(token_provider=lambda: "t", opener=opener)
    with pytest.raises(FileNotFoundError):
        src.snapshot("ws/item/Tables/missing")


def test_version_number_selects_version(tmp_path: Path) -> None:
    path = str(tmp_path / "v")
    DeltaTable.create(path, Schema([Field("id", PrimitiveType("long"))]))
    src = OneLakeSource(token_provider=lambda: "t", opener=lambda uri, storage_options=None: DeltaTable(path))
    snap = src.snapshot("ws/item/Tables/t", version="0")
    assert snap.metadata["version"] == 0


def test_bad_version_raises(local_table: str) -> None:
    src = OneLakeSource(
        token_provider=lambda: "t",
        opener=lambda uri, storage_options=None: DeltaTable(local_table),
    )
    with pytest.raises(Exception):
        src.snapshot("ws/item/Tables/t", version="99")


def test_snapshot_source_is_resolved_uri(local_table: str) -> None:
    src = OneLakeSource(
        token_provider=lambda: "t",
        opener=lambda uri, storage_options=None: DeltaTable(local_table),
    )
    snap = src.snapshot("ws/item/Tables/t")
    assert snap.source == f"abfss://ws@{HOST}/item/Tables/t"


def test_default_token_provider_missing_extra(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setitem(sys.modules, "azure.identity", None)
    with pytest.raises(RuntimeError, match="onelake"):
        onelake.default_token_provider()


def test_bearer_token_not_in_snapshot(local_table: str) -> None:
    src = OneLakeSource(
        token_provider=lambda: "SECRET-TOKEN",
        opener=lambda uri, storage_options=None: DeltaTable(local_table),
    )
    snap = src.snapshot("ws/item/Tables/t")
    assert "SECRET-TOKEN" not in repr(snap)
