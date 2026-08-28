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
import os
import subprocess
from pathlib import Path

import pytest

from repo_policy_sync.src.github import (
    CommitResult,
    GitHubCli,
    PullRequest,
    _pull_request_body,
    policy_branches,
)
from repo_policy_sync.src.errors import CommandError, redact_sensitive_text
from repo_policy_sync.src.models import (
    BazelCondition,
    Change,
    EnsureLine,
    RemoveFile,
    Policy,
)
from repo_policy_sync.src.policy import BUNDLED_POLICY_DIRECTORY, load_policy


def test_commit_stages_deleted_policy_files(monkeypatch, tmp_path: Path) -> None:
    commands: list[list[str]] = []

    def record(command: list[str]) -> str:
        commands.append(command)
        return ""

    monkeypatch.setattr(GitHubCli, "_run", staticmethod(record))
    policy = Policy(
        id="example",
        title="Example",
        description=None,
        bazel_condition=None,
        ensure=(
            EnsureLine(Path(".gitignore"), "_build", ()),
            RemoveFile(Path("docs/ubproject.toml")),
        ),
    )

    GitHubCli().commit_and_push(
        checkout=tmp_path,
        branch="repo-policy-sync/example",
        policy=policy,
        changes=(
            Change(Path(".gitignore"), "add '_build'"),
            Change(Path("docs/ubproject.toml"), "remove file"),
        ),
    )

    assert commands[0] == [
        "git",
        "-C",
        str(tmp_path),
        "add",
        "-A",
        "--",
        ".gitignore",
        "docs/ubproject.toml",
    ]


def test_commit_does_not_stage_policy_paths_without_changes(
    monkeypatch, tmp_path: Path
) -> None:
    commands: list[list[str]] = []

    def record(command: list[str]) -> str:
        commands.append(command)
        return ""

    monkeypatch.setattr(GitHubCli, "_run", staticmethod(record))
    policy = Policy("example", "Example", None, None, ())

    GitHubCli().commit_and_push(
        checkout=tmp_path,
        branch="repo-policy-sync/example",
        policy=policy,
        changes=(Change(Path(".gitignore"), "add '_build'"),),
    )

    assert commands[0] == ["git", "-C", str(tmp_path), "add", "-A", "--", ".gitignore"]


def test_has_changes_detects_untracked_policy_files(
    monkeypatch, tmp_path: Path
) -> None:
    commands: list[list[str]] = []

    def record(command: list[str]) -> str:
        commands.append(command)
        return "?? generated.txt\n"

    monkeypatch.setattr(GitHubCli, "_run", staticmethod(record))

    assert GitHubCli().has_changes(
        checkout=tmp_path,
        changes=(Change(Path("generated.txt"), "add generated file"),),
    )
    assert commands == [
        [
            "git",
            "-C",
            str(tmp_path),
            "status",
            "--short",
            "--untracked-files=all",
            "--",
            "generated.txt",
        ]
    ]


def test_commit_runs_pre_commit_after_staging_when_repository_configures_it(
    monkeypatch, tmp_path: Path
) -> None:
    (tmp_path / ".pre-commit-config.yaml").write_text("repos: []\n")
    (tmp_path / ".gitignore").write_text("\n")
    commands: list[tuple[list[str], Path | None]] = []

    def record(
        command: list[str],
        *,
        cwd: Path | None = None,
        env: dict[str, str] | None = None,
    ) -> str:
        commands.append((command, cwd))
        return ""

    monkeypatch.setattr(GitHubCli, "_run", staticmethod(record))
    policy = Policy("example", "Example", None, None, ())

    GitHubCli().commit_and_push(
        checkout=tmp_path,
        branch="repo-policy-sync/example",
        policy=policy,
        changes=(Change(Path(".gitignore"), "add '_build'"),),
    )

    assert commands[0][0] == [
        "git",
        "-C",
        str(tmp_path),
        "add",
        "-A",
        "--",
        ".gitignore",
    ]
    assert commands[1] == (
        ["pre-commit", "run", "--files", ".gitignore"],
        tmp_path,
    )
    assert commands[2][0] == [
        "git",
        "-C",
        str(tmp_path),
        "add",
        "-A",
        "--",
        ".gitignore",
    ]


