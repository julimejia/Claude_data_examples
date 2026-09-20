from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path

from .config import Config, ConfigError
from .gitops import Git
from .loop import AutoLoop, make_verifier
from .runner import ClaudeRunner
from .state import State
from .telegram_client import TelegramClient
from .tracker import Tracker


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(prog="autoloop", description="SchemaSentinel autonomous development loop")
    p.add_argument("--project", default=".", help="project root (default: current dir)")
    p.add_argument("--min-iterations", type=int, default=5)
    p.add_argument("--max-iterations", type=int, default=10)
    p.add_argument("--check", action="store_true", help="validate config, Telegram and claude CLI, then exit")
    args = p.parse_args(argv)

    root = Path(args.project).resolve()
    try:
        cfg = Config.load(root, args.min_iterations, args.max_iterations)
    except ConfigError as exc:
        print(f"config error: {exc}", file=sys.stderr)
        return 2
    tg = TelegramClient(cfg.token, cfg.chat_id)

    if args.check:
        print(f"claude: {cfg.claude_bin or 'NOT FOUND'}")
        print(f"iterations: {cfg.min_iterations}-{cfg.max_iterations}, branch: {cfg.branch}")
        tg.send("Autoloop check: Telegram channel OK.")
        print("telegram: message sent")
        return 0 if cfg.claude_bin else 2

    lock = cfg.state_dir / "lock"
    cfg.state_dir.mkdir(parents=True, exist_ok=True)
    try:
        fd = os.open(lock, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
    except FileExistsError:
        print(f"another autoloop seems to be running (remove {lock} if it is stale)", file=sys.stderr)
        return 2
    os.write(fd, str(os.getpid()).encode())
    os.close(fd)
    try:
        git = Git(root, cfg.branch, cfg.commit_trailer, cfg.fallback_git_email)
        loop = AutoLoop(cfg, tg, Tracker(root / "sdd" / "tracker.md"), git, ClaudeRunner(cfg),
                        State.load(cfg.state_dir / "state.json"), make_verifier(root))
        return loop.run()
    finally:
        lock.unlink(missing_ok=True)


if __name__ == "__main__":
    raise SystemExit(main())
