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
import textwrap
from pathlib import Path

import pytest
from score_codeql import compiler_check as cc
from score_codeql import replay
from score_codeql.errors import ConfigError, ScaError

LINUX = cc.DEFAULT_EXPECTED_COMPILERS["linux"]
GPP = "x86_64-unknown-linux-gnu-g++"


@pytest.fixture
def toolchain(tmp_path: Path) -> dict[str, Path]:
    base = tmp_path / "output_base"
    execroot = base / "execroot" / "_main"
    paths = {"base": base, "execroot": execroot}
    for name in (GPP, "clang++", "g++"):
        binary = base / "external" / "toolchain" / "bin" / name
        binary.parent.mkdir(parents=True, exist_ok=True)
        binary.write_text("#!/bin/sh\n")
        binary.chmod(binary.stat().st_mode | stat.S_IXUSR)
        paths[name] = binary
    execroot.mkdir(parents=True)
    return paths


def _entries(compiler: Path | str, execroot: Path, count: int = 3) -> list[dict]:
    return [
        {
            "directory": str(execroot),
            "arguments": [str(compiler), "-c", f"f{i}.cpp", "-o", f"f{i}.o"],
            "file": f"f{i}.cpp",
        }
        for i in range(count)
    ]


def _probe(version: str = "12.2.0", major: int | None = 12, clang: bool = False):
    return lambda _path, _cwd: cc.Probe(version, major, clang, ok=True)


def _pre(entries, toolchain, **overrides):
    options = dict(
        platform="linux",
        patterns=LINUX,
        require_root=toolchain["base"],
        execroot=toolchain["execroot"],
        expect_gcc_major=12,
        probe=_probe(),
        is_static=lambda _p: True,
    )
    options.update(overrides)
    return cc.check_pre_replay(entries, **options)


def test_pre_check_fails_on_clang_with_default_regex(
    toolchain: dict[str, Path],
) -> None:
    entries = _entries(toolchain["clang++"], toolchain["execroot"], 57)
    with pytest.raises(ScaError) as info:
        _pre(entries, toolchain, bazel_configs="--config=x86_64-linux")
    message = str(info.value)
    assert "compiler check failed" in message
    assert "found 'clang++' in 57/57 compile commands" in message
    assert "(bazel --config=x86_64-linux)" in message
    assert "no fallback is used" in message
    assert LINUX[0] in message


def test_pre_check_names_only_the_offending_compiler(
    toolchain: dict[str, Path],
) -> None:
    entries = _entries(toolchain[GPP], toolchain["execroot"], 4) + _entries(
        toolchain["clang++"], toolchain["execroot"], 1
    )
    with pytest.raises(
        ScaError, match="found 'clang\\+\\+' in 1/5 compile commands"
    ) as info:
        _pre(entries, toolchain)
    assert f"found {GPP!r}" not in str(info.value)


def test_pre_check_passes_for_production_gcc_and_describes_it(
    toolchain: dict[str, Path],
) -> None:
    entries = _entries(toolchain[GPP], toolchain["execroot"], 57)
    infos = _pre(entries, toolchain)
    assert len(infos) == 1
    info = infos[0]
    assert (info.basename, info.commands, info.static) == (GPP, 57, True)
    assert (info.version, info.gcc_major, info.clang) == ("12.2.0", 12, False)
    assert info.path == str(toolchain[GPP])


def test_pre_check_uses_the_original_compiler_of_wrapper_entries(
    toolchain: dict[str, Path], tmp_path: Path
) -> None:
    entries = _entries(tmp_path / "bin" / replay.WRAPPER_NAME, toolchain["execroot"], 2)
    for entry in entries:
        entry["_codeql_env"] = {replay.WRAPPER_TARGET_ENV: str(toolchain[GPP])}
    infos = _pre(entries, toolchain)
    assert [i.basename for i in infos] == [GPP]
    plain = _entries(tmp_path / "bin" / replay.WRAPPER_NAME, toolchain["execroot"], 1)
    with pytest.raises(ScaError, match=replay.WRAPPER_NAME):
        _pre(plain, toolchain)


def test_pre_check_rejects_host_compiler_outside_root(
    toolchain: dict[str, Path], tmp_path: Path
) -> None:
    host = tmp_path / "usr" / "bin" / GPP
    host.parent.mkdir(parents=True)
    host.write_text("#!/bin/sh\n")
    host.chmod(0o755)
    with pytest.raises(ScaError, match="outside the required root"):
        _pre(_entries(host, toolchain["execroot"]), toolchain)
    assert _pre(_entries(host, toolchain["execroot"]), toolchain, require_root=None)


