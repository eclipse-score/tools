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
from score_codeql import runfiles as rf
from score_codeql.errors import ScaError


class FakeRunfiles:
    def __init__(self, mapping: dict[str, str]) -> None:
        self.mapping = mapping

    def Rlocation(self, value: str) -> str | None:  # noqa: N802 - mirrors rules_python
        return self.mapping.get(value)


def test_missing_variable_names_the_variable() -> None:
    with pytest.raises(ScaError, match="SCORE_CODEQL_CLI is not set"):
        rf.resolve_env_path(rf.ENV_CLI, {})


def test_runfiles_location_is_resolved(tmp_path: Path) -> None:
    target = tmp_path / "bundle" / "codeql"
    target.parent.mkdir()
    target.write_text("")
    runfiles = FakeRunfiles({"codeql_bundle+/codeql/codeql": str(target)})
    got = rf.resolve_env_path(
        rf.ENV_CLI, {rf.ENV_CLI: "codeql_bundle+/codeql/codeql"}, runfiles=runfiles
    )
    assert got == target


def test_plain_path_fallback_and_missing_file(tmp_path: Path) -> None:
    existing = tmp_path / "file.txt"
    existing.write_text("")
    assert (
        rf.resolve_env_path("X", {"X": str(existing)}, runfiles=FakeRunfiles({}))
        == existing
    )
    with pytest.raises(ScaError, match=r"X='nowhere/file'"):
        rf.resolve_env_path("X", {"X": "nowhere/file"}, runfiles=FakeRunfiles({}))


def test_pack_dir_is_parent_of_qlpack(tmp_path: Path) -> None:
    qlpack = tmp_path / "pack" / "qlpack.yml"
    qlpack.parent.mkdir()
    qlpack.write_text("")
    assert rf.resolve_pack_dir("P", {"P": str(qlpack)}) == qlpack.parent


def test_codeql_override_wins_over_environment(tmp_path: Path) -> None:
    override = tmp_path / "codeql"
    override.write_text("")
    assert rf.resolve_codeql(str(override), {rf.ENV_CLI: "ignored"}) == override
    with pytest.raises(ScaError, match="--codeql-cli"):
        rf.resolve_codeql(str(tmp_path / "missing"), {})


def test_locate_relative_to_base(tmp_path: Path) -> None:
    (tmp_path / "r.toml").write_text("")
    assert (
        rf.locate("r.toml", base=tmp_path, runfiles=FakeRunfiles({}))
        == tmp_path / "r.toml"
    )
    assert rf.locate("zzz.toml", base=tmp_path, runfiles=FakeRunfiles({})) is None
    with pytest.raises(ScaError, match="--reports-file 'zzz.toml'"):
        rf.resolve_option_path("zzz.toml", "--reports-file", tmp_path)


def test_bcc_environment_requires_a_binary_and_wrapper(tmp_path: Path) -> None:
    wrapper = tmp_path / "w.sh"
    binary = tmp_path / "bcc"
    wrapper.write_text("")
    binary.write_text("")
    with pytest.raises(ScaError, match="bazel-compile-commands binary"):
        rf.bcc_environment({rf.ENV_BCC_BAZEL_WRAPPER: str(wrapper)})
    with pytest.raises(ScaError, match=rf.ENV_BCC_BAZEL_WRAPPER):
        rf.bcc_environment({rf.ENV_BCC: str(binary)})
    env = rf.bcc_environment(
        {rf.ENV_BCC: str(binary), rf.ENV_BCC_BAZEL_WRAPPER: str(wrapper)}
    )
    assert env == {rf.ENV_BCC: str(binary), rf.ENV_BCC_BAZEL_WRAPPER: str(wrapper)}
    override_only = rf.bcc_environment(
        {rf.ENV_BCC_OVERRIDE: "/usr/bin/true", rf.ENV_BCC_BAZEL_WRAPPER: str(wrapper)}
    )
    assert set(override_only) == {rf.ENV_BCC_BAZEL_WRAPPER}
