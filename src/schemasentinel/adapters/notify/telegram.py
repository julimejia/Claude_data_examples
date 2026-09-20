from __future__ import annotations

import json
import logging
import os
import urllib.error
import urllib.request
from collections.abc import Callable, Iterable
from typing import Any

from schemasentinel.ports.notifier import Notification, NotifierError

log = logging.getLogger(__name__)

# (url, json payload, timeout) -> (status code, response body)
HttpPost = Callable[[str, dict[str, Any], float], tuple[int, bytes]]

_API = "https://api.telegram.org/bot{token}/sendMessage"
_MAX_LEN = 4096


def _urllib_post(url: str, payload: dict[str, Any], timeout: float) -> tuple[int, bytes]:
    req = urllib.request.Request(
        url,
        data=json.dumps(payload).encode(),
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:  # noqa: S310 - https URL
            return resp.status, resp.read()
    except urllib.error.HTTPError as exc:
        return exc.code, exc.read()


class TelegramNotifier:
    """Sends the notification to one Telegram chat via the Bot API.

    Only `chat_id` is ever contacted, and only if it is in the allowlist (default: just
    `chat_id`). The token is never logged and is scrubbed from error messages.
    """

    def __init__(
        self,
        *,
        token: str,
        chat_id: str,
        allowed_chat_ids: Iterable[str] | None = None,
        timeout: float = 10.0,
        http_post: HttpPost | None = None,
    ) -> None:
        if not token or not chat_id:
            raise NotifierError("Telegram token and chat id are required")
        self._token = token
        self._chat_id = str(chat_id)
        allowed = {str(c) for c in allowed_chat_ids} if allowed_chat_ids is not None else {self._chat_id}
        self._allowed = allowed
        self._timeout = timeout
        self._post: HttpPost = http_post or _urllib_post

    @classmethod
    def from_env(cls, **kwargs: Any) -> TelegramNotifier:
        token = os.environ.get("TELEGRAM_BOT_TOKEN", "")
        chat_id = os.environ.get("TELEGRAM_CHAT_ID", "")
        return cls(token=token, chat_id=chat_id, **kwargs)

    def _scrub(self, text: str) -> str:
        return text.replace(self._token, "***")

    def send(self, message: Notification) -> None:
        if self._chat_id not in self._allowed:
            raise NotifierError(f"chat id {self._chat_id} is not in the allowlist")
        payload = {"chat_id": self._chat_id, "text": message.render()[:_MAX_LEN]}
        try:
            status, body = self._post(_API.format(token=self._token), payload, self._timeout)
        except Exception as exc:  # network errors may embed the URL, and thus the token
            raise NotifierError(self._scrub(f"Telegram request failed: {type(exc).__name__}")) from None
        if status != 200:
            detail = self._scrub(body.decode("utf-8", "replace"))[:200]
            raise NotifierError(f"Telegram returned HTTP {status}: {detail}")
        log.info("telegram notification sent to chat %s", self._chat_id)
