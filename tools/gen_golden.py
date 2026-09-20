"""Regenerate tests/golden/cases/*.json from the compact table below.

Expectations are written by hand from the FR-3.1 rule table, not derived from the engine.
Run from the project root: python tools/gen_golden.py
"""

import json
from pathlib import Path

OUT = Path("tests/golden/cases")

A, R, T, N, P, RN = (
    "column_added",
    "column_removed",
    "type_changed",
    "nullability_changed",
    "position_changed",
    "rename_candidate",
)
NB, B, W, NR = "non_breaking", "breaking", "warning", "needs_review"
cases = []


def c(cid, desc, base, cur, exp, verdict, fmt="parquet", resolution=None):
    changes = [
        {"change_type": t, "path": p, "severity": s, "rule_id": r} for t, p, s, r in exp
    ]
    d = {
        "id": cid,
        "description": desc,
        "format": fmt,
        "baseline": base,
        "current": cur,
        "expected": {"verdict": verdict, "changes": changes},
    }
    if resolution:
        d["expected_resolution"] = resolution
    cases.append(d)


ID = "id:BIGINT!"
c("add-nullable", "Nullable column added", [ID], [ID, "note:VARCHAR"],
  [(A, "note", NB, "ADD-NULLABLE")], NB)
c("add-not-null", "NOT NULL column without default", [ID], [ID, "qty:INTEGER!"],
  [(A, "qty", B, "ADD-NOT-NULL")], B)
c("add-not-null-default", "NOT NULL column with default", [ID], [ID, "qty:INTEGER!=0"],
  [(A, "qty", NB, "ADD-NOT-NULL-DEFAULT")], NB)
c("remove-column", "Column removed", [ID, "email:VARCHAR", "age:INTEGER"], [ID, "email:VARCHAR"],
  [(R, "age", B, "REMOVE")], B)
c("widen-int", "int32 to int64", [ID, "n:INTEGER"], [ID, "n:BIGINT"],
  [(T, "n", NB, "TYPE-WIDENED")], NB)
c("widen-float", "float32 to float64", [ID, "x:FLOAT"], [ID, "x:DOUBLE"],
  [(T, "x", NB, "TYPE-WIDENED")], NB)
c("widen-varchar-length", "varchar length up", [ID, "s:VARCHAR(50)"], [ID, "s:VARCHAR(100)"],
  [(T, "s", NB, "TYPE-WIDENED")], NB)
c("narrow-varchar-length", "varchar length down", [ID, "s:VARCHAR(100)"], [ID, "s:VARCHAR(50)"],
  [(T, "s", B, "TYPE-NARROWED")], B)
c("widen-date-timestamp", "date to timestamp", [ID, "d:DATE"], [ID, "d:TIMESTAMP"],
  [(T, "d", NB, "TYPE-WIDENED")], NB)
c("narrow-timestamp-date", "timestamp to date", [ID, "d:TIMESTAMP"], [ID, "d:DATE"],
  [(T, "d", B, "TYPE-NARROWED")], B)
c("narrow-int64-int32", "int64 to int32", [ID, "n:BIGINT"], [ID, "n:INTEGER"],
  [(T, "n", B, "TYPE-NARROWED")], B)
c("category-int-string", "integer to varchar", [ID, "code:INTEGER"], [ID, "code:VARCHAR"],
  [(T, "code", B, "TYPE-CATEGORY")], B)
c("category-string-date", "varchar to date", [ID, "when:VARCHAR"], [ID, "when:DATE"],
  [(T, "when", B, "TYPE-CATEGORY")], B)
c("category-string-int", "varchar to integer", [ID, "code:VARCHAR"], [ID, "code:INTEGER"],
  [(T, "code", B, "TYPE-CATEGORY")], B)
c("category-bool-int", "boolean to integer", [ID, "flag:BOOLEAN"], [ID, "flag:INTEGER"],
  [(T, "flag", B, "TYPE-CATEGORY")], B)
c("null-to-not-null", "nullable to NOT NULL", [ID, "email:VARCHAR"], [ID, "email:VARCHAR!"],
  [(N, "email", B, "NULL-TO-NOT-NULL")], B)
c("not-null-to-null", "NOT NULL to nullable is a warning", [ID, "email:VARCHAR!"],
  [ID, "email:VARCHAR"], [(N, "email", W, "NOT-NULL-TO-NULL")], NB)
c("reorder-parquet", "Reorder is harmless for named access", ["a:INTEGER", "b:VARCHAR"],
  ["b:VARCHAR", "a:INTEGER"],
  [(P, "a", NB, "REORDER-NAMED"), (P, "b", NB, "REORDER-NAMED")], NB)
c("reorder-csv", "Reorder breaks positional CSV", ["a:INTEGER", "b:VARCHAR"],
  ["b:VARCHAR", "a:INTEGER"],
  [(P, "a", B, "REORDER-POSITIONAL"), (P, "b", B, "REORDER-POSITIONAL")], B, fmt="csv")
c("reorder-delta", "Delta is accessed by name", ["a:INTEGER", "b:VARCHAR"],
  ["b:VARCHAR", "a:INTEGER"],
  [(P, "a", NB, "REORDER-NAMED"), (P, "b", NB, "REORDER-NAMED")], NB, fmt="delta")
c("rename-name-suffix", "customer_name to customer_nm", [ID, "customer_name:VARCHAR"],
  [ID, "customer_nm:VARCHAR"], [(RN, "customer_name", NR, "RENAME-CANDIDATE")], NB,
  resolution="rename")
c("rename-abbreviation", "cust_id to customer_id", [ID, "cust_id:BIGINT"],
  [ID, "customer_id:BIGINT"], [(RN, "cust_id", NR, "RENAME-CANDIDATE")], NB, resolution="rename")
