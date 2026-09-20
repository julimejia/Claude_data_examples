from __future__ import annotations

import json
import subprocess
import sys
import threading
from http.server import HTTPServer
from pathlib import Path
from urllib.error import HTTPError
from urllib.request import Request, urlopen

import pytest

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "api"))

import diff as api  # noqa: E402

from schemasentinel.evals.golden import load_cases, parse_columns  # noqa: E402


def _snap(cols, fmt="parquet"):
    return {
        "source": "x",
        "format": fmt,
        "columns": [c.model_dump(mode="json") for c in parse_columns(cols)],
    }


CASES = load_cases(ROOT / "tests" / "golden" / "cases")


@pytest.mark.parametrize("case", CASES, ids=lambda c: c.id)
def test_matches_golden_classification(case):
    body = json.dumps(
        {"baseline": _snap(case.baseline, case.format), "current": _snap(case.current, case.format)}
    ).encode()
    status, payload = api.handle(body)
    assert status == 200
    assert payload["verdict"] == case.expected.verdict.value
    got = sorted(
        (c["change_type"], c["path"], c["severity"], c["rule_id"]) for c in payload["changes"]
    )
    assert got == sorted(c.key() for c in case.expected.changes)


def test_invalid_inputs_return_400():
    assert api.handle(b"not json")[0] == 400
    assert api.handle(b"[]")[0] == 400
    assert api.handle(b'{"baseline": {}}')[0] == 400
    status, payload = api.handle(b'{"baseline": {"columns": 1}, "current": {}}')
    assert status == 400 and "invalid snapshot" in payload["error"]


def test_oversized_and_too_many_columns_return_400():
    assert api.handle(b" " * (api.MAX_BODY_BYTES + 1))[0] == 400
    cols = [f"c{i}:INTEGER" for i in range(api.MAX_COLUMNS + 1)]
    body = json.dumps({"baseline": _snap(cols), "current": _snap(cols)}).encode()
    if len(body) <= api.MAX_BODY_BYTES:
        assert api.handle(body)[0] == 400


def test_http_handler_and_no_logging(capsys):
    server = HTTPServer(("127.0.0.1", 0), api.handler)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    url = f"http://127.0.0.1:{server.server_port}/api/diff"
    body = json.dumps({"baseline": _snap(["a:INTEGER"]), "current": _snap(["a:BIGINT"])}).encode()
    try:
        with urlopen(Request(url, data=body, method="POST")) as r:
            assert r.status == 200
            assert json.loads(r.read())["verdict"] == "non_breaking"
        with pytest.raises(HTTPError) as err:
            urlopen(Request(url, data=b"{", method="POST"))
        assert err.value.code == 400
    finally:
        server.shutdown()
    assert capsys.readouterr().err == ""


def test_function_does_not_import_heavy_modules():
    code = (
        "import sys; sys.path.insert(0, 'api'); import diff; "
        "bad = [m for m in ('duckdb','deltalake','sqlglot','schemasentinel.adapters',"
        "'schemasentinel.cli') if m in sys.modules]; print(bad)"
    )
    out = subprocess.run(
        [sys.executable, "-c", code], cwd=ROOT, capture_output=True, text=True, check=True
    )
    assert out.stdout.strip() == "[]"
