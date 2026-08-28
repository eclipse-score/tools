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

from repo_cache import Repository as Repository


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
class RemoveFile:
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


EnsureOperation = EnsureLine | RemoveFile | ReplaceRegex | EnsureMinimumVersion


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
    legacy_names: tuple[str, ...] = ()


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
