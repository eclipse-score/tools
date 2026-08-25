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

from __future__ import annotations

import re
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
    """Synchronize a repository file with a UTF-8 asset beside its policy."""

    operation_type = "synchronize_file"
    operation_class = SynchronizeFile

    def parse(self, raw: dict[str, Any], source: Path) -> SynchronizeFile:
        expect_keys(
            raw,
            {
                "type",
                "path",
                "source",
                "executable",
                "preserve_reusable_workflow_refs",
                "preserve_workflow_content",
                "rationale",
            },
            source,
        )
        asset = safe_relative_path(required_string(raw, "source", source), source)
        asset_path = source.parent / asset
        if not asset_path.is_file():
            raise PolicyError(
                f"policy {source}: synchronize_file source must be an existing file: {asset}"
            )
        executable = raw.get("executable", False)
        if not isinstance(executable, bool):
            raise PolicyError(f"policy {source}: executable must be a boolean")
        preserve_refs = _parse_workflow_ref_rules(
            raw.get("preserve_reusable_workflow_refs", []), source
        )
        preserve_workflow_content = raw.get("preserve_workflow_content", False)
        if not isinstance(preserve_workflow_content, bool):
            raise PolicyError(
                f"policy {source}: preserve_workflow_content must be a boolean"
            )
        try:
            contents = asset_path.read_text(encoding="utf-8")
        except OSError as exc:
            raise PolicyError(
                f"policy {source}: could not read synchronize_file source {asset}: {exc}"
            ) from exc
        except UnicodeError as exc:
            raise PolicyError(
                f"policy {source}: synchronize_file source must be UTF-8: {asset}"
            ) from exc
        return SynchronizeFile(
            path=safe_relative_path(required_string(raw, "path", source), source),
            contents=contents,
            executable=executable,
            preserve_reusable_workflow_refs=preserve_refs,
            preserve_workflow_content=preserve_workflow_content,
            rationale=optional_string(raw, "rationale", source),
        )

    def describe_changes(
        self,
        root: Path,
        operation: EnsureOperation,
        *,
        organization: str | None = None,
    ) -> tuple[Change, ...]:
        assert isinstance(operation, SynchronizeFile)
        path = root / operation.path
        validate_repository_path(root, path)
        _validate_target(path, operation)
        content_changed = not path.is_file() or (
            _desired_contents(path, operation) != path.read_text(encoding="utf-8")
        )
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
    ) -> None:
        assert isinstance(operation, SynchronizeFile)
        path = root / operation.path
        validate_repository_path(root, path)
        _validate_target(path, operation)
        desired_contents = _desired_contents(path, operation)
        if not path.is_file() or path.read_text(encoding="utf-8") != desired_contents:
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(desired_contents, encoding="utf-8")
        if operation.executable and not _is_executable(path):
            path.chmod(path.stat().st_mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)


def _validate_target(path: Path, operation: SynchronizeFile) -> None:
    if path.exists() and not path.is_file():
        raise RepoPolicySyncError(f"{operation.path} must not be a directory")


def _is_executable(path: Path) -> bool:
    return bool(path.stat().st_mode & (stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH))


def _parse_workflow_ref_rules(
    raw: object, source: Path
) -> tuple[tuple[str, tuple[int, int, int]], ...]:
    if raw == []:
        return ()
    if not isinstance(raw, list) or not raw:
        raise PolicyError(
            f"policy {source}: preserve_reusable_workflow_refs must be a non-empty list"
        )
    rules: list[tuple[str, tuple[int, int, int]]] = []
    for index, item in enumerate(raw):
        if not isinstance(item, dict) or set(item) != {"workflow", "minimum_version"}:
            raise PolicyError(
                f"policy {source}: preserve_reusable_workflow_refs[{index}] "
                "must contain only workflow and minimum_version"
            )
        workflow = required_string(item, "workflow", source)
        version = required_string(item, "minimum_version", source)
        match = re.fullmatch(r"(\d+)\.(\d+)\.(\d+)", version)
        if match is None:
            raise PolicyError(
                f"policy {source}: preserve_reusable_workflow_refs[{index}].minimum_version "
                "must use major.minor.patch syntax"
            )
        rules.append((workflow, tuple(int(part) for part in match.groups())))
    return tuple(rules)


