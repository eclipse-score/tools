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

"""Ensure a direct bzlmod dependency exists at a configured version."""

from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from ..bazel import mask_starlark_comments, starlark_call_ranges
from ..errors import PolicyError, RepoPolicySyncError
from ..models import Change, EnsureBazelDependency, EnsureOperation, ValueReference
from ._validation import (
    expect_keys,
    optional_string,
    required_string,
    safe_relative_path,
    validate_repository_path,
)

_NUMERIC_VERSION = re.compile(r"(0|[1-9][0-9]*)\.(0|[1-9][0-9]*)\.(0|[1-9][0-9]*)\Z")
_MODULE_NAME = re.compile(r"[A-Za-z0-9_][A-Za-z0-9_.-]*\Z")
_NAME_ARGUMENT = re.compile(r"\bname\s*=\s*[\"']([^\"']+)[\"']")
_VERSION_ARGUMENT = re.compile(r"\bversion\s*=\s*([\"'])([^\"']*)\1")


@dataclass(frozen=True)
class _Dependency:
    version: str


class EnsureBazelDependencyOperation:
    operation_type = "ensure_bazel_dependency"
    operation_class = EnsureBazelDependency

    def parse(self, raw: dict[str, Any], source: Path) -> EnsureBazelDependency:
        expect_keys(
            raw,
            {"type", "module_file", "module_name", "version", "rationale"},
            source,
        )
        module_name = required_string(raw, "module_name", source)
        if _MODULE_NAME.fullmatch(module_name) is None:
            raise PolicyError(
                f"policy {source}: module_name must be a valid Bazel module name"
            )
        return EnsureBazelDependency(
            module_file=safe_relative_path(
                required_string(raw, "module_file", source), source
            ),
            module_name=module_name,
            # A literal is validated here; a reference is materialized by the
            # engine before this handler describes or applies the operation.
            version=_parse_version_value(raw.get("version"), source),
            rationale=optional_string(raw, "rationale", source),
        )

    def describe_changes(
        self,
        root: Path,
        operation: EnsureOperation,
        *,
        organization: str | None = None,
    ) -> tuple[Change, ...]:
        assert isinstance(operation, EnsureBazelDependency)
        dependency = _module_dependency(root, operation)
        if dependency is not None:
            return ()
        return (
            Change(
                operation.module_file,
                f"add Bazel dependency {operation.module_name!r} at version "
                f"{_resolved_version(operation)!r}",
                operation.rationale,
            ),
        )

    def apply(
        self,
        root: Path,
        operation: EnsureOperation,
        *,
        organization: str | None = None,
    ) -> None:
        assert isinstance(operation, EnsureBazelDependency)
        if _module_dependency(root, operation) is not None:
            return
        version = _resolved_version(operation)
        path = root / operation.module_file
        text = path.read_text(encoding="utf-8")
        separator = "" if text.endswith("\n") else "\n"
        blank_line = "" if text.endswith("\n\n") else "\n"
        dependency = (
            f"{separator}{blank_line}bazel_dep(\n"
            f'    name = "{operation.module_name}",\n'
            f'    version = "{version}",\n'
            ")\n"
        )
        path.write_text(text + dependency, encoding="utf-8")


def _parse_version_value(value: object, source: Path) -> str | ValueReference:
    if isinstance(value, str) and _parse_version(value) is not None:
        return value
    if (
        isinstance(value, dict)
        and set(value) == {"ref"}
        and isinstance(value["ref"], str)
        and value["ref"].strip()
    ):
        return ValueReference(value["ref"])
    raise PolicyError(
        f"policy {source}: version must be a numeric major.minor.patch version "
        "or a value reference"
    )


def _resolved_version(operation: EnsureBazelDependency) -> str:
    if not isinstance(operation.version, str):
        raise RepoPolicySyncError(
            "ensure_bazel_dependency value references must be resolved before use"
        )
    return operation.version


def _module_dependency(
    root: Path, operation: EnsureBazelDependency
) -> _Dependency | None:
    path = root / operation.module_file
    validate_repository_path(root, path)
    if not path.is_file():
        raise RepoPolicySyncError(f"{operation.module_file} must exist")
    text = path.read_text(encoding="utf-8")
    calls = []
    for start, end in starlark_call_ranges(text, "bazel_dep"):
        # A commented dependency is documentation, not an installed direct
        # dependency, so only ranges returned from active source are examined.
        body = mask_starlark_comments(text[start:end])
        name_matches = list(_NAME_ARGUMENT.finditer(body))
        if any(
            name_match.group(1) == operation.module_name for name_match in name_matches
        ):
            if len(name_matches) != 1:
                raise RepoPolicySyncError(
                    f"{operation.module_file} bazel_dep for {operation.module_name!r} "
                    "must declare name exactly once"
                )
            calls.append((start, end))
    if len(calls) > 1:
        raise RepoPolicySyncError(
            f"{operation.module_file} must contain at most one bazel_dep for {operation.module_name!r}"
        )
    if not calls:
        return None
    version_matches = list(
        _VERSION_ARGUMENT.finditer(
            mask_starlark_comments(text[calls[0][0] : calls[0][1]])
        )
    )
    if not version_matches:
        raise RepoPolicySyncError(
            f'{operation.module_file} bazel_dep for {operation.module_name!r} must declare version = "X.Y.Z"'
        )
    if len(version_matches) != 1:
        raise RepoPolicySyncError(
            f"{operation.module_file} bazel_dep for {operation.module_name!r} must declare version exactly once"
        )
    version = version_matches[0].group(2)
    if _parse_version(version) is None:
        raise RepoPolicySyncError(
            f"{operation.module_file} bazel_dep for {operation.module_name!r} must use X.Y.Z, found {version!r}"
        )
    return _Dependency(version)


def _parse_version(value: str) -> tuple[int, int, int] | None:
    match = _NUMERIC_VERSION.fullmatch(value)
    return tuple(int(component) for component in match.groups()) if match else None
