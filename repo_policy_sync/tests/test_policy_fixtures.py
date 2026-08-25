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

from pathlib import Path
from shutil import copytree

from repo_policy_sync.engine import apply_policy
from repo_policy_sync.policy import BUNDLED_POLICY_DIRECTORY, load_policy


def test_policy_examples_apply_as_documented(tmp_path: Path) -> None:
    for policy_directory in sorted(
        path for path in BUNDLED_POLICY_DIRECTORY.iterdir() if path.is_dir()
    ):
        policy = load_policy(policy_directory / "policy.yml")
        for case in sorted(
            path for path in policy_directory.iterdir() if path.is_dir()
        ):
            actual = tmp_path / policy_directory.name / case.name
            copytree(case / "before", actual)

            apply_policy(actual, policy, organization="eclipse-score")

            assert _tree(actual) == _tree(case / "after"), case
            compliant = tmp_path / policy_directory.name / f"{case.name}-compliant"
            copytree(case / "after", compliant)
            assert (
                apply_policy(compliant, policy, organization="eclipse-score").changes
                == ()
            ), case


def _tree(root: Path) -> dict[Path, str]:
    return {
        path.relative_to(root): path.read_text(encoding="utf-8")
        for path in sorted(root.rglob("*"))
        if path.is_file()
    }