def test_pre_check_rejects_clang_macros_and_wrong_gcc(
    toolchain: dict[str, Path],
) -> None:
    entries = _entries(toolchain[GPP], toolchain["execroot"])
    with pytest.raises(ScaError, match="is Clang"):
        _pre(entries, toolchain, probe=_probe(major=4, clang=True))
    with pytest.raises(ScaError, match="__GNUC__"):
        _pre(entries, toolchain, probe=_probe(major=None))
    with pytest.raises(ScaError, match="GCC 13, expected GCC 12"):
        _pre(entries, toolchain, probe=_probe(version="13.1.0", major=13))
    assert _pre(entries, toolchain, probe=_probe(major=13), expect_gcc_major=None)


def test_pre_check_probe_failure(toolchain: dict[str, Path]) -> None:
    entries = _entries(toolchain[GPP], toolchain["execroot"])
    failing = lambda _p, _c: cc.Probe()  # noqa: E731
    with pytest.raises(ScaError, match="could not query the identity"):
        _pre(entries, toolchain, probe=failing)
    assert _pre(entries, toolchain, probe=failing, platform="qnx")[0].version is None


def test_pre_check_missing_compiler_is_an_error(toolchain: dict[str, Path]) -> None:
    entries = _entries(toolchain["base"] / "gone" / GPP, toolchain["execroot"])
    with pytest.raises(ScaError, match="does not exist"):
        _pre(entries, toolchain)


def test_disabled_gate_only_describes(toolchain: dict[str, Path]) -> None:
    entries = _entries(toolchain["clang++"], toolchain["execroot"])
    infos = _pre(
        entries, toolchain, patterns=[], require_root=None, expect_gcc_major=None,
        probe=_probe(major=None, clang=True), enforce=False,
    )  # fmt: skip
    assert infos[0].clang is True


def test_resolve_expected_defaults_and_disable() -> None:
    assert cc.resolve_expected("linux", []) == LINUX
    assert cc.resolve_expected("qnx", []) == [r"^(qcc|q\+\+)$"]
    assert cc.resolve_expected("linux", ["^custom$"]) == ["^custom$"]
    assert cc.resolve_expected("linux", ["^custom$", ""]) == []
    with pytest.raises(ConfigError):
        cc.compile_patterns(["("])


def test_default_patterns_match_expected_names() -> None:
    import re

    linux = re.compile(LINUX[0])
    for name in ("x86_64-unknown-linux-gnu-gcc", GPP, "x86_64-unknown-linux-gnu-c++"):
        assert linux.search(name)
    for name in ("g++", "clang++", "gcc", "x86_64-unknown-linux-gnu-g++-12"):
        assert not linux.search(name)
    qnx = re.compile(cc.DEFAULT_EXPECTED_COMPILERS["qnx"][0])
    assert qnx.search("qcc") and qnx.search("q++") and not qnx.search("clang")


def test_is_under_handles_relative_paths_and_symlinks(tmp_path: Path) -> None:
    root = tmp_path / "base"
    (root / "external").mkdir(parents=True)
    link = tmp_path / "link"
    link.symlink_to(root / "external", target_is_directory=True)
    assert cc.is_under(str(root / "external" / "x"), root)
    assert cc.is_under(str(link / "x"), root)
    assert cc.is_under("external/x", root, execroot=root)
    assert not cc.is_under("/usr/bin/g++", root)
    assert not cc.is_under(str(tmp_path / "base2" / "x"), root)


def _info(
    toolchain, static: bool = True, original: str | None = None
) -> cc.CompilerInfo:
    info = cc.CompilerInfo(original or str(toolchain[GPP]), GPP, 3)
    info.path, info.static = str(toolchain[GPP]), static
    return info


def test_apply_replay_report_confirms_wrapper_usage(toolchain: dict[str, Path]) -> None:
    info = _info(toolchain)
    report = {"static_compilers": {info.original: {"rewritten": 3}}}
    assert cc.apply_replay_report([info], report) == []
    assert info.via_wrapper is True


def test_static_compiler_not_rewritten_is_an_error(toolchain: dict[str, Path]) -> None:
    info = _info(toolchain)
    errors = cc.apply_replay_report([info], {"static_compilers": {}})
    assert info.via_wrapper is False
    assert len(errors) == 1 and "not rewritten" in errors[0]
    partial = cc.apply_replay_report(
        [info], {"static_compilers": {info.original: {"rewritten": 1}}}
    )
    assert partial
    dynamic = _info(toolchain, static=False)
    assert cc.apply_replay_report([dynamic], {"static_compilers": {}}) == []
    assert dynamic.via_wrapper is False


