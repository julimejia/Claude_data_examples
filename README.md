# SchemaSentinel

Detects schema drift between two versions of a tabular dataset (CSV, Parquet, Delta),
classifies each change as breaking or non-breaking, explains the impact and proposes a
migration as DDL. Deterministic core, validated LLM enrichment, read-only on data.

Status: under construction. See `sdd/` for the constitution, requirements and architecture.

## Development
```
python -m venv .venv
.venv/Scripts/python.exe -m pip install -e ".[dev]"
.venv/Scripts/python.exe -m pytest
.venv/Scripts/python.exe -m ruff check
```
