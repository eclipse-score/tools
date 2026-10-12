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

from repo_policy_sync.src.engine import apply_policy, evaluate_policy
from repo_policy_sync.src.errors import RepoPolicySyncError
from repo_policy_sync.src.models import Change, Policy, RemoveFile


def test_remove_file_removes_an_existing_file(fake_repo: Path) -> None:
    target = fake_repo / "legacy"
    target.write_text("legacy\n")
    policy = Policy("example", "Example", None, None, (RemoveFile(Path("legacy")),))

    evaluation = apply_policy(fake_repo, policy)

    assert evaluation.changes == (Change(Path("legacy"), "remove file"),)
    assert not target.exists()
    assert apply_policy(fake_repo, policy).changes == ()


def test_remove_file_accepts_a_missing_file(fake_repo: Path) -> None:
    policy = Policy("example", "Example", None, None, (RemoveFile(Path("legacy")),))

    assert evaluate_policy(fake_repo, policy).changes == ()


def test_remove_file_refuses_to_remove_a_directory(fake_repo: Path) -> None:
    (fake_repo / "legacy").mkdir()
    policy = Policy("example", "Example", None, None, (RemoveFile(Path("legacy")),))

    with pytest.raises(RepoPolicySyncError, match="refusing to remove directory"):
        evaluate_policy(fake_repo, policy)


def test_remove_file_removes_a_dangling_symlink(fake_repo: Path) -> None:
    link = fake_repo / "legacy"
    link.symlink_to("missing-file")
    policy = Policy("example", "Example", None, None, (RemoveFile(Path("legacy")),))

    evaluation = apply_policy(fake_repo, policy)

    assert evaluation.changes == (Change(Path("legacy"), "remove file"),)
    assert not link.exists()
    assert not link.is_symlink()