def test_pre_commit_does_not_inherit_credentials_or_user_configuration(
    monkeypatch, tmp_path: Path
) -> None:
    (tmp_path / ".pre-commit-config.yaml").write_text("repos: []\n")
    observed: dict[str, str] = {}
    monkeypatch.setenv("GH_TOKEN", "secret")
    monkeypatch.setenv("GITHUB_TOKEN", "secret-too")
    monkeypatch.setenv("GH_CONFIG_DIR", "/tmp/user-gh-config")

    def record(
        command: list[str],
        *,
        cwd: Path | None = None,
        env: dict[str, str] | None = None,
    ) -> str:
        if command[0] == "pre-commit":
            assert env is not None
            observed.update(env)
            assert Path(env["HOME"]).is_dir()
        return ""

    monkeypatch.setattr(GitHubCli, "_run", staticmethod(record))

    assert GitHubCli().run_pre_commit(checkout=tmp_path)

    assert "GH_TOKEN" not in observed
    assert "GITHUB_TOKEN" not in observed
    assert observed["GH_CONFIG_DIR"] != os.environ["GH_CONFIG_DIR"]
    assert observed["GIT_CONFIG_NOSYSTEM"] == "1"
    assert observed["GIT_TERMINAL_PROMPT"] == "0"


def test_pre_commit_failure_stops_commit_and_push(monkeypatch, tmp_path: Path) -> None:
    (tmp_path / ".pre-commit-config.yaml").write_text("repos: []\n")
    (tmp_path / ".gitignore").write_text("\n")
    commands: list[tuple[list[str], Path | None]] = []

    def record(
        command: list[str],
        *,
        cwd: Path | None = None,
        env: dict[str, str] | None = None,
    ) -> str:
        commands.append((command, cwd))
        if command[0] == "pre-commit":
            raise CommandError("pre-commit found issues")
        return ""

    monkeypatch.setattr(GitHubCli, "_run", staticmethod(record))
    policy = Policy("example", "Example", None, None, ())

    with pytest.raises(CommandError, match="pre-commit found issues"):
        GitHubCli().commit_and_push(
            checkout=tmp_path,
            branch="repo-policy-sync/example",
            policy=policy,
            changes=(Change(Path(".gitignore"), "add '_build'"),),
        )

    assert [command[0][0] for command in commands] == [
        "git",
        "pre-commit",
        "git",
        "pre-commit",
    ]


def test_pre_commit_formatting_fix_is_rechecked_before_publishing(
    monkeypatch, tmp_path: Path
) -> None:
    (tmp_path / ".pre-commit-config.yaml").write_text("repos: []\n")
    (tmp_path / ".gitignore").write_text("\n")
    commands: list[tuple[list[str], Path | None]] = []
    pre_commit_runs = 0

    def record(
        command: list[str],
        *,
        cwd: Path | None = None,
        env: dict[str, str] | None = None,
    ) -> str:
        nonlocal pre_commit_runs
        commands.append((command, cwd))
        if command[0] == "pre-commit":
            pre_commit_runs += 1
            if pre_commit_runs == 1:
                raise CommandError("pre-commit fixed formatting")
        if command[-2:] == ["rev-parse", "HEAD"]:
            return "b" * 40
        return ""

    monkeypatch.setattr(GitHubCli, "_run", staticmethod(record))
    policy = Policy("example", "Example", None, None, ())

    result = GitHubCli().commit_and_push(
        checkout=tmp_path,
        branch="repo-policy-sync/example",
        policy=policy,
        changes=(Change(Path(".gitignore"), "add '_build'"),),
    )

    assert result == CommitResult("b" * 40)
    assert pre_commit_runs == 2
    assert commands[2][0] == [
        "git",
        "-C",
        str(tmp_path),
        "add",
        "-A",
        "--",
        ".gitignore",
    ]
    assert [command[0][0] for command in commands] == [
        "git",
        "pre-commit",
        "git",
        "pre-commit",
        "git",
        "git",
        "git",
        "git",
    ]


