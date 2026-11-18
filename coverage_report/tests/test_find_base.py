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

import urllib.error
from pathlib import Path
from typing import Any

from coverage_report.src.find_base import main

REPO = "org/repo"
COMPARE = f"repos/{REPO}/compare/main...head0000"
LIST = f"repos/{REPO}/actions/artifacts?name=coverage-report-base&per_page=100&page="


def artifact(
    run_id: int,
    sha: str,
    created: str,
    *,
    branch: str = "main",
    fork: bool = False,
    expired: bool = False,
) -> dict[str, Any]:
    """Return one entry of GitHub's list of artifacts."""
    return {
        "expired": expired,
        "created_at": created,
        "workflow_run": {
            "id": run_id,
            "head_sha": sha,
            "head_branch": branch,
            "repository_id": 1,
            "head_repository_id": 2 if fork else 1,
        },
    }


def run(tmp_path: Path, responses: dict[str, Any]) -> dict[str, str]:
    """Run main() against canned API responses; return what it wrote as outputs.

    A response that is an exception is raised instead, like a failing request.
    """

    def get(path: str) -> Any:
        response = responses[path]
        if isinstance(response, Exception):
            raise response
        return response

    output = tmp_path / "github_output"
    env = {
        "REPO": REPO,
        "BASE_BRANCH": "main",
        "ARTIFACT": "coverage-report-base",
        "HEAD_SHA": "head0000",
        "GITHUB_OUTPUT": str(output),
    }
    assert main(env, get) == 0
    return dict(line.split("=", 1) for line in output.read_text().splitlines())


def test_the_run_of_the_merge_base_wins_over_newer_ones(tmp_path: Path) -> None:
    outputs = run(
        tmp_path,
        {
            COMPARE: {"merge_base_commit": {"sha": "base0000"}},
            LIST + "1": {
                "artifacts": [
                    artifact(3, "newer000", "2026-11-18T10:00:00Z"),
                    artifact(2, "base0000", "2026-11-17T10:00:00Z"),
                    artifact(1, "older000", "2026-11-16T10:00:00Z"),
                ]
            },
        },
    )
    assert outputs == {"merge_base": "base0000", "run_id": "2"}


def test_without_a_run_of_the_merge_base_the_newest_run_is_taken(
    tmp_path: Path,
) -> None:
    outputs = run(
        tmp_path,
        {
            COMPARE: {"merge_base_commit": {"sha": "base0000"}},
            LIST + "1": {
                "artifacts": [
                    artifact(1, "older000", "2026-11-16T10:00:00Z"),
                    artifact(3, "newer000", "2026-11-18T10:00:00Z"),
                ]
            },
        },
    )
    assert outputs == {"merge_base": "base0000", "run_id": "3"}


def test_only_unexpired_runs_of_the_repositorys_own_base_branch_count(
    tmp_path: Path,
) -> None:
    outputs = run(
        tmp_path,
        {
            COMPARE: {"merge_base_commit": {"sha": "base0000"}},
            LIST + "1": {
                "artifacts": [
                    # A fork's branch named main, uploading under the same name.
                    artifact(4, "base0000", "2026-11-18T13:00:00Z", fork=True),
                    artifact(3, "base0000", "2026-11-18T12:00:00Z", branch="feature"),
                    artifact(2, "base0000", "2026-11-18T11:00:00Z", expired=True),
                    artifact(1, "older000", "2026-11-16T10:00:00Z"),
                ]
            },
        },
    )
    assert outputs == {"merge_base": "base0000", "run_id": "1"}


def test_later_pages_are_searched_for_the_merge_base(tmp_path: Path) -> None:
    full_page = [
        artifact(100 + n, f"new{n:05}", f"2026-11-18T10:{n % 60:02}:00Z")
        for n in range(100)
    ]
    outputs = run(
        tmp_path,
        {
            COMPARE: {"merge_base_commit": {"sha": "base0000"}},
            LIST + "1": {"artifacts": full_page},
            LIST + "2": {
                "artifacts": [artifact(7, "base0000", "2026-10-01T10:00:00Z")]
            },
        },
    )
    assert outputs == {"merge_base": "base0000", "run_id": "7"}


def test_an_unknown_merge_base_falls_back_to_the_newest_run(tmp_path: Path) -> None:
    outputs = run(
        tmp_path,
        {
            COMPARE: urllib.error.URLError("no network"),
            LIST + "1": {
                "artifacts": [artifact(1, "older000", "2026-11-16T10:00:00Z")]
            },
        },
    )
    assert outputs == {"merge_base": "", "run_id": "1"}


def test_a_failing_artifact_list_means_no_comparison(tmp_path: Path) -> None:
    outputs = run(
        tmp_path,
        {
            COMPARE: {"merge_base_commit": {"sha": "base0000"}},
            LIST + "1": urllib.error.HTTPError(LIST, 403, "Forbidden", None, None),  # type: ignore[arg-type]
        },
    )
    assert outputs == {"merge_base": "base0000"}
