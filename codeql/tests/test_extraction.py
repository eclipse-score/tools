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
import stat
import sys
from pathlib import Path

import pytest
from score_codeql import bazel as bz
from score_codeql import compiler_check, extraction, replay
from score_codeql.errors import ScaError

GPP = "x86_64-unknown-linux-gnu-g++"


def _settings(tmp_path: Path, codeql: Path = Path("codeql"), **overrides):
    workspace = tmp_path / "ws"
    work = tmp_path / "out" / "work"
    workspace.mkdir(exist_ok=True)
    options = dict(
        codeql=codeql, bazel="bazel", workspace=workspace, work_dir=work,
        db_dir=work / "db", targets=["//score/...", "-//score/legacy/..."],
        configs=["x86_64-linux"], bazel_options=["--copt=-O0"], platform="linux",
        replay_jobs=4, exclude_globs=["/gen/**"], expect_patterns=compiler_check.DEFAULT_EXPECTED_COMPILERS["linux"],
        require_root=tmp_path / "base", expect_gcc_major=12, max_failed_command_pct=5.0,
        bcc_sh=tmp_path / "bcc.sh", bcc_env={"SCORE_CODEQL_BCC": "/bcc"},
    )  # fmt: skip
    options.update(overrides)
    return extraction.ExtractionSettings(**options)


def test_bcc_command_layout(tmp_path: Path) -> None:
    settings = _settings(tmp_path)
    command = extraction.bcc_command(settings, ["//score/..."])
    assert command == [
        str(tmp_path / "bcc.sh"),
        "--output-dir",
        str(settings.work_dir),
        "--",
        "--config=x86_64-linux",
        "--bazelopt=--copt=-O0",
        "-o",
        str(settings.work_dir / "compile_commands.json"),
        "--",
        "//score/...",
    ]
    with pytest.raises(ScaError):
        extraction.bcc_command(_settings(tmp_path, bcc_sh=None), [])


