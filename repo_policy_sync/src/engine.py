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

"""Candidate selection and local policy evaluation."""

from __future__ import annotations

import os
import re
import shlex
import subprocess
import tempfile
from pathlib import Path

from .bazel import (
    mask_starlark_comments,
    matches_bazel_dependency_condition,
    parse_bazel_version,
    starlark_call_ranges,
)
from .errors import CommandError, RepoPolicySyncError, redact_sensitive_text
from .models import Change, EnsureOperation, Evaluation, Policy
from .operations import apply as apply_operation
from .operations import describe_changes, resolve_operation
from .operations._validation import validate_repository_path
from .values import resolve_values, value_source_exists

_NAME_ARGUMENT = re.compile(r"\bname\s*=\s*[\"']([^\"']+)[\"']")
_VERSION_ARGUMENT = re.compile(r"\bversion\s*=\s*[\"']([^\"']+)[\"']")
_REDUCED_ENVIRONMENT_KEYS = {
    "CI",
    "LANG",
    "PATH",
    "SHELL",
    "TERM",
    "TMPDIR",
    "TMP",
    "TEMP",
    "USER",
    "LOGNAME",
}


def evaluate_policy(
    root: Path, policy: Policy, *, organization: str | None = None
) -> Evaluation:
    """Evaluate a policy against a checked-out repository without changing it."""

    evaluation, _ = _evaluate_policy_with_operations(
        root, policy, organization=organization
    )
    return evaluation


def _evaluate_policy_with_operations(
    root: Path, policy: Policy, *, organization: str | None = None
) -> tuple[Evaluation, tuple[EnsureOperation, ...]]:
    """Evaluate a policy and retain the materialized operations for applying it."""

    if not _matches_conditions(root, policy):
        return Evaluation(applies=False, changes=()), ()
    # Resolve values only after applicability is established. The same
    # materialized operation tuple is then used for both planning and apply.
    values = resolve_values(root, policy.values)
    operations = tuple(
        resolve_operation(operation, values) for operation in policy.ensure
    )
    changes: list[Change] = []
    for operation in operations:
        changes.extend(describe_changes(root, operation, organization=organization))
    if changes:
        changes.extend(
            Change(command.when_file_exists, command.description)
            for command in policy.after_apply
            if _should_run_after_apply(
                root, command, {change.path for change in changes}
            )
        )
    return Evaluation(applies=True, changes=tuple(changes)), operations


def apply_policy(
    root: Path,
    policy: Policy,
    *,
    force_after_apply: bool = False,
    organization: str | None = None,
) -> Evaluation:
    """Apply a matching policy and return the changes that were made."""

    evaluation, operations = _evaluate_policy_with_operations(
        root, policy, organization=organization
    )
    if not evaluation.applies:
        return evaluation
    for operation in operations:
        apply_operation(root, operation, organization=organization)
    if evaluation.changes or force_after_apply:
        changed_paths = {change.path for change in evaluation.changes}
        for command in policy.after_apply:
            if _should_run_after_apply(
                root, command, changed_paths, force=force_after_apply
            ):
                _run_after_apply_command(root, command.command)
    if not force_after_apply:
        return evaluation
    changes = list(evaluation.changes)
    existing_paths = {change.path for change in changes}
    changes.extend(
        Change(command.when_file_exists, command.description)
        for command in policy.after_apply
        if _should_run_after_apply(root, command, set(), force=True)
        and command.when_file_exists not in existing_paths
    )
    return Evaluation(evaluation.applies, tuple(changes))


def _run_after_apply_command(root: Path, command: tuple[str, ...]) -> None:
    # Policy commands execute repository-controlled code. Keep only the basic
    # process environment and replace user configuration with temporary paths so
    # credentials and host-specific settings are not inherited accidentally.
    environment = {
        key: value
        for key, value in os.environ.items()
        if key in _REDUCED_ENVIRONMENT_KEYS or key.startswith("LC_")
    }
    try:
        with tempfile.TemporaryDirectory(
            prefix="repo-policy-sync-after-apply-"
        ) as home:
            environment.update(
                {
                    "HOME": home,
                    "XDG_CONFIG_HOME": str(Path(home) / ".config"),
                    "GH_CONFIG_DIR": str(Path(home) / ".gh"),
                    "GIT_CONFIG_NOSYSTEM": "1",
                    "GIT_TERMINAL_PROMPT": "0",
                }
            )
            subprocess.run(
                command,
                cwd=root,
                check=True,
                capture_output=True,
                text=True,
                env=environment,
            )
    except FileNotFoundError as exc:
        raise CommandError(f"required command is unavailable: {command[0]}") from exc
    except subprocess.CalledProcessError as exc:
        detail = (
            (exc.stderr or "").strip() or (exc.stdout or "").strip() or "command failed"
        )
        raise CommandError(
            f"{redact_sensitive_text(shlex.join(command))}: "
            f"{redact_sensitive_text(detail)} (exit status {exc.returncode})"
        ) from exc


