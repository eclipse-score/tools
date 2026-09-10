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

"""Ensure the ``dev_dependency`` setting of a direct bzlmod dependency."""

from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from ..bazel import mask_starlark_comments, starlark_call_ranges
from ..errors import PolicyError, RepoPolicySyncError
from ..models import (
    Change,
    EnsureBazelDependencyDevDependency,
    EnsureOperation,
)
from ._validation import (
    expect_keys,
    optional_string,
    required_string,
    validate_repository_path,
)

# bzlmod dependency declarations belong to the repository-root MODULE.bazel.
# Keep this path internal to the operation so every policy can focus on the
# dependency whose setting it governs instead of repeating an invariant path.
_MODULE_FILE = Path("MODULE.bazel")
_MODULE_NAME = re.compile(r"[A-Za-z0-9_][A-Za-z0-9_.-]*\Z")
_NAME_ARGUMENT = re.compile(r"\bname\s*=\s*[\"']([^\"']+)[\"']")
_DEV_DEPENDENCY_ARGUMENT = re.compile(r"\bdev_dependency\s*=\s*(True|False)\b")


@dataclass(frozen=True)
class _DependencyCall:
    body_start: int
    body_end: int
    dev_dependency: re.Match[str] | None


class EnsureBazelDependencyDevDependencyOperation:
    """Ensure one direct dependency is or is not development-only."""

    operation_type = "ensure_bazel_dependency_dev_dependency"
    operation_class = EnsureBazelDependencyDevDependency

    def parse(
        self, raw: dict[str, Any], source: Path
    ) -> EnsureBazelDependencyDevDependency:
        expect_keys(
            raw,
            {"type", "module_name", "dev_dependency", "rationale"},
            source,
        )
        module_name = required_string(raw, "module_name", source)
        if _MODULE_NAME.fullmatch(module_name) is None:
            raise PolicyError(
                f"policy {source}: module_name must be a valid Bazel module name"
            )
        dev_dependency = raw.get("dev_dependency")
        if not isinstance(dev_dependency, bool):
            raise PolicyError(f"policy {source}: dev_dependency must be a boolean")
        return EnsureBazelDependencyDevDependency(
            module_name=module_name,
            dev_dependency=dev_dependency,
            rationale=optional_string(raw, "rationale", source),
        )

    def describe_changes(
        self,
        root: Path,
        operation: EnsureOperation,
        *,
        organization: str | None = None,
    ) -> tuple[Change, ...]:
        assert isinstance(operation, EnsureBazelDependencyDevDependency)
        _, dependency = _find_dependency(root, operation)
        if dependency is None or _is_compliant(dependency, operation.dev_dependency):
            return ()
        if operation.dev_dependency:
            description = (
                f"set Bazel dependency {operation.module_name!r} dev_dependency to true"
            )
        else:
            description = (
                f"remove dev_dependency from Bazel dependency {operation.module_name!r}"
            )
        return (Change(_MODULE_FILE, description, operation.rationale),)

    def apply(
        self,
        root: Path,
        operation: EnsureOperation,
        *,
        organization: str | None = None,
    ) -> None:
        assert isinstance(operation, EnsureBazelDependencyDevDependency)
        path = root / _MODULE_FILE
        text, dependency = _find_dependency(root, operation)
        if dependency is None or _is_compliant(dependency, operation.dev_dependency):
            return
        if operation.dev_dependency:
            updated = _set_dev_dependency(text, dependency)
        else:
            updated = _remove_dev_dependency(text, dependency)
        path.write_text(updated, encoding="utf-8")


def _find_dependency(
    root: Path, operation: EnsureBazelDependencyDevDependency
) -> tuple[str, _DependencyCall | None]:
    path = root / _MODULE_FILE
    validate_repository_path(root, path)
    if not path.is_file():
        raise RepoPolicySyncError(f"{_MODULE_FILE} must exist")
    text = path.read_text(encoding="utf-8")
    calls: list[_DependencyCall] = []
    for start, end in starlark_call_ranges(text, "bazel_dep"):
        body = mask_starlark_comments(text[start:end])
        name_matches = [
            match
            for match in _NAME_ARGUMENT.finditer(body)
            if match.group(1) == operation.module_name
        ]
        if not name_matches:
            continue
        if len(name_matches) != 1:
            raise RepoPolicySyncError(
                f"{_MODULE_FILE} bazel_dep for {operation.module_name!r} "
                "must declare name exactly once"
            )
        dev_matches = list(_DEV_DEPENDENCY_ARGUMENT.finditer(body))
        if len(dev_matches) > 1:
            raise RepoPolicySyncError(
                f"{_MODULE_FILE} bazel_dep for {operation.module_name!r} "
                "must declare dev_dependency at most once"
            )
        calls.append(
            _DependencyCall(
                body_start=start,
                body_end=end,
                dev_dependency=dev_matches[0] if dev_matches else None,
            )
        )
    if len(calls) > 1:
        raise RepoPolicySyncError(
            f"{_MODULE_FILE} must contain at most one bazel_dep for "
            f"{operation.module_name!r}"
        )
    if not calls:
        # A policy can list several optional dependencies in one condition.
        # The condition selects repositories containing at least one target;
        # each operation is then a no-op for the other absent targets.
        return text, None
    return text, calls[0]


