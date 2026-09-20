"""Fail if the Vercel demo bundle (function + src/ + static files) exceeds the size limit (FR-9.5).

Usage: python scripts/check_demo_size.py [--limit-mb 50]
Vercel's hard limit for a Python function is 250 MB unzipped; 50 MB keeps us far below it.
Dependencies (pydantic) are installed by Vercel and are not counted here.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
BUNDLE_PATHS = ("api", "src", "public", "requirements.txt", "vercel.json")
DEFAULT_LIMIT_MB = 50


def bundle_size(root: Path = ROOT) -> int:
    """Total bytes of the files Vercel would ship, ignoring bytecode caches."""
    total = 0
    for name in BUNDLE_PATHS:
        path = root / name
        if path.is_file():
            total += path.stat().st_size
        elif path.is_dir():
            for f in path.rglob("*"):
                if f.is_file() and "__pycache__" not in f.parts and f.suffix != ".pyc":
                    total += f.stat().st_size
    return total


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--limit-mb", type=float, default=DEFAULT_LIMIT_MB)
    parser.add_argument("--root", type=Path, default=ROOT)
    args = parser.parse_args(argv)
    size = bundle_size(args.root)
    limit = int(args.limit_mb * 1024 * 1024)
    print(f"demo bundle: {size / 1024 / 1024:.2f} MB (limit {args.limit_mb:g} MB)")
    if size > limit:
        print("FAIL: bundle exceeds the limit", file=sys.stderr)
        return 1
    print("OK")
    return 0


if __name__ == "__main__":
    sys.exit(main())
