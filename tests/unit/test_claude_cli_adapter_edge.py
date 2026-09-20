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


def envelope(result, **extra) -> str:
    return json.dumps({"type": "result", "is_error": False, "result": result, **extra})


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


GOOD_JSON = '{"verdict": "rename", "confidence": 0.9}'
GOOD = envelope(GOOD_JSON)


def run(a: ClaudeCliAdapter) -> Answer:
    return a.complete_structured(system="sys", prompt="p", schema=Answer)


def mk(runner, **kw) -> ClaudeCliAdapter:
    return ClaudeCliAdapter(binary="claude-fake", runner=runner, **kw)


def test_empty_stdout_is_retried() -> None:
    runner = FakeRunner(proc(""), proc("   \n"), proc(GOOD))
    assert run(mk(runner)).verdict == "rename"
    assert len(runner.calls) == 3


def test_is_error_envelope_is_retried_then_raises() -> None:
    bad = envelope(GOOD_JSON, is_error=True)
    runner = FakeRunner(proc(bad), proc(bad), proc(bad))
    with pytest.raises(LLMError):
        run(mk(runner))
    assert len(runner.calls) == 3


def test_is_error_then_success() -> None:
    runner = FakeRunner(proc(envelope("boom", is_error=True)), proc(GOOD))
    assert run(mk(runner)).confidence == 0.9


def test_result_already_a_dict_is_accepted() -> None:
    out = envelope({"verdict": "v", "confidence": 0.5})
    assert run(mk(FakeRunner(proc(out)))).verdict == "v"


def test_bare_payload_without_envelope_is_accepted() -> None:
    assert run(mk(FakeRunner(proc(GOOD_JSON)))).verdict == "rename"


@pytest.mark.parametrize("stdout", ["null", "[]", "42", '"text"', "[1, 2]", "{}"])
def test_non_object_or_empty_payloads_are_llm_error(stdout: str) -> None:
    runner = FakeRunner(proc(stdout), proc(stdout), proc(stdout))
    with pytest.raises(LLMError):
        run(mk(runner))


@pytest.mark.parametrize("result", ["null", "[]", "", "plain prose", "```\n```", "{"])
def test_bad_inner_result_is_llm_error(result: str) -> None:
    out = proc(envelope(result))
    with pytest.raises(LLMError):
        run(mk(FakeRunner(out, out, out)))


def test_result_null_is_llm_error() -> None:
    out = proc(json.dumps({"result": None, "is_error": False}))
    with pytest.raises(LLMError):
        run(mk(FakeRunner(out, out, out)))


def test_fence_without_language_and_without_closing() -> None:
    assert run(mk(FakeRunner(proc(envelope("```\n" + GOOD_JSON + "\n```"))))).verdict == "rename"
    assert run(mk(FakeRunner(proc(envelope("```json\n" + GOOD_JSON))))).verdict == "rename"


def test_surrounding_whitespace_in_result() -> None:
    assert run(mk(FakeRunner(proc(envelope("\n  " + GOOD_JSON + "  \n"))))).verdict == "rename"


def test_wrong_types_rejected() -> None:
    bad = proc(envelope('{"verdict": "x", "confidence": "high"}'))
    with pytest.raises(LLMError):
        run(mk(FakeRunner(bad, bad, bad)))


def test_extra_stdout_garbage_after_json_is_retried() -> None:
    bad = proc(GOOD + "\ntrailing")
    runner = FakeRunner(bad, proc(GOOD))
    assert run(mk(runner)).verdict == "rename"
    assert len(runner.calls) == 2


def test_max_retries_zero_means_single_attempt() -> None:
    runner = FakeRunner(proc("bad"))
    with pytest.raises(LLMError):
        run(mk(runner, max_retries=0))
    assert len(runner.calls) == 1


def test_max_retries_custom() -> None:
    runner = FakeRunner(*[proc("bad")] * 5)
    with pytest.raises(LLMError):
        run(mk(runner, max_retries=4))
    assert len(runner.calls) == 5


def test_success_on_first_attempt_does_not_retry() -> None:
    runner = FakeRunner(proc(GOOD), proc("never used"))
    run(mk(runner))
    assert len(runner.calls) == 1


def test_all_timeouts_raise_llm_error_not_timeout() -> None:
    t = subprocess.TimeoutExpired("c", 1)
    runner = FakeRunner(t, t, t)
    with pytest.raises(LLMError):
        run(mk(runner))
    assert len(runner.calls) == 3


def test_nonzero_exit_ignores_valid_stdout() -> None:
    runner = FakeRunner(proc(GOOD, 2), proc(GOOD, 2), proc(GOOD, 2))
    with pytest.raises(LLMError):
        run(mk(runner))


def test_oserror_variants_not_retried_and_wrapped() -> None:
    for exc in (PermissionError("x"), FileNotFoundError("y"), OSError("z")):
        runner = FakeRunner(exc)
        with pytest.raises(LLMError):
            run(mk(runner))
        assert len(runner.calls) == 1


