# *******************************************************************************
# Copyright (c) 2026 Contributors to the Eclipse Foundation
#
# See the NOTICE file(s) distributed with this work for additional
# information regarding copyright ownership.
#
# This program and the accompanying materials are made available under the
# terms of the Apache License Version 2.0 which is available at
# https://www.apache.org/licenses/LICENSE-2.0
#
# SPDX-License-Identifier: Apache-2.0
# *******************************************************************************

"""Analyse the C/C++ extractor logs of a CodeQL database.

Reports parse/syntax errors in GCC style (sorted by file) and applies
configurable extraction-quality thresholds.

Exit codes: 0 = thresholds met, 1 = a threshold failed or the database is invalid.
"""

from __future__ import annotations

import argparse
import logging
import re
import sys
from collections import defaultdict
from dataclasses import dataclass, field
from pathlib import Path

from score_codeql.errors import ScaError
from score_codeql.logging_utils import setup_logging

_RE_PROCESSED_SOURCE = re.compile(r"\bProcessed command line:.*\s--\s+(\S+)\s*$")
_RE_CMD_SOURCE = re.compile(r"\bCommand:.*\s-c\s+(\S+)\s")
_RE_PARSE_ERROR = re.compile(
    r"Warning\[extractor-c\+\+\]:\s+In construct_text_message:\s+"
    r'"([^"]+)",\s+line\s+(\d+):\s+(catastrophic error|error|warning|note):\s+(.*)'
)
_RE_INTERNAL_WARN = re.compile(r"Warning\[extractor-c\+\+\]:\s+In (\w+):\s+(.*)")
_SUCCESS = "Extractor exited successfully"
_FAILURE = "Extractor exiting with code"
_INTERNAL_FILE = "<extractor-internal>"

Issue = tuple[str, int, str, str]


@dataclass
class DbReport:
    """Aggregated extractor-log statistics and threshold verdict."""

    total_logs: int = 0
    failed_logs: int = 0
    logs_with_errors: int = 0
    logs_with_internal: int = 0
    unique_parse: int = 0
    unique_internal: int = 0
    show_all_warnings: bool = False
    issues: list[tuple[Issue, set[str]]] = field(default_factory=list)
    failures: list[str] = field(default_factory=list)

    @property
    def failed_pct(self) -> float:
        return self.failed_logs / self.total_logs * 100 if self.total_logs else 0.0

    @property
    def issues_pct(self) -> float:
        return self.logs_with_errors / self.total_logs * 100 if self.total_logs else 0.0

    @property
    def passed(self) -> bool:
        return not self.failures

    def to_dict(self) -> dict[str, object]:
        """Statistics for ``summary.md``."""
        return {
            "total_logs": self.total_logs,
            "failed_logs": self.failed_logs,
            "failed_pct": self.failed_pct,
            "logs_with_errors": self.logs_with_errors,
            "issues_pct": self.issues_pct,
            "unique_parse": self.unique_parse,
            "passed": self.passed,
        }


def _strip_log_prefix(line: str) -> str:
    if line.startswith("[E ") and "] " in line:
        return line[line.index("] ") + 2 :]
    return line


