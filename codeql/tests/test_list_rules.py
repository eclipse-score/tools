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

import csv
import json
import subprocess
import sys
from pathlib import Path

import pytest
from score_codeql import config as cfg
from score_codeql import list_rules


def test_output_name() -> None:
    ref = cfg.parse_pack_ref(
        "codeql/cpp-queries@1.10.0:codeql-suites/cpp-code-scanning.qls"
    )
    assert list_rules.output_name(ref) == "cpp-queries-cpp-code-scanning"
    assert list_rules.output_name(cfg.parse_pack_ref("a/b@1.0.0")) == "b"
    assert list_rules.output_name(cfg.parse_pack_ref("a/b@1.0.0:x/q.ql")) == "b-q"


def test_relative_rule_strips_pack_root_and_suffix(tmp_path: Path) -> None:
    root = tmp_path / "pack"
    query = root / "rules" / "X" / "Q.ql"
    query.parent.mkdir(parents=True)
    query.write_text("")
    assert list_rules.relative_rule(str(query), root) == "rules/X/Q"
    outside = tmp_path / "other" / "Z.ql"
    assert list_rules.relative_rule(str(outside), root) == str(outside.with_suffix(""))


def test_write_rules_csv_is_sorted_with_header(tmp_path: Path) -> None:
    target = tmp_path / "out" / "queries.csv"
    list_rules.write_rules_csv(target, ["b/Q", "a/Q"])
    assert list(csv.reader(target.open())) == [["query"], ["a/Q"], ["b/Q"]]


def test_end_to_end_with_fake_cli(
    config_env: dict[str, Path],
    fake_codeql: Path,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    cache = tmp_path / "cache"
    queries = [
        str(cache / "codeql/cert-cpp-coding-standards/2.62.0/rules/A/Foo.ql"),
        str(cache / "codeql/cert-cpp-coding-standards/2.62.0/rules/B/Bar.ql"),
    ]
    monkeypatch.setenv("FAKE_CODEQL_QUERIES", json.dumps(queries))
    monkeypatch.setenv("SCORE_CODEQL_CLI", str(fake_codeql))
    # Bundled pack present in the fake distribution, the rest is "downloaded".
    bundled = tmp_path / "dist" / "qlpacks" / "codeql" / "cpp-queries" / "1.10.0"
    bundled.mkdir(parents=True)
    (bundled / "qlpack.yml").write_text("name: codeql/cpp-queries\nversion: 1.10.0\n")
    (bundled / "codeql-suites").mkdir()
    (bundled / "codeql-suites" / "cpp-code-scanning.qls").write_text("")
    out = tmp_path / "rules"
    rc = list_rules.main(
        ["--report=base", f"--output-dir={out}", f"--pack-cache-dir={cache}"]
    )
    assert rc == 0
    csv_file = out / "cert-cpp-coding-standards-cert-cpp-l1" / "queries.csv"
    assert list(csv.reader(csv_file.open()))[1:] == [["rules/A/Foo"], ["rules/B/Bar"]]
    assert (out / "cpp-queries-cpp-code-scanning" / "queries.csv").is_file()
    assert "2 rules" in capsys.readouterr().out


def test_module_entry_point_help() -> None:
    result = subprocess.run(
        [sys.executable, "-m", "score_codeql.list_rules", "--help"],
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode == 0 and "--output-dir" in result.stdout
    main_help = subprocess.run(
        [sys.executable, "-m", "score_codeql", "--help"],
        capture_output=True,
        text=True,
        check=False,
    )
    assert main_help.returncode == 0 and "--list-reports" in main_help.stdout
    assert "--default-target" not in main_help.stdout
