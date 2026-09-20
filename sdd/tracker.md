# SchemaSentinel — Tracker

Source of truth for the autonomous loop. **Do not change the line format**: the loop parses it.

Line format:
`- [S] T-NNN | P<priority> | deps: T-XXX,T-YYY or - | Title`
followed by indented `AC:` (acceptance criteria) and optional `Notes:` lines.

Status marks `S`: `" "` todo · `x` done · `!` blocked waiting for a decision · `~` in progress.
Priority: P1 highest. The loop picks the first task, in file order, that is todo with all
deps done and that is not blocked by a pending decision.

Requirements references (FR-x, NFR-x) point to `requirements.md`.

## M1 — Core (deterministic)

- [x] T-001 | P1 | deps: - | Scaffold project: pyproject.toml, package skeleton, ruff + pytest config, README stub
  AC: `python -m pytest` runs (0 tests ok), `ruff check` clean, package importable as `schemasentinel`; dependencies limited to the constitution's allowed core.
- [ ] T-002 | P1 | deps: T-001 | Domain models (SchemaSnapshot, Column, SchemaChange, Severity, DriftReport) with JSON round-trip
  AC: FR-1.5; Pydantic models are frozen; round-trip tests pass; recursive nested types via dotted paths (FR-1.4).
- [ ] T-003 | P1 | deps: T-002 | Logical type normalization and widening/narrowing lattice
  AC: maps DuckDB/Parquet/Delta type names to logical types; table-driven tests for widen/narrow/category-change.
- [ ] T-004 | P1 | deps: T-002,T-003 | Deterministic diff engine with rename candidates
  AC: FR-2.1–2.3; diff(x, x) is empty; add/remove symmetry test; order-independent. Rename similarity uses stdlib `difflib` (OQ-1).
- [ ] T-005 | P1 | deps: T-004 | Rule-based classifier (table in FR-3.1)
  AC: FR-3.1–3.3; table-driven test covering every rule row; each change has rule_id, reason, confidence.
- [ ] T-006 | P1 | deps: T-002 | Local files SchemaSource: CSV and Parquet via DuckDB
  AC: FR-1.1, FR-1.2; fixtures under tests/fixtures; metadata-only read for Parquet (no full scan).
- [ ] T-007 | P1 | deps: T-005,T-006 | DetectDrift use case + JSON and Markdown report
  AC: FR-6.1, FR-6.2; report has verdict, changes, metadata; snapshot tests for the Markdown output.
- [ ] T-008 | P2 | deps: T-007 | CLI: `snapshot` and `diff` commands with exit codes
  AC: FR-6.3; integration test via subprocess; exit 0/1/2 semantics verified.
- [ ] T-009 | P2 | deps: T-007 | Golden set (≥ 30 cases) and eval harness with thresholds
  AC: FR-8.1–8.3; `python -m schemasentinel.evals` prints metrics and fails on regression; breaking-change recall 1.0.

## M2 — Agent

- [ ] T-010 | P1 | deps: T-002 | LLMPort, FakeLLM and ReplayAdapter (+ record mode)
  AC: FR-7.2; Replay is deterministic keyed by hash of (system, prompt, schema); unit tests without network.
- [ ] T-011 | P2 | deps: T-010 | ClaudeCliAdapter (subprocess `claude -p`, JSON validation, 2 retries)
  AC: FR-7.2, FR-4.3; tested with a fake subprocess; locates the binary via CLAUDE_BIN env or PATH; never logs secrets.
  Notes: any decision about process handling or timeouts is routine; do not escalate.
- [ ] T-012 | P2 | deps: T-005,T-010 | Resolve use case: LLM decides rename vs drop_and_add
  AC: FR-4.1, FR-4.3; low confidence or invalid output → needs_human_review; Replay tests.
- [ ] T-013 | P2 | deps: T-005,T-010 | ProposeMigration: DDL for DuckDB, Spark SQL/Delta, T-SQL with validation
  AC: FR-5.1–5.4; DuckDB DDL validated by execution on an in-memory baseline table; ordering safe-before-destructive.
  Notes: validating T-SQL/Spark by parsing likely needs `sqlglot` → this is a RISKY LIBRARY decision (OQ-2); raise a decision request instead of adding it silently.
- [ ] T-014 | P3 | deps: T-012,T-013 | Explain use case: per-change impact and executive summary
  AC: FR-4.2; output validated by Pydantic; degrades gracefully.
- [ ] T-015 | P2 | deps: T-009,T-012,T-013 | Extend evals with resolution accuracy, DDL validity rate, invalid-output rate
  AC: FR-8.2; thresholds in evals/thresholds.json; Replay-mode reproducible.

## M3 — Integrations

- [ ] T-016 | P2 | deps: T-006 | Delta SchemaSource (deltalake) with version/timestamp
  AC: FR-1.3; fixtures created with deltalake in tmp_path; reads log only.
- [ ] T-017 | P2 | deps: T-007 | Notifier port with ConsoleNotifier and TelegramNotifier
  AC: FR-7.3; chat-id allowlist enforced; HTTP layer faked in tests; token never logged. Use stdlib `urllib`, no new dependency.
- [ ] T-018 | P3 | deps: T-008,T-015 | GitHub Actions workflow: ruff, pytest, evals in Replay mode
  AC: FR-7.4; workflow file valid YAML in .github/workflows/ci.yml, triggers on push to dev.
- [ ] T-019 | P3 | deps: T-016 | OneLake SchemaSource adapter (optional extra)
  AC: FR-7.1; unit-tested with a fake storage layer; documented setup; real Fabric recordings deferred (needs the user's tenant, before 2026-11-18).

## M4 — Polish

- [ ] T-020 | P3 | deps: T-014,T-017 | README with architecture diagram, usage and demo report
  AC: README explains problem, architecture (mermaid), quick start, eval results table.

## Decision log
(The loop appends resolved decisions here. Full text lives in `docs/adr/`.)
- ADR-0001 — Modular monolith with hexagonal architecture — accepted 2026-09-19 (pre-approved in SDD).

## Run log
(The loop appends one line per iteration: timestamp, task, outcome.)
- 2026-09-19 19:13 T-001 done: Added pyproject.toml (pydantic, duckdb, deltalake; dev: pytest, ruff; ruff and pytest config, tools/ excluded from ruff), src/schemasentinel package skeleton with domain/application/ports/adapters/evals subpackages, tests/unit, and README stub. ruff check is clean, pytest collects 0 tests, and the package imports.
