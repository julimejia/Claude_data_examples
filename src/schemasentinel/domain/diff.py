from __future__ import annotations

from difflib import SequenceMatcher

from schemasentinel.domain.models import (
    ChangeType,
    Column,
    SchemaChange,
    SchemaSnapshot,
    Severity,
)
from schemasentinel.domain.types import TypeRelation, compare_type_names

DEFAULT_RENAME_THRESHOLD = 0.6

# The diff only detects; rules.py assigns the real severity and rule_id.
UNCLASSIFIED_RULE = "DIFF-UNCLASSIFIED"
RENAME_RULE = "DIFF-RENAME-CANDIDATE"

_Index = dict[str, Column]


def name_similarity(a: str, b: str) -> float:
    """Symmetric, case-insensitive similarity in [0, 1] using stdlib difflib."""
    first, second = sorted((a.lower(), b.lower()))
    return SequenceMatcher(None, first, second).ratio()


def _split(path: str) -> tuple[str, str]:
    parent, _, name = path.rpartition(".")
    return parent, name


def _index(snapshot: SchemaSnapshot) -> _Index:
    return dict(snapshot.flatten())


def _change(
    change_type: ChangeType,
    path: str,
    baseline: Column | None,
    current: Column | None,
    reason: str,
    *,
    severity: Severity = Severity.NEEDS_REVIEW,
    rule_id: str = UNCLASSIFIED_RULE,
    confidence: float = 1.0,
) -> SchemaChange:
    return SchemaChange(
        change_type=change_type,
        path=path,
        baseline=baseline,
        current=current,
        severity=severity,
        rule_id=rule_id,
        reason=reason,
        confidence=confidence,
    )


def _rename_candidates(
    removed: list[str], added: list[str], base: _Index, cur: _Index, threshold: float
) -> list[tuple[str, str, float]]:
    """Greedy one-to-one matching of removed/added siblings, best score first."""
    scored: list[tuple[float, str, str]] = []
    for old in removed:
        old_parent, old_name = _split(old)
        for new in added:
            new_parent, new_name = _split(new)
            if old_parent != new_parent:
                continue
            relation = compare_type_names(base[old].data_type, cur[new].data_type)
            if relation is TypeRelation.CATEGORY_CHANGE:
                continue
            score = name_similarity(old_name, new_name)
            if score >= threshold:
                scored.append((score, old, new))
    scored.sort(key=lambda t: (-t[0], t[1], t[2]))
    used_old: set[str] = set()
    used_new: set[str] = set()
    pairs: list[tuple[str, str, float]] = []
    for score, old, new in scored:
        if old in used_old or new in used_new:
            continue
        used_old.add(old)
        used_new.add(new)
        pairs.append((old, new, score))
    return pairs


def _rank_by_position(index: _Index, paths: set[str]) -> dict[str, int]:
    """Rank of each path among the given siblings, ordered by declared position."""
    ordered = sorted(paths, key=lambda p: (index[p].position, p))
    return {path: rank for rank, path in enumerate(ordered)}


def _position_changes(base: _Index, cur: _Index, common: set[str]) -> list[SchemaChange]:
    """Compare order among surviving siblings so adds/removes do not shift everything."""
    by_parent: dict[str, set[str]] = {}
    for path in common:
        by_parent.setdefault(_split(path)[0], set()).add(path)
    changes: list[SchemaChange] = []
    for siblings in by_parent.values():
        old_rank = _rank_by_position(base, siblings)
        new_rank = _rank_by_position(cur, siblings)
        for path in siblings:
            if old_rank[path] != new_rank[path]:
                changes.append(
                    _change(
                        ChangeType.POSITION_CHANGED,
                        path,
                        base[path],
                        cur[path],
                        f"Position changed from {old_rank[path]} to {new_rank[path]}",
                    )
                )
    return changes


def diff(
    baseline: SchemaSnapshot,
    current: SchemaSnapshot,
    *,
    rename_threshold: float = DEFAULT_RENAME_THRESHOLD,
) -> tuple[SchemaChange, ...]:
    """Deterministic, order-independent diff of two snapshots.

    Changes come back unclassified (``needs_review``); ``rules`` assigns severities.
    A removed/added sibling pair with compatible types and similar names is replaced by a
    single ``RENAME_CANDIDATE`` at the baseline path.
    """
    base, cur = _index(baseline), _index(current)
    removed = sorted(base.keys() - cur.keys())
    added = sorted(cur.keys() - base.keys())
    common = base.keys() & cur.keys()

    changes: list[SchemaChange] = []

    pairs = _rename_candidates(removed, added, base, cur, rename_threshold)
    renamed_old = {old for old, _, _ in pairs}
    renamed_new = {new for _, new, _ in pairs}
    for old, new, score in pairs:
        changes.append(
            _change(
                ChangeType.RENAME_CANDIDATE,
                old,
                base[old],
                cur[new],
                f"'{old}' may have been renamed to '{new}' (name similarity {score:.2f})",
                rule_id=RENAME_RULE,
                confidence=round(score, 4),
            )
        )
    for path in removed:
        if path not in renamed_old:
            changes.append(
                _change(ChangeType.COLUMN_REMOVED, path, base[path], None, "Column removed")
            )
    for path in added:
        if path not in renamed_new:
            changes.append(_change(ChangeType.COLUMN_ADDED, path, None, cur[path], "Column added"))

    for path in common:
        old, new = base[path], cur[path]
        if compare_type_names(old.data_type, new.data_type) is not TypeRelation.SAME:
            changes.append(
                _change(
                    ChangeType.TYPE_CHANGED,
                    path,
                    old,
                    new,
                    f"Type changed from {old.data_type} to {new.data_type}",
                )
            )
        if old.nullable != new.nullable:
            changes.append(
                _change(
                    ChangeType.NULLABILITY_CHANGED,
                    path,
                    old,
                    new,
                    f"Nullable changed from {old.nullable} to {new.nullable}",
                )
            )
    changes.extend(_position_changes(base, cur, common))

    changes.sort(key=lambda c: (c.path, c.change_type.value))
    return tuple(changes)
