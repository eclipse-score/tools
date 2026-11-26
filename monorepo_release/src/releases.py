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

"""Read component path rules and compare Git commits with stable release tags."""

import re
import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import cast

import yaml

type Version = tuple[int, int, int]

LABELER_PATH = ".github/labeler.yml"
CONVENTIONAL_HEADER = re.compile(
    r"^(?P<type>[a-z]+)(?:\([^()\r\n]+\))?(?P<breaking>!)?:[ \t]+", re.IGNORECASE
)
MAINTENANCE_TYPES = {"chore", "test", "ci"}
STABLE_VERSION = re.compile(r"v(0|[1-9][0-9]*)\.(0|[1-9][0-9]*)\.(0|[1-9][0-9]*)")


@dataclass(frozen=True)
class Commit:
    sha: str
    subject: str

    @property
    def release_increment(self) -> int | None:
        """Return 3 for major, 2 for minor, 1 for patch, or None for maintenance."""
        header = CONVENTIONAL_HEADER.match(self.subject)
        if not header:
            return 1
        if header["breaking"]:
            return 3
        commit_type = header["type"].lower()
        if commit_type in MAINTENANCE_TYPES:
            return None
        return 2 if commit_type == "feat" else 1


@dataclass(frozen=True)
class ComponentReport:
    component: str
    previous_tag: str | None
    commits: list[Commit]

    @property
    def status(self) -> str:
        if self.previous_tag is None:
            return "Unreleased"
        return "Changes need review" if self.commits else "Unchanged"

    @property
    def next_tag(self) -> str | None:
        if not self.commits:
            return None
        if self.previous_tag is None:
            return f"{self.component}/v0.1.0"
        version = tag_version(self.component, self.previous_tag)
        assert version is not None, self.previous_tag
        major, minor, patch = version
        increment = max(commit.release_increment or 0 for commit in self.commits)
        if increment == 3:
            return f"{self.component}/v{major + 1}.0.0"
        if increment == 2:
            return f"{self.component}/v{major}.{minor + 1}.0"
        return f"{self.component}/v{major}.{minor}.{patch + 1}"


def git(root: Path, *arguments: str) -> str:
    # Skip optional writes such as refreshing the index during a status check.
    return subprocess.run(
        ["git", "--no-optional-locks", "-C", str(root), *arguments],
        check=True,
        capture_output=True,
        encoding="utf-8",
        errors="replace",
    ).stdout


def single_labeler_rule(rules: object, key: str, label: str) -> object:
    if isinstance(rules, list) and len(cast(list[object], rules)) == 1:
        rule = cast(list[object], rules)[0]
        if isinstance(rule, dict):
            mapping = cast(dict[object, object], rule)
            if list(mapping) == [key]:
                return mapping[key]
    raise ValueError(f"{label}: expected a single {key} rule")


def read_components(root: Path, head: str) -> dict[str, list[str]]:
    """Read the labeler mapping from the same revision as the release history."""
    try:
        config = git(root, "show", f"{head}:{LABELER_PATH}")
    except subprocess.CalledProcessError as error:
        raise ValueError(f"{LABELER_PATH} is required at {head[:12]}") from error
    try:
        contents = cast(object, yaml.safe_load(config))
    except yaml.YAMLError as error:
        raise ValueError(f"{LABELER_PATH}: {error}") from error
    if not isinstance(contents, dict) or not contents:
        raise ValueError(f"{LABELER_PATH} must define at least one component")

    components: dict[str, list[str]] = {}
    for label, rules in cast(dict[object, object], contents).items():
        if not isinstance(label, str):
            raise ValueError("Label names must be strings")
        # Workflow labels such as bazel.yml use bazel/ as the tag prefix.
        name = re.sub(r"\.[^.]{1,3}\Z", "", label)
        validate_component(root, name)
        if name in components:
            raise ValueError(f"{label}: duplicate release component {name!r}")

        changed_files = single_labeler_rule(rules, "changed-files", label)
        paths = single_labeler_rule(changed_files, "any-glob-to-any-file", label)
        if isinstance(paths, str):
            paths = [paths]
        if not isinstance(paths, list) or not paths:
            raise ValueError(f"{label}: expected one or more component paths")
        component_paths: list[str] = []
        for path in cast(list[object], paths):
            if (
                not isinstance(path, str)
                or not path.strip()
                or any(part in {"", ".", ".."} for part in path.split("/"))
                or re.search(r"[?\[\]{}\\]|[!+@*]\(", path)
                or any("**" in part and part != "**" for part in path.split("/"))
            ):
                raise ValueError(f"{label}: unsupported component path {path!r}")
            component_paths.append(path)
        components[name] = component_paths
    return components


def validate_component(root: Path, name: str) -> None:
    try:
        if not name or name.startswith("-"):
            raise ValueError(f"Invalid release component {name!r}")
        git(root, "check-ref-format", f"refs/tags/{name}/v0.1.0")
    except subprocess.CalledProcessError as error:
        raise ValueError(f"Invalid release component {name!r}") from error


def tag_version(component: str, tag: str) -> Version | None:
    prefix = f"{component}/"
    if not tag.startswith(prefix):
        return None
    match = STABLE_VERSION.fullmatch(tag.removeprefix(prefix))
    if match:
        return int(match[1]), int(match[2]), int(match[3])
    return None


def component_report(
    root: Path, head: str, tags: list[str], component: str, paths: list[str]
) -> ComponentReport:
    versions = [
        (version, tag)
        for tag in tags
        if (version := tag_version(component, tag)) is not None
    ]
    previous_tag = max(versions)[1] if versions else None
    revision = f"refs/tags/{previous_tag}..{head}" if previous_tag else head
    # Use Git pathspecs so historical changes include deleted files as well.
    pathspecs = [f":(glob){path}" for path in paths]
    history = git(root, "log", "-z", "--format=%H%x00%s", revision, "--", *pathspecs)
    fields = history.split("\0")[:-1]
    commits = [
        Commit(sha, subject)
        for sha, subject in zip(fields[::2], fields[1::2], strict=True)
    ]
    return ComponentReport(
        component, previous_tag, [c for c in commits if c.release_increment is not None]
    )


def reachable_tags(root: Path, head: str) -> list[str]:
    return git(root, "tag", "--list", "--merged", head).splitlines()
