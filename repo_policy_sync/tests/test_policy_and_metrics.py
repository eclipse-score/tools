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

from repo_policy_sync.errors import PolicyError
from repo_policy_sync.policy import (
    BUNDLED_POLICY_DIRECTORY,
    discover_policy_paths,
    load_policy,
    load_policies,
    resolve_policy_names,
)


def test_load_policy_rejects_path_outside_repository(tmp_path: Path) -> None:
    policy_path = tmp_path / "example" / "policy.yml"
    policy_path.parent.mkdir()
    policy_path.write_text(
        """title: Example
ensure:
  - type: ensure_no_such_file
    path: ../outside
"""
    )
    with pytest.raises(PolicyError, match="repository-relative"):
        load_policy(policy_path)


def test_load_policy_rejects_unknown_fields(tmp_path: Path) -> None:
    policy_path = tmp_path / "example" / "policy.yml"
    policy_path.parent.mkdir()
    policy_path.write_text(
        """title: Example
ensure:
  - type: ensure_no_such_file
    path: obsolete-file
surprise: value
"""
    )
    with pytest.raises(PolicyError, match="unexpected fields"):
        load_policy(policy_path)


def test_load_policy_rejects_malformed_yaml(tmp_path: Path) -> None:
    policy_path = tmp_path / "example" / "policy.yml"
    policy_path.parent.mkdir()
    policy_path.write_text("title: [unterminated\n", encoding="utf-8")

    with pytest.raises(PolicyError, match="invalid YAML"):
        load_policy(policy_path)


def test_load_policy_rejects_non_utf8_input(tmp_path: Path) -> None:
    policy_path = tmp_path / "example" / "policy.yml"
    policy_path.parent.mkdir()
    policy_path.write_bytes(b"title: Example\nensure: [\xff]\n")

    with pytest.raises(PolicyError, match="decode policy.*UTF-8"):
        load_policy(policy_path)


def test_load_policy_rejects_non_utf8_synchronize_file_source(tmp_path: Path) -> None:
    policy_path = tmp_path / "example" / "policy.yml"
    policy_path.parent.mkdir()
    (policy_path.parent / "asset.txt").write_bytes(b"\xff")
    policy_path.write_text(
        """title: Example
ensure:
  - type: synchronize_file
    path: target.txt
    source: asset.txt
""",
        encoding="utf-8",
    )

    with pytest.raises(PolicyError, match="source must be UTF-8"):
        load_policy(policy_path)


def test_load_policy_rejects_unsupported_operation(tmp_path: Path) -> None:
    policy_path = tmp_path / "example" / "policy.yml"
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
            "- type: ensure_no_such_file\n  path: .",
            "path must be a non-empty repository-relative path",
        ),
        (
            "- type: ensure_bazel_dependency\n"
            "  dockerfile: .devcontainer/Dockerfile\n"
            "  module_file: MODULE.bazel\n"
            "  image: '   '\n"
            "  module_name: score_devcontainer",
            "image must be a non-empty string",
        ),
        (
            "- type: migrate_devcontainer_json\n"
            "  sources: ['.']\n"
            "  destination: .devcontainer/devcontainer.json\n"
            "  dockerfile: .devcontainer/Dockerfile\n"
            "  image: ghcr.io/eclipse-score/devcontainer",
            "path must be a non-empty repository-relative path",
        ),
        (
            "- type: replace_regex\n"
            "  path: example.txt\n"
            "  pattern: '['\n"
            "  replacement: current",
            "invalid replace_regex pattern or replacement",
        ),
        (
            "- type: synchronize_devcontainer_version\n"
            "  dockerfile: .devcontainer/Dockerfile\n"
            "  module_file: MODULE.bazel\n"
            "  image: ghcr.io/eclipse-score/devcontainer\n"
            "  module_name: ''",
            "module_name must be a non-empty string",
        ),
        (
            "- type: synchronize_bazel_dependencies\n"
            "  module_file: MODULE.bazel\n"
            "  dependencies: []",
            "dependencies must be a non-empty list",
        ),
        (
            "- type: synchronize_file\n  path: workflow.yml\n  source: missing.yml",
            "synchronize_file source must be an existing file",
        ),
    ],
)
def test_load_policy_reports_invalid_values_for_each_operation(
    tmp_path: Path, operation: str, message: str
) -> None:
    policy_path = tmp_path / "example" / "policy.yml"
    policy_path.parent.mkdir()
    policy_path.write_text(
        f"title: Example\nensure:\n{operation}\n",
        encoding="utf-8",
    )

    with pytest.raises(PolicyError, match=message):
        load_policy(policy_path)


