from __future__ import annotations

import textwrap
from pathlib import Path

import pytest
from autoloop.config import Config
from autoloop.decisions import Decision, due_notices, match_reply
from autoloop.gitops import SecretDetected
from autoloop.loop import AutoLoop
from autoloop.runner import RunResult
from autoloop.state import State
from autoloop.telegram_client import Incoming, TelegramClient
from autoloop.tracker import Tracker

TRACKER = textwrap.dedent(
    """\
    # Tracker

    - [ ] T-001 | P1 | deps: - | First
      AC: first works
    - [ ] T-002 | P1 | deps: T-001 | Second needs decision
    - [ ] T-003 | P2 | deps: - | Independent third
    - [ ] T-004 | P2 | deps: T-002 | Fourth depends on second

    ## Decision log

    ## Run log
    """
)


class Clock:
    def __init__(self):
        self.t = 1_000_000.0

    def __call__(self):
        return self.t


class FakeTG:
    def __init__(self):
        self.sent: list[tuple[str, list | None]] = []
        self.queue: list[Incoming] = []

    def send(self, text, buttons=None):
        self.sent.append((text, buttons))
        return len(self.sent)

    def get_updates(self, offset, timeout=0):
        out, self.queue = self.queue, []
        return out, offset + len(out)

    def answer_callback(self, cid, text):
        pass


class FakeGit:
    def __init__(self):
        self.commits: list[str] = []
        self.stashes: list[str] = []
        self.secret = False

    def ensure_branch(self):
        pass

    def commit_all(self, msg):
        if self.secret and msg.startswith("feat"):
            raise SecretDetected("x")
        self.commits.append(msg)
        return True

    def stash(self, label):
        self.stashes.append(label)

    def push(self):
        return False


class ScriptedRunner:
    def __init__(self, script):
        self.script = script  # task_id -> list[RunResult]
        self.calls: list[str] = []

    def run(self, task, answered):
        self.calls.append(task.id)
        q = self.script.get(task.id, [RunResult("done", "ok")])
        return q.pop(0) if len(q) > 1 else q[0]


DECISION = {"title": "Sqlglot dependency", "context": "Needed for T-SQL parse", "recommendation": "A",
            "options": [{"id": "A", "label": "Add sqlglot", "tradeoffs": "extra dep"},
                        {"id": "B", "label": "Skip T-SQL validation", "tradeoffs": "less safe"}]}


def make(tmp_path: Path, script, min_it=5, max_it=10):
    (tmp_path / "sdd").mkdir()
    (tmp_path / "sdd" / "tracker.md").write_text(TRACKER, encoding="utf-8")
    cfg = Config(project_root=tmp_path, token="x", chat_id=1, claude_bin="claude",
                 min_iterations=min_it, max_iterations=max_it)
    clock, tg, git = Clock(), FakeTG(), FakeGit()
    state = State.load(tmp_path / ".autoloop" / "state.json")
    loop = AutoLoop(cfg, tg, Tracker(tmp_path / "sdd" / "tracker.md"), git, ScriptedRunner(script), state,
                    verify=lambda: (True, ""), clock=clock, log=lambda s: None)
    return loop, tg, git, clock


def test_decision_blocks_task_but_independent_work_continues(tmp_path):
    script = {"T-002": [RunResult("decision_needed", "needs lib", DECISION)]}
    loop, tg, git, clock = make(tmp_path, script, min_it=1, max_it=4)
    # Never answered: after T-001, T-002 blocks, T-003 runs, then loop waits. Stop by exhausting time.
    loop.tg.get_updates = lambda o, t=0: ([], o)
    loop.cfg.max_iterations = 3  # T-001, T-002, T-003
    loop.run()
    assert loop.runner.calls == ["T-001", "T-002", "T-003"]
    assert any("DECISION D001" in text for text, _ in tg.sent)
    tracker = Tracker(tmp_path / "sdd" / "tracker.md")
    status = {t.id: t.status for t in tracker.load()}
    assert status == {"T-001": "x", "T-002": "!", "T-003": "x", "T-004": " "}


def test_button_answer_writes_adr_and_unblocks(tmp_path):
    script = {"T-002": [RunResult("decision_needed", "needs lib", DECISION), RunResult("done", "ok")]}
    loop, tg, git, clock = make(tmp_path, script, min_it=1, max_it=6)
    answered = {"done": False}
    real = tg.get_updates

    def updates(offset, timeout=0):
        # answer once the decision exists and T-003 is done
        pending = loop.state.pending()
        if pending and "T-003" in loop.runner.calls and not answered["done"]:
            answered["done"] = True
            return [Incoming(1, "callback", f"dec:{pending[0].id}:A", callback_id="c")], offset + 1
        return real(offset, timeout)

    tg.get_updates = updates
    loop.run()
    assert "T-002" in loop.completed and "T-004" in loop.completed
    adrs = list((tmp_path / "docs" / "adr").glob("ADR-0001-*.md"))
    assert adrs and "Add sqlglot" in adrs[0].read_text(encoding="utf-8")
    assert "ADR-0001" in (tmp_path / "sdd" / "tracker.md").read_text(encoding="utf-8")


