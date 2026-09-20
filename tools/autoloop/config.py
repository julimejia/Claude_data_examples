from __future__ import annotations

import glob
import os
import re
import shutil
from dataclasses import dataclass
from pathlib import Path

TOKEN_RE = re.compile(r"^\d{6,12}:[A-Za-z0-9_-]{30,}$")


class ConfigError(Exception):
    pass


def load_env_file(path: Path) -> dict[str, str]:
    values: dict[str, str] = {}
    if not path.is_file():
        return values
    for raw in path.read_text(encoding="utf-8-sig").splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        values[key.strip()] = value.strip().strip('"').strip("'")
    return values


def find_claude() -> str | None:
    """CLAUDE_BIN env, then PATH, then the binary bundled with the VS Code extension."""
    explicit = os.environ.get("CLAUDE_BIN")
    if explicit and Path(explicit).is_file():
        return explicit
    on_path = shutil.which("claude")
    if on_path:
        return on_path
    pattern = str(
        Path.home() / ".vscode" / "extensions" / "anthropic.claude-code-*" / "resources" / "native-binary" / "claude*"
    )
    candidates = [p for p in glob.glob(pattern) if p.lower().endswith((".exe", "claude"))]

    def version_key(p: str) -> list[int]:
        return [int(n) for n in re.findall(r"\d+", Path(p).parents[2].name)]

    return sorted(candidates, key=version_key)[-1] if candidates else None


@dataclass
class Config:
    project_root: Path
    token: str
    chat_id: int
    claude_bin: str | None
    min_iterations: int = 5
    max_iterations: int = 10
    reminder_after_s: int = 15 * 60
    final_after_s: int = 60 * 60
    iteration_timeout_s: int = 30 * 60
    max_messages_per_hour: int = 6
    branch: str = "dev"
    commit_trailer: str = "Co-Authored-By: Claude Sonnet 5 <noreply@anthropic.com>"
    fallback_git_email: str = "davidmejiao43@gmail.com"

    @property
    def state_dir(self) -> Path:
        return self.project_root / ".autoloop"

    @classmethod
    def load(cls, project_root: Path, min_iterations: int = 5, max_iterations: int = 10) -> Config:
        if min_iterations < 1 or max_iterations < min_iterations:
            raise ConfigError("Require 1 <= min_iterations <= max_iterations")
        env = {}
        env.update(load_env_file(Path.home() / ".env"))
        env.update(load_env_file(project_root / ".env"))
        env.update({k: v for k, v in os.environ.items() if k.startswith("TELEGRAM_")})
        token = env.get("TELEGRAM_BOT_TOKEN", "")
        chat = env.get("TELEGRAM_CHAT_ID", "")
        if not TOKEN_RE.match(token):
            raise ConfigError("TELEGRAM_BOT_TOKEN is missing or malformed (set it in ~/.env)")
        if not re.fullmatch(r"-?\d+", chat):
            raise ConfigError("TELEGRAM_CHAT_ID must be the numeric chat id, not a username")
        return cls(
            project_root=project_root,
            token=token,
            chat_id=int(chat),
            claude_bin=find_claude(),
            min_iterations=min_iterations,
            max_iterations=max_iterations,
        )
