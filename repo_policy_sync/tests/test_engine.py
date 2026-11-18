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
import subprocess
import sys

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
        'module(name = "other")\nbazel_dep(name = "other", version = "1.0.0")\n'
    )

    evaluation = evaluate_policy(fake_repo, _policy())

    assert not evaluation.applies
    assert evaluation.changes == ()


def test_bazel_condition_accepts_own_module_for_each_required_name(
    fake_repo: Path,
) -> None:
    (fake_repo / "MODULE.bazel").write_text(
        'module(name = "score_docs_as_code")\n'
        'bazel_dep(name = "score_platform", version = "1.0.0")\n'
    )
    policy = Policy(
        "example",
        "Example",
        None,
        BazelCondition(("score_docs_as_code", "score_platform")),
        (),
    )

    assert evaluate_policy(fake_repo, policy).applies is True


def test_bazel_any_dependency_condition_accepts_own_module_name(
    fake_repo: Path,
) -> None:
    (fake_repo / "MODULE.bazel").write_text('module(name = "score_docs_as_code")\n')
    policy = Policy(
        "example",
        "Example",
        None,
        BazelCondition(
            (), any_direct_module_dependencies=("score_platform", "score_docs_as_code")
        ),
        (),
    )

    assert evaluate_policy(fake_repo, policy).applies is True


def test_bazel_version_condition_still_requires_a_direct_dependency(
    fake_repo: Path,
) -> None:
    module_file = fake_repo / "MODULE.bazel"
    module_file.write_text('module(name = "score_docs_as_code")\n')
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

    assert evaluate_policy(fake_repo, policy).applies is False

    module_file.write_text(
        'module(name = "score_docs_as_code")\n'
        'bazel_dep(name = "score_docs_as_code", version = "1.2.3")\n'
    )
    assert evaluate_policy(fake_repo, policy).applies is True


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

    monkeypatch.setattr(
        "repo_policy_sync.src.engine._copy_global_git_url_rewrites", lambda home: None
    )
    monkeypatch.setattr("repo_policy_sync.src.engine.subprocess.run", run)

    evaluation = evaluate_policy(fake_repo, policy)
    applied = apply_policy(fake_repo, policy)

    assert [change.path for change in evaluation.changes] == [
        Path(".bazelversion"),
        Path("MODULE.bazel.lock"),
    ]
    assert applied == evaluation
    assert calls == [(("bazel", "mod", "deps"), fake_repo)]


@pytest.mark.parametrize("with_rewrite", [True, False])
def test_after_apply_carries_only_global_git_url_rewrites(
    tmp_path: Path, monkeypatch, with_rewrite: bool
) -> None:
    """Real child processes can use private Git URLs without inheriting user settings."""

    user_home = tmp_path / "user-home"
    user_home.mkdir()
    # Honor an explicitly selected global configuration as well as HOME.
    global_config = tmp_path / "global.gitconfig"
    global_config.write_text("[user]\n\tname = Test User\n[alias]\n\tcustom = status\n")
    if with_rewrite:
        subprocess.run(
            [
                "git",
                "config",
                "--file",
                str(global_config),
                "--add",
                "url.https://x-access-token:dummy-token@github.com/.insteadOf",
                "https://github.com/",
            ],
            check=True,
        )
    monkeypatch.setenv("HOME", str(user_home))
    monkeypatch.setenv("GIT_CONFIG_GLOBAL", str(global_config))
    monkeypatch.setenv("GIT_CONFIG_NOSYSTEM", "1")
    monkeypatch.setenv("GH_TOKEN", "dummy-token")
    monkeypatch.setenv("GITHUB_TOKEN", "dummy-token")
    monkeypatch.setenv("GH_CONFIG_DIR", str(user_home / ".gh"))
    (tmp_path / "MODULE.bazel.lock").write_text("old lock\n")
    # This child uses real Git to resolve a URL without making a network request.
    # Reading global rewrites is also how a repository credential helper can
    # recover an automation token for private registry HTTP downloads.
    command = (
        sys.executable,
        "-c",
        r"""
import os
import subprocess
import sys
from pathlib import Path

assert 'GH_TOKEN' not in os.environ
assert 'GITHUB_TOKEN' not in os.environ
assert Path(os.environ['HOME']) != Path(sys.argv[1])
assert Path(os.environ['GH_CONFIG_DIR']) != Path(sys.argv[1]) / '.gh'
assert os.environ['GIT_CONFIG_NOSYSTEM'] == '1'
assert os.environ['GIT_TERMINAL_PROMPT'] == '0'
for key in ('user.name', 'alias.custom'):
    result = subprocess.run(['git', 'config', '--global', '--get', key], capture_output=True)
    assert result.returncode == 1
result = subprocess.run(
    ['git', 'ls-remote', '--get-url', 'https://github.com/example/private.git'],
    check=True, capture_output=True, text=True,
)
assert result.stdout.strip() == sys.argv[2]
rewrites = subprocess.run(
    ['git', 'config', '--global', '--get-regexp', r'^url\..*\.insteadof$'],
    capture_output=True, text=True,
)
if 'x-access-token' in sys.argv[2]:
    assert rewrites.returncode == 0
    assert 'dummy-token' in rewrites.stdout
    assert Path(os.environ['GIT_CONFIG_GLOBAL']).parent == Path(os.environ['HOME'])
else:
    assert rewrites.returncode == 1
    assert 'GIT_CONFIG_GLOBAL' not in os.environ
Path('MODULE.bazel.lock').write_text('new lock\n')
""",
        str(user_home),
        (
            "https://x-access-token:dummy-token@github.com/example/private.git"
            if with_rewrite
            else "https://github.com/example/private.git"
        ),
    )
    policy = Policy(
        id="example",
        title="Example",
        description=None,
        bazel_condition=None,
        ensure=(EnsureLine(Path(".bazelversion"), "8.6.0", ()),),
        after_apply=(
            AfterApplyCommand(
                command, Path("MODULE.bazel.lock"), "Regenerate the lock file."
            ),
        ),
    )

    applied = apply_policy(tmp_path, policy)

    assert (tmp_path / "MODULE.bazel.lock").read_text() == "new lock\n"
    assert Path("MODULE.bazel.lock") in {change.path for change in applied.changes}


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

    monkeypatch.setattr(
        "repo_policy_sync.src.engine._copy_global_git_url_rewrites", lambda home: None
    )
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

    monkeypatch.setattr(
        "repo_policy_sync.src.engine._copy_global_git_url_rewrites", lambda home: None
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

    monkeypatch.setattr(
        "repo_policy_sync.src.engine._copy_global_git_url_rewrites", lambda home: None
    )
    monkeypatch.setattr("repo_policy_sync.src.engine.subprocess.run", run)

    with pytest.raises(RepoPolicySyncError, match=r"command failed \(exit status 1\)"):
        apply_policy(fake_repo, policy)