def test_load_policy_rejects_invalid_file_condition_regex(tmp_path: Path) -> None:
    policy_path = tmp_path / "example" / "policy.yml"
    policy_path.parent.mkdir()
    policy_path.write_text(
        """title: Example
when:
  file_contains:
    path: README.md
    pattern: "["
ensure:
  - type: ensure_no_such_file
    path: obsolete-file
""",
        encoding="utf-8",
    )

    with pytest.raises(PolicyError, match="invalid file_contains pattern"):
        load_policy(policy_path)


def test_load_policy_rejects_invalid_replace_regex_replacement(
    tmp_path: Path,
) -> None:
    policy_path = tmp_path / "example" / "policy.yml"
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


def test_load_policy_rejects_policy_scoped_labels(tmp_path: Path) -> None:
    policy_path = tmp_path / "example" / "policy.yml"
    policy_path.parent.mkdir()
    policy_path.write_text(
        """title: Example
labels: [automation]
ensure:
  - type: ensure_no_such_file
    path: obsolete-file
"""
    )

    with pytest.raises(PolicyError, match="unexpected fields"):
        load_policy(policy_path)


def test_load_policy_accepts_operation_rationale(tmp_path: Path) -> None:
    policy_path = tmp_path / "example" / "policy.yml"
    policy_path.parent.mkdir()
    policy_path.write_text(
        """title: Example
ensure:
  - type: ensure_no_such_file
    path: obsolete-file
    rationale: This file is obsolete.
"""
    )

    policy = load_policy(policy_path)

    assert policy.ensure[0].rationale == "This file is obsolete."


