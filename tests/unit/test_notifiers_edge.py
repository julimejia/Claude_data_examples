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


def _report(changes=()) -> DriftReport:
    snap = SchemaSnapshot(source="s", format="csv", columns=())
    verdict = Verdict.BREAKING if changes else Verdict.NONE
    return DriftReport(baseline=snap, current=snap, verdict=verdict, changes=tuple(changes))


def _change(path: str, severity=Severity.BREAKING) -> SchemaChange:
    return SchemaChange(
        change_type=ChangeType.COLUMN_REMOVED,
        path=path,
        severity=severity,
        rule_id="R1",
        reason="gone",
    )


def _ok(calls):
    def post(url, payload, timeout):
        calls.append((url, payload, timeout))
        return 200, b"{}"

    return post


# ---- Notification / notification_from_report ----


def test_render_minimal_is_single_line():
    assert "\n" not in Notification(verdict=Verdict.NONE).render()


def test_render_empty_location_omitted():
    assert "Report:" not in Notification(verdict=Verdict.NONE, report_location="").render()


def test_from_report_limits_to_max_changes():
    rep = _report([_change(f"c{i}") for i in range(10)])
    n = notification_from_report(rep, max_changes=3)
    assert n.top_changes == ("c0: gone", "c1: gone", "c2: gone")


def test_from_report_only_breaking_changes():
    rep = _report([_change("a", Severity.BREAKING), _change("b", Severity.WARNING)])
    n = notification_from_report(rep)
    assert n.top_changes == ("a: gone",)


def test_from_report_no_changes_no_location():
    text = notification_from_report(_report()).render()
    assert "Top breaking changes" not in text and "Report:" not in text


def test_from_report_default_cap_is_five():
    rep = _report([_change(f"c{i}") for i in range(9)])
    assert len(notification_from_report(rep).top_changes) == 5


# ---- ConsoleNotifier ----


def test_console_defaults_to_stdout(capsys):
    ConsoleNotifier().send(Notification(verdict=Verdict.NONE, report_location="x.md"))
    out = capsys.readouterr().out
    assert "x.md" in out and out.endswith("\n")


def test_console_repeated_sends_append():
    buf = io.StringIO()
    n = ConsoleNotifier(buf)
    n.send(Notification(verdict=Verdict.NONE))
    n.send(Notification(verdict=Verdict.NONE))
    assert buf.getvalue().count("SchemaSentinel verdict") == 2


# ---- TelegramNotifier construction ----


@pytest.mark.parametrize("token,chat", [("", "1"), (TOKEN, ""), ("", "")])
def test_missing_token_or_chat_rejected(token, chat):
    with pytest.raises(NotifierError):
        TelegramNotifier(token=token, chat_id=chat)


def test_from_env_missing_vars_rejected(monkeypatch):
    monkeypatch.delenv("TELEGRAM_BOT_TOKEN", raising=False)
    monkeypatch.delenv("TELEGRAM_CHAT_ID", raising=False)
    with pytest.raises(NotifierError):
        TelegramNotifier.from_env()


def test_from_env_missing_chat_only(monkeypatch):
    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", TOKEN)
    monkeypatch.delenv("TELEGRAM_CHAT_ID", raising=False)
    with pytest.raises(NotifierError):
        TelegramNotifier.from_env()


# ---- allowlist ----


def test_default_allowlist_permits_own_chat():
    calls = []
    TelegramNotifier(token=TOKEN, chat_id="5", http_post=_ok(calls)).send(
        Notification(verdict=Verdict.NONE)
    )
    assert len(calls) == 1


def test_empty_allowlist_blocks_everything():
    calls = []
    n = TelegramNotifier(token=TOKEN, chat_id="5", allowed_chat_ids=[], http_post=_ok(calls))
    with pytest.raises(NotifierError):
        n.send(Notification(verdict=Verdict.NONE))
    assert calls == []


def test_allowlist_accepts_int_entries_for_str_chat():
    calls = []
    n = TelegramNotifier(token=TOKEN, chat_id="5", allowed_chat_ids=[5], http_post=_ok(calls))
    n.send(Notification(verdict=Verdict.NONE))
    assert calls[0][1]["chat_id"] == "5"


def test_int_chat_id_is_normalised_to_str():
    calls = []
    n = TelegramNotifier(token=TOKEN, chat_id=5, http_post=_ok(calls))  # type: ignore[arg-type]
    n.send(Notification(verdict=Verdict.NONE))
    assert calls[0][1]["chat_id"] == "5"


def test_allowlist_is_exact_match_not_substring():
    calls = []
    n = TelegramNotifier(token=TOKEN, chat_id="42", allowed_chat_ids=["420"], http_post=_ok(calls))
    with pytest.raises(NotifierError):
        n.send(Notification(verdict=Verdict.NONE))
    assert calls == []


def test_allowlist_accepts_generator():
    calls = []
    n = TelegramNotifier(
        token=TOKEN, chat_id="1", allowed_chat_ids=(c for c in ["1", "2"]), http_post=_ok(calls)
    )
    n.send(Notification(verdict=Verdict.NONE))
    n.send(Notification(verdict=Verdict.NONE))  # generator must have been materialised
    assert len(calls) == 2


