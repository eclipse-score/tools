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
from repo_policy_sync.src.models import Change, EnsureMinimumVersion, Policy
from repo_policy_sync.src.policy import BUNDLED_POLICY_DIRECTORY, load_policy


def test_ensure_minimum_version_upgrades_only_older_versions(fake_repo: Path) -> None:
    version_file = fake_repo / ".bazelversion"
    policy = Policy(
        "example",
        "Example",
        None,
        None,
        (EnsureMinimumVersion(Path(".bazelversion"), "8.6.0"),),
    )

    version_file.write_text("8.5.2\n")
    assert apply_policy(fake_repo, policy).changes == (
        Change(Path(".bazelversion"), "upgrade from '8.5.2' to '8.6.0'"),
    )
    assert version_file.read_text() == "8.6.0\n"

    version_file.write_text("8.6.1\n")
    assert apply_policy(fake_repo, policy).changes == ()
    assert version_file.read_text() == "8.6.1\n"


def test_ensure_minimum_version_accepts_a_missing_file(fake_repo: Path) -> None:
    policy = Policy(
        "example",
        "Example",
        None,
        None,
        (EnsureMinimumVersion(Path(".bazelversion"), "8.6.0"),),
    )

    assert evaluate_policy(fake_repo, policy).changes == ()


def test_minimum_bazel_policy_requires_a_declared_version_file(fake_repo: Path) -> None:
    policy = load_policy(
        BUNDLED_POLICY_DIRECTORY / "minimum-bazel-version" / "policy.yml"
    )

    evaluation = evaluate_policy(fake_repo, policy)

    assert evaluation.applies is False
    assert policy.file_exists_condition is not None
    assert policy.file_exists_condition.path == Path(".bazelversion")
    assert policy.after_apply[0].when_path_changed == Path(".bazelversion")


def test_ensure_minimum_version_rejects_a_directory_target(fake_repo: Path) -> None:
    (fake_repo / ".bazelversion").mkdir()
    policy = Policy(
        "example",
        "Example",
        None,
        None,
        (EnsureMinimumVersion(Path(".bazelversion"), "8.6.0"),),
    )

    with pytest.raises(RepoPolicySyncError, match="must be a file"):
        evaluate_policy(fake_repo, policy)
