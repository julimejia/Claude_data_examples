from __future__ import annotations

import re
from enum import StrEnum

from pydantic import BaseModel, ConfigDict


class Category(StrEnum):
    INTEGER = "integer"
    FLOAT = "float"
    DECIMAL = "decimal"
    STRING = "string"
    BINARY = "binary"
    BOOLEAN = "boolean"
    DATE = "date"
    TIME = "time"
    TIMESTAMP = "timestamp"
    UUID = "uuid"
    STRUCT = "struct"
    LIST = "list"
    MAP = "map"
    UNKNOWN = "unknown"


class TypeRelation(StrEnum):
    SAME = "same"
    WIDENED = "widened"
    NARROWED = "narrowed"
    CATEGORY_CHANGE = "category_change"


NUMERIC = frozenset({Category.INTEGER, Category.FLOAT, Category.DECIMAL})


class LogicalType(BaseModel):
    """Engine-independent type. Unused attributes stay ``None``."""

    model_config = ConfigDict(frozen=True)

    category: Category
    bits: int | None = None  # integer / float width
    signed: bool = True  # integer only
    length: int | None = None  # string / binary; None = unbounded
    precision: int | None = None  # decimal
    scale: int | None = None  # decimal
    tz: bool = False  # timestamp with time zone
    raw: str = ""  # original name, kept for unknown types


_INT_NAMES: dict[str, tuple[int, bool]] = {
    "tinyint": (8, True), "int1": (8, True), "byte": (8, True), "int8": (8, True),
    "smallint": (16, True), "int2": (16, True), "short": (16, True), "int16": (16, True),
    "integer": (32, True), "int": (32, True), "int4": (32, True), "int32": (32, True),
    "bigint": (64, True), "long": (64, True), "int64": (64, True),
    "hugeint": (128, True), "int128": (128, True),
    "utinyint": (8, False), "uint8": (8, False),
    "usmallint": (16, False), "uint16": (16, False),
    "uinteger": (32, False), "uint32": (32, False),
    "ubigint": (64, False), "uint64": (64, False),
    "uhugeint": (128, False), "uint128": (128, False),
}  # fmt: skip
_FLOAT_NAMES = {
    "float": 32, "float4": 32, "real": 32, "float32": 32,
    "double": 64, "float8": 64, "float64": 64, "double precision": 64,
}  # fmt: skip
_STRING_NAMES = {"varchar", "string", "text", "char", "bpchar", "byte_array_utf8", "utf8"}
_BINARY_NAMES = {"blob", "binary", "bytea", "varbinary", "byte_array", "fixed_len_byte_array"}
_BOOL_NAMES = {"boolean", "bool", "logical"}
_TS_NAMES = {"timestamp", "datetime", "timestamp_ntz", "timestamp_s", "timestamp_ms",
             "timestamp_ns", "timestamp_us", "int96"}  # fmt: skip
_TSTZ_NAMES = {"timestamp with time zone", "timestamptz", "timestamp_ltz"}
_TIME_NAMES = {"time", "time without time zone"}
_PARAM_RE = re.compile(r"^(?P<base>[a-z0-9_ ]+?)\s*(?:\((?P<args>[^)]*)\))?$")

# Decimal digits needed to hold each integer width.
_INT_DIGITS = {8: 3, 16: 5, 32: 10, 64: 19, 128: 39}
# Largest integer width a float can hold exactly (mantissa bits).
_FLOAT_EXACT_INT_BITS = {32: 16, 64: 32}


