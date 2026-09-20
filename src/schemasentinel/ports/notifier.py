from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol

from schemasentinel.domain.models import DriftReport, Severity, Verdict


class NotifierError(Exception):
    """Raised when a notification cannot be delivered."""


@dataclass(frozen=True)
class Notification:
    verdict: Verdict
    top_changes: tuple[str, ...] = ()
    report_location: str | None = None

    def render(self) -> str:
        lines = [f"SchemaSentinel verdict: {self.verdict.value}"]
        if self.top_changes:
            lines.append("Top breaking changes:")
            lines.extend(f"- {c}" for c in self.top_changes)
        if self.report_location:
            lines.append(f"Report: {self.report_location}")
        return "\n".join(lines)


def notification_from_report(
    report: DriftReport, location: str | None = None, *, max_changes: int = 5
) -> Notification:
    breaking = [c for c in report.changes if c.severity == Severity.BREAKING]
    top = tuple(f"{c.path}: {c.reason}" for c in breaking[:max_changes])
    return Notification(verdict=report.verdict, top_changes=top, report_location=location)


class Notifier(Protocol):
    def send(self, message: Notification) -> None: ...
