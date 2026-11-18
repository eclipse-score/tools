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

from coverage_report.src.cli import main
from coverage_report.src.summary import SCHEMA


@pytest.fixture
def workdir(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    monkeypatch.chdir(tmp_path)
    monkeypatch.delenv("BUILD_WORKING_DIRECTORY", raising=False)
    (tmp_path / "unit.lcov").write_text(
        "SF:a.py\nDA:1,1\nDA:2,0\nend_of_record\nSF:unused.py\nDA:1,0\nend_of_record\n"
    )
    (tmp_path / "docs").mkdir()
    (tmp_path / "docs" / "cached.lcov").write_text("SF:a.py\nDA:2,1\nend_of_record\n")
    return tmp_path


def test_suites_are_written_as_json_and_markdown(workdir: Path) -> None:
    exit_code = main(
        ["Unit=unit.lcov", "Docs=docs", "Other=missing"]
        + ["--commit", "abc", "--json", "out/c.json"]
        + ["--markdown", "out/c.md", "--title", "Python coverage"]
    )

    assert exit_code == 0
    summary = json.loads((workdir / "out" / "c.json").read_text())
    assert summary["schema"] == SCHEMA
    assert summary["commit"] == "abc"
    assert summary["suites"] == {
        "Unit": {"lines": [1, 3], "branches": [0, 0]},
        "Docs": {"lines": [1, 3], "branches": [0, 0]},
        "Other": None,
    }
    assert summary["combined"] == {"lines": [2, 3], "branches": [0, 0]}
    assert "### Python coverage" in (workdir / "out" / "c.md").read_text()


def test_a_pull_request_is_compared_with_the_base_json(workdir: Path) -> None:
    main(["Unit=unit.lcov", "--commit", "base0000", "--json", "main.json"])
    (workdir / "unit.lcov").write_text("SF:a.py\nDA:1,1\nDA:2,1\nend_of_record\n")

    main(
        ["Unit=unit.lcov", "--markdown", "pr.md", "--compare-with", "main.json"]
        + ["--base-name", "develop", "--note", "Measured: everything."]
    )

    text = (workdir / "pr.md").read_text()
    assert "| Unit | 100.0% (2/2) | +66.7% | – | – |" in text
    assert "against develop at `base0000`." in text
    assert "Measured: everything." in text


def test_base_numbers_of_another_commit_get_a_note(workdir: Path) -> None:
    main(["Unit=unit.lcov", "--commit", "newer000", "--json", "main.json"])
    compare = ["Unit=unit.lcov", "--markdown", "pr.md", "--compare-with", "main.json"]

    main([*compare, "--base-commit", "base0000"])
    assert (
        "No coverage of main at `base0000`, where this branch started; Δ also "
        "contains what changed on main up to `newer000`."
    ) in (workdir / "pr.md").read_text()

    main([*compare, "--base-commit", "newer000"])
    assert "where this branch started" not in (workdir / "pr.md").read_text()


def test_a_missing_base_json_is_a_note_not_an_error(workdir: Path) -> None:
    exit_code = main(["unit.lcov", "--markdown", "pr.md", "--compare-with", "nope"])
    assert exit_code == 0
    text = (workdir / "pr.md").read_text()
    assert "| unit.lcov | 33.3% (1/3) | – |" in text
    assert "No coverage of the base branch available to compare with." in text


@pytest.mark.parametrize(
    "args",
    [
        ["unit.lcov", "--package-depth", "0"],
        ["unit.lcov", "--package-depth", "-1"],
        ["unit.lcov", "--json", "c.json", "--compare-with", "main.json"],
    ],
)
def test_options_that_would_be_ignored_are_errors(
    workdir: Path, args: list[str]
) -> None:
    with pytest.raises(SystemExit) as exc:
        main(args)
    assert exc.value.code == 2
