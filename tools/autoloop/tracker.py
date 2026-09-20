from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path

TASK_RE = re.compile(
    r"^- \[(?P<s>[ x!~])\] (?P<id>T-\d+) \| P(?P<p>\d) \| deps: (?P<deps>[^|]*) \| (?P<title>.+?)\s*$"
)


@dataclass
class Task:
    id: str
    status: str
    priority: int
    deps: list[str]
    title: str
    detail: str = ""


class Tracker:
    def __init__(self, path: Path):
        self.path = path

    def _lines(self) -> list[str]:
        return self.path.read_text(encoding="utf-8").split("\n")

    def load(self) -> list[Task]:
        tasks: list[Task] = []
        for line in self._lines():
            m = TASK_RE.match(line)
            if m:
                deps_raw = m["deps"].strip()
                deps = [] if deps_raw in ("-", "") else [d.strip() for d in deps_raw.split(",")]
                tasks.append(Task(m["id"], m["s"], int(m["p"]), deps, m["title"]))
            elif tasks and line.startswith("  ") and line.strip():
                tasks[-1].detail += line.strip() + "\n"
            elif line.startswith("#"):
                pass
        return tasks

    def next_task(self, exclude: set[str] | None = None) -> Task | None:
        tasks = self.load()
        done = {t.id for t in tasks if t.status == "x"}
        for t in tasks:
            if t.status == " " and all(d in done for d in t.deps) and t.id not in (exclude or set()):
                return t
        return None

    def set_status(self, task_id: str, mark: str) -> None:
        lines = self._lines()
        for i, line in enumerate(lines):
            m = TASK_RE.match(line)
            if m and m["id"] == task_id:
                lines[i] = f"- [{mark}]" + line[5:]
                break
        else:
            raise KeyError(task_id)
        self.path.write_text("\n".join(lines), encoding="utf-8")

    def append_decision_log(self, entry: str) -> None:
        lines = self._lines()
        for i, line in enumerate(lines):
            if line.strip() == "## Run log":
                lines[i:i] = [entry, ""]
                break
        else:
            lines.append(entry)
        self.path.write_text("\n".join(lines), encoding="utf-8")

    def append_run_log(self, entry: str) -> None:
        text = self.path.read_text(encoding="utf-8").rstrip("\n")
        self.path.write_text(text + "\n" + entry + "\n", encoding="utf-8")
