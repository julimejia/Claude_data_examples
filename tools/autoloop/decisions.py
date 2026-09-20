from __future__ import annotations

import re
import time
from dataclasses import dataclass
from pathlib import Path

from .telegram_client import Incoming


@dataclass
class Option:
    id: str
    label: str
    tradeoffs: str = ""


@dataclass
class Decision:
    id: str
    task_id: str
    kind: str  # decision | question | blocker
    title: str
    context: str
    options: list[Option]
    recommendation: str
    created_at: float
    status: str = "pending"  # pending | answered
    answer: str = ""
    answer_option: str = ""
    answered_at: float = 0.0
    reminder_sent: bool = False
    final_sent: bool = False
    message_id: int = 0

    @classmethod
    def from_dict(cls, d: dict) -> Decision:
        d = dict(d)
        d["options"] = [Option(**o) for o in d.get("options", [])]
        return cls(**d)

    def render(self) -> str:
        head = {"decision": "DECISION", "question": "QUESTION", "blocker": "BLOCKER"}.get(self.kind, "DECISION")
        parts = [f"[{head} {self.id}] {self.task_id} - {self.title}", "", self.context.strip()]
        if self.options:
            parts += ["", "Options:"]
            for o in self.options:
                parts.append(f"{o.id}) {o.label}" + (f" - {o.tradeoffs}" if o.tradeoffs else ""))
            if self.recommendation:
                parts += ["", f"Recommendation: {self.recommendation}"]
            parts += ["", "Tap a button, or reply with the option letter or free text."]
        else:
            parts += ["", "Reply to this message with your answer."]
        parts += ["No answer: I keep going with unrelated tasks, remind you in 15 min, and pause after 1 h."]
        return "\n".join(parts)


def due_notices(decisions: list[Decision], now: float, reminder_after: float, final_after: float):
    out: list[tuple[Decision, str]] = []
    for d in decisions:
        if d.status != "pending":
            continue
        age = now - d.created_at
        if age >= final_after and not d.final_sent:
            out.append((d, "final"))
        elif age >= reminder_after and not d.reminder_sent and not d.final_sent:
            out.append((d, "reminder"))
    return out


def match_reply(pending: list[Decision], inc: Incoming) -> tuple[Decision, str, str] | None:
    """Return (decision, option_id, free_text) for an incoming message, or None."""
    if not pending:
        return None
    if inc.kind == "callback":
        m = re.fullmatch(r"dec:(D\d+):([A-Za-z0-9]+)", inc.text)
        if not m:
            return None
        for d in pending:
            if d.id == m[1]:
                return d, m[2].upper(), ""
        return None
    target = next((d for d in pending if inc.reply_to_message_id and d.message_id == inc.reply_to_message_id), None)
    target = target or sorted(pending, key=lambda d: d.created_at)[0]
    text = inc.text.strip()
    for o in target.options:
        if text.upper() == o.id or text.lower() == o.label.lower():
            return target, o.id, ""
    return target, "", text


def _slug(title: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", title.lower()).strip("-")[:50] or "decision"


def next_adr_number(adr_dir: Path) -> int:
    nums = [int(m[1]) for p in adr_dir.glob("ADR-*.md") if (m := re.match(r"ADR-(\d+)-", p.name))]
    return max(nums, default=0) + 1


def write_adr(adr_dir: Path, d: Decision, now: float | None = None) -> tuple[str, Path]:
    adr_dir.mkdir(parents=True, exist_ok=True)
    number = next_adr_number(adr_dir)
    adr_id = f"ADR-{number:04d}"
    path = adr_dir / f"{adr_id}-{_slug(d.title)}.md"
    date = time.strftime("%Y-%m-%d", time.localtime(now or time.time()))
    chosen = next((o for o in d.options if o.id == d.answer_option), None)
    decision_line = f"{chosen.id}) {chosen.label}" if chosen else f"Custom: {d.answer}"
    lines = [
        f"# {adr_id} — {d.title}",
        "",
        "- Status: accepted",
        f"- Date: {date}",
        f"- Raised by: autonomous loop while working on {d.task_id} ({d.kind} {d.id})",
        "- Decided by: project owner via Telegram",
        "",
        "## Context",
        d.context.strip(),
        "",
        "## Options",
    ]
    lines += [f"- **{o.id}) {o.label}** — {o.tradeoffs}" for o in d.options] or ["- (open question)"]
    if d.recommendation:
        lines += ["", f"Recommendation at the time: {d.recommendation}"]
    lines += ["", "## Decision", decision_line]
    if chosen and d.answer:
        lines.append(f"Note: {d.answer}")
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return adr_id, path
