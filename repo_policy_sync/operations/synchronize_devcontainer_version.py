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

"""Synchronize SCORE devcontainer versions across Docker and Bazel files."""

from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from ..errors import RepoPolicySyncError
from ..models import Change, EnsureOperation, SynchronizeDevcontainerVersion
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
class _VersionLocation:
    path: Path
    text: str
    start: int
    end: int
    version: tuple[int, int, int]


class SynchronizeDevcontainerVersionOperation:
    operation_type = "synchronize_devcontainer_version"
    operation_class = SynchronizeDevcontainerVersion

    def parse(
        self, raw: dict[str, Any], source: Path
    ) -> SynchronizeDevcontainerVersion:
        expect_keys(
            raw,
            {"type", "dockerfile", "module_file", "image", "module_name", "rationale"},
            source,
        )
        return SynchronizeDevcontainerVersion(
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
        assert isinstance(operation, SynchronizeDevcontainerVersion)
        docker, module = _locations(root, operation)
        if docker.version == module.version:
            return ()
        target, source = (
            (module, docker) if docker.version > module.version else (docker, module)
        )
        return (
            Change(
                target.path,
                f"align version from {_version_text(target.version)!r} to {_version_text(source.version)!r}",
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
        assert isinstance(operation, SynchronizeDevcontainerVersion)
        docker, module = _locations(root, operation)
        if docker.version == module.version:
            return
        target, source = (
            (module, docker) if docker.version > module.version else (docker, module)
        )
        replacement = _version_text(source.version)
        if target.path == operation.dockerfile:
            replacement = f"v{replacement}"
        target_file = root / target.path
        target_file.write_text(
            target.text[: target.start] + replacement + target.text[target.end :],
            encoding="utf-8",
        )


def _locations(
    root: Path, operation: SynchronizeDevcontainerVersion
) -> tuple[_VersionLocation, _VersionLocation]:
    docker = _docker_location(root, operation)
    module = _module_location(root, operation)
    return docker, module


def _docker_location(
    root: Path, operation: SynchronizeDevcontainerVersion
) -> _VersionLocation:
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
    if not tag.startswith("v") or (version := _parse_version(tag[1:])) is None:
        raise RepoPolicySyncError(
            f"{operation.dockerfile} must use {operation.image}:vX.Y.Z, found {tag!r}"
        )
    start, end = matches[0].span("tag")
    return _VersionLocation(operation.dockerfile, text, start, end, version)


def _module_location(
    root: Path, operation: SynchronizeDevcontainerVersion
) -> _VersionLocation:
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
    if len(calls) != 1:
        raise RepoPolicySyncError(
            f"{operation.module_file} must contain exactly one bazel_dep for {operation.module_name!r}"
        )
    version_matches = list(_VERSION_ARGUMENT.finditer(calls[0].group(1)))
    if not version_matches:
        raise RepoPolicySyncError(
            f'{operation.module_file} bazel_dep for {operation.module_name!r} must declare version = "X.Y.Z"'
        )
    if len(version_matches) != 1:
        raise RepoPolicySyncError(
            f"{operation.module_file} bazel_dep for {operation.module_name!r} must declare version exactly once"
        )
    version_match = version_matches[0]
    version_text = version_match.group(2)
    version = _parse_version(version_text)
    if version is None:
        raise RepoPolicySyncError(
            f"{operation.module_file} bazel_dep for {operation.module_name!r} must use X.Y.Z, found {version_text!r}"
        )
    start = calls[0].start(1) + version_match.start(2)
    end = calls[0].start(1) + version_match.end(2)
    return _VersionLocation(operation.module_file, text, start, end, version)


def _parse_version(value: str) -> tuple[int, int, int] | None:
    match = _NUMERIC_VERSION.fullmatch(value)
    return tuple(int(component) for component in match.groups()) if match else None


def _version_text(version: tuple[int, int, int]) -> str:
    return ".".join(str(component) for component in version)
