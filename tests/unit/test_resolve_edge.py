from schemasentinel.adapters.llm.fake import FakeLLM
from schemasentinel.application.detect_drift import build_report, overall_verdict
from schemasentinel.application.resolve import Resolve
from schemasentinel.domain.models import (
    ChangeType,
    Column,
    Decision,
    Resolution,
    SchemaSnapshot,
    Severity,
    Verdict,
)
from schemasentinel.ports.llm import LLMError


def _snap(*cols: tuple[str, str], fmt: str = "parquet") -> SchemaSnapshot:
    return SchemaSnapshot(
        source="s",
        format=fmt,
        columns=tuple(Column(name=n, data_type=t, position=i) for i, (n, t) in enumerate(cols)),
    )


def _rename_report():
    return build_report(
        _snap(("id", "BIGINT"), ("customer_name", "VARCHAR")),
        _snap(("id", "BIGINT"), ("cust_name", "VARCHAR")),
    )


def _res(decision=Decision.RENAME, conf=0.9):
    return Resolution(decision=decision, confidence=conf, rationale="r")


def _rc(report):
    return [c for c in report.changes if c.change_type is ChangeType.RENAME_CANDIDATE]


def test_no_needs_review_makes_no_llm_calls() -> None:
    report = build_report(_snap(("id", "BIGINT")), _snap(("id", "BIGINT"), ("x", "INT")))
    llm = FakeLLM([])
    out = Resolve(llm).run(report)
    assert llm.calls == []
    assert out.changes == report.changes
    assert out.run_metadata["llm_calls"] == 0


def test_empty_report_unchanged() -> None:
    report = build_report(_snap(("id", "BIGINT")), _snap(("id", "BIGINT")))
    out = Resolve(FakeLLM([])).run(report)
    assert out.verdict is Verdict.NONE and out.changes == ()


def test_input_report_not_mutated() -> None:
    report = _rename_report()
    before = report.model_dump()
    Resolve(FakeLLM([_res()])).run(report)
    assert report.model_dump() == before


def test_confidence_exactly_threshold_is_accepted() -> None:
    out = Resolve(FakeLLM([_res(conf=0.6)])).run(_rename_report())
    (c,) = _rc(out)
    assert not c.needs_human_review and c.rule_id == "RENAME-RESOLVED"


def test_unknown_high_confidence_needs_human_review() -> None:
    out = Resolve(FakeLLM([_res(Decision.UNKNOWN, 0.99)])).run(_rename_report())
    (c,) = _rc(out)
    assert c.needs_human_review and c.severity is Severity.NEEDS_REVIEW
    assert c.resolution is not None


def test_low_confidence_keeps_original_rule_and_records_resolution() -> None:
    report = _rename_report()
    orig = _rc(report)[0]
    out = Resolve(FakeLLM([_res(conf=0.1)])).run(report)
    (c,) = _rc(out)
    assert c.rule_id == orig.rule_id and c.reason == orig.reason
    assert c.confidence == orig.confidence
    assert c.resolution.confidence == 0.1


def test_confidence_zero_and_one_bounds() -> None:
    (lo,) = _rc(Resolve(FakeLLM([_res(conf=0.0)])).run(_rename_report()))
    (hi,) = _rc(Resolve(FakeLLM([_res(conf=1.0)])).run(_rename_report()))
    assert lo.needs_human_review and not hi.needs_human_review
    assert hi.confidence == 1.0


def test_llm_error_still_counts_call_and_run_succeeds() -> None:
    out = Resolve(FakeLLM([LLMError("boom")])).run(_rename_report())
    (c,) = _rc(out)
    assert c.needs_human_review and c.severity is Severity.NEEDS_REVIEW
    assert out.run_metadata["llm_calls"] == 1


def test_exhausted_fake_llm_degrades_for_every_change() -> None:
    report = build_report(
        _snap(("customer_name", "VARCHAR"), ("order_total", "DOUBLE")),
        _snap(("cust_name", "VARCHAR"), ("order_tot", "DOUBLE")),
    )
    n = len(_rc(report))
    assert n >= 1
    out = Resolve(FakeLLM([])).run(report)
    assert all(c.needs_human_review for c in _rc(out))
    assert out.run_metadata["llm_calls"] == n


