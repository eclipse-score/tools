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
from __future__ import annotations

import importlib.util
from pathlib import Path

import pytest


def _load_module():
    module_path = Path(__file__).resolve().parents[1] / "codeql_analysis.py"
    spec = importlib.util.spec_from_file_location("codeql_analysis", module_path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


codeql_analysis = _load_module()


def test_traced_build_bypasses_caches_and_forwards_tracer_environment():
    command = codeql_analysis.bazel_build_command(
        bazel="/w/bin/bazel",
        targets=["//score/...", "-//score/mock/..."],
        build_configs=["codeql", "qnx"],
        bazel_args=["--keep_going"],
        tracer_env={"SEMMLE_PRELOAD_libtrace": "x", "LD_PRELOAD": "y"},
        database=Path("/tmp/db"),
        seed="42",
    )
    assert command[:2] == ["/w/bin/bazel", "build"]
    assert "--nouse_action_cache" in command and "--disk_cache=" in command
    assert "--action_env=CODEQL_SEED_FORCE_RECOMPILE=42" in command
    assert "--sandbox_writable_path=/tmp/db" in command
    assert (
        command.index("--config=codeql")
        < command.index("--config=qnx")
        < command.index("--keep_going")
    )
    assert command[-5:] == [
        "--action_env=LD_PRELOAD",
        "--action_env=SEMMLE_PRELOAD_libtrace",
        "--",
        "//score/...",
        "-//score/mock/...",
    ]


def test_production_query_drops_testonly_and_excluded_targets():
    query = codeql_analysis.production_query(["//a/...", "//b:x", "-//a/gen/..."])
    selected = 'kind("^cc_", (//a/... + //b:x) - //a/gen/...)'
    assert query == (f"{selected} except attr(testonly, 1, {selected})")


def test_tracer_variables_are_prepended_to_existing_ones():
    env = codeql_analysis.merged_environment(
        {"LD_PRELOAD": "/own.so", "PATH": "/bin"}, {"LD_PRELOAD": "/trace.so"}
    )
    assert env == {"LD_PRELOAD": "/trace.so:/own.so", "PATH": "/bin"}


def test_pack_identity_and_missing_suite(tmp_path: Path):
    (tmp_path / "qlpack.yml").write_text(
        "name: codeql/misra-cpp-coding-standards\nversion: '2.63.0'\n"
    )
    (tmp_path / "codeql-suites").mkdir()
    (tmp_path / "codeql-suites" / "misra-cpp-default.qls").write_text("")

    root = codeql_analysis.pack_root(
        tmp_path / "qlpack.yml", "codeql-suites/misra-cpp-default.qls"
    )
    assert codeql_analysis.read_pack_identity(root) == (
        "codeql/misra-cpp-coding-standards",
        "2.63.0",
    )
    with pytest.raises(RuntimeError, match="missing"):
        codeql_analysis.pack_root(tmp_path / "qlpack.yml", "codeql-suites/other.qls")


def _parse(*extra: str):
    tools = [
        f"--{name}=/r/{name}"
        for name in (
            "codeql",
            "pack",
            "process-config",
            "recategorize",
            "analysis-report",
            "coding-standards-schema",
            "sarif-schema",
            "coding-standards-sources",
            "codescanning-config",
        )
    ]
    return codeql_analysis.parse_args([*tools, "--suite=s.qls", *extra])


def test_targets_accept_both_communication_and_repeated_flag_style():
    args = _parse(
        "--target=//a/...", "--target", "//b/...", "//c:x", "--target=-//a/gen/..."
    )
    assert args.target == ["//a/...", "//b/...", "//c:x", "-//a/gen/..."]


def test_phases_work_on_a_kept_database():
    assert _parse("--target=//a/...", "--database-path=db").database == "db"
    args = _parse("--phase=analyze-database", "--database=db")
    assert args.phase == "analyze-database" and args.target == []
    with pytest.raises(SystemExit):
        _parse("--target=//a/...", "--phase=create-database")
    with pytest.raises(SystemExit):
        _parse("--phase=create-database", "--database=db")


def test_coding_standards_root_is_four_levels_above_the_common_pack(tmp_path: Path):
    common = tmp_path / "cpp" / "common" / "src"
    common.mkdir(parents=True)
    (common / "qlpack.yml").write_text("")
    assert codeql_analysis.coding_standards_root(common / "qlpack.yml") == tmp_path


def test_sarif_is_normalized_in_place_without_leftovers(tmp_path: Path):
    sarif = tmp_path / "codeql.sarif"
    sarif.write_text(
        '{"runs": [{"tool": {"driver": {"rules": [{"id": "b"}, {"id": "a"}]}}}]}'
    )
    codeql_analysis.normalize_sarif_rule_order(sarif)
    assert [p.name for p in tmp_path.iterdir()] == ["codeql.sarif"]
    assert '"id": "a"' in sarif.read_text().split('"id": "b"')[0]


def test_sarif_rules_and_artifacts_are_sorted_with_indices_remapped():
    run = {
        "tool": {"driver": {"rules": [{"id": "b"}, {"id": "a"}]}},
        "artifacts": [{"location": {"uri": "z.cpp"}}, {"location": {"uri": "a.cpp"}}],
        "results": [
            {
                "ruleIndex": 0,
                "rule": {"id": "b", "index": 0},
                "locations": [{"physicalLocation": {"artifactLocation": {"index": 0}}}],
            },
            {
                "ruleIndex": 1,
                "locations": [
                    {
                        "physicalLocation": {
                            "artifactLocation": {"uri": "a.cpp", "index": 1}
                        }
                    }
                ],
            },
        ],
    }
    codeql_analysis.normalize_sarif_run(run)

    assert [rule["id"] for rule in run["tool"]["driver"]["rules"]] == ["a", "b"]
    assert [artifact["location"]["uri"] for artifact in run["artifacts"]] == [
        "a.cpp",
        "z.cpp",
    ]
    first, second = run["results"]
    assert first["ruleIndex"] == 1 and first["rule"]["index"] == 1
    assert first["locations"][0]["physicalLocation"]["artifactLocation"] == {"index": 1}
    assert second["ruleIndex"] == 0
    assert second["locations"][0]["physicalLocation"]["artifactLocation"] == {
        "uri": "a.cpp"
    }
