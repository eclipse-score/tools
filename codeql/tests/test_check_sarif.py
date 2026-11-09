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
from score_codeql import check_sarif
from score_codeql.errors import ScaError


def _result(uri: str, rule: str = "cpp/x", **extra) -> dict:
    return {
        "ruleId": rule,
        "locations": [{"physicalLocation": {"artifactLocation": {"uri": uri}}}],
        **extra,
    }


def _run(
    results: list[dict], metrics: list[dict] | None = None, rules: int = 1
) -> dict:
    return {
        "tool": {
            "driver": {"rules": []},
            "extensions": [{"rules": [{"id": f"r{i}"} for i in range(rules)]}],
        },
        "results": results,
        "properties": {"metricResults": metrics or []},
    }


@pytest.mark.parametrize(
    "uri",
    ["src/a.cpp", "score/time/a b.cpp", "a/b/c.cpp", "src/a%20b.cpp"],
)
def test_workspace_relative_uris_are_accepted(uri: str) -> None:
    assert check_sarif.uri_problem(uri) is None


@pytest.mark.parametrize(
    "uri, reason",
    [
        ("", "empty"),
        ("/home/runner/work/x.cpp", "absolute path"),
        ("file:///home/x.cpp", "scheme"),
        ("https://example.com/x.cpp", "scheme"),
        ("C:\\src\\x.cpp", "scheme"),
        ("../other/x.cpp", "escapes"),
        ("src/../../x.cpp", "escapes"),
        ("bazel-out/k8-fastbuild/bin/gen.cc", "Bazel output"),
        ("bazel-bin/x.cc", "Bazel output"),
        ("external/gtest/x.cc", "external"),
        ("%2Fabs%2Fpath.cc", "absolute path"),
    ],
)
def test_non_relative_uris_are_rejected(uri: str, reason: str) -> None:
    assert reason in (check_sarif.uri_problem(uri) or "")


def test_check_result_uris_lists_distinct_offenders_with_rule() -> None:
    runs = [
        _run(
            [
                _result("src/ok.cpp"),
                _result("/abs/a.cpp", "cpp/one"),
                _result("/abs/a.cpp", "cpp/two"),
                _result("bazel-out/x/bin/g.cc", "cpp/three"),
            ]
        )
    ]
    problems = check_sarif.check_result_uris(runs)
    assert len(problems) == 2
    assert any("/abs/a.cpp" in p and "cpp/one" in p for p in problems)


def test_related_locations_are_checked() -> None:
    result = _result("src/ok.cpp")
    result["relatedLocations"] = [
        {"physicalLocation": {"artifactLocation": {"uri": "external/dep/x.h"}}}
    ]
    assert len(check_sarif.check_result_uris([_run([result])])) == 1


def _write(tmp_path: Path, runs: list[dict]) -> Path:
    path = tmp_path / "r.sarif"
    path.write_text(json.dumps({"runs": runs}))
    return path


def test_main_fails_on_bad_uri_even_with_no_fail(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    sarif = _write(tmp_path, [_run([_result("/abs/a.cpp")])])
    assert check_sarif.main([str(sarif), "--no-fail"]) == 1
    assert "[uri check] FAIL" in capsys.readouterr().out
    assert check_sarif.main([str(sarif), "--no-uri-check"]) == 0


def test_main_passes_clean_sarif_and_prints_frequencies(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    sarif = _write(
        tmp_path, [_run([_result("a.cpp", "cpp/x"), _result("b.cpp", "cpp/x")])]
    )
    assert check_sarif.main([str(sarif)]) == 0
    assert "    2  cpp/x" in capsys.readouterr().out


def test_quality_metrics_fail_unless_no_fail(tmp_path: Path) -> None:
    metrics = [
        {
            "ruleId": "cpp/telemetry/extraction-metrics",
            "message": {"text": "compiler errors"},
            "value": 3,
        }
    ]
    sarif = _write(tmp_path, [_run([], metrics)])
    assert check_sarif.main([str(sarif)]) == 1
    assert check_sarif.main([str(sarif), "--no-fail"]) == 0


def test_database_quality_metric_below_100_percent_fails(tmp_path: Path) -> None:
    metrics = [
        {
            "ruleId": "cpp/telemetry/database-quality",
            "message": {"text": "Percentage of compilations without errors"},
            "value": 96.5,
        }
    ]
    report = check_sarif.analyze([_run([], metrics)])
    assert report.quality_failed
    assert "96.5%" in report.text


def test_no_rules_is_a_quality_failure() -> None:
    assert check_sarif.analyze([_run([], rules=0)]).quality_failed


def test_invalid_input_is_reported(tmp_path: Path) -> None:
    for content, message in (
        ("not json", "invalid JSON"),
        ("[]", "JSON object"),
        ('{"runs": {}}', "JSON array"),
        ('{"runs": []}', "no runs"),
    ):
        path = tmp_path / "bad.sarif"
        path.write_text(content)
        with pytest.raises(ScaError, match=message):
            check_sarif.load_sarif(path)
    with pytest.raises(ScaError, match="not found"):
        check_sarif.load_sarif(tmp_path / "missing.sarif")
    assert check_sarif.main([str(tmp_path / "missing.sarif")]) == 1


def test_notifications_and_ruleset_in_verbose_mode(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    run = _run([_result("a.cpp")])
    run["invocations"] = [
        {
            "toolExecutionNotifications": [
                {
                    "level": "warning",
                    "message": {"text": "careful\nmore"},
                    "descriptor": {"id": "n/1"},
                },
                {"level": "note", "message": {"text": "ignored"}},
            ]
        }
    ]
    sarif = _write(tmp_path, [run])
    assert check_sarif.main([str(sarif), "-v"]) == 0
    out = capsys.readouterr().out
    assert "[toolExecutionNotification] n/1: careful" in out
    assert "ignored" not in out
    assert "[ruleset] r0" in out