def _desired_contents(path: Path, operation: SynchronizeFile) -> str:
    desired = operation.contents
    if not path.is_file():
        return desired

    existing = path.read_text(encoding="utf-8")
    if operation.preserve_workflow_content:
        desired = _merge_workflow_content(
            existing, desired, operation.preserve_reusable_workflow_refs
        )
    if not operation.preserve_reusable_workflow_refs:
        return desired

    for workflow, minimum_version in operation.preserve_reusable_workflow_refs:
        pattern = re.compile(rf"(?P<prefix>{re.escape(workflow)}@)(?P<ref>[^\s#]+)")
        source_matches = list(pattern.finditer(operation.contents))
        desired_matches = list(pattern.finditer(desired))
        existing_matches = list(pattern.finditer(existing))
        for index, source_match in reversed(list(enumerate(source_matches))):
            if index >= len(desired_matches):
                continue
            source_ref = source_match.group("ref")
            if index >= len(existing_matches):
                continue
            desired_match = desired_matches[index]
            existing_ref = existing_matches[index].group("ref")
            selected_ref = _preserved_ref(existing_ref, source_ref, minimum_version)
            if selected_ref != desired_match.group("ref"):
                desired = (
                    desired[: desired_match.start("ref")]
                    + selected_ref
                    + desired[desired_match.end("ref") :]
                )
    return desired


def _merge_workflow_content(
    existing: str,
    source: str,
    rules: tuple[tuple[str, tuple[int, int, int]], ...],
) -> str:
    """Apply the standard workflow envelope without replacing local jobs."""

    merged = existing
    for section in ("name", "on"):
        merged = _replace_top_level_section(merged, source, section)
    # The shared build workflow is intentionally unprivileged. Existing
    # top-level write permissions would apply to every job in this workflow.
    merged = _remove_top_level_section(merged, "permissions")

    if rules:
        source_jobs = _top_level_section(source, "jobs")
        source_job = (
            _matching_job_block(source[source_jobs[0] : source_jobs[1]], rules)
            if source_jobs is not None
            else None
        )
        if source_job is not None:
            existing_jobs = _top_level_section(merged, "jobs")
            existing_job = (
                _matching_job_location(
                    merged[existing_jobs[0] : existing_jobs[1]], rules
                )
                if existing_jobs is not None
                else None
            )
            if existing_jobs is not None and existing_job is not None:
                merged = _merge_matching_job_permissions(merged, source, rules)
            else:
                merged = _append_workflow_job(merged, source, rules)
    # Workflow files are line-oriented YAML; always leave a separator for a
    # following section and a final newline for tools that rewrite the file.
    return merged if merged.endswith("\n") else merged + "\n"


def _replace_top_level_section(existing: str, source: str, key: str) -> str:
    source_section = _top_level_section(source, key)
    existing_section = _top_level_section(existing, key)
    if source_section is None:
        return existing
    if existing_section is None:
        first_section = re.search(r"(?m)^([^\s#][^:\n]*):[^\n]*(?:\n|$)", existing)
        insert_at = (
            first_section.start() if first_section is not None else len(existing)
        )
        prefix = existing[:insert_at]
        if prefix and not prefix.endswith("\n"):
            prefix += "\n"
        return (
            prefix
            + source[source_section[0] : source_section[1]]
            + existing[insert_at:]
        )
    start, end = existing_section
    replacement = source[source_section[0] : source_section[1]]
    if existing[end:] and not replacement.endswith("\n"):
        # A source section without a trailing newline would otherwise join the
        # next existing top-level key into the same YAML line.
        replacement += "\n"
    return (
        existing[:start]
        + replacement
        + existing[end:]
    )


def _remove_top_level_section(text: str, key: str) -> str:
    section = _top_level_section(text, key)
    if section is None:
        return text
    return text[: section[0]] + text[section[1] :]


def _top_level_section(text: str, key: str) -> tuple[int, int] | None:
    lines = list(re.finditer(r"(?m)^([^\s#][^:\n]*):[^\n]*(?:\n|$)", text))
    for index, match in enumerate(lines):
        if match.group(1).strip().strip("\"'") != key:
            continue
        end = lines[index + 1].start() if index + 1 < len(lines) else len(text)
        return match.start(), end
    return None


def _append_workflow_job(
    existing: str,
    source: str,
    rules: tuple[tuple[str, tuple[int, int, int]], ...],
) -> str:
    source_jobs = _top_level_section(source, "jobs")
    if source_jobs is None:
        return existing
    source_job = _matching_job_block(source[source_jobs[0] : source_jobs[1]], rules)
    if source_job is None:
        return existing

    existing_jobs = _top_level_section(existing, "jobs")
    if existing_jobs is None:
        separator = "" if existing.endswith("\n") else "\n"
        return existing + separator + source[source_jobs[0] : source_jobs[1]]

    source_job_name = _job_name(source_job)
    existing_job_names = {
        match.group("name")
        for match in _mapping_entries(existing[existing_jobs[0] : existing_jobs[1]])
    }
    if source_job_name is not None and source_job_name in existing_job_names:
        raise RepoPolicySyncError(
            f"cannot append workflow job {source_job_name!r}: "
            "a job with that ID already exists"
        )

    _, end = existing_jobs
    prefix = existing[:end]
    separator = "" if prefix.endswith("\n") else "\n"
    return prefix + separator + source_job + existing[end:]


