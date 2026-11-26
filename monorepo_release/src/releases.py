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

"""Read module path rules and compare Git commits with stable release tags."""

import re
import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import cast, override

import yaml
from yaml.constructor import ConstructorError
from yaml.nodes import MappingNode

type Version = tuple[int, int, int]

LABELER_PATH = ".github/labeler.yml"
CONVENTIONAL_HEADER = re.compile(
    r"^(?P<type>[a-z]+)(?:\([^()\r\n]+\))?(?P<breaking>!)?:[ \t]+", re.IGNORECASE
)
MAINTENANCE_TYPES = {"chore", "test", "ci"}
STABLE_VERSION = re.compile(r"v(0|[1-9][0-9]*)\.(0|[1-9][0-9]*)\.(0|[1-9][0-9]*)")
INITIAL_VERSION = "v0.0.1"


class LabelerLoader(yaml.SafeLoader):
    @override
    def construct_mapping(
        self, node: MappingNode, deep: bool = False
    ) -> dict[object, object]:
        mapping = cast(dict[object, object], super().construct_mapping(node, deep))
        if len(mapping) != len(node.value):
            raise ConstructorError(
                problem="duplicate mapping key", problem_mark=node.start_mark
            )
        return mapping


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
class ModuleReport:
    module: str
    previous_tag: str | None
    comparison_tag: str | None
    commits: list[Commit]
    is_new: bool

    @property
    def status(self) -> str:
        if self.is_new:
            return "New"
        if self.comparison_tag is None:
            return "Unreleased"
        return "Changed" if self.commits else "Unchanged"

    @property
    def next_tag(self) -> str | None:
        if not self.commits:
            return None
        if self.previous_tag is None:
            return f"{self.module}/{INITIAL_VERSION}"
        version = tag_version(self.previous_tag, module=self.module)
        assert version is not None, self.previous_tag
        major, minor, patch = version
        increment = max(commit.release_increment or 0 for commit in self.commits)
        if increment == 3:
            return f"{self.module}/v{major + 1}.0.0"
        if increment == 2:
            return f"{self.module}/v{major}.{minor + 1}.0"
        return f"{self.module}/v{major}.{minor}.{patch + 1}"


def git(root: Path, *arguments: str) -> str:
    # Skip optional writes such as refreshing the index during a status check.
    return subprocess.run(
        ["git", "--no-optional-locks", "-C", str(root), *arguments],
        check=True,
        capture_output=True,
        encoding="utf-8",
        errors="replace",
    ).stdout


def fetch(root: Path, *target: str, config: tuple[str, ...] = ()) -> None:
    shallow = git(root, "rev-parse", "--is-shallow-repository").strip() == "true"
    git(
        root,
        *config,
        "fetch",
        "--tags",
        "--no-prune",
        "--no-recurse-submodules",
        *(("--unshallow",) if shallow else ()),
        *target,
    )
    if shallow and git(root, "rev-parse", "--is-shallow-repository").strip() == "true":
        raise ValueError("Fetch the full Git history before comparing releases.")


def single_labeler_rule(rules: object, key: str, label: str) -> object:
    if isinstance(rules, list) and len(cast(list[object], rules)) == 1:
        rule = cast(list[object], rules)[0]
        if isinstance(rule, dict):
            mapping = cast(dict[object, object], rule)
            if list(mapping) == [key]:
                return mapping[key]
    raise ValueError(f"{label}: expected a single {key} rule")