def test_allowlist_rejection_message_has_no_token():
    n = TelegramNotifier(token=TOKEN, chat_id="9", allowed_chat_ids=["1"], http_post=_ok([]))
    with pytest.raises(NotifierError) as ei:
        n.send(Notification(verdict=Verdict.NONE))
    assert TOKEN not in str(ei.value)


# ---- payload ----


def test_text_truncated_to_4096():
    calls = []
    n = TelegramNotifier(token=TOKEN, chat_id="1", http_post=_ok(calls))
    n.send(Notification(verdict=Verdict.BREAKING, top_changes=("x" * 10000,)))
    assert len(calls[0][1]["text"]) == 4096


def test_short_text_not_truncated():
    calls = []
    msg = Notification(verdict=Verdict.NONE, report_location="r.md")
    TelegramNotifier(token=TOKEN, chat_id="1", http_post=_ok(calls)).send(msg)
    assert calls[0][1]["text"] == msg.render()


def test_timeout_is_passed_through():
    calls = []
    TelegramNotifier(token=TOKEN, chat_id="1", timeout=2.5, http_post=_ok(calls)).send(
        Notification(verdict=Verdict.NONE)
    )
    assert calls[0][2] == 2.5


def test_url_contains_token_and_uses_https():
    calls = []
    TelegramNotifier(token=TOKEN, chat_id="1", http_post=_ok(calls)).send(
        Notification(verdict=Verdict.NONE)
    )
    assert calls[0][0] == f"https://api.telegram.org/bot{TOKEN}/sendMessage"


def test_unicode_text_passes_through():
    calls = []
    TelegramNotifier(token=TOKEN, chat_id="1", http_post=_ok(calls)).send(
        Notification(verdict=Verdict.NONE, top_changes=("colonne supprimée ✗",))
    )
    assert "supprimée ✗" in calls[0][1]["text"]


# ---- failure modes ----


@pytest.mark.parametrize("status", [201, 204, 400, 401, 403, 429, 500, 502])
def test_non_200_raises(status):
    n = TelegramNotifier(token=TOKEN, chat_id="1", http_post=lambda *a: (status, b"err"))
    with pytest.raises(NotifierError, match=str(status)):
        n.send(Notification(verdict=Verdict.NONE))


def test_error_body_is_capped_and_scrubbed():
    body = ("y" * 500 + TOKEN).encode()
    n = TelegramNotifier(token=TOKEN, chat_id="1", http_post=lambda *a: (500, body))
    with pytest.raises(NotifierError) as ei:
        n.send(Notification(verdict=Verdict.NONE))
    assert TOKEN not in str(ei.value)
    assert len(str(ei.value)) < 300


def test_error_body_invalid_utf8_does_not_crash():
    n = TelegramNotifier(token=TOKEN, chat_id="1", http_post=lambda *a: (500, b"\xff\xfe\x00"))
    with pytest.raises(NotifierError):
        n.send(Notification(verdict=Verdict.NONE))


def test_error_body_scrubs_repeated_token():
    body = f"{TOKEN} and {TOKEN}".encode()
    n = TelegramNotifier(token=TOKEN, chat_id="1", http_post=lambda *a: (400, body))
    with pytest.raises(NotifierError) as ei:
        n.send(Notification(verdict=Verdict.NONE))
    assert TOKEN not in str(ei.value)


def test_transport_exception_wrapped_and_not_chained_with_token():
    def boom(url, payload, timeout):
        raise TimeoutError(f"timed out {url}")

    n = TelegramNotifier(token=TOKEN, chat_id="1", http_post=boom)
    with pytest.raises(NotifierError) as ei:
        n.send(Notification(verdict=Verdict.NONE))
    assert ei.value.__cause__ is None
    assert TOKEN not in repr(ei.value)


def test_transport_exception_token_not_reachable_via_traceback_chain():
    def boom(url, payload, timeout):
        raise OSError(f"cannot reach {url}")

    n = TelegramNotifier(token=TOKEN, chat_id="1", http_post=boom)
    with pytest.raises(NotifierError) as ei:
        n.send(Notification(verdict=Verdict.NONE))
    assert ei.value.__suppress_context__ is True


def test_token_not_logged_on_success(caplog):
    n = TelegramNotifier(token=TOKEN, chat_id="1", http_post=_ok([]))
    with caplog.at_level(logging.DEBUG):
        n.send(Notification(verdict=Verdict.NONE))
    assert TOKEN not in caplog.text


def test_token_not_in_repr_or_str_of_notifier():
    n = TelegramNotifier(token=TOKEN, chat_id="1", http_post=_ok([]))
    assert TOKEN not in repr(n) and TOKEN not in str(n)


def test_blocked_send_does_not_log_token(caplog):
    n = TelegramNotifier(token=TOKEN, chat_id="9", allowed_chat_ids=["1"], http_post=_ok([]))
    with caplog.at_level(logging.DEBUG):
        with pytest.raises(NotifierError):
            n.send(Notification(verdict=Verdict.NONE))
    assert TOKEN not in caplog.text


def test_send_is_repeatable_after_failure():
    responses = iter([(500, b"x"), (200, b"{}")])
    n = TelegramNotifier(token=TOKEN, chat_id="1", http_post=lambda *a: next(responses))
    with pytest.raises(NotifierError):
        n.send(Notification(verdict=Verdict.NONE))
    n.send(Notification(verdict=Verdict.NONE))