def _should_run_after_apply(
    root: Path, command, changed_paths: set[Path], *, force: bool = False
) -> bool:
    path = root / command.when_file_exists
    validate_repository_path(root, path)
    return path.is_file() and (
        force
        or command.when_path_changed is None
        or command.when_path_changed in changed_paths
    )


def _matches_conditions(root: Path, policy: Policy) -> bool:
    return (
        _matches_bazel_condition(root, policy)
        and _matches_file_exists_condition(root, policy)
        and _matches_file_contains_condition(root, policy)
        and _matches_file_contains_any_condition(root, policy)
        and _matches_value_exists_condition(root, policy)
    )


def _matches_bazel_condition(root: Path, policy: Policy) -> bool:
    condition = policy.bazel_condition
    if condition is None:
        return True
    module_file = root / "MODULE.bazel"
    validate_repository_path(root, module_file)
    if not module_file.is_file():
        return False
    text = module_file.read_text(encoding="utf-8")
    dependencies: dict[str, tuple[int, int, int] | None] = {}
    for start, end in starlark_call_ranges(text, "bazel_dep"):
        body = mask_starlark_comments(text[start:end])
        name_match = _NAME_ARGUMENT.search(body)
        if name_match is None:
            continue
        version_match = _VERSION_ARGUMENT.search(body)
        dependencies[name_match.group(1)] = (
            parse_bazel_version(version_match.group(1)) if version_match else None
        )
    condition_names = {
        dependency_condition.module_name
        for dependency_condition in condition.any_direct_module_conditions
    }
    invalid_versions = sorted(
        name
        for name in condition_names
        if name in dependencies and dependencies[name] is None
    )
    if invalid_versions:
        # A configured version condition cannot be evaluated meaningfully for a
        # missing or non-numeric version; fail loudly instead of silently
        # treating a malformed dependency as a non-match.
        names = ", ".join(repr(name) for name in invalid_versions)
        raise RepoPolicySyncError(
            f"MODULE.bazel configured bazel_dep versions must be numeric major.minor.patch: {names}"
        )
    # A policy can require a complete set and also accept one of several names.
    dependency_names = set(dependencies)
    if not set(condition.direct_module_dependencies).issubset(dependency_names):
        return False
    if (
        condition.any_direct_module_dependencies
        and not set(condition.any_direct_module_dependencies) & dependency_names
    ):
        return False
    if not condition.any_direct_module_conditions:
        return True

    return any(
        (version := dependencies.get(dependency_condition.module_name)) is not None
        and matches_bazel_dependency_condition(version, dependency_condition)
        for dependency_condition in condition.any_direct_module_conditions
    )


def _matches_file_exists_condition(root: Path, policy: Policy) -> bool:
    condition = policy.file_exists_condition
    if condition is None:
        return True
    path = root / condition.path
    validate_repository_path(root, path)
    return path.is_file()


def _matches_file_contains_condition(root: Path, policy: Policy) -> bool:
    condition = policy.file_contains_condition
    if condition is None:
        return True
    return any(
        re.search(condition.pattern, path.read_text(encoding="utf-8")) is not None
        for path in _condition_paths(root, condition.path)
    )


def _matches_file_contains_any_condition(root: Path, policy: Policy) -> bool:
    condition = policy.file_contains_any_condition
    if condition is None:
        return True
    return any(
        re.search(item.pattern, path.read_text(encoding="utf-8")) is not None
        for item in condition.conditions
        for path in _condition_paths(root, item.path)
    )


def _matches_value_exists_condition(root: Path, policy: Policy) -> bool:
    condition = policy.value_exists_condition
    if condition is None:
        return True
    binding = next(
        (binding for binding in policy.values if binding.name == condition.name),
        None,
    )
    if binding is None:
        raise RepoPolicySyncError(f"unknown policy value reference: {condition.name!r}")
    return value_source_exists(root, binding)


def _condition_paths(root: Path, path: Path) -> tuple[Path, ...]:
    """Return matching repository files for a literal path or a relative glob."""

    # Literal paths are common, so avoid glob expansion and keep their behavior simple.
    if not any(character in str(path) for character in "*?["):
        candidate = root / path
        validate_repository_path(root, candidate)
        return (candidate,) if candidate.is_file() else ()
    # Glob conditions are used for files such as BUILD files at any directory depth.
    candidates: list[Path] = []
    for candidate in sorted(root.glob(str(path))):
        relative = candidate.relative_to(root)
        # Git metadata is not part of the repository content being evaluated.
        if ".git" in relative.parts:
            continue
        validate_repository_path(root, candidate)
        if candidate.is_file():
            candidates.append(candidate)
    return tuple(candidates)
