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

from repo_policy_sync.src.errors import PolicyError
from repo_policy_sync.src.policy import (
    BUNDLED_POLICY_DIRECTORY,
    discover_policy_paths,
    load_policy,
    load_policies,
    resolve_policy_names,
)


def test_load_policy_rejects_path_outside_repository(fake_repo: Path) -> None:
    policy_path = fake_repo / "example" / "policy.yml"
    policy_path.parent.mkdir()
    policy_path.write_text(
        """title: Example
ensure:
  - type: remove_file
    path: ../outside
"""
    )
    with pytest.raises(PolicyError, match="repository-relative"):
        load_policy(policy_path)


def test_load_policy_rejects_unknown_fields(fake_repo: Path) -> None:
    policy_path = fake_repo / "example" / "policy.yml"
    policy_path.parent.mkdir()
    policy_path.write_text(
        """title: Example
ensure:
  - type: remove_file
    path: obsolete-file
surprise: value
"""
    )
    with pytest.raises(PolicyError, match="unexpected fields"):
        load_policy(policy_path)


def test_load_policy_rejects_malformed_yaml(fake_repo: Path) -> None:
    policy_path = fake_repo / "example" / "policy.yml"
    policy_path.parent.mkdir()
    policy_path.write_text("title: [unterminated\n", encoding="utf-8")

    with pytest.raises(PolicyError, match="invalid YAML"):
        load_policy(policy_path)


def test_load_policy_rejects_non_utf8_input(fake_repo: Path) -> None:
    policy_path = fake_repo / "example" / "policy.yml"
    policy_path.parent.mkdir()
    policy_path.write_bytes(b"title: Example\nensure: [\xff]\n")

    with pytest.raises(PolicyError, match="decode policy.*UTF-8"):
        load_policy(policy_path)


def test_load_policy_rejects_unsupported_operation(fake_repo: Path) -> None:
    policy_path = fake_repo / "example" / "policy.yml"
    policy_path.parent.mkdir()
    policy_path.write_text(
        """title: Example
ensure:
  - type: unsupported_operation
    path: example.txt
""",
        encoding="utf-8",
    )

    with pytest.raises(PolicyError, match="unsupported ensure type"):
        load_policy(policy_path)


def test_load_policy_accepts_policy_value_reference(fake_repo: Path) -> None:
    policy_path = fake_repo / "example" / "policy.yml"
    policy_path.parent.mkdir()
    policy_path.write_text(
        """title: Example
values:
  devcontainer_version:
    type: dockerfile_image_version
    dockerfile: .devcontainer/Dockerfile
    image: ghcr.io/eclipse-score/devcontainer
ensure:
  - type: ensure_bazel_dependency
    module_file: MODULE.bazel
    module_name: score_devcontainer
    version:
      ref: devcontainer_version
""",
        encoding="utf-8",
    )

    policy = load_policy(policy_path)

    assert policy.values[0].name == "devcontainer_version"
    assert policy.ensure[0].version.name == "devcontainer_version"


def test_load_policy_rejects_unknown_policy_value_reference(fake_repo: Path) -> None:
    policy_path = fake_repo / "example" / "policy.yml"
    policy_path.parent.mkdir()
    policy_path.write_text(
        """title: Example
ensure:
  - type: ensure_bazel_dependency
    module_file: MODULE.bazel
    module_name: score_devcontainer
    version:
      ref: missing_version
""",
        encoding="utf-8",
    )

    with pytest.raises(PolicyError, match="unknown value reference"):
        load_policy(policy_path)


def test_load_policy_rejects_unemittable_bazel_module_name(fake_repo: Path) -> None:
    policy_path = fake_repo / "example" / "policy.yml"
    policy_path.parent.mkdir()
    policy_path.write_text(
        """title: Example
ensure:
  - type: ensure_bazel_dependency
    module_file: MODULE.bazel
    module_name: 'foo"bar'
    version: 1.0.0
""",
        encoding="utf-8",
    )

    with pytest.raises(PolicyError, match="module_name"):
        load_policy(policy_path)


def test_load_policy_accepts_value_exists_condition(fake_repo: Path) -> None:
    policy_path = fake_repo / "example" / "policy.yml"
    policy_path.parent.mkdir()
    policy_path.write_text(
        """title: Example
values:
  version:
    type: dockerfile_image_version
    dockerfile: Dockerfile
    image: example/image
when:
  value_exists: version
ensure:
  - type: remove_file
    path: obsolete-file
""",
        encoding="utf-8",
    )

    policy = load_policy(policy_path)

    assert policy.value_exists_condition is not None
    assert policy.value_exists_condition.name == "version"


