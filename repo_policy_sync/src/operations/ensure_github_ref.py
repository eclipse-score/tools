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

"""Operations for maintaining external GitHub refs used in workflows."""

from __future__ import annotations

import re
from dataclasses import dataclass
from functools import cmp_to_key
from pathlib import Path
from typing import Any, Callable

from ..errors import PolicyError, RepoPolicySyncError
from ..github import GitHubResolver, GitHubTag
from ..models import (
    Change,
    EnsureExactGitHubRef,
    EnsureMinimalGitHubRef,
    EnsureOperation,
)
from ._validation import (
    expect_keys,
    optional_string,
    required_string,
    validate_repository_path,
)

_TARGET = re.compile(
    r"[A-Za-z0-9_-][A-Za-z0-9_.-]*/[A-Za-z0-9_.-]+"
    r"(?:/[A-Za-z0-9_.-]+)*\Z"
)
_FULL_SHA = re.compile(r"[0-9a-fA-F]{40}\Z")
_SEMVER = re.compile(
    r"v?(?P<major>0|[1-9][0-9]*)"
    r"(?:\.(?P<minor>0|[1-9][0-9]*))?"
    r"(?:\.(?P<patch>0|[1-9][0-9]*))?"
    r"(?:-(?P<prerelease>[0-9A-Za-z-]+(?:\.[0-9A-Za-z-]+)*))?"
    r"(?:\+[0-9A-Za-z-]+(?:\.[0-9A-Za-z-]+)*)?\Z"
)
_USES_REFERENCE = re.compile(
    r"(?m)^[ \t]*(?:-[ \t]+)?uses[ \t]*:[ \t]*"
    r"(?P<quote>[\"']?)"
    r"(?P<target>[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+(?:/[A-Za-z0-9_.-]+)*)@"
    r"(?P<ref>[^ \t\r\n#\"']+)"
    r"(?P=quote)[ \t]*(?:#.*)?(?=\r?$)"
)
_BLOCK_SCALAR_HEADER = re.compile(
    r"^[ \t]*.*:\s*[|>][+-]?[0-9]*\s*(?:#.*)?(?:\r?\n)?\Z"
)


@dataclass(frozen=True)
class _SemanticVersion:
    major: int
    minor: int
    patch: int
    prerelease: tuple[str, ...] = ()


@dataclass(frozen=True)
class _WorkflowReference:
    """One syntactically supported workflow ``uses:`` reference."""

    ref: str
    start: int
    end: int


