import pytest

from schemasentinel.adapters.llm.fake import FakeLLM
from schemasentinel.application.detect_drift import build_report
from schemasentinel.application.explain import IMPACT_SYSTEM, SUMMARY_SYSTEM, Explain
from schemasentinel.domain.models import (
    Column,
    ImpactText,
    SchemaSnapshot,
    Severity,
    SummaryText,
)
from schemasentinel.ports.llm import LLMError


def _snap(*cols):
    return SchemaSnapshot(
        source="s",
        format="parquet",
        columns=tuple(Column(name=n, data_type=t, position=i) for i, (n, t) in enumerate(cols)),
    )


def _breaking_report(*dropped):
    base = [("id", "BIGINT")] + [(n, "BIGINT") for n in dropped]
    return build_report(_snap(*base), _snap(("id", "BIGINT")))


def test_no_changes_only_summary_call() -> None:
    same = _snap(("id", "BIGINT"))
    llm = FakeLLM([SummaryText(summary="Nothing changed.")])
    out = Explain(llm).run(build_report(same, same))
    assert out.impacts == ()
    assert out.summary == "Nothing changed."
    assert not out.degraded
    assert len(llm.calls) == 1
    assert "(none)" in llm.calls[0].prompt


def test_no_changes_fallback_summary() -> None:
    same = _snap(("id", "BIGINT"))
    out = Explain(FakeLLM([LLMError("x")])).run(build_report(same, same))
    assert out.degraded
    assert "0 change(s)" in out.summary and "0 breaking" in out.summary


def test_non_breaking_changes_get_no_impact_call() -> None:
    report = build_report(_snap(("id", "BIGINT")), _snap(("id", "BIGINT"), ("extra", "VARCHAR")))
    assert all(c.severity is not Severity.BREAKING for c in report.changes)
    assert report.changes
    llm = FakeLLM([SummaryText(summary="ok")])
    out = Explain(llm).run(report)
    assert out.impacts == ()
    assert len(llm.calls) == 1
    assert llm.calls[0].schema is SummaryText


def test_multiple_breaking_impacts_in_change_order_then_summary() -> None:
    report = _breaking_report("a", "b", "c")
    breaking = [c.path for c in report.changes if c.severity is Severity.BREAKING]
    llm = FakeLLM(
        [ImpactText(impact=f"i{n}") for n in range(len(breaking))] + [SummaryText(summary="s")]
    )
    out = Explain(llm).run(report)
    assert [i.path for i in out.impacts] == breaking
    assert [i.impact for i in out.impacts] == [f"i{n}" for n in range(len(breaking))]
    assert [c.schema for c in llm.calls] == [ImpactText] * len(breaking) + [SummaryText]
    assert llm.calls[0].system == IMPACT_SYSTEM
    assert llm.calls[-1].system == SUMMARY_SYSTEM
    for call, path in zip(llm.calls, breaking):
        assert path in call.prompt


def test_partial_failure_only_that_impact_falls_back() -> None:
    report = _breaking_report("a", "b")
    changes = [c for c in report.changes if c.severity is Severity.BREAKING]
    assert len(changes) == 2
    llm = FakeLLM([LLMError("boom"), ImpactText(impact="real"), SummaryText(summary="s")])
    out = Explain(llm).run(report)
    assert out.degraded
    assert out.impacts[0].impact == changes[0].reason
    assert out.impacts[1].impact == "real"
    assert out.summary == "s"


def test_summary_failure_only_marks_degraded_keeps_impacts() -> None:
    llm = FakeLLM([ImpactText(impact="real"), LLMError("boom")])
    out = Explain(llm).run(_breaking_report("amount"))
    assert out.degraded
    assert out.impacts[0].impact == "real"
    assert "breaking" in out.summary


def test_exhausted_llm_responses_degrade_instead_of_raising() -> None:
    out = Explain(FakeLLM([])).run(_breaking_report("amount"))
    assert out.degraded
    assert out.impacts and out.impacts[0].impact
    assert out.summary


def test_wrong_schema_response_degrades() -> None:
    llm = FakeLLM([SummaryText(summary="wrong type"), SummaryText(summary="s")])
    out = Explain(llm).run(_breaking_report("amount"))
    assert out.degraded
    assert out.summary == "s"


def test_fallback_summary_mentions_verdict_and_counts() -> None:
    report = _breaking_report("a", "b")
    out = Explain(FakeLLM([LLMError("1"), LLMError("2"), LLMError("3")])).run(report)
    assert report.verdict.value in out.summary
    assert f"{len(report.changes)} change(s)" in out.summary
    assert "2 breaking" in out.summary


def test_run_is_idempotent_with_same_responses() -> None:
    report = _breaking_report("amount")

    def script():
        return FakeLLM([ImpactText(impact="i"), SummaryText(summary="s")])

    assert Explain(script()).run(report) == Explain(script()).run(report)


def test_empty_llm_text_is_rejected_by_schema() -> None:
    with pytest.raises(ValueError):
        ImpactText(impact="")
    with pytest.raises(ValueError):
        SummaryText(summary="")


def test_non_llm_error_is_not_swallowed() -> None:
    llm = FakeLLM([RuntimeError("bug")])
    with pytest.raises(RuntimeError):
        Explain(llm).run(_breaking_report("amount"))
