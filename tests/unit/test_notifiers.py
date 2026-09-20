from __future__ import annotations

import io
import logging

import pytest

from schemasentinel.adapters.notify.console import ConsoleNotifier
from schemasentinel.adapters.notify.telegram import TelegramNotifier
from schemasentinel.domain.models import (
    ChangeType,
    DriftReport,
    SchemaChange,
    SchemaSnapshot,
    Severity,
    Verdict,
)
from schemasentinel.ports.notifier import Notification, NotifierError, notification_from_report

TOKEN = "123:SECRET-TOKEN"


def _report() -> DriftReport:
    snap = SchemaSnapshot(source="s", format="csv", columns=())
    change = SchemaChange(
        change_type=ChangeType.COLUMN_REMOVED,
        path="id",
        severity=Severity.BREAKING,
        rule_id="R1",
        reason="column removed",
    )
    return DriftReport(baseline=snap, current=snap, verdict=Verdict.BREAKING, changes=(change,))


def test_notification_from_report_has_verdict_change_and_location():
    text = notification_from_report(_report(), "out/report.md").render()
    assert "breaking" in text
    assert "id: column removed" in text
    assert "out/report.md" in text


def test_console_notifier_prints():
    buf = io.StringIO()
    ConsoleNotifier(buf).send(notification_from_report(_report(), "r.md"))
    assert "r.md" in buf.getvalue()


def test_telegram_sends_to_configured_chat():
    calls = []

    def post(url, payload, timeout):
        calls.append((url, payload))
        return 200, b"{}"

    TelegramNotifier(token=TOKEN, chat_id="42", http_post=post).send(
        notification_from_report(_report(), "r.md")
    )
    url, payload = calls[0]
    assert url.endswith("/sendMessage") and payload["chat_id"] == "42"
    assert "breaking" in payload["text"] and "r.md" in payload["text"]


def test_telegram_allowlist_blocks_other_chat():
    calls = []
    n = TelegramNotifier(
        token=TOKEN, chat_id="99", allowed_chat_ids=["42"], http_post=lambda *a: calls.append(a)
    )
    with pytest.raises(NotifierError):
        n.send(Notification(verdict=Verdict.NONE))
    assert calls == []


def test_token_never_logged_or_in_errors(caplog):
    def boom(url, payload, timeout):
        raise OSError(f"cannot reach {url}")

    n = TelegramNotifier(token=TOKEN, chat_id="42", http_post=boom)
    with caplog.at_level(logging.DEBUG):
        with pytest.raises(NotifierError) as ei:
            n.send(Notification(verdict=Verdict.NONE))
    assert TOKEN not in str(ei.value)

    bad = TelegramNotifier(
        token=TOKEN, chat_id="42", http_post=lambda *a: (401, f"bad {TOKEN}".encode())
    )
    with pytest.raises(NotifierError) as ei:
        bad.send(Notification(verdict=Verdict.NONE))
    assert TOKEN not in str(ei.value)
    assert TOKEN not in caplog.text


def test_from_env(monkeypatch):
    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", TOKEN)
    monkeypatch.setenv("TELEGRAM_CHAT_ID", "7")
    calls = []
    TelegramNotifier.from_env(http_post=lambda u, p, t: calls.append(p) or (200, b"")).send(
        Notification(verdict=Verdict.NONE)
    )
    assert calls[0]["chat_id"] == "7"
