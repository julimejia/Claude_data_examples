# SchemaSentinel — Architecture

Status: v0.1. Structural changes require a decision (constitution §4) recorded as an ADR.

## 1. Style
**Modular monolith with hexagonal (ports and adapters) architecture.** One Python package,
one process, strict dependency direction: `adapters → application → domain`; `ports` are
interfaces owned by the application layer.

Why not microservices: a single-user CLI-first tool has no independent scaling or deploy
needs; process boundaries would add latency, ops cost and failure modes with no benefit.
Why not a plain monolith without ports: the LLM, storage and notification integrations are
exactly the parts that must be swapped (CLI vs Replay vs Fake; local vs OneLake; Telegram vs
console) to keep tests free and demos deployable. See ADR-0001.

## 2. Layout
```
src/schemasentinel/
  domain/            # pure, no I/O
    models.py        # SchemaSnapshot, Column, SchemaChange, DriftReport, Severity...
    diff.py          # deterministic diff + rename candidates
    rules.py         # rule table -> Severity, rule_id, reason
    types.py         # type normalization and widening/narrowing lattice
  application/
    detect_drift.py  # use case: baseline+current -> DriftReport (rules)
    resolve.py       # use case: LLM resolution of needs_review changes
    propose_migration.py  # DDL generation + validation
    explain.py       # impact explanations + summary
  ports/
    schema_source.py # SchemaSource protocol
    llm.py           # LLMPort protocol (structured output + tool calls)
    notifier.py      # Notifier protocol
    report_store.py  # ReportStore protocol
  adapters/
    sources/         # local_files.py (DuckDB), delta.py (deltalake), onelake.py
    llm/             # claude_cli.py, replay.py, fake.py
    notify/          # telegram.py, console.py
    store/           # fs_store.py
  cli.py             # composition root: wires adapters to use cases
  evals/             # eval harness (python -m schemasentinel.evals)
tests/
  unit/ integration/ golden/ recordings/
```

## 3. Ports (contracts)
```python
class SchemaSource(Protocol):
    def snapshot(self, ref: str, *, version: str | None = None) -> SchemaSnapshot: ...

class LLMPort(Protocol):
    def complete_structured(self, *, system: str, prompt: str,
                            schema: type[BaseModel], tools: list[Tool] | None = None,
                            ) -> BaseModel: ...   # raises LLMError; adapter validates JSON

class Notifier(Protocol):
    def send(self, message: Notification) -> None: ...

class ReportStore(Protocol):
    def save(self, report: DriftReport) -> str: ...   # returns location
```

## 4. Data flow
```
CLI ─► DetectDrift ─► (SchemaSource x2) ─► domain.diff ─► domain.rules
                                              │
                          needs_review? ──yes─► Resolve ─► LLMPort (validated, retried)
                                              │
                                              ▼
                         ProposeMigration ─► LLMPort (DDL draft) ─► validate (DuckDB exec / parse)
                                              │
                                              ▼
                         Explain ─► DriftReport ─► ReportStore + Notifier
```
Deterministic path (no LLM) always completes; LLM stages enrich the report and degrade to
`needs_human_review` on failure.

## 5. LLM integration
- **ClaudeCliAdapter**: runs `claude -p <prompt> --output-format json` as a subprocess
  with the JSON schema of the expected response in the prompt, parses and validates it with
  Pydantic, retries up to 2 times with the validation error appended.
  Trade-off: adds process start latency (~seconds) and depends on the local login, in exchange
  for zero API cost. Isolated behind `LLMPort` so an API adapter can replace it later.
- **ReplayAdapter**: keyed by a hash of `(system, prompt, schema name)`; loads JSON
  transcripts from `tests/recordings/`. A `--record` mode wraps a real adapter and stores
  new transcripts. Enables free CI and public demos.
- **Tools** exposed to the model are read-only and bounded (`sample_column_values` ≤ 20 values).

## 6. Engines and performance decisions
- **DuckDB** for CSV/Parquet metadata (`DESCRIBE`, `parquet_schema`) — reads footers, no full
  scan. Chosen over pandas/Polars: no in-memory load, one engine also validates DuckDB DDL.
- **deltalake (delta-rs)** for Delta schemas — reads the transaction log only; no Spark.
- Diff is O(n) over columns using dict indices; rename-candidate matching is O(a·r) over
  added × removed columns only.
- Caching: none in MVP (schemas are small); revisit only with evidence.

## 7. Configuration and secrets
`pydantic-settings`-free: a small `Settings` dataclass reading environment variables
(`TELEGRAM_BOT_TOKEN`, `TELEGRAM_CHAT_ID`, optional `ANTHROPIC_API_KEY`). `.env` loading is
done by the composition root only, never by the domain.

## 8. Testing strategy
| Level | What | Tools |
|---|---|---|
| Unit | domain diff and rules, table-driven | pytest, hypothesis-free property checks via parametrization |
| Contract | each adapter satisfies its port | shared test classes per port |
| Golden/eval | ≥ 30 drift cases with expected outcome | `evals/` harness, thresholds file |
| Integration | CLI end to end on fixture files with Replay LLM | pytest + tmp_path |

## 9. CI / deployment
GitHub Actions on `dev` pushes: install, `ruff`, `pytest`, evals in Replay mode. A static
Markdown/HTML demo report is published to GitHub Pages from recorded runs. No paid services.

## 10. Risks and trade-offs
| Risk | Mitigation |
|---|---|
| Claude CLI latency and availability | Port + Replay; deterministic core never depends on it |
| LLM invents columns or DDL | Pydantic validation; DDL executed in throwaway DuckDB; reference checks against snapshot |
| Type lattice differs per engine | Normalize to internal logical types; dialect-specific mapping in the DDL generator |
| OneLake access after trial ends | Record Fabric runs as fixtures/recordings before 2026-11-18 |
| Over-engineering | Only the ports listed above; no plugin system, no DI framework |

## 11. ADR index
- ADR-0001 — Modular monolith with hexagonal architecture (accepted, see `docs/adr/`).