def _merge_matching_job_permissions(
    existing: str,
    source: str,
    rules: tuple[tuple[str, tuple[int, int, int]], ...],
) -> str:
    existing_jobs = _top_level_section(existing, "jobs")
    source_jobs = _top_level_section(source, "jobs")
    assert existing_jobs is not None
    assert source_jobs is not None
    existing_jobs_text = existing[existing_jobs[0] : existing_jobs[1]]
    source_jobs_text = source[source_jobs[0] : source_jobs[1]]
    existing_location = _matching_job_location(existing_jobs_text, rules)
    source_location = _matching_job_location(source_jobs_text, rules)
    assert existing_location is not None
    assert source_location is not None
    existing_job_start, existing_job_end = existing_location
    source_job_start, source_job_end = source_location
    existing_job = existing_jobs_text[existing_job_start:existing_job_end]
    source_job = source_jobs_text[source_job_start:source_job_end]
    source_permissions_location = _nested_job_section(source_job, "permissions")
    if source_permissions_location is None:
        return existing
    source_permissions = source_job[
        source_permissions_location[0] : source_permissions_location[1]
    ]
    existing_permissions = _nested_job_section(existing_job, "permissions")
    if existing_permissions is None:
        insertion = existing_jobs[0] + existing_job_end
        prefix = "" if existing[:insertion].endswith("\n") else "\n"
        return existing[:insertion] + prefix + source_permissions + existing[insertion:]
    permission_start, permission_end = existing_permissions
    absolute_start = existing_jobs[0] + existing_job_start + permission_start
    absolute_end = existing_jobs[0] + existing_job_start + permission_end
    return existing[:absolute_start] + source_permissions + existing[absolute_end:]


def _job_name(job_block: str) -> str | None:
    match = re.match(r"[ \t]+([^\s#][^:\n]*):", job_block)
    return match.group(1) if match is not None else None


def _matching_job_block(
    jobs_section: str,
    rules: tuple[tuple[str, tuple[int, int, int]], ...],
) -> str | None:
    location = _matching_job_location(jobs_section, rules)
    return jobs_section[location[0] : location[1]] if location is not None else None


def _matching_job_location(
    jobs_section: str,
    rules: tuple[tuple[str, tuple[int, int, int]], ...],
) -> tuple[int, int] | None:
    job_lines = _mapping_entries(jobs_section)
    for index, match in enumerate(job_lines):
        end = (
            job_lines[index + 1].start()
            if index + 1 < len(job_lines)
            else len(jobs_section)
        )
        block = jobs_section[match.start() : end]
        if any(re.search(re.escape(workflow) + r"@", block) for workflow, _ in rules):
            return match.start(), end
    return None


def _nested_job_section(text: str, key: str) -> tuple[int, int] | None:
    lines = _mapping_entries(text, nested=True)
    for index, match in enumerate(lines):
        if match.group("name").strip().strip("\"'") != key:
            continue
        end = lines[index + 1].start() if index + 1 < len(lines) else len(text)
        return match.start(), end
    return None


def _mapping_entries(
    text: str, *, nested: bool = False
) -> list[re.Match[str]]:
    """Return mapping entries at the relevant indentation level.

    Workflow files commonly use two or four spaces. Selecting the first
    mapping indentation present keeps job detection independent of that local
    style without attempting to parse all of YAML.
    """

    candidates = list(
        re.finditer(
            r"(?m)^(?P<indent>[ \t]+)(?P<name>[^\s#][^:\n]*):[^\n]*(?:\n|$)",
            text,
        )
    )
    if not candidates:
        return []
    widths = [len(match.group("indent").expandtabs(8)) for match in candidates]
    if nested:
        parent_width = widths[0]
        child_widths = [width for width in widths if width > parent_width]
        if not child_widths:
            return []
        target_width = min(child_widths)
    else:
        target_width = min(widths)
    return [
        match
        for match, width in zip(candidates, widths)
        if width == target_width
    ]


def _preserved_ref(
    existing_ref: str, source_ref: str, minimum_version: tuple[int, int, int]
) -> str:
    match = re.fullmatch(r"v?(\d+)\.(\d+)\.(\d+)", existing_ref)
    if match is None:
        # A branch or an unknown immutable ref may point at a newer release. Keep
        # it because the policy cannot prove that replacing it is safe.
        return existing_ref
    existing_version = tuple(int(part) for part in match.groups())
    return existing_ref if existing_version >= minimum_version else source_ref
