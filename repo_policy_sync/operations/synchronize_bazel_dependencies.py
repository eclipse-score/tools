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

"""Synchronize related bzlmod dependencies and legacy BUILD references."""

from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from ..bazel import parse_bazel_version, starlark_call_ranges
from ..errors import PolicyError, RepoPolicySyncError
from ..models import (
    BazelDependencyUpdate,
    Change,
    EnsureOperation,
    SynchronizeBazelDependencies,
)
from ._validation import (
    expect_keys,
    optional_string,
    required_string,
    safe_relative_path,
    string_list,
    validate_repository_path,
)

# Keep the complete call text so argument values can be located exactly.
_NAME_ARGUMENT = re.compile(r"\bname\s*=\s*([\"'])([^\"']+)\1")
_VERSION_ARGUMENT = re.compile(r"\bversion\s*=\s*([\"'])([^\"']*)\1")
_MODULE_NAME_ARGUMENT = re.compile(r"\bmodule_name\s*=\s*([\"'])([^\"']+)\1")
_COMMIT_ARGUMENT = re.compile(r"\bcommit\s*=\s*([\"'])([^\"']*)\1")
_REMOTE_ARGUMENT = re.compile(r"\bremote\s*=\s*([\"'])([^\"']*)\1")


@dataclass(frozen=True)
class _DependencyLocation:
    # These are absolute offsets into MODULE.bazel, not offsets inside bazel_dep.
    # Absolute offsets let the caller update several fields without reparsing text.
    name: str
    version: tuple[int, int, int]
    name_start: int
    name_end: int
    version_start: int
    version_end: int


@dataclass(frozen=True)
class _GitOverrideLocation:
    # These are absolute offsets into MODULE.bazel.
    module_name: str
    commit: str
    module_name_start: int
    module_name_end: int
    commit_start: int
    commit_end: int
    remote: str | None
    remote_start: int | None
    remote_end: int | None
    remote_insertion: int
    remote_insertion_prefix: str