def test_dirty_commit_keeps_pre_commit_failure_and_publishes_changes(
    monkeypatch, tmp_path: Path
) -> None:
    (tmp_path / ".pre-commit-config.yaml").write_text("repos: []\n")
    (tmp_path / ".gitignore").write_text("\n")
    commands: list[tuple[list[str], Path | None]] = []

    def record(
        command: list[str],
        *,
        cwd: Path | None = None,
        env: dict[str, str] | None = None,
    ) -> str:
        commands.append((command, cwd))
        if command[0] == "pre-commit":
            raise CommandError("pre-commit found issues")
        if command[-2:] == ["rev-parse", "HEAD"]:
            return "b" * 40
        return ""

    monkeypatch.setattr(GitHubCli, "_run", staticmethod(record))
    policy = Policy("example", "Example", None, None, ())

    result = GitHubCli().commit_and_push(
        checkout=tmp_path,
        branch="repo-policy-sync/example",
        policy=policy,
        changes=(Change(Path(".gitignore"), "add '_build'"),),
        allow_dirty_pr=True,
    )

    assert result == CommitResult("b" * 40, "pre-commit found issues")
    assert [command[0][0] for command in commands] == [
        "git",
        "pre-commit",
        "git",
        "pre-commit",
        "git",
        "git",
        "git",
        "git",
    ]


def test_local_policy_branch_is_reused_after_a_failed_run(
    monkeypatch, tmp_path: Path
) -> None:
    commands: list[list[str]] = []

    def record(command: list[str]) -> str:
        commands.append(command)
        return ""

    monkeypatch.setattr(GitHubCli, "_run", staticmethod(record))

    GitHubCli().switch_to_policy_branch(
        checkout=tmp_path,
        branch="repo-policy-sync/example",
        exists_remotely=False,
    )

    assert commands == [
        ["git", "-C", str(tmp_path), "switch", "-C", "repo-policy-sync/example"]
    ]


def test_gh_command_failures_are_actionable(monkeypatch) -> None:
    def run(*_: object, **__: object) -> None:
        raise subprocess.CalledProcessError(
            1, ["gh", "auth", "status"], stderr="authentication failed\n"
        )

    monkeypatch.setattr("repo_policy_sync.src.github.subprocess.run", run)

    with pytest.raises(CommandError, match="gh auth status: authentication failed"):
        GitHubCli._run(["gh", "auth", "status"])


def test_gh_command_failures_redact_credentials(monkeypatch) -> None:
    token = "ghp_secret_value_12345"

    def run(*_: object, **__: object) -> None:
        raise subprocess.CalledProcessError(
            1,
            ["gh", "auth", "status"],
            stderr=f"Authorization: Bearer {token}\n",
        )

    monkeypatch.setattr("repo_policy_sync.src.github.subprocess.run", run)

    with pytest.raises(CommandError) as error:
        GitHubCli._run(["gh", "auth", "status"])

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

    monkeypatch.setattr("repo_policy_sync.src.github.subprocess.run", run)

    with pytest.raises(CommandError, match="required command is unavailable: gh"):
        GitHubCli._run(["gh", "auth", "status"])


def test_create_pull_request_creates_missing_automation_labels(monkeypatch) -> None:
    commands: list[list[str]] = []

    def run(command: list[str]) -> str:
        commands.append(command)
        if command[:5] == [
            "gh",
            "api",
            "--paginate",
            "--slurp",
            "/repos/owner/repo/labels?per_page=100",
        ]:
            return "[[]]"
        if command[:4] == ["gh", "api", "--method", "POST"]:
            return ""
        if command[:3] == ["gh", "pr", "create"]:
            return "https://github.example/owner/repo/pull/1\n"
        if command[:3] == ["gh", "pr", "edit"]:
            return ""
        raise AssertionError(command)

    monkeypatch.setattr(GitHubCli, "_run", staticmethod(run))
    policy = Policy("example", "Example", None, None, ())

    pull_request = GitHubCli().create_pull_request(
        repository="owner/repo",
        base="main",
        branch="repo-policy-sync/example",
        policy=policy,
        changes=(),
        head_oid="a" * 40,
    )

    assert pull_request.url == "https://github.example/owner/repo/pull/1"
    assert pull_request.warnings == ()
    assert [
        command[4]
        for command in commands
        if command[:4] == ["gh", "api", "--method", "POST"]
    ] == [
        "/repos/owner/repo/labels",
        "/repos/owner/repo/labels",
    ]
    assert [
        command[6]
        for command in commands
        if command[:4] == ["gh", "api", "--method", "POST"]
    ] == [
        "name=automation",
        "name=repo-policy-sync",
    ]
    assert [
        command[8]
        for command in commands
        if command[:4] == ["gh", "api", "--method", "POST"]
    ] == [
        "color=EDEDED",
        "color=EDEDED",
    ]
    assert [
        command[-1] for command in commands if command[:3] == ["gh", "pr", "edit"]
    ] == [
        "automation",
        "repo-policy-sync",
    ]


