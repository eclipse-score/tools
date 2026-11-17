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

"""Filter ``compile_commands.json`` down to the entries relevant for the analysis.

Entries whose source file (or object/dependency output path) matches an exclusion
glob are dropped. Globs are matched against ``/<workspace-relative path>`` so
``**/dir/**`` captures both root-level and nested directories.
"""

from __future__ import annotations

import argparse
import fnmatch
import json
import logging
import re
import shlex
from collections.abc import Iterable, Sequence
from pathlib import Path

from score_codeql.errors import ScaError
from score_codeql.logging_utils import setup_logging

EXCLUDE_GLOBS = [
    "**/external/**",
    "**/third_party/**",
    "**/test/**",
    "**/tests/**",
    "**/*_test.c*",
    "**/mock/**",
    "**/mocks/**",
]

_BAZEL_OUT_FILE_RE = re.compile(r"^bazel-out/[^/]+/(?:bin|genfiles|testlogs)/(.+)$")
_OUTPUT_PATH_FLAG_ARGS = {
    "-o",
    "-MF",
    "-MJ",
    "--serialize-diagnostics",
    "-dependency-file",
}


def normalize_file_path(path: str) -> str:
    """Strip the ``bazel-out/<cfg>/bin/`` prefix so generated files match package globs."""
    match = _BAZEL_OUT_FILE_RE.match(path)
    return match.group(1) if match else path


def entry_arguments(entry: dict) -> list[str]:
    """The argv of a compile-commands entry (``arguments`` or split ``command``)."""
    args = entry.get("arguments")
    if isinstance(args, list):
        return [a for a in args if isinstance(a, str)]
    command = entry.get("command")
    if isinstance(command, str):
        try:
            return shlex.split(command)
        except ValueError:
            return []
    return []


def entry_path_candidates(entry: dict) -> set[str]:
    """Paths revealing the package an entry belongs to.

    Besides ``file`` this includes object/dependency output paths, which carry the
    owning target's package (``bazel-out/<cfg>/bin/<pkg>/_objs/...``); a test target
    may compile a main-library source directly.
    """
    paths: set[str] = set()
    file_field = entry.get("file", "")
    if isinstance(file_field, str) and file_field:
        paths.add(file_field)
    args = entry_arguments(entry)
    for i, arg in enumerate(args):
        if arg.startswith("-frandom-seed="):
            paths.add(arg.split("=", 1)[1])
        elif arg in _OUTPUT_PATH_FLAG_ARGS and i + 1 < len(args):
            next_arg = args[i + 1]
            if not next_arg.startswith("-"):
                paths.add(next_arg)
    return paths


def matches_any(paths: Iterable[str], globs: Sequence[str]) -> bool:
    """True when any of *paths* (raw or bazel-out-stripped) matches any glob."""
    candidates: set[str] = set()
    for path in paths:
        candidates.add(path)
        candidates.add(normalize_file_path(path))
    return any(
        fnmatch.fnmatch("/" + candidate, pattern)
        for candidate in candidates
        for pattern in globs
    )


def load_entries(path: Path) -> list[dict]:
    """Load a compilation database; raises ``ScaError`` if unusable."""
    try:
        with path.open("r", encoding="utf-8") as fh:
            entries = json.load(fh)
    except FileNotFoundError as exc:
        raise ScaError(f"input file not found: {path}") from exc
    except (OSError, UnicodeDecodeError) as exc:
        raise ScaError(f"cannot read {path}: {exc}") from exc
    except json.JSONDecodeError as exc:
        raise ScaError(f"invalid JSON in {path}: {exc}") from exc
    if not isinstance(entries, list):
        raise ScaError(f"expected a JSON array in {path}, got {type(entries).__name__}")
    for index, entry in enumerate(entries):
        if not isinstance(entry, dict):
            raise ScaError(
                f"entry at index {index} in {path} is not a JSON object "
                f"(got {type(entry).__name__})"
            )
    return entries


def filter_commands(
    commands_path: Path,
    output: Path | None = None,
    extra_exclude_globs: Sequence[str] = (),
) -> tuple[int, int]:
    """Filter *commands_path*, write the result and return ``(kept, removed)``."""
    entries = load_entries(commands_path)
    output_path = output or commands_path.parent / "compile_commands.filtered.json"
    globs = [*EXCLUDE_GLOBS, *extra_exclude_globs]

    kept = []
    removed = []
    for entry in entries:
        if matches_any(entry_path_candidates(entry), globs):
            removed.append(entry.get("file", ""))
        else:
            kept.append(entry)

    for name in sorted(removed):
        logging.debug("Removed: %s", name)
    logging.info(
        "Filter summary: %d kept, %d removed (total %d)",
        len(kept),
        len(removed),
        len(entries),
    )
    try:
        output_path.parent.mkdir(parents=True, exist_ok=True)
        with output_path.open("w", encoding="utf-8") as fh:
            json.dump(kept, fh, indent=2)
    except OSError as exc:
        raise ScaError(f"cannot write {output_path}: {exc}") from exc
    return len(kept), len(removed)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Filter compile_commands.json for SCA analysis."
    )
    parser.add_argument("input", type=Path, help="Path to compile_commands.json")
    parser.add_argument(
        "-o",
        "--output",
        type=Path,
        help="Output file (default: compile_commands.filtered.json next to input)",
    )
    parser.add_argument(
        "--exclude-glob",
        action="append",
        default=[],
        help="Additional file glob to exclude (repeatable)",
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    setup_logging()
    try:
        filter_commands(args.input, args.output, args.exclude_glob)
    except ScaError as exc:
        logging.error("%s", exc)
        return exc.exit_code
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
