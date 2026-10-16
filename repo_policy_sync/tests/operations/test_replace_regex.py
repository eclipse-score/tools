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

import pytest

from repo_policy_sync.src.engine import apply_policy, evaluate_policy
from repo_policy_sync.src.errors import RepoPolicySyncError
from repo_policy_sync.src.models import Change, Policy, ReplaceRegex


def test_replace_regex_applies_and_is_idempotent(fake_repo: Path) -> None:
    (fake_repo / "example.txt").write_text("legacy\nunchanged\n")
    policy = Policy(
        "example",
        "Example",
        None,
        None,
        (ReplaceRegex(Path("example.txt"), "legacy", "current"),),
    )

    evaluation = apply_policy(fake_repo, policy)

    assert evaluation.changes == (Change(Path("example.txt"), "replace matching text"),)
    assert (fake_repo / "example.txt").read_text() == "current\nunchanged\n"
    assert apply_policy(fake_repo, policy).changes == ()


def test_replace_regex_accepts_a_missing_file(fake_repo: Path) -> None:
    policy = Policy(
        "example",
        "Example",
        None,
        None,
        (ReplaceRegex(Path("example.txt"), "legacy", "current"),),
    )

    assert evaluate_policy(fake_repo, policy).changes == ()


def test_replace_regex_unmatched_glob_does_not_create_target_directories(
    fake_repo: Path,
) -> None:
    policy = Policy(
        "example",
        "Example",
        None,
        None,
        (ReplaceRegex(Path(".github/workflows/*.yml"), "legacy", "current"),),
    )

    assert evaluate_policy(fake_repo, policy).changes == ()
    assert not (fake_repo / ".github").exists()


def test_replace_regex_rejects_symlink_directories_before_glob_descent(
    fake_repo: Path,
) -> None:
    outside = fake_repo.parent / "outside"
    (outside / "workflows").mkdir(parents=True)
    (outside / "workflows" / "README.md").write_text("unmatched\n")
    (fake_repo / ".github").symlink_to(outside, target_is_directory=True)
    policy = Policy(
        "example",
        "Example",
        None,
        None,
        (ReplaceRegex(Path(".github/workflows/*.yml"), "legacy", "current"),),
    )

    with pytest.raises(RepoPolicySyncError, match="symbolic link"):
        evaluate_policy(fake_repo, policy)


def test_replace_regex_rejects_a_directory_target(fake_repo: Path) -> None:
    (fake_repo / "target").mkdir()
    policy = Policy(
        "example",
        "Example",
        None,
        None,
        (ReplaceRegex(Path("target"), "legacy", "current"),),
    )

    with pytest.raises(RepoPolicySyncError, match="must be a file"):
        evaluate_policy(fake_repo, policy)


def test_replace_regex_updates_top_level_workflows_and_is_idempotent(
    fake_repo: Path,
) -> None:
    workflows = fake_repo / ".github" / "workflows"
    workflows.mkdir(parents=True)
    (workflows / "build.yml").write_text("runs-on: ubuntu-latest\n")
    (workflows / "test.yaml").write_text("runs-on: ubuntu-latest\n")
    (fake_repo / "README.md").write_text("ubuntu-latest\n")
    policy = Policy(
        "example",
        "Example",
        None,
        None,
        (
            ReplaceRegex(
                Path(".github/workflows/*.yml"), "ubuntu-latest", "ubuntu-24.04"
            ),
            ReplaceRegex(
                Path(".github/workflows/*.yaml"),
                "ubuntu-latest",
                "ubuntu-24.04",
            ),
        ),
    )

    evaluation = apply_policy(fake_repo, policy)

    assert {change.path for change in evaluation.changes} == {
        Path(".github/workflows/build.yml"),
        Path(".github/workflows/test.yaml"),
    }
    assert (workflows / "build.yml").read_text() == "runs-on: ubuntu-24.04\n"
    assert (workflows / "test.yaml").read_text() == "runs-on: ubuntu-24.04\n"
    assert (fake_repo / "README.md").read_text() == "ubuntu-latest\n"
    assert apply_policy(fake_repo, policy).changes == ()
