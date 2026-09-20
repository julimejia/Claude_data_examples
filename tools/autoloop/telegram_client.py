from __future__ import annotations

import json
import urllib.error
import urllib.request
from collections.abc import Callable
from dataclasses import dataclass


class TelegramError(Exception):
    pass


@dataclass
class Incoming:
    update_id: int
    kind: str  # "text" | "callback"
    text: str = ""
    reply_to_message_id: int = 0
    callback_id: str = ""


def _urllib_post(url: str, payload: dict, timeout: float) -> dict:
    data = json.dumps(payload).encode("utf-8")
    req = urllib.request.Request(url, data=data, headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return json.loads(resp.read().decode("utf-8"))


class TelegramClient:
    """Minimal Bot API client. Only ever talks to the allow-listed chat id."""

    def __init__(self, token: str, chat_id: int, post: Callable[[str, dict, float], dict] = _urllib_post):
        self._token = token
        self.chat_id = chat_id
        self._post = post

    def _call(self, method: str, payload: dict, timeout: float = 30) -> object:
        url = f"https://api.telegram.org/bot{self._token}/{method}"
        try:
            body = self._post(url, payload, timeout)
        except (urllib.error.URLError, TimeoutError, OSError) as exc:
            # Never include the URL: it contains the token.
            raise TelegramError(f"{method} failed: {type(exc).__name__}") from None
        if not body.get("ok"):
            raise TelegramError(f"{method} rejected: {body.get('description', 'unknown error')}")
        return body["result"]

    def send(self, text: str, buttons: list[list[tuple[str, str]]] | None = None) -> int:
        payload: dict = {"chat_id": self.chat_id, "text": text[:3900]}
        if buttons:
            payload["reply_markup"] = {
                "inline_keyboard": [[{"text": t, "callback_data": d} for t, d in row] for row in buttons]
            }
        result = self._call("sendMessage", payload)
        return int(result["message_id"])  # type: ignore[index]

    def answer_callback(self, callback_id: str, text: str) -> None:
        try:
            self._call("answerCallbackQuery", {"callback_query_id": callback_id, "text": text[:190]})
        except TelegramError:
            pass

    def get_updates(self, offset: int, timeout: int = 0) -> tuple[list[Incoming], int]:
        payload = {"offset": offset, "timeout": timeout, "allowed_updates": ["message", "callback_query"]}
        try:
            updates = self._call("getUpdates", payload, timeout=timeout + 15)
        except TelegramError:
            return [], offset
        out: list[Incoming] = []
        next_offset = offset
        for u in updates:  # type: ignore[union-attr]
            next_offset = max(next_offset, int(u["update_id"]) + 1)
            msg = u.get("message")
            cb = u.get("callback_query")
            if msg and msg.get("chat", {}).get("id") == self.chat_id and msg.get("text"):
                reply = (msg.get("reply_to_message") or {}).get("message_id", 0)
                out.append(Incoming(u["update_id"], "text", msg["text"], int(reply)))
            elif cb and (cb.get("message") or {}).get("chat", {}).get("id") == self.chat_id:
                out.append(Incoming(u["update_id"], "callback", cb.get("data", ""), callback_id=cb["id"]))
            # anything else (other chats) is ignored but its offset is still consumed
        return out, next_offset
