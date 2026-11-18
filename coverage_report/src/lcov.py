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
"""Read LCOV reports and merge them: executed anywhere wins.

LCOV has one record per source file; only these lines are read::

    SF:src/app.py              the record of src/app.py starts
    DA:3,1                     line 3 ran once            (DA:<line>,<hits>)
    BRDA:2,0,jump to line 4,-  a branch of line 2 did not run
                               (BRDA:<line>,<block>,<branch>,<taken>;
                               "-" and "0" both mean not taken)
    end_of_record              the record ends

Totals (LF, LH, BRF, BRH) and functions (FN*) are ignored: they are derived
from the lines above, and a merge has to recompute them anyway.
"""

import glob
from dataclasses import dataclass, field
from pathlib import Path

# File name patterns of LCOV reports inside a directory given as input.
LCOV_PATTERNS = ("*.lcov", "*.info")


@dataclass(frozen=True)
class Branch:
    """One branch of a source file.

    ``BRDA:2,0,jump to line 3,1`` is ``Branch(line=2, block="0", name="jump to line 3")``.
    The block is text, not a number: LCOV 2 names exception branches "e0".
    """

    line: int
    block: str
    name: str


@dataclass
class FileCoverage:
    """Whether each line and branch of one source file ran."""

    lines: dict[int, bool] = field(default_factory=dict[int, bool])
    branches: dict[Branch, bool] = field(default_factory=dict[Branch, bool])

    def mark_line(self, line: int, ran: bool) -> None:
        """Record that ``line`` ran or not; once run, it stays run."""
        self.lines[line] = self.lines.get(line, False) or ran

    def mark_branch(self, branch: Branch, ran: bool) -> None:
        """Record that ``branch`` was taken or not; once taken, it stays taken."""
        self.branches[branch] = self.branches.get(branch, False) or ran

    def merge(self, other: "FileCoverage") -> None:
        """Add what ``other`` ran to this file."""
        for line, ran in other.lines.items():
            self.mark_line(line, ran)
        for branch, ran in other.branches.items():
            self.mark_branch(branch, ran)


def parse_lcov(
    text: str, into: dict[str, FileCoverage] | None = None
) -> dict[str, FileCoverage]:
    """Merge all records of ``text`` (into ``into``, if given) and return them.

    A line or branch counts as run if any record says so, so a file that
    appears in several reports is not counted more than once.

    Example, two records of src/app.py, one after the other::

        SF:src/app.py    SF:src/app.py
        DA:3,1           DA:3,0
        DA:4,0           DA:4,1
        end_of_record    end_of_record

    -> ``{"src/app.py": FileCoverage(lines={3: True, 4: True})}``
    """
    files = {} if into is None else into
    current: FileCoverage | None = None
    for raw in text.splitlines():
        line = raw.strip()
        if line.startswith("SF:"):
            current = files.setdefault(line[3:], FileCoverage())
        elif current is None:
            continue
        elif line.startswith("DA:"):
            lineno, hits = line[3:].split(",")[:2]
            current.mark_line(int(lineno), _ran(hits))
        elif line.startswith("BRDA:"):
            # The branch name is free text and may contain commas: split
            # from both ends.
            lineno, block, rest = line[5:].split(",", 2)
            name, taken = rest.rsplit(",", 1)
            current.mark_branch(Branch(int(lineno), block, name), _ran(taken))
        elif line == "end_of_record":
            current = None
    return files


def _ran(count: str) -> bool:
    """Return whether an LCOV hit count means "executed": "-" and "0" do not."""
    return count not in ("-", "0")


def find_reports(path: str) -> list[Path]:
    """Return the LCOV files ``path`` names: a file, a directory or a glob.

    A directory is searched recursively for LCOV_PATTERNS, so downloaded
    artifact directories can be passed as is, also through a glob.

    Example::

        "coverage-unit.lcov"   -> [coverage-unit.lcov]           if it exists
        "artifacts/"           -> [artifacts/a/x.lcov, ...]
        "out/docs-*.lcov"      -> [out/docs-cached.lcov, out/docs-runtime.lcov]
        "lcov/docs-*"          -> the reports in lcov/docs-cached/, lcov/docs-runtime/
        "missing.lcov"         -> []
    """
    found: set[Path] = set()
    for match in map(Path, glob.glob(path, recursive=True)):
        if match.is_dir():
            found.update(p for pattern in LCOV_PATTERNS for p in match.rglob(pattern))
        else:
            found.add(match)
    return sorted(p for p in found if p.is_file())


def load_suite(path: str) -> dict[str, FileCoverage] | None:
    """Return the merged coverage of the LCOV files ``path`` names.

    None if there are none or they name no source file: a suite whose test
    job failed early must show as "no data", not as 0%.
    """
    files: dict[str, FileCoverage] = {}
    for report in find_reports(path):
        parse_lcov(report.read_text(encoding="utf-8", errors="replace"), into=files)
    return files or None
