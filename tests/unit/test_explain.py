from schemasentinel.adapters.llm.fake import FakeLLM
from schemasentinel.application.detect_drift import build_report
from schemasentinel.application.explain import Explain
from schemasentinel.domain.models import Column, ImpactText, SchemaSnapshot, SummaryText
from schemasentinel.ports.llm import LLMError


def _report():
    def snap(*cols):
        return SchemaSnapshot(
            source="s",
            format="parquet",
            columns=tuple(Column(name=n, data_type=t, position=i) for i, (n, t) in enumerate(cols)),
        )

    return build_report(snap(("id", "BIGINT"), ("amount", "BIGINT")), snap(("id", "BIGINT")))


def test_impact_and_summary() -> None:
    llm = FakeLLM(
        [ImpactText(impact="Dashboards break"), SummaryText(summary="One breaking change.")]
    )
    out = Explain(llm).run(_report())
    assert out.summary == "One breaking change."
    assert [i.path for i in out.impacts] == ["amount"]
    assert out.impacts[0].impact == "Dashboards break"
    assert not out.degraded


def test_degrades_gracefully() -> None:
    out = Explain(FakeLLM([LLMError("x"), LLMError("y")])).run(_report())
    assert out.degraded
    assert out.impacts[0].path == "amount" and out.impacts[0].impact
    assert "breaking" in out.summary