def test_generate_compile_commands_runs_in_workspace_with_env(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    settings = _settings(tmp_path, bazel="/opt/bazelisk")
    seen = {}

    def fake_run(command, *, cwd=None, env=None):
        seen.update(cwd=cwd, env=env)
        settings.compile_commands.parent.mkdir(parents=True, exist_ok=True)
        settings.compile_commands.write_text("[]")

    monkeypatch.setattr(extraction, "run_cmd", fake_run)
    extraction.generate_compile_commands(settings, ["//score/..."])
    assert seen["cwd"] == settings.workspace
    assert seen["env"]["BCC_BAZEL"] == "/opt/bazelisk"
    assert seen["env"]["SCORE_CODEQL_BCC"] == "/bcc"


def test_generate_compile_commands_requires_output(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(extraction, "run_cmd", lambda *a, **k: None)
    with pytest.raises(ScaError, match="did not produce"):
        extraction.generate_compile_commands(_settings(tmp_path), [])


def test_build_targets_command(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    captured = {}
    monkeypatch.setattr(
        extraction,
        "run_cmd",
        lambda command, cwd=None, env=None: captured.update(c=command, cwd=cwd),
    )
    extraction.build_targets(_settings(tmp_path))
    command = captured["c"]
    assert command[:2] == ["bazel", "build"]
    assert "--config=x86_64-linux" in command and "--copt=-O0" in command
    assert command[-3:] == ["--", "//score/...", "-//score/legacy/..."]
    assert "--remote_download_outputs=all" in command
    assert not any("MATERIALIZE" in c for c in command)


def test_rewrite_directory_and_validation(tmp_path: Path) -> None:
    source = tmp_path / "cc.json"
    source.write_text(
        json.dumps([{"directory": "/ws", "arguments": ["g++"], "file": "a.cpp"}])
    )
    target = tmp_path / "exec.json"
    extraction.rewrite_compilation_database_directory(source, target, Path("/execroot"))
    assert json.loads(target.read_text())[0]["directory"] == "/execroot"
    assert extraction.validate_compilation_database(target) == 1
    source.write_text("[]")
    with pytest.raises(ScaError, match="No compiler commands remain"):
        extraction.validate_compilation_database(source)


def test_validate_extraction_output(tmp_path: Path) -> None:
    with pytest.raises(ScaError, match="No CodeQL C/C\\+\\+ extractor logs"):
        extraction.validate_extraction_output(tmp_path)
    (tmp_path / "log" / "extractor").mkdir(parents=True)
    extraction.validate_extraction_output(tmp_path)


def test_compute_excludes_maps_packages_labels_and_extra_globs(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(
        bz, "resolve_labels_to_source_globs", lambda ws, labels, b: ["/x/y.cpp"]
    )
    globs = extraction.compute_excludes(
        _settings(tmp_path), ["//score/legacy/...", "//pkg:lib", "weird"]
    )
    assert globs == ["/score/legacy/**", "/x/y.cpp", "/gen/**"]


def test_tracer_environment_exposes_the_package(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import score_codeql

    monkeypatch.setenv("PYTHONPATH", "/existing")
    env = extraction.tracer_environment("linux")
    package_parent = str(Path(score_codeql.__file__).resolve().parent.parent)
    assert env["PYTHONPATH"].split(":") == [package_parent, "/existing"]
    monkeypatch.delenv("PYTHONPATH")
    assert extraction.tracer_environment("linux")["PYTHONPATH"] == package_parent


def test_replay_runs_under_trace_command(
    tmp_path: Path, fake_codeql: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    settings = _settings(tmp_path, codeql=fake_codeql)
    (settings.db_dir / "log" / "extractor").mkdir(parents=True)
    extraction.replay_under_tracer(settings, tmp_path / "exec", tmp_path / "wrapper")
    (call,) = [
        json.loads(x) for x in (tmp_path / "calls.jsonl").read_text().splitlines()
    ]
    assert call[:3] == ["database", "trace-command", "--"]
    assert call[3] == str(settings.db_dir)
    assert call[4:7] == [sys.executable, "-m", "score_codeql.replay"]
    assert "--no-output" in call and "--continue" in call
    assert "--max-failed-pct=5.0" in call
    assert f"--wrapper={tmp_path / 'wrapper'}" in call
    assert call[-1] == str(settings.filtered)


def test_replay_failure_without_extractor_logs_is_detected(
    tmp_path: Path, fake_codeql: Path
) -> None:
    settings = _settings(tmp_path, codeql=fake_codeql)
    with pytest.raises(ScaError, match="did not observe any compiler"):
        extraction.replay_under_tracer(settings, tmp_path, None)


@pytest.fixture
def pipeline(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    base = tmp_path / "base"
    execroot = base / "execroot" / "_main"
    execroot.mkdir(parents=True)
    compiler = base / "external" / "tc" / "bin" / GPP
    compiler.parent.mkdir(parents=True)
    compiler.write_bytes(b"\x7fELFSTATIC")
    compiler.chmod(compiler.stat().st_mode | stat.S_IXUSR)
    state = {"compiler": compiler, "base": base, "execroot": execroot, "static": True}

    def fake_generate(settings, positive):
        entries = [
            {
                "directory": str(settings.workspace),
                "arguments": [str(state["compiler"]), "-c", "a.cpp"],
                "file": "a.cpp",
            }
        ]
        settings.compile_commands.write_text(json.dumps(entries))

    def fake_replay(settings, execroot_, wrapper):
        state["wrapper"] = wrapper
        settings.replay_report.write_text(
            json.dumps({"static_compilers": {str(state["compiler"]): {"rewritten": 1}}})
            if state["static"]
            else "{}"
        )

    monkeypatch.setattr(extraction, "build_targets", lambda s: None)
    monkeypatch.setattr(extraction, "generate_compile_commands", fake_generate)
    monkeypatch.setattr(extraction, "replay_under_tracer", fake_replay)
    monkeypatch.setattr(bz, "get_execution_root", lambda bazel, ws: execroot)
    monkeypatch.setattr(
        compiler_check,
        "probe_compiler",
        lambda p, c, env=None: compiler_check.Probe("12.2.0", 12, False, True),
    )
    monkeypatch.setattr(replay, "is_elf_static", lambda p: state["static"])
    monkeypatch.setattr(replay, "build_wrapper", lambda src, dest, cc=None: dest)
    return state


def test_run_extraction_success_builds_wrapper_and_records_compilers(
    tmp_path: Path, pipeline: dict
) -> None:
    settings = _settings(
        tmp_path, require_root=pipeline["base"], wrapper_source=tmp_path / "w.c"
    )
    target = tmp_path / "results" / "compilers.json"
    outcome = extraction.run_extraction(settings, target)
    assert outcome.replayed_commands == 1 and outcome.execroot == pipeline["execroot"]
    assert pipeline["wrapper"] == settings.bin_dir / replay.WRAPPER_NAME
    (info,) = outcome.gate.compilers
    assert info.static is True and info.via_wrapper is True and info.version == "12.2.0"
    assert json.loads(target.read_text())["compilers"][0]["via_wrapper"] is True
    filtered = json.loads(settings.filtered.read_text())
    assert filtered[0]["directory"] == str(pipeline["execroot"])


def test_run_extraction_requires_positive_targets(
    tmp_path: Path, pipeline: dict
) -> None:
    with pytest.raises(ScaError, match="non-negative"):
        extraction.run_extraction(
            _settings(tmp_path, targets=["-//a/..."]), tmp_path / "c.json"
        )


def test_run_extraction_gate_failure_for_wrong_compiler_is_recorded(
    tmp_path: Path, pipeline: dict
) -> None:
    clang = pipeline["base"] / "external" / "tc" / "bin" / "clang++"
    clang.write_text("#!/bin/sh\n")
    clang.chmod(0o755)
    pipeline["compiler"] = clang
    target = tmp_path / "compilers.json"
    with pytest.raises(ScaError, match="found 'clang\\+\\+'"):
        extraction.run_extraction(
            _settings(tmp_path, require_root=pipeline["base"]), target
        )
    data = json.loads(target.read_text())
    assert data["status"] == "failed" and "clang++" in data["errors"][0]


def test_run_extraction_fails_when_static_compiler_was_not_rewritten(
    tmp_path: Path, pipeline: dict, monkeypatch: pytest.MonkeyPatch
) -> None:
    def fake_replay(settings, execroot_, wrapper):
        settings.replay_report.write_text(json.dumps({"static_compilers": {}}))

    monkeypatch.setattr(extraction, "replay_under_tracer", fake_replay)
    target = tmp_path / "compilers.json"
    with pytest.raises(ScaError, match="not rewritten"):
        extraction.run_extraction(
            _settings(
                tmp_path, require_root=pipeline["base"], wrapper_source=tmp_path / "w.c"
            ),
            target,
        )
    assert json.loads(target.read_text())["status"] == "failed"


def test_run_extraction_missing_wrapper_source_is_actionable(
    tmp_path: Path, pipeline: dict
) -> None:
    with pytest.raises(ScaError, match="SCORE_CODEQL_WRAPPER_SRC"):
        extraction.run_extraction(
            _settings(tmp_path, require_root=pipeline["base"]),
            tmp_path / "compilers.json",
        )


def test_dynamic_compiler_needs_no_wrapper(tmp_path: Path, pipeline: dict) -> None:
    pipeline["static"] = False
    outcome = extraction.run_extraction(
        _settings(tmp_path, require_root=pipeline["base"]), tmp_path / "compilers.json"
    )
    assert pipeline["wrapper"] is None
    assert outcome.gate.compilers[0].via_wrapper is False


def test_stale_outputs_are_removed_before_a_run(tmp_path: Path, pipeline: dict) -> None:
    settings = _settings(
        tmp_path, require_root=pipeline["base"], wrapper_source=tmp_path / "w.c"
    )
    settings.work_dir.mkdir(parents=True)
    settings.filtered.write_text("[1]")
    settings.compile_commands_execroot.write_text("[1]")
    extraction.run_extraction(settings, tmp_path / "compilers.json")
    assert len(json.loads(settings.filtered.read_text())) == 1
