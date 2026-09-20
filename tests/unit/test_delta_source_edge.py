from pathlib import Path

import pytest
from deltalake import DeltaTable, Field, Schema
from deltalake.schema import ArrayType, MapType, PrimitiveType, StructType

from schemasentinel.adapters.sources.delta import DeltaSource


def _make(path: Path, fields: list[Field], partition_by: list[str] | None = None) -> str:
    DeltaTable.create(str(path), Schema(fields), partition_by=partition_by)
    return str(path)


@pytest.fixture
def two_versions(tmp_path: Path) -> str:
    p = _make(tmp_path / "t", [Field("id", PrimitiveType("long"))])
    DeltaTable(p).alter.add_columns([Field("extra", PrimitiveType("string"))])
    return p


def test_out_of_range_version_raises(two_versions: str) -> None:
    with pytest.raises(Exception):  # noqa: B017
        DeltaSource().snapshot(two_versions, version="99")


def test_garbage_version_raises(two_versions: str) -> None:
    with pytest.raises(Exception):  # noqa: B017
        DeltaSource().snapshot(two_versions, version="not-a-version")


def test_version_with_whitespace(two_versions: str) -> None:
    snap = DeltaSource().snapshot(two_versions, version=" 0 ")
    assert snap.metadata["version"] == 0
    assert [c.name for c in snap.columns] == ["id"]


def test_timestamp_before_first_commit_raises(two_versions: str) -> None:
    with pytest.raises(Exception):  # noqa: B017
        DeltaSource().snapshot(two_versions, version="1970-01-01T00:00:00Z")


def test_empty_directory_is_not_found(tmp_path: Path) -> None:
    d = tmp_path / "empty"
    d.mkdir()
    with pytest.raises(FileNotFoundError):
        DeltaSource().snapshot(str(d))


def test_non_delta_directory_is_not_found(tmp_path: Path) -> None:
    d = tmp_path / "plain"
    d.mkdir()
    (d / "a.txt").write_text("hi")
    with pytest.raises(FileNotFoundError):
        DeltaSource().snapshot(str(d))


def test_missing_table_with_version(tmp_path: Path) -> None:
    with pytest.raises(FileNotFoundError):
        DeltaSource().snapshot(str(tmp_path / "nope"), version="0")


def test_partition_columns_reported(tmp_path: Path) -> None:
    p = _make(
        tmp_path / "p",
        [Field("id", PrimitiveType("long")), Field("dt", PrimitiveType("string"))],
        partition_by=["dt"],
    )
    snap = DeltaSource().snapshot(p)
    assert snap.metadata["partition_columns"] == ["dt"]


def test_no_partition_columns_is_empty_list(tmp_path: Path) -> None:
    p = _make(tmp_path / "p", [Field("id", PrimitiveType("long"))])
    assert DeltaSource().snapshot(p).metadata["partition_columns"] == []


def test_map_and_nested_array_labels(tmp_path: Path) -> None:
    p = _make(
        tmp_path / "m",
        [
            Field("m", MapType(PrimitiveType("string"), PrimitiveType("long"))),
            Field("aa", ArrayType(ArrayType(PrimitiveType("integer")))),
            Field("am", ArrayType(MapType(PrimitiveType("string"), PrimitiveType("string")))),
        ],
    )
    cols = {c.name: c for c in DeltaSource().snapshot(p).columns}
    assert cols["m"].data_type == "map<string,long>"
    assert cols["aa"].data_type == "array<array<integer>>"
    assert cols["am"].data_type == "array<map<string,string>>"
    assert cols["aa"].children[0].name == "element"


def test_deeply_nested_struct_and_positions(tmp_path: Path) -> None:
    inner = StructType([Field("a", PrimitiveType("string")), Field("b", PrimitiveType("long"))])
    outer = StructType([Field("x", PrimitiveType("string")), Field("inner", inner)])
    p = _make(tmp_path / "s", [Field("id", PrimitiveType("long")), Field("o", outer)])
    snap = DeltaSource().snapshot(p)
    o = snap.columns[1]
    assert o.position == 1
    assert [c.position for c in o.children] == [0, 1]
    assert [c.name for c in o.children[1].children] == ["a", "b"]
    assert o.children[1].children[1].position == 1


def test_idempotent(two_versions: str) -> None:
    src = DeltaSource()
    assert src.snapshot(two_versions) == src.snapshot(two_versions)


def test_explicit_latest_version_equals_default(two_versions: str) -> None:
    src = DeltaSource()
    assert src.snapshot(two_versions, version="1") == src.snapshot(two_versions)


def test_reads_log_only_no_data_files_touched(two_versions: str) -> None:
    before = sorted(p.name for p in Path(two_versions).rglob("*") if p.is_file())
    DeltaSource().snapshot(two_versions)
    after = sorted(p.name for p in Path(two_versions).rglob("*") if p.is_file())
    assert before == after
    assert not [n for n in after if n.endswith(".parquet")]


def test_source_field_is_ref(two_versions: str) -> None:
    assert DeltaSource().snapshot(two_versions).source == two_versions
