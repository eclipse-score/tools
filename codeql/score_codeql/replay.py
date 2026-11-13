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

"""Replay the commands of a ``compile_commands.json`` in parallel.

Run under ``codeql database trace-command`` so the CodeQL tracer observes every
compiler invocation::

    codeql database trace-command -- <db> python -m score_codeql.replay \\
        --no-output -j 8 compile_commands.filtered.json

Statically linked compilers (e.g. the hermetic GCC toolchain) cannot be hooked by
the ``LD_PRELOAD`` based tracer. Such entries are rewritten to start a small
dynamic forwarder, ``codeql_cc_wrapper``, which ``exec``s the real compiler and is
hooked instead. The wrapper must be built *before* the tracer starts
(``build_wrapper``) so that building it is not itself extracted.

Commands are executed without a shell.
"""

from __future__ import annotations

import argparse
import concurrent.futures
import json
import logging
import os
import re
import shlex
import shutil
import subprocess
import sys
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from score_codeql.errors import ScaError
from score_codeql.logging_utils import setup_logging

WRAPPER_NAME = "codeql_cc_wrapper"
WRAPPER_TARGET_ENV = "CODEQL_CC_WRAPPER_TARGET"

# Flags whose next argument is an output path that --no-output redirects to /dev/null.
OUTPUT_FILE_FLAGS = {"-o", "-MJ", "-dependency-file", "--serialize-diagnostics"}
# Flags that are dropped together with their argument.
STRIP_WITH_ARG_FLAGS = {"-MF"}
# Flags that implicitly create extra output files and are dropped.
IMPLICIT_OUTPUT_FLAGS = {
    "-MD",
    "-MMD",
    "-gsplit-dwarf",
    "-fstack-usage",
    "-ftime-trace",
    "-save-temps",
}
# QNX qcc passes options through as one token, e.g. "-Wc,-MD,bazel-out/.../foo.d";
# keeping it would make the compiler write into Bazel's read-only output tree.
QNX_COMPOUND_DEP_FLAG_RE = re.compile(r"^-W[cp],-M(?:D|MD|F)(?:,.*)?$")
_QNX_NTO_PREFIXED_RE = re.compile(r"^nto(?:aarch64|x86_64)-(?:g\+\+|gcc|c\+\+|cpp|cc)$")
_QNX_NTO_TRIPLET_RE = re.compile(
    r"^[a-z0-9_]+-[a-z0-9_]+-nto-qnx[0-9.]*-(?:g\+\+|gcc|c\+\+|cpp|cc)$"
)
_QNX_ENV_KEYS = (
    "QNX_HOST",
    "QNX_TARGET",
    "QNX_CONFIGURATION_EXCLUSIVE",
    "QNX_SHARED_LICENSE_FILE",
)
_DEFAULT_QNX_LICENSE_FILE = "/opt/score_qnx/license/licenses"


@dataclass(frozen=True)
class ExecutionResult:
    """Outcome of one replayed command."""

    index: int
    file_path: str
    exit_code: int
    output: str | None
    command: str | None = None


# ---------------------------------------------------------------------------
# Entry helpers
# ---------------------------------------------------------------------------


def entry_argv(entry: Mapping[str, Any]) -> list[str]:
    """argv of an entry (``arguments`` or POSIX-split ``command``)."""
    arguments = entry.get("arguments")
    if isinstance(arguments, list):
        return [str(a) for a in arguments]
    command = entry.get("command")
    if isinstance(command, str):
        try:
            return shlex.split(command, posix=True)
        except ValueError as exc:
            raise ValueError(f"cannot parse command {command!r}: {exc}") from exc
    raise ValueError("entry has neither 'command' nor 'arguments'")


def get_compiler_arg0(entry: Mapping[str, Any]) -> str | None:
    """argv[0] of an entry, or ``None`` when it cannot be determined."""
    try:
        argv = entry_argv(entry)
    except ValueError:
        return None
    return argv[0] if argv else None


def _with_arg0(entry: Mapping[str, Any], new_arg0: str) -> dict[str, Any]:
    """Shallow copy of *entry* with argv[0] replaced (keeps the original format)."""
    new_entry = dict(entry)
    if isinstance(entry.get("arguments"), list):
        arguments = list(entry["arguments"])
        arguments[0] = new_arg0
        new_entry["arguments"] = arguments
    else:
        parts = shlex.split(entry["command"], posix=True)
        parts[0] = new_arg0
        new_entry["command"] = shlex.join(parts)
    return new_entry


