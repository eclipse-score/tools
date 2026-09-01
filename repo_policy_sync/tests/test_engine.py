# *******************************************************************************
# Copyright (c) 2026 Contributors to the Eclipse Foundation
#
# See the NOTICE file(s) distributed with this work for additional
# information regarding copyright ownership.
#
# This program and the accompanying materials are made available under the
# terms of the Apache License 2.0 which is available at
# https://www.apache.org/licenses/LICENSE-2.0
#
# SPDX-License-Identifier: Apache-2.0
# *******************************************************************************

from pathlib import Path
import subprocess

import pytest

from repo_policy_sync.src.engine import apply_policy, evaluate_policy
from repo_policy_sync.src.errors import RepoPolicySyncError
from repo_policy_sync.src.models import (
    AfterApplyCommand,
    BazelDependencyCondition,
    BazelCondition,
    Change,
    EnsureLine,
    EnsureMinimumVersion,
    RemoveFile,
    DockerfileImageVersionSource,
    Policy,
    ValueBinding,
    ValueExistsCondition,
)


def _policy() -> Policy:
    return Policy(
        id="score-docs-as-code.gitignore-and-cleanup",
        title="Update docs files",
        description=None,
        bazel_condition=BazelCondition(("score_docs_as_code",)),
        ensure=(
            EnsureLine(Path(".gitignore"), "_build", ("/_build",)),
            EnsureLine(Path(".gitignore"), "ubproject.toml", ("/docs/ubproject.toml",)),
            RemoveFile(Path("docs/ubproject.toml")),
        ),
    )


def test_apply_policy_replaces_legacy_lines_and_removes_file(fake_repo: Path) -> None:
    (fake_repo / "MODULE.bazel").write_text(
        'module(name = "example")\nbazel_dep(name = "score_docs_as_code", version = "1.0")\n'
    )
    (fake_repo / ".gitignore").write_text(
        "/keep\n/_build\n/docs/ubproject.toml\n/_build\n"
    )
    (fake_repo / "docs").mkdir()
    (fake_repo / "docs/ubproject.toml").write_text("legacy\n")

    evaluation = apply_policy(fake_repo, _policy())

    assert len(evaluation.changes) == 3
    assert (fake_repo / ".gitignore").read_text() == "/keep\n_build\nubproject.toml\n"
    assert not (fake_repo / "docs/ubproject.toml").exists()
    assert apply_policy(fake_repo, _policy()).changes == ()


def test_policy_does_not_apply_without_direct_dependency(fake_repo: Path) -> None:
    (fake_repo / "MODULE.bazel").write_text(
        'bazel_dep(name = "other", version = "1.0")\n'
    )

    evaluation = evaluate_policy(fake_repo, _policy())

    assert not evaluation.applies
    assert evaluation.changes == ()


def test_value_exists_condition_skips_missing_source(fake_repo: Path) -> None:
    policy = Policy(
        id="example",
        title="Example",
        description=None,
        bazel_condition=None,
        ensure=(EnsureLine(Path("README.md"), "managed", ()),),
        values=(
            ValueBinding(
                "version",
                DockerfileImageVersionSource(Path("Dockerfile"), "example/image"),
            ),
        ),
        value_exists_condition=ValueExistsCondition("version"),
    )

    evaluation = evaluate_policy(fake_repo, policy)

    assert evaluation.applies is False
    assert evaluation.changes == ()


def test_value_exists_condition_accepts_crlf_dockerfile(fake_repo: Path) -> None:
    dockerfile = fake_repo / "Dockerfile"
    dockerfile.write_bytes(b"FROM example/image:v1.2.3\r\n")
    policy = Policy(
        id="example",
        title="Example",
        description=None,
        bazel_condition=None,
        ensure=(EnsureLine(Path("README.md"), "managed", ()),),
        values=(
            ValueBinding(
                "version",
                DockerfileImageVersionSource(Path("Dockerfile"), "example/image"),
            ),
        ),
        value_exists_condition=ValueExistsCondition("version"),
    )

    evaluation = evaluate_policy(fake_repo, policy)

    assert evaluation.applies is True
    assert evaluation.changes[0].path == Path("README.md")


def test_bazel_condition_ignores_commented_dependency(fake_repo: Path) -> None:
    (fake_repo / "MODULE.bazel").write_text(
        '# bazel_dep(name = "score_docs_as_code", version = "1.0.0")\n'
    )

    assert evaluate_policy(fake_repo, _policy()).applies is False


def test_bazel_condition_ignores_commented_arguments(fake_repo: Path) -> None:
    (fake_repo / "MODULE.bazel").write_text(
        """bazel_dep(
    # name = "score_docs_as_code",
    name = "other",
    version = "1.0.0",
)
"""
    )

    assert evaluate_policy(fake_repo, _policy()).applies is False


