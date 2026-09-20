from __future__ import annotations

from collections.abc import Callable
from enum import StrEnum

from pydantic import BaseModel, ConfigDict

from schemasentinel.domain.models import (
    ChangeType,
    Column,
    Decision,
    DriftReport,
    SchemaChange,
)
from schemasentinel.domain.types import (
    Category,
    LogicalType,
    TypeRelation,
    compare_types,
    parse_type,
)


class Dialect(StrEnum):
    DUCKDB = "duckdb"
    SPARK = "spark"  # Spark SQL / Delta
    TSQL = "tsql"  # Fabric Warehouse


class Safety(StrEnum):
    SAFE = "safe"
    DESTRUCTIVE = "destructive"


class MigrationStatement(BaseModel):
    model_config = ConfigDict(frozen=True)

    sql: str
    safety: Safety
    path: str
    phase: int


class SkippedChange(BaseModel):
    model_config = ConfigDict(frozen=True)

    path: str
    reason: str


class MigrationPlan(BaseModel):
    model_config = ConfigDict(frozen=True)

    dialect: Dialect
    table: str
    statements: tuple[MigrationStatement, ...] = ()
    skipped: tuple[SkippedChange, ...] = ()


# Execution order (FR-5.2): adds, then renames, then in-place alterations, then drops.
PHASE_ADD, PHASE_RENAME, PHASE_ALTER, PHASE_DROP = range(4)

_INT_DUCK = {8: "TINYINT", 16: "SMALLINT", 32: "INTEGER", 64: "BIGINT", 128: "HUGEINT"}
_UINT_DUCK = {8: "UTINYINT", 16: "USMALLINT", 32: "UINTEGER", 64: "UBIGINT", 128: "UHUGEINT"}


def _duck_type(lt: LogicalType, raw: str) -> str:
    c = lt.category
    if c is Category.INTEGER and lt.bits:
        return (_INT_DUCK if lt.signed else _UINT_DUCK)[lt.bits]
    if c is Category.FLOAT:
        return "FLOAT" if lt.bits == 32 else "DOUBLE"
    if c is Category.DECIMAL:
        return f"DECIMAL({lt.precision},{lt.scale})"
    if c is Category.STRING:
        return "VARCHAR"
    if c is Category.BINARY:
        return "BLOB"
    if c is Category.BOOLEAN:
        return "BOOLEAN"
    if c is Category.DATE:
        return "DATE"
    if c is Category.TIME:
        return "TIME"
    if c is Category.TIMESTAMP:
        return "TIMESTAMPTZ" if lt.tz else "TIMESTAMP"
    if c is Category.UUID:
        return "UUID"
    return raw


def _spark_type(lt: LogicalType, raw: str) -> str:
    c = lt.category
    if c is Category.INTEGER and lt.bits:
        if not lt.signed:
            return {8: "SMALLINT", 16: "INT", 32: "BIGINT"}.get(lt.bits, "DECIMAL(38,0)")
        return {8: "TINYINT", 16: "SMALLINT", 32: "INT", 64: "BIGINT"}.get(lt.bits, "DECIMAL(38,0)")
    if c is Category.FLOAT:
        return "FLOAT" if lt.bits == 32 else "DOUBLE"
    if c is Category.DECIMAL:
        return f"DECIMAL({lt.precision},{lt.scale})"
    if c in (Category.STRING, Category.TIME, Category.UUID):
        return "STRING"
    if c is Category.BINARY:
        return "BINARY"
    if c is Category.BOOLEAN:
        return "BOOLEAN"
    if c is Category.DATE:
        return "DATE"
    if c is Category.TIMESTAMP:
        return "TIMESTAMP" if lt.tz else "TIMESTAMP_NTZ"
    return raw


