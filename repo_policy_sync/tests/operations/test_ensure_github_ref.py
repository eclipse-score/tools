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

from repo_policy_sync.src.engine import apply_policy, evaluate_policy
from repo_policy_sync.src.errors import PolicyError, RepoPolicySyncError
from repo_policy_sync.src.github import GitHubCli
from repo_policy_sync.src.models import (
    EnsureExactGitHubRef,
    EnsureMinimalGitHubRef,
    Policy,
)
from repo_policy_sync.src.policy import load_policy


CHECKOUT = "actions/checkout"
SETUP_PYTHON = "actions/setup-python"
BAZEL_CACHE = "eclipse-score/cicd-actions/setup-bazel-cache"
REUSABLE_WORKFLOW = "eclipse-score/cicd-actions/.github/workflows/reusable.yml"
BAZEL_CACHE_REPOSITORY = "eclipse-score/cicd-actions"
OLD_SHA = "a" * 40
MINIMUM_SHA = "b" * 40
NEW_SHA = "c" * 40


def _policy(operations) -> Policy:
    return Policy("example", "Update GitHub Actions", None, None, operations)


def _mock_gh_api(monkeypatch, *, statuses: dict[str, str] | None = None):
    calls: list[list[str]] = []
    statuses = statuses or {}

    def run(command: list[str]) -> str:
        calls.append(command)
        route = command[-1]
        if route in {
            f"/repos/{SETUP_PYTHON}/tags?per_page=100",
            f"/repos/{BAZEL_CACHE_REPOSITORY}/tags?per_page=100",
        }:
            return json.dumps(
                [
                    [
                        {"name": "v5.1", "commit": {"sha": MINIMUM_SHA}},
                        {"name": "v5.2", "commit": {"sha": NEW_SHA}},
                    ]
                ]
            )
        if route.startswith(
            (
                f"/repos/{SETUP_PYTHON}/compare/",
                f"/repos/{BAZEL_CACHE_REPOSITORY}/compare/",
            )
        ):
            current = route.rsplit("...", 1)[1]
            return json.dumps({"status": statuses[current]})
        raise AssertionError(f"unexpected gh api request: {command}")

    monkeypatch.setattr(GitHubCli, "_run", staticmethod(run))
    return calls


def test_policy_parser_accepts_exact_and_minimal_github_target_operations(
    fake_repo: Path,
) -> None:
    policy_path = fake_repo / "example" / "policy.yml"
    policy_path.parent.mkdir()
    policy_path.write_text(
        """title: Update GitHub Actions
ensure:
  - type: ensure_exact
    target: eclipse-score/cicd-actions/setup-bazel-cache
    ref: 0123456789abcdef0123456789abcdef01234567
  - type: ensure_minimal
    target: eclipse-score/cicd-actions/.github/workflows/reusable.yml
    minimum_version: v5.1
""",
        encoding="utf-8",
    )

    policy = load_policy(policy_path)

    assert policy.ensure == (
        EnsureExactGitHubRef(BAZEL_CACHE, "0123456789abcdef0123456789abcdef01234567"),
        EnsureMinimalGitHubRef(REUSABLE_WORKFLOW, "v5.1"),
    )


@pytest.mark.parametrize("ref", ("v4", "release/stable", "0" * 40))
def test_ensure_exact_updates_tags_branches_and_shas(fake_repo: Path, ref: str) -> None:
    workflow = fake_repo / ".github/workflows/ci.yml"
    workflow.parent.mkdir(parents=True)
    workflow.write_text(
        f"jobs:\n  build:\n    uses: {CHECKOUT}@{ref} # keep this comment\n",
        encoding="utf-8",
    )
    desired = "0123456789abcdef0123456789abcdef01234567"

    evaluation = apply_policy(
        fake_repo,
        _policy((EnsureExactGitHubRef(CHECKOUT, desired),)),
    )

    assert len(evaluation.changes) == 1
    assert f"uses: {CHECKOUT}@{desired} # keep this comment" in workflow.read_text()


