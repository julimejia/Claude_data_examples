from __future__ import annotations

from typing import Protocol

from schemasentinel.domain.models import SchemaSnapshot


class SchemaSource(Protocol):
    def snapshot(self, ref: str, *, version: str | None = None) -> SchemaSnapshot: ...
