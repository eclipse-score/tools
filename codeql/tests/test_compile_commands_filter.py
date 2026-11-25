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
from pathlib import Path

import pytest
from score_codeql import compile_commands_filter as flt
from score_codeql.errors import ScaError


def _entry(file: str, output: str | None = None, command: bool = False) -> dict:
    args = ["g++", "-c", file, "-o", output or f"bazel-out/k8-fastbuild/bin/x/{file}.o"]
    if command:
        return {"directory": "/exec", "command": " ".join(args), "file": file}
    return {"directory": "/exec", "arguments": args, "file": file}


def _write(path: Path, entries: list) -> Path:
    path.write_text(json.dumps(entries), encoding="utf-8")
    return path


@pytest.mark.parametrize(
    "file",
    [
        "external/gtest/src/a.cc",
        "third_party/x/a.cc",
        "score/test/a.cpp",
        "a/tests/b.cpp",
        "score/foo_test.cpp",
        "x/mock/m.cpp",
        "x/mocks/m.cpp",
    ],
)
def test_default_globs_exclude(file: str) -> None:
    assert flt.matches_any(flt.entry_path_candidates(_entry(file)), flt.EXCLUDE_GLOBS)


@pytest.mark.parametrize("file", ["score/time/clock.cpp", "score/latest/x.cpp"])
def test_default_globs_keep_production_sources(file: str) -> None:
    entry = _entry(file, output=f"bazel-out/k8-fastbuild/bin/{file}.o")
    assert not flt.matches_any(flt.entry_path_candidates(entry), flt.EXCLUDE_GLOBS)


def test_bazel_out_prefix_is_normalised() -> None:
    assert (
        flt.normalize_file_path("bazel-out/k8-fastbuild/bin/pkg/gen.cc") == "pkg/gen.cc"
    )
    assert flt.normalize_file_path("bazel-out/x/genfiles/pkg/gen.cc") == "pkg/gen.cc"
    assert flt.normalize_file_path("pkg/a.cc") == "pkg/a.cc"
    assert flt.matches_any(["bazel-out/k8-fastbuild/bin/legacy/gen.cc"], ["/legacy/**"])


def test_output_path_reveals_owning_package() -> None:
    entry = _entry(
        "lib/impl.cpp", output="bazel-out/k8-fastbuild/bin/app/tests/_objs/t/impl.o"
    )
    assert flt.matches_any(flt.entry_path_candidates(entry), flt.EXCLUDE_GLOBS)


def test_command_string_entries_are_supported() -> None:
    entry = _entry(
        "lib/impl.cpp", output="bazel-out/k/bin/app/mock/_objs/m.o", command=True
    )
    assert "bazel-out/k/bin/app/mock/_objs/m.o" in flt.entry_path_candidates(entry)


def test_filter_commands_writes_kept_entries(tmp_path: Path) -> None:
    source = _write(
        tmp_path / "cc.json",
        [
            _entry("score/a.cpp", "o/a.o"),
            _entry("score/test/b.cpp"),
            _entry("legacy/c.cpp", "o/c.o"),
        ],
    )
    output = tmp_path / "out" / "filtered.json"
    kept, removed = flt.filter_commands(source, output, ["/legacy/**"])
    assert (kept, removed) == (1, 2)
    assert [e["file"] for e in json.loads(output.read_text())] == ["score/a.cpp"]


def test_filter_default_output_next_to_input(tmp_path: Path) -> None:
    source = _write(tmp_path / "cc.json", [_entry("a.cpp", "o/a.o")])
    flt.filter_commands(source)
    assert (tmp_path / "compile_commands.filtered.json").is_file()


def test_filter_rejects_bad_input(tmp_path: Path) -> None:
    with pytest.raises(ScaError, match="not found"):
        flt.load_entries(tmp_path / "missing.json")
    bad = tmp_path / "bad.json"
    bad.write_text("{not json")
    with pytest.raises(ScaError, match="invalid JSON"):
        flt.load_entries(bad)
    bad.write_text('{"a": 1}')
    with pytest.raises(ScaError, match="expected a JSON array"):
        flt.load_entries(bad)
    bad.write_text("[1]")
    with pytest.raises(ScaError, match="not a JSON object"):
        flt.load_entries(bad)


def test_main_exit_codes(tmp_path: Path) -> None:
    source = _write(tmp_path / "cc.json", [_entry("a.cpp", "o/a.o")])
    out = tmp_path / "f.json"
    assert flt.main([str(source), "-o", str(out), "--exclude-glob", "/a.cpp"]) == 0
    assert json.loads(out.read_text()) == []
    assert flt.main([str(tmp_path / "nope.json")]) == 1
