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

from repo_policy_sync.src.models import Change, Policy
from repo_policy_sync.src.reporting import render_json, render_markdown, render_table
from repo_policy_sync.src.runner import RepositoryOutcome, RunReport, RunSummary


def _report() -> RunReport:
    return RunReport(
        summary=RunSummary(
            repositories=1,
            synchronized=1,
            sync_failures=0,
            skipped=0,
            evaluations=1,
            compliant=0,
            drifted=1,
            not_applicable=0,
            evaluation_failures=0,
            pull_requests_created=0,
            pull_requests_updated=0,
            pull_requests_open=0,
            pull_requests_recreated=0,
        ),
        outcomes=(
            RepositoryOutcome(
                repository="example",
                policy_id="example-policy",
                when="yes (live)",
                status="changes-required",
                changes=(
                    Change(
                        Path(".gitignore"), "add '_build'", "Avoid generated files."
                    ),
                ),
            ),
        ),
    )


def test_render_table_includes_each_outcome_and_summary() -> None:
    output = render_table(_report())

    assert "┌" in output
    assert "┼" in output
    assert "└" in output
    assert "example-policy" in output
    assert "🔴" in output
    assert "Repositories" in output
    assert "Policy evaluations" in output
    assert "⚪" in output
    assert "100.0%" in output
    assert "-------|" not in output
    assert "When" not in output


def test_render_table_groups_failure_causes() -> None:
    report = RunReport(
        summary=RunSummary(
            repositories=2,
            synchronized=2,
            sync_failures=0,
            skipped=0,
            evaluations=2,
            compliant=0,
            drifted=0,
            not_applicable=0,
            evaluation_failures=2,
            pull_requests_created=0,
            pull_requests_updated=0,
            pull_requests_open=0,
            pull_requests_recreated=0,
        ),
        outcomes=(
            RepositoryOutcome(
                "first", "example", "unknown", "error", error="Bazel failed"
            ),
            RepositoryOutcome(
                "second", "example", "unknown", "error", error="Bazel failed"
            ),
        ),
    )

    output = render_table(report)

    assert "⚠ failed" in output
    assert "100.0%" in output
    assert "2" in output
    assert "Bazel failed" in output
    assert "example/first" in output
    assert "example/second" in output


def test_render_table_wraps_long_values_for_terminal_width(monkeypatch) -> None:
    monkeypatch.setenv("COLUMNS", "80")
    report = RunReport(
        summary=RunSummary(
            repositories=1,
            synchronized=1,
            sync_failures=0,
            skipped=0,
            evaluations=1,
            compliant=0,
            drifted=1,
            not_applicable=0,
            evaluation_failures=0,
            pull_requests_created=0,
            pull_requests_updated=0,
            pull_requests_open=0,
            pull_requests_recreated=0,
        ),
        outcomes=(
            RepositoryOutcome(
                repository="a-repository-with-a-deliberately-long-name",
                policy_id="a-policy-with-a-deliberately-long-name",
                when="yes (live)",
                status="changes-required",
                changes=(
                    Change(
                        Path(".github/workflows/a-very-long-file-name.yml"),
                        "replace a value with a much longer explanation",
                    ),
                ),
            ),
        ),
    )

    output = render_table(report)

    assert "a-policy-with" in output
    assert "a-policy-with-a-del" in output
    assert "iberately-long-name" in output
    assert "a-repository-with" in output
    assert "a-repository-with-a-de" in output
    assert "liberately-long-name" in output
    assert "with a much" in output
    assert "longer" in output
    assert "│" in output


def test_render_table_uses_red_changes_required_marker() -> None:
    output = render_table(_report())

    assert "🔴" in output


def test_render_table_shows_pull_request_state_and_number_in_status() -> None:
    report = _report()
    outcome = report.outcomes[0]
    report = RunReport(
        summary=report.summary,
        outcomes=(
            RepositoryOutcome(
                outcome.repository,
                outcome.policy_id,
                outcome.when,
                outcome.status,
                changes=outcome.changes,
                pull_request_url="https://github.example/owner/repo/pull/1",
                policy_pr_status="open",
            ),
        ),
    )

    output = render_table(report)

    assert "Pull request" not in output
    assert "🔄 open #1" in output
    assert "https://github.example/owner/repo/pull/1" not in output


def test_render_table_keeps_compliance_status_for_mismatched_pull_request() -> None:
    report = _report()
    outcome = report.outcomes[0]
    report = RunReport(
        summary=report.summary,
        outcomes=(
            RepositoryOutcome(
                outcome.repository,
                outcome.policy_id,
                "yes (live)",
                "compliant",
                pull_request_url="https://github.example/owner/repo/pull/1",
                policy_pr_status="open",
            ),
        ),
    )

    output = render_table(report).split("\n\n📊 Summary", 1)[0]

    assert "│ ✅     │" in output
    assert "open" not in output
    assert "#1" not in output


