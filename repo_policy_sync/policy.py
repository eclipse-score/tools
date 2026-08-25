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

"""Loading and validating policy YAML files."""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any

import yaml

from .errors import PolicyError
from .bazel import parse_bazel_dependency_condition
from .models import (
    AfterApplyCommand,
    BazelCondition,
    BazelDependencyCondition,
    FileContainsAnyCondition,
    FileContainsCondition,
    FileExistsCondition,
    Policy,
    policy_branch_slug,
)
from .operations import parse_operation
from .operations._validation import safe_relative_path

# Policy definitions belong to the consuming repository. Keep the default
# relative to the caller's working directory so `./policies` is enough.
DEFAULT_POLICY_DIRECTORY = Path("policies")
BUNDLED_POLICY_DIRECTORY = Path(__file__).with_name("policies")


def load_policies(paths: tuple[Path, ...] | None = None) -> tuple[Policy, ...]:
    """Load explicitly selected policies or the default policy catalogue."""

    selected_paths = (
        discover_policy_paths(DEFAULT_POLICY_DIRECTORY) if paths is None else paths
    )
    policies = tuple(load_policy(path) for path in selected_paths)
    identifiers = [policy.id for policy in policies]
    duplicates = sorted(
        {identifier for identifier in identifiers if identifiers.count(identifier) > 1}
    )
    if duplicates:
        raise PolicyError(f"policy IDs must be unique: {', '.join(duplicates)}")
    branches: dict[str, str] = {}
    for policy in policies:
        slug = policy_branch_slug(policy.id)
        if not slug:
            raise PolicyError(f"policy ID cannot produce a branch name: {policy.id!r}")
        previous = branches.get(slug)
        if previous is not None and previous != policy.id:
            raise PolicyError(
                f"policy IDs {previous!r} and {policy.id!r} map to the same "
                f"policy branch slug {slug!r}"
            )
        branches[slug] = policy.id
    return policies


def resolve_policy_names(
    names: tuple[str, ...],
    directory: Path | tuple[Path, ...] = DEFAULT_POLICY_DIRECTORY,
) -> tuple[Path, ...]:
    """Resolve policy directory names across one or more policy directories."""

    available_paths = tuple(
        sorted(
            {
                path
                for policy_directory in _policy_directories(directory)
                for path in discover_policy_paths(policy_directory)
            },
            key=str,
        )
    )
    paths_by_name: dict[str, Path] = {}
    for path in available_paths:
        policy = load_policy(path)
        name = policy.id
        if name in paths_by_name and paths_by_name[name] != path:
            raise PolicyError(f"policy ID is not unique: {name}")
        paths_by_name[name] = path
    unknown_names = sorted(set(names) - set(paths_by_name))
    if unknown_names:
        available_names = ", ".join(sorted(paths_by_name))
        raise PolicyError(
            f"unknown policy name(s): {', '.join(unknown_names)}; "
            f"available: {available_names}"
        )
    return tuple(paths_by_name[name] for name in names)


def _policy_directories(directory: Path | tuple[Path, ...]) -> tuple[Path, ...]:
    return (directory,) if isinstance(directory, Path) else directory


def discover_policy_paths(directory: Path) -> tuple[Path, ...]:
    """Find policy definitions in deterministic policy-directory order."""

    if not directory.is_dir():
        raise PolicyError(f"policy directory does not exist: {directory}")
    paths = tuple(sorted(directory.rglob("policy.yml")))
    if not paths:
        raise PolicyError(f"policy directory contains no YAML files: {directory}")
    return paths


