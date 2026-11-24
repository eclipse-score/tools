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

import subprocess
from pathlib import Path

import pytest

from repo_cache.src import command
from repo_cache.src.checkout import sync_default_branch


def git(*args: str, cwd: Path) -> str:
    return subprocess.run(
        ["git", *args], cwd=cwd, check=True, capture_output=True, text=True
    ).stdout.strip()


def commit(work: Path, message: str) -> str:
    git("commit", "--allow-empty", "-m", message, cwd=work)
    return git("rev-parse", "HEAD", cwd=work)


@pytest.fixture
def origin(tmp_path: Path) -> tuple[Path, Path]:
    """A local origin that serves partial clones, plus a working copy pushing to it."""

    remote = tmp_path / "origin.git"
    git("init", "--bare", "--initial-branch=main", str(remote), cwd=tmp_path)
    git("config", "uploadpack.allowFilter", "true", cwd=remote)
    work = tmp_path / "work"
    git("init", "--initial-branch=main", str(work), cwd=tmp_path)
    git("config", "user.email", "test@example.com", cwd=work)
    git("config", "user.name", "Test", cwd=work)
    git("remote", "add", "origin", str(remote), cwd=work)
    return remote, work


@pytest.fixture
def local_gh(monkeypatch: pytest.MonkeyPatch, origin: tuple[Path, Path]) -> None:
    """Serve `gh` lookups from the local origin; run every `git` command for real."""

    remote, _ = origin

    def run(cmd: list[str], *, cwd: Path | None = None) -> str:
        if cmd[:3] == ["gh", "repo", "clone"]:
            return command.run_command(
                ["git", "clone", *cmd[6:], f"file://{remote}", cmd[4]]
            )
        if cmd[:3] == ["gh", "repo", "view"] or cmd[-3:] == [
            "remote",
            "get-url",
            "origin",
        ]:
            # The remote check compares GitHub identities; a file URL has none.
            return "https://github.com/owner/repository\n"
        if cmd[:2] == ["gh", "api"]:
            return "1\n"
        return command.run_command(cmd, cwd=cwd)

    monkeypatch.setattr("repo_cache.src.checkout.run_command", run)


def sync(destination: Path, *, history: bool) -> None:
    sync_default_branch(
        repository="owner/repository",
        branch="main",
        destination=destination,
        history=history,
    )


@pytest.mark.usefixtures("local_gh")
def test_history_checkout_tracks_all_branches_and_tags(
    origin: tuple[Path, Path], tmp_path: Path
) -> None:
    _, work = origin
    first = commit(work, "first")
    git("tag", "-a", "v1.0.0", "-m", "release", cwd=work)
    commit(work, "second")
    git("push", "origin", "main", "--tags", cwd=work)
    destination = tmp_path / "cache" / "owner" / "repository"

    sync(destination, history=True)

    assert git("rev-parse", "v1.0.0^{commit}", cwd=destination) == first
    assert git("rev-list", "--count", "v1.0.0..HEAD", cwd=destination) == "1"
    assert git("config", "remote.origin.partialclonefilter", cwd=destination) == (
        "blob:none"
    )

    third = commit(work, "third")
    git("tag", "v1.1.0", cwd=work)
    git("checkout", "-b", "release", cwd=work)
    fourth = commit(work, "fourth")
    git("push", "origin", "main", "release", "--tags", cwd=work)
    git("push", "origin", "--delete", "v1.0.0", cwd=work)

    sync(destination, history=True)

    assert git("rev-parse", "HEAD", cwd=destination) == third
    assert git("rev-parse", "origin/release", cwd=destination) == fourth
    assert git("rev-list", "--count", f"{first}..HEAD", cwd=destination) == "2"
    assert git("tag", "--list", cwd=destination) == "v1.1.0"


@pytest.mark.usefixtures("local_gh")
def test_shallow_checkout_gains_history_and_keeps_it(
    origin: tuple[Path, Path], tmp_path: Path
) -> None:
    _, work = origin
    first = commit(work, "first")
    git("tag", "v1.0.0", cwd=work)
    git("push", "origin", "main", "--tags", cwd=work)
    destination = tmp_path / "cache" / "owner" / "repository"

    sync(destination, history=False)
    second = commit(work, "second")
    git("push", "origin", "main", cwd=work)
    sync(destination, history=False)

    assert git("rev-parse", "HEAD", cwd=destination) == second
    assert git("rev-list", "--count", "HEAD", cwd=destination) == "1"

    sync(destination, history=True)

    assert git("rev-parse", "v1.0.0", cwd=destination) == first
    assert git("rev-list", "--count", "v1.0.0..HEAD", cwd=destination) == "1"

    third = commit(work, "third")
    git("push", "origin", "main", cwd=work)

    sync(destination, history=False)

    assert git("rev-parse", "HEAD", cwd=destination) == third
    assert git("rev-list", "--count", "v1.0.0..HEAD", cwd=destination) == "2"
