import itertools
import random

from schemasentinel.domain.diff import diff, name_similarity
from schemasentinel.domain.models import ChangeType, Column, SchemaSnapshot

CT = ChangeType


def col(name, data_type="varchar", pos=0, nullable=True, children=()):
    return Column(
        name=name, data_type=data_type, position=pos, nullable=nullable, children=children
    )


def snap(*cols):
    return SchemaSnapshot(source="s", format="parquet", columns=tuple(cols))


def kinds(changes):
    return [(c.change_type, c.path) for c in changes]


BASE = snap(
    col("id", "int64", 0, nullable=False),
    col("email", "varchar", 1),
    col("address", "struct", 2, children=(col("city", "varchar", 0), col("zip", "varchar", 1))),
)


def test_identical_snapshots_have_empty_diff():
    assert diff(BASE, BASE) == ()


def test_column_added_and_removed():
    cur = snap(col("id", "int64", 0, nullable=False), col("zzz_flag", "boolean", 1))
    result = diff(snap(col("id", "int64", 0, nullable=False), col("qq", "date", 1)), cur)
    assert set(kinds(result)) == {(CT.COLUMN_REMOVED, "qq"), (CT.COLUMN_ADDED, "zzz_flag")}


def test_type_changed():
    cur = snap(col("id", "int32", 0, nullable=False))
    result = diff(snap(col("id", "int64", 0, nullable=False)), cur)
    assert kinds(result) == [(CT.TYPE_CHANGED, "id")]


def test_equivalent_type_spellings_are_not_a_change():
    assert diff(snap(col("a", "int64")), snap(col("a", "bigint"))) == ()


def test_nullability_changed():
    result = diff(snap(col("a", "int64")), snap(col("a", "int64", nullable=False)))
    assert kinds(result) == [(CT.NULLABILITY_CHANGED, "a")]


def test_position_change_ignores_shifts_caused_by_additions():
    base = snap(col("a", pos=0), col("b", pos=1))
    cur = snap(col("new_col_x", "int64", 0), col("a", pos=1), col("b", pos=2))
    assert kinds(diff(base, cur)) == [(CT.COLUMN_ADDED, "new_col_x")]


def test_position_changed_on_reorder():
    base = snap(col("a", pos=0), col("b", pos=1))
    cur = snap(col("a", pos=1), col("b", pos=0))
    assert kinds(diff(base, cur)) == [
        (CT.POSITION_CHANGED, "a"),
        (CT.POSITION_CHANGED, "b"),
    ]


def test_nested_field_changes_use_dotted_paths():
    cur = snap(
        col("id", "int64", 0, nullable=False),
        col("email", "varchar", 1),
        col(
            "address",
            "struct",
            2,
            children=(col("city", "int32", 0), col("country_code", "varchar", 1)),
        ),
    )
    result = diff(BASE, cur)
    assert set(kinds(result)) == {
        (CT.TYPE_CHANGED, "address.city"),
        (CT.COLUMN_REMOVED, "address.zip"),
        (CT.COLUMN_ADDED, "address.country_code"),
    }


def test_rename_candidate_replaces_removed_and_added_pair():
    base = snap(col("customer_email", "varchar", 0))
    cur = snap(col("customer_mail", "varchar", 0))
    (change,) = diff(base, cur)
    assert change.change_type is CT.RENAME_CANDIDATE
    assert change.path == "customer_email"
    assert change.baseline.name == "customer_email"
    assert change.current.name == "customer_mail"
    assert change.severity.value == "needs_review"
    assert 0.6 <= change.confidence <= 1.0


def test_no_rename_candidate_when_types_are_incompatible():
    base = snap(col("customer_email", "varchar", 0))
    cur = snap(col("customer_mail", "int64", 0))
    assert {c.change_type for c in diff(base, cur)} == {CT.COLUMN_REMOVED, CT.COLUMN_ADDED}


