from pathlib import Path

from schemasentinel.adapters.llm.fake import FakeLLM
from schemasentinel.adapters.llm.replay import ReplayAdapter
from schemasentinel.application.detect_drift import build_report
from schemasentinel.application.resolve import SYSTEM, Resolve, _describe
from schemasentinel.domain.models import (
    ChangeType,
    Column,
    Decision,
    Resolution,
    SchemaSnapshot,
    Severity,
)
from schemasentinel.ports.llm import LLMError


def _report():
    def snap(name):
        return SchemaSnapshot(
            source="s",
            format="parquet",
            columns=(
                Column(name="id", data_type="BIGINT", position=0),
                Column(name=name, data_type="VARCHAR", position=1),
            ),
        )

    return build_report(snap("customer_name"), snap("cust_name"))


def _one(llm):
    out = Resolve(llm).run(_report())
    change = next(c for c in out.changes if c.change_type is ChangeType.RENAME_CANDIDATE)
    return out, change


def test_rename_resolved() -> None:
    res = Resolution(decision=Decision.RENAME, confidence=0.9, rationale="abbrev")
    out, c = _one(FakeLLM([res]))
    assert c.rule_id == "RENAME-RESOLVED" and not c.needs_human_review
    assert c.resolution == res and out.run_metadata["llm_calls"] == 1


def test_drop_and_add_resolved() -> None:
    res = Resolution(decision=Decision.DROP_AND_ADD, confidence=0.8, rationale="x")
    _, c = _one(FakeLLM([res]))
    assert c.rule_id == "DROP-AND-ADD-RESOLVED" and c.severity is Severity.BREAKING


def test_low_confidence_needs_human_review() -> None:
    res = Resolution(decision=Decision.RENAME, confidence=0.59, rationale="x")
    _, c = _one(FakeLLM([res]))
    assert c.needs_human_review and c.severity is Severity.NEEDS_REVIEW


def test_invalid_output_needs_human_review() -> None:
    _, c = _one(FakeLLM([LLMError("bad json")]))
    assert c.needs_human_review and c.resolution is None


def test_replay(tmp_path: Path) -> None:
    res = Resolution(decision=Decision.RENAME, confidence=0.9, rationale="abbrev")
    change = next(c for c in _report().changes if c.severity is Severity.NEEDS_REVIEW)
    rec = ReplayAdapter(tmp_path, record_with=FakeLLM([res]))
    rec.complete_structured(system=SYSTEM, prompt=_describe(change), schema=Resolution)
    _, c = _one(ReplayAdapter(tmp_path))
    assert c.resolution == res
