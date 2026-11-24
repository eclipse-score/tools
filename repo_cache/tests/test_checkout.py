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

from pathlib import Path

import pytest

from repo_cache.src.checkout import restore_synced_default_branch, sync_default_branch
from repo_cache.src.errors import CommandError, EmptyRepositoryError


def test_cached_checkout_rejects_a_different_origin(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    (tmp_path / ".git").mkdir()
    commands: list[list[str]] = []

    def record(command: list[str]) -> str:
        commands.append(command)
        if command[:4] == ["gh", "repo", "view", "owner/repository"]:
            return "https://github.com/owner/repository\n"
        if command[-3:] == ["remote", "get-url", "origin"]:
            return "git@github.com:other/repository.git\n"
        return ""

    monkeypatch.setattr("repo_cache.src.checkout.run_command", record)

    with pytest.raises(CommandError, match="does not match"):
        sync_default_branch(
            repository="owner/repository", branch="main", destination=tmp_path
        )

    assert not any(command[4:5] == ["fetch"] for command in commands)


def test_sync_default_branch_clones_a_missing_checkout(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    destination = tmp_path / "checkout"
    commands: list[list[str]] = []

    def record(command: list[str]) -> str:
        commands.append(command)
        if command[:2] == ["gh", "api"]:
            return "1\n"
        return ""

    monkeypatch.setattr("repo_cache.src.checkout.run_command", record)

    sync_default_branch(
        repository="owner/repository", branch="main", destination=destination
    )

    assert commands == [
        [
            "gh",
            "api",
            "/repos/owner/repository/git/refs?per_page=1",
            "--jq",
            "length",
        ],
        [
            "gh",
            "repo",
            "clone",
            "owner/repository",
            str(destination),
            "--",
            "--depth",
            "1",
            "--branch",
            "main",
        ],
        [
            "git",
            "-C",
            str(destination),
            "update-ref",
            "refs/repo-cache/default",
            "HEAD",
        ],
    ]


def test_sync_default_branch_reports_an_empty_repository(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    destination = tmp_path / "checkout"
    commands: list[list[str]] = []

    def record(command: list[str]) -> str:
        commands.append(command)
        raise CommandError(
            "gh api /repos/owner/empty/git/refs: Git Repository is empty. (HTTP 409)"
        )

    monkeypatch.setattr("repo_cache.src.checkout.run_command", record)

    with pytest.raises(EmptyRepositoryError, match="has no Git references"):
        sync_default_branch(
            repository="owner/empty", branch="main", destination=destination
        )

    assert commands == [
        [
            "gh",
            "api",
            "/repos/owner/empty/git/refs?per_page=1",
            "--jq",
            "length",
        ]
    ]


def test_sync_default_branch_preserves_a_non_empty_repository_failure(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    destination = tmp_path / "checkout"

    def record(command: list[str]) -> str:
        if command[:2] == ["gh", "api"]:
            return "1\n"
        if command[:3] == ["gh", "repo", "clone"]:
            raise CommandError("gh repo clone: remote branch not found")
        return ""

    monkeypatch.setattr("repo_cache.src.checkout.run_command", record)

    with pytest.raises(CommandError, match="remote branch not found"):
        sync_default_branch(
            repository="owner/non-empty", branch="main", destination=destination
        )


@pytest.mark.parametrize(
    ("refs_count", "expected_exception", "message"),
    [
        ("0\n", EmptyRepositoryError, "has no Git references"),
        ("1\n", CommandError, "fetch failed"),
    ],
)
def test_sync_default_branch_handles_a_cached_fetch_failure(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    refs_count: str,
    expected_exception: type[Exception],
    message: str,
) -> None:
    destination = tmp_path / "checkout"
    (destination / ".git").mkdir(parents=True)
    commands: list[list[str]] = []

    def record(command: list[str]) -> str:
        commands.append(command)
        if command[:4] == ["gh", "repo", "view", "owner/repository"]:
            return "https://github.com/owner/repository\n"
        if command[-3:] == ["remote", "get-url", "origin"]:
            return "git@github.com:owner/repository.git\n"
        if command[-1] == "--is-shallow-repository":
            return "true\n"
        if command[3:4] == ["fetch"]:
            raise CommandError("git fetch failed")
        if command[:2] == ["gh", "api"]:
            return refs_count
        return ""

    monkeypatch.setattr("repo_cache.src.checkout.run_command", record)

    with pytest.raises(expected_exception, match=message):
        sync_default_branch(
            repository="owner/repository", branch="main", destination=destination
        )

    assert commands[-1][:2] == ["gh", "api"]


def test_restore_synced_default_branch_never_fetches(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    commands: list[list[str]] = []

    def record(command: list[str]) -> str:
        commands.append(command)
        return ""

    monkeypatch.setattr("repo_cache.src.checkout.run_command", record)

    restore_synced_default_branch(checkout=tmp_path)

    assert commands == [
        [
            "git",
            "-C",
            str(tmp_path),
            "checkout",
            "--detach",
            "--force",
            "refs/repo-cache/default",
        ],
        ["git", "-C", str(tmp_path), "clean", "-fdx"],
    ]
