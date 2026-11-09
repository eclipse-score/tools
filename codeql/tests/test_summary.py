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
from score_codeql import summary
from score_codeql.errors import ScaError


def _result(
    rule: str, uri: str, level: str | None = None, suppressed: bool = False
) -> dict:
    result: dict = {
        "ruleId": rule,
        "locations": [{"physicalLocation": {"artifactLocation": {"uri": uri}}}],
    }
    if level:
        result["level"] = level
    if suppressed:
        result["suppressions"] = [{"kind": "inSource"}]
    return result


def _sarif(results: list[dict]) -> dict:
    rules = [{"id": "r/note", "defaultConfiguration": {"level": "note"}}]
    return {
        "runs": [
            {
                "tool": {"driver": {}, "extensions": [{"rules": rules}]},
                "results": results,
            }
        ]
    }


def test_count_results_by_severity_rule_file_and_suppression() -> None:
    counts = summary.count_results(
        _sarif(
            [
                _result("r/a", "src/a.cpp", "error"),
                _result("r/a", "src/a.cpp", "error"),
                _result("r/b", "src/b.cpp", "warning"),
                _result("r/note", "src/b.cpp"),
                _result("r/none", "src/c.cpp"),
                _result("r/s", "src/a.cpp", "error", suppressed=True),
            ]
        )
    )
    assert counts.by_severity["error"] == 2
    assert counts.by_severity["warning"] == 2  # explicit + SARIF default
    assert counts.by_severity["note"] == 1  # from the rule's default level
    assert counts.suppressed == 1
    assert counts.total == 5
    assert counts.by_rule.most_common(1) == [("r/a", 2)]
    assert counts.by_file["src/b.cpp"] == 2


def test_unknown_levels_count_as_notes() -> None:
    counts = summary.count_results(_sarif([_result("r/x", "a.cpp", "none")]))
    assert counts.by_severity["note"] == 1


def _compilers() -> dict:
    return {
        "status": "ok",
        "errors": [],
        "compilers": [
            {
                "basename": "x86_64-unknown-linux-gnu-g++",
                "version": "12.2.0",
                "linkage": "static",
                "via_wrapper": True,
                "commands": 57,
                "extracted": 57,
            }
        ],
    }


def _build(**overrides) -> str:
    counts = summary.count_results(_sarif([_result("r/a", "src/a|b.cpp", "error")]))
    options = dict(
        per_report={"codeql-report": counts},
        combined=counts,
        compilers=_compilers(),
        extraction={"total_logs": 57, "failed_logs": 0, "failed_pct": 0.0,
                    "logs_with_errors": 1, "issues_pct": 1.8, "unique_parse": 0, "passed": True},
        pins={
            "codeql_cli": "2.27.4",
            "codeql_cli_actual": "2.27.4",
            "resolved": [{"pack": "codeql/cpp-queries@1.10.0", "origin": "bundle"}],
        },
    )  # fmt: skip
    options.update(overrides)
    return summary.build_summary(**options)


def test_summary_starts_with_compiler_table() -> None:
    text = _build()
    assert (
        text.index("## Compiler")
        < text.index("## Extraction")
        < text.index("## Findings")
    )
    assert (
        "| x86_64-unknown-linux-gnu-g++ | 12.2.0 | static | via wrapper | 57 commands | 57 extracted |"
        in text
    )
    assert "Compiler gate: **ok**" in text


def test_summary_findings_pins_and_escaping() -> None:
    text = _build()
    assert "| codeql-report | 1 | 0 | 0 | 0 | 1 |" in text
    assert "| **combined (Code Scanning)** | 1 | 0 | 0 | 0 | 1 |" in text
    assert "| r/a | 1 |" in text
    assert "src/a\\|b.cpp" in text
    assert "| CodeQL CLI | 2.27.4 |" in text
    assert "| codeql/cpp-queries@1.10.0 | bundle |" in text
    assert "| Commands with issues | 1 (1.8%) |" in text


def test_summary_handles_missing_inputs_and_failed_gate() -> None:
    text = _build(compilers=None, extraction=None, pins=None, combined=None)
    assert "_No compiler information available._" in text
    assert "_No extraction statistics available._" in text
    assert "_No pin information available._" in text
    failed = _compilers() | {"status": "failed", "errors": ["wrong | compiler"]}
    assert "- wrong \\| compiler" in _build(compilers=failed)


def test_pins_fall_back_to_pinned_versions() -> None:
    text = _build(
        pins={"codeql_cli": "2.27.4", "packs": {"a/b": "1.0.0"}, "local_packs": {}},
    )
    assert "| a/b@1.0.0 |  |" in text


def test_summary_reports_results_dropped_outside_the_workspace() -> None:
    assert "outside the workspace" not in _build()
    text = _build(dropped_external=3)
    assert "`3` result(s) in code outside the workspace" in text
    assert "not uploaded to Code Scanning" in text


def test_summarize_directory_and_main(tmp_path: Path) -> None:
    sarif = json.dumps(_sarif([_result("r/a", "a.cpp", "warning")]))
    (tmp_path / "one.sarif").write_text(sarif)
    (tmp_path / "code-scanning.sarif").write_text(sarif)
    (tmp_path / "compilers.json").write_text(json.dumps(_compilers()))
    (tmp_path / "pins.json").write_text(json.dumps({"codeql_cli": "2.27.4"}))
    assert summary.main([str(tmp_path)]) == 0
    text = (tmp_path / "summary.md").read_text()
    assert "| one | 0 | 1 | 0 | 0 | 1 |" in text
    assert "combined" in text and "x86_64-unknown-linux-gnu-g++" in text


def test_unreadable_sarif_is_an_error(tmp_path: Path) -> None:
    bad = tmp_path / "bad.sarif"
    bad.write_text("nope")
    with pytest.raises(ScaError, match="cannot read SARIF"):
        summary.load_counts(bad)
    assert summary.main([str(tmp_path)]) == 1
