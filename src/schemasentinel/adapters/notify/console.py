from __future__ import annotations

import sys
from typing import TextIO

from schemasentinel.ports.notifier import Notification


class ConsoleNotifier:
    def __init__(self, stream: TextIO | None = None) -> None:
        self._stream = stream

    def send(self, message: Notification) -> None:
        print(message.render(), file=self._stream or sys.stdout)