def _tsql_type(lt: LogicalType, raw: str) -> str:
    c = lt.category
    if c is Category.INTEGER and lt.bits:
        # T-SQL TINYINT is unsigned 0..255, so signed 8-bit needs SMALLINT.
        bits = lt.bits if lt.signed else lt.bits * 2
        if bits <= 16:
            return "SMALLINT"
        return "INT" if bits == 32 else "BIGINT" if bits == 64 else "DECIMAL(38,0)"
    if c is Category.FLOAT:
        return "REAL" if lt.bits == 32 else "FLOAT"
    if c is Category.DECIMAL:
        return f"DECIMAL({lt.precision},{lt.scale})"
    if c is Category.STRING:
        return f"VARCHAR({lt.length})" if lt.length else "VARCHAR(8000)"
    if c is Category.BINARY:
        return f"VARBINARY({lt.length})" if lt.length else "VARBINARY(8000)"
    if c is Category.BOOLEAN:
        return "BIT"
    if c is Category.DATE:
        return "DATE"
    if c is Category.TIME:
        return "TIME(6)"
    if c is Category.TIMESTAMP:
        return "DATETIMEOFFSET(6)" if lt.tz else "DATETIME2(6)"
    if c is Category.UUID:
        return "UNIQUEIDENTIFIER"
    return raw


_TYPE_RENDERERS: dict[Dialect, Callable[[LogicalType, str], str]] = {
    Dialect.DUCKDB: _duck_type,
    Dialect.SPARK: _spark_type,
    Dialect.TSQL: _tsql_type,
}


def render_type(data_type: str, dialect: Dialect) -> str:
    return _TYPE_RENDERERS[dialect](parse_type(data_type), data_type.strip())


def quote(name: str, dialect: Dialect) -> str:
    if dialect is Dialect.SPARK:
        return "`" + name.replace("`", "``") + "`"
    if dialect is Dialect.TSQL:
        return "[" + name.replace("]", "]]") + "]"
    return '"' + name.replace('"', '""') + '"'


def _table(table: str, dialect: Dialect) -> str:
    return ".".join(quote(part, dialect) for part in table.split("."))


class _Builder:
    def __init__(self, dialect: Dialect, table: str) -> None:
        self.d = dialect
        self.t = _table(table, dialect)
        self.stmts: list[MigrationStatement] = []

    def _emit(self, sql: str, safety: Safety, path: str, phase: int) -> None:
        self.stmts.append(
            MigrationStatement(sql=f"{sql};", safety=safety, path=path, phase=phase)
        )

    def _q(self, name: str) -> str:
        return quote(name, self.d)

    def add(self, col: Column, path: str) -> None:
        typ = render_type(col.data_type, self.d)
        q = self._q(col.name)
        default = f" DEFAULT {col.default}" if col.default and self.d is not Dialect.SPARK else ""
        if self.d is Dialect.SPARK:
            sql = f"ALTER TABLE {self.t} ADD COLUMNS ({q} {typ})"
        elif self.d is Dialect.TSQL:
            sql = f"ALTER TABLE {self.t} ADD {q} {typ} NULL{default}"
        else:
            sql = f"ALTER TABLE {self.t} ADD COLUMN {q} {typ}{default}"
        self._emit(sql, Safety.SAFE, path, PHASE_ADD)
        if not col.nullable:
            self.set_nullability(col.name, typ, False, path)

    def drop(self, col: Column, path: str) -> None:
        sql = f"ALTER TABLE {self.t} DROP COLUMN {self._q(col.name)}"
        self._emit(sql, Safety.DESTRUCTIVE, path, PHASE_DROP)

    def rename(self, old: str, new: str, path: str) -> None:
        if self.d is Dialect.TSQL:
            target = f"{self.t}.{self._q(old)}".replace("'", "''")
            new_name = new.replace("'", "''")
            sql = f"EXEC sp_rename N'{target}', N'{new_name}', N'COLUMN'"
        else:
            sql = f"ALTER TABLE {self.t} RENAME COLUMN {self._q(old)} TO {self._q(new)}"
        self._emit(sql, Safety.DESTRUCTIVE, path, PHASE_RENAME)

    def retype(self, name: str, old: Column, new: Column, path: str) -> None:
        rel = compare_types(parse_type(old.data_type), parse_type(new.data_type))
        safety = Safety.SAFE if rel is TypeRelation.WIDENED else Safety.DESTRUCTIVE
        typ = render_type(new.data_type, self.d)
        q = self._q(name)
        if self.d is Dialect.DUCKDB:
            sql = f"ALTER TABLE {self.t} ALTER COLUMN {q} SET DATA TYPE {typ}"
        elif self.d is Dialect.SPARK:
            sql = f"ALTER TABLE {self.t} ALTER COLUMN {q} TYPE {typ}"
        else:
            null = "NULL" if new.nullable else "NOT NULL"
            sql = f"ALTER TABLE {self.t} ALTER COLUMN {q} {typ} {null}"
        self._emit(sql, safety, path, PHASE_ALTER)

    def set_nullability(self, name: str, typ: str, nullable: bool, path: str) -> None:
        q = self._q(name)
        safety = Safety.SAFE if nullable else Safety.DESTRUCTIVE
        if self.d is Dialect.TSQL:
            sql = f"ALTER TABLE {self.t} ALTER COLUMN {q} {typ} {'NULL' if nullable else 'NOT NULL'}"
        else:
            action = "DROP NOT NULL" if nullable else "SET NOT NULL"
            sql = f"ALTER TABLE {self.t} ALTER COLUMN {q} {action}"
        self._emit(sql, safety, path, PHASE_ALTER)


