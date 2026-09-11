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

from repo_policy_sync.src.config import load_config
from repo_policy_sync.src.errors import RepoPolicySyncError


def test_load_config_resolves_policy_directories_relative_to_config(
    fake_repo: Path,
) -> None:
    config_path = fake_repo / "score-repo-policy-sync.toml"
    config_path.write_text(
        """[score-repo-policy-sync]
org = "eclipse-score"
policies = ["minimum-bazel-version"]
repos = ["reference_integration"]
policy_dirs = ["policies", "shared"]
exclude_policies = ["minimum-bazel-version"]
recreate = true
allow_dirty_pr = true
quiet = true
cache_dir = ".cache/repo-sync"
pull_request_template = "templates/custom-pull-request.md"
sync_workers = 2
policy_workers = 3
""",
        encoding="utf-8",
    )

    config = load_config(config_path)

    assert config.org == "eclipse-score"
    assert config.policies == ("minimum-bazel-version",)
    assert config.repositories == ("reference_integration",)
    assert config.policy_directories == (fake_repo / "policies", fake_repo / "shared")
    assert config.exclude_policies == ("minimum-bazel-version",)
    assert config.recreate is True
    assert config.allow_dirty_pr is True
    assert config.quiet is True
    assert config.cache_directory == fake_repo / ".cache/repo-sync"
    assert (
        config.pull_request_template == fake_repo / "templates/custom-pull-request.md"
    )
    assert config.sync_workers == 2
    assert config.policy_workers == 3


def test_load_config_without_a_default_file_is_empty(
    fake_repo: Path, monkeypatch
) -> None:
    monkeypatch.chdir(fake_repo)

    config = load_config()

    assert config.policy_directories is None
    assert config.exclude_policies == ()


def test_load_config_rejects_unknown_fields(fake_repo: Path) -> None:
    config_path = fake_repo / "config.toml"
    config_path.write_text(
        """[score-repo-policy-sync]
unexpected = true
""",
        encoding="utf-8",
    )

    with pytest.raises(RepoPolicySyncError, match="unexpected fields"):
        load_config(config_path)


def test_load_config_rejects_unknown_sections(fake_repo: Path) -> None:
    config_path = fake_repo / "config.toml"
    config_path.write_text("[wrong-section]\nvalue = true\n", encoding="utf-8")

    with pytest.raises(RepoPolicySyncError, match="unexpected sections"):
        load_config(config_path)


def test_load_config_rejects_non_utf8_input(fake_repo: Path) -> None:
    config_path = fake_repo / "config.toml"
    config_path.write_bytes(b"[score-repo-policy-sync]\norg = '\xff'\n")

    with pytest.raises(RepoPolicySyncError, match="decode configuration.*UTF-8"):
        load_config(config_path)
