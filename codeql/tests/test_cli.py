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
from score_codeql import analysis, check_db, compiler_check, extraction
from score_codeql import bazel as bz
from score_codeql import cli
from score_codeql.errors import ScaError


def test_negative_targets_are_collected_from_unknown_arguments() -> None:
    args = cli.parse_args(["//a/...", "-//a/tests/...", "--platform=qnx", "-//b:c"])
    assert args.targets == ["//a/...", "-//a/tests/...", "-//b:c"]
    assert args.platform == "qnx"


def test_unknown_option_is_rejected(capsys: pytest.CaptureFixture[str]) -> None:
    with pytest.raises(SystemExit) as info:
        cli.parse_args(["//a/...", "--no-such-option"])
    assert info.value.code == 2
    assert "unrecognized arguments: --no-such-option" in capsys.readouterr().err


def test_abbreviated_options_are_not_accepted() -> None:
    with pytest.raises(SystemExit):
        cli.parse_args(["//a/...", "--safety=QM"])


def test_macro_defaults_scalars_last_wins_and_repeatables_append() -> None:
    args = cli.parse_args(
        [
            "--platform=linux",
            "--safety-level=QM",
            "--config=x86_64-linux",
            "--max-failed-command-pct=2.5",
            "--default-target=//score/...",
            "--platform=qnx",
            "--safety-level=ASIL",
            "--config=extra",
            "--max-failed-command-pct=7",
        ]
    )
    assert args.platform == "qnx"
    assert args.safety_level == "ASIL"
    assert args.configs == ["x86_64-linux", "extra"]
    assert args.max_failed_command_pct == 7.0
    assert cli.effective_targets(args) == ["//score/..."]


def test_user_targets_replace_default_targets() -> None:
    args = cli.parse_args(
        [
            "--default-target=//score/...",
            "--default-target=-//score/x/...",
            "//other/...",
        ]
    )
    assert cli.effective_targets(args) == ["//other/..."]


def test_only_negative_user_targets_extend_default_targets() -> None:
    args = cli.parse_args(["--default-target=//score/...", "-//score/legacy/..."])
    assert cli.effective_targets(args) == ["//score/...", "-//score/legacy/..."]


def test_expect_compiler_empty_string_is_preserved() -> None:
    assert cli.parse_args(["--expect-compiler="]).expect_compiler == [""]


def test_print_pins(
    config_env: dict[str, Path], capsys: pytest.CaptureFixture[str]
) -> None:
    assert cli.main(["--print-pins"]) == 0
    data = json.loads(capsys.readouterr().out)
    assert data["codeql_cli"] == "2.27.4"
    assert data["packs"]["codeql/cert-cpp-coding-standards"] == "2.62.0"


def test_list_reports(
    config_env: dict[str, Path], capsys: pytest.CaptureFixture[str]
) -> None:
    assert cli.main(["--list-reports"]) == 0
    out = capsys.readouterr().out
    assert "base" in out and "QM: base, complexity" in out


