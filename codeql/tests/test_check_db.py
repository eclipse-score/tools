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
from score_codeql import check_db
from score_codeql.errors import ScaError

PREFIX = "[E 19:04:17 1] "


def _log(
    db: Path,
    name: str,
    source: str = "a.cpp",
    *,
    error: bool = False,
    exit_ok: bool = True,
    internal: bool = False,
) -> None:
    directory = db / "log" / "extractor" / "00"
    directory.mkdir(parents=True, exist_ok=True)
    lines = [f"{PREFIX}Processed command line: extractor --mimic g++ -- {source}"]
    if error:
        lines.append(
            f'{PREFIX}Warning[extractor-c++]: In construct_text_message: "{source}", '
            "line 3: error: expected a ';'"
        )
        lines.append("    int x")
        lines.append("       ^")
    if internal:
        lines.append(
            f"{PREFIX}Warning[extractor-c++]: In add_macro_expansion: odd thing"
        )
    lines.append(
        f"{PREFIX}Extractor exited successfully"
        if exit_ok
        else f"{PREFIX}Extractor exiting with code 4"
    )
    (directory / f"{name}.log").write_text("\n".join(lines) + "\n")


def test_clean_database_passes(tmp_path: Path) -> None:
    _log(tmp_path, "1")
    report = check_db.analyze_database(tmp_path)
    assert report.passed and report.total_logs == 1 and report.unique_parse == 0


def test_parse_errors_of_failed_extraction_are_reported(tmp_path: Path) -> None:
    _log(tmp_path, "1", "bad.cpp", error=True, exit_ok=False)
    _log(tmp_path, "2")
    report = check_db.analyze_database(tmp_path)
    assert report.failed_logs == 1 and report.logs_with_errors == 1
    assert report.unique_parse == 1
    assert not report.passed
    text = check_db.format_report(report)
    assert "bad.cpp:3: error: expected a ';'" in text
    assert "expected a ';'" in text and "int x" in text
    assert "compiled from:" in text and "FAIL" in text


def test_parse_errors_of_successful_extraction_are_ignored(tmp_path: Path) -> None:
    _log(tmp_path, "1", error=True, exit_ok=True)
    assert check_db.analyze_database(tmp_path).unique_parse == 0


def test_thresholds(tmp_path: Path) -> None:
    for i in range(9):
        _log(tmp_path, f"ok{i}")
    _log(tmp_path, "bad", "bad.cpp", error=True, exit_ok=False)
    strict = check_db.analyze_database(
        tmp_path, max_failed_command_pct=5.0, max_commands_with_issues_pct=5.0,
        max_unique_parse_errors=0,
    )  # fmt: skip
    assert len(strict.failures) == 3
    relaxed = check_db.analyze_database(
        tmp_path, max_failed_command_pct=10.0, max_commands_with_issues_pct=10.0,
        max_unique_parse_errors=1,
    )  # fmt: skip
    assert relaxed.passed
    assert "10.0%" in check_db.format_report(relaxed)


def test_default_parse_error_limit_is_zero(tmp_path: Path) -> None:
    _log(tmp_path, "bad", error=True, exit_ok=False)
    assert check_db.analyze_database(tmp_path).failures == [
        "Unique parse errors 1 exceeds limit 0"
    ]


def test_internal_warnings_only_with_show_all(tmp_path: Path) -> None:
    _log(tmp_path, "1", internal=True)
    assert check_db.analyze_database(tmp_path).unique_internal == 0
    report = check_db.analyze_database(tmp_path, show_all_warnings=True)
    assert report.unique_internal == 1 and report.unique_parse == 0
    assert "Unique internal warns : 1" in check_db.format_report(report)


def test_command_fallback_for_source_name(tmp_path: Path) -> None:
    directory = tmp_path / "cpp" / "log" / "extractor"
    directory.mkdir(parents=True)
    (directory / "1.log").write_text(
        f"{PREFIX}Command: /x/extractor --mimic cc1plus -c main.cpp -o x\n"
        f"{PREFIX}Extractor exiting with code 1\n"
    )
    source, errors, _internal, failed = check_db.analyze_log(directory / "1.log")
    assert source == "main.cpp" and failed and errors == []


def test_invalid_databases_raise(tmp_path: Path) -> None:
    with pytest.raises(ScaError, match="Not a directory"):
        check_db.analyze_database(tmp_path / "missing")
    with pytest.raises(ScaError, match="Extractor log directory not found"):
        check_db.analyze_database(tmp_path)
    (tmp_path / "log" / "extractor").mkdir(parents=True)
    with pytest.raises(ScaError, match=r"No \*\.log files"):
        check_db.analyze_database(tmp_path)


def test_main_exit_codes(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    _log(tmp_path, "1", error=True, exit_ok=False)
    assert check_db.main([str(tmp_path)]) == 1
    assert check_db.main([str(tmp_path), "--no-fail"]) == 0
    assert check_db.main([str(tmp_path), "--max-unique-parse-errors=5"]) == 0
    assert check_db.main([str(tmp_path / "missing")]) == 1
    assert "EXTRACTION SUMMARY" in capsys.readouterr().out


def test_report_to_dict_for_summary(tmp_path: Path) -> None:
    _log(tmp_path, "1")
    data = check_db.analyze_database(tmp_path).to_dict()
    assert data["total_logs"] == 1 and data["passed"] is True