def parse_type(name: str) -> LogicalType:
    """Map a DuckDB, Parquet, Delta or internal type name to a LogicalType."""
    text = " ".join(name.strip().lower().split())
    if text.endswith("[]") or text.startswith("list<") or text.startswith("array<"):
        return LogicalType(category=Category.LIST, raw=text)
    if text.startswith("struct") or text.startswith("row"):
        return LogicalType(category=Category.STRUCT, raw=text)
    if text.startswith("map"):
        return LogicalType(category=Category.MAP, raw=text)

    match = _PARAM_RE.match(text)
    if match is None:
        return LogicalType(category=Category.UNKNOWN, raw=text)
    base = match["base"].strip()
    args = [a.strip() for a in match["args"].split(",")] if match["args"] else []

    if base in _INT_NAMES:
        bits, signed = _INT_NAMES[base]
        return LogicalType(category=Category.INTEGER, bits=bits, signed=signed)
    if base in _FLOAT_NAMES:
        return LogicalType(category=Category.FLOAT, bits=_FLOAT_NAMES[base])
    if base in ("decimal", "numeric", "dec"):
        precision = int(args[0]) if args and args[0].isdigit() else 18
        scale = int(args[1]) if len(args) > 1 and args[1].isdigit() else 0
        return LogicalType(category=Category.DECIMAL, precision=precision, scale=scale)
    if base in _STRING_NAMES or base in ("character varying", "character"):
        length = int(args[0]) if args and args[0].isdigit() else None
        return LogicalType(category=Category.STRING, length=length)
    if base in _BINARY_NAMES:
        length = int(args[0]) if args and args[0].isdigit() else None
        return LogicalType(category=Category.BINARY, length=length)
    if base in _BOOL_NAMES:
        return LogicalType(category=Category.BOOLEAN)
    if base == "date":
        return LogicalType(category=Category.DATE)
    if base in _TSTZ_NAMES or base in ("timestamp with time zone",):
        return LogicalType(category=Category.TIMESTAMP, tz=True)
    if base in _TS_NAMES:
        return LogicalType(category=Category.TIMESTAMP)
    if base in _TIME_NAMES:
        return LogicalType(category=Category.TIME)
    if base == "uuid":
        return LogicalType(category=Category.UUID)
    return LogicalType(category=Category.UNKNOWN, raw=text)


def _compare_integers(old: LogicalType, new: LogicalType) -> TypeRelation:
    assert old.bits is not None and new.bits is not None
    if old.signed == new.signed:
        return _by_size(old.bits, new.bits)
    if not old.signed:  # unsigned -> signed needs one extra bit
        return TypeRelation.WIDENED if new.bits > old.bits else TypeRelation.NARROWED
    return TypeRelation.NARROWED  # signed -> unsigned loses negatives


def _by_size(old: int | None, new: int | None) -> TypeRelation:
    """Compare sizes where ``None`` means unbounded."""
    if old == new:
        return TypeRelation.SAME
    if new is None:
        return TypeRelation.WIDENED
    if old is None:
        return TypeRelation.NARROWED
    return TypeRelation.WIDENED if new > old else TypeRelation.NARROWED


def _compare_numeric(old: LogicalType, new: LogicalType) -> TypeRelation:
    o, n = old.category, new.category
    if o == n == Category.INTEGER:
        return _compare_integers(old, new)
    if o == n == Category.FLOAT:
        return _by_size(old.bits, new.bits)
    if o == n == Category.DECIMAL:
        assert old.precision is not None and new.precision is not None
        assert old.scale is not None and new.scale is not None
        if (old.precision, old.scale) == (new.precision, new.scale):
            return TypeRelation.SAME
        fits = new.scale >= old.scale and (new.precision - new.scale >= old.precision - old.scale)
        return TypeRelation.WIDENED if fits else TypeRelation.NARROWED
    if o == Category.INTEGER and n == Category.FLOAT:
        assert old.bits is not None and new.bits is not None
        exact = _FLOAT_EXACT_INT_BITS[new.bits]
        return TypeRelation.WIDENED if old.bits <= exact else TypeRelation.NARROWED
    if o == Category.INTEGER and n == Category.DECIMAL:
        assert old.bits is not None and new.precision is not None and new.scale is not None
        room = new.precision - new.scale >= _INT_DIGITS[old.bits]
        return TypeRelation.WIDENED if room else TypeRelation.NARROWED
    # float -> int/decimal, decimal -> int/float: may lose precision or range
    return TypeRelation.NARROWED


def compare_types(old: LogicalType, new: LogicalType) -> TypeRelation:
    """Classify a baseline -> current type change."""
    if old.category in NUMERIC and new.category in NUMERIC:
        return _compare_numeric(old, new)
    if old.category != new.category:
        if (old.category, new.category) == (Category.DATE, Category.TIMESTAMP):
            return TypeRelation.WIDENED
        if (old.category, new.category) == (Category.TIMESTAMP, Category.DATE):
            return TypeRelation.NARROWED
        return TypeRelation.CATEGORY_CHANGE
    cat = old.category
    if cat in (Category.STRING, Category.BINARY):
        return _by_size(old.length, new.length)
    if cat == Category.TIMESTAMP:
        return TypeRelation.SAME if old.tz == new.tz else TypeRelation.CATEGORY_CHANGE
    if cat == Category.UNKNOWN:
        return TypeRelation.SAME if old.raw == new.raw else TypeRelation.CATEGORY_CHANGE
    return TypeRelation.SAME  # nested children are diffed as their own paths


def compare_type_names(old: str, new: str) -> TypeRelation:
    return compare_types(parse_type(old), parse_type(new))
