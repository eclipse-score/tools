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

"""Executable examples colocated with the policies they specify."""

import subprocess
from pathlib import Path
from shutil import copytree

import pytest

from repo_policy_sync.src.engine import apply_policy
from repo_policy_sync.src.policy import BUNDLED_POLICY_DIRECTORY, load_policy


def _policy_examples():
    return [
        pytest.param(policy_directory, case, id=f"{policy_directory.name}-{case.name}")
        for policy_directory in sorted(
            path for path in BUNDLED_POLICY_DIRECTORY.iterdir() if path.is_dir()
        )
        for case in sorted(
            candidate for candidate in policy_directory.iterdir() if candidate.is_dir()
        )
    ]


@pytest.mark.parametrize(("policy_directory", "case"), _policy_examples())
def test_policy_examples_apply_as_documented(
    policy_directory: Path, case: Path, tmp_path: Path
) -> None:
    policy = load_policy(policy_directory / "policy.yml")
    actual = tmp_path / policy_directory.name / case.name
    copytree(case / "before", actual)
    expected = tmp_path / policy_directory.name / f"{case.name}-expected"
    copytree(case / "before", expected)
    # Apply the specification independently so comparison includes unchanged
    # files and catches any extra files created by the policy. --no-index keeps
    # this independent of a surrounding checkout; --allow-empty supports cases
    # where the policy must leave the input unchanged.
    subprocess.run(
        [
            "git",
            "apply",
            "--no-index",
            "--allow-empty",
            str((case / "expected.patch").resolve()),
        ],
        cwd=expected,
        check=True,
    )

    apply_policy(actual, policy, organization="eclipse-score")

    assert _tree(actual) == _tree(expected), case
    evaluation = apply_policy(expected, policy, organization="eclipse-score")
    assert evaluation.changes == (), case


def _tree(root: Path) -> dict[Path, str]:
    return {
        path.relative_to(root): path.read_text(encoding="utf-8")
        for path in sorted(root.rglob("*"))
        if path.is_file()
    }
