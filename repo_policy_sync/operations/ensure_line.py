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

"""The ensure_line operation."""

from fnmatch import fnmatchcase
from pathlib import Path
from typing import Any

from ..errors import RepoPolicySyncError
from ..models import Change, EnsureLine, EnsureOperation
from ._validation import (
    expect_keys,
    optional_string,
    required_string,
    safe_relative_path,
    string_list,
    validate_repository_path,
)


class EnsureLineOperation:
    operation_type = "ensure_line"
    operation_class = EnsureLine

    def parse(self, raw: dict[str, Any], source: Path) -> EnsureLine:
        expect_keys(
            raw,
            {
                "type",
                "path",
                "line",
                "replace_lines",
                "replace_line_globs",
                "rationale",
            },
            source,
        )
        return EnsureLine(
            path=safe_relative_path(required_string(raw, "path", source), source),
            line=required_string(raw, "line", source),
            replace_lines=string_list(
                raw.get("replace_lines", []), "replace_lines", source
            ),
            replace_line_globs=string_list(
                raw.get("replace_line_globs", []), "replace_line_globs", source
            ),
            rationale=optional_string(raw, "rationale", source),
        )

    def describe_changes(
        self,
        root: Path,
        operation: EnsureOperation,
        *,
        organization: str | None = None,
    ) -> tuple[Change, ...]:
        assert isinstance(operation, EnsureLine)
        path = root / operation.path
        validate_repository_path(root, path)
        _validate_target(path, operation)
        lines = _read_lines(path)
        normalized = _normalized_lines(lines, operation)
        if normalized == lines:
            return ()
        desired_count = sum(line == operation.line for line in lines)
        obsolete = [
            line
            for line in lines
            if line != operation.line and _matches_replacement(line, operation)
        ]
        if obsolete:
            description = f"replace {', '.join(repr(line) for line in dict.fromkeys(obsolete))} with {operation.line!r}"
        elif desired_count == 0:
            description = f"add {operation.line!r}"
        elif desired_count > 1:
            description = f"remove duplicate {operation.line!r} entries"
        else:
            description = f"normalize {operation.line!r}"
        return (Change(operation.path, description, operation.rationale),)

    def apply(
        self,
        root: Path,
        operation: EnsureOperation,
        *,
        organization: str | None = None,
    ) -> None:
        assert isinstance(operation, EnsureLine)
        path = root / operation.path
        validate_repository_path(root, path)
        _validate_target(path, operation)
        normalized = _normalized_lines(_read_lines(path), operation)
        if normalized == _read_lines(path):
            return
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("\n".join(normalized) + "\n", encoding="utf-8")


def _normalized_lines(lines: list[str], operation: EnsureLine) -> list[str]:
    indexes = [
        index
        for index, line in enumerate(lines)
        if line == operation.line or _matches_replacement(line, operation)
    ]
    if not indexes:
        return [*lines, operation.line]
    normalized = [
        line
        for line in lines
        if line != operation.line and not _matches_replacement(line, operation)
    ]
    normalized.insert(indexes[0], operation.line)
    return normalized


def _matches_replacement(line: str, operation: EnsureLine) -> bool:
    return line in operation.replace_lines or any(
        fnmatchcase(line, pattern) for pattern in operation.replace_line_globs
    )


def _read_lines(path: Path) -> list[str]:
    return path.read_text(encoding="utf-8").splitlines() if path.exists() else []


def _validate_target(path: Path, operation: EnsureLine) -> None:
    if path.exists() and not path.is_file():
        raise RepoPolicySyncError(f"{operation.path} must be a file")
