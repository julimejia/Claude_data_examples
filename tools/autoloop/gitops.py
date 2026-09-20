from __future__ import annotations

import re
import subprocess
from pathlib import Path

# Patterns assembled from parts so this file never matches its own scan.
SECRET_PATTERNS = [
    re.compile(r"\b\d{8,12}" + r":" + r"[A-Za-z0-9_-]{35}\b"),  # Telegram bot token
    re.compile(r"sk-" + r"ant-[A-Za-z0-9_-]{20,}"),  # Anthropic API key
]


class GitError(Exception):
    pass


class SecretDetected(GitError):
    pass


class Git:
    def __init__(self, root: Path, branch: str, trailer: str, fallback_email: str):
        self.root = root
        self.branch = branch
        self.trailer = trailer
        self.fallback_email = fallback_email

    def _git(self, *args: str, check: bool = True) -> str:
        proc = subprocess.run(
            ["git", *args], cwd=self.root, capture_output=True, text=True, encoding="utf-8", errors="replace"
        )
        if check and proc.returncode != 0:
            raise GitError(f"git {' '.join(args[:2])} failed: {proc.stderr.strip()[:300]}")
        return proc.stdout

    def _identity(self) -> list[str]:
        name = self._git("config", "user.name", check=False).strip()
        email = self._git("config", "user.email", check=False).strip()
        args: list[str] = []
        if not name:
            args += ["-c", "user.name=SchemaSentinel autoloop"]
        if not email:
            args += ["-c", f"user.email={self.fallback_email}"]
        return args

    def current_branch(self) -> str:
        return self._git("symbolic-ref", "--short", "HEAD").strip()

    def ensure_branch(self) -> None:
        """Stay on the working branch; never main/master."""
        if self.branch in ("main", "master"):
            raise GitError("Refusing to work on main/master")
        if self.current_branch() != self.branch:
            self._git("checkout", "-B", self.branch)

    def has_changes(self) -> bool:
        return bool(self._git("status", "--porcelain").strip())

    def _scan_staged(self) -> None:
        names = self._git("diff", "--cached", "--name-only").split()
        if any(Path(n).name == ".env" for n in names):
            raise SecretDetected("A .env file is staged")
        diff = self._git("diff", "--cached", "-U0")
        added = [ln[1:] for ln in diff.splitlines() if ln.startswith("+") and not ln.startswith("+++")]
        for line in added:
            for pat in SECRET_PATTERNS:
                if pat.search(line):
                    raise SecretDetected("A secret-looking value is staged")

    def commit_all(self, message: str) -> bool:
        self.ensure_branch()
        if not self.has_changes():
            return False
        self._git("add", "-A")
        try:
            self._scan_staged()
        except SecretDetected:
            self._git("reset", check=False)
            raise
        full = f"{message}\n\n{self.trailer}"
        self._git(*self._identity(), "commit", "-m", full)
        return True

    def stash(self, label: str) -> None:
        if self.has_changes():
            self._git(*self._identity(), "stash", "push", "-u", "-m", label)

    def push(self) -> bool:
        """Push the working branch only, and only if a remote named origin exists."""
        if "origin" not in self._git("remote").split():
            return False
        self._git("push", "origin", f"{self.branch}:{self.branch}", check=False)
        return True
