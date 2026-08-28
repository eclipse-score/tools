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

import pytest

from repo_cache.src.command import run_command
from repo_cache.src.errors import CommandError, redact_sensitive_text


def test_gh_command_failures_are_actionable(monkeypatch) -> None:
    def run(*_: object, **__: object) -> None:
        raise subprocess.CalledProcessError(
            1, ["gh", "auth", "status"], stderr="authentication failed\n"
        )

    monkeypatch.setattr("repo_cache.src.command.subprocess.run", run)

    with pytest.raises(CommandError, match="gh auth status: authentication failed"):
        run_command(["gh", "auth", "status"])


def test_gh_command_failures_redact_credentials(monkeypatch) -> None:
    token = "ghp_secret_value_12345"

    def run(*_: object, **__: object) -> None:
        raise subprocess.CalledProcessError(
            1,
            ["gh", "auth", "status"],
            stderr=f"Authorization: Bearer {token}\n",
        )

    monkeypatch.setattr("repo_cache.src.command.subprocess.run", run)

    with pytest.raises(CommandError) as error:
        run_command(["gh", "auth", "status"])

    assert token not in str(error.value)
    assert "[REDACTED]" in str(error.value)


def test_redact_sensitive_text_covers_environment_and_url_credentials(
    monkeypatch,
) -> None:
    monkeypatch.setenv("GH_TOKEN", "environment-secret-value")

    redacted = redact_sensitive_text(
        "GH_TOKEN=environment-secret-value "
        "https://user:password-value@example.test/repo "
        "--token ghp_secret_value_12345"
    )

    assert "environment-secret-value" not in redacted
    assert "password-value" not in redacted
    assert "ghp_secret_value_12345" not in redacted
    assert redacted.count("[REDACTED]") == 3


def test_missing_gh_command_is_actionable(monkeypatch) -> None:
    def run(*_: object, **__: object) -> None:
        raise FileNotFoundError

    monkeypatch.setattr("repo_cache.src.command.subprocess.run", run)

    with pytest.raises(CommandError, match="required command is unavailable: gh"):
        run_command(["gh", "auth", "status"])