def test_create_pull_request_keeps_existing_automation_labels(monkeypatch) -> None:
    commands: list[list[str]] = []

    def run(command: list[str]) -> str:
        commands.append(command)
        if command[:5] == [
            "gh",
            "api",
            "--paginate",
            "--slurp",
            "/repos/owner/repo/labels?per_page=100",
        ]:
            return '[[{"name":"automation"},{"name":"repo-policy-sync"}]]'
        if command[:3] == ["gh", "pr", "create"]:
            return "https://github.example/owner/repo/pull/1\n"
        if command[:3] == ["gh", "pr", "edit"]:
            return ""
        raise AssertionError(command)

    monkeypatch.setattr(GitHubCli, "_run", staticmethod(run))
    policy = Policy("example", "Example", None, None, ())

    GitHubCli().create_pull_request(
        repository="owner/repo",
        base="main",
        branch="repo-policy-sync/example",
        policy=policy,
        changes=(),
        head_oid="a" * 40,
    )

    assert not any(
        command[:4] == ["gh", "api", "--method", "POST"] for command in commands
    )


def test_create_pull_request_can_create_a_draft(monkeypatch) -> None:
    commands: list[list[str]] = []

    def run(command: list[str]) -> str:
        commands.append(command)
        if command[:5] == [
            "gh",
            "api",
            "--paginate",
            "--slurp",
            "/repos/owner/repo/labels?per_page=100",
        ]:
            return '[[{"name":"automation"},{"name":"repo-policy-sync"}]]'
        if command[:3] == ["gh", "pr", "create"]:
            return "https://github.example/owner/repo/pull/1\n"
        if command[:3] == ["gh", "pr", "edit"]:
            return ""
        raise AssertionError(command)

    monkeypatch.setattr(GitHubCli, "_run", staticmethod(run))
    policy = Policy("example", "Example", None, None, ())

    GitHubCli().create_pull_request(
        repository="owner/repo",
        base="main",
        branch="repo-policy-sync/example",
        policy=policy,
        changes=(),
        head_oid="a" * 40,
        draft=True,
    )

    assert commands[1][:4] == ["gh", "pr", "create", "--draft"]


def test_pull_request_template_explains_policy_trigger_and_changes() -> None:
    policy = Policy(
        "score-docs-as-code.cleanup",
        "Update docs files",
        "Replace legacy documentation files.",
        BazelCondition(("score_docs_as_code",)),
        (),
    )

    body = _pull_request_body(
        policy, (Change(Path(".gitignore"), "add '_build'"),), head_oid="a" * 40
    )

    assert "<!-- repo-policy-sync-policy: score-docs-as-code.cleanup -->" in body
    assert "## Policy" in body
    assert "**`score-docs-as-code.cleanup`**" in body
    assert (
        "This repository matches this policy because `MODULE.bazel` declares the required direct Bazel"
        in body
    )
    assert "`MODULE.bazel` declares the required direct Bazel dependency" in body
    assert "- `.gitignore`: add '_build'" in body
    assert body.index("## Policy") < body.index("<!-- repo-policy-sync-policy:")
    assert body.index("<!-- repo-policy-sync-policy:") < body.index(
        "<!-- repo-policy-sync-head:"
    )