def test_list_reports_includes_consumer_reports(
    config_env: dict[str, Path], tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    extra = tmp_path / "mine.toml"
    extra.write_text('[reports.mine]\npacks = ["codeql/cpp-queries@1.10.0:x.ql"]\n')
    assert cli.main(["--list-reports", f"--reports-file={extra}"]) == 0
    assert "mine" in capsys.readouterr().out


def test_missing_mandatory_variable_gives_clear_error(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.delenv("SCORE_CODEQL_PACKS", raising=False)
    assert cli.main(["--print-pins"]) == 1
    assert "SCORE_CODEQL_PACKS is not set" in capsys.readouterr().err


def test_run_without_targets_fails(
    config_env: dict[str, Path], capsys: pytest.CaptureFixture[str]
) -> None:
    assert cli.main(["--safety-level=QM"]) == 1
    assert "no Bazel targets" in capsys.readouterr().err


def test_unknown_preset_fails(
    config_env: dict[str, Path], capsys: pytest.CaptureFixture[str]
) -> None:
    assert cli.main(["//a/...", "--safety-level=SIL9"]) == 1
    assert "unknown safety level/preset 'SIL9'" in capsys.readouterr().err


def test_consumer_inputs_resolve_runfile_locations_and_paths(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("BUILD_WORKING_DIRECTORY", str(tmp_path))
    (tmp_path / "r.toml").write_text("")
    pack = tmp_path / "mypack"
    pack.mkdir()
    (pack / "qlpack.yml").write_text("name: acme/own\n")
    args = cli.parse_args(
        [
            "--reports-file=r.toml",
            "--additional-pack-qlpack=mypack/qlpack.yml",
            "--additional-pack-dir=mypack",
        ]
    )
    reports, packs = cli.consumer_inputs(args)
    assert reports == [tmp_path / "r.toml"]
    assert packs == [pack, pack]


def test_fail_on_severity_thresholds() -> None:
    from score_codeql.summary import SarifCounts

    counts = SarifCounts()
    counts.by_severity["warning"] = 2
    assert not cli._fail_on_findings("none", counts)
    assert not cli._fail_on_findings("error", counts)
    assert cli._fail_on_findings("warning", counts)
    counts.by_severity["error"] = 1
    assert cli._fail_on_findings("error", counts)


SARIF = {
    "runs": [
        {
            "tool": {
                "driver": {"name": "CodeQL"},
                "extensions": [{"rules": [{"id": "r/1"}]}],
            },
            "results": [
                {
                    "ruleId": "r/1",
                    "level": "error",
                    "locations": [
                        {"physicalLocation": {"artifactLocation": {"uri": "src/a.cpp"}}}
                    ],
                }
            ],
        }
    ]
}


@pytest.fixture
def pipeline(
    config_env: dict[str, Path],
    fake_codeql: Path,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> dict[str, list]:
    """Replace everything that would run Bazel/CodeQL queries with recorders."""
    workspace = tmp_path / "ws"
    workspace.mkdir()
    monkeypatch.setenv("BUILD_WORKSPACE_DIRECTORY", str(workspace))
    monkeypatch.setenv("BUILD_WORKING_DIRECTORY", str(workspace))
    monkeypatch.setenv("SCORE_CODEQL_CLI", str(fake_codeql))
    for name in ("BCC", "BCC_SH", "BCC_BAZEL_WRAPPER"):
        script = tmp_path / f"{name}.sh"
        script.write_text("")
        monkeypatch.setenv(f"SCORE_CODEQL_{name}", str(script))
    monkeypatch.setattr(bz, "find_bazel", lambda explicit=None: "bazel")
    monkeypatch.setattr(bz, "get_output_base", lambda *_: tmp_path / "output_base")
    calls: dict[str, list] = {"settings": [], "reports": [], "queries": []}

    monkeypatch.setattr(extraction, "init_database", lambda s: None)

    def fake_extraction(settings, compilers_json):
        calls["settings"].append(settings)
        gate = compiler_check.GateResult(
            "linux", settings.expect_patterns, None, replayed_commands=1
        )
        gate.compilers = [compiler_check.CompilerInfo("g++", "g++", 1, static=False)]
        return extraction.ExtractionOutcome(tmp_path, 1, gate)

    monkeypatch.setattr(extraction, "run_extraction", fake_extraction)
    monkeypatch.setattr(
        check_db,
        "analyze_database",
        lambda *_a, **_k: check_db.DbReport(total_logs=1),
    )
    monkeypatch.setattr(analysis, "finalize_database", lambda *a, **k: None)
    monkeypatch.setattr(compiler_check, "run_post_gate", lambda gate, **k: None)
    monkeypatch.setattr(
        analysis, "run_queries", lambda *a, **k: calls["queries"].append(a)
    )

    def fake_reports(codeql, db, by_report, results_dir, **kwargs):
        for name in by_report:
            calls["reports"].append(name)
            (results_dir / f"{name}.sarif").write_text(json.dumps(SARIF))

    def fake_combined(codeql, db, refs, output, **kwargs):
        calls["category"] = kwargs["category"]
        output.write_text(json.dumps(SARIF))

    monkeypatch.setattr(analysis, "generate_reports", fake_reports)
    monkeypatch.setattr(analysis, "generate_combined_sarif", fake_combined)
    return calls


def test_pipeline_writes_all_result_files(
    pipeline: dict[str, list], tmp_path: Path
) -> None:
    ws = tmp_path / "ws"
    rc = cli.main(
        [
            "--default-target=//score/...",
            "--safety-level=QM",
            "--pack-cache-dir",
            str(tmp_path / "cache"),
            "--threads=2",
            "--ram=1024",
        ]
    )
    assert rc == 0
    results = ws / "_codeql_sca" / "results"
    for name in (
        "base.sarif",
        "complexity.sarif",
        "code-scanning.sarif",
        "summary.md",
        "pins.json",
        "sarif-summary.txt",
        "extraction-summary.txt",
    ):
        assert (results / name).is_file(), name
    assert pipeline["reports"] == ["base", "complexity"]
    assert pipeline["category"] == "static-analysis-codeql/linux"
    settings = pipeline["settings"][0]
    assert settings.targets == ["//score/..."]
    assert settings.require_root == tmp_path / "output_base"
    assert settings.expect_gcc_major == 12
    assert settings.db_dir == ws / "_codeql_sca" / "work" / "db"
    pins = json.loads((results / "pins.json").read_text())
    assert {p["origin"] for p in pins["resolved"]} == {"downloaded", "local"}
    assert "## Compiler" in (results / "summary.md").read_text()


def test_pipeline_cli_targets_and_overrides_reach_extraction(
    pipeline: dict[str, list], tmp_path: Path
) -> None:
    (tmp_path / "ws" / "pkg" / "test").mkdir(parents=True)
    (tmp_path / "ws" / "pkg" / "test" / "BUILD").write_text("")
    rc = cli.main(
        [
            "--default-target=//score/...",
            "--platform=qnx",
            "--safety-level=ASIL",
            "//other/...",
            "--expect-compiler=",
            "--pack-cache-dir",
            str(tmp_path / "cache"),
            "--sarif-category=custom/cat",
        ]
    )
    assert rc == 0
    settings = pipeline["settings"][0]
    assert settings.targets == ["//other/...", "-//pkg/test/..."]
    assert settings.expect_patterns == []
    assert settings.expect_gcc_major is None and settings.require_root is None
    assert pipeline["category"] == "custom/cat"
    assert pipeline["reports"] == ["safety", "complexity"]


def test_pipeline_fail_on_findings_exit_code(
    pipeline: dict[str, list], tmp_path: Path
) -> None:
    rc = cli.main(
        [
            "//a/...",
            "--safety-level=QM",
            "--fail-on=error",
            "--pack-cache-dir",
            str(tmp_path / "c"),
        ]
    )
    assert rc == cli.EXIT_FINDINGS


def test_pipeline_rejects_non_workspace_relative_uris(
    pipeline: dict[str, list],
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    bad = json.loads(json.dumps(SARIF))
    bad["runs"][0]["results"][0]["locations"][0]["physicalLocation"][
        "artifactLocation"
    ]["uri"] = "/home/runner/execroot/_main/src/a.cpp"

    def fake_combined(codeql, db, refs, output, **kwargs):
        output.write_text(json.dumps(bad))

    monkeypatch.setattr(analysis, "generate_combined_sarif", fake_combined)
    rc = cli.main(
        ["//a/...", "--safety-level=QM", "--pack-cache-dir", str(tmp_path / "c")]
    )
    assert rc == 1
    assert "not workspace-relative" in capsys.readouterr().err


def test_pipeline_cli_version_mismatch_is_fatal(
    pipeline: dict[str, list],
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    monkeypatch.setenv("FAKE_CODEQL_VERSION", "2.26.0")
    assert cli.main(["//a/...", "--pack-cache-dir", str(tmp_path / "c")]) == 1
    assert "does not match the pinned version 2.27.4" in capsys.readouterr().err
    assert not pipeline["settings"]


def test_main_converts_sca_errors_to_exit_codes(
    pipeline: dict[str, list], monkeypatch: pytest.MonkeyPatch
) -> None:
    def boom(settings, compilers_json):
        raise ScaError("extraction exploded", 9)

    monkeypatch.setattr(extraction, "run_extraction", boom)
    assert cli.main(["//a/..."]) == 9
