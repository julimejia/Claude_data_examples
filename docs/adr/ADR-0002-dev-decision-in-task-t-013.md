# ADR-0002 - dev decision in task:T-013

- Status: accepted
- Date: 2026-09-19
- Raised during: task:T-013
- Decided by: project owner via Telegram

## Context
T-013 is implemented and tests pass (294 passed, ruff clean on touched files), with DuckDB DDL validated by execution and safe-before-destructive ordering. Spark/T-SQL are currently validated only by a light structural check. Decision (OQ-2): (1) keep the structural check and add no dependency, (2) add sqlglot as a core dependency for real parse validation, or (3) add sqlglot as an optional extra with structural fallback. I recommend (3).

## Decision
3
