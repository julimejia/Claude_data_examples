from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from typing import Any, Protocol, TypeVar

from pydantic import BaseModel

T = TypeVar("T", bound=BaseModel)


class LLMError(Exception):
    """Raised when an LLM adapter cannot produce a valid structured response."""


@dataclass(frozen=True)
class Tool:
    """A read-only, bounded function the model may call."""

    name: str
    description: str
    handler: Callable[..., Any]


class LLMPort(Protocol):
    def complete_structured(
        self,
        *,
        system: str,
        prompt: str,
        schema: type[T],
        tools: list[Tool] | None = None,
    ) -> T: ...
