"""Vercel Python function: POST /api/diff (public demo, FR-9.1/9.2, NFR-7).

Deterministic diff + rule classification only. Imports the domain/application layers, never the
I/O adapters, LLM or notifiers. Nothing from the request is stored or logged.
"""

from __future__ import annotations

import json
import sys
from http.server import BaseHTTPRequestHandler
from pathlib import Path

_SRC = str(Path(__file__).resolve().parent.parent / "src")
if _SRC not in sys.path:
    sys.path.insert(0, _SRC)

from pydantic import ValidationError  # noqa: E402

from schemasentinel.application.detect_drift import build_report  # noqa: E402
from schemasentinel.domain.models import SchemaSnapshot  # noqa: E402

MAX_BODY_BYTES = 256 * 1024
MAX_COLUMNS = 2000


def handle(body: bytes) -> tuple[int, dict]:
    """Pure request handling: raw body in, (status, JSON-able payload) out."""
    if len(body) > MAX_BODY_BYTES:
        return 400, {"error": f"payload too large (max {MAX_BODY_BYTES // 1024} KB)"}
    try:
        data = json.loads(body)
    except (ValueError, RecursionError):
        return 400, {"error": "body must be valid JSON"}
    if not isinstance(data, dict) or "baseline" not in data or "current" not in data:
        return 400, {"error": 'body must be an object with "baseline" and "current" snapshots'}
    try:
        baseline = SchemaSnapshot.model_validate(data["baseline"])
        current = SchemaSnapshot.model_validate(data["current"])
    except ValidationError as exc:
        details = "; ".join(
            f"{'.'.join(str(p) for p in e['loc'])}: {e['msg']}" for e in exc.errors()[:10]
        )
        return 400, {"error": f"invalid snapshot: {details}"}
    if max(len(baseline.flatten()), len(current.flatten())) > MAX_COLUMNS:
        return 400, {"error": f"too many columns (max {MAX_COLUMNS})"}
    report = build_report(baseline, current, {"llm_calls": 0})
    return 200, report.model_dump(mode="json")


class handler(BaseHTTPRequestHandler):  # noqa: N801 - name required by Vercel's runtime
    def _send(self, status: int, payload: dict) -> None:
        out = json.dumps(payload).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(out)))
        self.end_headers()
        self.wfile.write(out)

    def do_POST(self) -> None:  # noqa: N802
        try:
            length = int(self.headers.get("Content-Length") or 0)
        except ValueError:
            length = -1
        if length < 0:
            self._send(400, {"error": "invalid Content-Length"})
            return
        if length > MAX_BODY_BYTES:
            self._send(400, {"error": f"payload too large (max {MAX_BODY_BYTES // 1024} KB)"})
            return
        status, payload = handle(self.rfile.read(length))
        self._send(status, payload)

    def log_message(self, format: str, *args) -> None:  # noqa: A002
        """Never log requests (NFR-7)."""