def test_reminder_at_15_min_and_pause_at_1h(tmp_path):
    d = Decision("D001", "T-002", "decision", "t", "c", [], "", created_at=0.0)
    assert due_notices([d], 14 * 60, 15 * 60, 3600) == []
    assert [k for _, k in due_notices([d], 15 * 60, 15 * 60, 3600)] == ["reminder"]
    d.reminder_sent = True
    assert due_notices([d], 30 * 60, 15 * 60, 3600) == []
    assert [k for _, k in due_notices([d], 3600, 15 * 60, 3600)] == ["final"]
    d.final_sent = True
    assert due_notices([d], 7200, 15 * 60, 3600) == []


def test_paused_state_stops_calling_claude(tmp_path):
    loop, tg, git, clock = make(tmp_path, {}, min_it=1, max_it=10)
    d = Decision("D001", "T-001", "decision", "t", "c", [], "", created_at=clock.t - 4000, final_sent=True)
    loop.state.decisions.append(d)
    polls = {"n": 0}

    def updates(offset, timeout=0):
        polls["n"] += 1
        if polls["n"] > 3:
            raise RuntimeError("stop test")
        return [], offset

    tg.get_updates = updates
    with pytest.raises(RuntimeError):
        loop.run()
    assert loop.runner.calls == []  # paused: no Claude runs


def test_min_iterations_triggers_hardening_when_no_tasks(tmp_path):
    (tmp_path / "sdd").mkdir()
    (tmp_path / "sdd" / "tracker.md").write_text("## Run log\n", encoding="utf-8")
    cfg = Config(project_root=tmp_path, token="x", chat_id=1, claude_bin="c", min_iterations=5, max_iterations=10)
    tg, git = FakeTG(), FakeGit()
    loop = AutoLoop(cfg, tg, Tracker(tmp_path / "sdd" / "tracker.md"), git, ScriptedRunner({}),
                    State.load(tmp_path / "s.json"), verify=lambda: (True, ""), log=lambda s: None)
    loop.run()
    assert loop.state.iteration == 5
    assert all(c.startswith("H-") for c in loop.runner.calls)


def test_max_iterations_is_a_hard_cap(tmp_path):
    loop, *_ = make(tmp_path, {}, min_it=1, max_it=2)
    loop.run()
    assert loop.state.iteration == 2


def test_two_failures_raise_blocker_and_secret_aborts_commit(tmp_path):
    script = {"T-001": [RunResult("failed", "boom")]}
    loop, tg, git, clock = make(tmp_path, script, min_it=1, max_it=2)
    loop.run()
    assert git.stashes  # failed work is stashed, not lost
    assert any("BLOCKER" in text for text, _ in tg.sent)

    loop2, tg2, git2, _ = make(tmp_path / "b" if (tmp_path / "b").mkdir() is None else tmp_path, {}, 1, 1)
    git2.secret = True
    loop2.run()
    assert any("secret-looking" in text for text, _ in tg2.sent)


def test_match_reply_text_and_reply_to(tmp_path):
    d1 = Decision("D001", "T-1", "decision", "a", "c", [], "", 1.0, message_id=10)
    d2 = Decision("D002", "T-2", "decision", "b", "c", [], "", 2.0, message_id=11)
    d, opt, text = match_reply([d1, d2], Incoming(1, "text", "use hexagonal", reply_to_message_id=11))
    assert d.id == "D002" and text == "use hexagonal"
    d, *_ = match_reply([d1, d2], Incoming(2, "text", "ok"))
    assert d.id == "D001"  # oldest by default


def test_telegram_ignores_other_chats():
    def post(url, payload, timeout):
        return {"ok": True, "result": [
            {"update_id": 5, "message": {"chat": {"id": 999}, "text": "hi"}},
            {"update_id": 6, "message": {"chat": {"id": 42}, "text": "mine"}},
        ]}

    client = TelegramClient("t", 42, post=post)
    incoming, offset = client.get_updates(0)
    assert [i.text for i in incoming] == ["mine"] and offset == 7


def test_tracker_parses_and_orders(tmp_path):
    p = tmp_path / "t.md"
    p.write_text(TRACKER, encoding="utf-8")
    tr = Tracker(p)
    assert tr.next_task().id == "T-001"
    tr.set_status("T-001", "x")
    assert tr.next_task().id == "T-002"
    assert tr.next_task({"T-002"}).id == "T-003"
    assert "first works" not in tr.load()[1].detail