def test_no_rename_candidate_when_names_are_dissimilar():
    base = snap(col("alpha", "varchar", 0))
    cur = snap(col("zzzzz", "varchar", 0))
    assert {c.change_type for c in diff(base, cur)} == {CT.COLUMN_REMOVED, CT.COLUMN_ADDED}


def test_rename_threshold_is_configurable():
    base = snap(col("customer_email", "varchar", 0))
    cur = snap(col("customer_mail", "varchar", 0))
    assert {c.change_type for c in diff(base, cur, rename_threshold=0.99)} == {
        CT.COLUMN_REMOVED,
        CT.COLUMN_ADDED,
    }


def test_rename_matching_is_one_to_one_and_prefers_best_match():
    base = snap(col("user_name", "varchar", 0))
    cur = snap(col("user_names", "varchar", 0), col("user_nam", "varchar", 1))
    result = diff(base, cur)
    renames = [c for c in result if c.change_type is CT.RENAME_CANDIDATE]
    assert len(renames) == 1
    assert renames[0].current.name == "user_names"
    assert (CT.COLUMN_ADDED, "user_nam") in kinds(result)


def test_name_similarity_is_symmetric_and_case_insensitive():
    assert name_similarity("Email", "email") == 1.0
    assert name_similarity("customer_email", "mail") == name_similarity("mail", "customer_email")


def test_add_remove_symmetry():
    a = snap(col("id", "int64", 0), col("qq", "date", 1))
    b = snap(col("id", "int64", 0), col("zzz_flag", "boolean", 1))
    forward = {(c.change_type, c.path) for c in diff(a, b)}
    backward = {(c.change_type, c.path) for c in diff(b, a)}
    flip = {CT.COLUMN_ADDED: CT.COLUMN_REMOVED, CT.COLUMN_REMOVED: CT.COLUMN_ADDED}
    assert {(flip[t], p) for t, p in forward} == backward


def test_symmetry_swaps_baseline_and_current_payloads():
    a = snap(col("id", "int64", 0))
    b = snap(col("id", "int64", 0), col("extra", "boolean", 1))
    (added,) = diff(a, b)
    (removed,) = diff(b, a)
    assert added.change_type is CT.COLUMN_ADDED and added.current == removed.baseline
    assert removed.change_type is CT.COLUMN_REMOVED and removed.current is None


def test_rename_symmetry():
    a = snap(col("customer_email", "varchar", 0))
    b = snap(col("customer_mail", "varchar", 0))
    (fwd,) = diff(a, b)
    (bwd,) = diff(b, a)
    assert fwd.change_type is bwd.change_type is CT.RENAME_CANDIDATE
    assert fwd.baseline == bwd.current and fwd.current == bwd.baseline
    assert fwd.confidence == bwd.confidence


def _shuffled(snapshot: SchemaSnapshot, rng: random.Random) -> SchemaSnapshot:
    def shuffle(cols):
        items = [c.model_copy(update={"children": shuffle(c.children)}) for c in cols]
        rng.shuffle(items)
        return tuple(items)

    return snapshot.model_copy(update={"columns": shuffle(snapshot.columns)})


def test_diff_is_order_independent():
    base = snap(
        col("id", "int64", 0, nullable=False),
        col("customer_email", "varchar", 1),
        col("legacy", "date", 2),
        col("address", "struct", 3, children=(col("city", "varchar", 0), col("zip", "int32", 1))),
        col("b", "varchar", 4),
        col("a", "varchar", 5),
    )
    cur = snap(
        col("id", "int32", 0),
        col("customer_mail", "varchar", 1),
        col("address", "struct", 2, children=(col("city", "int64", 1), col("zip", "int32", 0))),
        col("a", "varchar", 3),
        col("b", "varchar", 4),
        col("created", "timestamp", 5),
    )
    expected = diff(base, cur)
    assert expected
    rng = random.Random(7)
    for _ in range(25):
        assert diff(_shuffled(base, rng), _shuffled(cur, rng)) == expected


def test_diff_with_itself_is_empty_for_permutations():
    for perm in itertools.permutations(BASE.columns):
        assert diff(BASE, BASE.model_copy(update={"columns": perm})) == ()
