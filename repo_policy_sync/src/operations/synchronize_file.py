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

"""The synchronize_file operation."""

import stat
from pathlib import Path
from typing import Any

from ..errors import PolicyError, RepoPolicySyncError
from ..models import Change, EnsureOperation, SynchronizeFile
from ._validation import (
    expect_keys,
    optional_string,
    required_string,
    safe_relative_path,
    validate_repository_path,
)


class SynchronizeFileOperation:
    """Copy a policy-owned file to a repository path, optionally making it executable."""

    operation_type = "synchronize_file"
    operation_class = SynchronizeFile

    def parse(self, raw: dict[str, Any], source: Path) -> SynchronizeFile:
        expect_keys(
            raw,
            {"type", "path", "source", "executable", "rationale"},
            source,
        )
        source_root = source.parent.absolute()
        source_file = source_root / safe_relative_path(
            required_string(raw, "source", source), source
        )
        try:
            validate_repository_path(source_root, source_file)
        except RepoPolicySyncError as exc:
            raise PolicyError(f"policy {source}: invalid source asset: {exc}") from exc
        if not source_file.is_file():
            raise PolicyError(
                f"policy {source}: synchronize_file source must be a file: "
                f"{source_file}"
            )
        try:
            source_file.read_bytes()
        except OSError as exc:
            raise PolicyError(
                f"policy {source}: could not read synchronize_file source "
                f"{source_file}: {exc}"
            ) from exc

        executable = raw.get("executable", False)
        if not isinstance(executable, bool):
            raise PolicyError(f"policy {source}: executable must be a boolean")

        return SynchronizeFile(
            path=safe_relative_path(required_string(raw, "path", source), source),
            source_root=source_root,
            source_file=source_file,
            executable=executable,
            rationale=optional_string(raw, "rationale", source),
        )

    def describe_changes(
        self,
        root: Path,
        operation: EnsureOperation,
        *,
        organization: str | None = None,
        github_resolver=None,
    ) -> tuple[Change, ...]:
        assert isinstance(operation, SynchronizeFile)
        path = root / operation.path
        validate_repository_path(root, path)
        _validate_target(path, operation)
        source_content = _read_source(operation)
        content_changed = not path.is_file() or path.read_bytes() != source_content
        executable_changed = (
            operation.executable and path.is_file() and not _is_executable(path)
        )
        if content_changed and executable_changed:
            description = "synchronize contents and make executable"
        elif content_changed:
            description = "add file" if not path.exists() else "synchronize contents"
        elif executable_changed:
            description = "make executable"
        else:
            return ()
        return (Change(operation.path, description, operation.rationale),)

    def apply(
        self,
        root: Path,
        operation: EnsureOperation,
        *,
        organization: str | None = None,
        github_resolver=None,
    ) -> None:
        assert isinstance(operation, SynchronizeFile)
        path = root / operation.path
        validate_repository_path(root, path)
        _validate_target(path, operation)
        source_content = _read_source(operation)
        if not path.is_file() or path.read_bytes() != source_content:
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(source_content)
        if operation.executable and not _is_executable(path):
            path.chmod(path.stat().st_mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)


def _read_source(operation: SynchronizeFile) -> bytes:
    validate_repository_path(operation.source_root, operation.source_file)
    try:
        return operation.source_file.read_bytes()
    except OSError as exc:
        raise RepoPolicySyncError(
            f"synchronize_file source must be readable: {operation.source_file}"
        ) from exc


def _validate_target(path: Path, operation: SynchronizeFile) -> None:
    if path.exists() and not path.is_file():
        raise RepoPolicySyncError(f"{operation.path} must not be a directory")


def _is_executable(path: Path) -> bool:
    return bool(path.stat().st_mode & (stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH))
