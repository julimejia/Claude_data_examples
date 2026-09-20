# SchemaSentinel — Constitution

Non-negotiable principles. Every spec, task and line of code must comply. If a task
would violate one of these, stop and raise a decision (see "Decision protocol").

## 1. Purpose
SchemaSentinel detects schema drift between two versions of a tabular dataset
(CSV, Parquet, Delta), classifies each change as **breaking** or **non-breaking**,
explains the impact, and proposes a migration as DDL. It is a portfolio project that
demonstrates a *reliable* AI agent applied to data engineering.

## 2. Principles

1. **Deterministic first, LLM second.** Schema diffing and rule-based classification
   are plain, tested Python. The LLM is used only where code cannot decide: resolving
   ambiguous changes (e.g. probable renames), explaining impact in prose, and drafting
   DDL that is then validated. Never use an LLM for something a `dict` comparison can do.
2. **Every LLM output is validated.** Responses must parse into Pydantic models. Invalid
   output triggers a bounded retry, then a safe fallback (`needs_human_review`). Free text
   is never trusted as data.
3. **Read-only on data.** The agent reads schemas and (optionally) small samples. It never
   writes, alters or deletes user data. DDL is *proposed*, never executed.
4. **Hexagonal architecture.** Domain has no I/O and no third-party imports beyond Pydantic.
   Everything external (files, Delta, OneLake, LLM, Telegram) sits behind a port and is
   swappable via an adapter.
5. **Evals are the definition of done.** A change to classification or prompts is
   accepted only if the golden-set evals do not regress.
6. **Reproducible and free.** Runs locally with no paid service. LLM calls go through the
   Claude Code CLI adapter locally, or the Replay adapter (recorded transcripts) in CI and
   public demos. No network needed for tests.
7. **No secrets in the repo.** Credentials live in `.env` / environment only. `.env.example`
   contains placeholders. Logs never print tokens.
8. **Small, boring dependencies.** Allowed core: `pydantic`, `duckdb`, `deltalake`,
   `pytest`, `ruff`. Anything else is a *risky library* decision (see below).
9. **Observable.** Every run emits structured logs (JSON lines) with run id, source,
   change counts, LLM calls, latency and outcome.

## 3. Engineering rules
- Python 3.11+, type hints everywhere, `ruff` clean, tests with `pytest`.
- `src/schemasentinel/` layout; domain in `domain/`, use cases in `application/`,
  contracts in `ports/`, implementations in `adapters/`.
- One logical change per commit, Conventional Commits (`feat:`, `fix:`, `test:`, `docs:`).
- Work happens on `dev` (or feature branches off `dev`). **Never commit or push to `main`.**
  No pull requests are opened automatically.
- Tests first for domain rules; adapters get contract tests against their port.
- Public functions have docstrings only when behaviour is non-obvious.

## 4. Decision protocol (used by the autonomous loop)
An agent must **not** silently decide the following; it writes a decision request and
continues with other work:
- **Architecture style or structure**: hexagonal vs monolith vs microservices, layering,
  introducing a new service or process boundary.
- **Performance trade-offs**: memory vs latency, eager vs lazy, engine choice
  (e.g. DuckDB vs pandas/Polars), caching, concurrency.
- **Risky library choices**: adding a dependency outside the allowed core when it is
  unmaintained (>12 months without release), has a restrictive licence (GPL/AGPL), has
  known CVEs, is large/heavy, or is a single-maintainer project on a critical path.
- **Anything that changes a requirement** in `requirements.md`.

Decision requests use the format in `docs/adr/pending/` and, once answered, become an
accepted ADR in `docs/adr/`. Routine choices (naming, test layout, small refactors) do
not need a decision and must not be escalated.

## 5. Communication policy
Telegram is for architectural decisions and for genuine blockers or requirement doubts.
Do not spam: batch questions, no per-step progress pings, at most one notification per
decision plus the defined reminders.

## 6. Definition of done (per task)
- Acceptance criteria in `requirements.md` for the task are met.
- Tests added and passing, `ruff` clean.
- `tracker.md` updated (status, notes).
- No secrets, no writes to `main`.
