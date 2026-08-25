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

"""Synchronize a reusable workflow without replacing its repository envelope."""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any

import yaml

from ..errors import PolicyError, RepoPolicySyncError
from ..models import Change, EnsureOperation, SynchronizeWorkflow
from ._validation import (
    expect_keys,
    optional_string,
    required_string,
    safe_relative_path,
    string_list,
    validate_repository_path,
)
from .synchronize_file import (
    _merge_workflow_content,
    _merge_workflow_jobs,
    _mapping_entries,
    _preserved_ref,
    _replace_top_level_section,
    _top_level_section,
)


class SynchronizeWorkflowOperation:
    """Synchronize a selected reusable workflow and its optional workflow_run."""

    operation_type = "synchronize_workflow"
    operation_class = SynchronizeWorkflow

    def parse(self, raw: dict[str, Any], source: Path) -> SynchronizeWorkflow:
        expect_keys(
            raw,
            {
                "type",
                "source",
                "reusable_workflow",
                "minimum_version",
                "required_triggers",
                "workflow_run",
                "rationale",
            },
            source,
        )
        asset = safe_relative_path(required_string(raw, "source", source), source)
        contents = _read_asset(source.parent / asset, source, asset)
        reusable_workflow = required_string(raw, "reusable_workflow", source)
        minimum_version = _parse_version(
            required_string(raw, "minimum_version", source), source
        )
        required_triggers = string_list(
            raw.get("required_triggers", []), "required_triggers", source
        )
        if not required_triggers:
            raise PolicyError(f"policy {source}: required_triggers must not be empty")
        _validate_source_triggers(contents, required_triggers, source)

        workflow_run_path: Path | None = None
        workflow_run_contents: str | None = None
        workflow_run = raw.get("workflow_run")
        if workflow_run is not None:
            if not isinstance(workflow_run, dict) or set(workflow_run) != {
                "path",
                "source",
            }:
                raise PolicyError(
                    f"policy {source}: workflow_run must contain only path and source"
                )
            workflow_run_path = safe_relative_path(
                required_string(workflow_run, "path", source), source
            )
            workflow_run_asset = safe_relative_path(
                required_string(workflow_run, "source", source), source
            )
            workflow_run_contents = _read_asset(
                source.parent / workflow_run_asset, source, workflow_run_asset
            )

        return SynchronizeWorkflow(
            source=asset,
            contents=contents,
            reusable_workflow=reusable_workflow,
            minimum_version=minimum_version,
            required_triggers=required_triggers,
            workflow_run_path=workflow_run_path,
            workflow_run_contents=workflow_run_contents,
            rationale=optional_string(raw, "rationale", source),
        )

    def describe_changes(
        self,
        root: Path,
        operation: EnsureOperation,
        *,
        organization: str | None = None,
    ) -> tuple[Change, ...]:
        assert isinstance(operation, SynchronizeWorkflow)
        target = _find_workflow(root, operation.reusable_workflow)
        target_text = target.read_text(encoding="utf-8")
        desired_target = _desired_workflow(target_text, operation)
        changes: list[Change] = []
        if desired_target != target_text:
            changes.append(
                Change(
                    target.relative_to(root),
                    "synchronize workflow",
                    operation.rationale,
                )
            )

        if operation.workflow_run_path is not None:
            workflow_run_path = root / operation.workflow_run_path
            validate_repository_path(root, workflow_run_path)
            workflow_run_text = (
                workflow_run_path.read_text(encoding="utf-8")
                if workflow_run_path.is_file()
                else None
            )
            desired_workflow_run = _desired_workflow_run(
                workflow_run_text,
                operation.workflow_run_contents,
                operation,
                _selected_workflow_name(target_text, operation.contents, target),
            )
            if workflow_run_text != desired_workflow_run:
                changes.append(
                    Change(
                        operation.workflow_run_path,
                        "synchronize workflow",
                        operation.rationale,
                    )
                )
        return tuple(changes)

    def apply(
        self,
        root: Path,
        operation: EnsureOperation,
        *,
        organization: str | None = None,
    ) -> None:
        assert isinstance(operation, SynchronizeWorkflow)
        target = _find_workflow(root, operation.reusable_workflow)
        target_text = target.read_text(encoding="utf-8")
        desired_target = _desired_workflow(target_text, operation)
        if desired_target != target_text:
            target.write_text(desired_target, encoding="utf-8")

        if operation.workflow_run_path is None:
            return
        workflow_run_path = root / operation.workflow_run_path
        validate_repository_path(root, workflow_run_path)
        workflow_run_text = (
            workflow_run_path.read_text(encoding="utf-8")
            if workflow_run_path.is_file()
            else None
        )
        desired_workflow_run = _desired_workflow_run(
            workflow_run_text,
            operation.workflow_run_contents,
            operation,
            _selected_workflow_name(target_text, operation.contents, target),
        )
        if workflow_run_text != desired_workflow_run:
            workflow_run_path.parent.mkdir(parents=True, exist_ok=True)
            workflow_run_path.write_text(desired_workflow_run, encoding="utf-8")