def test_ensure_exact_does_not_call_github_for_a_matching_action(
    fake_repo: Path, monkeypatch
) -> None:
    workflow = fake_repo / ".github/workflows/ci.yml"
    workflow.parent.mkdir(parents=True)
    workflow.write_text(f"jobs:\n  build:\n    uses: {CHECKOUT}@v4\n")
    calls = []
    monkeypatch.setattr(
        GitHubCli,
        "_run",
        staticmethod(lambda command: calls.append(command) or ""),
    )

    assert (
        evaluate_policy(
            fake_repo, _policy((EnsureExactGitHubRef(CHECKOUT, "v4"),))
        ).changes
        == ()
    )
    assert calls == []


def test_ensure_exact_updates_sequence_style_step_reference(fake_repo: Path) -> None:
    workflow = fake_repo / ".github/workflows/ci.yml"
    workflow.parent.mkdir(parents=True)
    workflow.write_text(f"jobs:\n  build:\n    steps:\n      - uses: {CHECKOUT}@v4\n")
    desired = "0123456789abcdef0123456789abcdef01234567"

    evaluation = apply_policy(
        fake_repo,
        _policy((EnsureExactGitHubRef(CHECKOUT, desired),)),
    )

    assert len(evaluation.changes) == 1
    assert f"      - uses: {CHECKOUT}@{desired}\n" in workflow.read_text()


def test_ensure_exact_updates_nested_action_without_touching_sibling_targets(
    fake_repo: Path,
) -> None:
    workflow = fake_repo / ".github/workflows/ci.yml"
    workflow.parent.mkdir(parents=True)
    workflow.write_text(
        "jobs:\n"
        "  build:\n"
        f"    uses: {BAZEL_CACHE}@v1\n"
        "  other:\n"
        f"    uses: {BAZEL_CACHE_REPOSITORY}/other-action@v1\n"
        "  reusable:\n"
        f"    uses: {REUSABLE_WORKFLOW}@v1\n"
    )
    desired = "0123456789abcdef0123456789abcdef01234567"

    apply_policy(
        fake_repo,
        _policy((EnsureExactGitHubRef(BAZEL_CACHE, desired),)),
    )

    contents = workflow.read_text()
    assert f"uses: {BAZEL_CACHE}@{desired}" in contents
    assert f"uses: {BAZEL_CACHE_REPOSITORY}/other-action@v1" in contents
    assert f"uses: {REUSABLE_WORKFLOW}@v1" in contents


def test_ensure_exact_updates_external_reusable_workflow_reference(
    fake_repo: Path,
) -> None:
    workflow = fake_repo / ".github/workflows/call-reusable.yaml"
    workflow.parent.mkdir(parents=True)
    workflow.write_text(f"jobs:\n  verify:\n    uses: {REUSABLE_WORKFLOW}@v1\n")

    apply_policy(
        fake_repo,
        _policy((EnsureExactGitHubRef(REUSABLE_WORKFLOW, "release/stable"),)),
    )

    assert f"uses: {REUSABLE_WORKFLOW}@release/stable" in workflow.read_text()


def test_action_ref_updates_preserve_crlf_workflow_formatting(fake_repo: Path) -> None:
    workflow = fake_repo / ".github/workflows/ci.yml"
    workflow.parent.mkdir(parents=True)
    workflow.write_bytes(
        f"jobs:\r\n  build:\r\n    uses: {CHECKOUT}@v3 # comment\r\n".encode()
    )

    apply_policy(
        fake_repo,
        _policy((EnsureExactGitHubRef(CHECKOUT, "v4"),)),
    )

    assert workflow.read_bytes() == (
        f"jobs:\r\n  build:\r\n    uses: {CHECKOUT}@v4 # comment\r\n".encode()
    )


def test_action_ref_ignores_uses_text_inside_run_scalar(fake_repo: Path) -> None:
    workflow = fake_repo / ".github/workflows/ci.yml"
    workflow.parent.mkdir(parents=True)
    workflow.write_text(
        "jobs:\n"
        "  build:\n"
        "    runs-on: ubuntu-latest\n"
        "    steps:\n"
        "      - run: |\n"
        f"          uses: {CHECKOUT}@v3\n"
    )

    assert (
        evaluate_policy(
            fake_repo, _policy((EnsureExactGitHubRef(CHECKOUT, "v4"),))
        ).changes
        == ()
    )