def _is_compliant(dependency: _DependencyCall, desired: bool) -> bool:
    if dependency.dev_dependency is None:
        return not desired
    return desired and dependency.dev_dependency.group(1) == "True"


def _set_dev_dependency(text: str, dependency: _DependencyCall) -> str:
    match = dependency.dev_dependency
    if match is not None:
        value_start = dependency.body_start + match.start(1)
        value_end = dependency.body_start + match.end(1)
        return text[:value_start] + "True" + text[value_end:]

    body = text[dependency.body_start : dependency.body_end]
    if "\n" not in body and "\r" not in body:
        content = body.rstrip(" \t")
        separator = "" if content.endswith(",") else ","
        insertion = f"{separator} dev_dependency = True"
        return (
            text[: dependency.body_start]
            + content
            + insertion
            + body[len(content) :]
            + text[dependency.body_end :]
        )

    content = body.rstrip(" \t\r\n")
    trailing = body[len(content) :]
    newline = "\r\n" if "\r\n" in trailing else "\n"
    close_indent = trailing.rsplit("\n", 1)[-1] if "\n" in trailing else ""
    argument_indent = _argument_indent(body)
    separator = "" if content.endswith(",") else ","
    insertion = (
        f"{separator}{newline}{argument_indent}dev_dependency = True,"
        f"{newline}{close_indent}"
    )
    return (
        text[: dependency.body_start]
        + content
        + insertion
        + text[dependency.body_end :]
    )


def _remove_dev_dependency(text: str, dependency: _DependencyCall) -> str:
    match = dependency.dev_dependency
    assert match is not None
    argument_start = dependency.body_start + match.start()
    argument_end = dependency.body_start + match.end()
    after = text[argument_end : dependency.body_end]
    trailing_match = re.match(r"[ \t]*(?:,[ \t]*(?:\r?\n[ \t]*)?)?", after)
    if trailing_match is not None and "," in trailing_match.group(0):
        suffix_start = argument_end + trailing_match.end()
        prefix = text[:argument_start]
        # If the target is the final multiline argument, remove its whole line
        # but retain the newline after its comma for the closing parenthesis.
        if (
            not after[trailing_match.end() :].strip()
            and "\n" in prefix[dependency.body_start :]
        ):
            line_start = text.rfind("\n", dependency.body_start, argument_start)
            if line_start > dependency.body_start and text[line_start - 1] == "\r":
                line_start -= 1
            comma_match = re.match(r"[ \t]*,", after)
            assert comma_match is not None
            suffix_start = argument_end + comma_match.end()
            return text[:line_start] + text[suffix_start:]
        # For an inline final argument, remove its preceding separator too so
        # the call does not retain a dangling comma before the closing parenthesis.
        elif (
            not after[trailing_match.end() :].strip()
            and "\n" not in text[dependency.body_start : argument_start]
        ):
            preceding = re.search(r",[ \t]*$", prefix[dependency.body_start :])
            if preceding is not None:
                argument_start = dependency.body_start + preceding.start()
        return text[:argument_start] + text[suffix_start:]

    before = text[dependency.body_start : argument_start]
    preceding = re.search(r",[ \t]*(?:\r?\n[ \t]*)?$", before)
    if preceding is None:
        raise RepoPolicySyncError(
            "dev_dependency argument must be separated from another argument"
        )
    separator_start = dependency.body_start + preceding.start()
    return text[:separator_start] + text[argument_end:]


def _argument_indent(body: str) -> str:
    match = re.search(r"(?m)^([ \t]+)\S", body)
    return match.group(1) if match is not None else "    "
