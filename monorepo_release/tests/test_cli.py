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

import os
import subprocess
from pathlib import Path

import pytest

from monorepo_release.src import github
from monorepo_release.src.cli import main
from monorepo_release.src.releases import (
    git,
    module_report,
    reachable_tags,
    read_modules,
)

REPOSITORY_URL = "https://github.com/example/monorepo"
DRAFT_URL = f"{REPOSITORY_URL}/releases/tag/bazel%2Fv1.3.0"


@pytest.fixture(autouse=True)
def isolate_git_config(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("GIT_CONFIG_GLOBAL", os.devnull)
    monkeypatch.setenv("GIT_CONFIG_NOSYSTEM", "1")
    monkeypatch.delenv("GIT_CONFIG_COUNT", raising=False)
    monkeypatch.delenv("GIT_CONFIG_PARAMETERS", raising=False)
    monkeypatch.setenv("LC_ALL", "C")


def commit(root: Path, subject: str, files: dict[str, str]) -> str:
    for name, contents in files.items():
        file = root / name
        file.parent.mkdir(parents=True, exist_ok=True)
        _ = file.write_text(contents, encoding="utf-8")
    git(root, "add", "-A")
    git(root, "commit", "--quiet", "-m", subject)
    return git(root, "rev-parse", "HEAD").strip()


@pytest.fixture
def repository(tmp_path: Path) -> Path:
    root = tmp_path / "source"
    root.mkdir()
    git(root, "init", "--quiet", "--initial-branch=main")
    git(root, "config", "user.name", "Release tests")
    git(root, "config", "user.email", "release-tests@example.test")
    commit(
        root,
        "feat: add workflows",
        {
            ".github/labeler.yml": """\
bazel.yml:
  - changed-files:
      - any-glob-to-any-file:
          - .github/workflows/bazel.yml
          - ".github/actions/_setup-*/**"
docs:
  - changed-files:
      - any-glob-to-any-file: "docs/**"
""",
            ".github/workflows/bazel.yml": "workflow",
            ".github/actions/_setup-bazel/action.yml": "action",
            "docs/index.md": "docs",
        },
    )
    return root


@pytest.fixture
def github_remote(repository: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    monkeypatch.setenv("GIT_CONFIG_COUNT", "1")
    monkeypatch.setenv("GIT_CONFIG_KEY_0", f"url.{repository.as_uri()}.insteadOf")
    monkeypatch.setenv("GIT_CONFIG_VALUE_0", f"{REPOSITORY_URL}.git")
    return repository


def test_status_uses_reachable_module_tags_and_shows_matching_changes(
    repository: Path,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    git(repository, "tag", "bazel/v0.9.0")
    git(repository, "tag", "bazel/v0.10.0")
    git(repository, "tag", "docs/v1.0.0")
    git(repository, "switch", "--quiet", "-c", "unrelated")
    commit(
        repository, "feat: unrelated branch", {".github/workflows/bazel.yml": "side"}
    )
    git(repository, "tag", "bazel/v99.0.0")
    git(repository, "switch", "--quiet", "main")
    git(repository, "rm", ".github/actions/_setup-bazel/action.yml")
    removal = commit(repository, "fix: remove setup action (#12)", {})
    commit(repository, "ci(bazel): housekeeping", {".github/workflows/bazel.yml": "ci"})
    git(repository, "tag", "bazel/v100.0.0-rc.1")
    git(repository, "tag", "v51.0.0")
    _ = (repository / ".github/labeler.yml").write_text("{}", encoding="utf-8")

    summary_path = tmp_path / "summary.md"
    monkeypatch.setenv("GITHUB_STEP_SUMMARY", str(summary_path))
    monkeypatch.setenv("GITHUB_REPOSITORY", "example/monorepo")
    monkeypatch.chdir(repository / "docs")
    assert main(["status"]) == 0
    status = capsys.readouterr().out
    assert "bazel/v0.10.0" in status
    assert "v51.0.0" not in status
    assert "fix: remove setup action (#12)" in status
    assert "housekeeping" not in status
    assert "unrelated branch" not in status
    assert "uncommitted" in status
    assert (
        f"- [{removal[:7]}]({REPOSITORY_URL}/commit/{removal}) "
        f"fix: remove setup action ([#12]({REPOSITORY_URL}/pull/12))"
        in summary_path.read_text(encoding="utf-8")
    )


def test_status_uses_highest_reachable_repository_tag_for_unreleased_modules(
    repository: Path,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    git(repository, "tag", "v0.9.0")
    commit(
        repository,
        "fix: update released workflow",
        {".github/workflows/bazel.yml": "released"},
    )
    git(repository, "tag", "-a", "v0.10.0", "-m", "Repository release")
    git(repository, "switch", "--quiet", "-c", "other")
    commit(
        repository, "fix: update other branch", {".github/workflows/bazel.yml": "other"}
    )
    git(repository, "tag", "v99.0.0")
    git(repository, "switch", "--quiet", "main")
    commit(
        repository,
        "feat: add pending workflow option",
        {".github/workflows/bazel.yml": "pending"},
    )
    git(repository, "tag", "v100.0.0-rc.1")
    labeler = (repository / ".github/labeler.yml").read_text(encoding="utf-8")
    commit(
        repository,
        "feat: add formatter",
        {
            ".github/labeler.yml": labeler
            + """\
formatter:
  - changed-files:
      - any-glob-to-any-file: "tools/formatter/**"
""",
            "tools/formatter/main.py": "# formatter",
        },
    )

    summary_path = tmp_path / "summary.md"
    monkeypatch.setenv("GITHUB_STEP_SUMMARY", str(summary_path))
    monkeypatch.setenv("GITHUB_REPOSITORY", "example/monorepo")
    monkeypatch.chdir(repository)
    assert main(["status"]) == 0
    output = capsys.readouterr().out
    rows = [" ".join(line.split()) for line in output.splitlines()]
    assert "bazel — v0.10.0 Changed" in rows
    assert "docs — v0.10.0 Unchanged" in rows
    assert "formatter — v0.10.0 New" in rows
    assert "feat: add pending workflow option" in output
    assert "fix: update released workflow" not in output
    assert "feat: add workflows" not in output
    summary = summary_path.read_text(encoding="utf-8")
    assert "| bazel | — | v0.10.0 | Changed |" in summary
    assert "| docs | — | v0.10.0 | Unchanged |" in summary
    assert "| formatter | — | v0.10.0 | New |" in summary


def test_status_fetches_full_history_from_shallow_checkout(
    repository: Path,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    git(repository, "tag", "bazel/v1.2.0")
    head = commit(
        repository, "fix: adjust triggers", {".github/workflows/bazel.yml": "change"}
    )
    checkout = tmp_path / "checkout"
    git(tmp_path, "clone", "--quiet", "--depth=1", repository.as_uri(), str(checkout))
    monkeypatch.chdir(checkout)

    assert main(["status"]) == 0
    output = capsys.readouterr().out
    assert "bazel bazel/v1.2.0 bazel/v1.2.0 Changed" in [
        " ".join(line.split()) for line in output.splitlines()
    ]
    assert git(checkout, "rev-parse", "--is-shallow-repository").strip() == "false"
    assert git(checkout, "rev-parse", "HEAD").strip() == head


def test_status_fetches_missing_tags_from_the_tracking_remote(
    repository: Path,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    git(repository, "tag", "-a", "v0.0.5", "-m", "Repository release")
    checkout = tmp_path / "checkout"
    git(
        tmp_path,
        "clone",
        "--quiet",
        "--no-tags",
        "-o",
        "upstream",
        repository.as_uri(),
        str(checkout),
    )
    git(checkout, "remote", "add", "origin", str(tmp_path / "missing"))
    head = git(checkout, "rev-parse", "HEAD")
    git(repository, "tag", "docs/v1.0.0")
    remote_head = commit(
        repository, "fix: adjust triggers", {".github/workflows/bazel.yml": "change"}
    )
    assert not git(checkout, "tag", "--list")
    monkeypatch.chdir(checkout)

    assert main(["status"]) == 0
    rows = [" ".join(line.split()) for line in capsys.readouterr().out.splitlines()]
    assert "bazel — v0.0.5 Unchanged" in rows
    assert "docs docs/v1.0.0 docs/v1.0.0 Unchanged" in rows
    assert git(checkout, "rev-parse", "HEAD") == head
    assert git(checkout, "rev-parse", "upstream/main").strip() == remote_head


def test_status_aborts_if_tags_cannot_be_fetched(
    repository: Path,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    git(repository, "remote", "add", "origin", str(tmp_path / "missing"))
    monkeypatch.chdir(repository)

    assert main(["status"]) == 1
    output = capsys.readouterr()
    assert not output.out
    assert "does not appear to be a git repository" in output.err


def test_file_rule_excludes_descendants_of_a_former_file(repository: Path) -> None:
    git(repository, "tag", "bazel/v1.2.0")
    workflow = ".github/workflows/bazel.yml"
    git(repository, "rm", "--", workflow)
    removal = commit(repository, "fix: remove workflow", {})
    head = commit(
        repository, "feat: add unrelated file", {f"{workflow}/README": "docs"}
    )
    report = module_report(
        repository,
        head,
        reachable_tags(repository, head),
        "bazel",
        read_modules(repository, head)["bazel"],
    )
    assert [change.sha for change in report.commits] == [removal]
    assert report.next_tag == "bazel/v1.2.1"


def test_globstar_matches_zero_or_more_directories(repository: Path) -> None:
    git(repository, "tag", "bazel/v1.2.0")
    config = """\
bazel.yml:
  - changed-files:
      - any-glob-to-any-file: "examples/**/sample-*.yml"
"""
    shallow_path = commit(
        repository,
        "fix: update base sample",
        {".github/labeler.yml": config, "examples/sample-1.yml": "sample"},
    )
    deep_path = commit(
        repository,
        "fix: update nested sample",
        {"examples/.nested/one/sample-2.yml": "sample"},
    )
    head = commit(
        repository,
        "feat: add sample directory",
        {"examples/sample-dir/a.yml": "unrelated"},
    )
    report = module_report(
        repository,
        head,
        reachable_tags(repository, head),
        "bazel",
        read_modules(repository, head)["bazel"],
    )
    assert [change.sha for change in report.commits] == [deep_path, shallow_path]


def test_duplicate_yaml_label_is_rejected(repository: Path) -> None:
    head = commit(
        repository,
        "chore: configure paths",
        {
            ".github/labeler.yml": """\
bazel:
  - changed-files:
      - any-glob-to-any-file: "old/**"
bazel:
  - changed-files:
      - any-glob-to-any-file: "new/**"
"""
        },
    )
    with pytest.raises(ValueError, match="duplicate mapping key"):
        read_modules(repository, head)


@pytest.mark.parametrize(
    ("previous_tag", "subject", "expected"),
    [
        (None, "feat!: introduce workflows", "bazel/v0.0.1"),
        ("v51.0.0", "feat!: introduce workflows", "bazel/v0.0.1"),
        ("bazel/v1.2.3", "chore(deps)!: remove support", "bazel/v2.0.0"),
        ("bazel/v1.2.3", "feat: add support", "bazel/v1.3.0"),
        ("bazel/v1.2.3", "Adjust workflow triggers", "bazel/v1.2.4"),
        ("bazel/v1.2.3", "chore(deps): update dependencies", None),
    ],
)
def test_version_is_derived_from_selected_commits(
    repository: Path, previous_tag: str | None, subject: str, expected: str | None
) -> None:
    if previous_tag:
        git(repository, "tag", previous_tag)
    head = commit(repository, subject, {".github/workflows/bazel.yml": "change"})
    paths = read_modules(repository, head)["bazel"]
    report = module_report(
        repository, head, reachable_tags(repository, head), "bazel", paths
    )
    assert report.next_tag == expected


@pytest.mark.parametrize("shallow", [False, True])
@pytest.mark.parametrize("create_fails", [False, True], ids=["success", "error"])
def test_draft_uses_remote_commit_and_preserves_existing_draft(
    github_remote: Path,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    shallow: bool,
    create_fails: bool,
) -> None:
    git(github_remote, "tag", "bazel/v1.2.0")
    commit(github_remote, "chore: prepare checkout", {"docs/index.md": "local"})
    checkout = tmp_path / "checkout"
    depth = ["--depth=1"] if shallow else []
    git(
        tmp_path,
        "clone",
        "--quiet",
        "--no-tags",
        *depth,
        github_remote.as_uri(),
        str(checkout),
    )
    remote_head = commit(
        github_remote,
        "feat: add setup flag (#27)",
        {".github/actions/_setup-bazel/action.yml": "new action"},
    )
    monkeypatch.chdir(checkout)
    local_head = git(checkout, "rev-parse", "HEAD")
    preview_args = ["draft", "bazel", "--repo", "example/monorepo", "--dry-run"]
    notes = f"- feat: add setup flag ([#27]({REPOSITORY_URL}/pull/27))\n"
    create_attempted = False
    created = False
    repo_view_arguments: list[tuple[str, ...]] = []

    def gh_stub(root: Path, *args: str, input_text: str | None = None) -> str:
        nonlocal create_attempted, created
        assert root == checkout
        if args[:2] == ("repo", "view"):
            repo_view_arguments.append(args)
            return f"example/monorepo\tmain\t{REPOSITORY_URL}"
        if args[0] == "api":
            assert "--paginate" in args
            assert not (create_attempted and create_fails)
            other_draft = f"docs/v1.3.0\t{REPOSITORY_URL}/releases/tag/docs%2Fv1.3.0"
            return (
                f"{other_draft}\nbazel/v1.3.0\t{DRAFT_URL}" if created else other_draft
            )
        assert not created, "An existing draft must not be changed or duplicated."
        assert args == (
            "release",
            "create",
            "--repo",
            "github.com/example/monorepo",
            "--draft",
            "--target",
            remote_head,
            "--title",
            "bazel/v1.3.0",
            "--notes-file",
            "-",
            "--",
            "bazel/v1.3.0",
        )
        assert input_text == notes
        create_attempted = True
        if create_fails:
            raise subprocess.CalledProcessError(1, args, stderr="draft creation failed")
        created = True
        return DRAFT_URL

    monkeypatch.setattr(github, "gh", gh_stub)
    assert main(preview_args) == 0
    assert git(checkout, "tag", "--list").splitlines() == ["bazel/v1.2.0"]
    assert git(checkout, "rev-parse", "--is-shallow-repository").strip() == "false"
    assert repo_view_arguments[-1][-2:] == ("--", "example/monorepo")
    assert not create_attempted
    assert capsys.readouterr().out == (
        f"Repository: {REPOSITORY_URL}\nTag: bazel/v1.3.0\nTarget: main @ {remote_head}\n"
        f"Comparison base: bazel/v1.2.0\n\n{notes}"
    )

    if create_fails:
        assert main(["draft", "bazel"]) == 1
        assert capsys.readouterr().err == "monorepo-release: draft creation failed\n"
    else:
        assert main(["draft", "bazel"]) == 0
        assert capsys.readouterr().out == f"{DRAFT_URL}\n"
        assert main(["draft", "bazel"]) == 0
        assert capsys.readouterr().out == f"Existing draft for bazel: {DRAFT_URL}\n"
    assert git(checkout, "rev-parse", "HEAD") == local_head
    assert git(checkout, "rev-parse", "origin/main") == local_head
    assert not git(checkout, "status", "--porcelain")


@pytest.mark.parametrize("pending", [False, True], ids=["no-changes", "first-release"])
def test_draft_compares_first_release_with_remote_repository_tag(
    github_remote: Path,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    pending: bool,
) -> None:
    checkout = tmp_path / "checkout"
    git(tmp_path, "clone", "--quiet", github_remote.as_uri(), str(checkout))
    commit(
        github_remote,
        "fix: update released workflow",
        {".github/workflows/bazel.yml": "released"},
    )
    git(github_remote, "tag", "v51.0.0")
    head = commit(
        github_remote, "feat: update unrelated docs", {"docs/index.md": "updated"}
    )
    if pending:
        head = commit(
            github_remote,
            "feat!: add setup flag",
            {".github/workflows/bazel.yml": "pending"},
        )
    monkeypatch.chdir(checkout)

    def gh_stub(root: Path, *args: str, input_text: str | None = None) -> str:
        assert root == checkout
        if args[:2] == ("repo", "view"):
            return f"example/monorepo\tmain\t{REPOSITORY_URL}"
        assert args[0] == "api" and input_text is None
        return ""

    monkeypatch.setattr(github, "gh", gh_stub)
    if not pending:
        assert main(["draft", "bazel"]) == 0
        assert capsys.readouterr().out == "No changes to draft for bazel.\n"
        assert main(["draft", "bazel", "--dry-run"]) == 0
        assert capsys.readouterr().out == (
            "No changes to draft for bazel.\nComparison base: v51.0.0\n"
        )
        return

    assert main(["draft", "bazel", "--dry-run"]) == 0
    assert capsys.readouterr().out == (
        f"Repository: {REPOSITORY_URL}\nTag: bazel/v0.0.1\nTarget: main @ {head}\n"
        f"Comparison base: v51.0.0\n\n"
        "- feat!: add setup flag\n"
    )

    git(checkout, "tag", "v99.0.0", head)
    assert main(["draft", "bazel", "--dry-run"]) == 0
    assert capsys.readouterr().out == (
        "No changes to draft for bazel.\nComparison base: v99.0.0\n"
    )


def test_draft_aborts_on_conflicting_local_tag(
    github_remote: Path,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    checkout = tmp_path / "checkout"
    git(tmp_path, "clone", "--quiet", github_remote.as_uri(), str(checkout))
    git(checkout, "tag", "v0.0.5")
    original_tag = git(checkout, "rev-parse", "v0.0.5")
    commit(
        github_remote,
        "fix: update the workflow",
        {".github/workflows/bazel.yml": "update"},
    )
    git(github_remote, "tag", "v0.0.5")
    monkeypatch.chdir(checkout)

    def gh_stub(root: Path, *args: str, input_text: str | None = None) -> str:
        assert root == checkout and args[:2] == ("repo", "view")
        assert input_text is None
        return f"example/monorepo\tmain\t{REPOSITORY_URL}"

    monkeypatch.setattr(github, "gh", gh_stub)
    assert main(["draft", "bazel"]) == 1
    assert "would clobber existing tag" in capsys.readouterr().err
    assert git(checkout, "rev-parse", "v0.0.5") == original_tag