def test_load_policy_rejects_non_string_operation_rationale(tmp_path: Path) -> None:
    policy_path = tmp_path / "example" / "policy.yml"
    policy_path.parent.mkdir()
    policy_path.write_text(
        """title: Example
ensure:
  - type: ensure_no_such_file
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
  - type: ensure_no_such_file
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
  - type: ensure_no_such_file
    path: obsolete
""",
            "when.bazel.direct_module_dependencies must be a list",
        ),
        (
            """title: Example
ensure:
  - type: ensure_no_such_file
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
    tmp_path: Path, content: str, message: str
) -> None:
    policy_path = tmp_path / "example" / "policy.yml"
    policy_path.parent.mkdir()
    policy_path.write_text(content, encoding="utf-8")

    with pytest.raises(PolicyError, match=message):
        load_policy(policy_path)


def test_discover_policies_uses_deterministic_directory_order(tmp_path: Path) -> None:
    for name in ("z-policy", "a-policy"):
        policy_path = tmp_path / name / "policy.yml"
        policy_path.parent.mkdir()
        policy_path.write_text(
            f"title: {name}\nensure:\n  - type: ensure_no_such_file\n    path: {name}\n"
        )

    paths = discover_policy_paths(tmp_path)

    assert [path.parent.name for path in paths] == ["a-policy", "z-policy"]
    assert [policy.id for policy in load_policies(paths)] == ["a-policy", "z-policy"]


def test_load_policies_accepts_an_explicit_empty_selection() -> None:
    assert load_policies(()) == ()


def test_load_policies_rejects_ids_that_collide_on_policy_branches(
    tmp_path: Path,
) -> None:
    for name in ("foo_bar", "foo-bar"):
        policy_path = tmp_path / name / "policy.yml"
        policy_path.parent.mkdir()
        policy_path.write_text(
            f"title: {name}\nensure:\n  - type: ensure_no_such_file\n    path: {name}\n",
            encoding="utf-8",
        )

    with pytest.raises(PolicyError, match="map to the same policy branch slug"):
        load_policies(discover_policy_paths(tmp_path))


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


def test_resolve_policy_names_uses_custom_policy_directory(tmp_path: Path) -> None:
    policy_path = tmp_path / "etas-standard" / "policy.yml"
    policy_path.parent.mkdir()
    policy_path.write_text(
        """title: ETAS standard
ensure:
  - type: ensure_no_such_file
    path: obsolete-file
"""
    )

    assert resolve_policy_names(("etas-standard",), tmp_path) == (policy_path,)


def test_resolve_policy_names_combines_policy_directories(tmp_path: Path) -> None:
    paths = []
    for directory_name, policy_name in (
        ("etas", "etas-standard"),
        ("score", "minimum-bazel-version"),
    ):
        policy_path = tmp_path / directory_name / policy_name / "policy.yml"
        policy_path.parent.mkdir(parents=True)
        policy_path.write_text(
            f"""title: {policy_name}
ensure:
  - type: ensure_no_such_file
    path: obsolete-file
"""
        )
        paths.append(policy_path)

    assert resolve_policy_names(
        ("etas-standard", "minimum-bazel-version"),
        tuple((tmp_path / "etas", tmp_path / "score")),
    ) == tuple(paths)


def test_load_policy_rejects_legacy_identity_field(tmp_path: Path) -> None:
    policy_path = tmp_path / "current" / "policy.yml"
    policy_path.parent.mkdir()
    policy_path.write_text(
        """legacy_ids: [old]
title: Example
ensure:
  - type: ensure_no_such_file
    path: obsolete-file
"""
    )

    with pytest.raises(PolicyError, match="unexpected fields.*legacy_ids"):
        load_policy(policy_path)


def test_load_policy_rejects_inline_id(tmp_path: Path) -> None:
    policy_path = tmp_path / "current" / "policy.yml"
    policy_path.parent.mkdir()
    policy_path.write_text(
        """id: different
title: Example
ensure:
  - type: ensure_no_such_file
    path: obsolete-file
"""
    )

    with pytest.raises(PolicyError, match="unexpected fields.*id"):
        load_policy(policy_path)


def test_resolve_policy_names_rejects_unknown_policy_name(tmp_path: Path) -> None:
    known_policy = tmp_path / "known" / "policy.yml"
    known_policy.parent.mkdir()
    known_policy.write_text(
        """title: Known
ensure:
  - type: ensure_no_such_file
    path: obsolete-file
"""
    )

    with pytest.raises(PolicyError, match="unknown policy name"):
        resolve_policy_names(("not-a-policy",), tmp_path)


def test_load_policy_accepts_conditional_after_apply_command(tmp_path: Path) -> None:
    policy_path = tmp_path / "example" / "policy.yml"
    policy_path.parent.mkdir()
    policy_path.write_text(
        """title: Example
ensure:
  - type: ensure_no_such_file
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
    tmp_path: Path,
) -> None:
    policy_path = tmp_path / "example" / "policy.yml"
    policy_path.parent.mkdir()
    policy_path.write_text(
        """title: Example
when:
  file_contains:
    path: .devcontainer/Dockerfile
    pattern: '^FROM example:'
ensure:
  - type: ensure_no_such_file
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


def test_load_policy_accepts_file_exists_condition(tmp_path: Path) -> None:
    policy_path = tmp_path / "example" / "policy.yml"
    policy_path.parent.mkdir()
    policy_path.write_text(
        """title: Example
when:
  file_exists: MODULE.bazel
ensure:
  - type: ensure_no_such_file
    path: obsolete-file
"""
    )

    policy = load_policy(policy_path)

    assert policy.file_exists_condition is not None
    assert policy.file_exists_condition.path == Path("MODULE.bazel")


def test_load_policy_accepts_any_direct_bazel_dependency_and_glob_condition(
    tmp_path: Path,
) -> None:
    policy_path = tmp_path / "example" / "policy.yml"
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
  - type: ensure_no_such_file
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
