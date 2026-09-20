from __future__ import annotations

import json
import logging
import os
import shutil
import subprocess
from collections.abc import Callable
from typing import Any

from pydantic import ValidationError

from schemasentinel.ports.llm import LLMError, T, Tool

log = logging.getLogger(__name__)

Runner = Callable[..., "subprocess.CompletedProcess[str]"]


def locate_claude_binary() -> str:
    """CLAUDE_BIN env var first, then `claude` on PATH."""
    explicit = os.environ.get("CLAUDE_BIN")
    if explicit:
        return explicit
    found = shutil.which("claude")
    if found is None:
        raise LLMError("claude CLI not found: set CLAUDE_BIN or add `claude` to PATH")
    return found


def _strip_fences(text: str) -> str:
    text = text.strip()
    if text.startswith("```"):
        lines = text.splitlines()[1:]
        if lines and lines[-1].strip().startswith("```"):
            lines = lines[:-1]
        text = "\n".join(lines).strip()
    return text


class ClaudeCliAdapter:
    """Calls `claude -p --output-format json`, validating the reply against a schema.

    An invalid reply is retried up to `max_retries` times (so 3 attempts in total), after
    which LLMError is raised. Prompts and replies are never logged. Tool calling is not
    supported through the CLI; `tools` is ignored.
    """

    def __init__(
        self,
        *,
        binary: str | None = None,
        timeout: float = 120.0,
        max_retries: int = 2,
        runner: Runner | None = None,
    ) -> None:
        self._binary = binary
        self._timeout = timeout
        self._max_retries = max_retries
        self._runner: Runner = runner or subprocess.run

    def complete_structured(
        self,
        *,
        system: str,
        prompt: str,
        schema: type[T],
        tools: list[Tool] | None = None,
    ) -> T:
        binary = self._binary or locate_claude_binary()
        json_schema = json.dumps(schema.model_json_schema())
        full_system = (
            f"{system}\n\nRespond with ONLY a JSON object (no prose, no code fences) "
            f"conforming to this JSON schema:\n{json_schema}"
        )
        cmd = [binary, "-p", "--output-format", "json", "--system-prompt", full_system]
        last_error = "unknown"
        attempts = self._max_retries + 1
        for attempt in range(1, attempts + 1):
            try:
                proc = self._runner(
                    cmd,
                    input=prompt,
                    capture_output=True,
                    text=True,
                    encoding="utf-8",
                    timeout=self._timeout,
                    check=False,
                )
            except subprocess.TimeoutExpired:
                last_error = "timeout"
            except OSError as exc:
                raise LLMError(f"cannot run claude CLI: {type(exc).__name__}") from exc
            else:
                if proc.returncode != 0:
                    last_error = f"exit code {proc.returncode}"
                else:
                    try:
                        return self._parse(proc.stdout, schema)
                    except (ValueError, ValidationError) as exc:
                        last_error = f"invalid output ({type(exc).__name__})"
            log.warning("claude CLI attempt %d/%d failed: %s", attempt, attempts, last_error)
        raise LLMError(f"claude CLI failed after {attempts} attempts: {last_error}")

    @staticmethod
    def _parse(stdout: str, schema: type[T]) -> T:
        envelope: Any = json.loads(stdout)
        if isinstance(envelope, dict) and "result" in envelope:
            if envelope.get("is_error"):
                raise ValueError("CLI reported an error")
            payload = envelope["result"]
            if isinstance(payload, str):
                payload = json.loads(_strip_fences(payload))
        else:
            payload = envelope
        return schema.model_validate(payload)
