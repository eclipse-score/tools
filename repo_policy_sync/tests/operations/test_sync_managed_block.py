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
from repo_policy_sync.src.models import Policy, SyncManagedBlock


def _policy(
    fake_repo: Path,
    source_text: str = "managed line\n",
    *,
    replace_lines: tuple[str, ...] = (),
    replace_line_globs: tuple[str, ...] = (),
) -> Policy:
    source_root = fake_repo.parent / "policy"
    source_root.mkdir(exist_ok=True)
    source_file = source_root / "managed-block.txt"
    source_file.write_text(source_text, encoding="utf-8")
    operation = SyncManagedBlock(
        path=Path(".gitignore"),
        source_root=source_root,
        source_file=source_file,
        marker="managed section",
        replace_lines=replace_lines,
        replace_line_globs=replace_line_globs,
    )
    return Policy(
        id="example",
        title="Example",
        description=None,
        bazel_condition=None,
        ensure=(operation,),
    )


def test_sync_managed_block_creates_a_missing_destination(fake_repo: Path) -> None:
    policy = _policy(fake_repo)

    evaluation = apply_policy(fake_repo, policy)

    assert evaluation.changes[0].path == Path(".gitignore")
    assert (fake_repo / ".gitignore").read_text() == (
        "# managed section\nmanaged line\n# end managed section\n"
    )
    assert apply_policy(fake_repo, policy).changes == ()


def test_sync_managed_block_replaces_only_the_marked_body_and_preserves_neighbors(
    fake_repo: Path,
) -> None:
    (fake_repo / ".gitignore").write_text(
        "before\n"
        "# managed section\nold body\n# end managed section\n"
        "_build-old\nubproject.toml\nafter\n"
    )
    policy = _policy(
        fake_repo,
        "new body\n",
        replace_line_globs=("*_build*", "*ubproject.toml*"),
    )

    apply_policy(fake_repo, policy)

    assert (fake_repo / ".gitignore").read_text() == (
        "before\n# managed section\nnew body\n# end managed section\nafter\n"
    )


def test_sync_managed_block_migrates_matching_legacy_lines_in_place(
    fake_repo: Path,
) -> None:
    (fake_repo / ".gitignore").write_text(
        "before\n# legacy docs heading\n/_build\nmiddle\n/docs/ubproject.toml\nafter\n"
    )
    policy = _policy(
        fake_repo,
        "managed line\n",
        replace_lines=("# legacy docs heading",),
        replace_line_globs=("*_build*", "*ubproject.toml*"),
    )

    apply_policy(fake_repo, policy)

    assert (fake_repo / ".gitignore").read_text() == (
        "before\n\n"
        "# managed section\nmanaged line\n# end managed section\n\n"
        "middle\nafter\n"
    )


@pytest.mark.parametrize(
    "content",
    (
        "# managed section\nbody\n",
        "# managed section\nbody\n# end managed section\n# end managed section\n",
        "# end managed section\n# managed section\n",
    ),
)
def test_sync_managed_block_rejects_incomplete_duplicate_or_misordered_markers(
    fake_repo: Path, content: str
) -> None:
    (fake_repo / ".gitignore").write_text(content)

    with pytest.raises(RepoPolicySyncError, match="exactly one ordered pair"):
        evaluate_policy(fake_repo, _policy(fake_repo))


def test_sync_managed_block_rejects_unmarked_existing_file_without_legacy_match(
    fake_repo: Path,
) -> None:
    (fake_repo / ".gitignore").write_text("unrelated entry\n")
    policy = _policy(
        fake_repo,
        replace_line_globs=("*_build*", "*ubproject.toml*"),
    )

    with pytest.raises(
        RepoPolicySyncError, match="at least one configured legacy line"
    ):
        apply_policy(fake_repo, policy)
    assert (fake_repo / ".gitignore").read_text() == "unrelated entry\n"