@pytest.mark.parametrize(
    "declaration",
    (
        'bazel_dep(name = "score_docs_as_code", version = "1.0")\n',
        'bazel_dep(name = "score_docs_as_code", version = "1.0.0-rc1")\n',
        'bazel_dep(name = "score_docs_as_code")\n',
    ),
)
def test_bazel_condition_rejects_uncomparable_configured_versions(
    fake_repo: Path, declaration: str
) -> None:
    (fake_repo / "MODULE.bazel").write_text(declaration)
    policy = Policy(
        "example",
        "Example",
        None,
        BazelCondition(
            (),
            any_direct_module_conditions=(
                BazelDependencyCondition("score_docs_as_code", ">=", (1, 0, 0)),
            ),
        ),
        (),
    )

    with pytest.raises(RepoPolicySyncError, match="numeric major.minor.patch"):
        evaluate_policy(fake_repo, policy)


def test_after_apply_regenerates_existing_conditional_file(
    fake_repo: Path, monkeypatch
) -> None:
    (fake_repo / "MODULE.bazel.lock").write_text("old lock\n")
    policy = Policy(
        id="example",
        title="Example",
        description=None,
        bazel_condition=None,
        ensure=(EnsureLine(Path(".bazelversion"), "8.6.0", ()),),
        after_apply=(
            AfterApplyCommand(
                ("bazel", "mod", "deps"),
                Path("MODULE.bazel.lock"),
                "Regenerate the lock file.",
            ),
        ),
    )
    calls: list[tuple[tuple[str, ...], Path]] = []

    def run(command, *, cwd, check, capture_output, text, env):
        calls.append((tuple(command), cwd))
        assert capture_output is True
        assert text is True
        assert env["GIT_CONFIG_NOSYSTEM"] == "1"
        assert "GH_TOKEN" not in env

    monkeypatch.setattr("repo_policy_sync.src.engine.subprocess.run", run)

    evaluation = evaluate_policy(fake_repo, policy)
    applied = apply_policy(fake_repo, policy)

    assert [change.path for change in evaluation.changes] == [
        Path(".bazelversion"),
        Path("MODULE.bazel.lock"),
    ]
    assert applied == evaluation
    assert calls == [(("bazel", "mod", "deps"), fake_repo)]


def test_force_after_apply_runs_for_an_already_compliant_policy(
    fake_repo: Path, monkeypatch
) -> None:
    lock_file = fake_repo / "MODULE.bazel.lock"
    lock_file.write_text("old lock\n")
    (fake_repo / ".bazelversion").write_text("8.6.0\n")
    policy = Policy(
        id="example",
        title="Example",
        description=None,
        bazel_condition=None,
        ensure=(EnsureMinimumVersion(Path(".bazelversion"), "8.6.0"),),
        after_apply=(
            AfterApplyCommand(
                ("bazel", "mod", "deps"), Path("MODULE.bazel.lock"), "Regenerate lock."
            ),
        ),
    )

    def run(command, *, cwd, check, capture_output, text, env):
        assert command == ("bazel", "mod", "deps")
        assert cwd == fake_repo
        assert capture_output is True
        assert text is True
        assert env["GIT_CONFIG_NOSYSTEM"] == "1"
        lock_file.write_text("new lock\n")

    monkeypatch.setattr("repo_policy_sync.src.engine.subprocess.run", run)

    applied = apply_policy(fake_repo, policy, force_after_apply=True)

    assert applied.changes == (Change(Path("MODULE.bazel.lock"), "Regenerate lock."),)
    assert lock_file.read_text() == "new lock\n"


def test_after_apply_failure_redacts_credentials(fake_repo: Path, monkeypatch) -> None:
    (fake_repo / "MODULE.bazel.lock").write_text("old lock\n")
    policy = Policy(
        id="example",
        title="Example",
        description=None,
        bazel_condition=None,
        ensure=(EnsureLine(Path(".bazelversion"), "8.6.0", ()),),
        after_apply=(
            AfterApplyCommand(
                ("bazel", "mod", "deps", "--token", "ghp_secret_value_12345"),
                Path("MODULE.bazel.lock"),
                "Regenerate the lock file.",
            ),
        ),
    )

    def run(*_, **__):
        raise subprocess.CalledProcessError(
            1,
            ["bazel", "mod", "deps"],
            stderr="authorization: Bearer ghp_secret_value_12345\n",
            output="password=another-secret\n",
        )

    monkeypatch.setattr("repo_policy_sync.src.engine.subprocess.run", run)

    with pytest.raises(RepoPolicySyncError) as error:
        apply_policy(fake_repo, policy)

    message = str(error.value)
    assert "ghp_secret_value_12345" not in message
    assert "another-secret" not in message
    assert "[REDACTED]" in message


def test_after_apply_failure_without_output_has_a_fallback_message(
    fake_repo: Path, monkeypatch
) -> None:
    (fake_repo / "MODULE.bazel.lock").write_text("old lock\n")
    policy = Policy(
        id="example",
        title="Example",
        description=None,
        bazel_condition=None,
        ensure=(EnsureLine(Path(".bazelversion"), "8.6.0", ()),),
        after_apply=(
            AfterApplyCommand(
                ("bazel", "mod", "deps"),
                Path("MODULE.bazel.lock"),
                "Regenerate the lock file.",
            ),
        ),
    )

    def run(*_, **__):
        raise subprocess.CalledProcessError(1, ["bazel", "mod", "deps"])

    monkeypatch.setattr("repo_policy_sync.src.engine.subprocess.run", run)

    with pytest.raises(RepoPolicySyncError, match=r"command failed \(exit status 1\)"):
        apply_policy(fake_repo, policy)
