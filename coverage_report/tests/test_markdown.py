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

from coverage_report.src.lcov import FileCoverage
from coverage_report.src.markdown import COMMENT_MARKER, render
from coverage_report.src.summary import Summary, summarize


def run(**files: list[bool]) -> dict[str, FileCoverage]:
    """Return a suite: per file, whether each line (1, 2, ...) ran."""
    return {
        path.replace("_", "/") + ".py": FileCoverage(lines=dict(enumerate(hits, 1)))
        for path, hits in files.items()
    }


def base() -> Summary:
    return summarize(
        {"Unit": run(src_a=[True, False], src_b=[True, True]), "Docs": None},
        commit="1234567890",
    )


def test_without_base_there_are_no_change_columns() -> None:
    text = render(base(), None, title="Python coverage", notes=["Measured: src/"])
    assert text.startswith(f"{COMMENT_MARKER}\n### Python coverage\n")
    assert "| Unit | 75.0% (3/4) | – |" in text
    assert "| Docs | no data | no data |" in text
    assert "Measured: src/" in text
    assert "Δ" not in text
    assert "Files with changed coverage" not in text


def test_the_change_against_the_base_branch_is_shown() -> None:
    now = summarize(
        {
            "Unit": run(src_a=[True, True], src_b=[False, True], src_new=[False]),
            "Docs": None,
        },
        commit="abc",
    )
    text = render(now, base(), base_name="main")

    assert "| Unit | 60.0% (3/5) | -15.0% | – | – |" in text
    assert "| Docs | no data | – | no data | – |" in text
    assert "against main at `12345678`." in text
    # Biggest drop first, files the base does not have last.
    assert (
        "<details><summary>Files with changed coverage (3, biggest drop first)"
        "</summary>\n\n| File | Lines | Δ | Branches | Δ |\n|---|---:|---:|---:|---:|\n"
        "| `src/b.py` | 50.0% | -50.0% | – | – |\n"
        "| `src/a.py` | 100.0% | +50.0% | – | – |\n"
        "| `src/new.py` | 0.0% | new | – | new |\n"
    ) in text
    assert "- `src/new.py` (1 lines)" in text


def test_unchanged_coverage_says_so_and_leaves_change_cells_empty() -> None:
    text = render(base(), base())
    assert "No file changed its coverage." in text
    assert "| Unit | 75.0% (3/4) |  | – | – |" in text
