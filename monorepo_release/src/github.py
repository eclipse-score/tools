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
    module_report,
    git,
    reachable_tags,
    read_modules,
    tag_version,
    validate_module,
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


def github_repository(root: Path, repo: str | None = None) -> Repository:
    # GitHub CLI resolves the default repository from the checkout and its gh settings.
    name, branch, url = gh(
        root,
        "repo",
        "view",
        "--json",
        "nameWithOwner,defaultBranchRef,url",
        "--jq",
        "[.nameWithOwner, .defaultBranchRef.name, .url] | @tsv",
        *(("--", repo) if repo is not None else ()),
    ).split("\t")
    parsed_url = urlsplit(url)
    if not branch or parsed_url.scheme != "https" or not parsed_url.netloc:
        raise ValueError("A GitHub repository with a default branch is required")
    return Repository(name, branch, url)


def existing_draft(root: Path, repository: Repository, module: str) -> str | None:
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
        if tag_version(tag, module=module) is not None:
            return url
    return None


def fetch_snapshot(
    root: Path, snapshot: Path, repository: Repository, module: str
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
    module_tags = f"refs/tags/{module}/v*"
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
        f"{module_tags}:{module_tags}",
        "refs/tags/v*:refs/tags/v*",
    )
    return git(snapshot, "rev-parse", "--verify", TARGET_REF).strip()


def draft_release(
    root: Path, module: str, *, repo: str | None = None, dry_run: bool = False
) -> str:
    validate_module(root, module)
    repository = github_repository(root, repo)

    with TemporaryDirectory(prefix="monorepo-release-") as temp:
        snapshot = Path(temp) / "repo.git"
        head = fetch_snapshot(root, snapshot, repository, module)
        modules = read_modules(snapshot, head)
        if module not in modules:
            raise ValueError(
                f"Unknown module {module!r}. Choose from: {', '.join(modules)}"
            )

        if url := existing_draft(root, repository, module):
            return f"Existing draft for {module}: {url}"
        report = module_report(
            snapshot,
            head,
            reachable_tags(snapshot, head),
            module,
            modules[module],
        )
        comparison_base = f"Comparison base: {report.comparison_tag or 'full history'}"
        next_tag = report.next_tag
        if next_tag is None:
            message = f"No changes to draft for {module}."
            return f"{message}\n{comparison_base}" if dry_run else message
        # An existing tag would cause GitHub to ignore --target.
        if git(snapshot, "tag", "--list", next_tag).strip():
            raise ValueError(
                f"Tag {next_tag} already exists outside the release history"
            )

    notes = commit_list(report.commits, repository.url, link_commits=False)
    if dry_run:
        return (
            f"Repository: {repository.url}\n"
            f"Tag: {next_tag}\n"
            f"Target: {repository.branch} @ {head}\n"
            f"{comparison_base}\n\n"
            f"{notes.rstrip()}"
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
            input_text=notes,
        )
    except subprocess.CalledProcessError as error:
        # The request may have reached GitHub, or another invocation won the race.
        try:
            url = existing_draft(root, repository, module)
        except (OSError, ValueError, subprocess.CalledProcessError):
            raise error from None
        if url:
            return f"Existing draft for {module}: {url}"
        raise