def test_module_policy_pull_request_includes_the_matching_rationale() -> None:
    policy = load_policy(
        BUNDLED_POLICY_DIRECTORY / "minimal-bazel-module-declaration" / "policy.yml"
    )
    operation = policy.ensure[0]

    body = _pull_request_body(
        policy,
        (Change(operation.path, "replace matching text", operation.rationale),),
        head_oid="a" * 40,
    )

    assert "- `MODULE.bazel`: replace matching text" in body
    assert "This repository matches this policy because `MODULE.bazel` exists." in body
    assert (
        "  - Remove module metadata that should be derived by the Bazel Registry."
    ) in body


def test_existing_pull_request_is_updated_with_the_current_template(
    monkeypatch,
) -> None:
    commands: list[list[str]] = []

    def record(command: list[str]) -> str:
        commands.append(command)
        return ""

    monkeypatch.setattr(GitHubCli, "_run", staticmethod(record))
    policy = Policy("example", "Current title", "Current description", None, ())

    GitHubCli().update_pull_request(
        repository="owner/repo",
        pull_request=PullRequest(1, "https://github.example/owner/repo/pull/1"),
        policy=policy,
        changes=(Change(Path(".gitignore"), "add '_build'"),),
        head_oid="a" * 40,
    )

    assert commands[0][:7] == [
        "gh",
        "pr",
        "edit",
        "https://github.example/owner/repo/pull/1",
        "--repo",
        "owner/repo",
        "--title",
    ]
    assert "## Policy" in commands[0][-1]


def test_pull_request_template_includes_automation_failure() -> None:
    policy = Policy("example", "Example", None, None, ())

    body = _pull_request_body(
        policy, (), head_oid="a" * 40, failure="bazel mod deps: command failed"
    )

    assert "## Automation failure" in body
    assert "bazel mod deps: command failed" in body


def test_close_pull_request_uses_gh(monkeypatch) -> None:
    commands: list[list[str]] = []
    monkeypatch.setattr(
        GitHubCli, "_run", staticmethod(lambda command: commands.append(command) or "")
    )

    GitHubCli().close_pull_request(
        repository="owner/repo",
        pull_request=PullRequest(1, "https://github.example/owner/repo/pull/1"),
    )

    assert commands == [
        [
            "gh",
            "pr",
            "close",
            "https://github.example/owner/repo/pull/1",
            "--repo",
            "owner/repo",
            "--delete-branch",
        ]
    ]


def test_dirty_pull_request_is_marked_draft_and_commented(monkeypatch) -> None:
    commands: list[list[str]] = []
    monkeypatch.setattr(
        GitHubCli, "_run", staticmethod(lambda command: commands.append(command) or "")
    )
    pull_request = PullRequest(1, "https://github.example/owner/repo/pull/1")

    GitHubCli().mark_pull_request_draft(
        repository="owner/repo", pull_request=pull_request
    )
    GitHubCli().comment_on_pull_request(
        repository="owner/repo",
        pull_request=pull_request,
        failure="pre-commit found issues",
    )

    assert commands[0] == [
        "gh",
        "pr",
        "ready",
        pull_request.url,
        "--repo",
        "owner/repo",
        "--undo",
    ]
    assert commands[1][:6] == [
        "gh",
        "pr",
        "comment",
        pull_request.url,
        "--repo",
        "owner/repo",
    ]
    assert "pre-commit found issues" in commands[1][-1]


def test_recreate_policy_branch_starts_from_the_current_checkout(
    monkeypatch, tmp_path: Path
) -> None:
    commands: list[list[str]] = []
    monkeypatch.setattr(
        GitHubCli, "_run", staticmethod(lambda command: commands.append(command) or "")
    )

    GitHubCli().recreate_policy_branch(
        checkout=tmp_path, branch="repo-policy-sync/example"
    )

    assert commands == [
        ["git", "-C", str(tmp_path), "switch", "-C", "repo-policy-sync/example"]
    ]