def test_load_policy_rejects_unknown_value_exists_condition(fake_repo: Path) -> None:
    policy_path = fake_repo / "example" / "policy.yml"
    policy_path.parent.mkdir()
    policy_path.write_text(
        """title: Example
when:
  value_exists: missing_version
ensure:
  - type: remove_file
    path: obsolete-file
""",
        encoding="utf-8",
    )

    with pytest.raises(PolicyError, match="unknown value reference"):
        load_policy(policy_path)


@pytest.mark.parametrize(
    ("operation", "message"),
    [
        (
            "- type: ensure_line\n  path: example.txt\n  line: 1",
            "line must be a non-empty string",
        ),
        (
            "- type: ensure_minimum_version\n"
            "  path: .bazelversion\n"
            "  minimum_version: '8.6'",
            "minimum_version must be a numeric major.minor.patch version",
        ),
        (
            "- type: remove_file\n  path: .",
            "path must be a non-empty repository-relative path",
        ),
        (
            "- type: replace_regex\n"
            "  path: example.txt\n"
            "  pattern: '['\n"
            "  replacement: current",
            "invalid replace_regex pattern or replacement",
        ),
    ],
)
def test_load_policy_reports_invalid_values_for_each_operation(
    fake_repo: Path, operation: str, message: str
) -> None:
    policy_path = fake_repo / "example" / "policy.yml"
    policy_path.parent.mkdir()
    policy_path.write_text(
        f"title: Example\nensure:\n{operation}\n",
        encoding="utf-8",
    )

    with pytest.raises(PolicyError, match=message):
        load_policy(policy_path)


def test_load_policy_rejects_invalid_file_condition_regex(fake_repo: Path) -> None:
    policy_path = fake_repo / "example" / "policy.yml"
    policy_path.parent.mkdir()
    policy_path.write_text(
        """title: Example
when:
  file_contains:
    path: README.md
    pattern: "["
ensure:
  - type: remove_file
    path: obsolete-file
""",
        encoding="utf-8",
    )

    with pytest.raises(PolicyError, match="invalid file_contains pattern"):
        load_policy(policy_path)


def test_load_policy_rejects_invalid_replace_regex_replacement(
    fake_repo: Path,
) -> None:
    policy_path = fake_repo / "example" / "policy.yml"
    policy_path.parent.mkdir()
    policy_path.write_text(
        """title: Example
ensure:
  - type: replace_regex
    path: example.txt
    pattern: legacy
    replacement: '\\1'
""",
        encoding="utf-8",
    )

    with pytest.raises(
        PolicyError, match="invalid replace_regex pattern or replacement"
    ):
        load_policy(policy_path)


def test_load_policy_rejects_policy_scoped_labels(fake_repo: Path) -> None:
    policy_path = fake_repo / "example" / "policy.yml"
    policy_path.parent.mkdir()
    policy_path.write_text(
        """title: Example
labels: [automation]
ensure:
  - type: remove_file
    path: obsolete-file
"""
    )

    with pytest.raises(PolicyError, match="unexpected fields"):
        load_policy(policy_path)


def test_load_policy_accepts_operation_rationale(fake_repo: Path) -> None:
    policy_path = fake_repo / "example" / "policy.yml"
    policy_path.parent.mkdir()
    policy_path.write_text(
        """title: Example
ensure:
  - type: remove_file
    path: obsolete-file
    rationale: This file is obsolete.
"""
    )

    policy = load_policy(policy_path)

    assert policy.ensure[0].rationale == "This file is obsolete."


def test_load_policy_rejects_non_string_operation_rationale(fake_repo: Path) -> None:
    policy_path = fake_repo / "example" / "policy.yml"
    policy_path.parent.mkdir()
    policy_path.write_text(
        """title: Example
ensure:
  - type: remove_file
    path: obsolete-file
    rationale: [not, a, string]
"""
    )

    with pytest.raises(PolicyError, match="rationale must be a non-empty string"):
        load_policy(policy_path)


