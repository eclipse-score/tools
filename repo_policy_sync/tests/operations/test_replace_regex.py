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
