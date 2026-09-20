from __future__ import annotations

import re

import duckdb
from pydantic import BaseModel, ConfigDict

from schemasentinel.domain.migration import (
    Dialect,
    MigrationPlan,
    generate_migration,
    quote,
    render_type,
)
from schemasentinel.domain.models import DriftReport
from schemasentinel.domain.types import parse_type

_ALTER_RE = re.compile(r"^(ALTER TABLE|EXEC sp_rename)\b", re.IGNORECASE)


class ProposedMigration(BaseModel):
    model_config = ConfigDict(frozen=True)

    plan: MigrationPlan
    valid: bool
    validation: str  # "executed" (DuckDB, in memory) or "structural" (no execution)
    parsed: bool = False  # True when sqlglot (optional extra `sql`) also parsed every statement
    errors: tuple[str, ...] = ()


def _balanced(sql: str) -> bool:
    """Quotes and brackets are closed; a light stand-in for a real parser (see OQ-2)."""
    depth = 0
    quote_char = ""
    for ch in sql:
        if quote_char:
            if ch == quote_char:
                quote_char = ""
        elif ch in "'\"`":
            quote_char = ch
        elif ch == "[":
            quote_char = "]"
        elif ch == "(":
            depth += 1
        elif ch == ")":
            depth -= 1
            if depth < 0:
                return False
    return depth == 0 and not quote_char


def _validate_parsed(plan: MigrationPlan) -> list[str] | None:
    """Parse with sqlglot (optional extra `sql`); None when it is not installed."""
    try:
        import sqlglot
        from sqlglot.errors import SqlglotError
    except ImportError:
        return None
    errors = []
    for st in plan.statements:
        try:
            sqlglot.parse_one(st.sql, read=plan.dialect.value)
        except SqlglotError as exc:
            errors.append(f"unparseable {plan.dialect.value} statement: {st.sql} ({exc})")
    return errors


def _validate_structure(plan: MigrationPlan) -> list[str]:
    errors = []
    for st in plan.statements:
        if not _ALTER_RE.match(st.sql) or not st.sql.endswith(";") or not _balanced(st.sql):
            errors.append(f"malformed statement: {st.sql}")
    return errors


def _validate_duckdb(report: DriftReport, plan: MigrationPlan) -> list[str]:
    """Run the DDL on an in-memory copy of the baseline; never touches user data (FR-5.4)."""
    con = duckdb.connect(":memory:")
    try:
        t = quote(plan.table, Dialect.DUCKDB)
        cols = ", ".join(
            f"{quote(c.name, Dialect.DUCKDB)} {render_type(c.data_type, Dialect.DUCKDB)}"
            f"{'' if c.nullable else ' NOT NULL'}"
            for c in report.baseline.columns
        )
        con.execute(f"CREATE TABLE {t} ({cols})")
        for st in plan.statements:
            con.execute(st.sql)
        rows = con.execute(
            "SELECT column_name, data_type, is_nullable FROM information_schema.columns "
            "WHERE table_name = ?",
            [plan.table],
        ).fetchall()
    except duckdb.Error as exc:
        return [f"DuckDB rejected the DDL: {exc}"]
    finally:
        con.close()

    got = {name: (parse_type(typ), nullable == "YES") for name, typ, nullable in rows}
    want = {
        c.name: (parse_type(render_type(c.data_type, Dialect.DUCKDB)), c.nullable)
        for c in report.current.columns
    }
    if got != want:
        return [f"applying the DDL to the baseline gives {sorted(got)} != current {sorted(want)}"]
    return []


class ProposeMigration:
    """Use case: generate DDL for a drift report and validate it (FR-5)."""

    def run(
        self, report: DriftReport, dialect: Dialect = Dialect.DUCKDB, table: str = "dataset"
    ) -> ProposedMigration:
        plan = generate_migration(report, dialect, table)
        if dialect is Dialect.DUCKDB:
            return ProposedMigration(
                plan=plan,
                valid=not (errs := _validate_duckdb(report, plan)),
                validation="executed",
                errors=tuple(errs),
            )
        errs = _validate_structure(plan)
        parsed = _validate_parsed(plan)
        if parsed is not None:
            errs += parsed
        return ProposedMigration(
            plan=plan,
            valid=not errs,
            validation="structural",
            parsed=parsed is not None,
            errors=tuple(errs),
        )
