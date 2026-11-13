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
from score_codeql import analysis
from score_codeql import config as cfg
from score_codeql.csv_header import CSV_HEADER
from score_codeql.errors import ScaError


def _calls(tmp_path: Path) -> list[list[str]]:
    return [json.loads(x) for x in (tmp_path / "calls.jsonl").read_text().splitlines()]


def test_default_threads_and_ram(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    assert analysis.default_threads(3) == 3
    monkeypatch.setattr(analysis.os, "cpu_count", lambda: 64)
    assert analysis.default_threads() == 16
    monkeypatch.setattr(analysis.os, "cpu_count", lambda: None)
    assert analysis.default_threads() == 1
    assert analysis.default_ram_mb(2048) == 2048
    meminfo = tmp_path / "meminfo"
    meminfo.write_text("MemTotal: 99999999 kB\nMemAvailable: 8388608 kB\n")
    assert analysis.default_ram_mb(None, meminfo) == 6144  # 75 % of 8 GiB
    meminfo.write_text("MemAvailable: 67108864 kB\n")
    assert analysis.default_ram_mb(None, meminfo) == analysis.MAX_RAM_MB
    meminfo.write_text("MemAvailable: 1024 kB\n")
    assert analysis.default_ram_mb(None, meminfo) == 1024
    assert analysis.default_ram_mb(None, tmp_path / "missing") == analysis.MAX_RAM_MB


REFS = [
    cfg.parse_pack_ref("codeql/cpp-queries@1.10.0:codeql-suites/cpp-code-scanning.qls"),
    cfg.parse_pack_ref("score/code-complexity-queries@1.0.0:suites/thresholds.qls"),
]


def test_run_queries_passes_union_of_refs_and_additional_packs(
    fake_codeql: Path, tmp_path: Path
) -> None:
    analysis.run_queries(fake_codeql, tmp_path / "db", REFS, "/cache:/local", 4, 2048)
    (call,) = _calls(tmp_path)
    assert call[:3] == ["database", "run-queries", str(tmp_path / "db")]
    assert "--additional-packs=/cache:/local" in call
    assert "--ram=2048" in call and call[call.index("-j") + 1] == "4"
    assert call[-2:] == [str(r) for r in REFS]


def test_finalize_database(fake_codeql: Path, tmp_path: Path) -> None:
    analysis.finalize_database(fake_codeql, tmp_path / "db", 2, 1024)
    (call,) = _calls(tmp_path)
    assert call[:2] == ["database", "finalize"] and "--ram=1024" in call


def test_interpret_sarif_flags_and_category(fake_codeql: Path, tmp_path: Path) -> None:
    analysis.interpret(
        fake_codeql, tmp_path / "db", REFS, fmt="sarif", output=tmp_path / "o.sarif",
        additional_packs="/c", threads=2, category="static-analysis-codeql/linux",
    )  # fmt: skip
    call = _calls(tmp_path)[0]
    for flag in (
        "--format=sarif-latest",
        "--no-sarif-minify",
        "--sarif-group-rules-by-pack",
        "--sarif-include-query-help=always",
        "--sublanguage-file-coverage",
        "--sarif-category=static-analysis-codeql/linux",
        "--additional-packs=/c",
    ):
        assert flag in call
    assert call[-2:] == [str(r) for r in REFS]


def test_interpret_csv_has_no_sarif_flags_and_rejects_unknown_format(
    fake_codeql: Path, tmp_path: Path
) -> None:
    analysis.interpret(
        fake_codeql, tmp_path / "db", REFS, fmt="csv", output=tmp_path / "o.csv",
        additional_packs="/c", threads=1,
    )  # fmt: skip
    call = _calls(tmp_path)[0]
    assert "--format=csv" in call and not any("sarif" in a for a in call)
    with pytest.raises(ValueError):
        analysis.interpret(
            fake_codeql, tmp_path / "db", REFS, fmt="xml", output=tmp_path / "o",
            additional_packs="/c", threads=1,
        )  # fmt: skip


@pytest.fixture
def recording_interpret(monkeypatch: pytest.MonkeyPatch) -> list[dict]:
    calls: list[dict] = []

    def fake(
        codeql,
        database,
        refs,
        *,
        fmt,
        output,
        additional_packs,
        threads,
        verbose=False,
        category=None,
    ):
        calls.append(
            {"fmt": fmt, "output": output, "threads": threads, "category": category}
        )
        if fmt == "csv":
            output.write_text('"R1","d"\n')
        else:
            output.write_text(json.dumps({"runs": [{"results": []}]}))

    monkeypatch.setattr(analysis, "interpret", fake)
    return calls


def test_generate_report_creates_sarif_and_csv_with_header(
    tmp_path: Path, recording_interpret: list[dict]
) -> None:
    sarif, csv_file = analysis.generate_report(
        Path("codeql"), tmp_path / "db", "my-report", REFS, tmp_path,
        additional_packs="/c", threads=2, source_root=tmp_path,
    )  # fmt: skip
    assert sarif.name == "my-report.sarif" and csv_file.name == "my-report.csv"
    assert csv_file.read_text().splitlines()[0] == CSV_HEADER
    assert [c["fmt"] for c in recording_interpret] == ["sarif", "csv"]


def test_threads_are_divided_across_report_workers(
    tmp_path: Path, recording_interpret: list[dict]
) -> None:
    by_report = {f"r{i}": REFS for i in range(3)}
    analysis.generate_reports(
        Path("codeql"), tmp_path / "db", by_report, tmp_path,
        additional_packs="/c", threads=12, source_root=tmp_path,
    )  # fmt: skip
    assert len(recording_interpret) == 6
    assert {c["threads"] for c in recording_interpret} == {4}
    recording_interpret.clear()
    analysis.generate_reports(
        Path("codeql"), tmp_path / "db", {"only": REFS}, tmp_path,
        additional_packs="/c", threads=12, source_root=tmp_path,
    )  # fmt: skip
    assert {c["threads"] for c in recording_interpret} == {12}
    recording_interpret.clear()
    analysis.generate_reports(
        Path("codeql"), tmp_path / "db", {f"s{i}": REFS for i in range(8)}, tmp_path,
        additional_packs="/c", threads=2, source_root=tmp_path,
    )  # fmt: skip
    assert {c["threads"] for c in recording_interpret} == {1}  # never below one


def test_report_failure_names_the_report(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    def boom(*_a, **_k):
        raise ScaError("interpret failed", 5)

    monkeypatch.setattr(analysis, "interpret", boom)
    with pytest.raises(ScaError, match="report generation failed") as info:
        analysis.generate_reports(
            Path("codeql"), tmp_path / "db", {"a": REFS, "b": REFS}, tmp_path,
            additional_packs="/c", threads=4, source_root=tmp_path,
        )  # fmt: skip
    assert info.value.exit_code == 5


def test_combined_sarif_is_a_single_interpretation_with_category(
    tmp_path: Path, recording_interpret: list[dict]
) -> None:
    out = tmp_path / "code-scanning.sarif"
    analysis.generate_combined_sarif(
        Path("codeql"), tmp_path / "db", REFS, out, category="cat/linux",
        additional_packs="/c", threads=8, source_root=tmp_path,
    )  # fmt: skip
    assert recording_interpret == [
        {"fmt": "sarif", "output": out, "threads": 8, "category": "cat/linux"}
    ]
    assert out.is_file()


def test_outside_workspace_classifies_uris(tmp_path: Path) -> None:
    source = tmp_path / "ws"
    execroot = tmp_path / "exec"
    toolchain = execroot / "external" / "gcc" / "include" / "c++"
    toolchain.mkdir(parents=True)
    (toolchain / "basic_string.h").write_text("// header\n")
    system = tmp_path / "sysroot" / "usr" / "include"
    system.mkdir(parents=True)
    (system / "stdio.h").write_text("// header\n")

    def outside(uri: str) -> bool:
        return analysis.outside_workspace(uri, source_root=source, execroot=execroot)

    # Workspace files: relative, and paths that are merely expressed badly.
    assert not outside("score/time/clock/foo.cpp")
    assert not outside("bazel-out/k8-fastbuild/bin/score/time/clock/foo.cpp")
    assert not outside(f"file://{source}/score/time/clock/foo.cpp")
    assert not outside(f"file://{execroot}/_main/score/time/clock/foo.cpp")
    assert not outside("")
    # A path that exists nowhere cannot be analysed: report it, do not drop it.
    assert not outside("/home/runner/execroot/_main/src/a.cpp")
    # Third-party code: external repositories, the toolchain and system headers.
    assert outside("external/score_baselibs+/score/concurrency/interruptible_wait.h")
    assert outside(f"file://{toolchain}/basic_string.h")
    assert outside(f"file://{system}/stdio.h")
    assert outside(f"file://{source}/../outside/foo.cpp") is False  # does not exist


def _sarif_with(tmp_path: Path, results: list[dict]) -> Path:
    path = tmp_path / "code-scanning.sarif"
    path.write_text(json.dumps({"version": "2.1.0", "runs": [{"results": results}]}))
    return path


def _located(rule: str, uri: str) -> dict:
    return {
        "ruleId": rule,
        "locations": [{"physicalLocation": {"artifactLocation": {"uri": uri}}}],
    }


def test_prune_external_results_keeps_only_workspace_locations(tmp_path: Path) -> None:
    source = tmp_path / "ws"
    source.mkdir()
    execroot = tmp_path / "exec"
    header = execroot / "external" / "gcc" / "include" / "stdio.h"
    sarif = _sarif_with(
        tmp_path,
        [
            _located("cpp/keep", "score/time/clock/foo.cpp"),
            _located("cpp/external", f"file://{header}"),
            _located("cpp/mislocated", "bazel-out/k8-fastbuild/bin/score/b.cpp"),
            {
                "ruleId": "cpp/mixed",
                "locations": [
                    {"physicalLocation": {"artifactLocation": {"uri": "score/a.cpp"}}}
                ],
                "relatedLocations": [
                    {
                        "physicalLocation": {
                            "artifactLocation": {"uri": f"file://{header}"}
                        }
                    },
                    {"physicalLocation": {"artifactLocation": {"uri": "score/a.cpp"}}},
                ],
            },
        ],
    )

    counts = analysis.prune_external_results(
        sarif, source_root=source, execroot=execroot
    )

    assert counts == {"results": 1, "locations": 2}
    document = json.loads(sarif.read_text(encoding="utf-8"))
    results = document["runs"][0]["results"]
    assert [r["ruleId"] for r in results] == [
        "cpp/keep",
        "cpp/mislocated",
        "cpp/mixed",
    ]
    # The external related location is gone, the workspace one is kept.
    assert len(results[2]["relatedLocations"]) == 1


def test_prune_external_results_keeps_results_without_a_location(
    tmp_path: Path,
) -> None:
    sarif = _sarif_with(tmp_path, [{"ruleId": "cpp/global", "message": {"text": "x"}}])
    counts = analysis.prune_external_results(sarif, source_root=tmp_path / "ws")
    assert counts == {"results": 0, "locations": 0}
    document = json.loads(sarif.read_text(encoding="utf-8"))
    assert [r["ruleId"] for r in document["runs"][0]["results"]] == ["cpp/global"]


def test_prune_external_results_rejects_invalid_json(tmp_path: Path) -> None:
    sarif = tmp_path / "code-scanning.sarif"
    sarif.write_text("{not json", encoding="utf-8")
    with pytest.raises(ScaError, match="cannot read SARIF"):
        analysis.prune_external_results(sarif, source_root=tmp_path)
