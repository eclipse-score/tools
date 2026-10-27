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
from repo_policy_sync.src.models import Change, EnsureLine, Policy


def test_ensure_line_replaces_complete_line_glob_matches(fake_repo: Path) -> None:
    (fake_repo / ".gitignore").write_text("prefix_build_suffix\n_build\n")
    policy = Policy(
        "example",
        "Example",
        None,
        None,
        (EnsureLine(Path(".gitignore"), "_build", (), ("*_build*",)),),
    )

    apply_policy(fake_repo, policy)

    assert (fake_repo / ".gitignore").read_text() == "_build\n"
    assert apply_policy(fake_repo, policy).changes == ()


def test_ensure_line_creates_a_missing_file(fake_repo: Path) -> None:
    policy = Policy(
        "example",
        "Example",
        None,
        None,
        (EnsureLine(Path(".gitignore"), "_build", ()),),
    )

    evaluation = apply_policy(fake_repo, policy)

    assert evaluation.changes == (Change(Path(".gitignore"), "add '_build'"),)
    assert (fake_repo / ".gitignore").read_text() == "_build\n"


def test_ensure_line_rejects_a_directory_target(fake_repo: Path) -> None:
    (fake_repo / "target").mkdir()
    policy = Policy(
        "example", "Example", None, None, (EnsureLine(Path("target"), "line", ()),)
    )

    with pytest.raises(RepoPolicySyncError, match="must be a file"):
        evaluate_policy(fake_repo, policy)


def test_ensure_line_rejects_a_symlink_outside_the_checkout(fake_repo: Path) -> None:
    outside = fake_repo.parent / "outside"
    outside.mkdir()
    (outside / "target").write_text("outside\n")
    (fake_repo / "target").symlink_to(outside / "target")
    policy = Policy(
        "example",
        "Example",
        None,
        None,
        (EnsureLine(Path("target"), "inside", ()),),
    )

    with pytest.raises(RepoPolicySyncError, match="symbolic link"):
        evaluate_policy(fake_repo, policy)