def validate_entries(data: Any) -> None:
    """Raise ``ValueError`` unless *data* is a list of usable entries."""
    if not isinstance(data, list):
        raise ValueError("compile_commands.json must be a JSON array")
    for i, entry in enumerate(data):
        if not isinstance(entry, dict):
            raise ValueError(f"Entry {i} is not a JSON object")
        if "command" not in entry and "arguments" not in entry:
            raise ValueError(f"Entry {i} has neither 'command' nor 'arguments'")
        if "file" not in entry:
            raise ValueError(f"Entry {i} is missing required 'file' field")


def load_compilation_database(path: Path) -> list[dict[str, Any]]:
    with path.open("r", encoding="utf-8") as fh:
        data = json.load(fh)
    validate_entries(data)
    return data


# ---------------------------------------------------------------------------
# Compiler resolution
# ---------------------------------------------------------------------------


def resolve_compiler_executable(
    compiler: str,
    directory: str,
    execroot: Path | None = None,
    qnx_host: str | None = None,
) -> Path | None:
    """Resolve *compiler* via its directory, the execroot, ``QNX_HOST`` or ``PATH``."""
    compiler_path = Path(compiler)
    candidates: list[Path] = []
    if compiler_path.is_absolute():
        candidates.append(compiler_path)
    else:
        candidates.append(Path(directory) / compiler_path)
        if execroot is not None:
            candidates.append(execroot / compiler_path)
    if qnx_host and compiler_path.name in {"qcc", "q++"}:
        candidates.append(Path(qnx_host) / "usr" / "bin" / compiler_path.name)
    for candidate in candidates:
        if candidate.is_file() and os.access(candidate, os.X_OK):
            return candidate
    found = shutil.which(compiler_path.name)
    return Path(found) if found else None


def is_elf_static(path: Path) -> bool:
    """True if *path* is a statically linked ELF executable (judged by ``ldd``)."""
    try:
        with path.open("rb") as fh:
            if fh.read(4) != b"\x7fELF":
                return False
        result = subprocess.run(
            ["ldd", str(path)],
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            check=False,
        )
    except OSError:
        return False
    return (
        "not a dynamic executable" in result.stdout
        or "statically linked" in result.stdout
    )


def find_static_compilers(
    entries: Sequence[Mapping[str, Any]],
    execroot: Path | None = None,
    qnx_host: str | None = None,
) -> dict[str, Path]:
    """Map every distinct argv[0] that resolves to a static binary to its path."""
    checked: dict[tuple[str, str], bool] = {}
    static: dict[str, Path] = {}
    for entry in entries:
        compiler = get_compiler_arg0(entry)
        if not compiler:
            continue
        directory = str(entry.get("directory", "."))
        key = (compiler, directory if not os.path.isabs(compiler) else "")
        if key in checked:
            continue
        path = resolve_compiler_executable(compiler, directory, execroot, qnx_host)
        checked[key] = bool(path and is_elf_static(path))
        if checked[key] and path is not None:
            static[compiler] = path
            logging.debug("Compiler is statically linked: %s", compiler)
    return static


def absolute_compiler(
    compiler: str, directory: str, execroot: Path | None, qnx_host: str | None
) -> str:
    """Absolute path of *compiler* when resolvable, else *compiler* unchanged."""
    if os.path.isabs(compiler):
        return compiler
    resolved = resolve_compiler_executable(compiler, directory, execroot, qnx_host)
    return str(resolved) if resolved else compiler


def apply_static_compiler_workaround(
    entries: Sequence[Mapping[str, Any]],
    static_compilers: Mapping[str, Path],
    wrapper: Path,
    execroot: Path | None = None,
    qnx_host: str | None = None,
) -> tuple[list[dict[str, Any]], dict[str, int]]:
    """Rewrite entries using a static compiler to start *wrapper* instead.

    Every rewritten entry carries ``CODEQL_CC_WRAPPER_TARGET`` (the original
    compiler) in its private ``_codeql_env`` key, so mixed-compiler databases work.
    Returns the new entries and the number of rewritten entries per original
    argv[0].
    """
    rewritten: list[dict[str, Any]] = []
    counts: dict[str, int] = {}
    for entry in entries:
        compiler = get_compiler_arg0(entry)
        if compiler is None or compiler not in static_compilers:
            rewritten.append(dict(entry))
            continue
        directory = str(entry.get("directory", "."))
        target = absolute_compiler(compiler, directory, execroot, qnx_host)
        new_entry = _with_arg0(entry, str(wrapper))
        env = dict(new_entry.get("_codeql_env", {}))
        env[WRAPPER_TARGET_ENV] = target
        new_entry["_codeql_env"] = env
        counts[compiler] = counts.get(compiler, 0) + 1
        rewritten.append(new_entry)
    return rewritten, counts


