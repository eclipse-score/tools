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

"""Extraction pipeline: database init, build, compile commands, filter, replay.

Compile commands are generated with ``bazel-compile-commands`` (through
``scripts/bcc.sh``) and replayed under the CodeQL tracer. ``bcc.sh`` is run with the
workspace as working directory, because ``bazel-compile-commands`` locates the
workspace through ``bazel info`` of its working directory; its output is written
to ``<output-dir>/work`` with ``-o`` so the developer's own
``compile_commands.json`` is never touched.
"""

from __future__ import annotations

import json
import logging
import os
import sys
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from pathlib import Path

from score_codeql import bazel as bz
from score_codeql import (
    compile_commands_filter,
    compiler_check,
    replay,
)
from score_codeql.errors import ScaError
from score_codeql.process import run_cmd


@dataclass
class ExtractionSettings:
    """Everything the extraction phase needs."""

    codeql: Path
    bazel: str
    workspace: Path
    work_dir: Path
    db_dir: Path
    targets: list[str]
    configs: list[str]
    bazel_options: list[str]
    platform: str
    replay_jobs: int
    exclude_globs: list[str]
    expect_patterns: list[str]
    require_root: Path | None
    expect_gcc_major: int | None
    max_failed_command_pct: float
    force_rebuild: bool = False
    verbose: bool = False
    bcc_sh: Path | None = None
    bcc_env: Mapping[str, str] = field(default_factory=dict)
    wrapper_source: Path | None = None

    @property
    def compile_commands(self) -> Path:
        return self.work_dir / "compile_commands.json"

    @property
    def compile_commands_execroot(self) -> Path:
        return self.work_dir / "compile_commands.execroot.json"

    @property
    def filtered(self) -> Path:
        return self.work_dir / "compile_commands.filtered.json"

    @property
    def replay_report(self) -> Path:
        return self.work_dir / "replay-report.json"

    @property
    def bin_dir(self) -> Path:
        return self.work_dir / "bin"


def init_database(settings: ExtractionSettings) -> None:
    settings.db_dir.parent.mkdir(parents=True, exist_ok=True)
    run_cmd(
        [
            str(settings.codeql),
            "database",
            "init",
            *([] if settings.verbose else ["-q"]),
            "--overwrite",
            "--language=c-cpp",
            f"--source-root={settings.workspace}",
            "--sublanguage-file-coverage",
            str(settings.db_dir),
        ],
        cwd=settings.workspace,
    )


def config_args(configs: Sequence[str]) -> list[str]:
    return [f"--config={c}" for c in configs]


def build_targets(settings: ExtractionSettings) -> None:
    """Real build so toolchain, generated files and the execroot ``external/`` exist.

    ``bazel aquery`` alone does not guarantee that every file referenced by the
    compilation database is materialised.
    """
    run_cmd(
        [
            settings.bazel,
            "build",
            *bz.build_flags(settings.force_rebuild),
            *config_args(settings.configs),
            *settings.bazel_options,
            *bz.bazel_target_args(settings.targets),
        ],
        cwd=settings.workspace,
    )


def bcc_command(settings: ExtractionSettings, positive: Sequence[str]) -> list[str]:
    """``bcc.sh --output-dir <work> -- <bazel-compile-commands args>``.

    ``bazel-compile-commands`` takes ``--config`` natively but needs every other
    Bazel flag through ``--bazelopt``.
    """
    if settings.bcc_sh is None:
        raise ScaError("scripts/bcc.sh was not resolved")
    return [
        str(settings.bcc_sh),
        "--output-dir",
        str(settings.work_dir),
        "--",
        *config_args(settings.configs),
        *(f"--bazelopt={opt}" for opt in settings.bazel_options),
        "-o",
        str(settings.compile_commands),
        "--",
        *positive,
    ]


def generate_compile_commands(
    settings: ExtractionSettings, positive: Sequence[str]
) -> None:
    env = os.environ.copy()
    env.update(settings.bcc_env)
    env["BCC_BAZEL"] = settings.bazel
    run_cmd(bcc_command(settings, positive), cwd=settings.workspace, env=env)
    if not settings.compile_commands.is_file():
        raise ScaError(
            f"bazel-compile-commands did not produce {settings.compile_commands}"
        )


