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

from repo_policy_sync.src.engine import apply_policy
from repo_policy_sync.src.errors import PolicyError
from repo_policy_sync.src.models import EnsureBazelDependency, Policy
from repo_policy_sync.src.operations.ensure_bazel_dependency import (
    EnsureBazelDependencyOperation,
)


def test_ensure_bazel_dependency_ignores_commented_dependency(
    tmp_path: Path,
) -> None:
    module = tmp_path / "MODULE.bazel"
    module.write_text('# bazel_dep(name = "example_dependency", version = "1.0.0")\n')
    policy = Policy(
        "example",
        "Example",
        None,
        None,
        (
            EnsureBazelDependency(
                Path("MODULE.bazel"),
                "example_dependency",
                "1.9.0",
            ),
        ),
    )

    apply_policy(tmp_path, policy)

    assert module.read_text().count('name = "example_dependency"') == 2


def test_ensure_bazel_dependency_ignores_commented_arguments(
    tmp_path: Path,
) -> None:
    module = tmp_path / "MODULE.bazel"
    module.write_text(
        """bazel_dep(
    name = "example_dependency",
    # version = "1.0.0",
    version = "1.9.0",
)
"""
    )
    policy = Policy(
        "example",
        "Example",
        None,
        None,
        (
            EnsureBazelDependency(
                Path("MODULE.bazel"),
                "example_dependency",
                "1.9.0",
            ),
        ),
    )

    apply_policy(tmp_path, policy)

    assert module.read_text().count('name = "example_dependency"') == 1


def test_ensure_bazel_dependency_ignores_commented_name_argument(
    tmp_path: Path,
) -> None:
    module = tmp_path / "MODULE.bazel"
    module.write_text(
        """bazel_dep(
    # name = "example_dependency",
    name = "other_dependency",
    version = "1.0.0",
)
"""
    )
    policy = Policy(
        "example",
        "Example",
        None,
        None,
        (
            EnsureBazelDependency(
                Path("MODULE.bazel"),
                "example_dependency",
                "1.9.0",
            ),
        ),
    )

    apply_policy(tmp_path, policy)

    assert module.read_text().count('name = "example_dependency"') == 2


@pytest.mark.parametrize("module_name", ['foo"bar', "foo\nbar"])
def test_parse_rejects_module_names_that_cannot_be_emitted_as_starlark(
    module_name: str,
) -> None:
    with pytest.raises(PolicyError, match="module_name"):
        EnsureBazelDependencyOperation().parse(
            {
                "type": "ensure_bazel_dependency",
                "module_file": "MODULE.bazel",
                "module_name": module_name,
                "version": "1.9.0",
            },
            Path("policy.yml"),
        )
