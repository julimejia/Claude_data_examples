from __future__ import annotations

import json
from dataclasses import asdict, dataclass, field
from pathlib import Path

from .decisions import Decision


@dataclass
class State:
    path: Path
    iteration: int = 0
    offset: int = 0
    next_decision: int = 1
    hardening: int = 0
    decisions: list[Decision] = field(default_factory=list)
    sent: list[float] = field(default_factory=list)
    failures: dict[str, int] = field(default_factory=dict)

    @classmethod
    def load(cls, path: Path) -> State:
        state = cls(path=path)
        if path.is_file():
            raw = json.loads(path.read_text(encoding="utf-8"))
            state.offset = raw.get("offset", 0)
            state.next_decision = raw.get("next_decision", 1)
            state.decisions = [Decision.from_dict(d) for d in raw.get("decisions", [])]
            state.sent = raw.get("sent", [])
            state.failures = raw.get("failures", {})
            # iteration counters are per run and intentionally not restored
        return state

    def save(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        raw = {
            "offset": self.offset,
            "next_decision": self.next_decision,
            "decisions": [asdict(d) for d in self.decisions],
            "sent": self.sent[-50:],
            "failures": self.failures,
        }
        tmp = self.path.with_suffix(".tmp")
        tmp.write_text(json.dumps(raw, indent=2), encoding="utf-8")
        tmp.replace(self.path)

    def pending(self) -> list[Decision]:
        return [d for d in self.decisions if d.status == "pending"]
