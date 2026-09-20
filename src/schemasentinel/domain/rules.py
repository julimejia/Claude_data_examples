from __future__ import annotations

from collections.abc import Iterable

from schemasentinel.domain.models import ChangeType, SchemaChange, Severity
from schemasentinel.domain.types import TypeRelation, compare_type_names

# Formats where consumers read columns by position rather than by name.
POSITIONAL_FORMATS = frozenset({"csv"})


def _verdict(change: SchemaChange, severity: Severity, rule_id: str, reason: str) -> SchemaChange:
    return change.model_copy(
        update={"severity": severity, "rule_id": rule_id, "reason": reason, "confidence": 1.0}
    )


def _classify_added(change: SchemaChange) -> SchemaChange:
    col = change.current
    assert col is not None
    if col.nullable:
        return _verdict(change, Severity.NON_BREAKING, "ADD-NULLABLE", "Nullable column added")
    if col.default is not None:
        return _verdict(
            change,
            Severity.NON_BREAKING,
            "ADD-NOT-NULL-DEFAULT",
            "NOT NULL column added with a default value",
        )
    return _verdict(
        change,
        Severity.BREAKING,
        "ADD-NOT-NULL",
        "NOT NULL column added without a default: existing writers will fail",
    )


def _classify_type(change: SchemaChange) -> SchemaChange:
    old, new = change.baseline, change.current
    assert old is not None and new is not None
    relation = compare_type_names(old.data_type, new.data_type)
    text = f"{old.data_type} -> {new.data_type}"
    if relation is TypeRelation.WIDENED:
        return _verdict(change, Severity.NON_BREAKING, "TYPE-WIDENED", f"Type widened: {text}")
    if relation is TypeRelation.NARROWED:
        return _verdict(
            change, Severity.BREAKING, "TYPE-NARROWED", f"Type narrowed, values may not fit: {text}"
        )
    return _verdict(change, Severity.BREAKING, "TYPE-CATEGORY", f"Type category changed: {text}")


def _classify_nullability(change: SchemaChange) -> SchemaChange:
    new = change.current
    assert new is not None
    if not new.nullable:
        return _verdict(
            change,
            Severity.BREAKING,
            "NULL-TO-NOT-NULL",
            "Column became NOT NULL: writers sending nulls will fail",
        )
    return _verdict(
        change,
        Severity.WARNING,
        "NOT-NULL-TO-NULL",
        "Column became nullable: consumers assuming no nulls may break",
    )


def _classify_position(change: SchemaChange, source_format: str) -> SchemaChange:
    if source_format.lower() in POSITIONAL_FORMATS:
        return _verdict(
            change,
            Severity.BREAKING,
            "REORDER-POSITIONAL",
            f"Column reordered in positional format '{source_format}'",
        )
    return _verdict(
        change,
        Severity.NON_BREAKING,
        "REORDER-NAMED",
        f"Column reordered; '{source_format}' is accessed by name",
    )


def classify(change: SchemaChange, *, source_format: str) -> SchemaChange:
    """Apply the FR-3.1 rule table to one raw change; pure and total over ChangeType."""
    match change.change_type:
        case ChangeType.COLUMN_ADDED:
            return _classify_added(change)
        case ChangeType.COLUMN_REMOVED:
            return _verdict(change, Severity.BREAKING, "REMOVE", "Column removed")
        case ChangeType.TYPE_CHANGED:
            return _classify_type(change)
        case ChangeType.NULLABILITY_CHANGED:
            return _classify_nullability(change)
        case ChangeType.POSITION_CHANGED:
            return _classify_position(change, source_format)
        case ChangeType.RENAME_CANDIDATE:
            # Keep the similarity score as confidence; resolution is left to a later stage.
            return change.model_copy(
                update={"severity": Severity.NEEDS_REVIEW, "rule_id": "RENAME-CANDIDATE"}
            )
    raise ValueError(f"unhandled change type: {change.change_type}")


def classify_all(
    changes: Iterable[SchemaChange], *, source_format: str
) -> tuple[SchemaChange, ...]:
    return tuple(classify(c, source_format=source_format) for c in changes)