def test_undecodable_output_is_llm_error() -> None:
    # subprocess.run(text=True, encoding="utf-8") raises UnicodeDecodeError on bad bytes.
    exc = UnicodeDecodeError("utf-8", b"\xff", 0, 1, "invalid start byte")
    runner = FakeRunner(exc, exc, exc)
    with pytest.raises(LLMError):
        run(mk(runner))


def test_command_contains_schema_and_system_and_prompt_via_stdin() -> None:
    runner = FakeRunner(proc(GOOD))
    ClaudeCliAdapter(binary="b", runner=runner).complete_structured(
        system="MY-SYSTEM", prompt="MY-PROMPT", schema=Answer
    )
    cmd, kwargs = runner.calls[0]
    joined = " ".join(cmd)
    assert "MY-SYSTEM" in joined
    assert "verdict" in joined and "confidence" in joined
    assert "MY-PROMPT" not in joined  # prompt goes through stdin, not argv
    assert kwargs["input"] == "MY-PROMPT"


def test_timeout_passed_through() -> None:
    runner = FakeRunner(proc(GOOD))
    mk(runner, timeout=7.5)
    run(mk(runner, timeout=7.5))
    assert runner.calls[0][1]["timeout"] == 7.5


def test_tools_are_ignored() -> None:
    runner = FakeRunner(proc(GOOD))
    a = mk(runner)
    out = a.complete_structured(system="s", prompt="p", schema=Answer, tools=[])
    assert out.verdict == "rename"


def test_unicode_prompt_roundtrip() -> None:
    runner = FakeRunner(proc(envelope('{"verdict": "ñandú ✓", "confidence": 1}')))
    out = a = mk(runner).complete_structured(system="s", prompt="日本語", schema=Answer)
    assert out.verdict == "ñandú ✓"
    assert runner.calls[0][1]["input"] == "日本語"
    assert a is out


def test_binary_resolved_from_env_when_not_given(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("CLAUDE_BIN", "/env/claude")
    runner = FakeRunner(proc(GOOD))
    ClaudeCliAdapter(runner=runner).complete_structured(system="s", prompt="p", schema=Answer)
    assert runner.calls[0][0][0] == "/env/claude"


def test_binary_missing_everywhere_raises_llm_error(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("CLAUDE_BIN", raising=False)
    monkeypatch.setattr("shutil.which", lambda name: None)
    runner = FakeRunner()
    with pytest.raises(LLMError):
        ClaudeCliAdapter(runner=runner).complete_structured(system="s", prompt="p", schema=Answer)
    assert runner.calls == []


def test_empty_claude_bin_falls_back_to_path(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("CLAUDE_BIN", "")
    monkeypatch.setattr("shutil.which", lambda name: "/usr/bin/claude")
    assert locate_claude_binary() == "/usr/bin/claude"


def test_env_beats_path(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("CLAUDE_BIN", "/env/claude")
    monkeypatch.setattr("shutil.which", lambda name: "/usr/bin/claude")
    assert locate_claude_binary() == "/env/claude"


def test_secret_not_logged_on_success_or_timeouts(caplog: pytest.LogCaptureFixture) -> None:
    secret = "sk-TOPSECRET-999"
    t = subprocess.TimeoutExpired(["claude", secret], 1)
    leaked = envelope(f'{{"verdict": "{secret}", "confidence": 1}}')
    runner = FakeRunner(t, proc(secret, 1), proc(leaked))
    with caplog.at_level(logging.DEBUG):
        mk(runner).complete_structured(system=f"s {secret}", prompt=f"p {secret}", schema=Answer)
    assert secret not in caplog.text


def test_secret_not_in_error_for_oserror_and_exit(caplog: pytest.LogCaptureFixture) -> None:
    secret = "sk-TOPSECRET-999"
    with caplog.at_level(logging.DEBUG), pytest.raises(LLMError) as info:
        run(mk(FakeRunner(OSError(f"cannot exec {secret}"))))
    assert secret not in str(info.value)
    assert secret not in caplog.text
    assert secret not in repr(info.value.__cause__ and "")


def test_stderr_content_not_logged(caplog: pytest.LogCaptureFixture) -> None:
    secret = "sk-STDERR-1"
    p = subprocess.CompletedProcess(args=[], returncode=1, stdout="", stderr=secret)
    with caplog.at_level(logging.DEBUG), pytest.raises(LLMError) as info:
        run(mk(FakeRunner(p, p, p)))
    assert secret not in caplog.text
    assert secret not in str(info.value)


def test_runner_called_with_check_false_and_no_shell() -> None:
    runner = FakeRunner(proc(GOOD))
    run(mk(runner))
    cmd, kwargs = runner.calls[0]
    assert isinstance(cmd, list)
    assert not kwargs.get("shell")
    assert kwargs.get("check") is False