class SynchronizeBazelDependenciesOperation:
    operation_type = "synchronize_bazel_dependencies"
    operation_class = SynchronizeBazelDependencies

    def parse(self, raw: dict[str, Any], source: Path) -> SynchronizeBazelDependencies:
        expect_keys(
            raw,
            {"type", "module_file", "dependencies", "build_file_names", "rationale"},
            source,
        )
        dependency_items = raw.get("dependencies")
        if not isinstance(dependency_items, list) or not dependency_items:
            raise PolicyError(f"policy {source}: dependencies must be a non-empty list")
        dependencies = tuple(
            _parse_dependency(item, source, index)
            for index, item in enumerate(dependency_items)
        )
        names = [
            name
            for dependency in dependencies
            for name in _dependency_names(dependency)
        ]
        if len(names) != len(set(names)):
            raise PolicyError(
                f"policy {source}: dependencies must not contain duplicate module names"
            )

        build_file_names = string_list(
            raw.get("build_file_names", ["BUILD", "BUILD.bazel"]),
            "build_file_names",
            source,
        )
        if any(
            Path(name).name != name or Path(name).is_absolute()
            for name in build_file_names
        ):
            raise PolicyError(
                f"policy {source}: build_file_names must contain file names only"
            )
        if len(build_file_names) != len(set(build_file_names)):
            raise PolicyError(
                f"policy {source}: build_file_names must not contain duplicates"
            )
        return SynchronizeBazelDependencies(
            module_file=safe_relative_path(
                required_string(raw, "module_file", source), source
            ),
            dependencies=dependencies,
            build_file_names=build_file_names,
            rationale=optional_string(raw, "rationale", source),
        )

    def describe_changes(
        self,
        root: Path,
        operation: EnsureOperation,
        *,
        organization: str | None = None,
    ) -> tuple[Change, ...]:
        assert isinstance(operation, SynchronizeBazelDependencies)
        module_path = root / operation.module_file
        validate_repository_path(root, module_path)
        # Validation happens while collecting replacements, even when no text changes.
        replacements, locations = _module_replacements(module_path, operation)
        changes: list[Change] = []
        if replacements:
            changes.append(
                Change(
                    operation.module_file,
                    "synchronize Bazel dependency versions and module names",
                    operation.rationale,
                )
            )
        build_pairs = _build_reference_pairs(operation, locations)
        for path in _build_files(root, operation):
            text = path.read_text(encoding="utf-8")
            if _replace_build_references(text, build_pairs) != text:
                changes.append(
                    Change(
                        path.relative_to(root),
                        _build_reference_description(build_pairs),
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
        assert isinstance(operation, SynchronizeBazelDependencies)
        module_path = root / operation.module_file
        validate_repository_path(root, module_path)
        replacements, locations = _module_replacements(module_path, operation)
        if replacements:
            text = module_path.read_text(encoding="utf-8")
            # Apply from the end so earlier offsets stay valid after each edit.
            for start, end, replacement in sorted(replacements, reverse=True):
                text = text[:start] + replacement + text[end:]
            module_path.write_text(text, encoding="utf-8")
        build_pairs = _build_reference_pairs(operation, locations)
        for path in _build_files(root, operation):
            text = path.read_text(encoding="utf-8")
            replaced = _replace_build_references(text, build_pairs)
            if replaced != text:
                path.write_text(replaced, encoding="utf-8")


def _parse_dependency(raw: object, source: Path, index: int) -> BazelDependencyUpdate:
    if not isinstance(raw, dict):
        raise PolicyError(f"policy {source}: dependencies[{index}] must be a mapping")
    expect_keys(
        raw,
        {"name", "version", "replacement_name", "optional", "override", "remote"},
        source,
    )
    version = required_string(raw, "version", source)
    if parse_bazel_version(version) is None:
        raise PolicyError(
            f"policy {source}: dependencies[{index}].version must be a numeric major.minor.patch version"
        )
    replacement_name = raw.get("replacement_name")
    if replacement_name is not None and (
        not isinstance(replacement_name, str) or not replacement_name.strip()
    ):
        raise PolicyError(
            f"policy {source}: replacement_name must be a non-empty string"
        )
    name = required_string(raw, "name", source)
    if replacement_name == name:
        raise PolicyError(f"policy {source}: replacement_name must differ from name")
    optional = raw.get("optional", False)
    if not isinstance(optional, bool):
        raise PolicyError(
            f"policy {source}: dependencies[{index}].optional must be a boolean"
        )
    override = optional_string(raw, "override", source)
    remote = optional_string(raw, "remote", source)
    if override is None and remote is not None:
        raise PolicyError(f"policy {source}: remote requires override")
    if override is not None and remote is None:
        raise PolicyError(f"policy {source}: override requires remote")
    return BazelDependencyUpdate(
        name, version, replacement_name, optional, override, remote
    )


def _dependency_names(dependency: BazelDependencyUpdate) -> tuple[str, ...]:
    return (dependency.module_name,) + (
        (dependency.replacement_name,)
        if dependency.replacement_name is not None
        else ()
    )


def _module_replacements(
    path: Path, operation: SynchronizeBazelDependencies
) -> tuple[list[tuple[int, int, str]], dict[str, _DependencyLocation]]:
    if not path.is_file():
        raise RepoPolicySyncError(f"{operation.module_file} must exist")
    text = path.read_text(encoding="utf-8")
    locations = _module_locations(text, operation)
    replacements: list[tuple[int, int, str]] = []
    for dependency in operation.dependencies:
        location = locations.get(dependency.module_name)
        if location is None:
            continue
        is_legacy_name = (
            location.name == dependency.module_name
            and dependency.replacement_name is not None
        )
        if is_legacy_name:
            assert dependency.replacement_name is not None
            replacements.append(
                (location.name_start, location.name_end, dependency.replacement_name)
            )
            replacements.append(
                (location.version_start, location.version_end, dependency.version)
            )
        else:
            # Existing module names are only upgraded; newer versions are preserved.
            target_version = parse_bazel_version(dependency.version)
            assert target_version is not None
            if location.version < target_version:
                replacements.append(
                    (location.version_start, location.version_end, dependency.version)
                )
    replacements.extend(_git_override_replacements(text, operation, locations))
    return replacements, locations


def _build_reference_pairs(
    operation: SynchronizeBazelDependencies,
    locations: dict[str, _DependencyLocation],
) -> tuple[tuple[str, str], ...]:
    """Return active legacy-to-current module renames for BUILD files."""

    return tuple(
        (dependency.module_name, dependency.replacement_name)
        for dependency in operation.dependencies
        if dependency.replacement_name is not None
        and dependency.module_name in locations
    )


def _replace_build_references(text: str, pairs: tuple[tuple[str, str], ...]) -> str:
    if not pairs:
        return text
    replacements = dict(pairs)
    names = sorted(replacements, key=lambda name: (-len(name), name))
    # Only external labels have @ or @@. Requiring that marker avoids changing
    # local target names that happen to contain the old module name.
    pattern = re.compile(
        r"(?P<prefix>@@?)(?P<module>"
        + "|".join(re.escape(name) for name in names)
        + r")(?=//)"
    )
    return pattern.sub(
        lambda match: match.group("prefix") + replacements[match.group("module")],
        text,
    )


def _build_reference_description(pairs: tuple[tuple[str, str], ...]) -> str:
    if len(pairs) == 1:
        old_name, new_name = pairs[0]
        return f"replace {old_name!r} with {new_name!r} in BUILD files"
    return "replace legacy Bazel module references in BUILD files"


def _git_override_replacements(
    text: str,
    operation: SynchronizeBazelDependencies,
    locations: dict[str, _DependencyLocation],
) -> list[tuple[int, int, str]]:
    overrides = _git_override_locations(text, operation)
    replacements: list[tuple[int, int, str]] = []
    missing: list[tuple[str, str, str]] = []
    for dependency in operation.dependencies:
        if dependency.override is None:
            continue
        if dependency.remote is None:
            raise RepoPolicySyncError(
                f"{operation.module_file} git override for {dependency.module_name!r} "
                "must define remote"
            )
        location = locations.get(dependency.module_name)
        # Optional dependencies absent from a repository are skipped, including
        # their override. Required dependencies have already been validated by
        # _module_locations.
        if location is None:
            continue
        target_version = parse_bazel_version(dependency.version)
        assert target_version is not None
        if location.version > target_version:
            # A git override belongs to the configured baseline. Preserve a
            # newer released dependency and its existing source pin.
            continue
        final_name = (
            dependency.replacement_name
            if location.name == dependency.module_name
            and dependency.replacement_name is not None
            else location.name
        )
        matching_names = [
            name for name in _dependency_names(dependency) if name in overrides
        ]
        if len(matching_names) > 1:
            raise RepoPolicySyncError(
                f"{operation.module_file} must contain at most one git_override for "
                f"{final_name!r}"
            )
        if not matching_names:
            missing.append((final_name, dependency.override, dependency.remote))
            continue
        override = overrides[matching_names[0]]
        if override.module_name != final_name:
            replacements.append(
                (override.module_name_start, override.module_name_end, final_name)
            )
        if override.commit != dependency.override:
            replacements.append(
                (override.commit_start, override.commit_end, dependency.override)
            )
        if override.remote is None:
            replacements.append(
                (
                    override.remote_insertion,
                    override.remote_insertion,
                    f'{override.remote_insertion_prefix}    remote = "{dependency.remote}",\n',
                )
            )
        elif override.remote != dependency.remote:
            assert override.remote_start is not None
            assert override.remote_end is not None
            replacements.append(
                (override.remote_start, override.remote_end, dependency.remote)
            )
    if missing:
        separator = "" if text.endswith("\n\n") else "\n"
        blocks = "\n".join(
            "\n".join(
                (
                    "git_override(",
                    f'    module_name = "{module_name}",',
                    f'    commit = "{commit}",',
                    f'    remote = "{remote}",',
                    ")",
                )
            )
            for module_name, commit, remote in missing
        )
        replacements.append((len(text), len(text), f"{separator}{blocks}\n"))
    return replacements


def _git_override_locations(
    text: str, operation: SynchronizeBazelDependencies
) -> dict[str, _GitOverrideLocation]:
    configured_names = {
        name
        for dependency in operation.dependencies
        if dependency.override is not None
        for name in _dependency_names(dependency)
    }
    locations: dict[str, _GitOverrideLocation] = {}
    for body_start, body_end in starlark_call_ranges(text, "git_override"):
        # Commented examples are not active overrides and must remain unchanged.
        body = text[body_start:body_end]
        module_name_matches = list(_MODULE_NAME_ARGUMENT.finditer(body))
        matching_names = [
            match for match in module_name_matches if match.group(2) in configured_names
        ]
        if not matching_names:
            continue
        if len(module_name_matches) != 1:
            raise RepoPolicySyncError(
                f"{operation.module_file} git_override must declare module_name exactly once "
                f"for {matching_names[0].group(2)!r}"
            )
        module_name_match = matching_names[0]
        module_name = module_name_match.group(2)
        if module_name in locations:
            raise RepoPolicySyncError(
                f"{operation.module_file} must contain at most one git_override for "
                f"{module_name!r}"
            )
        commit_matches = list(_COMMIT_ARGUMENT.finditer(body))
        if len(commit_matches) != 1:
            raise RepoPolicySyncError(
                f"{operation.module_file} git_override for {module_name!r} must declare "
                "commit exactly once"
            )
        commit_match = commit_matches[0]
        remote_matches = list(_REMOTE_ARGUMENT.finditer(body))
        if len(remote_matches) > 1:
            raise RepoPolicySyncError(
                f"{operation.module_file} git_override for {module_name!r} must declare "
                "remote at most once"
            )
        remote_match = remote_matches[0] if remote_matches else None
        locations[module_name] = _GitOverrideLocation(
            module_name=module_name,
            commit=commit_match.group(2),
            module_name_start=body_start + module_name_match.start(2),
            module_name_end=body_start + module_name_match.end(2),
            commit_start=body_start + commit_match.start(2),
            commit_end=body_start + commit_match.end(2),
            remote=remote_match.group(2) if remote_match else None,
            remote_start=(body_start + remote_match.start(2))
            if remote_match
            else None,
            remote_end=(body_start + remote_match.end(2))
            if remote_match
            else None,
            remote_insertion=body_end,
            remote_insertion_prefix="" if body.endswith("\n") else "\n",
        )
    return locations


def _module_locations(
    text: str, operation: SynchronizeBazelDependencies
) -> dict[str, _DependencyLocation]:
    locations: dict[str, _DependencyLocation] = {}
    configured_names = {
        name
        for dependency in operation.dependencies
        for name in _dependency_names(dependency)
    }
    for body_start, body_end in starlark_call_ranges(text, "bazel_dep"):
        # Only active bazel_dep calls participate in synchronization; comments
        # often document an old dependency and must not affect the result.
        body = text[body_start:body_end]
        name_matches = list(_NAME_ARGUMENT.finditer(body))
        matching_names = [
            match for match in name_matches if match.group(2) in configured_names
        ]
        if not matching_names:
            continue
        # Only configured direct dependencies need strict validation. Other Bazel
        # calls are left untouched and can use a different declaration style.
        if len(name_matches) != 1:
            raise RepoPolicySyncError(
                f"{operation.module_file} bazel_dep must declare name exactly once for "
                f"{matching_names[0].group(2)!r}"
            )
        name_match = matching_names[0]
        name = name_match.group(2)
        if name in locations:
            raise RepoPolicySyncError(
                f"{operation.module_file} must contain at most one bazel_dep for {name!r}"
            )
        version_matches = list(_VERSION_ARGUMENT.finditer(body))
        if len(version_matches) != 1:
            raise RepoPolicySyncError(
                f"{operation.module_file} bazel_dep for {name!r} must declare version exactly once"
            )
        version_match = version_matches[0]
        version_text = version_match.group(2)
        version = parse_bazel_version(version_text)
        if version is None:
            raise RepoPolicySyncError(
                f"{operation.module_file} bazel_dep for {name!r} must use X.Y.Z, found {version_text!r}"
            )
        locations[name] = _DependencyLocation(
            name=name,
            version=version,
            name_start=body_start + name_match.start(2),
            name_end=body_start + name_match.end(2),
            version_start=body_start + version_match.start(2),
            version_end=body_start + version_match.end(2),
        )
    for dependency in operation.dependencies:
        # A migration may find either its old name or its new name, but never both.
        configured = [
            name for name in _dependency_names(dependency) if name in locations
        ]
        if not configured and dependency.optional:
            continue
        if len(configured) != 1:
            names = " or ".join(repr(name) for name in _dependency_names(dependency))
            raise RepoPolicySyncError(
                f"{operation.module_file} must contain exactly one bazel_dep for {names}"
            )
        location = locations[configured[0]]
        locations[dependency.module_name] = location
    return locations


def _build_files(
    root: Path, operation: SynchronizeBazelDependencies
) -> tuple[Path, ...]:
    # BUILD files are selected by basename because Bazel allows them in every package.
    paths: list[Path] = []
    for path in sorted(root.rglob("*")):
        relative = path.relative_to(root)
        if ".git" in relative.parts:
            continue
        # Validate every discovered path before filtering so a symlinked
        # directory cannot be followed later when a BUILD file is read.
        validate_repository_path(root, path)
        if path.is_file() and path.name in operation.build_file_names:
            paths.append(path)
    return tuple(paths)
