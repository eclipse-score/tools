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

from monorepo_release.src import github
from monorepo_release.src.cli import main
from monorepo_release.src.releases import (
    component_report,
    git,
    reachable_tags,
    read_components,
)

REPOSITORY_URL = "https://github.com/example/monorepo"
DRAFT_URL = f"{REPOSITORY_URL}/releases/tag/bazel%2Fv1.3.0"


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


def test_status_uses_reachable_component_tags_and_shows_matching_changes(
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
    commit(repository, "fix: remove setup action (#12)", {})
    commit(repository, "ci(bazel): housekeeping", {".github/workflows/bazel.yml": "ci"})
    git(repository, "tag", "bazel/v100.0.0-rc.1")
    _ = (repository / ".github/labeler.yml").write_text("{}", encoding="utf-8")

    summary_path = tmp_path / "summary.md"
    monkeypatch.setenv("GITHUB_STEP_SUMMARY", str(summary_path))
    monkeypatch.setenv("GITHUB_REPOSITORY", "example/monorepo")
    monkeypatch.chdir(repository / "docs")
    assert main(["status"]) == 0
    status = capsys.readouterr().out
    assert "bazel/v0.10.0" in status
    assert "fix: remove setup action (#12)" in status
    assert "housekeeping" not in status
    assert "unrelated branch" not in status
    assert "uncommitted" in status
    assert f"[#12]({REPOSITORY_URL}/pull/12)" in summary_path.read_text(
        encoding="utf-8"
    )


@pytest.mark.parametrize(
    ("previous_tag", "subject", "expected"),
    [
        (None, "feat!: introduce workflows", "bazel/v0.1.0"),
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
    paths = read_components(repository, head)["bazel"]
    report = component_report(
        repository, head, reachable_tags(repository, head), "bazel", paths
    )
    assert report.next_tag == expected


@pytest.mark.parametrize("shallow", [False, True])
def test_draft_uses_remote_commit_and_preserves_existing_draft(
    repository: Path,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    shallow: bool,
) -> None:
    git(repository, "tag", "bazel/v1.2.0")
    checkout = tmp_path / "checkout"
    depth = ["--depth=1"] if shallow else []
    git(tmp_path, "clone", "--quiet", *depth, repository.as_uri(), str(checkout))
    git(checkout, "tag", "bazel/v10.0.0")
    git(checkout, "tag", "bazel/v1.3.0")
    remote_head = commit(
        repository,
        "feat: add setup flag (#27)",
        {".github/actions/_setup-bazel/action.yml": "new action"},
    )
    monkeypatch.setenv("GIT_CONFIG_COUNT", "1")
    monkeypatch.setenv("GIT_CONFIG_KEY_0", f"url.{repository.as_uri()}.insteadOf")
    monkeypatch.setenv("GIT_CONFIG_VALUE_0", f"{REPOSITORY_URL}.git")
    monkeypatch.chdir(checkout)
    local_head = git(checkout, "rev-parse", "HEAD")
    created = False

    def gh_stub(root: Path, *args: str, input_text: str | None = None) -> str:
        nonlocal created
        assert root == checkout
        if args[:2] == ("repo", "view"):
            return f"example/monorepo\tmain\t{REPOSITORY_URL}"
        if args[0] == "api":
            assert "--paginate" in args
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
        assert input_text is not None
        assert (
            f"[{remote_head[:7]}]({REPOSITORY_URL}/commit/{remote_head})" in input_text
        )
        assert f"feat: add setup flag ([#27]({REPOSITORY_URL}/pull/27))" in input_text
        assert "add workflows" not in input_text
        created = True
        return DRAFT_URL

    monkeypatch.setattr(github, "gh", gh_stub)
    assert main(["draft", "bazel"]) == 0
    assert capsys.readouterr().out.strip() == DRAFT_URL
    assert main(["draft", "bazel"]) == 0
    assert capsys.readouterr().out.strip() == f"Existing draft for bazel: {DRAFT_URL}"
    assert git(checkout, "rev-parse", "HEAD") == local_head
    assert not git(checkout, "status", "--porcelain")
