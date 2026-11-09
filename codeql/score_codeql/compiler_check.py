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

"""Compiler identity gate: prove the database was built with the production compiler.

Three checks, each failing with an actionable message (there is no fallback to
another compiler):

1. ``check_pre_replay`` - before the expensive extraction: every distinct compiler
   of the filtered compile commands must match ``--expect-compiler``, live under the
   required root (the Bazel output base, i.e. the hermetic toolchain), must not
   define ``__clang__`` and must be GCC-compatible (``__GNUC__``).
2. ``apply_replay_report`` - static compilers must have been rewritten to the
   ``codeql_cc_wrapper``; the tracer cannot see them otherwise.
3. ``check_post_extraction`` - the finished database must contain as many
   compilations as commands were replayed (``Compilation.getArgument(1) =
   "--mimic"``, argument 2 is what the tracer observed). For a dynamic compiler
   that is the compiler (or its ``cc1plus``) and it is checked against
   ``--expect-compiler`` and ``--require-compiler-root``. For a static compiler
   started through ``codeql_cc_wrapper`` the tracer only observes the wrapper, so
   the database cannot name the real compiler. A mimic of the wrapper we built is
   therefore an *alias* of the wrapped compilers: it is accepted only because
   step 1 already verified those compilers in this run and step 2 recorded how
   many commands were rewritten for each; the number of wrapper compilations must
   match that total (within the tolerance).
"""

from __future__ import annotations

import json
import logging
import os
import re
import subprocess
from collections import Counter
from collections.abc import Callable, Iterable, Mapping, Sequence
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from score_codeql import check_db, replay
from score_codeql.errors import ConfigError, ScaError
from score_codeql.process import run_capture

DEFAULT_EXPECTED_COMPILERS: dict[str, list[str]] = {
    "linux": [r"^x86_64-unknown-linux-gnu-(gcc|g\+\+|c\+\+)$"],
    "qnx": [r"^(qcc|q\+\+)$"],
}
DEFAULT_EXPECT_GCC_MAJOR: dict[str, int | None] = {"linux": 12, "qnx": None}
# A dynamic GCC driver is observed through the compiler proper it spawns.
_EXTRACTOR_HOOK_RE = re.compile(r"^cc1(plus)?$")
_MIMIC_LOG_RE = re.compile(r"\bCommand:\s+\S+\s+--mimic\s+(\S+)")
_DEFINE_RE = re.compile(r"^#define\s+(\w+)\s+(.*)$", re.MULTILINE)
_LIKELY_CAUSE = (
    "tracer did not observe the static GCC driver - was the wrapper built/used?"
)


@dataclass
class Probe:
    """Result of asking a compiler for its identity."""

    version: str | None = None
    gcc_major: int | None = None
    clang: bool = False
    ok: bool = False


@dataclass
class CompilerInfo:
    """Everything known about one distinct compiler of the compile commands."""

    original: str
    basename: str
    commands: int
    path: str | None = None
    static: bool | None = None
    version: str | None = None
    gcc_major: int | None = None
    clang: bool = False
    via_wrapper: bool | None = None
    rewritten: int = 0
    extracted: int | None = None

    def to_json(self) -> dict[str, Any]:
        return {
            "basename": self.basename,
            "path": self.path,
            "version": self.version,
            "gcc_major": self.gcc_major,
            "clang": self.clang,
            "linkage": {True: "static", False: "dynamic", None: "unknown"}[self.static],
            "via_wrapper": self.via_wrapper,
            "commands": self.commands,
            "extracted": self.extracted,
        }


@dataclass
class GateResult:
    """Collected data written to ``compilers.json``."""

    platform: str
    patterns: list[str]
    require_root: str | None
    compilers: list[CompilerInfo] = field(default_factory=list)
    mimicked: list[tuple[str, int]] = field(default_factory=list)
    replayed_commands: int = 0
    wrapper: str | None = None
    status: str = "pending"
    errors: list[str] = field(default_factory=list)

    def to_json(self) -> dict[str, Any]:
        return {
            "status": self.status,
            "platform": self.platform,
            "expected_compilers": self.patterns,
            "require_compiler_root": self.require_root,
            "replayed_commands": self.replayed_commands,
            "wrapper": self.wrapper,
            "compilers": [c.to_json() for c in self.compilers],
            "extracted_compilers": [
                {"compiler": name, "count": count} for name, count in self.mimicked
            ],
            "errors": self.errors,
        }