def analyze_log(log_path: Path) -> tuple[str | None, list[Issue], list[Issue], bool]:
    """Parse one extractor log.

    Returns ``(source_file, parse_errors, internal_warnings, failed)``.
    """
    source_file: str | None = None
    errors: list[Issue] = []
    internal_seen: set[str] = set()
    internal: list[Issue] = []
    failed = False
    succeeded = False
    pending: Issue | None = None
    pending_body: list[str] = []

    try:
        text = log_path.read_text(encoding="utf-8", errors="replace")
    except OSError as exc:
        logging.warning("Cannot read %s: %s", log_path, exc)
        return None, [], [], False

    def flush() -> None:
        if pending is not None:
            file_, line_no, severity, msg = pending
            body = "\n".join(pending_body).rstrip()
            errors.append((file_, line_no, severity, f"{msg}\n{body}" if body else msg))

    for raw in text.splitlines():
        if not raw.startswith("[E "):
            if pending is not None:
                pending_body.append(raw)
            continue
        if pending is not None:
            flush()
            pending = None
            pending_body = []
        line = _strip_log_prefix(raw)
        if "Processed command line:" in raw:
            m = _RE_PROCESSED_SOURCE.search(raw)
            if m:
                source_file = m.group(1)
        elif source_file is None and "Command:" in raw:
            m = _RE_CMD_SOURCE.search(raw)
            if m:
                source_file = m.group(1)

        m = _RE_PARSE_ERROR.match(line)
        if m:
            pending = (m.group(1), int(m.group(2)), m.group(3), m.group(4).rstrip())
            pending_body = []
        else:
            m2 = _RE_INTERNAL_WARN.match(line)
            if m2:
                key = f"In {m2.group(1)}: {m2.group(2).rstrip()}"
                if key not in internal_seen:
                    internal_seen.add(key)
                    internal.append((_INTERNAL_FILE, 0, "warning", key))

        if _FAILURE in line:
            failed = True
        elif _SUCCESS in line:
            succeeded = True

    flush()
    # Parse errors of an extraction that exited successfully are spurious
    # (the extractor recovered).
    if succeeded and not failed:
        errors = []
    return source_file, errors, internal, failed


def extractor_log_dir(db_path: Path) -> Path:
    """Locate the extractor log directory of *db_path* or raise ``ScaError``."""
    for candidate in (
        db_path / "cpp" / "log" / "extractor",
        db_path / "log" / "extractor",
    ):
        if candidate.is_dir():
            return candidate
    raise ScaError(
        f"Extractor log directory not found under {db_path} "
        "(tried cpp/log/extractor and log/extractor)"
    )


def analyze_database(
    database: str | Path,
    *,
    show_all_warnings: bool = False,
    max_failed_command_pct: float | None = None,
    max_commands_with_issues_pct: float | None = None,
    max_unique_parse_errors: int | None = None,
) -> DbReport:
    """Analyse all extractor logs and evaluate the thresholds."""
    db_path = Path(database).resolve()
    if not db_path.is_dir():
        raise ScaError(f"Not a directory: {db_path}")
    log_files = sorted(extractor_log_dir(db_path).rglob("*.log"))
    if not log_files:
        raise ScaError(f"No *.log files found under {db_path}")

    report = DbReport(total_logs=len(log_files), show_all_warnings=show_all_warnings)
    sources: dict[Issue, set[str]] = defaultdict(set)
    for log_path in log_files:
        src, errors, internal, failed = analyze_log(log_path)
        label = src or f"<unknown, log={log_path.name}>"
        if failed:
            report.failed_logs += 1
        if errors:
            report.logs_with_errors += 1
            for issue in errors:
                sources[issue].add(label)
        if show_all_warnings:
            if internal:
                report.logs_with_internal += 1
            for issue in internal:
                sources[issue].add(label)

    report.issues = sorted(sources.items(), key=lambda kv: kv[0])
    report.unique_parse = sum(
        1 for (file_, *_), _s in report.issues if file_ != _INTERNAL_FILE
    )
    report.unique_internal = len(report.issues) - report.unique_parse

    if (
        max_failed_command_pct is not None
        and report.failed_pct > max_failed_command_pct
    ):
        report.failures.append(
            f"Commands failed {report.failed_pct:.1f}% exceeds limit "
            f"{max_failed_command_pct:.1f}%"
        )
    if (
        max_commands_with_issues_pct is not None
        and report.issues_pct > max_commands_with_issues_pct
    ):
        report.failures.append(
            f"Commands with issues {report.issues_pct:.1f}% exceeds limit "
            f"{max_commands_with_issues_pct:.1f}%"
        )
    limit = 0 if max_unique_parse_errors is None else max_unique_parse_errors
    if report.unique_parse > limit:
        report.failures.append(
            f"Unique parse errors {report.unique_parse} exceeds limit {limit}"
        )
    return report


