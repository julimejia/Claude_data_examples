# SchemaSentinel — Requirements

Status: draft v0.1 — the loop must not change requirements without a decision (see constitution §4).

## 1. Scope

**In scope (MVP):** compare two schema snapshots (baseline vs current) from CSV, Parquet
and Delta sources (local files first, OneLake adapter after), classify drift, explain impact,
propose DDL migration, report through CLI/JSON/Markdown and notify via Telegram.

**Out of scope (MVP):** executing migrations, data backfills, streaming sources, row-level
data quality (that is DQ Copilot, project #3), UI beyond a static demo.

## 2. Glossary
- **Snapshot**: a normalized schema (`SchemaSnapshot`) of a dataset at a point in time.
- **Change**: one difference between two snapshots (`SchemaChange`).
- **Drift report**: the full result (`DriftReport`) for a baseline/current pair.
- **Breaking**: a change that can make existing consumers or writers fail or silently
  produce wrong data.

## 3. Functional requirements

### FR-1 Schema extraction
- FR-1.1 Extract a `SchemaSnapshot` from a local Parquet file or directory (DuckDB).
- FR-1.2 Extract from CSV with type inference (DuckDB `read_csv` sniffing); record the
  sniffed dialect (delimiter, header) in snapshot metadata.
- FR-1.3 Extract from a Delta table at a given version or timestamp (`deltalake`).
- FR-1.4 Nested types (struct, list) are represented recursively with dotted paths.
- FR-1.5 Snapshots serialize to and load from JSON (`SchemaSnapshot.model_dump_json`).
- Acceptance: golden fixtures for each format produce the expected snapshot JSON.

### FR-2 Deterministic diff
- FR-2.1 Detect: column added, removed, type changed, nullability changed, position changed,
  nested field added/removed/changed.
- FR-2.2 Detect *rename candidates*: a removed and an added column with compatible type and
  name similarity above a threshold. Emit them as `RENAME_CANDIDATE` (unresolved).
- FR-2.3 Diff is pure, deterministic and order-independent (same result for shuffled input).
- Acceptance: property tests for symmetry (add/remove inverse) and idempotence (diff of a
  snapshot with itself is empty).

### FR-3 Rule-based classification
- FR-3.1 Rules (default severity):

  | Change | Severity |
  |---|---|
  | Column added, nullable | non-breaking |
  | Column added, NOT NULL, no default | breaking (writers) |
  | Column removed | breaking |
  | Type widened (int32→int64, float32→float64, varchar length up, date→timestamp) | non-breaking |
  | Type narrowed (int64→int32, timestamp→date) | breaking |
  | Type category change (numeric↔string, string→date, etc.) | breaking |
  | Nullable → NOT NULL | breaking |
  | NOT NULL → nullable | non-breaking (breaking for consumers assuming no nulls: flag `warning`) |
  | Column reorder | non-breaking for named access; breaking for positional CSV |
  | Rename candidate | `needs_review` until resolved |

- FR-3.2 Each classified change carries: `severity`, `rule_id`, `reason`, `confidence` (1.0
  for deterministic rules).
- FR-3.3 Severity can depend on the source format (positional CSV vs named Parquet).
- Acceptance: a table-driven test covers every row of the table above.

### FR-4 LLM-assisted resolution and explanation
- FR-4.1 For `needs_review` changes (rename candidates, semantic type changes) the agent asks
  the LLM to decide between `rename`, `drop_and_add`, or `unknown`, returning a Pydantic
  `Resolution` with `confidence` and `rationale`.
- FR-4.2 The agent produces a human-readable impact explanation per breaking change and an
  executive summary for the report.
- FR-4.3 If the LLM output is invalid after 2 retries, or confidence < 0.6, the change is
  marked `needs_human_review`; the run still succeeds.
- FR-4.4 The LLM can call tools (function calling via the adapter): `get_snapshot`,
  `get_diff`, `sample_column_values` (bounded to 20 values), `validate_ddl`.
- Acceptance: Replay-adapter tests reproduce recorded runs byte-for-byte; the eval suite
  (FR-8) measures accuracy.

### FR-5 Migration proposal
- FR-5.1 Generate DDL for dialects: **DuckDB**, **Spark SQL / Delta**, **T-SQL (Fabric Warehouse)**.
- FR-5.2 Each statement is tagged `safe` (additive) or `destructive` and ordered safely
  (adds before type changes before drops).
- FR-5.3 Generated DDL is validated: DuckDB dialect by executing it against an in-memory
  DuckDB table with the baseline schema; other dialects by parsing (no execution).
- FR-5.4 The agent never executes DDL against user data.
- Acceptance: for every golden case, applying the proposed DuckDB DDL to the baseline table
  yields the current schema.

### FR-6 Reporting
- FR-6.1 `DriftReport` as JSON (schema-versioned) and as Markdown.
- FR-6.2 Report includes: overall verdict (`breaking`, `non_breaking`, `none`), change list,
  explanations, proposed DDL, run metadata.
- FR-6.3 CLI: `schemasentinel diff <baseline> <current> [--dialect ...] [--format json|md]`
  and `schemasentinel snapshot <source> -o snapshot.json`. Exit code: 0 no drift or non-breaking,
  1 breaking drift, 2 error.

### FR-7 Adapters and notifications
- FR-7.1 `SchemaSource` port with adapters: local files, Delta (local), OneLake (Delta over
  ABFSS via `deltalake` storage options; optional extra `[onelake]`).
- FR-7.2 `LLMPort` with adapters: `ClaudeCliAdapter` (subprocess `claude -p`, JSON output),
  `ReplayAdapter` (recorded transcripts), and a `FakeLLM` for unit tests.
- FR-7.3 `Notifier` port with `TelegramNotifier` and `ConsoleNotifier`. Telegram messages
  contain the verdict and top breaking changes and link/path to the report. Only the
  configured chat id is ever contacted.
- FR-7.4 A GitHub Actions workflow runs the test suite and evals in Replay mode on every push
  to `dev`.

### FR-8 Evaluation
- FR-8.1 Golden set of ≥ 30 drift cases (input pair + expected classification + expected DDL
  semantics), stored in `tests/golden/`.
- FR-8.2 Eval metrics: classification accuracy, breaking-change recall (must be 1.0 on the
  deterministic rules), rename-resolution accuracy, DDL validity rate, invalid-output rate.
- FR-8.3 `python -m schemasentinel.evals` prints a table and exits non-zero if any metric
  regresses below its threshold in `evals/thresholds.json`.

## 4. Non-functional requirements
- NFR-1 **Performance**: schema extraction from a 1 GB Parquet file < 2 s (metadata only,
  no full scan). Diff of two 500-column schemas < 100 ms.
- NFR-2 **Reliability**: an LLM failure never fails the run; the report degrades to rule-based
  results with `needs_human_review` flags.
- NFR-3 **Cost**: default local runs use only the Claude Code CLI; Replay mode uses zero LLM.
- NFR-4 **Security**: read-only access; secrets only via environment; Telegram chat-id allowlist.
- NFR-5 **Portability**: Windows and Linux; Python 3.11+.
- NFR-6 **Testability**: domain layer ≥ 90 % line coverage; no network in unit tests.

## 5. Milestones
1. **M1 — Core (deterministic)**: FR-1 (local), FR-2, FR-3, FR-6 basic, CLI.
2. **M2 — Agent**: FR-4, FR-5, LLM port + Replay, eval harness FR-8.
3. **M3 — Integrations**: Delta + OneLake adapters, Telegram notifier, CI workflow, demo.
4. **M4 — Polish**: README, architecture diagram, recorded demo, Fabric recordings
   (must finish before the Fabric trial ends on 2026-11-18).

## 6. Open questions (escalate via decision protocol if blocking)
- OQ-1 Rename similarity: plain Levenshtein vs token-based? Start with a simple normalized
  ratio in stdlib (`difflib`) to avoid a new dependency.
- OQ-2 T-SQL dialect validation without a Fabric connection: parse-only with `sqlglot`
  (risky-library check required before adding).