def compile_patterns(patterns: Iterable[str]) -> list[re.Pattern[str]]:
    try:
        return [re.compile(p) for p in patterns]
    except re.error as exc:
        raise ConfigError(f"invalid --expect-compiler regex: {exc}") from exc


def resolve_expected(platform: str, given: Sequence[str]) -> list[str]:
    """Effective regexes: ``[]`` (disabled) if any given value is empty."""
    if any(not g for g in given):
        return []
    return list(given) or list(DEFAULT_EXPECTED_COMPILERS.get(platform, []))


def is_under(path: str, root: Path, execroot: Path | None = None) -> bool:
    """True if *path* (or its symlink-resolved form) lies below *root*."""
    candidate = Path(path)
    if not candidate.is_absolute() and execroot is not None:
        candidate = execroot / candidate
    paths = {Path(os.path.normpath(candidate)), Path(os.path.realpath(candidate))}
    roots = {Path(os.path.normpath(root)), Path(os.path.realpath(root))}
    return any(p == r or r in p.parents for p in paths for r in roots)


def probe_compiler(
    path: Path, cwd: Path, env: Mapping[str, str] | None = None
) -> Probe:
    """``-dumpfullversion`` and ``-dM -E`` identity of *path*."""
    probe = Probe()
    try:
        version = subprocess.run(
            [str(path), "-dumpfullversion"],
            cwd=str(cwd),
            env=dict(env) if env else None,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            check=False,
            timeout=60,
        )
        macros = subprocess.run(
            [str(path), "-dM", "-E", "-x", "c++", "/dev/null"],
            cwd=str(cwd),
            env=dict(env) if env else None,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            check=False,
            timeout=60,
        )
    except (OSError, subprocess.TimeoutExpired):
        return probe
    if macros.returncode != 0:
        return probe
    defines = dict(_DEFINE_RE.findall(macros.stdout))
    probe.clang = "__clang__" in defines
    gnuc = defines.get("__GNUC__", "").strip()
    probe.gcc_major = int(gnuc) if gnuc.isdigit() else None
    if version.returncode == 0 and version.stdout.strip():
        probe.version = version.stdout.strip().splitlines()[0]
    probe.ok = True
    return probe


def original_compiler(entry: Mapping[str, Any]) -> str | None:
    """argv[0] of *entry*; for a wrapper-rewritten entry the compiler it forwards to."""
    compiler = replay.get_compiler_arg0(entry)
    if compiler and Path(compiler).name == replay.WRAPPER_NAME:
        target = (entry.get("_codeql_env") or {}).get(replay.WRAPPER_TARGET_ENV)
        return target or compiler
    return compiler


def check_pre_replay(
    entries: Sequence[Mapping[str, Any]],
    *,
    platform: str,
    patterns: Sequence[str],
    require_root: Path | None,
    execroot: Path | None,
    expect_gcc_major: int | None = None,
    bazel_configs: str = "",
    probe: Callable[[Path, Path], Probe] | None = None,
    is_static: Callable[[Path], bool] | None = None,
    qnx_host: str | None = None,
    enforce: bool = True,
) -> list[CompilerInfo]:
    """Validate the compilers of *entries* before replay; return their descriptions.

    With ``enforce=False`` (checks disabled by an empty ``--expect-compiler``) the
    compilers are only described and problems are logged as warnings.
    """
    compiled = compile_patterns(patterns)
    probe_fn = probe or probe_compiler
    static_fn = is_static or replay.is_elf_static
    total = len(entries)
    by_original: dict[str, list[Mapping[str, Any]]] = {}
    for entry in entries:
        compiler = original_compiler(entry)
        if compiler:
            by_original.setdefault(compiler, []).append(entry)

    infos: list[CompilerInfo] = []
    errors: list[str] = []
    mismatches: list[str] = []
    for original, group in sorted(by_original.items()):
        info = CompilerInfo(original, Path(original).name, len(group))
        infos.append(info)
        if compiled and not any(p.search(info.basename) for p in compiled):
            mismatches.append(
                f"found {info.basename!r} in {info.commands}/{total} compile commands"
            )
            continue
        directory = str(group[0].get("directory", "."))
        resolved = replay.resolve_compiler_executable(
            original, directory, execroot, qnx_host
        )
        if resolved is None:
            errors.append(
                f"compiler {original!r} ({info.commands} command(s)) does not exist; "
                "was the Bazel build materialised?"
            )
            continue
        info.path = str(resolved)
        if require_root is not None and not is_under(info.path, require_root, execroot):
            errors.append(
                f"compiler {info.path} is outside the required root {require_root} "
                "(a silent fall-back to a host compiler?); use --require-compiler-root "
                "to change the root"
            )
        info.static = static_fn(resolved)
        result = probe_fn(resolved, Path(directory))
        if not result.ok:
            message = f"could not query the identity of compiler {info.path}"
            if platform == "qnx" or not enforce:
                logging.warning("%s; skipping its identity probe", message)
            else:
                errors.append(message)
            continue
        info.version, info.gcc_major, info.clang = (
            result.version,
            result.gcc_major,
            result.clang,
        )
        if not enforce:
            continue
        if result.clang:
            errors.append(f"compiler {info.path} is Clang (__clang__ is defined)")
        elif result.gcc_major is None:
            errors.append(
                f"compiler {info.path} does not define __GNUC__ (not GCC-compatible)"
            )
        elif expect_gcc_major is not None and result.gcc_major != expect_gcc_major:
            errors.append(
                f"compiler {info.path} is GCC {result.gcc_major}, expected GCC "
                f"{expect_gcc_major} (--expect-gcc-major)"
            )

    if mismatches:
        configs = f" (bazel {bazel_configs})" if bazel_configs else ""
        errors.insert(
            0,
            f"expected GCC ({' | '.join(patterns)}), {'; '.join(mismatches)}{configs}.\n"
            "The CodeQL analysis requires the production GCC toolchain; no fallback is used.",
        )
    if errors:
        raise ScaError("compiler check failed: " + "\n".join(errors))
    return infos


