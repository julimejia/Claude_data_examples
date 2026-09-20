import pytest
from pydantic import ValidationError

from schemasentinel.domain.models import (
    ChangeType,
    Column,
    DriftReport,
    SchemaChange,
    SchemaSnapshot,
    Severity,
    Verdict,
)


def make_snapshot() -> SchemaSnapshot:
    address = Column(
        name="address",
        data_type="struct",
        position=2,
        children=(
            Column(name="city", data_type="varchar", position=0),
            Column(
                name="geo",
                data_type="struct",
                position=1,
                children=(Column(name="lat", data_type="float64", position=0),),
            ),
        ),
    )
    tags = Column(
        name="tags",
        data_type="list",
        position=3,
        children=(Column(name="element", data_type="varchar", position=0),),
    )
    return SchemaSnapshot(
        source="data/users.csv",
        format="csv",
        columns=(
            Column(name="id", data_type="int64", nullable=False, position=0),
            Column(name="email", data_type="varchar", position=1),
            address,
            tags,
        ),
        metadata={"delimiter": ",", "header": True},
    )


def test_snapshot_json_round_trip():
    snap = make_snapshot()
    assert SchemaSnapshot.model_validate_json(snap.model_dump_json()) == snap


def test_flatten_uses_dotted_paths_recursively():
    paths = list(make_snapshot().flatten())
    assert paths == [
        "id",
        "email",
        "address",
        "address.city",
        "address.geo",
        "address.geo.lat",
        "tags",
        "tags.element",
    ]


def test_flatten_returns_columns_by_path():
    flat = make_snapshot().flatten()
    assert flat["address.geo.lat"].data_type == "float64"
    assert flat["id"].nullable is False


def test_models_are_frozen():
    snap = make_snapshot()
    with pytest.raises(ValidationError):
        snap.source = "other"
    with pytest.raises(ValidationError):
        snap.columns[0].name = "x"


def test_column_defaults():
    col = Column(name="a", data_type="int32", position=0)
    assert col.nullable is True
    assert col.children == ()
    assert col.default is None


def test_change_and_report_round_trip():
    snap = make_snapshot()
    change = SchemaChange(
        change_type=ChangeType.COLUMN_REMOVED,
        path="email",
        baseline=snap.columns[1],
        current=None,
        severity=Severity.BREAKING,
        rule_id="R-REMOVED",
        reason="Column removed",
    )
    assert change.confidence == 1.0
    report = DriftReport(
        baseline=snap,
        current=snap,
        verdict=Verdict.BREAKING,
        changes=(change,),
        run_metadata={"run_id": "abc"},
    )
    assert report.schema_version == 1
    assert DriftReport.model_validate_json(report.model_dump_json()) == report
    with pytest.raises(ValidationError):
        report.verdict = Verdict.NONE


def test_confidence_bounds():
    with pytest.raises(ValidationError):
        SchemaChange(
            change_type=ChangeType.COLUMN_ADDED,
            path="a",
            severity=Severity.NON_BREAKING,
            rule_id="r",
            reason="x",
            confidence=1.5,
        )
