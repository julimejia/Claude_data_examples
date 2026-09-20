from __future__ import annotations

import hashlib
import json
from pathlib import Path

from pydantic import ValidationError

from schemasentinel.ports.llm import LLMError, LLMPort, T, Tool


def recording_key(system: str, prompt: str, schema_name: str) -> str:
    payload = json.dumps([system, prompt, schema_name], ensure_ascii=False)
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


class ReplayAdapter:
    """Serves recorded transcripts keyed by hash of (system, prompt, schema name).

    With `record_with`, a missing transcript is obtained from that adapter and stored.
    """

    def __init__(self, directory: Path, *, record_with: LLMPort | None = None) -> None:
        self._dir = Path(directory)
        self._recorder = record_with

    def complete_structured(
        self,
        *,
        system: str,
        prompt: str,
        schema: type[T],
        tools: list[Tool] | None = None,
    ) -> T:
        key = recording_key(system, prompt, schema.__name__)
        path = self._dir / f"{key}.json"
        if not path.exists():
            if self._recorder is None:
                raise LLMError(f"No recording for {schema.__name__} (key {key[:12]})")
            result = self._recorder.complete_structured(
                system=system, prompt=prompt, schema=schema, tools=tools
            )
            self._dir.mkdir(parents=True, exist_ok=True)
            transcript = {
                "system": system,
                "prompt": prompt,
                "schema": schema.__name__,
                "response": result.model_dump(mode="json"),
            }
            path.write_text(
                json.dumps(transcript, indent=2, ensure_ascii=False, sort_keys=True) + "\n",
                encoding="utf-8",
            )
            return result
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
            return schema.model_validate(data["response"])
        except (ValueError, KeyError, ValidationError) as exc:
            raise LLMError(f"Invalid recording {path.name}: {exc}") from exc
