from __future__ import annotations

import io
import json
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "api"))

import diff as api  # noqa: E402


def _col(name, typ="INTEGER", pos=0, **kw):
    return {"name": name, "data_type": typ, "position": pos, **kw}


def _snap(cols):
    return {"source": "s", "format": "parquet", "columns": cols}


def _body(b, c):
    return json.dumps({"baseline": b, "current": c}).encode()


def test_identical_snapshots_no_drift():
    s = _snap([_col("a")])
    status, payload = api.handle(_body(s, s))
    assert status == 200
    assert payload["changes"] == []


def test_empty_columns_ok():
    status, payload = api.handle(_body(_snap([]), _snap([])))
    assert status == 200 and payload["changes"] == []


def test_dropped_column_is_breaking_and_llm_calls_zero():
    status, payload = api.handle(_body(_snap([_col("a"), _col("b", pos=1)]), _snap([_col("a")])))
    assert status == 200
    assert payload["verdict"] == "breaking"
    assert payload["run_metadata"]["llm_calls"] == 0


def test_idempotent():
    b = _body(_snap([_col("a")]), _snap([_col("a", "BIGINT")]))
    assert api.handle(b) == api.handle(b)


@pytest.mark.parametrize(
    "body",
    [
        b"",
        b"null",
        b"42",
        b'"str"',
        b"{}",
        b'{"current": {}}',
        b'{"baseline": null, "current": null}',
        b'{"baseline": [], "current": []}',
        b"\xff\xfe",
        b"[" * 100000,
    ],
    ids=lambda b: repr(b[:12]),
)
def test_malformed_bodies_400(body):
    status, payload = api.handle(body)
    assert status == 400
    assert isinstance(payload["error"], str)


def test_missing_required_fields_400_and_details_capped():
    status, payload = api.handle(_body({"columns": [{}] * 30}, _snap([])))
    assert status == 400
    assert payload["error"].count(";") <= 9


def test_wrong_column_types_400():
    bad = _snap([_col("a", pos="x")])
    assert api.handle(_body(bad, _snap([])))[0] == 400
    assert api.handle(_body(_snap([_col("a", nullable="maybe")]), _snap([])))[0] == 400


def test_error_does_not_echo_input_values():
    secret = "SUPERSECRETVALUE"
    status, payload = api.handle(_body(_snap([_col("a", pos=secret)]), _snap([])))
    assert status == 400
    assert secret not in json.dumps(payload)


def test_body_size_boundary():
    pad = api.MAX_BODY_BYTES
    assert api.handle(b" " * pad)[0] == 400  # exactly max: parses as invalid JSON, not "too large"
    status, payload = api.handle(b" " * (pad + 1))
    assert status == 400 and "too large" in payload["error"]


def test_column_limit_counts_nested_children():
    kids = [_col(f"k{i}", pos=i) for i in range(api.MAX_COLUMNS)]
    snap = _snap([_col("s", "STRUCT", children=kids)])
    if len(_body(snap, snap)) <= api.MAX_BODY_BYTES:
        status, payload = api.handle(_body(snap, snap))
        assert status == 400 and "too many columns" in payload["error"]


def test_column_limit_checked_on_either_side():
    cols = [_col(f"c{i}", pos=i) for i in range(api.MAX_COLUMNS + 1)]
    big = _snap(cols)
    if len(_body(big, _snap([]))) <= api.MAX_BODY_BYTES:
        assert api.handle(_body(_snap([]), big))[0] == 400
        assert api.handle(_body(big, _snap([])))[0] == 400


def _run_handler(headers, body):
    """Drive do_POST without a socket."""
    sent = {}

    class H(api.handler):
        def __init__(self):
            self.headers = headers
            self.rfile = io.BytesIO(body)
            self.wfile = io.BytesIO()

        def send_response(self, code, message=None):
            sent["status"] = code

        def send_header(self, k, v):
            pass

        def end_headers(self):
            pass

    h = H()
    h.do_POST()
    return sent["status"], json.loads(h.wfile.getvalue())


def test_content_length_invalid_400():
    assert _run_handler({"Content-Length": "abc"}, b"{}")[0] == 400
    assert _run_handler({"Content-Length": "-5"}, b"{}")[0] == 400


def test_content_length_over_limit_400_without_reading():
    status, payload = _run_handler({"Content-Length": str(api.MAX_BODY_BYTES + 1)}, b"{}")
    assert status == 400 and "too large" in payload["error"]


def test_missing_content_length_gives_400_not_crash():
    status, _ = _run_handler({}, b"")
    assert status == 400


def test_valid_request_via_handler_200():
    body = _body(_snap([_col("a")]), _snap([_col("a", "BIGINT")]))
    status, payload = _run_handler({"Content-Length": str(len(body))}, body)
    assert status == 200 and "verdict" in payload


def test_log_message_silent(capsys):
    h = api.handler.__new__(api.handler)
    h.log_message("%s", "secret")
    out = capsys.readouterr()
    assert out.out == "" and out.err == ""
