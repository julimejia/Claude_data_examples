from __future__ import annotations

import json
import subprocess
from dataclasses import dataclass
from pathlib import Path

from .config import Config
from .decisions import Decision
from .tracker import Task


@dataclass
class RunResult:
    status: str  # done | decision_needed | question | failed
    summary: str = ""
    decision: dict | None = None
    tail: str = ""


ALLOWED_TOOLS = ",".join(
    [
        "Read", "Write", "Edit", "Glob", "Grep",
        "Bash(python:*)", "Bash(python -m:*)", "Bash(.venv/Scripts/python.exe:*)", "Bash(.venv/Scripts/python:*)",
        "Bash(pip:*)", "Bash(ls:*)", "Bash(mkdir:*)",
        "Bash(git status:*)", "Bash(git diff:*)", "Bash(git log:*)",
    ]
)
DENIED_TOOLS = ",".join(["Bash(git push:*)", "Bash(git commit:*)", "Bash(git checkout:*)", "Bash(git reset:*)"])

PROMPT = """You are the autonomous developer for the SchemaSentinel project (working directory = project root).
Read sdd/constitution.md, sdd/requirements.md, sdd/architecture.md first; they are binding.

CURRENT TASK: {task_id} — {title}
{detail}
{answered}
RULES
- Implement ONLY this task, test-first for domain logic. Run tests and ruff with the project virtualenv
  (.venv; create it with `python -m venv .venv` and install with `.venv/Scripts/python.exe -m pip install -e ".[dev]"` if missing).
- Do not run git commands that change state, do not edit sdd/tracker.md, never touch or print secrets, never read .env files.
- Follow the constitution's decision protocol (section 4). If the task needs an architecture-style choice, a
  performance trade-off, a risky library (not in the allowed core) or a requirement change, DO NOT decide: do the
  independent parts, then report a decision. Routine choices are yours; do not escalate them.
- If you are blocked by a genuine doubt only the owner can answer, report a question. Do not ask for anything else.

FINISH by writing the file .autoloop/result.json (UTF-8 JSON, nothing else in it) with this shape:
{{"status": "done" | "decision_needed" | "question" | "failed",
  "summary": "one or two sentences of what changed",
  "decision": {{"title": "...", "context": "why this matters here", "recommendation": "A",
               "options": [{{"id": "A", "label": "...", "tradeoffs": "..."}}, {{"id": "B", "label": "...", "tradeoffs": "..."}}]}}}}
"decision" is only needed for decision_needed (2-3 options with trade-offs) or question (options may be empty).
"""

HARDENING_DETAIL = (
    "AC: All tracker tasks are finished or waiting on decisions. Review the existing code against the requirements, "
    "add missing tests, fix lint, tighten docs. Do not add features or change requirements."
)


def build_prompt(task: Task, answered: list[Decision]) -> str:
    ctx = ""
    if answered:
        lines = [f"- {d.id} ({d.task_id}) {d.title}: {d.answer_option or ''} {d.answer}".strip() for d in answered]
        ctx = "\nDECISIONS ALREADY MADE BY THE OWNER (binding):\n" + "\n".join(lines) + "\n"
    return PROMPT.format(task_id=task.id, title=task.title, detail=task.detail.strip(), answered=ctx)


class ClaudeRunner:
    def __init__(self, cfg: Config, log=print):
        self.cfg = cfg
        self.log = log

    @property
    def result_path(self) -> Path:
        return self.cfg.state_dir / "result.json"

    def run(self, task: Task, answered: list[Decision]) -> RunResult:
        if not self.cfg.claude_bin:
            return RunResult("failed", "claude CLI not found (set CLAUDE_BIN)")
        self.cfg.state_dir.mkdir(parents=True, exist_ok=True)
        self.result_path.unlink(missing_ok=True)
        cmd = [
            self.cfg.claude_bin, "-p", "--permission-mode", "acceptEdits",
            "--allowedTools", ALLOWED_TOOLS, "--disallowedTools", DENIED_TOOLS, "--output-format", "json",
        ]
        try:
            proc = subprocess.run(
                cmd, input=build_prompt(task, answered), cwd=self.cfg.project_root, capture_output=True,
                text=True, encoding="utf-8", errors="replace", timeout=self.cfg.iteration_timeout_s,
            )
        except subprocess.TimeoutExpired:
            return RunResult("failed", f"timeout after {self.cfg.iteration_timeout_s}s")
        tail = (proc.stdout or proc.stderr or "")[-600:]
        if not self.result_path.is_file():
            return RunResult("failed", "no result.json written", tail=tail)
        try:
            raw = json.loads(self.result_path.read_text(encoding="utf-8-sig"))
            status = raw.get("status", "failed")
            if status not in ("done", "decision_needed", "question", "failed"):
                status = "failed"
            return RunResult(status, str(raw.get("summary", "")), raw.get("decision"), tail)
        except (json.JSONDecodeError, AttributeError):
            return RunResult("failed", "result.json was not valid JSON", tail=tail)