def test_recreate_force_push_uses_the_verified_remote_head(
    monkeypatch, tmp_path: Path
) -> None:
    commands: list[list[str]] = []

    def record(command: list[str]) -> str:
        commands.append(command)
        return "b" * 40 if command[-2:] == ["rev-parse", "HEAD"] else ""

    monkeypatch.setattr(GitHubCli, "_run", staticmethod(record))
    policy = Policy("example", "Example", None, None, ())

    GitHubCli().commit_and_force_push(
        checkout=tmp_path,
        branch="repo-policy-sync/example",
        expected_head_oid="a" * 40,
        policy=policy,
        changes=(Change(Path("MODULE.bazel.lock"), "Regenerate lock."),),
    )

    assert commands[2] == [
        "git",
        "-C",
        str(tmp_path),
        "push",
        "--force-with-lease=refs/heads/repo-policy-sync/example:" + "a" * 40,
        "--set-upstream",
        "origin",
        "repo-policy-sync/example",
    ]


def test_recreate_force_push_runs_pre_commit_before_commit(
    monkeypatch, tmp_path: Path
) -> None:
    (tmp_path / ".pre-commit-config.yaml").write_text("repos: []\n")
    (tmp_path / "MODULE.bazel.lock").write_text("lock\n")
    commands: list[tuple[list[str], Path | None]] = []

    def record(
        command: list[str],
        *,
        cwd: Path | None = None,
        env: dict[str, str] | None = None,
    ) -> str:
        commands.append((command, cwd))
        return "b" * 40 if command[-2:] == ["rev-parse", "HEAD"] else ""

    monkeypatch.setattr(GitHubCli, "_run", staticmethod(record))
    policy = Policy("example", "Example", None, None, ())

    GitHubCli().commit_and_force_push(
        checkout=tmp_path,
        branch="repo-policy-sync/example",
        expected_head_oid="a" * 40,
        policy=policy,
        changes=(Change(Path("MODULE.bazel.lock"), "Regenerate lock."),),
    )

    assert commands[0][0] == [
        "git",
        "-C",
        str(tmp_path),
        "add",
        "-A",
        "--",
        "MODULE.bazel.lock",
    ]
    assert commands[1] == (
        ["pre-commit", "run", "--files", "MODULE.bazel.lock"],
        tmp_path,
    )
    assert commands[2][0] == [
        "git",
        "-C",
        str(tmp_path),
        "add",
        "-A",
        "--",
        "MODULE.bazel.lock",
    ]


def test_verify_policy_branch_head_refuses_a_changed_branch(
    monkeypatch, tmp_path: Path
) -> None:
    monkeypatch.setattr(
        GitHubCli,
        "_run",
        staticmethod(lambda _: "b" * 40 + "\trefs/heads/repo-policy-sync/example\n"),
    )

    with pytest.raises(CommandError, match="refusing to modify policy branch"):
        GitHubCli().verify_policy_branch_head(
            checkout=tmp_path,
            branch="repo-policy-sync/example",
            expected_head_oid="a" * 40,
        )


def test_policy_pull_request_without_head_marker_is_not_safe_to_reuse(
    monkeypatch,
) -> None:
    policy = Policy("example", "Example", None, None, ())
    branch = policy_branches(policy)[0]

    def run(command: list[str]) -> str:
        if command[command.index("--head") + 1] == branch:
            return json.dumps(
                [
                    {
                        "number": 1,
                        "url": "https://github.example/owner/repo/pull/1",
                        "body": "<!-- repo-policy-sync-policy: example -->",
                        "mergeable": "CONFLICTING",
                    }
                ]
            )
        return "[]"

    monkeypatch.setattr(GitHubCli, "_run", staticmethod(run))

    pull_request = GitHubCli().find_open_pull_request(
        repository="owner/repo",
        branches=policy_branches(policy),
        policy_id=policy.id,
    )

    assert pull_request is not None
    assert pull_request.expected_head_oid is None
    assert pull_request.mergeable == "CONFLICTING"


def test_pre_existing_user_pull_request_is_not_reused(monkeypatch) -> None:
    policy = Policy("example", "Example", None, None, ())
    branch = policy_branches(policy)[0]

    def run(command: list[str]) -> str:
        if command[command.index("--head") + 1] == branch:
            return json.dumps(
                [
                    {
                        "number": 1,
                        "url": "https://github.example/owner/repo/pull/1",
                        "body": "A pull request opened by a maintainer.",
                        "mergeable": "MERGEABLE",
                    }
                ]
            )
        return "[]"

    monkeypatch.setattr(GitHubCli, "_run", staticmethod(run))

    with pytest.raises(CommandError, match="is not owned by policy example"):
        GitHubCli().find_open_pull_request(
            repository="owner/repo",
            branches=policy_branches(policy),
            policy_id=policy.id,
        )


