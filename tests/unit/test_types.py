import pytest

from schemasentinel.domain.types import (
    Category,
    TypeRelation,
    compare_type_names,
    parse_type,
)

W, N, C, S = (
    TypeRelation.WIDENED,
    TypeRelation.NARROWED,
    TypeRelation.CATEGORY_CHANGE,
    TypeRelation.SAME,
)


@pytest.mark.parametrize(
    ("name", "category", "bits"),
    [
        ("INTEGER", Category.INTEGER, 32),  # DuckDB
        ("BIGINT", Category.INTEGER, 64),
        ("TINYINT", Category.INTEGER, 8),
        ("INT32", Category.INTEGER, 32),  # Parquet
        ("INT64", Category.INTEGER, 64),
        ("integer", Category.INTEGER, 32),  # Delta
        ("long", Category.INTEGER, 64),
        ("short", Category.INTEGER, 16),
        ("byte", Category.INTEGER, 8),
        ("int64", Category.INTEGER, 64),  # internal
        ("FLOAT", Category.FLOAT, 32),
        ("DOUBLE", Category.FLOAT, 64),
        ("float64", Category.FLOAT, 64),
        ("VARCHAR", Category.STRING, None),
        ("string", Category.STRING, None),
        ("BYTE_ARRAY", Category.BINARY, None),
        ("BOOLEAN", Category.BOOLEAN, None),
        ("DATE", Category.DATE, None),
        ("TIMESTAMP", Category.TIMESTAMP, None),
        ("timestamp_ntz", Category.TIMESTAMP, None),
        ("TIMESTAMP WITH TIME ZONE", Category.TIMESTAMP, None),
        ("INT96", Category.TIMESTAMP, None),
        ("DECIMAL(10,2)", Category.DECIMAL, None),
        ("decimal(10, 2)", Category.DECIMAL, None),
        ("STRUCT(a INTEGER)", Category.STRUCT, None),
        ("INTEGER[]", Category.LIST, None),
        ("array<string>", Category.LIST, None),
        ("MAP(VARCHAR, INTEGER)", Category.MAP, None),
        ("UUID", Category.UUID, None),
        ("geometry", Category.UNKNOWN, None),
    ],
)
def test_parse_type(name, category, bits):
    parsed = parse_type(name)
    assert parsed.category == category
    assert parsed.bits == bits


def test_parse_parameters():
    dec = parse_type("DECIMAL(12,3)")
    assert (dec.precision, dec.scale) == (12, 3)
    assert parse_type("VARCHAR(50)").length == 50
    assert parse_type("VARCHAR").length is None
    assert parse_type("TIMESTAMPTZ").tz is True
    assert parse_type("UINTEGER").signed is False


@pytest.mark.parametrize(
    ("old", "new", "expected"),
    [
        # identical / cross-engine equivalents
        ("INTEGER", "INTEGER", S),
        ("INTEGER", "int32", S),
        ("BIGINT", "long", S),
        ("VARCHAR", "string", S),
        # integer widening / narrowing
        ("int32", "int64", W),
        ("SMALLINT", "INTEGER", W),
        ("int64", "int32", N),
        ("BIGINT", "TINYINT", N),
        ("UINTEGER", "BIGINT", W),
        ("UINTEGER", "INTEGER", N),
        ("INTEGER", "UINTEGER", N),
        ("UTINYINT", "USMALLINT", W),
        # floats
        ("float32", "float64", W),
        ("DOUBLE", "FLOAT", N),
        # int <-> float / decimal
        ("INTEGER", "DOUBLE", W),
        ("BIGINT", "DOUBLE", N),
        ("SMALLINT", "FLOAT", W),
        ("DOUBLE", "INTEGER", N),
        ("INTEGER", "DECIMAL(12,2)", W),
        ("BIGINT", "DECIMAL(10,2)", N),
        ("DECIMAL(10,2)", "INTEGER", N),
        # decimal
        ("DECIMAL(10,2)", "DECIMAL(12,2)", W),
        ("DECIMAL(10,2)", "DECIMAL(12,4)", W),
        ("DECIMAL(10,2)", "DECIMAL(10,4)", N),
        ("DECIMAL(12,2)", "DECIMAL(10,2)", N),
        ("DECIMAL(10,2)", "DECIMAL(10,2)", S),
        ("DECIMAL(10,2)", "DOUBLE", N),
        # string length
        ("VARCHAR(50)", "VARCHAR(100)", W),
        ("VARCHAR(100)", "VARCHAR(50)", N),
        ("VARCHAR(50)", "VARCHAR", W),
        ("VARCHAR", "VARCHAR(50)", N),
        ("VARCHAR(50)", "VARCHAR(50)", S),
        # temporal
        ("DATE", "TIMESTAMP", W),
        ("TIMESTAMP", "DATE", N),
        ("TIMESTAMP", "TIMESTAMPTZ", C),
        ("TIMESTAMP", "timestamp_ntz", S),
        # category changes
        ("INTEGER", "VARCHAR", C),
        ("VARCHAR", "INTEGER", C),
        ("VARCHAR", "DATE", C),
        ("DATE", "VARCHAR", C),
        ("BOOLEAN", "INTEGER", C),
        ("VARCHAR", "BLOB", C),
        ("STRUCT(a INTEGER)", "INTEGER[]", C),
        ("STRUCT(a INTEGER)", "STRUCT(a INTEGER, b INTEGER)", S),
        # unknown types
        ("geometry", "geometry", S),
        ("geometry", "geography", C),
    ],
)
def test_compare_type_names(old, new, expected):
    assert compare_type_names(old, new) == expected