def build_wrapper(source: Path, destination: Path, cc: str | None = None) -> Path:
    """Compile ``codeql_cc_wrapper.c`` into *destination* with the host C compiler."""
    if not source.is_file():
        raise ScaError(f"wrapper source not found: {source}")
    compiler = cc or shutil.which("cc") or shutil.which("gcc")
    if compiler is None:
        raise ScaError(
            "a statically linked compiler was detected, which requires building "
            f"{WRAPPER_NAME} from {source}, but no host C compiler (cc/gcc) is "
            "available on PATH; install one (e.g. build-essential)"
        )
    destination.parent.mkdir(parents=True, exist_ok=True)
    result = subprocess.run(
        [compiler, str(source), "-O2", "-o", str(destination)],
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        check=False,
    )
    if result.returncode != 0:
        raise ScaError(f"failed to build {WRAPPER_NAME}:\n{result.stderr.strip()}")
    destination.chmod(0o755)
    return destination


# ---------------------------------------------------------------------------
# QNX environment
# ---------------------------------------------------------------------------


def is_qnx_compiler_name(name: str) -> bool:
    base = Path(name).name
    return (
        base in {"qcc", "q++"}
        or _QNX_NTO_PREFIXED_RE.match(base) is not None
        or _QNX_NTO_TRIPLET_RE.match(base) is not None
    )


def derive_qnx_base_from_compiler_path(compiler_path: Path) -> Path | None:
    """QNX SDP base of ``<base>/host/<os>/<arch>/usr/bin/{qcc,q++}``."""
    text = str(compiler_path)
    if "/host/" not in text:
        return None
    base, tail = text.split("/host/", 1)
    if not (tail.endswith("/usr/bin/q++") or tail.endswith("/usr/bin/qcc")) or not base:
        return None
    return Path(base)


def derive_qnx_env_from_entries(
    entries: Sequence[Mapping[str, Any]], execroot: Path | None = None
) -> dict[str, str]:
    """Detect a QNX build and derive the toolchain environment Bazel would set."""
    qnx_base: Path | None = None
    for entry in entries:
        compiler = get_compiler_arg0(entry)
        if not compiler or not is_qnx_compiler_name(compiler):
            continue
        path = resolve_compiler_executable(
            compiler, str(entry.get("directory", ".")), execroot
        )
        base = derive_qnx_base_from_compiler_path(path) if path else None
        if base is None:
            continue
        if qnx_base is None:
            qnx_base = base
        elif qnx_base != base:
            logging.warning(
                "Detected multiple QNX SDK bases in compile_commands; using first one: %s",
                qnx_base,
            )
            break
    if qnx_base is None:
        return {}
    env = {
        "QNX_HOST": f"{qnx_base}/host/linux/x86_64",
        "QNX_TARGET": f"{qnx_base}/target/qnx",
        # Bazel's toolchain sets this for every QNX action; replay runs outside Bazel.
        "QNX_CONFIGURATION_EXCLUSIVE": "/var/tmp/.qnx",
    }
    license_file = os.environ.get("QNX_SHARED_LICENSE_FILE")
    if not license_file and Path(_DEFAULT_QNX_LICENSE_FILE).is_file():
        license_file = _DEFAULT_QNX_LICENSE_FILE
    if license_file:
        env["QNX_SHARED_LICENSE_FILE"] = license_file
    else:
        logging.warning(
            "Could not determine QNX_SHARED_LICENSE_FILE (no env var set and %s not "
            "found); QNX compiler invocations may fail with a license error.",
            _DEFAULT_QNX_LICENSE_FILE,
        )
    return env


# ---------------------------------------------------------------------------
# Execution
# ---------------------------------------------------------------------------


def redirect_outputs_to_devnull(args: Sequence[str]) -> list[str]:
    """Redirect output-file flags to /dev/null and strip implicit-output flags."""
    result: list[str] = []
    i = 0
    items = list(args)
    while i < len(items):
        arg = items[i]
        if arg in OUTPUT_FILE_FLAGS and i + 1 < len(items):
            result += [arg, "/dev/null"]
            i += 2
        elif arg in STRIP_WITH_ARG_FLAGS and i + 1 < len(items):
            i += 2
        elif arg in IMPLICIT_OUTPUT_FLAGS or QNX_COMPOUND_DEP_FLAG_RE.match(arg):
            i += 1
        else:
            result.append(arg)
            i += 1
    return result