@pytest.mark.parametrize(
    ("current", "expected"),
    (("v5.0", "v5.1"), ("v5.1", "v5.1"), ("v5.2", "v5.2")),
)
def test_ensure_minimal_updates_only_older_semver_tags(
    fake_repo: Path,
    monkeypatch,
    current: str,
    expected: str,
) -> None:
    workflow = fake_repo / ".github/workflows/ci.yml"
    workflow.parent.mkdir(parents=True)
    workflow.write_text(
        f"jobs:\n  build:\n    steps:\n      - uses: {SETUP_PYTHON}@{current}\n"
    )
    _mock_gh_api(monkeypatch)

    evaluation = apply_policy(
        fake_repo,
        _policy((EnsureMinimalGitHubRef(SETUP_PYTHON, "v5.1"),)),
    )

    assert f"uses: {SETUP_PYTHON}@{expected}" in workflow.read_text()
    assert bool(evaluation.changes) is (current == "v5.0")


def test_ensure_minimal_is_idempotent_after_updating_a_tag(
    fake_repo: Path, monkeypatch
) -> None:
    workflow = fake_repo / ".github/workflows/ci.yml"
    workflow.parent.mkdir(parents=True)
    workflow.write_text(f"jobs:\n  build:\n    uses: {SETUP_PYTHON}@v5.0\n")
    calls = _mock_gh_api(monkeypatch)
    policy = _policy((EnsureMinimalGitHubRef(SETUP_PYTHON, "v5.1"),))

    assert apply_policy(fake_repo, policy).changes
    assert apply_policy(fake_repo, policy).changes == ()
    assert sum("/tags?" in command[-1] for command in calls) == 2


@pytest.mark.parametrize(
    ("status", "changed"),
    (("behind", True), ("identical", False), ("ahead", False)),
)
def test_ensure_minimal_compares_full_sha_pins(
    fake_repo: Path, monkeypatch, status: str, changed: bool
) -> None:
    workflow = fake_repo / ".github/workflows/ci.yml"
    workflow.parent.mkdir(parents=True)
    workflow.write_text(f"jobs:\n  build:\n    uses: {SETUP_PYTHON}@{OLD_SHA}\n")
    calls = _mock_gh_api(monkeypatch, statuses={OLD_SHA: status})
    policy = _policy((EnsureMinimalGitHubRef(SETUP_PYTHON, "v5.1"),))

    evaluation = apply_policy(fake_repo, policy)

    assert (f"uses: {SETUP_PYTHON}@{MINIMUM_SHA}" in workflow.read_text()) is changed
    assert bool(evaluation.changes) is changed
    assert sum("/tags?" in command[-1] for command in calls) == 1
    assert sum("/compare/" in command[-1] for command in calls) == 1


def test_ensure_minimal_keeps_tagged_newer_sha_without_comparing_histories(
    fake_repo: Path, monkeypatch
) -> None:
    """A newer tagged release remains valid when release histories diverge."""

    workflow = fake_repo / ".github/workflows/ci.yml"
    workflow.parent.mkdir(parents=True)
    workflow.write_text(f"jobs:\n  build:\n    uses: {SETUP_PYTHON}@{NEW_SHA}\n")
    calls = _mock_gh_api(monkeypatch, statuses={NEW_SHA: "diverged"})
    policy = _policy((EnsureMinimalGitHubRef(SETUP_PYTHON, "v5.1"),))

    evaluation = evaluate_policy(fake_repo, policy)

    assert evaluation.changes == ()
    assert f"uses: {SETUP_PYTHON}@{NEW_SHA}" in workflow.read_text()
    assert sum("/compare/" in command[-1] for command in calls) == 0


def test_ensure_minimal_rejects_diverged_sha_histories(
    fake_repo: Path, monkeypatch
) -> None:
    workflow = fake_repo / ".github/workflows/ci.yml"
    workflow.parent.mkdir(parents=True)
    workflow.write_text(f"jobs:\n  build:\n    uses: {SETUP_PYTHON}@{OLD_SHA}\n")
    _mock_gh_api(monkeypatch, statuses={OLD_SHA: "diverged"})

    with pytest.raises(RepoPolicySyncError, match="histories diverged"):
        evaluate_policy(
            fake_repo,
            _policy((EnsureMinimalGitHubRef(SETUP_PYTHON, "v5.1"),)),
        )


