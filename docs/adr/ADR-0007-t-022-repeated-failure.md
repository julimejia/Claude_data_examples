# ADR-0007 - T-022 repeated failure

- Status: accepted
- Date: 2026-09-20
- Raised during: task:T-022
- Decided by: project owner via Telegram

## Context
Owner chose option 3 (option C). Recorded as ADR-0006: the freshness test is deleted, no generator script is written, and the two content tests become the standing guarantee that public/examples.json stays derived from the golden set. ADR-0004 is marked superseded in part (its T-022 contract withdrawn; the scripts/ vs tools/ split and the non-mutating-check principle stay in force) and ADR-0005's bare '3' now names the option and links the write-up. The dev can retry T-022 against ADR-0006's contract immediately. Flagged but not escalated: the harness-revert blocker is routed around, not fixed, and T-023 will hit it again with no fallback available because FR-9.5 requires scripts/check_demo_size.py at requirement level.

## Decision
Then revert change and try opt 2