def format_report(report: DbReport, color: bool = False) -> str:
    """Render *report* as text (GCC-style issue list plus summary)."""
    red, yellow, green, cyan, bold, reset = (
        ("\033[91m", "\033[93m", "\033[92m", "\033[96m", "\033[1m", "\033[0m")
        if color
        else ("",) * 6
    )
    sep = "=" * 70
    out: list[str] = [
        "",
        sep,
        f"UNIQUE PARSE / SYNTAX ERRORS ({len(report.issues)})",
        sep,
    ]
    if report.issues:
        out.append("")
    for (err_file, err_line, severity, message), src_files in report.issues:
        loc = f"{err_file}:{err_line}" if err_line else err_file
        if severity in ("error", "catastrophic error"):
            sev = f"{red}{bold}{severity}{reset}"
        elif severity == "warning":
            sev = f"{yellow}warning{reset}"
        else:
            sev = f"{cyan}note{reset}"
        lines = message.splitlines() or [""]
        out.append(f"{bold}{loc}{reset}: {sev}: {lines[0]}")
        out.extend(f"  {body}" for body in lines[1:])
        out.extend(f"  {cyan}-> compiled from:{reset} {sf}" for sf in sorted(src_files))
    if report.issues:
        out.append("")

    status = (
        f"{red}{bold}FAIL{reset}" if report.failures else f"{green}{bold}PASS{reset}"
    )
    out += ["", sep, "EXTRACTION SUMMARY", sep]
    out.append(f"  Command logs analyzed : {report.total_logs}")
    out.append(
        f"  Commands failed       : {report.failed_logs}  ({report.failed_pct:.1f}%)"
    )
    out.append(
        f"  Commands with issues  : {report.logs_with_errors}  ({report.issues_pct:.1f}%)"
    )
    if report.show_all_warnings:
        pct = (
            report.logs_with_internal / report.total_logs * 100
            if report.total_logs
            else 0.0
        )
        out.append(
            f"  Cmds with int. warns  : {report.logs_with_internal}  ({pct:.1f}%)"
        )
        out.append(f"  Unique parse errors   : {report.unique_parse}")
        out.append(f"  Unique internal warns : {report.unique_internal}")
    else:
        out.append(f"  Unique parse errors   : {report.unique_parse}")
    out.append(f"  Status                : {status}")
    if report.failures:
        out.append("  Threshold failures    :")
        out.extend(f"    - {failure}" for failure in report.failures)
    out.append("")
    return "\n".join(out) + "\n"


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Analyze CodeQL extractor logs for parsing/syntax errors."
    )
    parser.add_argument("database", metavar="DATABASE_DIR", type=Path)
    parser.add_argument("-a", "--show-all-warnings", action="store_true")
    parser.add_argument(
        "--no-fail",
        action="store_true",
        help="Return 0 even when thresholds fail (an invalid database still fails)",
    )
    parser.add_argument("--max-failed-command-pct", type=float, default=None)
    parser.add_argument("--max-commands-with-issues-pct", type=float, default=None)
    parser.add_argument(
        "--max-unique-parse-errors",
        type=int,
        default=None,
        help="Defaults to 0 when omitted",
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    setup_logging()
    try:
        report = analyze_database(
            args.database,
            show_all_warnings=args.show_all_warnings,
            max_failed_command_pct=args.max_failed_command_pct,
            max_commands_with_issues_pct=args.max_commands_with_issues_pct,
            max_unique_parse_errors=args.max_unique_parse_errors,
        )
    except ScaError as exc:
        logging.error("%s", exc)
        return exc.exit_code
    print(format_report(report, sys.stdout.isatty()), end="")
    return 0 if report.passed or args.no_fail else 1


if __name__ == "__main__":
    raise SystemExit(main())
