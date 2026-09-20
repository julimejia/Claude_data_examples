from __future__ import annotations

from pathlib import Path

import duckdb

from schemasentinel.domain.models import Column, SchemaSnapshot

_CSV_SUFFIXES = {".csv", ".tsv"}
_PARQUET_SUFFIX = ".parquet"

_Row = tuple[str, str, int | None]


def top_level_nullability(rows: list[_Row]) -> dict[str, bool]:
    """Map top-level column name -> nullable from ``parquet_schema`` rows.

    Rows are ``(name, repetition_type, num_children)`` in depth-first order, the first
    being the schema root; descendants of nested columns are skipped.
    """
    out: dict[str, bool] = {}
    if not rows:
        return out
    remaining = rows[0][2] or 0
    i = 1
    while remaining > 0 and i < len(rows):
        name, repetition, _ = rows[i]
        out[name] = repetition != "REQUIRED"
        i += 1 + _descendants(rows, i)
        remaining -= 1
    return out


def _descendants(rows: list[_Row], index: int) -> int:
    """Number of rows following ``rows[index]`` that belong to its subtree."""
    count = 0
    todo = rows[index][2] or 0
    j = index + 1
    while todo > 0 and j < len(rows):
        todo += (rows[j][2] or 0) - 1
        count += 1
        j += 1
    return count


class LocalFilesSource:
    """SchemaSource for local CSV files and Parquet files/directories, read via DuckDB.

    Parquet schemas come from the file footer and CSV from a sniffed sample, so no
    full scan happens.
    """

    def snapshot(self, ref: str, *, version: str | None = None) -> SchemaSnapshot:
        if version is not None:
            raise ValueError("version is not supported for local files")
        path = Path(ref)
        if not path.exists():
            raise FileNotFoundError(ref)
        if path.is_dir():
            return self._parquet(ref, path, (path / "**" / "*.parquet").as_posix())
        suffix = path.suffix.lower()
        if suffix == _PARQUET_SUFFIX:
            return self._parquet(ref, path, path.as_posix())
        if suffix in _CSV_SUFFIXES:
            return self._csv(ref, path)
        raise ValueError(f"unsupported file type: {ref}")

    def _parquet(self, ref: str, path: Path, pattern: str) -> SchemaSnapshot:
        first = _first_parquet(path)
        con = duckdb.connect()
        try:
            described = con.execute(
                "SELECT column_name, column_type FROM (DESCRIBE SELECT * FROM read_parquet(?))",
                [pattern],
            ).fetchall()
            rows = con.execute(
                "SELECT name, repetition_type, num_children FROM parquet_schema(?)",
                [first.as_posix()],
            ).fetchall()
        finally:
            con.close()
        nullable = top_level_nullability(rows)
        columns = tuple(
            Column(name=name, data_type=dtype, nullable=nullable.get(name, True), position=i)
            for i, (name, dtype) in enumerate(described)
        )
        return SchemaSnapshot(source=ref, format="parquet", columns=columns)

    def _csv(self, ref: str, path: Path) -> SchemaSnapshot:
        con = duckdb.connect()
        try:
            row = con.execute(
                "SELECT Delimiter, HasHeader, Columns FROM sniff_csv(?)", [path.as_posix()]
            ).fetchone()
        finally:
            con.close()
        assert row is not None
        delimiter, has_header, sniffed = row
        columns = tuple(
            Column(name=c["name"], data_type=c["type"], nullable=True, position=i)
            for i, c in enumerate(sniffed)
        )
        metadata = {"delimiter": delimiter, "has_header": bool(has_header)}
        return SchemaSnapshot(source=ref, format="csv", columns=columns, metadata=metadata)


def _first_parquet(path: Path) -> Path:
    if path.is_file():
        return path
    files = sorted(path.rglob("*.parquet"))
    if not files:
        raise ValueError(f"no parquet files found in {path}")
    return files[0]