def test_ensure_minimal_updates_nested_action_and_reusable_workflow_sha_pins(
    fake_repo: Path, monkeypatch
) -> None:
    workflows = fake_repo / ".github/workflows"
    nested = workflows / "nested"
    nested.mkdir(parents=True)
    action_workflow = workflows / "action.yml"
    reusable_workflow = nested / "reusable.yaml"
    action_workflow.write_text(f"jobs:\n  build:\n    uses: {BAZEL_CACHE}@{OLD_SHA}\n")
    reusable_workflow.write_text(
        f"jobs:\n  call:\n    uses: {REUSABLE_WORKFLOW}@{OLD_SHA}\n"
    )
    calls = _mock_gh_api(monkeypatch, statuses={OLD_SHA: "behind"})
    policy = _policy(
        (
            EnsureMinimalGitHubRef(BAZEL_CACHE, "v5.1"),
            EnsureMinimalGitHubRef(REUSABLE_WORKFLOW, "v5.1"),
        )
    )

    evaluation = apply_policy(fake_repo, policy)

    assert f"{BAZEL_CACHE}@{MINIMUM_SHA}" in action_workflow.read_text()
    assert f"{REUSABLE_WORKFLOW}@{MINIMUM_SHA}" in reusable_workflow.read_text()
    assert len(evaluation.changes) == 2
    assert sum("/tags?" in command[-1] for command in calls) == 1
    assert sum("/compare/" in command[-1] for command in calls) == 1


def test_one_policy_can_update_multiple_actions_and_workflow_files(
    fake_repo: Path, monkeypatch
) -> None:
    workflows = fake_repo / ".github/workflows"
    nested = workflows / "nested"
    nested.mkdir(parents=True)
    first = workflows / "a.yml"
    second = nested / "b.yaml"
    first.write_text(
        f"jobs:\n  one:\n    uses: {CHECKOUT}@v3\n"
        f"  two:\n    uses: {CHECKOUT}@v3 # comment\n"
    )
    second.write_text(f"jobs:\n  python:\n    uses: {SETUP_PYTHON}@v5.0\n")
    calls = _mock_gh_api(monkeypatch)
    policy = _policy(
        (
            EnsureExactGitHubRef(CHECKOUT, "v4"),
            EnsureMinimalGitHubRef(SETUP_PYTHON, "v5.1"),
        )
    )

    evaluation = apply_policy(fake_repo, policy)

    assert {change.path for change in evaluation.changes} == {
        Path(".github/workflows/a.yml"),
        Path(".github/workflows/nested/b.yaml"),
    }
    assert first.read_text().count(f"{CHECKOUT}@v4") == 2
    assert "# comment" in first.read_text()
    assert f"{SETUP_PYTHON}@v5.1" in second.read_text()
    assert sum("/tags?" in command[-1] for command in calls) == 1


def test_minimal_leaves_branches_and_missing_matches_without_remote_calls(
    fake_repo: Path, monkeypatch
) -> None:
    workflow = fake_repo / ".github/workflows/ci.yml"
    workflow.parent.mkdir(parents=True)
    workflow.write_text(
        "# uses: actions/setup-python@v1\n"
        f"jobs:\n  build:\n    uses: {SETUP_PYTHON}@main\n"
    )
    calls = []
    monkeypatch.setattr(
        GitHubCli,
        "_run",
        staticmethod(lambda command: calls.append(command) or ""),
    )
    policy = _policy((EnsureMinimalGitHubRef(SETUP_PYTHON, "v5.1"),))

    assert evaluate_policy(fake_repo, policy).changes == ()
    assert apply_policy(fake_repo, policy).changes == ()
    assert calls == []


def test_action_operation_validation_rejects_local_actions_and_bad_versions(
    fake_repo: Path,
) -> None:
    policy_path = fake_repo / "example" / "policy.yml"
    policy_path.parent.mkdir()
    policy_path.write_text(
        """title: Example
ensure:
  - type: ensure_exact
    target: ./.github/actions/local
    ref: main
"""
    )
    with pytest.raises(PolicyError, match="local targets are not supported"):
        load_policy(policy_path)

    policy_path.write_text(
        """title: Example
ensure:
  - type: ensure_minimal
    target: actions/checkout
    minimum_version: latest
"""
    )
    with pytest.raises(PolicyError, match="minimum_version must be a semantic version"):
        load_policy(policy_path)