def _post(toolchain, rows, replayed=3, **overrides):
    info = _info(toolchain)
    options = dict(
        patterns=LINUX, require_root=toolchain["base"], execroot=toolchain["execroot"]
    )
    options.update(overrides)
    cc.check_post_extraction([info], rows, replayed, **options)
    return info


def test_post_check_passes_when_every_command_was_extracted(
    toolchain: dict[str, Path],
) -> None:
    info = _post(toolchain, [(str(toolchain[GPP]), 3)])
    assert info.extracted == 3


def test_post_check_fails_when_fewer_compilations_than_commands(
    toolchain: dict[str, Path],
) -> None:
    with pytest.raises(ScaError) as error:
        _post(toolchain, [(str(toolchain[GPP]), 2)])
    assert "only 2 of 3 replayed commands were extracted" in str(error.value)
    assert "wrapper" in str(error.value)
    _post(toolchain, [(str(toolchain[GPP]), 2)], tolerance_pct=40.0)


def test_post_check_fails_on_unexpected_compiler_in_database(
    toolchain: dict[str, Path],
) -> None:
    rows = [(str(toolchain[GPP]), 3), (str(toolchain["clang++"]), 1)]
    with pytest.raises(
        ScaError, match="extracted compiler .*clang\\+\\+.* does not match"
    ):
        _post(toolchain, rows)


def test_post_check_fails_on_empty_database(toolchain: dict[str, Path]) -> None:
    with pytest.raises(ScaError, match="no compilations.*wrapper"):
        _post(toolchain, [])


def test_post_check_rejects_host_cc1plus_but_accepts_hermetic_cc1plus(
    toolchain: dict[str, Path],
) -> None:
    host = [("/usr/lib/gcc/x86_64-linux-gnu/11/cc1plus", 3)]
    with pytest.raises(ScaError, match="outside the required root"):
        _post(toolchain, host)
    hermetic = [(str(toolchain["base"] / "external/gcc/libexec/cc1plus"), 3)]
    dynamic = _info(toolchain, static=False)
    cc.check_post_extraction(
        [dynamic],
        hermetic,
        3,
        patterns=LINUX,
        require_root=toolchain["base"],
        execroot=None,
    )
    assert dynamic.extracted == 3
    _post(toolchain, host, require_root=None)


def test_post_check_without_patterns_only_checks_counts(
    toolchain: dict[str, Path],
) -> None:
    _post(toolchain, [("/usr/bin/anything", 3)], patterns=[], require_root=None)


def test_parse_decoded_rows() -> None:
    decoded = {
        "#select": {"columns": [{}, {}], "tuples": [["/x/g++", 2], ["/x/gcc", 5]]}
    }
    assert cc.parse_decoded_rows(decoded) == [("/x/g++", 2), ("/x/gcc", 5)]
    with pytest.raises(ScaError):
        cc.parse_decoded_rows({"other": {}})


def test_mimic_rows_from_extractor_logs(tmp_path: Path) -> None:
    logs = tmp_path / "db" / "log" / "extractor" / "00"
    logs.mkdir(parents=True)
    line = "[E 19:04:17 1] Command: /cl/extractor --mimic {} -quiet a.cpp -o /t/x.s\n"
    (logs / "1.log").write_text("[E 1 1] CodeQL\n" + line.format("/hermetic/g++"))
    (logs / "2.log").write_text(line.format("/hermetic/g++"))
    (logs / "3.log").write_text(line.format("/usr/lib/gcc/cc1plus"))
    (logs / "4.log").write_text("no command here\n")
    assert cc.mimic_rows_from_logs(tmp_path / "db") == [
        ("/hermetic/g++", 2),
        ("/usr/lib/gcc/cc1plus", 1),
    ]


def _gate(toolchain) -> cc.GateResult:
    gate = cc.GateResult("linux", LINUX, str(toolchain["base"]), replayed_commands=3)
    gate.compilers = [_info(toolchain)]
    return gate


