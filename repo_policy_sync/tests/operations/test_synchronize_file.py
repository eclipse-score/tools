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
from repo_policy_sync.src.errors import PolicyError, RepoPolicySyncError
from repo_policy_sync.src.models import Policy, SynchronizeFile
from repo_policy_sync.src.operations import apply, describe_changes
from repo_policy_sync.src.policy import load_policy


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
    assert target.stat().st_mode & stat.S_IXUSR
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
    assert target.stat().st_mode & stat.S_IXUSR


@pytest.mark.parametrize(
    ("executable", "mode", "expected_mode"),
    [
        (True, 0o644, 0o744),
        (True, 0o641, 0o741),
        (True, 0o650, 0o750),
        (True, 0o744, 0o744),
        (True, 0o755, 0o755),
        (False, 0o644, 0o644),
        (False, 0o641, 0o641),
        (False, 0o755, 0o755),
    ],
)
def test_synchronize_file_ensures_owner_execution_and_preserves_other_permissions(
    fake_repo: Path,
    executable: bool,
    mode: int,
    expected_mode: int,
) -> None:
    """Group/other execute bits cannot substitute for the owner's execute bit."""
    content = b"already current\n"
    target = fake_repo / ".devcontainer/run-tool"
    target.parent.mkdir()
    target.write_bytes(content)
    target.chmod(mode)
    policy = _policy(fake_repo, content, executable=executable)

    evaluation = evaluate_policy(fake_repo, policy)
    apply_policy(fake_repo, policy)

    assert bool(evaluation.changes) == (mode != expected_mode)
    assert target.read_bytes() == content
    assert stat.S_IMODE(target.stat().st_mode) == expected_mode
    assert apply_policy(fake_repo, policy).changes == ()


def test_synchronize_file_rejects_a_directory_destination(fake_repo: Path) -> None:
    (fake_repo / ".devcontainer/run-tool").mkdir(parents=True)

    with pytest.raises(RepoPolicySyncError, match="must be a regular file"):
        evaluate_policy(fake_repo, _policy(fake_repo))


@pytest.fixture
def source_policy_path(fake_repo: Path) -> Path:
    policy_directory = fake_repo.parent / "policy"
    policy_directory.mkdir()
    (policy_directory / "run-tool").write_bytes(b"managed launcher\n")
    policy_path = policy_directory / "policy.yml"
    policy_path.write_text(
        "title: Example\n"
        "ensure:\n"
        "  - type: synchronize_file\n"
        "    path: .devcontainer/run-tool\n"
        "    source: run-tool\n",
        encoding="utf-8",
    )
    return policy_path


def _invalidate_source(
    source_file: Path, problem: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    if problem == "unreadable":
        source_path_class = type(source_file)
        original_read_bytes = source_path_class.read_bytes

        def read_bytes(path: Path) -> bytes:
            if path == source_file:
                raise PermissionError(13, "Permission denied", str(path))
            return original_read_bytes(path)

        # Inject the read error so this test also works when running as root.
        monkeypatch.setattr(source_path_class, "read_bytes", read_bytes)
        return

    source_file.unlink()
    if problem == "directory":
        source_file.mkdir()
    elif problem == "symlink":
        outside = source_file.parent.parent / "outside-launcher"
        outside.write_bytes(b"outside the policy directory\n")
        source_file.symlink_to(outside)


_SOURCE_ERRORS = [
    pytest.param("missing", "must be a regular file", id="missing"),
    pytest.param("directory", "must be a regular file", id="directory"),
    pytest.param("symlink", "must not contain a symbolic link", id="symlink"),
    pytest.param("unreadable", "Permission denied", id="unreadable"),
]


@pytest.mark.parametrize(("problem", "message"), _SOURCE_ERRORS)
def test_synchronize_file_loading_reports_policy_and_invalid_source(
    source_policy_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    problem: str,
    message: str,
) -> None:
    source_file = source_policy_path.parent / "run-tool"
    _invalidate_source(source_file, problem, monkeypatch)

    with pytest.raises(PolicyError, match=message) as error:
        load_policy(source_policy_path)

    assert f"policy {source_policy_path}:" in str(error.value)
    assert "synchronize_file source" in str(error.value)
    assert str(source_file) in str(error.value)


@pytest.mark.parametrize(("problem", "message"), _SOURCE_ERRORS)
@pytest.mark.parametrize(
    "execute", [describe_changes, apply], ids=["describe", "apply"]
)
def test_synchronize_file_revalidates_source_after_loading_before_changing_target(
    fake_repo: Path,
    source_policy_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    problem: str,
    message: str,
    execute,
) -> None:
    policy = load_policy(source_policy_path)
    target = fake_repo / ".devcontainer/run-tool"
    target.parent.mkdir()
    target.write_bytes(b"consumer content\n")
    source_file = source_policy_path.parent / "run-tool"
    _invalidate_source(source_file, problem, monkeypatch)

    with pytest.raises(RepoPolicySyncError, match=message) as error:
        execute(fake_repo, policy.ensure[0])

    assert type(error.value) is RepoPolicySyncError
    assert "synchronize_file source" in str(error.value)
    assert str(source_file) in str(error.value)
    assert target.read_bytes() == b"consumer content\n"
