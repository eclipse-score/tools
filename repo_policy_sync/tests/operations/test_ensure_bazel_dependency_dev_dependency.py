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

from repo_policy_sync.src.errors import PolicyError, RepoPolicySyncError
from repo_policy_sync.src.models import EnsureBazelDependencyDevDependency
from repo_policy_sync.src.operations import apply, describe_changes
from repo_policy_sync.src.operations.ensure_bazel_dependency_dev_dependency import (
    EnsureBazelDependencyDevDependencyOperation,
)


def _operation(dev_dependency: object = False):
    return EnsureBazelDependencyDevDependencyOperation().parse(
        {
            "type": "ensure_bazel_dependency_dev_dependency",
            "module_name": "example_dependency",
            "dev_dependency": dev_dependency,
        },
        Path("policy.yml"),
    )


@pytest.mark.parametrize("dev_dependency", [None, "false", 0, 1])
def test_parse_rejects_non_boolean_dev_dependency(dev_dependency: object) -> None:
    with pytest.raises(PolicyError, match="dev_dependency must be a boolean"):
        _operation(dev_dependency)


@pytest.mark.parametrize(
    ("before", "after", "desired"),
    [
        (
            'bazel_dep(name = "example_dependency", version = "1.0.0")\n',
            'bazel_dep(name = "example_dependency", version = "1.0.0", dev_dependency = True)\n',
            True,
        ),
        (
            """bazel_dep(
    name = "example_dependency",
    version = "1.0.0",
)
""",
            """bazel_dep(
    name = "example_dependency",
    version = "1.0.0",
    dev_dependency = True,
)
""",
            True,
        ),
        (
            'bazel_dep(name = "example_dependency", version = "1.0.0", dev_dependency = False)\n',
            'bazel_dep(name = "example_dependency", version = "1.0.0", dev_dependency = True)\n',
            True,
        ),
        (
            'bazel_dep(name = "example_dependency", dev_dependency = True, version = "1.0.0")\n',
            'bazel_dep(name = "example_dependency", version = "1.0.0")\n',
            False,
        ),
        (
            'bazel_dep(name = "example_dependency", version = "1.0.0", dev_dependency = True)\n',
            'bazel_dep(name = "example_dependency", version = "1.0.0")\n',
            False,
        ),
        (
            'bazel_dep(name = "example_dependency", version = "1.0.0", dev_dependency = False)\n',
            'bazel_dep(name = "example_dependency", version = "1.0.0")\n',
            False,
        ),
        (
            """bazel_dep(
    name = "example_dependency",
    version = "1.0.0",
    dev_dependency = True,
)
""",
            """bazel_dep(
    name = "example_dependency",
    version = "1.0.0",
)
""",
            False,
        ),
        (
            """bazel_dep(
    dev_dependency = True,
    name = "example_dependency",
    version = "1.0.0",
)
""",
            """bazel_dep(
    name = "example_dependency",
    version = "1.0.0",
)
""",
            False,
        ),
    ],
)
def test_ensure_dev_dependency_setting_is_added_or_removed(
    tmp_path: Path, before: str, after: str, desired: bool
) -> None:
    module = tmp_path / "MODULE.bazel"
    module.write_text(before, encoding="utf-8")
    operation = _operation(desired)

    assert describe_changes(tmp_path, operation)
    apply(tmp_path, operation)

    assert module.read_text(encoding="utf-8") == after
    assert describe_changes(tmp_path, operation) == ()


def test_ensure_dev_dependency_ignores_commented_arguments(tmp_path: Path) -> None:
    module = tmp_path / "MODULE.bazel"
    module.write_text(
        """bazel_dep(
    name = "example_dependency",
    # dev_dependency = True,
    version = "1.0.0",
)
""",
        encoding="utf-8",
    )

    assert describe_changes(tmp_path, _operation()) == ()


def test_ensure_dev_dependency_ignores_missing_target_dependency(
    tmp_path: Path,
) -> None:
    module = tmp_path / "MODULE.bazel"
    original = 'bazel_dep(name = "other_dependency", version = "1.0.0")\n'
    module.write_text(original, encoding="utf-8")

    operation = _operation()
    assert describe_changes(tmp_path, operation) == ()
    apply(tmp_path, operation)

    assert module.read_text(encoding="utf-8") == original


def test_ensure_dev_dependency_rejects_duplicate_target_dependencies(
    tmp_path: Path,
) -> None:
    module = tmp_path / "MODULE.bazel"
    module.write_text(
        """bazel_dep(name = "example_dependency", version = "1.0.0")
bazel_dep(name = "example_dependency", version = "1.1.0")
""",
        encoding="utf-8",
    )

    with pytest.raises(RepoPolicySyncError, match="at most one bazel_dep"):
        describe_changes(tmp_path, _operation())


def test_ensure_dev_dependency_rejects_duplicate_attributes(tmp_path: Path) -> None:
    module = tmp_path / "MODULE.bazel"
    module.write_text(
        """bazel_dep(
    name = "example_dependency",
    dev_dependency = True,
    dev_dependency = False,
)
""",
        encoding="utf-8",
    )

    with pytest.raises(RepoPolicySyncError, match="dev_dependency at most once"):
        describe_changes(tmp_path, _operation())


def test_operation_model_is_registered() -> None:
    operation = _operation(True)
    assert isinstance(operation, EnsureBazelDependencyDevDependency)