c("rename-with-widening", "user_id to userid with a wider type", ["user_id:INTEGER"],
  ["userid:BIGINT"], [(RN, "user_id", NR, "RENAME-CANDIDATE")], NB, resolution="rename")
c("drop-and-add-category", "Unrelated columns of different category", [ID, "amount:DECIMAL(10,2)"],
  [ID, "region:VARCHAR"],
  [(R, "amount", B, "REMOVE"), (A, "region", NB, "ADD-NULLABLE")], B, resolution="drop_and_add")
c("drop-and-add-dissimilar", "Same type but dissimilar names", [ID, "email:VARCHAR"],
  [ID, "quantity:VARCHAR"],
  [(R, "email", B, "REMOVE"), (A, "quantity", NB, "ADD-NULLABLE")], B, resolution="drop_and_add")
c("rename-category-differs", "Similar names but incompatible types", [ID, "price:DOUBLE"],
  [ID, "price_label:VARCHAR"],
  [(R, "price", B, "REMOVE"), (A, "price_label", NB, "ADD-NULLABLE")], B)
c("no-change-parquet", "Identical schemas", [ID, "name:VARCHAR"], [ID, "name:VARCHAR"], [], "none")
c("no-change-csv", "Identical CSV schemas", [ID, "name:VARCHAR"], [ID, "name:VARCHAR"], [],
  "none", fmt="csv")

S1 = {"name": "s", "type": "STRUCT(a INTEGER)", "children": ["a:INTEGER"]}
S2 = {"name": "s", "type": "STRUCT(a INTEGER, b VARCHAR)", "children": ["a:INTEGER", "b:VARCHAR"]}
S3 = {"name": "s", "type": "STRUCT(a VARCHAR)", "children": ["a:VARCHAR"]}
c("nested-add-field", "Field added inside a struct", [S1], [S2],
  [(A, "s.b", NB, "ADD-NULLABLE")], NB)
c("nested-remove-field", "Field removed from a struct", [S2], [S1],
  [(R, "s.b", B, "REMOVE")], B)
c("nested-type-change", "Struct field type category change", [S1], [S3],
  [(T, "s.a", B, "TYPE-CATEGORY")], B)

c("combined-breaking", "Widen, remove and add together", [ID, "n:INTEGER", "old:VARCHAR"],
  [ID, "n:BIGINT", "note:DATE"],
  [(T, "n", NB, "TYPE-WIDENED"), (R, "old", B, "REMOVE"), (A, "note", NB, "ADD-NULLABLE")], B)
c("combined-non-breaking", "Add nullable and widen", [ID, "n:INTEGER"],
  [ID, "n:BIGINT", "note:VARCHAR"],
  [(T, "n", NB, "TYPE-WIDENED"), (A, "note", NB, "ADD-NULLABLE")], NB)
c("widen-decimal", "decimal precision up", [ID, "m:DECIMAL(10,2)"], [ID, "m:DECIMAL(12,2)"],
  [(T, "m", NB, "TYPE-WIDENED")], NB)
c("narrow-decimal", "decimal precision down", [ID, "m:DECIMAL(12,2)"], [ID, "m:DECIMAL(10,2)"],
  [(T, "m", B, "TYPE-NARROWED")], B)
c("widen-int-to-double", "int32 fits exactly in a double", [ID, "n:INTEGER"], [ID, "n:DOUBLE"],
  [(T, "n", NB, "TYPE-WIDENED")], NB)
c("narrow-bigint-to-double", "int64 loses precision in a double", [ID, "n:BIGINT"],
  [ID, "n:DOUBLE"], [(T, "n", B, "TYPE-NARROWED")], B)
c("timestamp-tz-change", "Timestamp gains a time zone", [ID, "ts:TIMESTAMP"],
  [ID, "ts:TIMESTAMP WITH TIME ZONE"], [(T, "ts", B, "TYPE-CATEGORY")], B)
c("unsigned-to-wider-signed", "uint32 to int64", [ID, "n:UINTEGER"], [ID, "n:BIGINT"],
  [(T, "n", NB, "TYPE-WIDENED")], NB)
c("signed-to-unsigned", "int32 to uint32 loses negatives", [ID, "n:INTEGER"], [ID, "n:UINTEGER"],
  [(T, "n", B, "TYPE-NARROWED")], B)
c("add-column-at-front", "New nullable column first keeps survivor order",
  ["a:INTEGER", "b:VARCHAR"], ["z:DATE", "a:INTEGER", "b:VARCHAR"],
  [(A, "z", NB, "ADD-NULLABLE")], NB, fmt="csv")
c("type-and-nullability", "Two changes on one column", [ID, "n:BIGINT"], [ID, "n:INTEGER!"],
  [(T, "n", B, "TYPE-NARROWED"), (N, "n", B, "NULL-TO-NOT-NULL")], B)
c("remove-not-null-column", "Removing a NOT NULL column", [ID, "code:VARCHAR!"], [ID],
  [(R, "code", B, "REMOVE")], B)
c("csv-add-not-null", "NOT NULL added to CSV", [ID], [ID, "qty:INTEGER!"],
  [(A, "qty", B, "ADD-NOT-NULL")], B, fmt="csv")


if __name__ == "__main__":
    OUT.mkdir(parents=True, exist_ok=True)
    for old in OUT.glob("*.json"):
        old.unlink()
    for d in cases:
        (OUT / f"{d['id']}.json").write_text(json.dumps(d, indent=2) + "\n", encoding="utf-8")
    print(f"wrote {len(cases)} cases")
