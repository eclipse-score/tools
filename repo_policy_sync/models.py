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

"""Domain models for policies and evaluated changes."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path


def policy_branch_slug(policy_id: str) -> str:
    """Normalize a policy identifier to the branch-name component it owns."""

    slug = "".join(
        character if character.isalnum() else "-" for character in policy_id.lower()
    )
    return "-".join(part for part in slug.split("-") if part)


@dataclass(frozen=True)
class BazelDependencyCondition:
    """A comparison against one direct bzlmod dependency version."""

    module_name: str
    operator: str
    version: tuple[int, int, int]


@dataclass(frozen=True)
class BazelCondition:
    """A condition on direct bzlmod dependencies."""

    # The first group is required in full; the second group provides alternatives.
    direct_module_dependencies: tuple[str, ...]
    any_direct_module_dependencies: tuple[str, ...] = ()
    any_direct_module_conditions: tuple[BazelDependencyCondition, ...] = ()


@dataclass(frozen=True)
class FileContainsCondition:
    """A condition requiring a file to match a regular expression."""

    path: Path
    pattern: str


@dataclass(frozen=True)
class FileContainsAnyCondition:
    """A condition requiring at least one file to match a regular expression."""

    conditions: tuple[FileContainsCondition, ...]


@dataclass(frozen=True)
class FileExistsCondition:
    """A condition requiring a repository-relative file to exist."""

    path: Path


@dataclass(frozen=True)
class EnsureLine:
    path: Path
    line: str
    replace_lines: tuple[str, ...]
    replace_line_globs: tuple[str, ...] = ()
    rationale: str | None = None


@dataclass(frozen=True)
class EnsureNoSuchFile:
    path: Path
    rationale: str | None = None


@dataclass(frozen=True)
class ReplaceRegex:
    path: Path
    pattern: str
    replacement: str
    rationale: str | None = None


@dataclass(frozen=True)
class EnsureMinimumVersion:
    path: Path
    minimum_version: str
    rationale: str | None = None


@dataclass(frozen=True)
class EnsureBazelDependency:
    """Ensure a SCORE devcontainer's direct bzlmod dependency exists."""

    dockerfile: Path
    module_file: Path
    image: str
    module_name: str
    rationale: str | None = None


@dataclass(frozen=True)
class SynchronizeDevcontainerVersion:
    """Keep a SCORE devcontainer image and bzlmod dependency on one version."""

    dockerfile: Path
    module_file: Path
    image: str
    module_name: str
    rationale: str | None = None


@dataclass(frozen=True)
class BazelDependencyUpdate:
    """A target version, optional module-name migration, and git override."""

    module_name: str
    version: str
    replacement_name: str | None = None
    optional: bool = False
    override: str | None = None
    remote: str | None = None


@dataclass(frozen=True)
class SynchronizeBazelDependencies:
    """Synchronize related bzlmod dependencies and legacy BUILD references."""

    module_file: Path
    dependencies: tuple[BazelDependencyUpdate, ...]
    build_file_names: tuple[str, ...] = ("BUILD", "BUILD.bazel")
    rationale: str | None = None


@dataclass(frozen=True)
class SynchronizeFile:
    """Keep a repository file equal to a policy-owned text asset."""

    path: Path
    contents: str
    executable: bool = False
    rationale: str | None = None
    preserve_reusable_workflow_refs: tuple[tuple[str, tuple[int, int, int]], ...] = ()
    preserve_workflow_content: bool = False


@dataclass(frozen=True)
class SynchronizeWorkflow:
    """Synchronize a reusable workflow while preserving repository structure."""

    source: Path
    contents: str
    reusable_workflow: str
    minimum_version: tuple[int, int, int]
    required_triggers: tuple[str, ...]
    workflow_run_path: Path | None = None
    workflow_run_contents: str | None = None
    rationale: str | None = None


@dataclass(frozen=True)
class MigrateDevcontainerJson:
    """Replace an image-based devcontainer config with a Dockerfile."""

    sources: tuple[Path, ...]
    destination: Path
    dockerfile: Path
    image: str
    dockerfile_comment: str | None = None
    rationale: str | None = None
    copyright_header: str | None = None
    copyright_header_organization: str | None = None


EnsureOperation = (
    EnsureLine
    | EnsureNoSuchFile
    | ReplaceRegex
    | EnsureMinimumVersion
    | EnsureBazelDependency
    | SynchronizeDevcontainerVersion
    | SynchronizeBazelDependencies
    | SynchronizeFile
    | SynchronizeWorkflow
    | MigrateDevcontainerJson
)


@dataclass(frozen=True)
class AfterApplyCommand:
    """A command to run after a policy has changed a repository."""

    command: tuple[str, ...]
    when_file_exists: Path
    description: str
    when_path_changed: Path | None = None


@dataclass(frozen=True)
class Policy:
    id: str
    title: str
    description: str | None
    bazel_condition: BazelCondition | None
    ensure: tuple[EnsureOperation, ...]
    after_apply: tuple[AfterApplyCommand, ...] = ()
    file_exists_condition: FileExistsCondition | None = None
    file_contains_condition: FileContainsCondition | None = None
    file_contains_any_condition: FileContainsAnyCondition | None = None


@dataclass(frozen=True)
class Repository:
    name: str
    default_branch: str | None
    archived: bool = False


@dataclass(frozen=True)
class Change:
    path: Path
    description: str
    rationale: str | None = None


@dataclass(frozen=True)
class Evaluation:
    applies: bool
    changes: tuple[Change, ...]

    @property
    def compliant(self) -> bool:
        return self.applies and not self.changes