def test_multiple_candidates_one_call_each_in_order() -> None:
    report = build_report(
        _snap(("customer_name", "VARCHAR"), ("order_total", "DOUBLE")),
        _snap(("cust_name", "VARCHAR"), ("order_tot", "DOUBLE")),
    )
    cands = _rc(report)
    assert len(cands) == 2
    llm = FakeLLM([_res(Decision.RENAME, 0.9), _res(Decision.DROP_AND_ADD, 0.7)])
    out = Resolve(llm).run(report)
    got = _rc(out)
    assert [c.path for c in got] == [c.path for c in cands]
    assert got[0].rule_id == "RENAME-RESOLVED"
    assert got[1].rule_id == "DROP-AND-ADD-RESOLVED"
    assert len(llm.calls) == 2


def test_mixed_success_and_failure() -> None:
    report = build_report(
        _snap(("customer_name", "VARCHAR"), ("order_total", "DOUBLE")),
        _snap(("cust_name", "VARCHAR"), ("order_tot", "DOUBLE")),
    )
    out = Resolve(FakeLLM([LLMError("x"), _res()])).run(report)
    a, b = _rc(out)
    assert a.needs_human_review and not b.needs_human_review


def test_non_review_changes_pass_through_untouched() -> None:
    report = build_report(
        _snap(("id", "BIGINT"), ("customer_name", "VARCHAR"), ("z", "INT")),
        _snap(("id", "INT"), ("cust_name", "VARCHAR"), ("z", "INT")),
    )
    others = [c for c in report.changes if c.severity is not Severity.NEEDS_REVIEW]
    assert others
    out = Resolve(FakeLLM([_res()])).run(report)
    for o in others:
        assert o in out.changes


def test_change_order_preserved() -> None:
    report = _rename_report()
    out = Resolve(FakeLLM([_res()])).run(report)
    assert [c.path for c in out.changes] == [c.path for c in report.changes]


def test_llm_calls_accumulates_on_existing_metadata() -> None:
    report = build_report(
        _snap(("id", "BIGINT"), ("customer_name", "VARCHAR")),
        _snap(("id", "BIGINT"), ("cust_name", "VARCHAR")),
        {"llm_calls": 3, "run_id": "abc"},
    )
    out = Resolve(FakeLLM([_res()])).run(report)
    assert out.run_metadata["llm_calls"] == 4 and out.run_metadata["run_id"] == "abc"


def test_prompt_contains_both_columns_and_uses_resolution_schema() -> None:
    llm = FakeLLM([_res()])
    Resolve(llm).run(_rename_report())
    (call,) = llm.calls
    assert "customer_name" in call.prompt and "cust_name" in call.prompt
    assert call.schema is Resolution


def test_prompt_is_deterministic() -> None:
    a, b = FakeLLM([_res()]), FakeLLM([_res()])
    Resolve(a).run(_rename_report())
    Resolve(b).run(_rename_report())
    assert a.calls[0].prompt == b.calls[0].prompt


def test_idempotent_second_run_makes_no_calls_when_resolved() -> None:
    once = Resolve(FakeLLM([_res()])).run(_rename_report())
    llm = FakeLLM([])
    twice = Resolve(llm).run(once)
    assert llm.calls == [] and twice.changes == once.changes


def test_verdict_recomputed_after_resolution() -> None:
    report = _rename_report()
    out = Resolve(FakeLLM([_res(Decision.DROP_AND_ADD, 0.9)])).run(report)
    assert out.verdict is overall_verdict(out.changes)


def test_change_counts_refreshed_after_resolution() -> None:
    out = Resolve(FakeLLM([_res()])).run(_rename_report())
    counts = out.run_metadata.get("change_counts", {})
    assert counts.get("needs_review", 0) == 0
    assert counts.get("breaking", 0) == 1
