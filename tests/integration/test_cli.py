from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

import duckdb

from schemasentinel.domain.models import SchemaSnapshot

FIXTURES = Path(__file__).parents[1] / "fixtures"
ORDERS = FIXTURES / "orders.parquet"
SRC = Path(__file__).parents[2] / "src"


def run(*args: str) -> subprocess.CompletedProcess[str]:
    env = {**os.environ, "PYTHONPATH": str(SRC)}
    return subprocess.run(
        [sys.executable, "-m", "schemasentinel", *args],
        capture_output=True,
        text=True,
        encoding="utf-8",
        env=env,
    )


def make_parquet(path: Path, select: str) -> str:
    con = duckdb.connect()
    con.execute(f"COPY ({select}) TO '{path.as_posix()}' (FORMAT PARQUET)")
    con.close()
    return str(path)


def test_snapshot_to_file_round_trips(tmp_path):
    out = tmp_path / "snap.json"
    res = run("snapshot", str(ORDERS), "-o", str(out))
    assert res.returncode == 0, res.stderr
    snap = SchemaSnapshot.model_validate_json(out.read_text(encoding="utf-8"))
    assert snap.format == "parquet" and snap.columns


def test_snapshot_to_stdout():
    res = run("snapshot", str(ORDERS))
    assert res.returncode == 0
    assert json.loads(res.stdout)["format"] == "parquet"


def test_diff_no_drift_exit_0():
    res = run("diff", str(ORDERS), str(ORDERS))
    assert res.returncode == 0, res.stderr
    assert json.loads(res.stdout)["verdict"] == "none"


def test_diff_non_breaking_exit_0(tmp_path):
    base = make_parquet(tmp_path / "b.parquet", "SELECT 1::BIGINT AS id")
    cur = make_parquet(tmp_path / "c.parquet", "SELECT 1::BIGINT AS id, 'x' AS note")
    res = run("diff", base, cur)
    assert res.returncode == 0, res.stderr
    assert json.loads(res.stdout)["verdict"] == "non_breaking"


def test_diff_breaking_exit_1_markdown(tmp_path):
    base = make_parquet(tmp_path / "b.parquet", "SELECT 1::BIGINT AS id, 'x' AS name")
    cur = make_parquet(tmp_path / "c.parquet", "SELECT 1::BIGINT AS id")
    res = run("diff", base, cur, "--format", "md", "--dialect", "tsql")
    assert res.returncode == 1, res.stderr
    assert "BREAKING" in res.stdout


def test_diff_accepts_saved_snapshot(tmp_path):
    snap = tmp_path / "snap.json"
    assert run("snapshot", str(ORDERS), "-o", str(snap)).returncode == 0
    res = run("diff", str(snap), str(ORDERS))
    assert res.returncode == 0, res.stderr
    assert json.loads(res.stdout)["verdict"] == "none"


def test_missing_source_exit_2(tmp_path):
    res = run("diff", str(tmp_path / "nope.parquet"), str(ORDERS))
    assert res.returncode == 2
    assert "error" in res.stderr


def test_bad_usage_exit_2():
    assert run("diff", "onlyone").returncode == 2
    assert run("diff", "a", "b", "--format", "xml").returncode == 2
    assert run().returncode == 2