def test_run_post_gate_uses_query_result_and_writes_json(
    toolchain: dict[str, Path],
    fake_codeql: Path,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    rows = {"#select": {"tuples": [[str(toolchain[GPP]), 3]]}}
    monkeypatch.setenv("FAKE_CODEQL_BQRS", json.dumps(rows))
    gate = _gate(toolchain)
    target = tmp_path / "results" / "compilers.json"
    work = tmp_path / "work"
    work.mkdir()
    diagnostics = tmp_path / "diag"
    diagnostics.mkdir()
    cc.run_post_gate(
        gate, codeql=fake_codeql, database=tmp_path / "db", diagnostics_dir=diagnostics,
        work_dir=work, execroot=toolchain["execroot"], tolerance_pct=0.0, threads=1,
        compilers_json=target,
    )  # fmt: skip
    data = json.loads(target.read_text())
    assert data["status"] == "ok"
    assert data["extracted_compilers"] == [
        {"compiler": str(toolchain[GPP]), "count": 3}
    ]
    assert data["compilers"][0]["extracted"] == 3
    calls = [
        json.loads(line) for line in (tmp_path / "calls.jsonl").read_text().splitlines()
    ]
    query_call = next(c for c in calls if c[:2] == ["query", "run"])
    assert f"--additional-packs={diagnostics}" in query_call
    assert query_call[-1] == str(diagnostics / "ExtractedCompilers.ql")


def test_run_post_gate_failure_is_recorded(
    toolchain: dict[str, Path],
    fake_codeql: Path,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    rows = {"#select": {"tuples": [[str(toolchain["clang++"]), 3]]}}
    monkeypatch.setenv("FAKE_CODEQL_BQRS", json.dumps(rows))
    target = tmp_path / "compilers.json"
    with pytest.raises(ScaError):
        cc.run_post_gate(
            _gate(toolchain), codeql=fake_codeql, database=tmp_path / "db",
            diagnostics_dir=tmp_path, work_dir=tmp_path, execroot=None,
            tolerance_pct=0.0, threads=1, compilers_json=target,
        )  # fmt: skip
    data = json.loads(target.read_text())
    assert data["status"] == "failed" and data["errors"]


def test_run_post_gate_falls_back_to_extractor_logs(
    toolchain: dict[str, Path],
    fake_codeql: Path,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("FAKE_CODEQL_FAIL", "query run")
    logs = tmp_path / "db" / "log" / "extractor"
    logs.mkdir(parents=True)
    for i in range(3):
        (logs / f"{i}.log").write_text(
            f"[E 1 1] Command: /cl/extractor --mimic {toolchain[GPP]} -quiet\n"
        )
    gate = _gate(toolchain)
    cc.run_post_gate(
        gate, codeql=fake_codeql, database=tmp_path / "db", diagnostics_dir=tmp_path,
        work_dir=tmp_path, execroot=None, tolerance_pct=0.0, threads=1,
        compilers_json=tmp_path / "compilers.json",
    )  # fmt: skip
    assert gate.status == "ok" and gate.mimicked == [(str(toolchain[GPP]), 3)]


def test_compilers_json_schema(toolchain: dict[str, Path]) -> None:
    gate = _gate(toolchain)
    gate.compilers[0].version = "12.2.0"
    gate.compilers[0].via_wrapper = True
    data = gate.to_json()
    assert data["compilers"][0]["linkage"] == "static"
    assert data["compilers"][0]["via_wrapper"] is True
    assert data["expected_compilers"] == LINUX
    assert textwrap.dedent(json.dumps(data))


def _wrapped_info(toolchain: dict[str, Path], rewritten: int = 3) -> cc.CompilerInfo:
    info = _info(toolchain)
    info.via_wrapper, info.rewritten = True, rewritten
    return info


def _wrapper(toolchain: dict[str, Path]) -> Path:
    path = toolchain["base"].parent / "out" / "work" / "bin" / replay.WRAPPER_NAME
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("")
    return path


def _post_wrapped(toolchain, rows, infos=None, replayed=3, **overrides):
    options = dict(
        patterns=LINUX,
        require_root=toolchain["base"],
        execroot=toolchain["execroot"],
        wrapper=_wrapper(toolchain),
    )
    options.update(overrides)
    infos = infos if infos is not None else [_wrapped_info(toolchain)]
    cc.check_post_extraction(infos, rows, replayed, **options)
    return infos


def test_wrapper_mimic_is_an_alias_of_the_verified_wrapped_compiler(
    toolchain: dict[str, Path],
) -> None:
    wrapper = _wrapper(toolchain)
    (info,) = _post_wrapped(toolchain, [(str(wrapper), 3)])
    assert info.extracted == 3
    data = info.to_json()
    assert data["via_wrapper"] is True and data["basename"] == GPP
    assert data["path"] == str(toolchain[GPP])


def test_wrapper_alias_is_matched_by_resolved_path(
    toolchain: dict[str, Path], tmp_path: Path
) -> None:
    wrapper = _wrapper(toolchain)
    link = tmp_path / "link"
    link.symlink_to(wrapper.parent, target_is_directory=True)
    _post_wrapped(toolchain, [(str(link / replay.WRAPPER_NAME), 3)])


def test_wrapper_mimic_without_recorded_wrapped_compilers_is_rejected(
    toolchain: dict[str, Path],
) -> None:
    wrapper = _wrapper(toolchain)
    with pytest.raises(ScaError, match="no wrapped compiler was verified"):
        _post_wrapped(toolchain, [(str(wrapper), 3)], infos=[])
    unwrapped = _info(toolchain, static=False)
    unwrapped.via_wrapper = False
    with pytest.raises(ScaError, match="no wrapped compiler was verified"):
        _post_wrapped(toolchain, [(str(wrapper), 3)], infos=[unwrapped])


def test_mimic_of_a_different_wrapper_is_rejected(
    toolchain: dict[str, Path], tmp_path: Path
) -> None:
    other = tmp_path / "elsewhere" / replay.WRAPPER_NAME
    other.parent.mkdir()
    other.write_text("")
    with pytest.raises(ScaError, match="does not match --expect-compiler"):
        _post_wrapped(toolchain, [(str(other), 3)])
    with pytest.raises(ScaError, match="does not match --expect-compiler"):
        _post_wrapped(toolchain, [(str(_wrapper(toolchain)), 3)], wrapper=None)


@pytest.mark.parametrize("count", [2, 4])
def test_wrapper_count_must_match_rewritten_commands(
    toolchain: dict[str, Path], count: int
) -> None:
    wrapper = _wrapper(toolchain)
    with pytest.raises(ScaError, match="mimic the codeql_cc_wrapper but 3 command"):
        _post_wrapped(toolchain, [(str(wrapper), count)], replayed=2)


def test_wrapper_count_tolerance_applies(toolchain: dict[str, Path]) -> None:
    wrapper = _wrapper(toolchain)
    _post_wrapped(toolchain, [(str(wrapper), 2)], tolerance_pct=40.0)


def test_unexpected_cc1plus_under_usr_is_still_rejected_next_to_wrapper_alias(
    toolchain: dict[str, Path],
) -> None:
    wrapper = _wrapper(toolchain)
    rows = [(str(wrapper), 3), ("/usr/lib/gcc/x86_64-linux-gnu/11/cc1plus", 1)]
    with pytest.raises(ScaError, match="outside the required root"):
        _post_wrapped(toolchain, rows, replayed=4)


def test_wrapper_and_regular_compilers_can_coexist(toolchain: dict[str, Path]) -> None:
    dynamic = cc.CompilerInfo(str(toolchain["g++"]), "g++", 2)
    dynamic.path, dynamic.static, dynamic.via_wrapper = (
        str(toolchain["g++"]),
        False,
        False,
    )
    rows = [(str(_wrapper(toolchain)), 3), (str(toolchain["g++"]), 2)]
    infos = _post_wrapped(
        toolchain,
        rows,
        infos=[_wrapped_info(toolchain), dynamic],
        replayed=5,
        patterns=[*LINUX, "^g\\+\\+$"],
    )
    assert [i.extracted for i in infos] == [3, 2]


def test_apply_replay_report_records_rewritten_counts(
    toolchain: dict[str, Path],
) -> None:
    info = _info(toolchain)
    cc.apply_replay_report(
        [info], {"static_compilers": {info.original: {"rewritten": 3}}}
    )
    assert info.rewritten == 3


def test_run_post_gate_accepts_wrapper_alias_and_lists_real_compiler(
    toolchain: dict[str, Path],
    fake_codeql: Path,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    wrapper = _wrapper(toolchain)
    rows = {"#select": {"tuples": [[str(wrapper), 3]]}}
    monkeypatch.setenv("FAKE_CODEQL_BQRS", json.dumps(rows))
    gate = _gate(toolchain)
    gate.compilers = [_wrapped_info(toolchain)]
    gate.wrapper = str(wrapper)
    gate.compilers[0].version = "12.2.0"
    target = tmp_path / "compilers.json"
    cc.run_post_gate(
        gate,
        codeql=fake_codeql,
        database=tmp_path / "db",
        diagnostics_dir=tmp_path,
        work_dir=tmp_path,
        execroot=toolchain["execroot"],
        tolerance_pct=0.0,
        threads=1,
        compilers_json=target,
    )
    data = json.loads(target.read_text())
    assert data["status"] == "ok" and data["wrapper"] == str(wrapper)
    assert data["compilers"][0]["basename"] == GPP
    assert data["compilers"][0]["extracted"] == 3
    assert data["compilers"][0]["via_wrapper"] is True
