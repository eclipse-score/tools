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

from pathlib import Path

from coverage_report.src.lcov import (
    Branch,
    FileCoverage,
    find_reports,
    load_suite,
    parse_lcov,
)


def lcov(path: str, *body: str) -> str:
    """Return one LCOV record for ``path``."""
    return "\n".join([f"SF:{path}", *body, "end_of_record"]) + "\n"


def test_a_line_or_branch_counts_as_run_if_any_record_ran_it() -> None:
    merged = parse_lcov(
        lcov("a.py", "DA:1,0", "DA:2,3")
        + lcov("a.py", "BRDA:2,0,jump to line 3,-", "BRDA:2,0,jump to line 4,0")
        # The second report lists the same two branches in the other order.
        + lcov("a.py", "DA:1,1", "DA:2,0")
        + lcov("a.py", "BRDA:2,0,jump to line 4,0", "BRDA:2,0,jump to line 3,2")
    )
    assert merged == {
        "a.py": FileCoverage(
            lines={1: True, 2: True},
            branches={
                Branch(2, "0", "jump to line 3"): True,
                Branch(2, "0", "jump to line 4"): False,
            },
        )
    }


def test_branch_names_may_contain_commas() -> None:
    merged = parse_lcov(lcov("a.py", "BRDA:5,1,exit from f(a, b),1"))
    assert merged["a.py"].branches == {Branch(5, "1", "exit from f(a, b)"): True}


def test_exception_branches_of_lcov_2_are_read() -> None:
    merged = parse_lcov(lcov("a.cpp", "BRDA:7,e0,1,1", "BRDA:7,e0,2,-"))
    assert merged["a.cpp"].branches == {
        Branch(7, "e0", "1"): True,
        Branch(7, "e0", "2"): False,
    }


def test_lines_outside_a_record_and_totals_are_ignored() -> None:
    text = "TN:\nDA:1,1\n" + lcov("a.py", "DA:1,1", "LF:1", "LH:1", "FN:1,f")
    assert parse_lcov(text) == {"a.py": FileCoverage(lines={1: True})}


def test_reports_are_found_by_file_directory_and_glob(tmp_path: Path) -> None:
    (tmp_path / "artifact" / "nested").mkdir(parents=True)
    unit = tmp_path / "unit.lcov"
    nested = tmp_path / "artifact" / "nested" / "docs-cached.lcov"
    info = tmp_path / "artifact" / "docs-runtime.info"
    for report in (unit, nested, info):
        report.write_text(lcov("a.py", "DA:1,1"))
    (tmp_path / "artifact" / "readme.txt").write_text("not a report")

    assert find_reports(str(unit)) == [unit]
    assert find_reports(str(tmp_path / "artifact")) == [info, nested]
    assert find_reports(str(tmp_path / "artifact" / "**" / "docs-*")) == [info, nested]
    assert find_reports(str(tmp_path / "missing.lcov")) == []


def test_a_glob_may_match_directories(tmp_path: Path) -> None:
    # What actions/download-artifact writes for a `pattern`: one directory
    # per artifact.
    reports = []
    for suite in ("cached", "runtime"):
        (tmp_path / f"lcov-docs-{suite}").mkdir()
        reports.append(tmp_path / f"lcov-docs-{suite}" / f"docs-{suite}.lcov")
        reports[-1].write_text(lcov("a.py", "DA:1,1"))
    (tmp_path / "lcov-unit").mkdir()
    (tmp_path / "lcov-unit" / "unit.lcov").write_text(lcov("b.py", "DA:1,1"))

    assert find_reports(str(tmp_path / "lcov-docs-*")) == reports


def test_suites_without_usable_data_are_none(tmp_path: Path) -> None:
    (tmp_path / "empty").mkdir()
    (tmp_path / "no_records.lcov").write_text("TN:\n")
    assert load_suite(str(tmp_path / "missing")) is None
    assert load_suite(str(tmp_path / "empty")) is None
    assert load_suite(str(tmp_path / "no_records.lcov")) is None


def test_all_reports_of_a_suite_are_merged(tmp_path: Path) -> None:
    (tmp_path / "cached.lcov").write_text(lcov("a.py", "DA:1,1", "DA:2,0"))
    (tmp_path / "runtime.lcov").write_text(lcov("a.py", "DA:1,0", "DA:2,1"))
    assert load_suite(str(tmp_path)) == {"a.py": FileCoverage(lines={1: True, 2: True})}
