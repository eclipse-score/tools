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

"""The sync_managed_block operation."""

from fnmatch import fnmatchcase
from pathlib import Path
from typing import Any

from ..errors import PolicyError, RepoPolicySyncError
from ..models import Change, EnsureOperation, SyncManagedBlock
from ._validation import (
    expect_keys,
    optional_string,
    required_string,
    safe_relative_path,
    string_list,
    validate_repository_path,
)


class SyncManagedBlockOperation:
    operation_type = "sync_managed_block"
    operation_class = SyncManagedBlock

    def parse(self, raw: dict[str, Any], source: Path) -> SyncManagedBlock:
        expect_keys(
            raw,
            {
                "type",
                "path",
                "source",
                "marker",
                "replace_lines",
                "replace_line_globs",
                "rationale",
            },
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
                f"policy {source}: source asset must be a file: {source_file}"
            )
        try:
            source_text = source_file.read_bytes().decode("utf-8")
        except (OSError, UnicodeError) as exc:
            raise PolicyError(
                f"policy {source}: source asset must be readable UTF-8 text: "
                f"{source_file}"
            ) from exc

        marker = required_string(raw, "marker", source)
        if (
            "\n" in marker
            or "\r" in marker
            or marker != marker.strip()
            or marker.startswith("#")
        ):
            raise PolicyError(
                f"policy {source}: marker must be a single-line label without "
                "leading comment syntax or surrounding whitespace"
            )
        start_marker, end_marker = _marker_lines(marker)
        if any(line in {start_marker, end_marker} for line in source_text.splitlines()):
            raise PolicyError(
                f"policy {source}: source asset must not contain block marker lines"
            )

        return SyncManagedBlock(
            path=safe_relative_path(required_string(raw, "path", source), source),
            source_root=source_root,
            source_file=source_file,
            marker=marker,
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
        github_resolver=None,
    ) -> tuple[Change, ...]:
        assert isinstance(operation, SyncManagedBlock)
        path = root / operation.path
        validate_repository_path(root, path)
        _validate_target(path, operation)
        current = _read_target(path, operation)
        updated = _updated_text(
            current,
            _read_source(operation),
            operation,
        )
        return (
            (
                Change(
                    operation.path,
                    "synchronize managed block",
                    operation.rationale,
                ),
            )
            if updated != current
            else ()
        )

    def apply(
        self,
        root: Path,
        operation: EnsureOperation,
        *,
        organization: str | None = None,
        github_resolver=None,
    ) -> None:
        assert isinstance(operation, SyncManagedBlock)
        path = root / operation.path
        validate_repository_path(root, path)
        _validate_target(path, operation)
        current = _read_target(path, operation)
        updated = _updated_text(current, _read_source(operation), operation)
        if updated != current:
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(updated.encode("utf-8"))


def _updated_text(current: str, source_text: str, operation: SyncManagedBlock) -> str:
    start_marker, end_marker = _marker_lines(operation.marker)
    lines = current.splitlines(keepends=True)
    values = [_line_value(line) for line in lines]
    starts = [index for index, line in enumerate(values) if line == start_marker]
    ends = [index for index, line in enumerate(values) if line == end_marker]

    if starts or ends:
        if len(starts) != 1 or len(ends) != 1 or starts[0] >= ends[0]:
            raise RepoPolicySyncError(
                f"{operation.path} must contain exactly one ordered pair of "
                "managed-block markers"
            )
        start, end = starts[0], ends[0]
        newline = _line_ending(lines[start]) or _preferred_newline(lines)
        body = _render_body(source_text, newline)
        prefix = _without_legacy_lines(lines[:start], operation)
        suffix = _without_legacy_lines(lines[end + 1 :], operation)
        return "".join([*prefix, lines[start], body, lines[end], *suffix])

    first_legacy = next(
        (
            index
            for index, line in enumerate(values)
            if _matches_legacy_line(line, operation)
        ),
        None,
    )
    insertion_index = (
        len(lines)
        if first_legacy is None
        else sum(
            not _matches_legacy_line(line, operation) for line in values[:first_legacy]
        )
    )
    cleaned = _without_legacy_lines(lines, operation)
    newline = _preferred_newline(lines)
    block = (
        f"{start_marker}{newline}"
        f"{_render_body(source_text, newline)}"
        f"{end_marker}{newline}"
    )
    prefix = "".join(cleaned[:insertion_index])
    suffix = "".join(cleaned[insertion_index:])
    if prefix and not prefix.endswith(("\n", "\r")):
        prefix += newline
    if prefix and _line_value(prefix.splitlines(keepends=True)[-1]):
        prefix += newline
    if suffix and _line_value(suffix.splitlines(keepends=True)[0]):
        block += newline
    return f"{prefix}{block}{suffix}"


def _read_source(operation: SyncManagedBlock) -> str:
    validate_repository_path(operation.source_root, operation.source_file)
    try:
        return operation.source_file.read_bytes().decode("utf-8")
    except (OSError, UnicodeError) as exc:
        raise RepoPolicySyncError(
            f"managed-block source must be readable UTF-8 text: {operation.source_file}"
        ) from exc


def _read_target(path: Path, operation: SyncManagedBlock) -> str:
    if not path.exists():
        return ""
    try:
        return path.read_bytes().decode("utf-8")
    except (OSError, UnicodeError) as exc:
        raise RepoPolicySyncError(
            f"{operation.path} must be readable UTF-8 text"
        ) from exc


def _without_legacy_lines(lines: list[str], operation: SyncManagedBlock) -> list[str]:
    return [
        line for line in lines if not _matches_legacy_line(_line_value(line), operation)
    ]


def _matches_legacy_line(line: str, operation: SyncManagedBlock) -> bool:
    return line in operation.replace_lines or any(
        fnmatchcase(line, pattern) for pattern in operation.replace_line_globs
    )


def _line_value(line: str) -> str:
    return line.rstrip("\r\n")


def _line_ending(line: str) -> str:
    if line.endswith("\r\n"):
        return "\r\n"
    if line.endswith("\n"):
        return "\n"
    if line.endswith("\r"):
        return "\r"
    return ""


def _preferred_newline(lines: list[str]) -> str:
    return next(
        (ending for line in lines if (ending := _line_ending(line))),
        "\n",
    )


def _render_body(source_text: str, newline: str) -> str:
    return "".join(f"{line}{newline}" for line in source_text.splitlines())


def _marker_lines(marker: str) -> tuple[str, str]:
    return f"# {marker}", f"# end {marker}"


def _validate_target(path: Path, operation: SyncManagedBlock) -> None:
    if path.exists() and not path.is_file():
        raise RepoPolicySyncError(f"{operation.path} must be a file")
