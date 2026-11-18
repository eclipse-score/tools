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
"""Render a Summary as the Markdown posted on pull requests.

Example, compared with the base branch::

    <!-- score-coverage-report -->
    ### Coverage

    | Tests | Lines | Δ | Branches | Δ |
    |---|---:|---:|---:|---:|
    | Unit tests | 60.0% (3/5) | +20.0% | 50.0% (1/2) |  |
    | **Combined** | 80.0% (4/5) | +20.0% | 100.0% (2/2) |  |

    Δ is the change in percentage points against main at `9f8e7d6c`.

    <details><summary>Files with changed coverage (1, biggest drop first)</summary>
    ...
    <details><summary>Per package (lowest line coverage first)</summary>
    ...
    <details><summary>Files no test runs (1)</summary>
    ...
"""

from collections.abc import Mapping

from .summary import Summary, TotalCoverage, percent

# Lets the comment action find and update its earlier comment instead of
# adding a new one on every push. actions/comment/action.yml uses it too.
COMMENT_MARKER = "<!-- score-coverage-report -->"

# Changes below this many percentage points round to "+0.0%" and are shown as
# no change.
_PRECISION = 0.05

type Column = tuple[int, int] | None


def _columns(totals: TotalCoverage | None) -> list[Column]:
    """Return the (hit, total) of each table column: lines, then branches."""
    if totals is None:
        return [None, None]
    return [
        (totals.lines_hit, totals.lines_total),
        (totals.branches_hit, totals.branches_total),
    ]


def _percent(column: Column) -> float | None:
    return percent(*column) if column else None


def _change(now: float | None, before: float | None) -> str:
    """Return the Δ cell.

    Example::

        (80.0, 60.0)   -> "+20.0%"
        (60.0, 60.04)  -> ""        no change: the rows that did change stand out
        (None, 60.0)   -> "–"       one side has no number
    """
    if now is None or before is None:
        return "–"
    change = now - before
    if abs(change) < _PRECISION:
        return ""
    return f"{change:+.1f}%"


def _header(first: str, compare: bool) -> list[str]:
    if compare:
        return [f"| {first} | Lines | Δ | Branches | Δ |", "|---|---:|---:|---:|---:|"]
    return [f"| {first} | Lines | Branches |", "|---|---:|---:|"]


def _row(
    label: str,
    totals: TotalCoverage | None,
    reference: Mapping[str, TotalCoverage | None] | None,
    key: str,
    show_counts: bool = False,
) -> str:
    """Render one table row; ``reference`` is the matching section of the base.

    Example::

        "| Unit tests | 80.0% (4/5) | +20.0% | 50.0% (1/2) |  |"
        "| `src/new.py` | 0.0% | new | – | new |"         not on the base branch
        "| docs | no data | – | no data | – |"             suite without data
    """
    before_columns = _columns(reference.get(key)) if reference else [None, None]
    cells = [label]
    for now, before in zip(_columns(totals), before_columns, strict=True):
        value = _percent(now)
        if now is None:
            cells.append("no data")
        elif value is None:
            cells.append("–")
        elif show_counts:
            cells.append(f"{value:.1f}% ({now[0]}/{now[1]})")
        else:
            cells.append(f"{value:.1f}%")
        if reference is not None:
            cells.append(
                _change(value, _percent(before)) if key in reference else "new"
            )
    return "| " + " | ".join(cells) + " |"


def _changed(now: TotalCoverage, before: TotalCoverage | None) -> bool:
    """Return whether a file's percentages differ from the base branch's."""
    if before is None:
        return True
    for n, b in zip(_columns(now), _columns(before), strict=True):
        a, c = _percent(n), _percent(b)
        if (a is None) != (c is None):
            return True
        if a is not None and c is not None and abs(a - c) >= _PRECISION:
            return True
    return False


def _details(summary: str, body: list[str]) -> list[str]:
    """Return a collapsed section: the totals are the overview, the rest is detail."""
    return [f"<details><summary>{summary}</summary>", "", *body, "", "</details>", ""]


def _changed_files(summary: Summary, reference: Summary) -> list[str]:
    files, before = summary.files, reference.files
    changed = [p for p, c in files.items() if _changed(c, before.get(p))]
    if not changed:
        return ["No file changed its coverage.", ""]

    def biggest_drop_first(path: str) -> tuple[bool, float, str]:
        base = before.get(path)
        was = base.lines_percent if base else None
        now = files[path].lines_percent
        if was is None or now is None:
            return (True, 0.0, path)  # new files after the changed ones
        return (False, now - was, path)

    return _details(
        f"Files with changed coverage ({len(changed)}, biggest drop first)",
        [
            *_header("File", True),
            *(
                _row(f"`{path}`", files[path], before, path)
                for path in sorted(changed, key=biggest_drop_first)
            ),
        ],
    )


def render(
    summary: Summary,
    reference: Summary | None,
    *,
    title: str = "Coverage",
    notes: list[str] | None = None,
    base_name: str = "main",
) -> str:
    """Return the Markdown of ``summary``, with Δ columns if ``reference`` is set."""
    compare = reference is not None
    out = [COMMENT_MARKER, f"### {title}", "", *_header("Tests", compare)]
    for label, totals in summary.suites.items():
        out.append(
            _row(label, totals, reference.suites if reference else None, label, True)
        )
    out += [
        _row(
            "**Combined**",
            summary.combined,
            {"combined": reference.combined} if reference else None,
            "combined",
            True,
        ),
        "",
    ]
    if reference:
        out.append(
            f"Δ is the change in percentage points against {base_name} at "
            f"`{reference.commit[:8]}`."
        )
    out += [*(notes or []), ""]

    if reference:
        out += _changed_files(summary, reference)

    packages = sorted(
        summary.packages.items(),
        key=lambda item: (item[1].lines_percent or 0.0, item[0]),
    )
    out += _details(
        "Per package (lowest line coverage first)",
        [
            *_header("Package", compare),
            *(
                _row(
                    f"`{name}`", totals, reference.packages if reference else None, name
                )
                for name, totals in packages
            ),
        ],
    )

    untested = [
        (path, totals.lines_total)
        for path, totals in sorted(summary.files.items())
        if totals.lines_hit == 0
    ]
    if untested:
        out += _details(
            f"Files no test runs ({len(untested)})",
            [f"- `{path}` ({count} lines)" for path, count in untested],
        )
    return "\n".join(out)