def test_legacy_policy_pull_request_is_recognized_on_its_old_branch(
    monkeypatch,
) -> None:
    policy = Policy(
        "current",
        "Example",
        None,
        None,
        (),
        legacy_names=("old-policy",),
    )
    branches = policy_branches(policy)

    def run(command: list[str]) -> str:
        branch = command[command.index("--head") + 1]
        if branch == branches[0]:
            return "[]"
        assert branch == branches[1]
        return json.dumps(
            [
                {
                    "number": 1,
                    "url": "https://github.example/owner/repo/pull/1",
                    "body": "<!-- repo-policy-sync-policy: old-policy -->\n"
                    "<!-- repo-policy-sync-head: " + "a" * 40 + " -->",
                    "mergeable": "MERGEABLE",
                }
            ]
        )

    monkeypatch.setattr(GitHubCli, "_run", staticmethod(run))

    pull_request = GitHubCli().find_open_pull_request(
        repository="owner/repo",
        branches=branches,
        policy_id=policy.id,
        legacy_policy_ids=policy.legacy_names,
    )

    assert pull_request is not None
    assert pull_request.branch == branches[1]


def test_policy_pull_request_status_includes_latest_merged_pull_request(
    monkeypatch,
) -> None:
    policy = Policy("example", "Example", None, None, ())
    branch = policy_branches(policy)[0]

    def run(command: list[str]) -> str:
        assert command[command.index("--head") + 1] == branch
        state = command[command.index("--state") + 1]
        if state == "open":
            return json.dumps(
                [
                    {
                        "number": 3,
                        "url": "https://github.example/owner/repo/pull/3",
                        "body": "<!-- repo-policy-sync-policy: example -->\n"
                        "<!-- repo-policy-sync-head: " + "a" * 40 + " -->",
                    }
                ]
            )
        return json.dumps(
            [
                {
                    "number": 2,
                    "url": "https://github.example/owner/repo/pull/2",
                    "body": "<!-- repo-policy-sync-policy: example -->\n"
                    "<!-- repo-policy-sync-head: " + "a" * 40 + " -->",
                    "mergedAt": "2026-01-01T00:00:00Z",
                },
                {
                    "number": 99,
                    "url": "https://github.example/owner/repo/pull/99",
                    "body": "a historical PR owned by another tool",
                    "mergedAt": "2026-02-01T00:00:00Z",
                },
            ]
        )

    monkeypatch.setattr(GitHubCli, "_run", staticmethod(run))

    status = GitHubCli().find_policy_pull_request_status(
        repository="owner/repo",
        branches=(branch,),
        policy_id=policy.id,
    )

    assert status.open is not None
    assert status.open.url.endswith("/3")
    assert status.merged is not None
    assert status.merged.url.endswith("/2")


def test_multiple_policy_pull_requests_fail_instead_of_choosing(monkeypatch) -> None:
    policy = Policy("current", "Example", None, None, ())
    branch = policy_branches(policy)[0]
    body = (
        "<!-- repo-policy-sync-policy: current -->\n<!-- repo-policy-sync-head: "
        + "a" * 40
        + " -->"
    )

    def run(command: list[str]) -> str:
        assert command[command.index("--head") + 1] == branch
        return json.dumps(
            [
                {
                    "number": 1,
                    "url": "https://github.example/owner/repo/pull/1",
                    "body": body,
                },
                {
                    "number": 2,
                    "url": "https://github.example/owner/repo/pull/2",
                    "body": body,
                },
            ]
        )

    monkeypatch.setattr(GitHubCli, "_run", staticmethod(run))

    with pytest.raises(
        CommandError, match="multiple open pull requests match policy current"
    ):
        GitHubCli().find_open_pull_request(
            repository="owner/repo",
            branches=policy_branches(policy),
            policy_id=policy.id,
        )
