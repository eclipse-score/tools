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

"""Create drafts against a pinned snapshot of the selected GitHub repository."""

import subprocess
from dataclasses import dataclass
from pathlib import Path
from tempfile import TemporaryDirectory
from urllib.parse import urlsplit

from .releases import (
    component_report,
    git,
    reachable_tags,
    read_components,
    tag_version,
    validate_component,
)
from .reporting import commit_list

TARGET_REF = "refs/heads/release-target"


@dataclass(frozen=True)
class Repository:
    name: str
    branch: str
    url: str

    @property
    def host(self) -> str:
        return urlsplit(self.url).netloc


def gh(root: Path, *arguments: str, input_text: str | None = None) -> str:
    return subprocess.run(
        ["gh", *arguments],
        cwd=root,
        input=input_text,
        check=True,
        capture_output=True,
        encoding="utf-8",
        errors="replace",
    ).stdout.rstrip("\n")


def github_repository(root: Path) -> Repository:
    # GitHub CLI resolves the default repository from the checkout and its gh settings.
    name, branch, url = gh(
        root,
        "repo",
        "view",
        "--json",
        "nameWithOwner,defaultBranchRef,url",
        "--jq",
        "[.nameWithOwner, .defaultBranchRef.name, .url] | @tsv",
    ).split("\t")
    parsed_url = urlsplit(url)
    if not branch or parsed_url.scheme != "https" or not parsed_url.netloc:
        raise ValueError("A GitHub repository with a default branch is required")
    return Repository(name, branch, url)


def existing_draft(root: Path, repository: Repository, component: str) -> str | None:
    rows = gh(
        root,
        "api",
        "--hostname",
        repository.host,
        "--paginate",
        f"repos/{repository.name}/releases?per_page=100",
        "--jq",
        ".[] | select(.draft) | [.tag_name, .html_url] | @tsv",
    )
    for row in rows.splitlines():
        tag, url = row.split("\t")
        if tag_version(component, tag) is not None:
            return url
    return None


def fetch_snapshot(
    root: Path, snapshot: Path, repository: Repository, component: str
) -> str:
    # Borrow existing objects when a full checkout is available. Fetch fresh refs
    # into the temporary bare repository so local branches and tags play no role.
    if git(root, "rev-parse", "--is-shallow-repository").strip() == "true":
        git(root, "init", "--quiet", "--bare", str(snapshot))
    else:
        git(
            root,
            "clone",
            "--quiet",
            "--bare",
            "--shared",
            "--no-tags",
            "--single-branch",
            ".",
            str(snapshot),
        )
    tags = f"refs/tags/{component}/v*"
    git(
        snapshot,
        "-c",
        "credential.helper=",
        "-c",
        "credential.helper=!gh auth git-credential",
        "fetch",
        "--quiet",
        "--no-tags",
        f"{repository.url}.git",
        f"+refs/heads/{repository.branch}:{TARGET_REF}",
        f"{tags}:{tags}",
    )
    return git(snapshot, "rev-parse", "--verify", TARGET_REF).strip()


def draft_release(root: Path, component: str) -> str:
    validate_component(root, component)
    repository = github_repository(root)

    with TemporaryDirectory(prefix="monorepo-release-") as temp:
        snapshot = Path(temp) / "repo.git"
        head = fetch_snapshot(root, snapshot, repository, component)
        components = read_components(snapshot, head)
        if component not in components:
            raise ValueError(
                f"Unknown component {component!r}. Choose from: {', '.join(components)}"
            )

        if url := existing_draft(root, repository, component):
            return f"Existing draft for {component}: {url}"
        report = component_report(
            snapshot,
            head,
            reachable_tags(snapshot, head),
            component,
            components[component],
        )
        next_tag = report.next_tag
        if next_tag is None:
            return f"No changes to draft for {component}."
        # An existing tag would cause GitHub to ignore --target.
        if git(snapshot, "tag", "--list", next_tag).strip():
            raise ValueError(
                f"Tag {next_tag} already exists outside the release history"
            )

    try:
        return gh(
            root,
            "release",
            "create",
            "--repo",
            f"{repository.host}/{repository.name}",
            "--draft",
            "--target",
            head,
            "--title",
            next_tag,
            "--notes-file",
            "-",
            "--",
            next_tag,
            input_text=commit_list(report.commits, repository.url),
        )
    except subprocess.CalledProcessError:
        # The request may have reached GitHub, or another invocation won the race.
        if url := existing_draft(root, repository, component):
            return f"Existing draft for {component}: {url}"
        raise