def apply_replay_report(
    infos: Sequence[CompilerInfo], report: Mapping[str, Any]
) -> list[str]:
    """Record wrapper usage from the replay report; return error messages."""
    static = report.get("static_compilers", {})
    errors: list[str] = []
    for info in infos:
        entry = static.get(info.original)
        info.via_wrapper = entry is not None
        info.rewritten = int(entry.get("rewritten", 0)) if entry else 0
        if info.static and (entry is None or entry.get("rewritten") != info.commands):
            errors.append(
                f"static compiler {info.path} was not rewritten to the codeql_cc_wrapper "
                f"for all {info.commands} command(s); the tracer cannot observe it"
            )
    return errors


def parse_decoded_rows(decoded: Mapping[str, Any]) -> list[tuple[str, int]]:
    """Rows ``(compiler, count)`` from ``codeql bqrs decode --format=json``."""
    table = decoded.get("#select")
    if not isinstance(table, dict):
        raise ScaError("unexpected result format of the compiler query")
    rows: list[tuple[str, int]] = []
    for tuple_ in table.get("tuples", []):
        rows.append((str(tuple_[0]), int(tuple_[1])))
    return rows


def run_compiler_query(
    codeql: Path,
    database: Path,
    diagnostics_dir: Path,
    work_dir: Path,
    threads: int = 1,
) -> list[tuple[str, int]]:
    """Evaluate ``ExtractedCompilers.ql`` against the finalized database."""
    bqrs = work_dir / "extracted-compilers.bqrs"
    ql = diagnostics_dir / "ExtractedCompilers.ql"
    run_capture(
        [
            str(codeql),
            "query",
            "run",
            f"--database={database}",
            f"--output={bqrs}",
            f"--additional-packs={diagnostics_dir}",
            "-j",
            str(threads),
            str(ql),
        ]
    )
    decoded = run_capture([str(codeql), "bqrs", "decode", "--format=json", str(bqrs)])
    try:
        return parse_decoded_rows(json.loads(decoded.stdout))
    except json.JSONDecodeError as exc:
        raise ScaError(f"cannot parse compiler query result: {exc}") from exc


def mimic_rows_from_logs(database: Path) -> list[tuple[str, int]]:
    """Fallback: ``--mimic <compiler>`` of every extractor invocation in the logs."""
    counter: Counter[str] = Counter()
    for log in sorted(check_db.extractor_log_dir(database).rglob("*.log")):
        try:
            text = log.read_text(encoding="utf-8", errors="replace")
        except OSError:
            continue
        match = _MIMIC_LOG_RE.search(text)
        if match:
            counter[match.group(1)] += 1
    return sorted(counter.items())


def _same_file(path: str, other: Path) -> bool:
    return os.path.realpath(path) == os.path.realpath(other)


