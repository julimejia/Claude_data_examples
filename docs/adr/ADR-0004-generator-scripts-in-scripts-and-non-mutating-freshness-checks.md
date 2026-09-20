# ADR-0004 - Generator scripts live in `scripts/`, and freshness checks never mutate the working tree

- Status: accepted
- Date: 2026-09-20
- Raised during: task:T-022 (3 failed dev attempts)
- Decided by: project owner (option A of ADR-0003)
- Supersedes nothing; extends [ADR-0003](ADR-0003-architect-decision-in-task-t-022.md)

## Context

T-022 ("Demo UI: static interactive page with bundled examples and results view") failed three dev
attempts on exactly one test, `tests/unit/test_demo_ui.py::test_generator_output_is_up_to_date`:

```python
before = (PUBLIC / "examples.json").read_text(encoding="utf-8")
subprocess.run([sys.executable, "tools/gen_demo_examples.py"], cwd=ROOT, check=True)
assert (PUBLIC / "examples.json").read_text(encoding="utf-8") == before
```

`tools/gen_demo_examples.py` has never existed in any commit, is not on disk, is not in `git stash list`
and is not matched by `.gitignore`. The other 630 tests pass, including the two that actually prove the
demo data is honest (`test_every_golden_case_is_an_example_and_vice_versa`,
`test_stored_results_match_deterministic_classification`).

The evidence says the dev role *wrote* the generator and the devloop harness reverted it:

- `ce62f9f` added `public/{index.html,style.css,app.js,examples.json}` and the test that invokes the
  generator — but not the generator itself.
- `56f9637` rewrote 61 lines of `public/examples.json` into a different order (by case id rather than the
  filename order `load_cases` yields), which only a generator run can produce. So a generator existed
  during the attempt and did not survive it.

Root cause: **the dev role cannot persist a file under `tools/`.** The devloop harness reverts writes
outside each role's allowlist (this is the same mechanism that limits the architect to `PLAN.md` and
`docs/`), and `tools/` holds the supervising harness itself (`tools/autoloop/`). A fourth retry of the
same spec fails identically — this is not a coding error.

Two design defects in the test compound the failure and must be fixed regardless of where the generator
lives:

1. It runs the generator with `cwd=ROOT`, so the generator **overwrites the tracked
   `public/examples.json`**. When the assertion fails, the tree is left dirty and the loop commits the
   damage (that is precisely what `56f9637` is).
2. Byte-for-byte regeneration is not required by FR-9.3/FR-9.4 or by ACCEPTANCE.md, and the comparable
   generator `tools/gen_golden.py` has no such test. Byte equality also makes the check hostage to JSON
   formatting and dict ordering rather than to content.

Options weighed (full text in ADR-0003): (A) move the generator to `scripts/` and make the check
non-mutating; (B) keep it in `tools/` with the owner committing it by hand; (C) drop the freshness test
and rely on the two content tests. The owner chose **A**, and the non-mutating principle from C is folded
into it.

## Decision

1. **`scripts/` is the home for repository maintenance and generator scripts** that a role may create and
   maintain: `scripts/gen_demo_examples.py` and, per the tracker, `scripts/check_demo_size.py` (T-023) and
   `scripts/record_replays.py` (T-028).
2. **`tools/` is reserved for the supervising harness** (`tools/autoloop/`) and for scripts the owner
   maintains by hand. `tools/gen_golden.py` stays where it is; it is not moved as part of T-022, and no
   byte-equality test is added for it.
3. **A freshness check for a generated artifact must not write into the working tree.** It regenerates
   into a temporary directory (`tmp_path`) and compares *parsed* data against the committed artifact.
4. Generator scripts take an explicit output path (`--out`) defaulting to the committed location, and
   resolve the project root from `__file__`, not from the current working directory.
5. Generated artifacts have a canonical, documented order so that regeneration is deterministic. For
   `public/examples.json`, the canonical order is **sorted by case id**.

## Consequences

- T-022 becomes implementable by the dev role without widening its write scope into the directory that
  contains its own supervising harness.
- The failing test can no longer corrupt `public/examples.json`, and a failed attempt can no longer leave
  a mangled artifact for the loop to commit.
- The freshness check tests what the AC actually asks for ("examples generated from the golden set ... by
  a script") instead of JSON byte formatting.
- `scripts/` and `tools/` now mean different things; the split is documented here and is the reason
  T-023/T-028 paths in the tracker already say `scripts/`.
- **Open prerequisite:** this only works if the dev role's write allowlist includes `scripts/`. The
  allowlist lives in the devloop harness, outside this repository, so no role can fix it. If `scripts/`
  is not writable by the dev, T-022, T-023 and T-028 all fail the same way.

## Implementation contract for T-022 (attempt 4)

`scripts/gen_demo_examples.py`

- Reads the golden cases via `schemasentinel.evals.golden.load_cases(ROOT / "tests/golden/cases")`;
  `ROOT = Path(__file__).resolve().parents[1]`.
- Computes each example's `result` with the same deterministic path the API uses (`api/diff.py:handle`),
  never by hand, so `test_stored_results_match_deterministic_classification` holds by construction.
- Emits `{"examples": [...]}` with examples **sorted by case id**; each example carries
  `id`, `baseline`, `current`, `result`, `explanation`, `ddl`.
- Labels AI output `"sample"` unless a real recording exists under `tests/recordings/`
  (`test_stored_ai_output_is_labelled_honestly` already enforces this).
- No timestamps, no randomness, no network. Writes UTF-8 with `json.dumps(..., indent=2,
  ensure_ascii=False)` plus a trailing newline.
- `--out PATH` (default `public/examples.json`) selects the destination.
- Docstring says `python scripts/gen_demo_examples.py`, matching the `gen_golden.py` convention.

`tests/unit/test_demo_ui.py::test_generator_output_is_up_to_date`

- Runs `[sys.executable, "scripts/gen_demo_examples.py", "--out", str(tmp_path / "examples.json")]` with
  `cwd=ROOT`, `check=True`.
- Asserts on parsed JSON, not bytes: the regenerated and committed payloads are equal when examples are
  keyed by id, and the committed file's id sequence equals `sorted(ids)` (so the artifact stays canonical).
- Must leave `public/examples.json` untouched on every path, pass or fail.
- Regenerate and commit `public/examples.json` in the new canonical order as part of the same task, so the
  committed artifact and the generator agree from the first run.