def _read_asset(path: Path, source: Path, relative: Path) -> str:
    if not path.is_file():
        raise PolicyError(
            f"policy {source}: synchronize_workflow source must be an existing file: {relative}"
        )
    try:
        return path.read_text(encoding="utf-8")
    except OSError as exc:
        raise PolicyError(
            f"policy {source}: could not read synchronize_workflow source {relative}: {exc}"
        ) from exc
    except UnicodeError as exc:
        raise PolicyError(
            f"policy {source}: synchronize_workflow source must be UTF-8: {relative}"
        ) from exc


def _parse_version(raw: str, source: Path) -> tuple[int, int, int]:
    match = re.fullmatch(r"(\d+)\.(\d+)\.(\d+)", raw)
    if match is None:
        raise PolicyError(
            f"policy {source}: minimum_version must use major.minor.patch syntax"
        )
    return tuple(int(part) for part in match.groups())


def _validate_source_triggers(
    source_text: str, required_triggers: tuple[str, ...], source: Path
) -> None:
    on = _top_level_section(source_text, "on")
    if on is None:
        raise PolicyError(f"policy {source}: workflow source must define on")
    event_names = {
        match.group("name").strip().strip("\"'")
        for match in _mapping_entries(source_text[on[0] : on[1]])
    }
    missing = sorted(set(required_triggers) - event_names)
    if missing:
        raise PolicyError(
            f"policy {source}: workflow source is missing required triggers: "
            + ", ".join(missing)
        )


def _find_workflow(root: Path, reusable_workflow: str) -> Path:
    workflows = root / ".github/workflows"
    validate_repository_path(root, workflows)
    if not workflows.is_dir():
        raise RepoPolicySyncError(f"workflow directory does not exist: {workflows}")
    pattern = re.compile(rf"(?m)^[ \t]+uses:\s*{re.escape(reusable_workflow)}@")
    matches: list[Path] = []
    for path in sorted(workflows.rglob("*")):
        if path.suffix not in {".yml", ".yaml"} or not path.is_file():
            continue
        validate_repository_path(root, path)
        if pattern.search(path.read_text(encoding="utf-8")):
            matches.append(path)
    if len(matches) != 1:
        rendered = ", ".join(str(path.relative_to(root)) for path in matches)
        expectation = "exactly one" if len(matches) == 0 else "only one"
        raise RepoPolicySyncError(
            f"expected {expectation} workflow calling {reusable_workflow!r}; found {rendered or 'none'}"
        )
    return matches[0]


def _desired_workflow(existing: str, operation: SynchronizeWorkflow) -> str:
    rules = ((operation.reusable_workflow, operation.minimum_version),)
    desired = _ensure_triggers(
        existing, operation.contents, operation.required_triggers
    )
    source_permissions = _top_level_section(operation.contents, "permissions")
    if source_permissions is None:
        desired = _remove_top_level_section(desired, "permissions")
    else:
        desired = _replace_top_level_section(desired, operation.contents, "permissions")
    desired = _merge_workflow_jobs(desired, operation.contents, rules)
    return _preserve_refs(
        existing,
        desired,
        operation.contents,
        operation.reusable_workflow,
        operation.minimum_version,
    )


def _desired_workflow_run(
    existing: str | None,
    source: str | None,
    operation: SynchronizeWorkflow,
    workflow_name: str,
) -> str:
    assert source is not None
    workflow_run_workflow = _reusable_workflow_in(source)
    rules = ((workflow_run_workflow, operation.minimum_version),)
    desired = (
        source if existing is None else _merge_workflow_content(existing, source, rules)
    )
    desired = _set_workflow_run_name(desired, workflow_name)
    if existing is not None:
        desired = _preserve_refs(existing, desired, source, rules[0][0], rules[0][1])
    return desired if desired.endswith("\n") else desired + "\n"


