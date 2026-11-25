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
from score_codeql import replay
from score_codeql.errors import ScaError

ELF = b"\x7fELF" + bytes(16)


@pytest.fixture
def fake_ldd(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """``ldd`` on PATH that reports files containing STATIC as static executables."""
    bindir = tmp_path / "fakebin"
    bindir.mkdir()
    ldd = bindir / "ldd"
    ldd.write_text(
        "#!/bin/sh\n"
        'if grep -q STATIC "$1"; then echo "\\tnot a dynamic executable"; exit 1; fi\n'
        'echo "\\tlibc.so.6 => /lib/libc.so.6"\n'
    )
    ldd.chmod(0o755)
    monkeypatch.setenv("PATH", f"{bindir}:/usr/bin:/bin")


def _compiler(path: Path, static: bool = False, script: str | None = None) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    if script is not None:
        path.write_text(script)
    else:
        path.write_bytes(ELF + (b"STATIC" if static else b"DYNAMIC"))
    path.chmod(path.stat().st_mode | stat.S_IXUSR)
    return path


def _entry(
    compiler: str | Path, directory: Path, file: str = "a.cpp", *extra: str
) -> dict:
    return {
        "directory": str(directory),
        "arguments": [str(compiler), "-c", file, "-o", "a.o", *extra],
        "file": file,
    }


def test_redirect_outputs_to_devnull() -> None:
    args = [
        "g++", "-MD", "-MF", "a.d", "-c", "a.cpp", "-o", "a.o", "-MJ", "a.json",
        "-fstack-usage", "-save-temps", "-Wc,-MD,bazel-out/x/a.d", "-Wp,-MF,y.d", "-O2",
    ]  # fmt: skip
    assert replay.redirect_outputs_to_devnull(args) == [
        "g++", "-c", "a.cpp", "-o", "/dev/null", "-MJ", "/dev/null", "-O2",
    ]  # fmt: skip


def test_redirect_keeps_trailing_flag_without_argument() -> None:
    assert replay.redirect_outputs_to_devnull(["g++", "-o"]) == ["g++", "-o"]


def test_is_elf_static_uses_ldd_and_ignores_scripts(
    tmp_path: Path, fake_ldd: None
) -> None:
    assert replay.is_elf_static(_compiler(tmp_path / "s", static=True))
    assert not replay.is_elf_static(_compiler(tmp_path / "d", static=False))
    assert not replay.is_elf_static(
        _compiler(tmp_path / "sh", script="#!/bin/sh\nSTATIC\n")
    )
    assert not replay.is_elf_static(tmp_path / "missing")


def test_find_static_compilers_resolves_relative_paths(
    tmp_path: Path, fake_ldd: None
) -> None:
    execroot = tmp_path / "exec"
    _compiler(execroot / "external/gcc/bin/x86_64-unknown-linux-gnu-g++", static=True)
    _compiler(execroot / "external/other/bin/dyn-g++", static=False)
    entries = [
        _entry("external/gcc/bin/x86_64-unknown-linux-gnu-g++", execroot),
        _entry("external/gcc/bin/x86_64-unknown-linux-gnu-g++", execroot, "b.cpp"),
        _entry("external/other/bin/dyn-g++", execroot),
    ]
    found = replay.find_static_compilers(entries, execroot)
    assert list(found) == ["external/gcc/bin/x86_64-unknown-linux-gnu-g++"]
    assert found["external/gcc/bin/x86_64-unknown-linux-gnu-g++"] == (
        execroot / "external/gcc/bin/x86_64-unknown-linux-gnu-g++"
    )


def test_static_compiler_rewrite_arguments_and_command_entries(
    tmp_path: Path, fake_ldd: None
) -> None:
    execroot = tmp_path / "exec"
    gpp = _compiler(execroot / "bin/gpp", static=True)
    dyn = _compiler(execroot / "bin/dyn", static=False)
    wrapper = _compiler(tmp_path / "codeql_cc_wrapper")
    command_entry = {
        "directory": str(execroot),
        "command": "bin/gpp -c 'my file.cpp' -o a.o",
        "file": "my file.cpp",
    }
    entries = [
        _entry("bin/gpp", execroot),
        _entry("bin/dyn", execroot),
        command_entry,
    ]
    static = replay.find_static_compilers(entries, execroot)
    rewritten, counts = replay.apply_static_compiler_workaround(
        entries, static, wrapper, execroot
    )
    assert counts == {"bin/gpp": 2}
    assert rewritten[0]["arguments"][0] == str(wrapper)
    assert rewritten[0]["_codeql_env"] == {replay.WRAPPER_TARGET_ENV: str(gpp)}
    assert rewritten[1]["arguments"][0] == "bin/dyn"
    assert "_codeql_env" not in rewritten[1]
    assert replay.entry_argv(rewritten[2])[0] == str(wrapper)
    assert replay.entry_argv(rewritten[2])[2] == "my file.cpp"
    assert rewritten[2]["_codeql_env"][replay.WRAPPER_TARGET_ENV] == str(gpp)
    assert entries[0]["arguments"][0] == "bin/gpp"  # input is not mutated
    assert dyn.exists()


def test_replay_runs_static_compiler_through_wrapper(
    tmp_path: Path, fake_ldd: None, capsys: pytest.CaptureFixture[str]
) -> None:
    execroot = tmp_path / "exec"
    execroot.mkdir()
    out = tmp_path / "invocations.txt"
    # The "static compiler" is a marker file; only the wrapper is ever executed.
    static_cc = _compiler(execroot / "bin/static-g++", static=True)
    wrapper = _compiler(
        tmp_path / "codeql_cc_wrapper",
        script=f'#!/bin/sh\necho "$CODEQL_CC_WRAPPER_TARGET $@" >> {out}\n',
    )
    dynamic_cc = _compiler(
        execroot / "bin/dyn-g++", script=f'#!/bin/sh\necho "dyn $@" >> {out}\n'
    )
    db = tmp_path / "cc.json"
    db.write_text(
        json.dumps(
            [
                _entry(static_cc, execroot),
                _entry(dynamic_cc, execroot, "b.cpp", "-MD", "-MF", "x.d"),
            ]
        )
    )
    report = tmp_path / "report.json"
    rc = replay.main(
        [
            str(db), "--no-output", "-j", "1", "--wrapper", str(wrapper),
            "--execroot", str(execroot), "--report-file", str(report),
        ]
    )  # fmt: skip
    assert rc == 0
    lines = sorted(out.read_text().splitlines())
    assert lines == [f"{static_cc} -c a.cpp -o /dev/null", "dyn -c b.cpp -o /dev/null"]
    data = json.loads(report.read_text())
    assert data["commands"] == 2 and data["failed"] == 0
    assert data["static_compilers"] == {str(static_cc): {"rewritten": 1}}


def test_replay_fails_clearly_without_wrapper_for_static_compiler(
    tmp_path: Path, fake_ldd: None, caplog: pytest.LogCaptureFixture
) -> None:
    static_cc = _compiler(tmp_path / "cc", static=True)
    db = tmp_path / "cc.json"
    db.write_text(json.dumps([_entry(static_cc, tmp_path)]))
    assert replay.main([str(db)]) == 1
    assert replay.main([str(db), "--wrapper", str(tmp_path / "nope")]) == 1


def test_commands_are_executed_without_a_shell(tmp_path: Path) -> None:
    entry = {
        "directory": str(tmp_path),
        "command": "/bin/echo 'a;b' '$(id)' > out.txt",
        "file": "x.cpp",
    }
    result = replay.execute_command(
        entry, 1, no_output=False, dry_run=False, execroot=None, env_overrides={}
    )
    assert result.exit_code == 0
    assert result.output == "a;b $(id) > out.txt\n"
    assert not (tmp_path / "out.txt").exists()


def test_dry_run_prints_without_executing(tmp_path: Path) -> None:
    entry = _entry("/bin/false", tmp_path)
    result = replay.execute_command(
        entry, 1, no_output=True, dry_run=True, execroot=None, env_overrides={}
    )
    assert result.exit_code == 0 and result.output.startswith("[DRY-RUN] /bin/false")


def test_missing_directory_and_executable_fail(tmp_path: Path) -> None:
    bad_dir = replay.execute_command(
        _entry("/bin/true", tmp_path / "gone"), 1, no_output=False, dry_run=False,
        execroot=None, env_overrides={},
    )  # fmt: skip
    assert bad_dir.exit_code == 1 and "Directory does not exist" in bad_dir.output
    missing = replay.execute_command(
        _entry("/no/such/compiler", tmp_path), 1, no_output=False, dry_run=False,
        execroot=None, env_overrides={},
    )  # fmt: skip
    assert missing.exit_code == 1 and "Execution failed" in missing.output


def test_stop_on_first_error_vs_continue(tmp_path: Path) -> None:
    entries = [_entry("/bin/false", tmp_path), _entry("/bin/true", tmp_path)]
    results, stopped = replay.execute_all(entries, 1)
    assert stopped and results[0].exit_code == 1
    results, stopped = replay.execute_all(entries, 1, continue_on_error=True)
    assert not stopped and [r.exit_code for r in results] == [1, 0]


def test_max_failed_pct_tolerates_some_failures(tmp_path: Path) -> None:
    db = tmp_path / "cc.json"
    db.write_text(
        json.dumps(
            [_entry("/bin/false", tmp_path)] + [_entry("/bin/true", tmp_path)] * 9
        )
    )
    assert replay.main([str(db), "--continue", "--max-failed-pct=5"]) == 1
    assert replay.main([str(db), "--continue", "--max-failed-pct=10"]) == 0
    assert replay.main([str(db)]) == 1


def test_compiler_relative_to_execroot_changes_working_directory(
    tmp_path: Path,
) -> None:
    execroot = tmp_path / "exec"
    cc = _compiler(execroot / "bin/cc", script="#!/bin/sh\npwd\n")
    other = tmp_path / "other"
    other.mkdir()
    result = replay.execute_command(
        {"directory": str(other), "arguments": ["bin/cc"], "file": "a.c"},
        1, no_output=False, dry_run=False, execroot=execroot, env_overrides={},
    )  # fmt: skip
    assert result.exit_code == 0
    assert result.output.strip() == str(execroot)
    assert cc.exists()


def test_build_wrapper_compiles_with_given_cc(tmp_path: Path) -> None:
    source = tmp_path / "w.c"
    source.write_text("int main(void){return 0;}\n")
    fake_cc = tmp_path / "fakecc"
    fake_cc.write_text(
        '#!/bin/sh\nwhile [ $# -gt 0 ]; do [ "$1" = -o ] && out=$2; shift; done\necho built > "$out"\n'
    )
    fake_cc.chmod(0o755)
    dest = tmp_path / "bin" / "codeql_cc_wrapper"
    assert replay.build_wrapper(source, dest, cc=str(fake_cc)) == dest
    assert dest.read_text() == "built\n"
    assert dest.stat().st_mode & stat.S_IXUSR


def test_build_wrapper_errors(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    with pytest.raises(ScaError, match="wrapper source not found"):
        replay.build_wrapper(tmp_path / "nope.c", tmp_path / "w")
    source = tmp_path / "w.c"
    source.write_text("")
    monkeypatch.setattr(replay.shutil, "which", lambda _name: None)
    with pytest.raises(ScaError, match="no host C compiler"):
        replay.build_wrapper(source, tmp_path / "w")
    failing = tmp_path / "failcc"
    failing.write_text("#!/bin/sh\necho oops >&2\nexit 1\n")
    failing.chmod(0o755)
    with pytest.raises(ScaError, match="oops"):
        replay.build_wrapper(source, tmp_path / "w", cc=str(failing))


def test_qnx_environment_is_derived_from_compiler_path(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.delenv("QNX_SHARED_LICENSE_FILE", raising=False)
    qcc = _compiler(tmp_path / "sdp/host/linux/x86_64/usr/bin/qcc")
    env = replay.derive_qnx_env_from_entries([_entry(qcc, tmp_path)])
    assert env["QNX_HOST"] == f"{tmp_path}/sdp/host/linux/x86_64"
    assert env["QNX_TARGET"] == f"{tmp_path}/sdp/target/qnx"
    assert env["QNX_CONFIGURATION_EXCLUSIVE"] == "/var/tmp/.qnx"
    monkeypatch.setenv("QNX_SHARED_LICENSE_FILE", "/lic")
    assert (
        replay.derive_qnx_env_from_entries([_entry(qcc, tmp_path)])[
            "QNX_SHARED_LICENSE_FILE"
        ]
        == "/lic"
    )
    assert replay.derive_qnx_env_from_entries([_entry("/usr/bin/g++", tmp_path)]) == {}


@pytest.mark.parametrize(
    "name, expected",
    [
        ("qcc", True),
        ("q++", True),
        ("ntoaarch64-g++", True),
        ("aarch64-unknown-nto-qnx8.0.0-g++", True),
        ("x86_64-unknown-linux-gnu-g++", False),
        ("clang++", False),
    ],
)
def test_is_qnx_compiler_name(name: str, expected: bool) -> None:
    assert replay.is_qnx_compiler_name(f"/some/dir/{name}") is expected


def test_validate_entries() -> None:
    replay.validate_entries([{"arguments": ["g++"], "file": "a.cpp"}])
    for bad in ({}, [1], [{"file": "a"}], [{"arguments": []}]):
        with pytest.raises(ValueError):
            replay.validate_entries(bad)


def test_python_module_entry_point_runs() -> None:
    import subprocess

    result = subprocess.run(
        [sys.executable, "-m", "score_codeql.replay", "--help"],
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode == 0 and "--wrapper" in result.stdout
