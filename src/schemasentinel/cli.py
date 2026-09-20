from __future__ import annotations

import argparse
import sys
from collections.abc import Sequence
from pathlib import Path

from schemasentinel.adapters.sources.local_files import LocalFilesSource
from schemasentinel.application.detect_drift import DetectDrift
from schemasentinel.application.report import render_json, render_markdown
from schemasentinel.domain.models import SchemaSnapshot, Verdict
from schemasentinel.ports.schema_source import SchemaSource

EXIT_OK = 0
EXIT_BREAKING = 1
EXIT_ERROR = 2

DIALECTS = ("duckdb", "spark", "tsql")


class CliSource:
    """Composition-root source: saved snapshot ``.json`` files, otherwise local data files."""

    def __init__(self, files: SchemaSource | None = None) -> None:
        self._files = files or LocalFilesSource()

    def snapshot(self, ref: str, *, version: str | None = None) -> SchemaSnapshot:
        path = Path(ref)
        if path.suffix.lower() == ".json" and path.is_file():
            if version is not None:
                raise ValueError("version is not supported for snapshot files")
            return SchemaSnapshot.model_validate_json(path.read_text(encoding="utf-8"))
        return self._files.snapshot(ref, version=version)


class _Parser(argparse.ArgumentParser):
    def error(self, message: str) -> None:
        self.print_usage(sys.stderr)
        self.exit(EXIT_ERROR, f"{self.prog}: error: {message}\n")


def build_parser() -> argparse.ArgumentParser:
    parser = _Parser(prog="schemasentinel", description="Detect schema drift.")
    sub = parser.add_subparsers(dest="command", required=True, parser_class=_Parser)

    snap = sub.add_parser("snapshot", help="write the schema snapshot of a source as JSON")
    snap.add_argument("source")
    snap.add_argument("-o", "--output", help="output file (default: stdout)")

    d = sub.add_parser("diff", help="compare baseline and current sources")
    d.add_argument("baseline")
    d.add_argument("current")
    d.add_argument(
        "--dialect",
        choices=DIALECTS,
        default="duckdb",
        help="DDL dialect (reserved: migration proposals are not generated yet)",
    )
    d.add_argument("--format", choices=("json", "md"), default="json", dest="fmt")
    d.add_argument("-o", "--output", help="output file (default: stdout)")
    return parser


def _emit(text: str, output: str | None) -> None:
    if output:
        Path(output).write_text(text, encoding="utf-8")
    else:
        sys.stdout.write(text if text.endswith("\n") else text + "\n")


def main(argv: Sequence[str] | None = None, source: SchemaSource | None = None) -> int:
    args = build_parser().parse_args(argv)
    source = source or CliSource()
    try:
        if args.command == "snapshot":
            snapshot = source.snapshot(args.source)
            _emit(snapshot.model_dump_json(indent=2), args.output)
            return EXIT_OK
        report = DetectDrift(source).run(args.baseline, args.current)
        text = render_json(report) if args.fmt == "json" else render_markdown(report)
        _emit(text, args.output)
    except Exception as exc:  # noqa: BLE001 - CLI boundary: any failure is exit code 2
        print(f"error: {exc}", file=sys.stderr)
        return EXIT_ERROR
    return EXIT_BREAKING if report.verdict is Verdict.BREAKING else EXIT_OK


if __name__ == "__main__":
    raise SystemExit(main())