def rewrite_compilation_database_directory(
    source: Path, destination: Path, directory: Path
) -> None:
    """Rewrite all entries to run from *directory* (Bazel's execution root)."""
    entries = compile_commands_filter.load_entries(source)
    rewritten = [{**entry, "directory": str(directory)} for entry in entries]
    try:
        with destination.open("w", encoding="utf-8") as fh:
            json.dump(rewritten, fh, indent=2)
    except OSError as exc:
        raise ScaError(f"cannot write {destination}: {exc}") from exc


def validate_compilation_database(path: Path) -> int:
    """Entry count of a non-empty database; raises ``ScaError`` otherwise."""
    entries = compile_commands_filter.load_entries(path)
    if not entries:
        raise ScaError(
            f"No compiler commands remain in {path} after filtering. "
            "Check the Bazel targets and exclusion patterns."
        )
    return len(entries)


def validate_extraction_output(db_dir: Path) -> None:
    """The tracer must have produced C/C++ extractor logs."""
    if any(
        p.is_dir()
        for p in (db_dir / "cpp" / "log" / "extractor", db_dir / "log" / "extractor")
    ):
        return
    raise ScaError(
        f"No CodeQL C/C++ extractor logs were produced under {db_dir}. The compilation "
        "commands were replayed, but CodeQL did not observe any compiler invocations."
    )


def compute_excludes(
    settings: ExtractionSettings, negative: Sequence[str]
) -> list[str]:
    """Exclusion globs from negative targets plus the configured extra globs."""
    globs: list[str] = []
    labels: list[str] = []
    for target in negative:
        glob = bz.negative_target_to_exclude_glob(target)
        if glob is not None:
            globs.append(glob)
        elif target.startswith("//"):
            labels.append(target)
        else:
            logging.warning("Ignoring unsupported negative target pattern: -%s", target)
    if labels:
        globs += bz.resolve_labels_to_source_globs(
            settings.workspace, labels, settings.bazel
        )
    return [*globs, *settings.exclude_globs]


def tracer_environment(platform: str) -> dict[str, str]:
    """Environment for ``trace-command``: the package must be importable there."""
    env = os.environ.copy()
    package_parent = str(Path(__file__).resolve().parent.parent)
    existing = env.get("PYTHONPATH")
    env["PYTHONPATH"] = (
        f"{package_parent}{os.pathsep}{existing}" if existing else package_parent
    )
    if platform == "qnx":
        for key in ("QNX_HOST", "QNX_TARGET"):
            if os.environ.get(key):
                logging.info("Passing %s to compilation replay", key)
    return env


def replay_under_tracer(
    settings: ExtractionSettings, execroot: Path, wrapper: Path | None
) -> None:
    command = [
        str(settings.codeql),
        "database",
        "trace-command",
        *(["-v"] if settings.verbose else []),
        "--",
        str(settings.db_dir),
        sys.executable,
        "-m",
        "score_codeql.replay",
        "--no-output",
        "--continue",
        f"--max-failed-pct={settings.max_failed_command_pct}",
        "-j",
        str(settings.replay_jobs),
        f"--execroot={execroot}",
        f"--report-file={settings.replay_report}",
        *([f"--wrapper={wrapper}"] if wrapper else []),
        str(settings.filtered),
    ]
    run_cmd(command, cwd=settings.workspace, env=tracer_environment(settings.platform))
    validate_extraction_output(settings.db_dir)


@dataclass
class ExtractionOutcome:
    """What the extraction phase hands to later phases."""

    execroot: Path
    replayed_commands: int
    gate: compiler_check.GateResult


