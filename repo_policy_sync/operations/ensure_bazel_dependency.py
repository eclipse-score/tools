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

"""Ensure a SCORE devcontainer has a direct bzlmod dependency."""

from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from ..errors import RepoPolicySyncError
from ..models import Change, EnsureBazelDependency, EnsureOperation
from ._validation import (
    expect_keys,
    optional_string,
    required_string,
    safe_relative_path,
)

_NUMERIC_VERSION = re.compile(r"(0|[1-9][0-9]*)\.(0|[1-9][0-9]*)\.(0|[1-9][0-9]*)\Z")
_BAZEL_DEP_CALL = re.compile(r"bazel_dep\s*\((.*?)\)", re.DOTALL)
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
            {"type", "dockerfile", "module_file", "image", "module_name", "rationale"},
            source,
        )
        return EnsureBazelDependency(
            dockerfile=safe_relative_path(
                required_string(raw, "dockerfile", source), source
            ),
            module_file=safe_relative_path(
                required_string(raw, "module_file", source), source
            ),
            image=required_string(raw, "image", source),
            module_name=required_string(raw, "module_name", source),
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
        version = _docker_version(root, operation)
        dependency = _module_dependency(root, operation)
        if dependency is not None:
            return ()
        return (
            Change(
                operation.module_file,
                f"add Bazel dependency {operation.module_name!r} at version {version!r}",
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
        version = _docker_version(root, operation)
        if _module_dependency(root, operation) is not None:
            return
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


def _docker_version(root: Path, operation: EnsureBazelDependency) -> str:
    path = root / operation.dockerfile
    if not path.is_file():
        raise RepoPolicySyncError(f"{operation.dockerfile} must exist")
    text = path.read_text(encoding="utf-8")
    matches = list(
        re.finditer(
            rf"(?m)^\s*FROM\s+{re.escape(operation.image)}:(?P<tag>[^\s#]+)[^\r\n]*$",
            text,
        )
    )
    if len(matches) != 1:
        raise RepoPolicySyncError(
            f"{operation.dockerfile} must contain exactly one FROM {operation.image}:... instruction"
        )
    tag = matches[0].group("tag")
    if not tag.startswith("v") or _parse_version(tag[1:]) is None:
        raise RepoPolicySyncError(
            f"{operation.dockerfile} must use {operation.image}:vX.Y.Z, found {tag!r}"
        )
    return tag[1:]


def _module_dependency(
    root: Path, operation: EnsureBazelDependency
) -> _Dependency | None:
    path = root / operation.module_file
    if not path.is_file():
        raise RepoPolicySyncError(f"{operation.module_file} must exist")
    text = path.read_text(encoding="utf-8")
    calls = []
    for match in _BAZEL_DEP_CALL.finditer(text):
        name_matches = list(_NAME_ARGUMENT.finditer(match.group(1)))
        if any(
            name_match.group(1) == operation.module_name for name_match in name_matches
        ):
            if len(name_matches) != 1:
                raise RepoPolicySyncError(
                    f"{operation.module_file} bazel_dep for {operation.module_name!r} "
                    "must declare name exactly once"
                )
            calls.append(match)
    if len(calls) > 1:
        raise RepoPolicySyncError(
            f"{operation.module_file} must contain at most one bazel_dep for {operation.module_name!r}"
        )
    if not calls:
        return None
    version_matches = list(_VERSION_ARGUMENT.finditer(calls[0].group(1)))
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
