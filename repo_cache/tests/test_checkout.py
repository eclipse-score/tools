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
from repo_cache.src.errors import CommandError


def test_cached_checkout_rejects_a_different_origin(
    monkeypatch, tmp_path: Path
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
    monkeypatch, tmp_path: Path
) -> None:
    destination = tmp_path / "checkout"
    commands: list[list[str]] = []

    def record(command: list[str]) -> str:
        commands.append(command)
        return ""

    monkeypatch.setattr("repo_cache.src.checkout.run_command", record)

    sync_default_branch(
        repository="owner/repository", branch="main", destination=destination
    )

    assert commands == [
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


def test_restore_synced_default_branch_never_fetches(
    monkeypatch, tmp_path: Path
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
