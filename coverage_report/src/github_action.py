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
"""Run the CLI for actions/report/action.yml, from its inputs in the environment.

The action passes its inputs as environment variables; this turns them into
CLI arguments, so the action's shell step stays a single command.

Example environment::

    SUITES="Unit tests=lcov/unit\\ndocs=lcov/docs-*"   one LABEL=PATH per line
    NOTES="Covers src/."                               one note per line
    TITLE="Python coverage"  PACKAGE_DEPTH="3"  BASE_BRANCH="main"
    COMMIT="1a2b3c"  IS_PR="true"  BASE_JSON="/tmp/base/coverage.json"
    MERGE_BASE="9f8e7d"                                from find_base.py, may be empty
    OUT="/tmp/coverage-report"  PR_NUMBER="42"       PR_NUMBER only on pull requests
    GITHUB_STEP_SUMMARY=...  GITHUB_OUTPUT=...         set by GitHub

-> score-coverage-report "Unit tests=lcov/unit" "docs=lcov/docs-*" \\
       --note "Covers src/." --package-depth 3 --compare-with /tmp/base/coverage.json \\
       --base-commit 9f8e7d \\
       --title "Python coverage" --base-name main --commit 1a2b3c \\
       --json /tmp/coverage-report/coverage.json --markdown .../summary.md

and writes PR_NUMBER to OUT/pr_number for ../../actions/comment.
"""

import os
from collections.abc import Mapping
from pathlib import Path

from . import cli


def _lines(text: str) -> list[str]:
    """Return the non-blank lines of a multiline action input, stripped."""
    return [line.strip() for line in text.splitlines() if line.strip()]


def cli_args(env: Mapping[str, str]) -> list[str]:
    """Return the CLI arguments for the action inputs in ``env``."""
    out = Path(env["OUT"])
    args = _lines(env["SUITES"])
    for note in _lines(env.get("NOTES", "")):
        args += ["--note", note]
    if env.get("PACKAGE_DEPTH", "").strip():
        args += ["--package-depth", env["PACKAGE_DEPTH"].strip()]
    if env.get("IS_PR") == "true":
        args += ["--compare-with", env["BASE_JSON"]]
        if env.get("MERGE_BASE"):
            args += ["--base-commit", env["MERGE_BASE"]]
    return args + [
        "--title",
        env["TITLE"],
        "--base-name",
        env["BASE_BRANCH"],
        "--commit",
        env["COMMIT"],
        "--json",
        str(out / "coverage.json"),
        "--markdown",
        str(out / "summary.md"),
    ]


def main(env: Mapping[str, str] = os.environ) -> int:
    """Write the report, add it to the job summary and set the `markdown` output."""
    exit_code = cli.main(cli_args(env))
    out = Path(env["OUT"])
    # The comment action cannot take the number from its workflow_run event:
    # that list of pull requests is empty for forks.
    if env.get("PR_NUMBER"):
        (out / "pr_number").write_text(env["PR_NUMBER"], encoding="utf-8")
    markdown = out / "summary.md"
    with open(env["GITHUB_STEP_SUMMARY"], "a", encoding="utf-8") as summary:
        summary.write(markdown.read_text(encoding="utf-8"))
    with open(env["GITHUB_OUTPUT"], "a", encoding="utf-8") as output:
        output.write(f"markdown={markdown}\n")
    return exit_code


if __name__ == "__main__":
    raise SystemExit(main())
