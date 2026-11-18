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

from coverage_report.src.github_action import cli_args, main


def action_env(tmp_path: Path, **overrides: str) -> dict[str, str]:
    """Return the environment actions/report/action.yml sets, for a base run."""
    return {
        "OUT": str(tmp_path / "out"),
        "SUITES": "Unit tests=unit.lcov\n\n  Docs=docs-*  \n",
        "NOTES": "",
        "TITLE": "Python coverage",
        "PACKAGE_DEPTH": "",
        "BASE_BRANCH": "main",
        "BASE_JSON": str(tmp_path / "base" / "coverage.json"),
        "COMMIT": "base0000",
        "IS_PR": "false",
        "GITHUB_STEP_SUMMARY": str(tmp_path / "step_summary"),
        "GITHUB_OUTPUT": str(tmp_path / "github_output"),
        **overrides,
    }


def test_inputs_become_cli_arguments(tmp_path: Path) -> None:
    env = action_env(
        tmp_path, NOTES="First.\nSecond.", PACKAGE_DEPTH=" 3 ", IS_PR="true"
    )
    out = tmp_path / "out"
    assert cli_args(env) == [
        "Unit tests=unit.lcov",
        "Docs=docs-*",
        "--note",
        "First.",
        "--note",
        "Second.",
        "--package-depth",
        "3",
        "--compare-with",
        env["BASE_JSON"],
        "--title",
        "Python coverage",
        "--base-name",
        "main",
        "--commit",
        "base0000",
        "--json",
        str(out / "coverage.json"),
        "--markdown",
        str(out / "summary.md"),
        "--lcov",
        str(out / "coverage.lcov"),
    ]


def test_only_pull_requests_compare_with_the_base(tmp_path: Path) -> None:
    assert "--compare-with" not in cli_args(action_env(tmp_path))


def test_a_base_run_then_a_pull_request_run(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.chdir(tmp_path)
    monkeypatch.delenv("BUILD_WORKING_DIRECTORY", raising=False)
    (tmp_path / "unit.lcov").write_text("SF:a.py\nDA:1,1\nDA:2,0\nend_of_record\n")

    assert main(action_env(tmp_path)) == 0
    # What the base run uploads, the pull request run downloads.
    (tmp_path / "base").mkdir()
    (tmp_path / "out" / "coverage.json").rename(tmp_path / "base" / "coverage.json")
    (tmp_path / "unit.lcov").write_text("SF:a.py\nDA:1,1\nDA:2,1\nend_of_record\n")
    (tmp_path / "step_summary").write_text("")

    assert main(action_env(tmp_path, IS_PR="true", COMMIT="pr000000")) == 0

    summary = (tmp_path / "out" / "summary.md").read_text()
    assert "| Unit tests | 100.0% (2/2) | +50.0% | – | – |" in summary
    assert "| Docs | no data | – | no data | – |" in summary
    assert "against main at `base0000`." in summary
    assert (tmp_path / "step_summary").read_text() == summary
    assert (
        (tmp_path / "github_output")
        .read_text()
        .endswith(f"markdown={tmp_path / 'out' / 'summary.md'}\n")
    )
    assert json.loads((tmp_path / "out" / "coverage.json").read_text())["commit"] == (
        "pr000000"
    )
