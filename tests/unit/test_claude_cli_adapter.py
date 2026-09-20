import json
import logging
import subprocess

import pytest
from pydantic import BaseModel

from schemasentinel.adapters.llm.claude_cli import ClaudeCliAdapter, locate_claude_binary
from schemasentinel.ports.llm import LLMError


class Answer(BaseModel):
    verdict: str
    confidence: float


def proc(stdout: str, code: int = 0) -> subprocess.CompletedProcess:
    return subprocess.CompletedProcess(args=[], returncode=code, stdout=stdout, stderr="")


def envelope(result: str) -> str:
    return json.dumps({"type": "result", "is_error": False, "result": result})


class FakeRunner:
    def __init__(self, *outcomes):
        self.outcomes = list(outcomes)
        self.calls: list[tuple[list[str], dict]] = []

    def __call__(self, cmd, **kwargs):
        self.calls.append((cmd, kwargs))
        outcome = self.outcomes.pop(0)
        if isinstance(outcome, Exception):
            raise outcome
        return outcome


GOOD = envelope('{"verdict": "rename", "confidence": 0.9}')


def adapter(runner, **kw) -> ClaudeCliAdapter:
    return ClaudeCliAdapter(binary="claude-fake", runner=runner, **kw)


def run(a: ClaudeCliAdapter) -> Answer:
    return a.complete_structured(system="sys", prompt="the prompt", schema=Answer)


def test_happy_path_parses_and_builds_command() -> None:
    runner = FakeRunner(proc(GOOD))
    result = run(adapter(runner))
    assert result == Answer(verdict="rename", confidence=0.9)
    cmd, kwargs = runner.calls[0]
    assert cmd[:4] == ["claude-fake", "-p", "--output-format", "json"]
    assert kwargs["input"] == "the prompt"
    assert kwargs["timeout"] > 0


def test_code_fenced_result_is_accepted() -> None:
    fenced = envelope('```json\n{"verdict": "x", "confidence": 1}\n```')
    assert run(adapter(FakeRunner(proc(fenced)))).verdict == "x"


def test_retries_twice_then_succeeds() -> None:
    runner = FakeRunner(proc("not json"), proc(envelope('{"verdict": 1}')), proc(GOOD))
    assert run(adapter(runner)).confidence == 0.9
    assert len(runner.calls) == 3


def test_fails_after_two_retries() -> None:
    runner = FakeRunner(proc("bad"), proc("bad"), proc("bad"))
    with pytest.raises(LLMError):
        run(adapter(runner))
    assert len(runner.calls) == 3


def test_timeout_and_nonzero_exit_are_retried() -> None:
    runner = FakeRunner(subprocess.TimeoutExpired("c", 1), proc("", 1), proc(GOOD))
    assert run(adapter(runner)).verdict == "rename"


def test_missing_binary_raises_llm_error() -> None:
    runner = FakeRunner(FileNotFoundError("nope"))
    with pytest.raises(LLMError):
        run(adapter(runner))


def test_locate_binary_prefers_env(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("CLAUDE_BIN", "/opt/custom/claude")
    assert locate_claude_binary() == "/opt/custom/claude"


def test_locate_binary_uses_path(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("CLAUDE_BIN", raising=False)
    monkeypatch.setattr("shutil.which", lambda name: "/usr/bin/claude")
    assert locate_claude_binary() == "/usr/bin/claude"
    monkeypatch.setattr("shutil.which", lambda name: None)
    with pytest.raises(LLMError):
        locate_claude_binary()


def test_never_logs_prompt_or_output(caplog: pytest.LogCaptureFixture) -> None:
    secret = "sk-SECRET-123"
    runner = FakeRunner(proc(envelope(secret)), proc(secret), proc(secret))
    a = ClaudeCliAdapter(binary="claude-fake", runner=runner)
    with caplog.at_level(logging.DEBUG), pytest.raises(LLMError) as info:
        a.complete_structured(system="s", prompt=f"key {secret}", schema=Answer)
    assert secret not in caplog.text
    assert secret not in str(info.value)
