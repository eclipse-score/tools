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

"""Shared YAML validation helpers for built-in operations."""

from pathlib import Path
from typing import Any

from ..errors import PolicyError, RepoPolicySyncError


def expect_keys(value: dict[str, Any], allowed: set[str], source: Path) -> None:
    unexpected = set(value) - allowed
    if unexpected:
        raise PolicyError(
            f"policy {source}: unexpected fields: {', '.join(sorted(unexpected))}"
        )


def safe_relative_path(raw: str, source: Path) -> Path:
    path = Path(raw)
    if path.is_absolute() or ".." in path.parts or raw in {"", "."}:
        raise PolicyError(
            f"policy {source}: path must be a non-empty repository-relative path"
        )
    return path


def validate_repository_path(
    root: Path, path: Path, *, allow_final_symlink: bool = False
) -> None:
    """Reject repository paths that escape through symlinks before I/O.

    Every component is checked before a caller reads or writes the path. A
    final symlink can be allowed for operations that remove the link itself;
    its parent is then used for containment checking so the link is never
    followed as part of validation.
    """

    root_absolute = root.absolute()
    path_absolute = path.absolute()
    try:
        relative = path_absolute.relative_to(root_absolute)
    except ValueError as exc:
        raise RepoPolicySyncError(
            f"repository path is outside checkout: {path}"
        ) from exc

    # Check components individually so an intermediate symlink cannot redirect
    # a seemingly repository-relative path before the final containment check.
    current = root_absolute
    for index, part in enumerate(relative.parts):
        current /= part
        if current.is_symlink() and not (
            allow_final_symlink and index == len(relative.parts) - 1
        ):
            raise RepoPolicySyncError(
                f"repository path must not contain a symbolic link: {relative}"
            )

    containment_path = (
        path_absolute.parent
        if allow_final_symlink and path_absolute.is_symlink()
        else path_absolute
    )
    try:
        containment_path.resolve(strict=False).relative_to(
            root_absolute.resolve(strict=False)
        )
    except (OSError, ValueError) as exc:
        raise RepoPolicySyncError(
            f"repository path resolves outside checkout: {relative}"
        ) from exc


def required_string(value: dict[str, Any], key: str, source: Path) -> str:
    result = value.get(key)
    if not isinstance(result, str) or not result.strip():
        raise PolicyError(f"policy {source}: {key} must be a non-empty string")
    return result


def optional_string(value: dict[str, Any], key: str, source: Path) -> str | None:
    result = value.get(key)
    if result is None:
        return None
    if not isinstance(result, str) or not result.strip():
        raise PolicyError(f"policy {source}: {key} must be a non-empty string")
    return result


def string_list(value: object, name: str, source: Path) -> tuple[str, ...]:
    if not isinstance(value, list) or not all(
        isinstance(item, str) and item for item in value
    ):
        raise PolicyError(
            f"policy {source}: {name} must be a list of non-empty strings"
        )
    return tuple(value)
