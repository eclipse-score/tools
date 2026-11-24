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

import json
from pathlib import Path
import shlex
import subprocess
import sys

import pytest

from repo_policy_sync.src.engine import _run_after_apply_command
from repo_policy_sync.src.github import GitHubCli


@pytest.mark.parametrize("entry_point", ["follow_up", "pre_commit"])
def test_repository_commands_inherit_authentication_and_tool_configuration(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, entry_point: str
) -> None:
    """Real child processes retain Git helpers, URL rewrites, tokens, and cache settings."""

    home = tmp_path / "user home"
    home.mkdir()
    bazelrc = home / ".bazelrc"
    bazel_settings = 'common --repository_cache="/shared/downloads"\n'
    bazelrc.write_text(bazel_settings)
    helper = tmp_path / "credential helper.py"
    helper.write_text(
        "import os\n"
        "print('username=x-access-token')\n"
        "print('password=' + os.environ['GH_TOKEN'])\n"
    )
    config = home / ".gitconfig"
    for key, value in (
        ("user.name", "Example User"),
        ("credential.helper", "!" + shlex.join([sys.executable, str(helper)])),
        ("url.https://github.com/.insteadOf", "https://example.invalid/"),
    ):
        subprocess.run(["git", "config", "--file", str(config), key, value], check=True)
    expected = {
        "HOME": str(home),
        "XDG_CONFIG_HOME": str(home / ".config"),
        "GH_CONFIG_DIR": str(home / ".gh"),
        "GIT_CONFIG_GLOBAL": str(config),
        "GIT_CONFIG_NOSYSTEM": "1",
        "GH_TOKEN": "dummy-token",
        "GITHUB_TOKEN": "dummy-token-too",
        "BAZELISK_HOME": str(tmp_path / "bazelisk"),
        "TOOL_SPECIFIC_SETTING": "configured",
    }
    for key, value in expected.items():
        monkeypatch.setenv(key, value)
    monkeypatch.setenv("GIT_TERMINAL_PROMPT", "1")
    expected["GIT_TERMINAL_PROMPT"] = "0"
    checkout = tmp_path / "checkout"
    checkout.mkdir()
    (checkout / ".pre-commit-config.yaml").write_text("repos: []\n")
    command = (
        sys.executable,
        "-c",
        r"""
import json
import os
import subprocess
import sys
from pathlib import Path

for key, value in json.loads(sys.argv[1]).items():
    assert os.environ.get(key) == value, key
assert Path.home().joinpath('.bazelrc').read_text() == sys.argv[2]
identity = subprocess.run(
    ['git', 'config', '--global', '--get', 'user.name'],
    check=True, capture_output=True, text=True,
)
assert identity.stdout.strip() == 'Example User'
url = subprocess.run(
    ['git', 'ls-remote', '--get-url', 'https://example.invalid/example/private.git'],
    check=True, capture_output=True, text=True,
)
assert url.stdout.strip() == 'https://github.com/example/private.git'
# Git invokes a real credential helper; no network request is made.
credentials = subprocess.run(
    ['git', 'credential', 'fill'], input='protocol=https\nhost=github.com\n\n',
    check=True, capture_output=True, text=True,
)
assert 'password=dummy-token' in credentials.stdout
""",
        json.dumps(expected),
        bazel_settings,
    )
    if entry_point == "follow_up":
        _run_after_apply_command(checkout, command)
    else:
        # Replace only the pre-commit executable with a real child process;
        # the runner still builds the environment passed to repository hooks.
        def run_pre_commit(args: list[str], *, cwd: Path, env: dict[str, str]):
            assert args == ["pre-commit", "run", "--all-files"]
            subprocess.run(command, cwd=cwd, env=env, check=True, capture_output=True)
            return ""

        monkeypatch.setattr(GitHubCli, "_run", staticmethod(run_pre_commit))
        assert GitHubCli().run_pre_commit(checkout=checkout)

    assert bazelrc.read_text() == bazel_settings
    assert home.is_dir()
