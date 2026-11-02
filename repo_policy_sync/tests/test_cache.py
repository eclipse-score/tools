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
import shlex

import pytest

from repo_policy_sync.src.engine import _run_after_apply_command
from repo_policy_sync.src.github import GitHubCli


@pytest.mark.parametrize("cache_enabled", [False, True])
def test_follow_up_commands_and_hooks_share_only_explicit_cache_configuration(
    tmp_path: Path, monkeypatch, cache_enabled: bool
) -> None:
    """Policy commands and hooks find the shared caches from fresh homes.

    Both must import the selected cache configuration without copying the
    runner's personal Bazel configuration or passing GitHub token variables.
    """

    user_home = tmp_path / "user-home"
    user_home.mkdir()
    (user_home / ".bazelrc").write_text("build --remote_header=secret\n")
    cache_rc = tmp_path / "cache configuration.bazelrc"
    cache_rc.write_text(f'common --repository_cache="{tmp_path / "downloads"}"\n')
    bazelisk_home = tmp_path / "bazelisk"
    monkeypatch.setenv("HOME", str(user_home))
    monkeypatch.setenv("BAZELRC", str(user_home / ".bazelrc"))
    monkeypatch.setenv("GH_TOKEN", "secret")
    monkeypatch.delenv("REPO_POLICY_SYNC_BAZELRC", raising=False)
    monkeypatch.delenv("BAZELISK_HOME", raising=False)
    if cache_enabled:
        # Cache paths are relative to where policy sync starts, even though
        # the commands themselves run in a different repository directory.
        monkeypatch.chdir(tmp_path)
        monkeypatch.setenv("REPO_POLICY_SYNC_BAZELRC", cache_rc.name)
        monkeypatch.setenv("BAZELISK_HOME", bazelisk_home.name)
    checkout = tmp_path / "checkout"
    checkout.mkdir()
    (checkout / ".pre-commit-config.yaml").write_text("repos: []\n")
    homes: list[Path] = []

    def observe(command, *, cwd, env, **kwargs):
        assert cwd == checkout
        assert "GH_TOKEN" not in env
        assert "BAZELRC" not in env
        home = Path(env["HOME"])
        assert home != user_home
        homes.append(home)
        rc = home / ".bazelrc"
        if cache_enabled:
            assert shlex.split(rc.read_text()) == ["import", str(cache_rc)]
            assert env["BAZELISK_HOME"] == str(bazelisk_home)
        else:
            assert not rc.exists()
            assert "BAZELISK_HOME" not in env
        return ""

    monkeypatch.setattr("repo_policy_sync.src.engine.subprocess.run", observe)
    monkeypatch.setattr(GitHubCli, "_run", staticmethod(observe))
    monkeypatch.setattr(
        "repo_policy_sync.src.github._copy_global_git_url_rewrites", lambda home: None
    )

    _run_after_apply_command(checkout, ("bazel", "mod", "deps"))
    assert GitHubCli().run_pre_commit(checkout=checkout)

    assert len(set(homes)) == 2
    assert all(not home.exists() for home in homes)
