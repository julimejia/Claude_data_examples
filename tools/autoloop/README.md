# autoloop — autonomous development loop

Runs Claude Code headless (`claude -p`, your subscription, no API key) against `sdd/tracker.md`,
one task per iteration, and talks to you through a Telegram bot when it needs a decision.
Stdlib only.

## Run
```powershell
# from the project root
$env:PYTHONPATH = "tools"
python -m autoloop --check                                  # validates config, Telegram, claude binary
python -m autoloop --min-iterations 5 --max-iterations 10   # the real loop
```
Secrets are read from `~/.env` (then project `.env`, then environment): `TELEGRAM_BOT_TOKEN`, `TELEGRAM_CHAT_ID`.
The claude binary is found via `CLAUDE_BIN`, `PATH`, or the VS Code extension's bundled binary.

## Behaviour
- **Iteration** = one Claude session on the first todo task whose deps are done. Min 5, max 10 (hard cap).
  If no work is left before the minimum, it runs "review and harden" iterations.
- Works only on branch `dev`; refuses `main`/`master`. Commits per task, pushes `dev` only if `origin` exists.
  Never opens PRs. Commits are blocked if a `.env` file or a secret-looking value is staged.
- Claude may not run state-changing git commands, edit the tracker, or read `.env` files; the loop does git and
  tracker updates. Failed attempts are kept in `git stash`.
- **Decisions** (architecture style, performance trade-offs, risky libraries, requirement changes) and genuine
  questions: Claude reports them in `.autoloop/result.json`; the loop sends a Telegram message with buttons,
  marks the task `!`, and continues with independent tasks.
  - Unanswered after **15 min** → one reminder.
  - Unanswered after **1 h** → final notice and **pause**: no Claude runs, Telegram long-poll only (zero tokens).
  - Your answer (button, letter, or free text) becomes `docs/adr/ADR-NNNN-*.md` and unblocks the task.
- A task that fails twice raises a *blocker* decision (Retry / Skip).
- Anti-spam: at most 6 non-critical messages per hour; decisions and reminders always go through.
- Only the configured chat id is honoured; other chats are ignored.

## Files
`.autoloop/state.json` (decisions, offsets), `.autoloop/lock`, `.autoloop/result.json` — all git-ignored.
If a crash leaves a stale `.autoloop/lock`, delete it.

## Tests
```powershell
python -m pytest tools/autoloop/tests -q
```