def normalize_entry(
    entry: Mapping[str, Any], execroot: Path | None, qnx_host: str | None
) -> dict[str, Any]:
    """Absolutise argv[0] and fall back to the execroot as working directory."""
    modified = dict(entry)
    compiler = get_compiler_arg0(modified)
    if not compiler or os.path.isabs(compiler):
        return modified
    directory = str(modified.get("directory", "."))
    if (
        not (Path(directory) / compiler).exists()
        and execroot is not None
        and (execroot / compiler).exists()
    ):
        modified["directory"] = str(execroot)
        directory = str(execroot)
    resolved = absolute_compiler(compiler, directory, execroot, qnx_host)
    if resolved != compiler:
        modified = _with_arg0(modified, resolved)
    return modified


def execute_command(
    entry: Mapping[str, Any],
    index: int,
    *,
    no_output: bool,
    dry_run: bool,
    execroot: Path | None,
    env_overrides: Mapping[str, str],
) -> ExecutionResult:
    """Run one entry and capture its interleaved stdout/stderr."""
    file_path = str(entry.get("file", "unknown"))
    qnx_host = env_overrides.get("QNX_HOST") or os.environ.get("QNX_HOST")
    entry = normalize_entry(entry, execroot, qnx_host)
    directory = str(entry.get("directory", "."))
    if not os.path.isdir(directory):
        return ExecutionResult(
            index, file_path, 1, f"Directory does not exist: {directory}"
        )
    try:
        argv = entry_argv(entry)
    except ValueError as exc:
        return ExecutionResult(index, file_path, 1, str(exc))
    if not argv:
        return ExecutionResult(index, file_path, 1, "empty command")
    if no_output:
        argv = redirect_outputs_to_devnull(argv)
    command = shlex.join(argv)
    if dry_run:
        return ExecutionResult(index, file_path, 0, f"[DRY-RUN] {command}", command)

    proc_env = os.environ.copy()
    proc_env.update(env_overrides)
    if proc_env.get("QNX_HOST"):
        qnx_bin = f"{proc_env['QNX_HOST']}/usr/bin"
        proc_env["PATH"] = (
            f"{qnx_bin}:{proc_env['PATH']}" if proc_env.get("PATH") else qnx_bin
        )
    proc_env.update(entry.get("_codeql_env") or {})
    try:
        result = subprocess.run(
            argv,
            cwd=directory,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            env=proc_env,
            check=False,
        )
    except OSError as exc:
        return ExecutionResult(index, file_path, 1, f"Execution failed: {exc}", command)
    return ExecutionResult(
        index, file_path, result.returncode, result.stdout or None, command
    )


def execute_all(
    entries: Sequence[Mapping[str, Any]],
    max_workers: int,
    *,
    no_output: bool = False,
    dry_run: bool = False,
    continue_on_error: bool = False,
    execroot: Path | None = None,
    env_overrides: Mapping[str, str] | None = None,
) -> tuple[list[ExecutionResult], bool]:
    """Run all entries in parallel; returns ``(results sorted by index, stopped_early)``."""
    workers = 1 if dry_run else max(1, min(max_workers, len(entries)))
    overrides = env_overrides or {}
    results: list[ExecutionResult] = []
    stopped = False
    with concurrent.futures.ThreadPoolExecutor(max_workers=workers) as executor:
        futures = [
            executor.submit(
                execute_command,
                entry,
                i,
                no_output=no_output,
                dry_run=dry_run,
                execroot=execroot,
                env_overrides=overrides,
            )
            for i, entry in enumerate(entries, 1)
        ]
        for future in concurrent.futures.as_completed(futures):
            result = future.result()
            results.append(result)
            if result.exit_code != 0 and not continue_on_error:
                for pending in futures:
                    pending.cancel()
                stopped = True
                break
    results.sort(key=lambda r: r.index)
    return results, stopped


def display_results(
    results: Sequence[ExecutionResult], total: int, verbose: bool
) -> int:
    """Print failures (all results when *verbose*); return the failure count."""
    failed = 0
    for result in results:
        if result.exit_code != 0:
            failed += 1
            print(
                f"[{result.index}/{total}] {result.file_path} returned exit code {result.exit_code}:"
            )
            if result.command:
                print(f"Command: {result.command}")
            if result.output:
                print(result.output, end="" if result.output.endswith("\n") else "\n")
        elif verbose:
            print(f"[{result.index}/{total}] {result.file_path}")
            if result.output:
                print(result.output, end="" if result.output.endswith("\n") else "\n")
    return failed


