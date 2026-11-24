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

"""The ensure_minimum_version operation."""

from pathlib import Path
from typing import Any

from ..bazel import BazelVersion, parse_bazel_version
from ..errors import PolicyError, RepoPolicySyncError
from ..github import GitHubResolver
from ..models import Change, EnsureMinimumVersion, EnsureOperation
from ._validation import (
    expect_keys,
    optional_string,
    required_string,
    safe_relative_path,
    validate_repository_path,
)


class EnsureMinimumVersionOperation:
    operation_type: str = "ensure_minimum_version"
    operation_class = EnsureMinimumVersion

    def parse(self, raw: dict[str, Any], source: Path) -> EnsureMinimumVersion:
        expect_keys(raw, {"type", "path", "minimum_version", "rationale"}, source)
        minimum_version = required_string(raw, "minimum_version", source)
        if parse_bazel_version(minimum_version) is None:
            raise PolicyError(
                f"policy {source}: minimum_version must be a numeric major.minor.patch version"
            )
        return EnsureMinimumVersion(
            safe_relative_path(required_string(raw, "path", source), source),
            minimum_version,
            optional_string(raw, "rationale", source),
        )

    def describe_changes(
        self,
        root: Path,
        operation: EnsureOperation,
        *,
        organization: str | None = None,
        github_resolver: GitHubResolver | None = None,
    ) -> tuple[Change, ...]:
        assert isinstance(operation, EnsureMinimumVersion)
        path = root / operation.path
        validate_repository_path(root, path)
        current_version = _read_version(path, operation)
        if current_version is None or current_version >= _required_version(operation):
            return ()
        return (
            Change(
                operation.path,
                f"upgrade from {path.read_text(encoding='utf-8').strip()!r} to {operation.minimum_version!r}",
                operation.rationale,
            ),
        )

    def apply(
        self,
        root: Path,
        operation: EnsureOperation,
        *,
        organization: str | None = None,
        github_resolver: GitHubResolver | None = None,
    ) -> None:
        assert isinstance(operation, EnsureMinimumVersion)
        path = root / operation.path
        validate_repository_path(root, path)
        current_version = _read_version(path, operation)
        if current_version is None or current_version >= _required_version(operation):
            return
        path.write_text(f"{operation.minimum_version}\n", encoding="utf-8")


def _read_version(path: Path, operation: EnsureMinimumVersion) -> BazelVersion | None:
    if path.exists() and not path.is_file():
        raise RepoPolicySyncError(f"{operation.path} must be a file")
    if not path.is_file():
        return None
    version = path.read_text(encoding="utf-8").strip()
    parsed = parse_bazel_version(version)
    if parsed is None:
        raise RepoPolicySyncError(
            f"{operation.path} must contain a numeric major.minor.patch version, found {version!r}"
        )
    return parsed


def _required_version(operation: EnsureMinimumVersion) -> BazelVersion:
    parsed = parse_bazel_version(operation.minimum_version)
    assert parsed is not None
    return parsed
