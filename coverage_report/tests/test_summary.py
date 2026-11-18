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

import json
from pathlib import Path

import pytest

from coverage_report.src.lcov import Branch, FileCoverage
from coverage_report.src.summary import (
    Summary,
    TotalCoverage,
    load_reference,
    package_of,
    run_by,
    summarize,
)


@pytest.mark.parametrize(
    ("path", "depth", "package"),
    [
        ("src/ext/metamodel/checks/graph.py", None, "src/ext/metamodel/checks"),
        ("src/ext/metamodel/checks/graph.py", 3, "src/ext/metamodel"),
        ("src/helper/__init__.py", 3, "src/helper"),
        ("setup.py", None, "."),
        ("setup.py", 2, "."),
    ],
)
def test_files_are_grouped_by_their_directory_cut_to_the_depth(
    path: str, depth: int | None, package: str
) -> None:
    assert package_of(path, depth) == package


def test_every_suite_counts_every_file_of_the_combined_report() -> None:
    combined = {
        "a.py": FileCoverage(lines={1: True, 2: True}),
        "unused.py": FileCoverage(lines={1: False}),
    }
    suite = {"a.py": FileCoverage(lines={1: True})}
    assert run_by(suite, combined) == {
        "a.py": FileCoverage(lines={1: True, 2: False}),
        "unused.py": FileCoverage(lines={1: False}),
    }


def test_suites_are_summarized_apart_and_combined() -> None:
    branch = Branch(1, "0", "jump to line 2")
    unit = {
        "src/a.py": FileCoverage(lines={1: True, 2: False}, branches={branch: True}),
        # Listed by the unit test run though no test imports it.
        "src/unused.py": FileCoverage(lines={1: False}),
        "src/__init__.py": FileCoverage(),
    }
    docs = {"src/a.py": FileCoverage(lines={1: True, 2: True})}

    summary = summarize({"Unit": unit, "Docs": docs, "Other": None}, commit="abc")

    # Every suite counts the same 3 lines and 1 branch.
    assert summary.suites == {
        "Unit": TotalCoverage(1, 3, 1, 1),
        "Docs": TotalCoverage(2, 3, 0, 1),
        "Other": None,
    }
    assert summary.combined == TotalCoverage(2, 3, 1, 1)
    assert summary.packages == {"src": TotalCoverage(2, 3, 1, 1)}
    # Files without code get no row.
    assert summary.files == {
        "src/a.py": TotalCoverage(2, 2, 1, 1),
        "src/unused.py": TotalCoverage(0, 1, 0, 0),
    }


def test_a_summary_reads_back_what_it_wrote(tmp_path: Path) -> None:
    summary = summarize({"Unit": {"a.py": FileCoverage(lines={1: True})}}, "abc")
    path = tmp_path / "main.json"
    path.write_text(json.dumps(summary.to_json()))
    assert load_reference(path) == (summary, None)


@pytest.mark.parametrize(
    ("content", "note"),
    [
        (None, "No coverage of the base branch available"),
        ('{"schema": 1, "comm', "could not be read"),
        ('{"schema": 1, "commit": "abc"}', "could not be read"),
        ('{"commit": "abc"}', "another format"),
        ("[1]", "another format"),
    ],
)
def test_unusable_base_numbers_give_a_note_instead_of_a_comparison(
    tmp_path: Path, content: str | None, note: str
) -> None:
    path = tmp_path / "main.json"
    if content is not None:
        path.write_text(content)
    reference, message = load_reference(path)
    assert reference is None
    assert message is not None and note in message


@pytest.mark.parametrize(
    "lines",
    [[1], [1, "2"], [3, 2], [-1, 2], [True, 2]],
)
def test_totals_must_be_pairs_of_hit_and_total(lines: list[object]) -> None:
    with pytest.raises(ValueError):
        TotalCoverage.from_json({"lines": lines, "branches": [0, 0]})


def test_a_summary_without_a_commit_string_is_rejected() -> None:
    data = summarize({}, "abc").to_json() | {"commit": 1}
    with pytest.raises(ValueError):
        Summary.from_json(data)