def load_policy(path: Path) -> Policy:
    """Load one policy file using the intentionally small policy schema."""

    try:
        raw = yaml.safe_load(path.read_text(encoding="utf-8"))
    except OSError as exc:
        raise PolicyError(f"could not read policy {path}: {exc}") from exc
    except UnicodeError as exc:
        raise PolicyError(f"could not decode policy {path} as UTF-8: {exc}") from exc
    except yaml.YAMLError as exc:
        raise PolicyError(f"invalid YAML in policy {path}: {exc}") from exc
    if not isinstance(raw, dict):
        raise PolicyError(f"policy {path} must contain a YAML mapping")
    _expect_keys(
        raw,
        {"title", "description", "when", "ensure", "after_apply"},
        path,
    )

    policy_id = path.parent.name
    if not policy_id:
        raise PolicyError(
            f"policy {path}: policy.yml must be inside a named policy directory"
        )
    title = _required_string(raw, "title", path)
    description = _optional_string(raw, "description", path)
    (
        bazel_condition,
        file_exists_condition,
        file_contains_condition,
        file_contains_any_condition,
    ) = _parse_condition(raw.get("when"), path)
    ensure_raw = raw.get("ensure")
    if not isinstance(ensure_raw, list) or not ensure_raw:
        raise PolicyError(f"policy {path}: ensure must be a non-empty list")
    ensure = tuple(parse_operation(item, path) for item in ensure_raw)
    after_apply = _parse_after_apply(raw.get("after_apply", []), path)
    return Policy(
        id=policy_id,
        title=title,
        description=description,
        bazel_condition=bazel_condition,
        ensure=ensure,
        after_apply=after_apply,
        file_exists_condition=file_exists_condition,
        file_contains_condition=file_contains_condition,
        file_contains_any_condition=file_contains_any_condition,
    )


def _parse_condition(
    raw: object, source: Path
) -> tuple[
    BazelCondition | None,
    FileExistsCondition | None,
    FileContainsCondition | None,
    FileContainsAnyCondition | None,
]:
    if raw is None:
        return None, None, None, None
    if not isinstance(raw, dict):
        raise PolicyError(f"policy {source}: when must be a mapping")
    if (
        not set(raw).issubset(
            {"bazel", "file_exists", "file_contains", "file_contains_any"}
        )
        or not raw
    ):
        raise PolicyError(
            f"policy {source}: only when.bazel, when.file_exists, when.file_contains, "
            "and when.file_contains_any are supported"
        )
    bazel_condition = None
    if "bazel" in raw:
        bazel = raw["bazel"]
        if (
            not isinstance(bazel, dict)
            or not set(bazel).issubset(
                {
                    "direct_module_dependencies",
                    "any_direct_module_dependencies",
                    "any_direct_module_conditions",
                }
            )
            or not bazel
        ):
            raise PolicyError(
                f"policy {source}: bazel must contain only direct_module_dependencies, "
                "any_direct_module_dependencies, and any_direct_module_conditions"
            )
        # Missing fields become empty lists so either kind of dependency check
        # can be used on its own.
        dependencies = _string_list(
            bazel.get("direct_module_dependencies", []),
            "when.bazel.direct_module_dependencies",
            source,
        )
        any_dependencies = _string_list(
            bazel.get("any_direct_module_dependencies", []),
            "when.bazel.any_direct_module_dependencies",
            source,
        )
        any_conditions = _parse_bazel_dependency_conditions(
            bazel.get("any_direct_module_conditions", []), source
        )
        if not dependencies and not any_dependencies and not any_conditions:
            raise PolicyError(
                f"policy {source}: direct_module_dependencies or "
                "any_direct_module_dependencies or any_direct_module_conditions "
                "must not be empty"
            )
        bazel_condition = BazelCondition(dependencies, any_dependencies, any_conditions)
    file_exists_condition = None
    if "file_exists" in raw:
        file_exists = raw["file_exists"]
        if not isinstance(file_exists, str) or not file_exists.strip():
            raise PolicyError(
                f"policy {source}: when.file_exists must be a non-empty path"
            )
        file_exists_condition = FileExistsCondition(
            safe_relative_path(file_exists, source)
        )
    file_contains_condition = None
    if "file_contains" in raw:
        file_contains_condition = _parse_file_contains_condition(
            raw["file_contains"], source
        )
    file_contains_any_condition = None
    if "file_contains_any" in raw:
        file_contains_any = raw["file_contains_any"]
        if not isinstance(file_contains_any, list) or not file_contains_any:
            raise PolicyError(
                f"policy {source}: file_contains_any must be a non-empty list"
            )
        file_contains_any_condition = FileContainsAnyCondition(
            tuple(
                _parse_file_contains_condition(item, source)
                for item in file_contains_any
            )
        )
    return (
        bazel_condition,
        file_exists_condition,
        file_contains_condition,
        file_contains_any_condition,
    )