class EnsureExactGitHubRefOperation:
    """Set all workflow references to one external target to one exact ref."""

    operation_type = "ensure_exact"
    operation_class = EnsureExactGitHubRef

    def parse(self, raw: dict[str, Any], source: Path) -> EnsureExactGitHubRef:
        expect_keys(raw, {"type", "target", "ref", "rationale"}, source)
        target = _parse_target(raw, source)
        ref = required_string(raw, "ref", source)
        if any(character.isspace() or character in "\"'#" for character in ref):
            raise PolicyError(
                f"policy {source}: ref must be a non-empty branch, tag, or full commit SHA"
            )
        return EnsureExactGitHubRef(
            target=target,
            ref=ref,
            rationale=optional_string(raw, "rationale", source),
        )

    def describe_changes(
        self,
        root: Path,
        operation: EnsureOperation,
        *,
        organization: str | None = None,
        github_resolver: GitHubResolver | None = None,
    ) -> tuple[Change, ...]:
        assert isinstance(operation, EnsureExactGitHubRef)
        return _describe_workflow_updates(
            root,
            target=operation.target,
            rationale=operation.rationale,
            replacement_for=lambda _ref: operation.ref,
            description=lambda count: (
                f"set {count} {operation.target!r} GitHub target reference(s) "
                f"to {operation.ref!r}"
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
        assert isinstance(operation, EnsureExactGitHubRef)
        _apply_workflow_updates(
            root,
            target=operation.target,
            replacement_for=lambda _ref: operation.ref,
        )


class EnsureMinimalGitHubRefOperation:
    """Keep tags and commit pins at least as new as a configured release."""

    operation_type = "ensure_minimal"
    operation_class = EnsureMinimalGitHubRef

    def parse(self, raw: dict[str, Any], source: Path) -> EnsureMinimalGitHubRef:
        expect_keys(raw, {"type", "target", "minimum_version", "rationale"}, source)
        target = _parse_target(raw, source)
        minimum_version = required_string(raw, "minimum_version", source)
        if _parse_semantic_version(minimum_version) is None:
            raise PolicyError(
                f"policy {source}: minimum_version must be a semantic version such as v5.1"
            )
        return EnsureMinimalGitHubRef(
            target=target,
            minimum_version=minimum_version,
            rationale=optional_string(raw, "rationale", source),
        )

    def describe_changes(
        self,
        root: Path,
        operation: EnsureOperation,
        *,
        organization: str | None = None,
        github_resolver: GitHubResolver | None = None,
    ) -> tuple[Change, ...]:
        assert isinstance(operation, EnsureMinimalGitHubRef)
        references = _workflow_references(root, operation.target)
        if not references or all(
            _parse_semantic_version(reference.ref) is None
            and _FULL_SHA.fullmatch(reference.ref) is None
            for reference in references
        ):
            return ()
        resolver = github_resolver or GitHubResolver()
        required_tag = _resolve_minimum_tag(resolver, operation)
        return _describe_workflow_updates(
            root,
            target=operation.target,
            rationale=operation.rationale,
            replacement_for=_minimal_replacement_for(operation, required_tag, resolver),
            description=lambda count: (
                f"update {count} {operation.target!r} GitHub target reference(s) "
                f"to minimum release {required_tag.name!r}"
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
        assert isinstance(operation, EnsureMinimalGitHubRef)
        references = _workflow_references(root, operation.target)
        if not references or all(
            _parse_semantic_version(reference.ref) is None
            and _FULL_SHA.fullmatch(reference.ref) is None
            for reference in references
        ):
            return
        resolver = github_resolver or GitHubResolver()
        required_tag = _resolve_minimum_tag(resolver, operation)
        _apply_workflow_updates(
            root,
            target=operation.target,
            replacement_for=_minimal_replacement_for(operation, required_tag, resolver),
        )


def _parse_target(raw: dict[str, Any], source: Path) -> str:
    target = required_string(raw, "target", source)
    if _TARGET.fullmatch(target) is None:
        raise PolicyError(
            f"policy {source}: target must be an external GitHub uses target in "
            "owner/repository[/path] form; local targets are not supported"
        )
    return target


def _repository_for(target: str) -> str:
    """Extract the API repository from an action or reusable-workflow target.

    Workflow targets may append an action directory or workflow file to the
    repository name, but GitHub's tag and comparison endpoints are scoped to
    ``owner/repository``. The remaining path is used only for workflow text
    matching and must not be sent to those endpoints.
    """

    return "/".join(target.split("/", 2)[:2])


def _resolve_minimum_tag(
    resolver: GitHubResolver, operation: EnsureMinimalGitHubRef
) -> GitHubTag:
    minimum = _parse_semantic_version(operation.minimum_version)
    assert minimum is not None
    candidates = [
        (tag, _parse_semantic_version(tag.name))
        for tag in resolver.tags(_repository_for(operation.target))
    ]
    candidates = [
        (tag, version)
        for tag, version in candidates
        if version is not None and _compare_versions(version, minimum) >= 0
    ]
    if not candidates:
        raise RepoPolicySyncError(
            f"GitHub target {operation.target!r} has no tag meeting minimum version "
            f"{operation.minimum_version!r}"
        )

    # Prefer the spelling requested by the policy when it exists. Otherwise
    # choose the lowest available release so a policy does not jump farther
    # than necessary; the name is a deterministic tie-breaker for equivalent
    # forms such as v5.1 and v5.1.0.
    exact_name = next(
        (tag for tag, _version in candidates if tag.name == operation.minimum_version),
        None,
    )
    if exact_name is not None:
        return exact_name
    return min(
        candidates,
        key=cmp_to_key(_compare_tag_candidates),
    )[0]


def _minimal_replacement_for(
    operation: EnsureMinimalGitHubRef,
    required_tag: GitHubTag,
    resolver: GitHubResolver,
) -> Callable[[str], str]:
    required_version = _parse_semantic_version(required_tag.name)
    assert required_version is not None
    repository = _repository_for(operation.target)
    tagged_versions = _tagged_release_versions(resolver, repository)

    def replacement(current_ref: str) -> str:
        current_version = _parse_semantic_version(current_ref)
        if current_version is not None:
            return (
                required_tag.name
                if _compare_versions(current_version, required_version) < 0
                else current_ref
            )
        if _FULL_SHA.fullmatch(current_ref) is None:
            # A branch is intentionally not interpreted as a version. This
            # also leaves expressions and other unsupported refs untouched.
            return current_ref
        if current_ref.lower() == required_tag.sha.lower():
            return current_ref

        # A release tag is the authoritative semantic-version information for
        # its commit. This handles newer release lines whose histories do not
        # descend from the configured minimum tag.
        tagged_version = tagged_versions.get(current_ref.lower())
        if tagged_version is not None:
            if _compare_versions(tagged_version, required_version) >= 0:
                return current_ref
            else:
                return required_tag.sha

        # Without a release tag, ancestry is the only available ordering
        # signal. Keep rejecting diverged histories because timestamps cannot
        # safely establish that an untagged commit meets the minimum version.
        status = resolver.compare_commits(repository, required_tag.sha, current_ref)
        if status == "behind":
            return required_tag.sha
        if status == "diverged":
            raise RepoPolicySyncError(
                f"cannot compare GitHub target {operation.target!r} commits "
                f"{current_ref} and {required_tag.sha}: commit histories diverged"
            )
        if status in {"ahead", "identical"}:
            return current_ref
        raise RepoPolicySyncError(
            f"cannot compare GitHub target {operation.target!r} commits "
            f"{current_ref} and {required_tag.sha}: unknown comparison status {status!r}"
        )

    return replacement


def _tagged_release_versions(
    resolver: GitHubResolver, repository: str
) -> dict[str, _SemanticVersion]:
    """Index the highest semantic release version associated with each SHA.

    A repository may maintain multiple release branches at the same time, so
    the commit behind a newer release tag is not required to be an ancestor of
    the commit behind an older minimum tag. Mapping tags to versions lets the
    minimum-version policy compare those commits semantically while retaining
    ancestry comparison for commits that have no release tag.
    """

    tagged_versions: dict[str, _SemanticVersion] = {}
    for tag in resolver.tags(repository):
        version = _parse_semantic_version(tag.name)
        if version is None:
            continue
        normalized_sha = tag.sha.lower()
        previous_version = tagged_versions.get(normalized_sha)
        if previous_version is None or _compare_versions(version, previous_version) > 0:
            tagged_versions[normalized_sha] = version
    return tagged_versions


def _workflow_files(root: Path) -> tuple[Path, ...]:
    workflow_directory = root / ".github" / "workflows"
    validate_repository_path(root, workflow_directory)
    if not workflow_directory.exists():
        return ()
    if not workflow_directory.is_dir():
        raise RepoPolicySyncError(".github/workflows must be a directory")
    paths = tuple(
        sorted(
            path
            for path in workflow_directory.rglob("*")
            if path.is_file() and path.suffix in {".yml", ".yaml"}
        )
    )
    for path in paths:
        validate_repository_path(root, path)
    return paths


def _references(text: str, target: str) -> tuple[_WorkflowReference, ...]:
    blocked_ranges = _block_scalar_ranges(text)
    return tuple(
        _WorkflowReference(
            ref=match.group("ref"),
            start=match.start("ref"),
            end=match.end("ref"),
        )
        for match in _USES_REFERENCE.finditer(text)
        if match.group("target") == target
        and not any(start <= match.start() < end for start, end in blocked_ranges)
    )


def _workflow_references(root: Path, target: str) -> tuple[_WorkflowReference, ...]:
    """Collect matching entries before deciding whether remote data is needed."""

    references: list[_WorkflowReference] = []
    for path in _workflow_files(root):
        references.extend(_references(_read_workflow(path), target))
    return tuple(references)


def _block_scalar_ranges(text: str) -> tuple[tuple[int, int], ...]:
    """Return content spans of YAML literal/folded scalar values.

    A shell script in ``run: |`` can contain text that looks like a workflow
    key. It is data, not a ``uses`` property, so it must not be rewritten.
    This small indentation scan avoids a YAML round trip while retaining the
    source formatting needed by the operation.
    """

    lines = text.splitlines(keepends=True)
    offsets: list[int] = []
    offset = 0
    for line in lines:
        offsets.append(offset)
        offset += len(line)
    ranges: list[tuple[int, int]] = []
    index = 0
    while index < len(lines):
        line = lines[index]
        if _BLOCK_SCALAR_HEADER.fullmatch(line):
            base_indent = len(line) - len(line.lstrip(" \t"))
            content_start = offsets[index] + len(line)
            end_index = index + 1
            while end_index < len(lines):
                content_line = lines[end_index]
                stripped = content_line.lstrip(" \t\r\n")
                if (
                    stripped
                    and len(content_line) - len(content_line.lstrip(" \t"))
                    <= base_indent
                ):
                    break
                end_index += 1
            content_end = offsets[end_index] if end_index < len(lines) else len(text)
            if content_start < content_end:
                ranges.append((content_start, content_end))
            index = end_index
            continue
        index += 1
    return tuple(ranges)


def _updated_text(
    text: str,
    references: tuple[_WorkflowReference, ...],
    replacement_for: Callable[[str], str],
) -> tuple[str, int]:
    replacements: list[tuple[int, int, str]] = []
    for reference in references:
        replacement = replacement_for(reference.ref)
        if replacement != reference.ref:
            replacements.append((reference.start, reference.end, replacement))
    updated = text
    for start, end, replacement in reversed(replacements):
        updated = updated[:start] + replacement + updated[end:]
    return updated, len(replacements)


def _describe_workflow_updates(
    root: Path,
    *,
    target: str,
    rationale: str | None,
    replacement_for: Callable[[str], str],
    description: Callable[[int], str],
) -> tuple[Change, ...]:
    changes: list[Change] = []
    for path in _workflow_files(root):
        text = _read_workflow(path)
        references = _references(text, target)
        _updated, count = _updated_text(text, references, replacement_for)
        if count:
            changes.append(
                Change(path.relative_to(root), description(count), rationale)
            )
    return tuple(changes)


def _apply_workflow_updates(
    root: Path,
    *,
    target: str,
    replacement_for: Callable[[str], str],
) -> None:
    for path in _workflow_files(root):
        text = _read_workflow(path)
        references = _references(text, target)
        updated, count = _updated_text(text, references, replacement_for)
        if count:
            # Read/write bytes so Python does not normalize CRLF workflow files
            # while changing only the ref span.
            path.write_bytes(updated.encode("utf-8"))


def _read_workflow(path: Path) -> str:
    """Read UTF-8 workflow text without normalizing its line endings."""

    return path.read_bytes().decode("utf-8")


def _parse_semantic_version(value: str) -> _SemanticVersion | None:
    match = _SEMVER.fullmatch(value)
    if match is None:
        return None
    prerelease = match.group("prerelease")
    return _SemanticVersion(
        major=int(match.group("major")),
        minor=int(match.group("minor") or 0),
        patch=int(match.group("patch") or 0),
        prerelease=tuple(prerelease.split(".")) if prerelease else (),
    )


def _compare_versions(left: _SemanticVersion, right: _SemanticVersion) -> int:
    left_core = (left.major, left.minor, left.patch)
    right_core = (right.major, right.minor, right.patch)
    if left_core != right_core:
        return (left_core > right_core) - (left_core < right_core)
    if not left.prerelease or not right.prerelease:
        return (not left.prerelease) - (not right.prerelease)
    for left_part, right_part in zip(left.prerelease, right.prerelease):
        if left_part == right_part:
            continue
        left_numeric = left_part.isdecimal()
        right_numeric = right_part.isdecimal()
        if left_numeric and right_numeric:
            return (int(left_part) > int(right_part)) - (
                int(left_part) < int(right_part)
            )
        if left_numeric != right_numeric:
            return -1 if left_numeric else 1
        return (left_part > right_part) - (left_part < right_part)
    return (len(left.prerelease) > len(right.prerelease)) - (
        len(left.prerelease) < len(right.prerelease)
    )


def _compare_tag_candidates(
    left: tuple[GitHubTag, _SemanticVersion],
    right: tuple[GitHubTag, _SemanticVersion],
) -> int:
    comparison = _compare_versions(left[1], right[1])
    if comparison:
        return comparison
    left_tie_breaker = (len(left[0].name), left[0].name)
    right_tie_breaker = (len(right[0].name), right[0].name)
    return (left_tie_breaker > right_tie_breaker) - (
        left_tie_breaker < right_tie_breaker
    )