@pytest.mark.parametrize(
    ("content", "message"),
    [
        (
            """title: Example
description: '   '
ensure:
  - type: remove_file
    path: obsolete
""",
            "description must be a non-empty string",
        ),
        (
            """title: Example
when:
  bazel:
    direct_module_dependencies: ['   ']
ensure:
  - type: remove_file
    path: obsolete
""",
            "when.bazel.direct_module_dependencies must be a list",
        ),
        (
            """title: Example
ensure:
  - type: remove_file
    path: obsolete
after_apply:
  - command: ['bazel', '   ']
    when_file_exists: lock
    description: Run
""",
            "after_apply command must be a non-empty list of strings",
        ),
    ],
)
def test_load_policy_rejects_whitespace_only_values(
    fake_repo: Path, content: str, message: str
) -> None:
    policy_path = fake_repo / "example" / "policy.yml"
    policy_path.parent.mkdir()
    policy_path.write_text(content, encoding="utf-8")

    with pytest.raises(PolicyError, match=message):
        load_policy(policy_path)


def test_discover_policies_uses_deterministic_directory_order(fake_repo: Path) -> None:
    for name in ("z-policy", "a-policy"):
        policy_path = fake_repo / name / "policy.yml"
        policy_path.parent.mkdir()
        policy_path.write_text(
            f"title: {name}\nensure:\n  - type: remove_file\n    path: {name}\n"
        )

    paths = discover_policy_paths(fake_repo)

    assert [path.parent.name for path in paths] == ["a-policy", "z-policy"]
    assert [policy.id for policy in load_policies(paths)] == ["a-policy", "z-policy"]


def test_load_policies_accepts_an_explicit_empty_selection() -> None:
    assert load_policies(()) == ()


def test_load_policies_rejects_ids_that_collide_on_policy_branches(
    fake_repo: Path,
) -> None:
    for name in ("foo_bar", "foo-bar"):
        policy_path = fake_repo / name / "policy.yml"
        policy_path.parent.mkdir()
        policy_path.write_text(
            f"title: {name}\nensure:\n  - type: remove_file\n    path: {name}\n",
            encoding="utf-8",
        )

    with pytest.raises(PolicyError, match="map to the same policy branch slug"):
        load_policies(discover_policy_paths(fake_repo))


def test_resolve_policy_names_uses_bundled_policy_directory_names() -> None:
    paths = resolve_policy_names(
        ("minimal-bazel-module-declaration", "minimum-bazel-version"),
        BUNDLED_POLICY_DIRECTORY,
    )

    assert paths == (
        BUNDLED_POLICY_DIRECTORY / "minimal-bazel-module-declaration" / "policy.yml",
        BUNDLED_POLICY_DIRECTORY / "minimum-bazel-version" / "policy.yml",
    )


def test_resolve_policy_names_rejects_legacy_ids() -> None:
    with pytest.raises(PolicyError, match="unknown policy name"):
        resolve_policy_names(("module-one-line",), BUNDLED_POLICY_DIRECTORY)


def test_load_policy_accepts_legacy_names(fake_repo: Path) -> None:
    policy_path = fake_repo / "current" / "policy.yml"
    policy_path.parent.mkdir()
    policy_path.write_text(
        """legacy_names: [old-policy]
title: Example
ensure:
  - type: remove_file
    path: obsolete-file
""",
        encoding="utf-8",
    )

    assert load_policy(policy_path).legacy_names == ("old-policy",)


def test_resolve_policy_names_uses_custom_policy_directory(fake_repo: Path) -> None:
    policy_path = fake_repo / "etas-standard" / "policy.yml"
    policy_path.parent.mkdir()
    policy_path.write_text(
        """title: ETAS standard
ensure:
  - type: remove_file
    path: obsolete-file
"""
    )

    assert resolve_policy_names(("etas-standard",), fake_repo) == (policy_path,)


def test_resolve_policy_names_combines_policy_directories(fake_repo: Path) -> None:
    paths = []
    for directory_name, policy_name in (
        ("etas", "etas-standard"),
        ("score", "minimum-bazel-version"),
    ):
        policy_path = fake_repo / directory_name / policy_name / "policy.yml"
        policy_path.parent.mkdir(parents=True)
        policy_path.write_text(
            f"""title: {policy_name}
ensure:
  - type: remove_file
    path: obsolete-file
"""
        )
        paths.append(policy_path)

    assert resolve_policy_names(
        ("etas-standard", "minimum-bazel-version"),
        tuple((fake_repo / "etas", fake_repo / "score")),
    ) == tuple(paths)