def write_report(
    path: Path,
    total: int,
    failed: int,
    static_counts: Mapping[str, int],
    wrapper: Path | None,
) -> None:
    """Machine-readable replay summary consumed by the compiler gate."""
    payload = {
        "commands": total,
        "failed": failed,
        "wrapper": str(wrapper) if wrapper else None,
        "static_compilers": {
            compiler: {"rewritten": count}
            for compiler, count in sorted(static_counts.items())
        },
    }
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2), encoding="utf-8")


def run_replay(args: argparse.Namespace) -> int:
    try:
        entries = load_compilation_database(args.file)
    except FileNotFoundError:
        logging.error("File not found: %s", args.file)
        return 1
    except (OSError, UnicodeDecodeError, json.JSONDecodeError, ValueError) as exc:
        logging.error("Cannot load %s: %s", args.file, exc)
        return 1
    if not entries:
        logging.error("No entries found in %s", args.file)
        return 1

    total = len(entries)
    execroot = args.execroot
    env_overrides = derive_qnx_env_from_entries(entries, execroot)
    for key in _QNX_ENV_KEYS:
        if os.environ.get(key):
            env_overrides[key] = os.environ[key]
    if env_overrides:
        logging.info(
            "Detected QNX build; derived QNX_* environment from compile_commands"
        )
    qnx_host = env_overrides.get("QNX_HOST")

    static = find_static_compilers(entries, execroot, qnx_host)
    static_counts: dict[str, int] = {}
    wrapper: Path | None = None
    if static:
        if args.wrapper is None or not args.wrapper.is_file():
            logging.error(
                "Statically linked compiler(s) detected (%s) but %s is not available "
                "(--wrapper %s); the CodeQL tracer cannot observe static compilers.",
                ", ".join(sorted(static)),
                WRAPPER_NAME,
                args.wrapper,
            )
            return 1
        wrapper = args.wrapper
        entries, static_counts = apply_static_compiler_workaround(
            entries, static, wrapper, execroot, qnx_host
        )
        for compiler, path in sorted(static.items()):
            logging.info(
                "static compiler %s -> %s (%d command(s))",
                path,
                WRAPPER_NAME,
                static_counts.get(compiler, 0),
            )

    if not args.quiet:
        logging.info("Executing %d compilation commands...", total)
    workers = args.jobs or os.cpu_count() or 1
    results, stopped = execute_all(
        entries,
        workers,
        no_output=args.no_output,
        dry_run=args.dry_run,
        continue_on_error=args.continue_on_error,
        execroot=execroot,
        env_overrides=env_overrides,
    )
    failed = display_results(results, total, args.verbose or args.dry_run)
    if args.report_file:
        write_report(args.report_file, total, failed, static_counts, wrapper)
    if stopped:
        logging.error("Replay stopped on first error. Use --continue to keep going.")
        return 1
    failed_pct = failed / total * 100
    if failed:
        logging.error("%d of %d commands failed (%.1f%%)", failed, total, failed_pct)
        return 0 if failed_pct <= args.max_failed_pct else 1
    if not args.quiet:
        logging.info("All %d commands succeeded", total)
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Run compilation commands from compile_commands.json in parallel."
    )
    parser.add_argument("file", type=Path, help="Path to compile_commands.json")
    parser.add_argument(
        "-j", "--jobs", type=int, default=0, help="Parallel jobs (0 = all cores)"
    )
    parser.add_argument(
        "-c",
        "--continue",
        "--continue-on-error",
        dest="continue_on_error",
        action="store_true",
        help="Continue after a failed command",
    )
    parser.add_argument(
        "--max-failed-pct",
        type=float,
        default=0.0,
        help="With --continue: exit 0 while at most this percentage of commands failed",
    )
    parser.add_argument("--dry-run", "--dry", dest="dry_run", action="store_true")
    parser.add_argument("-v", "--verbose", action="store_true")
    parser.add_argument("-q", "--quiet", action="store_true")
    parser.add_argument(
        "--no-output",
        action="store_true",
        help="Redirect compiler output files (-o, -MJ, ...) to /dev/null",
    )
    parser.add_argument(
        "--execroot",
        type=Path,
        default=None,
        help="Bazel execution root used to resolve relative compiler paths",
    )
    parser.add_argument(
        "--wrapper",
        type=Path,
        default=None,
        help=f"Prebuilt {WRAPPER_NAME} binary (required if a static compiler is used)",
    )
    parser.add_argument(
        "--report-file",
        type=Path,
        default=None,
        help="Write a JSON replay summary here",
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    setup_logging(args.verbose)
    return run_replay(args)


if __name__ == "__main__":
    sys.exit(main())