def _workflow_name(text: str, path: Path) -> str:
    match = re.search(r"(?m)^name:\s*(?P<value>[^\n]+)$", text)
    if match is None:
        raise RepoPolicySyncError(f"workflow {path} must define a top-level name")
    try:
        value = yaml.safe_load(match.group("value").strip())
    except yaml.YAMLError as exc:
        raise RepoPolicySyncError(f"workflow {path} has an invalid name") from exc
    if not isinstance(value, str) or not value:
        raise RepoPolicySyncError(f"workflow {path} must define a string name")
    return value


def _selected_workflow_name(existing: str, source: str, path: Path) -> str:
    try:
        return _workflow_name(existing, path)
    except RepoPolicySyncError:
        return _workflow_name(source, path)


def _reusable_workflow_in(text: str) -> str:
    matches = re.findall(r"(?m)^[ \t]+uses:\s*(?P<workflow>[^@\s]+)@[^\s#]+", text)
    if len(matches) != 1:
        raise RepoPolicySyncError(
            "workflow source must contain exactly one reusable workflow call"
        )
    return matches[0]


def _ensure_triggers(existing: str, source: str, required: tuple[str, ...]) -> str:
    source_on = _top_level_section(source, "on")
    existing_on = _top_level_section(existing, "on")
    if source_on is None:
        raise RepoPolicySyncError("workflow source must define on")
    if existing_on is None:
        return _replace_top_level_section(existing, source, "on")
    source_on_text = source[source_on[0] : source_on[1]]
    existing_on_text = existing[existing_on[0] : existing_on[1]]
    source_entries = _mapping_entries(source_on_text)
    existing_entries = _mapping_entries(existing_on_text)
    if not source_entries or not existing_entries:
        raise RepoPolicySyncError(
            "workflow trigger synchronization requires mapping-style on sections"
        )
    source_locations = {
        match.group("name").strip().strip("\"'"): (index, match)
        for index, match in enumerate(source_entries)
    }
    existing_names = {
        match.group("name").strip().strip("\"'") for match in existing_entries
    }
    missing = [name for name in required if name not in existing_names]
    if not missing:
        return existing
    additions: list[str] = []
    for name in missing:
        index, match = source_locations[name]
        end = (
            source_entries[index + 1].start()
            if index + 1 < len(source_entries)
            else len(source_on_text)
        )
        additions.append(source_on_text[match.start() : end].strip("\n"))
    replacement = existing_on_text.rstrip("\n") + "\n" + "\n".join(additions) + "\n"
    return existing[: existing_on[0]] + replacement + existing[existing_on[1] :]


def _set_workflow_run_name(text: str, workflow_name: str) -> str:
    on = _top_level_section(text, "on")
    if on is None:
        raise RepoPolicySyncError("workflow_run workflow must define on")
    on_text = text[on[0] : on[1]]
    if re.search(r"(?m)^\s+workflow_run:", on_text) is None:
        raise RepoPolicySyncError("workflow_run workflow must define on.workflow_run")
    replacement, replacements = re.subn(
        r"(?m)^(?P<indent>[ \t]+)workflows:\s*[^\n]*$",
        lambda match: (
            f"{match.group('indent')}workflows: [{json.dumps(workflow_name, ensure_ascii=False)}]"
        ),
        on_text,
    )
    if replacements != 1:
        raise RepoPolicySyncError(
            "workflow_run workflow must define on.workflow_run.workflows"
        )
    return text[: on[0]] + replacement + text[on[1] :]


def _preserve_refs(
    existing: str,
    desired: str,
    source: str,
    workflow: str,
    minimum_version: tuple[int, int, int],
) -> str:
    pattern = re.compile(rf"(?P<prefix>{re.escape(workflow)}@)(?P<ref>[^\s#]+)")
    source_matches = list(pattern.finditer(source))
    existing_matches = list(pattern.finditer(existing))
    desired_matches = list(pattern.finditer(desired))
    for index, source_match in reversed(list(enumerate(source_matches))):
        if index >= len(existing_matches) or index >= len(desired_matches):
            continue
        desired_match = desired_matches[index]
        selected = _preserved_ref(
            existing_matches[index].group("ref"),
            source_match.group("ref"),
            minimum_version,
        )
        if selected != desired_match.group("ref"):
            desired = (
                desired[: desired_match.start("ref")]
                + selected
                + desired[desired_match.end("ref") :]
            )
    return desired


def _remove_top_level_section(text: str, key: str) -> str:
    section = _top_level_section(text, key)
    if section is None:
        return text
    return text[: section[0]] + text[section[1] :]
