from __future__ import annotations

from dataclasses import dataclass

from pydantic import BaseModel

from schemasentinel.ports.llm import LLMError, T, Tool


@dataclass(frozen=True)
class FakeCall:
    system: str
    prompt: str
    schema: type[BaseModel]


class FakeLLM:
    """Scripted LLM for unit tests: returns queued responses in order and records calls.

    A queued item may be a model instance (returned as is) or an Exception (raised).
    """

    def __init__(self, responses: list[BaseModel | Exception] | None = None) -> None:
        self._responses = list(responses or [])
        self.calls: list[FakeCall] = []

    def complete_structured(
        self,
        *,
        system: str,
        prompt: str,
        schema: type[T],
        tools: list[Tool] | None = None,
    ) -> T:
        self.calls.append(FakeCall(system, prompt, schema))
        if not self._responses:
            raise LLMError("FakeLLM has no more queued responses")
        item = self._responses.pop(0)
        if isinstance(item, Exception):
            raise item
        if not isinstance(item, schema):
            raise LLMError(f"FakeLLM queued {type(item).__name__}, expected {schema.__name__}")
        return item