def check_post_extraction(
    infos: Sequence[CompilerInfo],
    rows: Sequence[tuple[str, int]],
    replayed_commands: int,
    *,
    patterns: Sequence[str],
    require_root: Path | None,
    execroot: Path | None,
    tolerance_pct: float = 0.0,
    wrapper: Path | None = None,
) -> None:
    """Verify the mimicked compilers in the database; raises ``ScaError``.

    *wrapper* is the ``codeql_cc_wrapper`` built for this run; mimics of exactly
    that file stand for the wrapped static compilers (see the module docstring).
    """
    compiled = compile_patterns(patterns)
    errors: list[str] = []
    total = sum(count for _name, count in rows)
    if total == 0:
        raise ScaError(
            "compiler check failed: the database contains no compilations; "
            + _LIKELY_CAUSE
        )
    wrapped = [i for i in infos if i.via_wrapper and i.rewritten > 0]
    wrapped_total = sum(i.rewritten for i in wrapped)
    wrapper_count = 0
    for name, count in rows:
        if wrapper is not None and _same_file(name, wrapper):
            if not wrapped:
                errors.append(
                    f"the database mimics {name!r} ({count} compilation(s)) but no "
                    "wrapped compiler was verified and recorded in this run"
                )
            else:
                wrapper_count += count
            continue
        base = Path(name).name
        allowed = (
            not compiled
            or any(p.search(base) for p in compiled)
            or bool(_EXTRACTOR_HOOK_RE.match(base))
        )
        if not allowed:
            errors.append(
                f"extracted compiler {name!r} ({count} compilation(s)) does not match "
                f"--expect-compiler ({' | '.join(patterns)})"
            )
        elif require_root is not None and not is_under(name, require_root, execroot):
            errors.append(
                f"extracted compiler {name!r} ({count} compilation(s)) is outside the "
                f"required root {require_root}"
            )

    if wrapper_count:
        minimum_wrapped = wrapped_total * (1 - tolerance_pct / 100)
        if wrapper_count < minimum_wrapped or wrapper_count > wrapped_total:
            errors.append(
                f"{wrapper_count} compilation(s) mimic the codeql_cc_wrapper but "
                f"{wrapped_total} command(s) were rewritten to use it "
                f"(tolerance {tolerance_pct:.1f}%); {_LIKELY_CAUSE}"
            )
        if len(wrapped) == 1:
            wrapped[0].extracted = wrapper_count
        elif wrapper_count == wrapped_total:
            for info in wrapped:
                info.extracted = info.rewritten

    dynamic = [i for i in infos if i.static is not True]
    for info in infos:
        if info in wrapped:
            continue
        info.extracted = sum(
            count
            for name, count in rows
            if info.path and Path(name).name == Path(info.path).name
        )
    hooked = sum(c for n, c in rows if _EXTRACTOR_HOOK_RE.match(Path(n).name))
    if hooked and len(dynamic) == 1:
        dynamic[0].extracted = (dynamic[0].extracted or 0) + hooked

    minimum = replayed_commands * (1 - tolerance_pct / 100)
    if total < minimum:
        errors.append(
            f"only {total} of {replayed_commands} replayed commands were extracted "
            f"(tolerance {tolerance_pct:.1f}%); {_LIKELY_CAUSE}"
        )
    if errors:
        raise ScaError("compiler check failed: " + "\n".join(errors))


def write_compilers_json(path: Path, result: GateResult) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(result.to_json(), indent=2) + "\n", encoding="utf-8")


def run_post_gate(
    gate: GateResult,
    *,
    codeql: Path,
    database: Path,
    diagnostics_dir: Path,
    work_dir: Path,
    execroot: Path | None,
    tolerance_pct: float,
    threads: int,
    compilers_json: Path,
) -> None:
    """Post-extraction check; always rewrites ``compilers.json`` with the verdict."""
    try:
        try:
            rows = run_compiler_query(
                codeql, database, diagnostics_dir, work_dir, threads
            )
        except ScaError as exc:
            logging.warning(
                "Compiler query failed (%s); falling back to the extractor logs", exc
            )
            rows = mimic_rows_from_logs(database)
        gate.mimicked = rows
        require_root = Path(gate.require_root) if gate.require_root else None
        check_post_extraction(
            gate.compilers,
            rows,
            gate.replayed_commands,
            patterns=gate.patterns,
            require_root=require_root,
            execroot=execroot,
            tolerance_pct=tolerance_pct,
            wrapper=Path(gate.wrapper) if gate.wrapper else None,
        )
    except ScaError as exc:
        gate.status = "failed"
        gate.errors.append(str(exc))
        write_compilers_json(compilers_json, gate)
        raise
    gate.status = "ok"
    write_compilers_json(compilers_json, gate)
