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
"""Command line: merge LCOV suites, write JSON and Markdown, compare with a base.

Example::

    score-coverage-report "Unit tests=coverage-unit.lcov" "Scenarios=out/*.lcov" \\
        --json coverage.json --markdown summary.md --compare-with main/coverage.json
"""

import argparse
import json
import os
import subprocess
from collections.abc import Sequence
from pathlib import Path

from .lcov import find_reports, load_suite
from .markdown import render
from .summary import load_reference, summarize


def _head_commit() -> str:
    """Return the checked-out commit, or "" outside a Git repository."""
    try:
        return subprocess.run(
            ["git", "rev-parse", "HEAD"], capture_output=True, text=True, check=False
        ).stdout.strip()
    except OSError:
        return ""


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="score-coverage-report",
        description="Merge LCOV reports of several test suites and show how their "
        "coverage changed against a base branch.",
    )
    parser.add_argument(
        "suites",
        nargs="+",
        metavar="[LABEL=]PATH",
        help="LCOV reports of one test suite: a file, a directory (searched for "
        "*.lcov, *.info) or a glob. A PATH without reports is shown as 'no data'.",
    )
    parser.add_argument("--json", help="write the numbers to this file")
    parser.add_argument("--markdown", help="write the Markdown summary to this file")
    parser.add_argument("--lcov", help="write all reports, concatenated, to this file")
    parser.add_argument(
        "--compare-with",
        metavar="JSON",
        help="numbers of the base branch, written by --json; adds Δ columns. "
        "A missing or unreadable file is noted in the summary, not an error.",
    )
    parser.add_argument("--title", default="Coverage", help="heading of the summary")
    parser.add_argument(
        "--note",
        action="append",
        default=[],
        help="line of text below the totals; can be repeated",
    )
    parser.add_argument(
        "--base-name", default="main", help="name of the base branch in the summary"
    )
    parser.add_argument(
        "--package-depth",
        type=int,
        help="group files by at most this many leading directories "
        "(default: by their full directory)",
    )
    parser.add_argument(
        "--commit", help="commit the reports belong to (default: git rev-parse HEAD)"
    )
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = _parser().parse_args(argv)

    # `bazel run` starts in the runfiles tree; paths on the command line are
    # meant relative to where the user called it.
    os.chdir(os.environ.get("BUILD_WORKING_DIRECTORY", "."))

    paths = dict(_label_and_path(arg) for arg in args.suites)
    suites = {label: load_suite(path) for label, path in paths.items()}
    for label, files in suites.items():
        if files is None:
            print(
                f"warning: no LCOV data in {paths[label]}; reporting '{label}' as no data"
            )

    if args.lcov:
        # genhtml and similar tools merge the records of a file themselves.
        reports = [p for path in paths.values() for p in find_reports(path)]
        _write(args.lcov, "".join(p.read_text(encoding="utf-8") for p in reports))

    summary = summarize(
        suites, args.commit or _head_commit(), package_depth=args.package_depth
    )
    if args.json:
        _write(args.json, json.dumps(summary.to_json(), indent=2, sort_keys=True))
    if args.markdown:
        reference, note = None, None
        if args.compare_with:
            reference, note = load_reference(Path(args.compare_with))
        notes = [*args.note, *([note] if note else [])]
        text = render(
            summary, reference, title=args.title, notes=notes, base_name=args.base_name
        )
        _write(args.markdown, text)
    return 0


def _label_and_path(arg: str) -> tuple[str, str]:
    """Split ``LABEL=PATH``; without a label, the path is the label too."""
    label, sep, path = arg.partition("=")
    return (label, path) if sep else (arg, arg)


def _write(path: str, text: str) -> None:
    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(text, encoding="utf-8")
    print(f"Wrote {target.absolute()}")
