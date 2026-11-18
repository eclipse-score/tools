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
"""The numbers of a coverage summary, per suite, package and file, and their JSON.

A run on the base branch stores its Summary as JSON; a pull request run reads
it back to show what changed.
"""

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Self

from .lcov import FileCoverage

# Version of the JSON format. Raise it whenever that format changes, so a pull
# request does not compare against base numbers it cannot read.
SCHEMA = 1


def percent(hit: int, total: int) -> float | None:
    """Return ``hit`` of ``total`` in percent; None if there is nothing to run."""
    return 100 * hit / total if total else None


@dataclass(frozen=True)
class TotalCoverage:
    """How many lines and branches of a file, package or suite ran.

    In JSON: ``{"lines": [hit, total], "branches": [hit, total]}``.
    """

    lines_hit: int
    lines_total: int
    branches_hit: int
    branches_total: int

    @classmethod
    def of(cls, files: list[FileCoverage]) -> Self:
        lines = [ran for f in files for ran in f.lines.values()]
        branches = [ran for f in files for ran in f.branches.values()]
        return cls(sum(lines), len(lines), sum(branches), len(branches))

    @property
    def lines_percent(self) -> float | None:
        return percent(self.lines_hit, self.lines_total)

    @property
    def branches_percent(self) -> float | None:
        return percent(self.branches_hit, self.branches_total)

    def to_json(self) -> dict[str, list[int]]:
        return {
            "lines": [self.lines_hit, self.lines_total],
            "branches": [self.branches_hit, self.branches_total],
        }

    @classmethod
    def from_json(cls, data: Any) -> Self:
        """Return the TotalCoverage of to_json()'s output; ValueError otherwise."""
        values: list[int] = []
        for key in ("lines", "branches"):
            pair = data[key]
            if (
                not isinstance(pair, list)
                or len(pair) != 2
                or any(type(v) is not int or v < 0 for v in pair)
                or pair[0] > pair[1]
            ):
                raise ValueError(f"{key} must be [hit, total] with 0 <= hit <= total")
            values += pair
        return cls(*values)


@dataclass
class Summary:
    """The numbers of one run. A suite without data has None."""

    commit: str
    suites: dict[str, TotalCoverage | None]
    combined: TotalCoverage
    packages: dict[str, TotalCoverage]
    files: dict[str, TotalCoverage]

    def to_json(self) -> dict[str, Any]:
        return {
            "schema": SCHEMA,
            "commit": self.commit,
            "suites": {k: v.to_json() if v else None for k, v in self.suites.items()},
            "combined": self.combined.to_json(),
            "packages": {k: v.to_json() for k, v in self.packages.items()},
            "files": {k: v.to_json() for k, v in self.files.items()},
        }

    @classmethod
    def from_json(cls, data: Any) -> Self:
        """Return the Summary of to_json()'s output.

        Raises KeyError, TypeError or ValueError if ``data`` has another shape.
        """
        if not isinstance(data["commit"], str):
            raise ValueError("commit must be a string")
        return cls(
            commit=data["commit"],
            suites={
                k: TotalCoverage.from_json(v) if v else None
                for k, v in data["suites"].items()
            },
            combined=TotalCoverage.from_json(data["combined"]),
            packages={
                k: TotalCoverage.from_json(v) for k, v in data["packages"].items()
            },
            files={k: TotalCoverage.from_json(v) for k, v in data["files"].items()},
        )


def package_of(path: str, depth: int | None = None) -> str:
    """Return the package a file counts towards: its directory, cut to ``depth``.

    Example::

        ("src/ext/metamodel/checks/graph.py", None) -> "src/ext/metamodel/checks"
        ("src/ext/metamodel/checks/graph.py", 3)    -> "src/ext/metamodel"
        ("src/helper/__init__.py", 3)               -> "src/helper"
        ("setup.py", None)                          -> "."
    """
    parts = path.split("/")[:-1]
    if depth:
        parts = parts[:depth]
    return "/".join(parts) or "."


def run_by(
    suite: dict[str, FileCoverage], combined: dict[str, FileCoverage]
) -> dict[str, FileCoverage]:
    """Return what ``suite`` ran of every line and branch in ``combined``.

    Gives every suite the same denominator: a file only one suite lists
    (e.g. one no test imports) counts as not run for all others.
    """
    files: dict[str, FileCoverage] = {}
    for path, file in combined.items():
        ran = suite.get(path, FileCoverage())
        files[path] = FileCoverage(
            lines={n: ran.lines.get(n, False) for n in file.lines},
            branches={b: ran.branches.get(b, False) for b in file.branches},
        )
    return files


def combine(
    suites: dict[str, dict[str, FileCoverage] | None],
) -> dict[str, FileCoverage]:
    """Return the union of all suites: run in any suite counts as run."""
    combined: dict[str, FileCoverage] = {}
    for files in suites.values():
        for path, file in (files or {}).items():
            combined.setdefault(path, FileCoverage()).merge(file)
    return combined


def summarize(
    suites: dict[str, dict[str, FileCoverage] | None],
    commit: str,
    package_depth: int | None = None,
) -> Summary:
    """Return the numbers per suite, combined, per package and per file."""
    combined = combine(suites)
    # Files without code (an empty __init__.py) would only add empty rows.
    files = {path: file for path, file in sorted(combined.items()) if file.lines}
    packages: dict[str, list[FileCoverage]] = {}
    for path, file in files.items():
        packages.setdefault(package_of(path, package_depth), []).append(file)
    return Summary(
        commit=commit,
        suites={
            label: TotalCoverage.of(list(run_by(s, combined).values())) if s else None
            for label, s in suites.items()
        },
        combined=TotalCoverage.of(list(combined.values())),
        packages={name: TotalCoverage.of(fs) for name, fs in sorted(packages.items())},
        files={path: TotalCoverage.of([file]) for path, file in files.items()},
    )


def load_reference(path: Path) -> tuple[Summary | None, str | None]:
    """Return the base branch's Summary, or None and a note why there is none.

    A pull request summary without comparison is still useful; one that fails
    because of the base branch's file is not, so nothing here raises.
    """
    if not path.is_file():
        return None, "No coverage of the base branch available to compare with."
    unreadable = "The coverage of the base branch could not be read; no comparison."
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        print(f"warning: cannot read {path}: {exc}; not comparing")
        return None, unreadable
    if not isinstance(data, dict) or data.get("schema") != SCHEMA:
        print(f"warning: {path} is not in format {SCHEMA}; not comparing")
        return (
            None,
            "The coverage of the base branch has another format; no comparison.",
        )
    try:
        return Summary.from_json(data), None
    except (KeyError, TypeError, ValueError, AttributeError) as exc:
        print(f"warning: {path} is malformed: {exc!r}; not comparing")
        return None, unreadable
