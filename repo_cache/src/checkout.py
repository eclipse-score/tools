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

"""Disposable, cached Git checkouts synced to a repository's default branch."""

from __future__ import annotations

import re
from pathlib import Path
from urllib.parse import urlparse

from .command import run_command
from .errors import CommandError

_DEFAULT_BRANCH_REF = "refs/repo-cache/default"


def sync_default_branch(*, repository: str, branch: str, destination: Path) -> None:
    """Clone once, then refresh a disposable cached checkout on later runs."""

    if (destination / ".git").is_dir():
        _verify_cached_remote(repository=repository, checkout=destination)
        run_command(
            ["git", "-C", str(destination), "fetch", "--depth", "1", "origin", branch]
        )
        run_command(
            [
                "git",
                "-C",
                str(destination),
                "checkout",
                "--detach",
                "--force",
                "FETCH_HEAD",
            ]
        )
        run_command(["git", "-C", str(destination), "clean", "-fdx"])
        run_command(
            ["git", "-C", str(destination), "update-ref", _DEFAULT_BRANCH_REF, "HEAD"]
        )
        return

    if destination.exists():
        raise CommandError(
            f"checkout cache path exists but is not a Git repository: {destination}"
        )
    destination.parent.mkdir(parents=True, exist_ok=True)
    run_command(
        [
            "gh",
            "repo",
            "clone",
            repository,
            str(destination),
            "--",
            "--depth",
            "1",
            "--branch",
            branch,
        ]
    )
    run_command(
        ["git", "-C", str(destination), "update-ref", _DEFAULT_BRANCH_REF, "HEAD"]
    )


def _verify_cached_remote(*, repository: str, checkout: Path) -> None:
    expected_url = run_command(
        ["gh", "repo", "view", repository, "--json", "url", "--jq", ".url"]
    )
    actual_url = run_command(
        ["git", "-C", str(checkout), "remote", "get-url", "origin"]
    )
    expected = _remote_identity(expected_url)
    actual = _remote_identity(actual_url)
    if expected is None or actual is None or expected != actual:
        raise CommandError(
            f"checkout cache remote does not match requested repository {repository}"
        )


def restore_synced_default_branch(*, checkout: Path) -> None:
    """Discard a preceding operation's local changes without fetching again."""

    run_command(
        [
            "git",
            "-C",
            str(checkout),
            "checkout",
            "--detach",
            "--force",
            _DEFAULT_BRANCH_REF,
        ]
    )
    run_command(["git", "-C", str(checkout), "clean", "-fdx"])


def _remote_identity(value: str) -> tuple[str, str] | None:
    """Normalize HTTPS, SSH, and scp-like Git remotes for safe comparison."""

    value = value.strip()
    if not value:
        return None
    if "://" in value:
        parsed = urlparse(value)
        host = parsed.hostname
        path = parsed.path
    else:
        match = re.match(r"^(?:[^@]+@)?([^:]+):(.+)$", value)
        if match is None:
            return None
        host, path = match.groups()
    if not host or not path:
        return None
    normalized_path = path.strip("/")
    if normalized_path.endswith(".git"):
        normalized_path = normalized_path[:-4]
    if not normalized_path:
        return None
    return host.lower(), normalized_path.lower()