def read_modules(root: Path, head: str) -> dict[str, list[str]]:
    """Read the labeler mapping from the same revision as the release history."""
    try:
        config = git(root, "show", f"{head}:{LABELER_PATH}")
    except subprocess.CalledProcessError as error:
        raise ValueError(f"{LABELER_PATH} is required at {head[:12]}") from error
    try:
        contents = cast(object, yaml.load(config, Loader=LabelerLoader))
    except yaml.YAMLError as error:
        raise ValueError(f"{LABELER_PATH}: {error}") from error
    if not isinstance(contents, dict) or not contents:
        raise ValueError(f"{LABELER_PATH} must define at least one module")

    modules: dict[str, list[str]] = {}
    for label, rules in cast(dict[object, object], contents).items():
        if not isinstance(label, str):
            raise ValueError("Label names must be strings")
        # Workflow labels such as bazel.yml use bazel/ as the tag prefix.
        name = re.sub(r"\.[^.]{1,3}\Z", "", label)
        validate_module(root, name)
        if name in modules:
            raise ValueError(f"{label}: duplicate release module {name!r}")

        changed_files = single_labeler_rule(rules, "changed-files", label)
        paths = single_labeler_rule(changed_files, "any-glob-to-any-file", label)
        if isinstance(paths, str):
            paths = [paths]
        if not isinstance(paths, list) or not paths:
            raise ValueError(f"{label}: expected one or more module paths")
        module_paths: list[str] = []
        for path in cast(list[object], paths):
            if (
                not isinstance(path, str)
                or not path.strip()
                or path.startswith(("!", "#"))
                or any(part in {"", ".", ".."} for part in path.split("/"))
                or re.search(r"[?\[\]{}\\]|[!+@*]\(", path)
                or any("**" in part and part != "**" for part in path.split("/"))
            ):
                raise ValueError(f"{label}: unsupported module path {path!r}")
            module_paths.append(path)
        modules[name] = module_paths
    return modules


def validate_module(root: Path, name: str) -> None:
    try:
        if not name or name.startswith("-"):
            raise ValueError(f"Invalid release module {name!r}")
        git(root, "check-ref-format", f"refs/tags/{name}/{INITIAL_VERSION}")
    except subprocess.CalledProcessError as error:
        raise ValueError(f"Invalid release module {name!r}") from error


def tag_version(tag: str, *, module: str | None = None) -> Version | None:
    if module is not None:
        prefix = f"{module}/"
        if not tag.startswith(prefix):
            return None
        tag = tag.removeprefix(prefix)
    match = STABLE_VERSION.fullmatch(tag)
    if match:
        return int(match[1]), int(match[2]), int(match[3])
    return None


def latest_stable_tag(tags: list[str], *, module: str | None = None) -> str | None:
    versions = [
        (version, tag)
        for tag in tags
        if (version := tag_version(tag, module=module)) is not None
    ]
    return max(versions)[1] if versions else None


def path_pattern(path: str) -> str:
    segments = path.split("/")
    parts: list[str] = []
    for index, segment in enumerate(segments):
        last = index == len(segments) - 1
        if segment == "**":
            parts.append(".*" if last else "(?:[^/]+/)*")
        else:
            parts.append(re.escape(segment).replace(r"\*", "[^/]*"))
            if not last:
                parts.append("/")
    return "".join(parts)


def has_matching_files(root: Path, revision: str, matcher: re.Pattern[str]) -> bool:
    paths = git(root, "ls-tree", "-r", "-z", "--name-only", revision)
    return any(
        matcher.fullmatch(path) is not None for path in paths.split("\0") if path
    )


def module_report(
    root: Path, head: str, tags: list[str], module: str, paths: list[str]
) -> ModuleReport:
    previous_tag = latest_stable_tag(tags, module=module)
    comparison_tag = previous_tag or latest_stable_tag(tags)
    revision = f"refs/tags/{comparison_tag}..{head}" if comparison_tag else head
    # Let Git prune unrelated history, then match the emitted file names.
    # A Git pathspec for an exact file name can also include its descendants.
    pathspecs = [f":(glob){path}" for path in paths]
    history = git(
        root,
        "log",
        "-z",
        "--format=%x00%H%x00%s",
        "--name-only",
        "--no-merges",
        "--no-renames",
        "--no-notes",
        "--no-follow",
        "--no-show-signature",
        "--root",
        revision,
        "--",
        *pathspecs,
    )
    matcher = re.compile("|".join(path_pattern(path) for path in paths), re.DOTALL)
    commits: list[Commit] = []
    # An empty field marks each header. Git prefixes the first path with a newline.
    fields = iter(history.split("\0")[1:])
    for sha in fields:
        commit = Commit(sha, next(fields))
        path = next(fields).removeprefix("\n")
        matched = False
        while path:
            matched = matched or matcher.fullmatch(path) is not None
            path = next(fields)
        if matched and commit.release_increment is not None:
            commits.append(commit)
    is_new = (
        previous_tag is None
        and comparison_tag is not None
        and not has_matching_files(root, f"refs/tags/{comparison_tag}", matcher)
        and has_matching_files(root, head, matcher)
    )
    return ModuleReport(module, previous_tag, comparison_tag, commits, is_new)


def reachable_tags(root: Path, head: str) -> list[str]:
    return git(root, "tag", "--list", "--merged", head).splitlines()
