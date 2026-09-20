# ADR-0006 - Drop the generator freshness test for `public/examples.json`; the content tests are the guarantee

- Status: accepted
- Date: 2026-09-20
- Raised during: task:T-022 (3 failed dev attempts)
- Decided by: project owner via Telegram (answer "3" to the deliberation recorded in
  [ADR-0005](ADR-0005-architect-decision-in-task-t-022.md))
- Supersedes [ADR-0004](ADR-0004-generator-scripts-in-scripts-and-non-mutating-freshness-checks.md) **in part**
  (its implementation contract for T-022); extends [ADR-0003](ADR-0003-architect-decision-in-task-t-022.md)

## Context

T-022 fails on exactly one test, `tests/unit/test_demo_ui.py::test_generator_output_is_up_to_date`, which
shells out to `tools/gen_demo_examples.py` — a file that has never existed in any commit. The root cause is
established in ADR-0004 and unchanged: the dev role writes the generator during the attempt and the devloop
harness's per-role revert removes it, because `tools/` is outside the dev's write allowlist. The blocker is
the revert, not a stash, not `.gitignore`, and not a coding error; `gitops.py` commits with `git add -A`, the
stash list is empty, and `.gitignore` matches neither `tools/` nor `scripts/`. A fourth attempt at the same
spec would fail identically.

ADR-0004 chose option A (move the generator to `scripts/`) but left an open prerequisite: the dev role's
allowlist must include `scripts/`, and that allowlist lives in the devloop harness, outside this repository,
so no role can change it. ADR-0005 put three ways forward to the owner: (1) widen the allowlist to `scripts/`,
(2) have the owner hand-commit the generator, (3) fall back to option C — delete the freshness test and rely
on the two content tests. The owner chose **(3)**.

What the binding documents actually require matters here, and it is narrower than the tracker's wording:

- `sdd/requirements.md` FR-9.3 requires "a picker of bundled examples **derived from the golden set**". It
  does not name a script. Its acceptance line requires "a static check proves every example referenced by the
  UI exists".
- `ACCEPTANCE.md` has no criterion for FR-9 example generation at all.
- Only `sdd/tracker.md`'s T-022 line says "examples generated from the golden set into a JSON file by a
  script".

So the requirement-level acceptance for FR-9.3/FR-9.4 is met without a generator; it is the tracker's
phrasing that is left partly unmet.

## Decision

1. **Delete `test_generator_output_is_up_to_date`.** No generator script is written for T-022, in `tools/`
   or in `scripts/`. `public/examples.json` is a committed, hand-maintained artifact.
2. **The two content tests are the standing guarantee** that the artifact stays derived from the golden set:
   - `test_every_golden_case_is_an_example_and_vice_versa` asserts set equality between example ids and
     golden case ids, so adding or removing a golden case fails the suite until `examples.json` is updated;
   - `test_stored_results_match_deterministic_classification` recomputes every stored result through
     `api/diff.py:handle` and compares verdict and changes against both the fresh run and
     `case.expected`, so a stored result cannot drift from the deterministic classification.
   Together these make the hand-maintained file observationally equivalent to a generated one for every
   field the acceptance criteria constrain. What is lost is only the guarantee that a *script* can reproduce
   the file byte-for-byte, plus the drift check on the two free-text fields (`explanation`, `ddl`), which
   `test_stored_ai_output_is_labelled_honestly` still constrains for labelling.
3. **ADR-0004 stays in force for `scripts/` as a naming convention** — `scripts/` is for role-maintained
   repository scripts, `tools/` for the supervising harness — and its principle that a freshness check must
   never write into the working tree applies to any such check added later. Only ADR-0004's T-022
   implementation contract (write `scripts/gen_demo_examples.py`, add a non-mutating freshness test,
   regenerate `examples.json` in canonical order) is withdrawn.
4. **`public/examples.json` is left exactly as committed.** No reordering, no regeneration. Nothing tests its
   order now, and a gratuitous rewrite of the artifact is what produced the mangled diff in `56f9637`.

## Implementation contract for T-022 (attempt 4)

Scope is deliberately minimal; the dev should change nothing else.

- In `tests/unit/test_demo_ui.py`: remove the whole `test_generator_output_is_up_to_date` function and the
  now-unused `import subprocess` (it is the only use in the file; `sys` stays, it is used for
  `sys.path.insert`). Leave the other four tests untouched.
- Do **not** create `tools/gen_demo_examples.py` or `scripts/gen_demo_examples.py`.
- Do **not** modify `public/examples.json`, `index.html`, `style.css` or `app.js`; the 630 passing tests
  already cover them (self-containment, viewport, dark mode, example/golden parity, deterministic results,
  honest AI labels).
- Done when `pytest` is fully green (631 collected, 0 failed) and `ruff check src tests` is clean.

## Consequences

- T-022 is unblocked without touching the harness allowlist, and without any role writing outside its scope.
- The "generated by a script" phrasing in the tracker's T-022 line is knowingly not met. This ADR is the
  record of that gap; the requirement-level acceptance (FR-9.3/FR-9.4 and the FR-9 acceptance line in
  `sdd/requirements.md`) is met. `sdd/` is binding context and is not edited to match.
- Adding a golden case now requires a manual edit of `public/examples.json`. The suite fails loudly until
  that edit is made, so the drift cannot ship silently — it just costs a hand edit instead of a script run.
- **The underlying blocker is not resolved, only routed around.** T-023 requires
  `scripts/check_demo_size.py`, and unlike T-022 that script is required at the *requirement* level (FR-9.5:
  "a script checks that the function bundle stays far below Vercel's size limit"), so option C is not
  available there — T-023 will fail the same revert unless `scripts/` is added to the dev's write allowlist
  or the owner hand-commits the file. T-028's `scripts/record_replays.py` is the same situation, and the
  tracker already notes the owner must run that script by hand. Expect this decision to return at T-023.
