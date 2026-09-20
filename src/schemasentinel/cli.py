from __future__ import annotations

import argparse
import sys
from collections.abc import Sequence
from pathlib import Path

from schemasentinel.adapters.llm.claude_cli import ClaudeCliAdapter
from schemasentinel.adapters.llm.replay import ReplayAdapter
from schemasentinel.adapters.notify.console import ConsoleNotifier
from schemasentinel.adapters.notify.telegram import TelegramNotifier
from schemasentinel.adapters.sources.local_files import LocalFilesSource
from schemasentinel.application.detect_drift import DetectDrift
from schemasentinel.application.explain import Explain
from schemasentinel.application.propose_migration import ProposeMigration
from schemasentinel.application.report import render_json, render_markdown
from schemasentinel.application.resolve import Resolve
from schemasentinel.application.run_log import CountingLLM, RunLog
from schemasentinel.domain.migration import Dialect
from schemasentinel.domain.models import DriftReport, Explanation, SchemaSnapshot, Verdict
from schemasentinel.ports.llm import LLMPort
from schemasentinel.ports.notifier import Notifier, notification_from_report
from schemasentinel.ports.schema_source import SchemaSource

LLM_CHOICES = ("none", "replay", "claude-cli")
NOTIFY_CHOICES = ("telegram", "console")
DEFAULT_REPLAY_DIR = Path("evals") / "replay"

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
        default=None,
        help="DDL dialect; adds a proposed migration to the report (default with --llm: duckdb)",
    )
    d.add_argument("--table", default="dataset", help="table name used in the DDL")
    d.add_argument(
        "--llm",
        choices=LLM_CHOICES,
        default="none",
        help="run the agent stages (resolve, explain) with this LLM; none = deterministic only",
    )
    d.add_argument(
        "--replay-dir",
        default=str(DEFAULT_REPLAY_DIR),
        help="recordings directory for --llm replay",
    )
    d.add_argument("--notify", choices=NOTIFY_CHOICES, help="send the summary to a notifier")
    d.add_argument("--format", choices=("json", "md"), default="json", dest="fmt")
    d.add_argument("-o", "--output", help="output file (default: stdout)")
    return parser


def _make_llm(name: str, replay_dir: str) -> LLMPort | None:
    if name == "replay":
        return ReplayAdapter(Path(replay_dir))
    if name == "claude-cli":
        return ClaudeCliAdapter()
    return None


def _make_notifier(name: str) -> Notifier:
    if name == "telegram":
        return TelegramNotifier.from_env()
    return ConsoleNotifier(sys.stderr)


def _warn(message: str) -> None:
    print(f"warning: {message}", file=sys.stderr)


def _agent_stages(
    report: DriftReport, llm: LLMPort | None
) -> tuple[DriftReport, Explanation | None]:
    """Resolve + Explain; an LLM failure degrades the result but never fails the run."""
    if llm is None:
        return report, None
    try:
        report = Resolve(llm).run(report)
    except Exception as exc:  # noqa: BLE001 - LLM problems must not fail the run
        _warn(f"resolve stage skipped: {type(exc).__name__}")
    try:
        return report, Explain(llm).run(report)
    except Exception as exc:  # noqa: BLE001
        _warn(f"explain stage skipped: {type(exc).__name__}")
        return report, None


def _emit(text: str, output: str | None) -> None:
    if output:
        Path(output).write_text(text, encoding="utf-8")
    else:
        sys.stdout.write(text if text.endswith("\n") else text + "\n")


def _notify(
    report: DriftReport, location: str | None, notifier: Notifier | None, name: str | None
) -> None:
    """Delivery failures are reported as a warning; they never change the exit code."""
    try:
        notifier = notifier or _make_notifier(name or "console")
        notifier.send(notification_from_report(report, location))
    except Exception as exc:  # noqa: BLE001
        _warn(f"notification not sent: {type(exc).__name__}")


def main(
    argv: Sequence[str] | None = None,
    source: SchemaSource | None = None,
    llm: LLMPort | None = None,
    notifier: Notifier | None = None,
) -> int:
    """`llm` and `notifier` override the ones built from --llm / --notify (used by tests)."""
    try:
        args = build_parser().parse_args(argv)
    except SystemExit as exc:  # argparse exits; return the code so callers get an int
        return exc.code if isinstance(exc.code, int) else EXIT_ERROR
    source = source or CliSource()
    runlog = RunLog(sys.stderr)
    counting: CountingLLM | None = None
    report: DriftReport | None = None
    outcome = "error"
    refs = [args.source] if args.command == "snapshot" else [args.baseline, args.current]
    try:
        if args.command == "snapshot":
            snapshot = source.snapshot(args.source)
            _emit(snapshot.model_dump_json(indent=2), args.output)
            outcome = "ok"
            return EXIT_OK
        report = DetectDrift(source).run(args.baseline, args.current)
        inner = llm or _make_llm(args.llm, args.replay_dir)
        counting = CountingLLM(inner) if inner is not None else None
        report, explanation = _agent_stages(report, counting)
        migration = None
        if args.dialect is not None or args.llm != "none" or llm is not None:
            dialect = Dialect(args.dialect or "duckdb")
            migration = ProposeMigration().run(report, dialect, args.table)
        if args.fmt == "json":
            text = render_json(report, explanation, migration)
        else:
            text = render_markdown(report, explanation, migration)
        _emit(text, args.output)
        if args.notify or notifier is not None:
            _notify(report, args.output, notifier, args.notify)
        outcome = report.verdict.value
    except Exception as exc:  # noqa: BLE001 - CLI boundary: any failure is exit code 2
        print(f"error: {exc}", file=sys.stderr)
        return EXIT_ERROR
    finally:
        runlog.emit(
            command=args.command, sources=refs, outcome=outcome, report=report, llm=counting
        )
    return EXIT_BREAKING if report.verdict is Verdict.BREAKING else EXIT_OK


if __name__ == "__main__":
    raise SystemExit(main())
