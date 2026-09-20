from __future__ import annotations

import subprocess
import time
from collections.abc import Callable
from pathlib import Path

from .config import Config
from .decisions import Decision, Option, due_notices, match_reply, write_adr
from .gitops import GitError, SecretDetected
from .runner import HARDENING_DETAIL, RunResult
from .state import State
from .tracker import Task, Tracker


def make_verifier(project_root: Path, timeout: int = 300) -> Callable[[], tuple[bool, str]]:
    """Run the project's tests with its own virtualenv. No venv yet means nothing to verify."""

    def verify() -> tuple[bool, str]:
        py = project_root / ".venv" / "Scripts" / "python.exe"
        if not py.is_file():
            py = project_root / ".venv" / "bin" / "python"
        if not py.is_file():
            return True, "no venv yet; skipped"
        try:
            proc = subprocess.run(
                [str(py), "-m", "pytest", "-q", "-x", "--no-header", "tests"],
                cwd=project_root, capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=timeout,
            )
        except subprocess.TimeoutExpired:
            return False, "pytest timeout"
        ok = proc.returncode in (0, 5)  # 5 = no tests collected
        return ok, (proc.stdout + proc.stderr)[-400:]

    return verify


class AutoLoop:
    def __init__(self, cfg: Config, tg, tracker: Tracker, git, runner, state: State, verify,
                 clock: Callable[[], float] = time.time, log: Callable[[str], None] = print):
        self.cfg, self.tg, self.tracker, self.git = cfg, tg, tracker, git
        self.runner, self.state, self.verify, self.now, self.log = runner, state, verify, clock, log
        self.completed: list[str] = []

    # ---- Telegram ---------------------------------------------------------
    def _send(self, text: str, buttons=None, critical: bool = False) -> int:
        now = self.now()
        self.state.sent = [t for t in self.state.sent if now - t < 3600]
        if not critical and len(self.state.sent) >= self.cfg.max_messages_per_hour:
            self.log("rate limit: message skipped")
            return 0
        self.state.sent.append(now)
        try:
            return self.tg.send(text, buttons)
        except Exception as exc:  # network trouble must not kill the loop
            self.log(f"telegram send failed: {type(exc).__name__}")
            return 0

    def _poll(self, timeout: int) -> None:
        incoming, self.state.offset = self.tg.get_updates(self.state.offset, timeout)
        for inc in incoming:
            m = match_reply(self.state.pending(), inc)
            if m is None:
                if inc.kind == "callback":
                    self.tg.answer_callback(inc.callback_id, "No pending decision.")
                continue
            d, option_id, text = m
            self._apply_answer(d, option_id, text)
            if inc.kind == "callback":
                self.tg.answer_callback(inc.callback_id, f"{d.id} -> {option_id}")
            else:
                self._send(f"Got it: {d.id} answered. Resuming.")
        self.state.save()

    def _reminders(self) -> None:
        for d, kind in due_notices(self.state.pending(), self.now(), self.cfg.reminder_after_s, self.cfg.final_after_s):
            if kind == "reminder":
                self._send(f"Reminder: {d.id} ({d.title}) is still waiting for your answer. "
                           f"I'm working on other tasks; I will pause in about 45 min if there's no reply.", critical=True)
                d.reminder_sent = True
            else:
                self._send(f"Paused: {d.id} ({d.title}) unanswered for 1 h. No Claude runs until you reply "
                           f"(polling Telegram only, no tokens spent).", critical=True)
                d.reminder_sent = d.final_sent = True
        self.state.save()

    # ---- Decisions --------------------------------------------------------
    def _create_decision(self, task: Task, kind: str, raw: dict, blocker: bool = False) -> Decision:
        opts = [
            Option(str(o.get("id") or chr(65 + i)).upper(), str(o.get("label", "")), str(o.get("tradeoffs", "")))
            for i, o in enumerate(raw.get("options") or [])
        ]
        d = Decision(
            id=f"D{self.state.next_decision:03d}", task_id=task.id, kind="blocker" if blocker else kind,
            title=str(raw.get("title") or task.title)[:120], context=str(raw.get("context", "")),
            options=opts, recommendation=str(raw.get("recommendation", "")), created_at=self.now(),
        )
        self.state.next_decision += 1
        buttons = [[(f"{o.id}: {o.label}"[:60], f"dec:{d.id}:{o.id}")] for o in opts]
        d.message_id = self._send(d.render(), buttons, critical=True)
        self.state.decisions.append(d)
        return d

    def _apply_answer(self, d: Decision, option_id: str, text: str) -> None:
        d.status, d.answer_option, d.answer, d.answered_at = "answered", option_id, text, self.now()
        adr_id, path = write_adr(self.cfg.project_root / "docs" / "adr", d, self.now())
        self.tracker.append_decision_log(f"- {adr_id} — {d.title} — accepted: {option_id or 'custom'} {text}".rstrip())
        if d.task_id.startswith("T-"):
            skip = d.kind == "blocker" and option_id == "S"
            if not skip:
                self.tracker.set_status(d.task_id, " ")
            self.state.failures.pop(d.task_id, None)
        try:
            self.git.commit_all(f"docs(adr): {adr_id} {d.title}")
        except GitError as exc:
            self.log(f"adr commit failed: {exc}")

    # ---- Iterations -------------------------------------------------------
    def _pick(self) -> Task | None:
        return self.tracker.next_task({d.task_id for d in self.state.pending()})

    def _iterate(self, task: Task) -> None:
        self.state.iteration += 1
        n = self.state.iteration
        self.log(f"iteration {n}/{self.cfg.max_iterations}: {task.id} {task.title}")
        self.git.ensure_branch()
        answered = [d for d in self.state.decisions if d.status == "answered"]
        res: RunResult = self.runner.run(task, answered)
        tracked = task.id.startswith("T-")
        stamp = time.strftime("%Y-%m-%d %H:%M", time.localtime(self.now()))

        if res.status in ("decision_needed", "question") and res.decision is not None:
            d = self._create_decision(task, "question" if res.status == "question" else "decision", res.decision)
            if tracked:
                self.tracker.set_status(task.id, "!")
            self.tracker.append_run_log(f"- {stamp} {task.id} blocked on {d.id}: {res.summary}")
            self._commit(f"wip({task.id}): partial work before decision {d.id}", task)
            return

        ok, detail = (True, "") if res.status != "done" else self.verify()
        if res.status == "done" and ok:
            if tracked:
                self.tracker.set_status(task.id, "x")
            self.tracker.append_run_log(f"- {stamp} {task.id} done: {res.summary}")
            if self._commit(f"feat({task.id}): {task.title}", task):
                self.completed.append(task.id)
                self.state.failures.pop(task.id, None)
                return
            if tracked:
                self.tracker.set_status(task.id, " ")

        # failure path
        reason = res.summary or detail or res.tail or "unknown"
        self.git.stash(f"autoloop-failed-{task.id}-iter{n}")
        self.tracker.append_run_log(f"- {stamp} {task.id} FAILED (kept in git stash): {reason[:200]}")
        self.git.commit_all(f"chore(loop): log failed attempt for {task.id}")
        fails = self.state.failures.get(task.id, 0) + 1
        self.state.failures[task.id] = fails
        if tracked and fails >= 2:
            self.tracker.set_status(task.id, "!")
            self._create_decision(task, "blocker", {
                "title": f"{task.id} failed {fails} times",
                "context": f"{task.title}\nLast failure: {reason[:400]}",
                "recommendation": "R",
                "options": [{"id": "R", "label": "Retry", "tradeoffs": "one more attempt with the same spec"},
                            {"id": "S", "label": "Skip task", "tradeoffs": "dependent tasks stay blocked"}],
            }, blocker=True)

    def _commit(self, message: str, task: Task) -> bool:
        try:
            changed = self.git.commit_all(message)
            if changed:
                self.git.push()
            return True
        except SecretDetected:
            self._send(f"Aborted commit for {task.id}: a secret-looking value was staged. Nothing was committed.",
                       critical=True)
            return False

    # ---- Main loop --------------------------------------------------------
    def run(self) -> int:
        self._send(f"Autoloop started on branch {self.cfg.branch}: {self.cfg.min_iterations}-"
                   f"{self.cfg.max_iterations} iterations.")
        try:
            while self.state.iteration < self.cfg.max_iterations:
                self._poll(0)
                self._reminders()
                paused = any(d.final_sent for d in self.state.pending())
                task = None if paused else self._pick()
                if task is None:
                    if self.state.pending():
                        self._poll(25)  # waiting: Telegram long-poll only, no Claude tokens
                        continue
                    if self.state.iteration >= self.cfg.min_iterations:
                        break
                    self.state.hardening += 1
                    task = Task(f"H-{self.state.hardening}", " ", 3, [], "Review and harden existing work",
                                HARDENING_DETAIL)
                self._iterate(task)
                self.state.save()
        except Exception as exc:
            self.state.save()
            self._send(f"Autoloop crashed: {type(exc).__name__}: {str(exc)[:200]}", critical=True)
            raise
        pending = self.state.pending()
        self._send(f"Autoloop finished: {self.state.iteration} iterations, {len(self.completed)} tasks done "
                   f"({', '.join(self.completed) or 'none'}), {len(pending)} decisions pending.")
        self.state.save()
        return 0