def run_extraction(
    settings: ExtractionSettings, compilers_json: Path
) -> ExtractionOutcome:
    """Build, generate + filter compile commands, run the pre-replay gate and replay."""
    positive, negative = bz.split_bazel_targets(settings.targets)
    if not positive:
        raise ScaError(
            f"{settings.platform.upper()} extraction requires at least one "
            "non-negative Bazel target."
        )
    exclude_globs = compute_excludes(settings, negative)

    for stale in (
        settings.compile_commands,
        settings.compile_commands_execroot,
        settings.filtered,
        settings.replay_report,
    ):
        stale.unlink(missing_ok=True)
    settings.work_dir.mkdir(parents=True, exist_ok=True)

    logging.info(
        "> Build %s targets (materialize toolchain and generated files)",
        settings.platform.upper(),
    )
    build_targets(settings)
    logging.info("> Generate compile_commands for %s", settings.platform.upper())
    generate_compile_commands(settings, positive)

    execroot = bz.get_execution_root(settings.bazel, settings.workspace)
    rewrite_compilation_database_directory(
        settings.compile_commands, settings.compile_commands_execroot, execroot
    )
    logging.info("> Filter compile_commands for SCA replay")
    compile_commands_filter.filter_commands(
        settings.compile_commands_execroot, settings.filtered, exclude_globs
    )
    count = validate_compilation_database(settings.filtered)
    entries = compile_commands_filter.load_entries(settings.filtered)

    gate = compiler_check.GateResult(
        platform=settings.platform,
        patterns=list(settings.expect_patterns),
        require_root=str(settings.require_root) if settings.require_root else None,
        replayed_commands=count,
    )
    try:
        gate.compilers = compiler_check.check_pre_replay(
            entries,
            platform=settings.platform,
            patterns=settings.expect_patterns,
            require_root=settings.require_root,
            execroot=execroot,
            expect_gcc_major=settings.expect_gcc_major,
            bazel_configs=" ".join(config_args(settings.configs)),
            qnx_host=os.environ.get("QNX_HOST"),
            enforce=bool(settings.expect_patterns),
        )
        wrapper = prepare_wrapper(settings, gate.compilers)
        gate.wrapper = str(wrapper) if wrapper else None
    except ScaError as exc:
        gate.status = "failed"
        gate.errors.append(str(exc))
        compiler_check.write_compilers_json(compilers_json, gate)
        raise

    logging.info(
        "Replaying %d compiler command(s) for %s CodeQL extraction",
        count,
        settings.platform.upper(),
    )
    try:
        replay_under_tracer(settings, execroot, wrapper)
        report = json.loads(settings.replay_report.read_text(encoding="utf-8"))
        errors = compiler_check.apply_replay_report(gate.compilers, report)
        if errors:
            raise ScaError("compiler check failed: " + "\n".join(errors))
    except (OSError, json.JSONDecodeError) as exc:
        gate.status = "failed"
        gate.errors.append(f"replay report unavailable: {exc}")
        compiler_check.write_compilers_json(compilers_json, gate)
        raise ScaError(f"replay report unavailable: {exc}") from exc
    except ScaError as exc:
        gate.status = "failed"
        gate.errors.append(str(exc))
        compiler_check.write_compilers_json(compilers_json, gate)
        raise
    compiler_check.write_compilers_json(compilers_json, gate)
    return ExtractionOutcome(execroot, count, gate)


def prepare_wrapper(
    settings: ExtractionSettings, compilers: Sequence[compiler_check.CompilerInfo]
) -> Path | None:
    """Build ``codeql_cc_wrapper`` (outside the tracer) if any compiler is static."""
    static = [c for c in compilers if c.static]
    if not static:
        return None
    if settings.wrapper_source is None:
        raise ScaError(
            "a static compiler was detected, which requires building the "
            f"{replay.WRAPPER_NAME}; required environment variable "
            "SCORE_CODEQL_WRAPPER_SRC is not set"
        )
    logging.info(
        "Static compiler(s) detected (%s); building %s",
        ", ".join(c.basename for c in static),
        replay.WRAPPER_NAME,
    )
    return replay.build_wrapper(
        settings.wrapper_source, settings.bin_dir / replay.WRAPPER_NAME
    )