def _parse_file_contains_condition(raw: object, source: Path) -> FileContainsCondition:
    if not isinstance(raw, dict) or set(raw) != {"path", "pattern"}:
        raise PolicyError(
            f"policy {source}: file condition must contain only path and pattern"
        )
    pattern = _required_string(raw, "pattern", source)
    try:
        re.compile(pattern)
    except re.error as exc:
        raise PolicyError(
            f"policy {source}: invalid file_contains pattern: {exc}"
        ) from exc
    return FileContainsCondition(
        safe_relative_path(_required_string(raw, "path", source), source), pattern
    )


def _parse_bazel_dependency_conditions(
    raw: object, source: Path
) -> tuple[BazelDependencyCondition, ...]:
    if raw == []:
        return ()
    if not isinstance(raw, list) or not raw:
        raise PolicyError(
            f"policy {source}: when.bazel.any_direct_module_conditions must be a "
            "non-empty list"
        )
    conditions: list[BazelDependencyCondition] = []
    for index, item in enumerate(raw):
        if not isinstance(item, str) or not item.strip():
            raise PolicyError(
                f"policy {source}: when.bazel.any_direct_module_conditions[{index}] "
                "must be a non-empty condition string"
            )
        condition = parse_bazel_dependency_condition(item)
        if condition is None:
            raise PolicyError(
                f"policy {source}: when.bazel.any_direct_module_conditions[{index}] "
                "must use 'module OP major.minor.patch' syntax"
            )
        conditions.append(condition)
    return tuple(conditions)


def _parse_after_apply(raw: object, source: Path) -> tuple[AfterApplyCommand, ...]:
    if not isinstance(raw, list):
        raise PolicyError(f"policy {source}: after_apply must be a list")
    commands: list[AfterApplyCommand] = []
    for item in raw:
        if (
            not isinstance(item, dict)
            or not set(item).issubset(
                {"command", "when_file_exists", "when_path_changed", "description"}
            )
            or not {"command", "when_file_exists", "description"}.issubset(item)
        ):
            raise PolicyError(
                f"policy {source}: each after_apply item must contain command, when_file_exists, and description"
            )
        command = item["command"]
        if (
            not isinstance(command, list)
            or not command
            or not all(
                isinstance(argument, str) and argument.strip() for argument in command
            )
        ):
            raise PolicyError(
                f"policy {source}: after_apply command must be a non-empty list of strings"
            )
        when_file_exists = safe_relative_path(
            _required_string(item, "when_file_exists", source), source
        )
        when_path_changed = (
            safe_relative_path(
                _required_string(item, "when_path_changed", source), source
            )
            if "when_path_changed" in item
            else None
        )
        description = _required_string(item, "description", source)
        commands.append(
            AfterApplyCommand(
                tuple(command), when_file_exists, description, when_path_changed
            )
        )
    return tuple(commands)


def _expect_keys(value: dict[str, Any], allowed: set[str], source: Path) -> None:
    unexpected = set(value) - allowed
    if unexpected:
        raise PolicyError(
            f"policy {source}: unexpected fields: {', '.join(sorted(unexpected))}"
        )


def _required_string(value: dict[str, Any], key: str, source: Path) -> str:
    result = value.get(key)
    if not isinstance(result, str) or not result.strip():
        raise PolicyError(f"policy {source}: {key} must be a non-empty string")
    return result


def _optional_string(value: dict[str, Any], key: str, source: Path) -> str | None:
    result = value.get(key)
    if result is None:
        return None
    if not isinstance(result, str) or not result.strip():
        raise PolicyError(f"policy {source}: {key} must be a non-empty string")
    return result


def _string_list(value: object, name: str, source: Path) -> tuple[str, ...]:
    if not isinstance(value, list) or not all(
        isinstance(item, str) and item.strip() for item in value
    ):
        raise PolicyError(
            f"policy {source}: {name} must be a list of non-empty strings"
        )
    return tuple(value)
