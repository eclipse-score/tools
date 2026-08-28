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

import pytest

from repo_cache.src.errors import CommandError
from repo_cache.src.github import ensure_authenticated, list_repositories
from repo_cache.src.models import Repository


def test_ensure_authenticated_runs_gh_auth_status(monkeypatch) -> None:
    commands: list[list[str]] = []

    def record(command: list[str]) -> str:
        commands.append(command)
        return ""

    monkeypatch.setattr("repo_cache.src.github.run_command", record)

    ensure_authenticated()

    assert commands == [["gh", "auth", "status"]]


def test_list_repositories_reads_all_paginated_results(monkeypatch) -> None:
    commands: list[list[str]] = []

    def record(command: list[str]) -> str:
        commands.append(command)
        return (
            '[[{"name":"first","default_branch":"main","archived":false}],'
            '[{"name":"empty","default_branch":null,"archived":true}]]'
        )

    monkeypatch.setattr("repo_cache.src.github.run_command", record)

    repositories = list_repositories(org="eclipse-score")

    assert repositories == (
        Repository("first", "main"),
        Repository("empty", None, archived=True),
    )
    assert commands == [
        ["gh", "api", "--paginate", "--slurp", "/orgs/eclipse-score/repos?per_page=100"]
    ]


@pytest.mark.parametrize(
    "output, message",
    [
        ("not-json", "invalid repository JSON"),
        ("{}", "invalid repository JSON"),
        ("[{}]", "invalid repository JSON"),
        ('[[{"name":"","default_branch":"main"}]]', "without a valid name"),
        ('[[{"name":"repo","default_branch":false}]]', "invalid default branch"),
        ('[[{"name":"repo","archived":"no"}]]', "invalid archived state"),
    ],
)
def test_list_repositories_rejects_invalid_api_payloads(
    monkeypatch, output: str, message: str
) -> None:
    monkeypatch.setattr("repo_cache.src.github.run_command", lambda _: output)

    with pytest.raises(CommandError, match=message):
        list_repositories(org="eclipse-score")
