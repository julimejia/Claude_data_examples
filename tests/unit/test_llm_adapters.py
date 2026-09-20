import json
from pathlib import Path

import pytest
from pydantic import BaseModel

from schemasentinel.adapters.llm.fake import FakeLLM
from schemasentinel.adapters.llm.replay import ReplayAdapter, recording_key
from schemasentinel.ports.llm import LLMError, LLMPort


class Answer(BaseModel):
    verdict: str
    confidence: float


class Other(BaseModel):
    verdict: str
    confidence: float


def call(llm: LLMPort, system: str = "s", prompt: str = "p", schema: type = Answer):
    return llm.complete_structured(system=system, prompt=prompt, schema=schema)


def test_key_is_deterministic_and_sensitive() -> None:
    k = recording_key("s", "p", "Answer")
    assert k == recording_key("s", "p", "Answer")
    others = [
        recording_key("s2", "p", "Answer"),
        recording_key("s", "p2", "Answer"),
        recording_key("s", "p", "Other"),
        recording_key("sp", "", "Answer"),
    ]
    assert len({k, *others}) == 5


def test_fake_returns_in_order_and_records_calls() -> None:
    a, b = Answer(verdict="a", confidence=1), Answer(verdict="b", confidence=0.5)
    fake = FakeLLM([a, b])
    assert call(fake) == a
    assert call(fake, prompt="q") == b
    assert [c.prompt for c in fake.calls] == ["p", "q"]
    with pytest.raises(LLMError):
        call(fake)


def test_fake_raises_queued_exception_and_checks_type() -> None:
    fake = FakeLLM([LLMError("boom"), Other(verdict="x", confidence=0)])
    with pytest.raises(LLMError, match="boom"):
        call(fake)
    with pytest.raises(LLMError):
        call(fake)


def test_replay_missing_recording_raises(tmp_path: Path) -> None:
    with pytest.raises(LLMError, match="No recording"):
        call(ReplayAdapter(tmp_path))


def test_record_then_replay_roundtrip(tmp_path: Path) -> None:
    expected = Answer(verdict="rename", confidence=0.9)
    fake = FakeLLM([expected])
    assert call(ReplayAdapter(tmp_path, record_with=fake)) == expected
    files = list(tmp_path.glob("*.json"))
    assert [f.stem for f in files] == [recording_key("s", "p", "Answer")]
    assert json.loads(files[0].read_text(encoding="utf-8"))["response"] == expected.model_dump()

    replay = ReplayAdapter(tmp_path)
    first, second = call(replay), call(replay)
    assert first == second == expected
    assert len(fake.calls) == 1  # replay never hits the recorder


def test_record_mode_reuses_existing_recording(tmp_path: Path) -> None:
    fake = FakeLLM([Answer(verdict="a", confidence=1)])
    rec = ReplayAdapter(tmp_path, record_with=fake)
    call(rec)
    call(rec)
    assert len(fake.calls) == 1


def test_schema_name_is_part_of_key(tmp_path: Path) -> None:
    recorder = ReplayAdapter(tmp_path, record_with=FakeLLM([Answer(verdict="a", confidence=1)]))
    call(recorder)
    with pytest.raises(LLMError):
        call(ReplayAdapter(tmp_path), schema=Other)


def test_corrupt_recording_raises_llm_error(tmp_path: Path) -> None:
    (tmp_path / f"{recording_key('s', 'p', 'Answer')}.json").write_text(
        json.dumps({"response": {"verdict": 1}}), encoding="utf-8"
    )
    with pytest.raises(LLMError, match="Invalid recording"):
        call(ReplayAdapter(tmp_path))
