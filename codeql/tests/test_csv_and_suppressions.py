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

from score_codeql import csv_header, sarif_suppressions


def test_header_is_prepended(tmp_path: Path) -> None:
    csv_file = tmp_path / "r.csv"
    csv_file.write_text('"R1","d","error","m","/a.cpp","1","1","1","2"\n')
    assert csv_header.ensure_csv_header(csv_file) == 0
    lines = csv_file.read_text().splitlines()
    assert lines[0] == csv_header.CSV_HEADER
    assert len(lines) == 2


def test_existing_header_is_untouched(tmp_path: Path) -> None:
    csv_file = tmp_path / "r.csv"
    content = f"{csv_header.CSV_HEADER}\nrow\n"
    csv_file.write_text(content)
    assert csv_header.ensure_csv_header(csv_file) == 0
    assert csv_file.read_text() == content


def test_header_with_crlf_is_recognised(tmp_path: Path) -> None:
    csv_file = tmp_path / "r.csv"
    csv_file.write_bytes(f"{csv_header.CSV_HEADER}\r\nrow\r\n".encode())
    assert csv_header.ensure_csv_header(csv_file) == 0
    assert csv_file.read_bytes().count(csv_header.CSV_HEADER.encode()) == 1


def test_empty_file_gets_header_only(tmp_path: Path) -> None:
    csv_file = tmp_path / "r.csv"
    csv_file.write_text("")
    assert csv_header.main([str(csv_file)]) == 0
    assert csv_file.read_text() == f"{csv_header.CSV_HEADER}\n"


def test_missing_file_fails(tmp_path: Path) -> None:
    assert csv_header.ensure_csv_header(tmp_path / "missing.csv") == 1


def _sarif(rule: str, uri: str, line: int, suppress: bool = True) -> dict:
    result = {
        "ruleId": rule,
        "locations": [
            {
                "physicalLocation": {
                    "artifactLocation": {"uri": uri, "index": 0},
                    "region": {"startLine": line},
                }
            }
        ],
    }
    if suppress:
        result["suppressions"] = [{"kind": "inSource"}]
    return {"runs": [{"artifacts": [{"location": {"uri": uri}}], "results": [result]}]}


def _annotate(tmp_path: Path, source: str, rule: str, line: int) -> dict:
    root = tmp_path / "src dir"
    root.mkdir(exist_ok=True)
    (root / "a.cpp").write_text(source)
    sarif = tmp_path / "r.sarif"
    sarif.write_text(json.dumps(_sarif(rule, "a.cpp", line)))
    assert sarif_suppressions.annotate_sarif(sarif, root) == 0
    return json.loads(sarif.read_text())["runs"][0]["results"][0]["suppressions"][0]


def test_justification_is_extracted_from_line_comment(tmp_path: Path) -> None:
    source = "int x;\n// codeql[cpp/goto] intentional jump table\ngoto L;\n"
    assert (
        _annotate(tmp_path, source, "cpp/goto", 3)["justification"]
        == "intentional jump table"
    )


def test_justification_from_block_comment_and_dash(tmp_path: Path) -> None:
    source = "/* codeql[cpp/goto] - legacy code */\ngoto L;\n"
    assert _annotate(tmp_path, source, "cpp/goto", 2)["justification"] == "legacy code"


def test_space_before_bracket_is_accepted(tmp_path: Path) -> None:
    source = "// codeql [cpp/goto] ok\ngoto L;\n"
    assert _annotate(tmp_path, source, "cpp/goto", 2)["justification"] == "ok"


def test_rule_mismatch_and_missing_text_give_no_justification(tmp_path: Path) -> None:
    assert "justification" not in _annotate(
        tmp_path, "// codeql[cpp/other] text\ngoto L;\n", "cpp/goto", 2
    )
    assert "justification" not in _annotate(
        tmp_path, "// codeql[cpp/goto]\ngoto L;\n", "cpp/goto", 2
    )
    assert "justification" not in _annotate(tmp_path, "goto L;\n", "cpp/goto", 1)


def test_non_suppressed_results_are_not_touched(tmp_path: Path) -> None:
    (tmp_path / "a.cpp").write_text("// codeql[r] text\nx;\n")
    sarif = tmp_path / "r.sarif"
    sarif.write_text(json.dumps(_sarif("r", "a.cpp", 2, suppress=False)))
    assert sarif_suppressions.annotate_sarif(sarif, tmp_path) == 0
    assert "suppressions" not in json.loads(sarif.read_text())["runs"][0]["results"][0]


def test_percent_encoded_uri_and_file_scheme(tmp_path: Path) -> None:
    root = tmp_path / "my src"
    root.mkdir()
    (root / "b c.cpp").write_text("// codeql[r] spaced\nx;\n")
    sarif = tmp_path / "r.sarif"
    sarif.write_text(json.dumps(_sarif("r", "b%20c.cpp", 2)))
    assert sarif_suppressions.main([str(sarif), "--source-root", str(root)]) == 0
    got = json.loads(sarif.read_text())["runs"][0]["results"][0]["suppressions"][0]
    assert got["justification"] == "spaced"
    sarif.write_text(json.dumps(_sarif("r", f"file://{root}/b%20c.cpp", 2)))
    assert sarif_suppressions.main([str(sarif)]) == 0
    got = json.loads(sarif.read_text())["runs"][0]["results"][0]["suppressions"][0]
    assert got["justification"] == "spaced"


def test_invalid_sarif_fails(tmp_path: Path) -> None:
    bad = tmp_path / "bad.sarif"
    bad.write_text("not json")
    assert sarif_suppressions.annotate_sarif(bad, tmp_path) == 1
    assert sarif_suppressions.main([str(tmp_path / "missing.sarif")]) == 1
