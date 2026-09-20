# SchemaSentinel — Acceptance Criteria

Derived from `sdd/requirements.md` (v0.1), `sdd/constitution.md` and `sdd/architecture.md`. No `client/` folder exists, so nothing here comes from client documents.

## Schema extraction (FR-1)
- AC-1: Given a local Parquet file or directory, `snapshot` returns a `SchemaSnapshot` with the expected columns, types and nullability, matching the golden JSON fixture.
- AC-2: Given a CSV, the snapshot has inferred types and records the sniffed delimiter and header flag in its metadata.
- AC-3: Given a Delta table and a version or timestamp, the snapshot reflects the schema at that point, read from the transaction log only.
- AC-4: Nested struct and list types are represented recursively, and the flattened form uses dotted paths.
- AC-5: A snapshot serializes to JSON and loads back to an equal object.
- AC-6: Extracting the schema of a 1 GB Parquet file takes under 2 s, from the footer only with no full scan (NFR-1).

## Deterministic diff (FR-2)
- AC-7: The diff detects column added, removed, type changed, nullability changed, position changed, and nested field added, removed or changed.
- AC-8: A removed and an added column with compatible type and name similarity above the threshold produce an unresolved `RENAME_CANDIDATE`.
- AC-9: The diff of a snapshot with itself is empty.
- AC-10: Swapping baseline and current turns adds into removes and the reverse.
- AC-11: Shuffling column order in the input does not change the result.
- AC-12: Diffing two 500-column schemas takes under 100 ms.

## Rule-based classification (FR-3)
- AC-13: A nullable column added is non-breaking.
- AC-14: A NOT NULL column added without a default is breaking.
- AC-15: A column removed is breaking.
- AC-16: Type widening (int32→int64, float32→float64, varchar length up, date→timestamp) is non-breaking.
- AC-17: Type narrowing (int64→int32, timestamp→date) is breaking.
- AC-18: A type category change (numeric↔string, string→date, etc.) is breaking.
- AC-19: Nullable → NOT NULL is breaking.
- AC-20: NOT NULL → nullable is non-breaking and carries a `warning` flag.
- AC-21: A column reorder is non-breaking for named-access sources (Parquet) and breaking for positional CSV.
- AC-22: A rename candidate is `needs_review` until resolved.
- AC-23: Every classified change carries `severity`, `rule_id`, `reason` and `confidence` (1.0 for deterministic rules).
- AC-24: A table-driven test covers every row of the FR-3.1 table.

## LLM-assisted resolution and explanation (FR-4)
- AC-25: For each `needs_review` change, the agent returns a validated `Resolution` (`rename`, `drop_and_add` or `unknown`) with `confidence` and `rationale`.
- AC-26: If the LLM output is invalid after 2 retries, or confidence is below 0.6, the change is marked `needs_human_review` and the run still succeeds.
- AC-27: Each breaking change has a human-readable impact explanation, and the report has an executive summary. Both are Pydantic-validated.
- AC-28: The LLM tools `get_snapshot`, `get_diff`, `sample_column_values` (at most 20 values) and `validate_ddl` are available, read-only and bounded.
- AC-29: Replay-adapter runs reproduce recorded runs byte for byte.
- AC-30: An LLM failure never fails the run. The report degrades to rule-based results with `needs_human_review` flags (NFR-2).

## Migration proposal (FR-5)
- AC-31: DDL can be generated for DuckDB, Spark SQL/Delta and T-SQL (Fabric Warehouse).
- AC-32: Each statement is tagged `safe` (additive) or `destructive`, and ordered adds, then type changes, then drops.
- AC-33: DuckDB DDL is validated by executing it on an in-memory table with the baseline schema. Other dialects are validated by parsing only.
- AC-34: For every golden case, applying the proposed DuckDB DDL to the baseline yields the current schema.
- AC-35: The agent never executes DDL against user data.

## Reporting and CLI (FR-6)
- AC-36: The `DriftReport` is available as schema-versioned JSON and as Markdown.
- AC-37: The report contains the overall verdict (`breaking`, `non_breaking` or `none`), the change list, explanations, proposed DDL and run metadata.
- AC-38: `schemasentinel diff <baseline> <current> [--dialect ...] [--format json|md]` works, and `--dialect` produces DDL in the report.
- AC-39: `schemasentinel snapshot <source> -o snapshot.json` writes a loadable snapshot.
- AC-40: The exit code is 0 for no drift or non-breaking drift, 1 for breaking drift and 2 for an error.

## Adapters and notifications (FR-7)
- AC-41: `SchemaSource` has adapters for local files, local Delta and OneLake (Delta over ABFSS, optional `[onelake]` extra). Each is contract-tested against the port, and OneLake is tested with a fake storage layer.
- AC-42: `LLMPort` has `ClaudeCliAdapter` (`claude -p`, JSON output, located via `CLAUDE_BIN` or PATH), `ReplayAdapter` (with record mode) and `FakeLLM`.
- AC-43: `Notifier` has `ConsoleNotifier` and `TelegramNotifier`. A Telegram message contains the verdict, the top breaking changes and a link or path to the report.
- AC-44: Only the configured chat id is ever contacted, and the bot token never appears in logs.
- AC-45: `.github/workflows/ci.yml` is valid YAML, triggers on push to `dev`, and runs ruff, pytest and evals in Replay mode.

## Evaluation (FR-8)
- AC-46: `tests/golden/` holds at least 30 drift cases, each with an input pair, expected classification and expected DDL semantics.
- AC-47: The evals report classification accuracy, breaking-change recall, rename-resolution accuracy, DDL validity rate and invalid-output rate. Breaking-change recall is 1.0 on the deterministic rules.
- AC-48: `python -m schemasentinel.evals` prints a metric table and exits non-zero if any metric falls below its threshold in `evals/thresholds.json`.
- AC-49: The evals are reproducible in Replay mode with no network.

## Non-functional and process
- AC-50: Local runs use only the Claude Code CLI, and Replay mode makes zero LLM calls (NFR-3).
- AC-51: Data access is read-only, and secrets come only from the environment. `.env.example` holds placeholders and no secrets are in the repo (NFR-4).
- AC-52: The tool runs on Windows and Linux with Python 3.11+ (NFR-5).
- AC-53: The domain layer has at least 90 % line coverage, and unit tests use no network (NFR-6).
- AC-54: The domain layer has no I/O and no third-party imports beyond Pydantic. Dependencies are limited to pydantic, duckdb, deltalake, pytest and ruff unless a decision approves more.
- AC-55: Each run emits structured JSON-line logs with run id, source, change counts, LLM calls, latency and outcome.
- AC-56: `ruff check` is clean and `pytest` passes.
- AC-57: The README explains the problem and shows a mermaid architecture diagram, a quick start and an eval results table, and includes a demo report (M4).

## Out of scope
- Executing migrations.
- Data backfills.
- Streaming sources.
- Row-level data quality (DQ Copilot).
- UI beyond a static demo.
- Real Fabric recordings, which need the user's tenant and must be done before 2026-11-18.

## Assumptions
- No client documents were supplied. `sdd/requirements.md` is the sole source of requirements.
- The `sqlglot` dependency for T-SQL and Spark parsing (OQ-2) is a pending risky-library decision. AC-33 and AC-31 for those dialects depend on its outcome.
- The rename similarity threshold is an implementation choice (currently 0.6, `difflib`).
- The GitHub Pages demo publication (architecture §9) is optional and not an acceptance criterion.
