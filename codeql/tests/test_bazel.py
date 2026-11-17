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
from pathlib import Path

import pytest
from score_codeql import bazel as bz
from score_codeql.errors import ScaError


def test_bazel_target_args_prefixes_double_dash() -> None:
    assert bz.bazel_target_args(["//a/...", "-//a/tests/..."]) == [
        "--",
        "//a/...",
        "-//a/tests/...",
    ]
    assert bz.bazel_target_args([]) == []


def test_split_bazel_targets() -> None:
    positive, negative = bz.split_bazel_targets(["//a/...", "-//a/tests/...", "//b:c"])
    assert positive == ["//a/...", "//b:c"]
    assert negative == ["//a/tests/..."]


@pytest.mark.parametrize(
    "target, expected",
    [
        ("//src/tests/...", "/src/tests/**"),
        ("//src/tests", "/src/tests/**"),
        ("//src/tests/", "/src/tests/**"),
        ("//src:lib", None),
        ("//src/tests:all", None),
        ("//...", "/**"),
        ("//", None),
        ("@repo//x/...", None),
        ("src/tests", None),
    ],
)
def test_negative_target_to_exclude_glob(target: str, expected: str | None) -> None:
    assert bz.negative_target_to_exclude_glob(target) == expected


def test_build_flags_default_and_forced() -> None:
    default = bz.build_flags(False)
    assert "--remote_download_outputs=all" in default
    assert not any("MATERIALIZE" in f for f in default)
    forced = bz.build_flags(True)
    assert any(
        f.startswith("--action_env=_SCORE_CODEQL_MATERIALIZE_RUN_ID=") for f in forced
    )
    assert "--noremote_upload_local_results" in forced
    assert "--disk_cache=" in forced


def test_find_bazel_explicit_env_and_missing(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    tool = tmp_path / "mybazel"
    tool.write_text("#!/bin/sh\n")
    tool.chmod(0o755)
    monkeypatch.delenv("BAZEL", raising=False)
    assert bz.find_bazel(str(tool)) == str(tool)
    monkeypatch.setenv("BAZEL", str(tool))
    assert bz.find_bazel() == str(tool)
    with pytest.raises(ScaError, match="was not found"):
        bz.find_bazel("definitely-not-a-bazel")
    monkeypatch.delenv("BAZEL")
    monkeypatch.setenv("PATH", str(tmp_path / "empty"))
    with pytest.raises(ScaError, match="no Bazel executable"):
        bz.find_bazel()


def test_find_workspace_prefers_bazel_variable(tmp_path: Path) -> None:
    assert bz.find_workspace({"BUILD_WORKSPACE_DIRECTORY": str(tmp_path)}) == tmp_path


def test_find_workspace_walks_up_to_module_file(tmp_path: Path) -> None:
    (tmp_path / "MODULE.bazel").write_text("")
    nested = tmp_path / "a" / "b"
    nested.mkdir(parents=True)
    assert (
        bz.find_workspace({"BUILD_WORKING_DIRECTORY": str(nested)})
        == tmp_path.resolve()
    )
    with pytest.raises(ScaError, match="no Bazel workspace"):
        bz.find_workspace({"BUILD_WORKING_DIRECTORY": "/"})


def test_user_path_is_relative_to_working_directory(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("BUILD_WORKING_DIRECTORY", str(tmp_path))
    assert bz.user_path("out/../x") == tmp_path / "x"
    assert bz.user_path("/abs/path") == Path("/abs/path")


def _package(root: Path, rel: str, build: str = "BUILD") -> None:
    directory = root / rel
    directory.mkdir(parents=True, exist_ok=True)
    (directory / build).write_text("")


def test_auto_exclude_finds_test_and_mock_packages(tmp_path: Path) -> None:
    _package(tmp_path, "score/time/test")
    _package(tmp_path, "score/time/utests", "BUILD.bazel")
    _package(tmp_path, "libs/mock")
    _package(tmp_path, "score/time/src")
    (tmp_path / "tests").mkdir()  # directory name matches, but no BUILD file
    assert bz.auto_exclude_targets(tmp_path) == [
        "-//libs/mock/...",
        "-//score/time/test/...",
        "-//score/time/utests/...",
    ]


def test_auto_exclude_honours_custom_directory_names(tmp_path: Path) -> None:
    _package(tmp_path, "a/fixtures")
    _package(tmp_path, "a/test")
    assert bz.auto_exclude_targets(tmp_path, ["fixtures"]) == ["-//a/fixtures/..."]


def test_auto_exclude_prunes_bazel_symlinks_git_output_and_bazelignore(
    tmp_path: Path,
) -> None:
    _package(tmp_path, "real/test")
    _package(tmp_path, "bazel-out/k8-fastbuild/bin/test")
    _package(tmp_path, "bazel-workspace/test")
    _package(tmp_path, ".git/hooks/test")
    _package(tmp_path, "_codeql_sca/work/test")
    _package(tmp_path, "vendored/dep/test")
    _package(tmp_path, "node_modules_like/test")
    (tmp_path / ".bazelignore").write_text(
        "# comment\nvendored/dep\n\n./node_modules_like\n"
    )
    (tmp_path / "linked").symlink_to(tmp_path / "real", target_is_directory=True)
    result = bz.auto_exclude_targets(tmp_path, skip_dirs=[tmp_path / "_codeql_sca"])
    assert result == ["-//real/test/..."]


def test_auto_exclude_does_not_descend_into_symlinked_directories(
    tmp_path: Path,
) -> None:
    outside = tmp_path / "outside"
    _package(outside, "pkg/test")
    workspace = tmp_path / "ws"
    workspace.mkdir()
    (workspace / "ext").symlink_to(outside, target_is_directory=True)
    assert bz.auto_exclude_targets(workspace) == []


def test_resolve_labels_to_source_globs(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    answers = {
        "//a:lib": "cc_library rule //a:lib\n",
        'labels("srcs", //a:lib)': "source file //a:x.cpp\nfilegroup rule //a:fg\n",
        'labels("srcs", //a:fg)': "source file //a:sub/y.cpp\n",
    }

    class Result:
        def __init__(self, out: str) -> None:
            self.stdout = out

    def fake(args, cwd=None, **_kw):
        assert args[:2] == ["bazel", "query"]
        return Result(answers.get(args[2], ""))

    monkeypatch.setattr(bz, "run_capture", fake)
    assert bz.resolve_labels_to_source_globs(tmp_path, ["//a:lib"], "bazel") == [
        "/a/x.cpp",
        "/a/sub/y.cpp",
    ]
    assert bz.resolve_labels_to_source_globs(tmp_path, [], "bazel") == []


def test_resolve_labels_reports_query_failure(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    def fake(*_a, **_kw):
        raise ScaError("command failed", 7)

    monkeypatch.setattr(bz, "run_capture", fake)
    with pytest.raises(ScaError, match="negative label") as info:
        bz.resolve_labels_to_source_globs(tmp_path, ["//a:b"], "bazel")
    assert info.value.exit_code == 7


def test_walk_pruning_keeps_bazel_prefix_only_for_directories(tmp_path: Path) -> None:
    # A *file* called bazel-x or a dir merely containing the word is not pruned.
    _package(tmp_path, "my-bazel-tools/test")
    assert os.path.isdir(tmp_path / "my-bazel-tools")
    assert bz.auto_exclude_targets(tmp_path) == ["-//my-bazel-tools/test/..."]