def test_load_policy_rejects_legacy_identity_field(fake_repo: Path) -> None:
    policy_path = fake_repo / "current" / "policy.yml"
    policy_path.parent.mkdir()
    policy_path.write_text(
        """legacy_ids: [old]
title: Example
ensure:
  - type: remove_file
    path: obsolete-file
"""
    )

    with pytest.raises(PolicyError, match="unexpected fields.*legacy_ids"):
        load_policy(policy_path)


def test_load_policy_rejects_inline_id(fake_repo: Path) -> None:
    policy_path = fake_repo / "current" / "policy.yml"
    policy_path.parent.mkdir()
    policy_path.write_text(
        """id: different
title: Example
ensure:
  - type: remove_file
    path: obsolete-file
"""
    )

    with pytest.raises(PolicyError, match="unexpected fields.*id"):
        load_policy(policy_path)


def test_resolve_policy_names_rejects_unknown_policy_name(fake_repo: Path) -> None:
    known_policy = fake_repo / "known" / "policy.yml"
    known_policy.parent.mkdir()
    known_policy.write_text(
        """title: Known
ensure:
  - type: remove_file
    path: obsolete-file
"""
    )

    with pytest.raises(PolicyError, match="unknown policy name"):
        resolve_policy_names(("not-a-policy",), fake_repo)


def test_load_policy_accepts_conditional_after_apply_command(fake_repo: Path) -> None:
    policy_path = fake_repo / "example" / "policy.yml"
    policy_path.parent.mkdir()
    policy_path.write_text(
        """title: Example
ensure:
  - type: remove_file
    path: obsolete-file
after_apply:
  - command: [bazel, mod, deps]
    when_file_exists: MODULE.bazel.lock
    description: Regenerate the lock file.
"""
    )

    policy = load_policy(policy_path)

    assert policy.after_apply[0].command == ("bazel", "mod", "deps")
    assert policy.after_apply[0].when_file_exists == Path("MODULE.bazel.lock")


def test_load_policy_accepts_file_content_condition_and_changed_path_guard(
    fake_repo: Path,
) -> None:
    policy_path = fake_repo / "example" / "policy.yml"
    policy_path.parent.mkdir()
    policy_path.write_text(
        """title: Example
when:
  file_contains:
    path: .devcontainer/Dockerfile
    pattern: '^FROM example:'
ensure:
  - type: remove_file
    path: obsolete-file
after_apply:
  - command: [bazel, mod, deps]
    when_file_exists: MODULE.bazel.lock
    when_path_changed: MODULE.bazel
    description: Regenerate the lock file.
"""
    )

    policy = load_policy(policy_path)

    assert policy.file_contains_condition is not None
    assert policy.file_contains_condition.path == Path(".devcontainer/Dockerfile")
    assert policy.after_apply[0].when_path_changed == Path("MODULE.bazel")


def test_load_policy_accepts_file_exists_condition(fake_repo: Path) -> None:
    policy_path = fake_repo / "example" / "policy.yml"
    policy_path.parent.mkdir()
    policy_path.write_text(
        """title: Example
when:
  file_exists: MODULE.bazel
ensure:
  - type: remove_file
    path: obsolete-file
"""
    )

    policy = load_policy(policy_path)

    assert policy.file_exists_condition is not None
    assert policy.file_exists_condition.path == Path("MODULE.bazel")


def test_load_policy_accepts_any_direct_bazel_dependency_and_glob_condition(
    fake_repo: Path,
) -> None:
    policy_path = fake_repo / "example" / "policy.yml"
    policy_path.parent.mkdir()
    policy_path.write_text(
        """title: Example
when:
  bazel:
    direct_module_dependencies: [score_platform]
    any_direct_module_dependencies: [score_process, score_process_description]
    any_direct_module_conditions:
      - score_process_description < 2.1.1
  file_contains_any:
    - path: '**/BUILD'
      pattern: score_process
ensure:
  - type: remove_file
    path: obsolete-file
"""
    )

    policy = load_policy(policy_path)

    assert policy.bazel_condition is not None
    assert policy.bazel_condition.direct_module_dependencies == ("score_platform",)
    assert policy.bazel_condition.any_direct_module_dependencies == (
        "score_process",
        "score_process_description",
    )
    assert policy.bazel_condition.any_direct_module_conditions[0].module_name == (
        "score_process_description"
    )
    assert policy.bazel_condition.any_direct_module_conditions[0].operator == "<"
    assert policy.bazel_condition.any_direct_module_conditions[0].version == (2, 1, 1)
    assert policy.file_contains_any_condition is not None
    assert policy.file_contains_any_condition.conditions[0].path == Path("**/BUILD")
