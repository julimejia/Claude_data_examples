from pathlib import Path

import pytest

from schemasentinel.adapters.sources.local_files import LocalFilesSource
from schemasentinel.domain.models import SchemaSnapshot
from schemasentinel.ports.schema_source import SchemaSource

FIXTURES = Path(__file__).parent.parent / "fixtures"


@pytest.fixture
def source() -> SchemaSource:
    return LocalFilesSource()


def test_parquet_file(source: SchemaSource) -> None:
    snap = source.snapshot(str(FIXTURES / "orders.parquet"))
    assert snap.format == "parquet"
    got = [(c.name, c.data_type, c.nullable, c.position) for c in snap.columns]
    assert got == [
        ("id", "BIGINT", True, 0),  # DuckDB writes every column OPTIONAL
        ("customer", "VARCHAR", True, 1),
        ("amount", "DECIMAL(10,2)", True, 2),
        ("created", "DATE", True, 3),
        ("paid", "BOOLEAN", True, 4),
    ]


def test_parquet_directory(source: SchemaSource) -> None:
    snap = source.snapshot(str(FIXTURES / "orders_dir"))
    assert snap.format == "parquet"
    assert [c.name for c in snap.columns] == ["id", "customer", "amount", "created", "paid"]


def test_csv_infers_types_and_dialect(source: SchemaSource) -> None:
    snap = source.snapshot(str(FIXTURES / "people.csv"))
    assert snap.format == "csv"
    assert [(c.name, c.data_type) for c in snap.columns] == [
        ("id", "BIGINT"),
        ("name", "VARCHAR"),
        ("score", "DOUBLE"),
        ("joined", "DATE"),
    ]
    assert snap.metadata["delimiter"] == ","
    assert snap.metadata["has_header"] is True


def test_csv_sniffs_other_delimiter(source: SchemaSource) -> None:
    snap = source.snapshot(str(FIXTURES / "semicolon.csv"))
    assert snap.metadata["delimiter"] == ";"
    assert [c.name for c in snap.columns] == ["id", "name"]


def test_snapshot_json_roundtrip(source: SchemaSource) -> None:
    snap = source.snapshot(str(FIXTURES / "people.csv"))
    assert SchemaSnapshot.model_validate_json(snap.model_dump_json()) == snap


def test_parquet_reads_metadata_only(source: SchemaSource, tmp_path: Path) -> None:
    import duckdb

    big = tmp_path / "big.parquet"
    duckdb.execute(
        f"COPY (SELECT range AS id, range::VARCHAR AS s FROM range(2000000)) "
        f"TO '{big.as_posix()}' (FORMAT PARQUET)"
    )
    import time

    start = time.perf_counter()
    snap = source.snapshot(str(big))
    assert time.perf_counter() - start < 2.0
    assert [c.name for c in snap.columns] == ["id", "s"]


def test_missing_path(source: SchemaSource) -> None:
    with pytest.raises(FileNotFoundError):
        source.snapshot(str(FIXTURES / "nope.csv"))


def test_unsupported_extension(source: SchemaSource, tmp_path: Path) -> None:
    f = tmp_path / "x.txt"
    f.write_text("a")
    with pytest.raises(ValueError, match="unsupported"):
        source.snapshot(str(f))


def test_required_columns_map_to_not_null() -> None:
    from schemasentinel.adapters.sources.local_files import top_level_nullability

    rows = [
        ("root", "REQUIRED", 3),
        ("id", "REQUIRED", None),
        ("addr", "OPTIONAL", 2),
        ("street", "REQUIRED", None),
        ("zip", "OPTIONAL", None),
        ("note", "OPTIONAL", None),
    ]
    expected = {"id": False, "addr": True, "note": True}
    assert top_level_nullability(rows) == expected


def test_version_not_supported(source: SchemaSource) -> None:
    with pytest.raises(ValueError, match="version"):
        source.snapshot(str(FIXTURES / "people.csv"), version="1")