def _is_nested(path: str) -> bool:
    return "." in path


def _type_differs(old: Column, new: Column) -> bool:
    lo, ln = parse_type(old.data_type), parse_type(new.data_type)
    if compare_types(lo, ln) is not TypeRelation.SAME:
        return True
    return lo.category is not Category.UNKNOWN and lo != ln


def _handle(b: _Builder, ch: SchemaChange, skipped: list[SkippedChange]) -> None:
    path, old, new = ch.path, ch.baseline, ch.current
    ct = ch.change_type
    if ct is ChangeType.POSITION_CHANGED:
        return  # column order cannot be altered by DDL; not a schema-content change
    if _is_nested(path):
        skipped.append(SkippedChange(path=path, reason="nested field changes are not supported"))
        return
    if ct is ChangeType.COLUMN_ADDED and new:
        b.add(new, path)
    elif ct is ChangeType.COLUMN_REMOVED and old:
        b.drop(old, path)
    elif ct is ChangeType.TYPE_CHANGED and old and new:
        if ch.needs_human_review:
            skipped.append(SkippedChange(path=path, reason="needs human review"))
        else:
            b.retype(new.name, old, new, path)
    elif ct is ChangeType.NULLABILITY_CHANGED and old and new:
        b.set_nullability(new.name, render_type(new.data_type, b.d), new.nullable, path)
    elif ct is ChangeType.RENAME_CANDIDATE and old and new:
        res = ch.resolution
        if ch.needs_human_review or res is None or res.decision is Decision.UNKNOWN:
            skipped.append(SkippedChange(path=path, reason="unresolved rename candidate"))
        elif res.decision is Decision.RENAME:
            b.rename(old.name, new.name, path)
            if _type_differs(old, new):
                b.retype(new.name, old, new, path)
            if old.nullable != new.nullable:
                b.set_nullability(new.name, render_type(new.data_type, b.d), new.nullable, path)
        else:  # drop_and_add
            b.add(new, path)
            b.drop(old, path)
    else:
        skipped.append(SkippedChange(path=path, reason=f"unsupported change {ct.value}"))


def generate_migration(report: DriftReport, dialect: Dialect, table: str) -> MigrationPlan:
    """Deterministic DDL taking the baseline schema to the current one (FR-5.1, FR-5.2)."""
    b = _Builder(dialect, table)
    skipped: list[SkippedChange] = []
    for ch in report.changes:
        _handle(b, ch, skipped)
    ordered = sorted(b.stmts, key=lambda s: s.phase)  # stable: keeps report order in a phase
    return MigrationPlan(
        dialect=dialect, table=table, statements=tuple(ordered), skipped=tuple(skipped)
    )