def test_render_json_is_machine_readable_and_versioned() -> None:
    output = render_json(_report())

    assert '"schema_version": 2' in output
    assert '"drifted": 1' in output
    assert '"path": ".gitignore"' in output


def test_render_json_includes_selected_policy_metadata() -> None:
    base_report = _report()
    report = RunReport(
        summary=base_report.summary,
        outcomes=base_report.outcomes,
        policies=(
            Policy(
                id="example-policy",
                title="Keep generated files out of version control",
                description="Ensure generated build output is ignored.",
                bazel_condition=None,
                ensure=(),
                legacy_names=("old-example-policy",),
            ),
        ),
    )

    document = json.loads(render_json(report))

    assert document["policies"] == [
        {
            "description": "Ensure generated build output is ignored.",
            "id": "example-policy",
            "legacy_names": ["old-example-policy"],
            "title": "Keep generated files out of version control",
        }
    ]


def test_render_markdown_distinguishes_compliance_and_policy_pr_status() -> None:
    report = RunReport(
        summary=RunSummary(
            repositories=4,
            synchronized=4,
            sync_failures=0,
            skipped=0,
            evaluations=4,
            compliant=3,
            drifted=1,
            not_applicable=0,
            evaluation_failures=0,
            pull_requests_created=0,
            pull_requests_updated=0,
            pull_requests_open=0,
            pull_requests_recreated=0,
            pull_requests_closed=1,
        ),
        outcomes=(
            RepositoryOutcome(
                "compliant",
                "example",
                "yes (live)",
                "compliant",
                policy_pr_status="none",
            ),
            RepositoryOutcome(
                "open-pr",
                "example",
                "yes (live)",
                "changes-required",
                pull_request_url="https://github.example/owner/open-pr/pull/1",
                policy_pr_status="open",
            ),
            RepositoryOutcome(
                "merged-pr",
                "example",
                "yes (live)",
                "compliant",
                pull_request_url="https://github.example/owner/merged-pr/pull/2",
                policy_pr_status="merged",
            ),
            RepositoryOutcome(
                "closed-pr",
                "example",
                "yes (live)",
                "pull-request-closed",
                pull_request_url="https://github.example/owner/closed-pr/pull/3",
                policy_pr_status="closed",
            ),
        ),
    )

    output = render_markdown(report)

    assert "# Repository policy compliance" in output
    assert "| Repository | example |" in output
    assert "| compliant | ✅ |" in output
    assert (
        "| open-pr | ❌ [![Open PR](https://img.shields.io/badge/-Open-2ea043"
        "?style=flat&logo=github&logoColor=white)]"
        "(https://github.example/owner/open-pr/pull/1) |"
    ) in output
    assert (
        "| merged-pr | ✅ [![Merged PR](https://img.shields.io/badge/-Merged-8250df"
        "?style=flat&logo=github&logoColor=white)]"
        "(https://github.example/owner/merged-pr/pull/2) |"
    ) in output
    assert (
        "| closed-pr | ✅ [![Closed PR](https://img.shields.io/badge/-Closed-6e7781"
        "?style=flat&logo=github&logoColor=white)]"
        "(https://github.example/owner/closed-pr/pull/3) |"
    ) in output
    assert "`✅ 1 closed`" in output


def test_render_markdown_uses_policies_as_matrix_columns_and_keeps_details_compact() -> (
    None
):
    report = RunReport(
        summary=RunSummary(
            repositories=2,
            synchronized=2,
            sync_failures=0,
            skipped=0,
            evaluations=4,
            compliant=1,
            drifted=1,
            not_applicable=1,
            evaluation_failures=1,
            pull_requests_created=0,
            pull_requests_updated=0,
            pull_requests_open=0,
            pull_requests_recreated=0,
        ),
        outcomes=(
            RepositoryOutcome(
                "first",
                "policy-a",
                "yes (live)",
                "changes-required",
                changes=(Change(Path(".gitignore"), "add generated files", None),),
            ),
            RepositoryOutcome("first", "policy-b", "no (live)", "not-applicable"),
            RepositoryOutcome("second", "policy-a", "yes (live)", "compliant"),
            RepositoryOutcome(
                "second", "policy-b", "unknown", "error", error="Bazel failed"
            ),
        ),
    )

    output = render_markdown(report)

    assert "| Repository | policy-a | policy-b |" in output
    assert "| first | ❌ | N/A |" in output
    assert "| second | ✅ | ⚠️ |" in output
    assert "<summary>Details (2)</summary>" in output
    assert ".gitignore: add generated files" in output
    assert "Bazel failed" in output
    assert "| Repository | Policy | Status |" not in output
