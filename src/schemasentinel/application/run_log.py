from __future__ import annotations

import json
import time
import uuid
from collections import Counter
from typing import Any, TextIO
from urllib.parse import urlsplit, urlunsplit

from schemasentinel.domain.models import DriftReport
from schemasentinel.ports.llm import LLMPort, T, Tool


def safe_source(ref: str) -> str:
    """Drop credentials and query strings from a URL-like source reference."""
    parts = urlsplit(ref)
    if not parts.scheme or not parts.netloc:
        return ref
    host = parts.netloc.rpartition("@")[2]
    return urlunsplit((parts.scheme, host, parts.path, "", ""))


class CountingLLM:
    """LLMPort wrapper that counts calls; it never records prompts or answers."""

    def __init__(self, inner: LLMPort) -> None:
        self._inner = inner
        self.calls = 0
        self.failures = 0

    def complete_structured(
        self, *, system: str, prompt: str, schema: type[T], tools: list[Tool] | None = None
    ) -> T:
        self.calls += 1
        try:
            return self._inner.complete_structured(
                system=system, prompt=prompt, schema=schema, tools=tools
            )
        except Exception:
            self.failures += 1
            raise


class RunLog:
    """One JSON line per run: run id, source, change counts, LLM calls, latency, outcome.

    Only counts and identifiers are logged, never column values, prompts or secrets.
    """

    def __init__(self, stream: TextIO, run_id: str | None = None) -> None:
        self._stream = stream
        self.run_id = run_id or uuid.uuid4().hex
        self._start = time.perf_counter()

    def emit(
        self,
        *,
        command: str,
        sources: list[str],
        outcome: str,
        report: DriftReport | None = None,
        llm: CountingLLM | None = None,
    ) -> dict[str, Any]:
        record: dict[str, Any] = {
            "run_id": self.run_id,
            "command": command,
            "source": [safe_source(s) for s in sources],
            "changes": _change_counts(report),
            "llm_calls": llm.calls if llm else 0,
            "llm_failures": llm.failures if llm else 0,
            "latency_ms": round((time.perf_counter() - self._start) * 1000, 2),
            "outcome": outcome,
        }
        self._stream.write(json.dumps(record, sort_keys=True) + "\n")
        self._stream.flush()
        return record


def _change_counts(report: DriftReport | None) -> dict[str, int]:
    if report is None:
        return {"total": 0}
    counts = Counter(c.severity.value for c in report.changes)
    return {"total": len(report.changes), **dict(sorted(counts.items()))}
