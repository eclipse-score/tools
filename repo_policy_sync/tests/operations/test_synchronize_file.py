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
import stat

import pytest

from repo_policy_sync.src.engine import apply_policy, evaluate_policy
from repo_policy_sync.src.errors import RepoPolicySyncError
from repo_policy_sync.src.models import Policy, SynchronizeFile


def _policy(
    fake_repo: Path,
    source_content: bytes = b"#!/usr/bin/env bash\nmanaged\n",
    *,
    executable: bool = True,
) -> Policy:
    source_root = fake_repo.parent / "policy"
    source_root.mkdir(exist_ok=True)
    source_file = source_root / "run-tool"
    source_file.write_bytes(source_content)
    operation = SynchronizeFile(
        path=Path(".devcontainer/run-tool"),
        source_root=source_root,
        source_file=source_file,
        executable=executable,
    )
    return Policy(
        id="example",
        title="Example",
        description=None,
        bazel_condition=None,
        ensure=(operation,),
    )


def _is_executable(path: Path) -> bool:
    return bool(path.stat().st_mode & (stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH))


def test_synchronize_file_creates_byte_exact_executable_and_is_idempotent(
    fake_repo: Path,
) -> None:
    source = b"#!/usr/bin/env bash\r\necho exact\r\n"
    policy = _policy(fake_repo, source)

    evaluation = apply_policy(fake_repo, policy)
    target = fake_repo / ".devcontainer/run-tool"

    assert [change.path for change in evaluation.changes] == [
        Path(".devcontainer/run-tool")
    ]
    assert target.read_bytes() == source
    assert _is_executable(target)
    assert apply_policy(fake_repo, policy).changes == ()


def test_synchronize_file_replaces_existing_content_and_sets_executable(
    fake_repo: Path,
) -> None:
    target = fake_repo / ".devcontainer/run-tool"
    target.parent.mkdir()
    target.write_bytes(b"old launcher\n")
    policy = _policy(fake_repo, b"new launcher\r\n")

    apply_policy(fake_repo, policy)

    assert target.read_bytes() == b"new launcher\r\n"
    assert _is_executable(target)


def test_synchronize_file_repairs_executable_bit_without_changing_content(
    fake_repo: Path,
) -> None:
    content = b"already current\n"
    target = fake_repo / ".devcontainer/run-tool"
    target.parent.mkdir()
    target.write_bytes(content)
    policy = _policy(fake_repo, content)

    evaluation = evaluate_policy(fake_repo, policy)
    apply_policy(fake_repo, policy)

    assert evaluation.changes[0].description == "make executable"
    assert target.read_bytes() == content
    assert _is_executable(target)


def test_synchronize_file_rejects_a_directory_destination(fake_repo: Path) -> None:
    (fake_repo / ".devcontainer/run-tool").mkdir(parents=True)

    with pytest.raises(RepoPolicySyncError, match="must not be a directory"):
        evaluate_policy(fake_repo, _policy(fake_repo))
