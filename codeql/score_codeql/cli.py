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

"""``sca`` command line: run the CodeQL static analysis of a Bazel workspace.

Precedence of settings (lowest to highest): ``reports.toml`` presets, values baked
into the Bazel macro (placed *before* user arguments), arguments given on the
command line. Scalars are last-wins, repeatable options accumulate. Baked default
targets (hidden ``--default-target``) are used only when no positive target is
given on the command line.
"""

from __future__ import annotations

import argparse
import json
import logging
import os
import shutil
from collections.abc import Sequence
from pathlib import Path

from score_codeql import (
    analysis,
    check_db,
    check_sarif,
    compiler_check,
    extraction,
    summary,
)
from score_codeql import bazel as bz
from score_codeql import config as cfg
from score_codeql import packs as pk
from score_codeql import runfiles as rf
from score_codeql.errors import ScaError
from score_codeql.logging_utils import setup_logging

EXIT_FINDINGS = 3
DEFAULT_PLATFORM = "linux"
RESULTS_DIRNAME = "results"
WORK_DIRNAME = "work"
DEFAULT_OUTPUT_DIRNAME = "_codeql_sca"


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="sca",
        allow_abbrev=False,
        description="Run CodeQL static code analysis on Bazel targets.",
    )
    parser.add_argument(
        "targets",
        nargs="*",
        help="Bazel targets to analyze; negative patterns such as -//x/tests/... are "
        "supported. Replace the targets baked in by the Bazel macro.",
    )
    parser.add_argument(
        "--default-target",
        action="append",
        default=[],
        dest="default_targets",
        help=argparse.SUPPRESS,
    )
    parser.add_argument(
        "-c",
        "--config",
        action="append",
        default=[],
        dest="configs",
        help="Bazel --config to use (repeatable)",
    )
    parser.add_argument(
        "--bazel-option",
        action="append",
        default=[],
        dest="bazel_options",
        metavar="OPTION",
        help="Extra Bazel build option for nested Bazel calls (repeatable), e.g. "
        "--bazel-option=--copt=-Wno-error",
    )
    parser.add_argument(
        "--bazel",
        default=None,
        help="Bazel executable (default: $BAZEL, bazelisk, bazel)",
    )
    parser.add_argument(
        "--platform",
        choices=["linux", "qnx"],
        default=DEFAULT_PLATFORM,
        help="Target platform; both use compile-commands replay under the CodeQL tracer",
    )
    parser.add_argument(
        "--safety-level",
        default=None,
        help="Report preset from reports.toml [presets] (e.g. QM or ASIL)",
    )
    parser.add_argument(
        "-r",
        "--report",
        action="append",
        default=None,
        metavar="NAME",
        help="Report to run (repeatable; default: all reports)",
    )
    parser.add_argument(
        "--reports-file",
        action="append",
        default=[],
        metavar="PATH",
        help="Consumer reports.toml extending/overriding the defaults (repeatable)",
    )
    parser.add_argument(
        "--additional-pack-dir",
        action="append",
        default=[],
        metavar="PATH",
        help="Directory of a consumer-local CodeQL pack (repeatable)",
    )
    parser.add_argument(
        "--additional-pack-qlpack",
        action="append",
        default=[],
        metavar="LOCATION",
        help="qlpack.yml of a consumer-local pack as runfile location or path; "
        "its directory is used (repeatable; baked in by the Bazel macro)",
    )
    parser.add_argument(
        "--output-dir",
        default=None,
        help="Output directory (default: <workspace>/_codeql_sca)",
    )
    parser.add_argument(
        "--pack-cache-dir",
        default=None,
        help="Pack cache (default: ~/.cache/score-codeql/packs)",
    )
    parser.add_argument(
        "--threads",
        type=int,
        default=None,
        help="CodeQL threads (default: min(cpu, 16))",
    )
    parser.add_argument(
        "--ram",
        type=int,
        default=None,
        help="CodeQL RAM in MB (default: 75%% of available, max 12288)",
    )
    parser.add_argument(
        "--replay-jobs",
        type=int,
        default=None,
        help="Parallel replay jobs (default: min(cpu, 16))",
    )
    parser.add_argument("--max-failed-command-pct", type=float, default=5.0)
    parser.add_argument("--max-commands-with-issues-pct", type=float, default=5.0)
    parser.add_argument("--max-unique-parse-errors", type=int, default=0)
    parser.add_argument(
        "--exclude-glob",
        action="append",
        default=[],
        metavar="GLOB",
        help="Additional compile_commands exclusion glob (repeatable)",
    )
    parser.add_argument(
        "--auto-exclude-dir",
        action="append",
        default=[],
        metavar="DIR",
        help="Directory name whose Bazel packages are excluded (repeatable; default: "
        + ", ".join(bz.DEFAULT_AUTO_EXCLUDE_DIRS)
        + ")",
    )
    parser.add_argument(
        "--no-auto-exclude",
        action="store_true",
        help="Disable automatic test/mock exclusion",
    )
    parser.add_argument(
        "--sarif-category",
        default=None,
        help="Code Scanning category (default: static-analysis-codeql/<platform>)",
    )
    parser.add_argument(
        "--expect-compiler",
        action="append",
        default=[],
        metavar="REGEX",
        help="Regex for the basename of every replayed/extracted compiler (repeatable; "
        "empty string disables the compiler gate)",
    )
    parser.add_argument(
        "--require-compiler-root",
        default=None,
        metavar="PATH",
        help="Compilers must live below this directory (default: `bazel info "
        "output_base`; empty disables)",
    )
    parser.add_argument(
        "--expect-gcc-major",
        type=int,
        default=None,
        metavar="N",
        help="Required __GNUC__ of the compiler (default: 12 on linux, none on qnx; 0 disables)",
    )
    parser.add_argument(
        "--fail-on", choices=["none", "error", "warning"], default="none"
    )
    parser.add_argument(
        "--force-rebuild",
        action="store_true",
        help="Force a non-cached, non-uploaded materialising build",
    )
    parser.add_argument(
        "--list-reports",
        action="store_true",
        help="Print reports and presets, then exit",
    )
    parser.add_argument(
        "--print-pins",
        action="store_true",
        help="Print pinned versions as JSON, then exit",
    )
    parser.add_argument(
        "--codeql-cli",
        default=None,
        metavar="PATH",
        help="CodeQL launcher (overrides $SCORE_CODEQL_CLI)",
    )
    parser.add_argument("-v", "--verbose", action="store_true")
    return parser


def parse_args(argv: Sequence[str] | None = None) -> argparse.Namespace:
    """Parse *argv*; unknown ``-//...`` tokens are negative targets, others are errors."""
    parser = build_parser()
    args, unknown = parser.parse_known_args(argv)
    invalid = [u for u in unknown if not u.startswith("-//")]
    if invalid:
        parser.error(f"unrecognized arguments: {' '.join(invalid)}")
    args.targets = [*args.targets, *unknown]
    return args


def effective_targets(args: argparse.Namespace) -> list[str]:
    """User targets replace baked defaults; negatives alone extend them."""
    user = list(args.targets)
    positive, _negative = bz.split_bazel_targets(user)
    if positive:
        return user
    return [*args.default_targets, *user]


def format_reports(config: cfg.ReportsConfig) -> str:
    lines = ["Reports:"]
    for name, refs in config.reports.items():
        lines.append(f"  {name}")
        lines.extend(f"    {ref}" for ref in refs)
    lines.append("Presets (--safety-level):")
    for name, members in config.presets.items():
        lines.append(f"  {name}: {', '.join(members)}")
    return "\n".join(lines)


def _clean_outputs(results_dir: Path, work_dir: Path) -> None:
    """Remove stale results and DB; keep ``work/bin`` (wrapper cache)."""
    shutil.rmtree(results_dir, ignore_errors=True)
    shutil.rmtree(work_dir / "db", ignore_errors=True)
    for name in ("compilers.json", "replay-report.json"):
        (work_dir / name).unlink(missing_ok=True)
    results_dir.mkdir(parents=True, exist_ok=True)
    work_dir.mkdir(parents=True, exist_ok=True)


def _fail_on_findings(fail_on: str, counts: summary.SarifCounts) -> bool:
    if fail_on == "error":
        return counts.by_severity["error"] > 0
    if fail_on == "warning":
        return counts.by_severity["error"] + counts.by_severity["warning"] > 0
    return False


def consumer_inputs(args: argparse.Namespace) -> tuple[list[Path], list[Path]]:
    """Consumer reports files and local pack directories from the options.

    Values may be runfile locations (baked in by the Bazel macro) or paths relative
    to the directory ``bazel run`` was started in.
    """
    base = bz.user_path(".")
    reports_files = [
        rf.resolve_option_path(v, "--reports-file", base) for v in args.reports_file
    ]
    pack_dirs = [bz.user_path(p) for p in args.additional_pack_dir]
    pack_dirs += [
        rf.resolve_option_path(v, "--additional-pack-qlpack", base).parent
        for v in args.additional_pack_qlpack
    ]
    return reports_files, pack_dirs


def run(args: argparse.Namespace) -> int:
    pins = cfg.load_pins(rf.resolve_env_path(rf.ENV_PACKS))
    if args.print_pins:
        print(json.dumps(pins.as_json(), indent=2))
        return 0

    reports_files, extra_pack_dirs = consumer_inputs(args)
    reports_config = cfg.load_reports(
        rf.resolve_env_path(rf.ENV_REPORTS), reports_files
    )
    if args.list_reports:
        print(format_reports(reports_config))
        return 0

    selected = cfg.select_reports(reports_config, args.safety_level, args.report)
    by_report = cfg.refs_of(reports_config, selected)
    complexity_dir = rf.resolve_pack_dir(rf.ENV_COMPLEXITY_QLPACK)
    diagnostics_dir = rf.resolve_pack_dir(rf.ENV_DIAGNOSTICS_QLPACK)
    local_dirs = [complexity_dir, *extra_pack_dirs]
    local_packs = pk.find_local_packs(local_dirs)
    cfg.validate_refs(by_report, pins, local_packs.keys())

    targets = effective_targets(args)
    if not bz.split_bazel_targets(targets)[0]:
        raise ScaError(
            "no Bazel targets given; pass targets on the command line or use a "
            "codeql_sca() target that defines them"
        )

    workspace = bz.find_workspace()
    output_dir = (
        bz.user_path(args.output_dir)
        if args.output_dir
        else workspace / DEFAULT_OUTPUT_DIRNAME
    )
    results_dir = output_dir / RESULTS_DIRNAME
    work_dir = output_dir / WORK_DIRNAME
    db_dir = work_dir / "db"
    bazel = bz.find_bazel(args.bazel)
    codeql = rf.resolve_codeql(args.codeql_cli)
    cli_info = pk.get_cli_info(codeql)
    pk.check_cli_version(cli_info, pins)

    if not args.no_auto_exclude:
        dirs = args.auto_exclude_dir or list(bz.DEFAULT_AUTO_EXCLUDE_DIRS)
        targets = [*targets, *bz.auto_exclude_targets(workspace, dirs, [output_dir])]

    threads = analysis.default_threads(args.threads)
    ram_mb = analysis.default_ram_mb(args.ram)
    replay_jobs = args.replay_jobs or min(
        os.cpu_count() or 1, analysis.MAX_CODEQL_THREADS
    )
    category = args.sarif_category or f"static-analysis-codeql/{args.platform}"
    patterns = compiler_check.resolve_expected(args.platform, args.expect_compiler)
    if args.expect_gcc_major is None:
        gcc_major = compiler_check.DEFAULT_EXPECT_GCC_MAJOR.get(args.platform)
    else:
        gcc_major = args.expect_gcc_major or None
    if not patterns:
        gcc_major = None
        require_root = None
    elif args.require_compiler_root is None:
        require_root = bz.get_output_base(bazel, workspace)
    else:
        require_root = (
            bz.user_path(args.require_compiler_root)
            if args.require_compiler_root
            else None
        )

    cache_dir = (
        bz.user_path(args.pack_cache_dir)
        if args.pack_cache_dir
        else pk.default_pack_cache()
    )
    refs = cfg.union_refs(by_report)
    resolved = pk.ensure_packs(
        codeql,
        refs,
        cache_dir=cache_dir,
        distribution=cli_info.distribution,
        local_packs=local_packs,
        listed_external=pins.packs,
    )
    additional = pk.additional_packs_value(cache_dir, local_dirs)

    logging.info("Configuration:")
    logging.info("  TARGETS: %s", " ".join(targets))
    logging.info("  BAZEL CONFIGS: %s", " ".join(args.configs) or "<none>")
    logging.info("  PLATFORM: %s", args.platform)
    logging.info("  REPORTS: %s", ", ".join(selected))
    logging.info(
        "  CODEQL: %s (threads=%d, ram=%dMB)", cli_info.version, threads, ram_mb
    )
    logging.info("  OUTPUT_DIR: %s", output_dir)
    logging.info("  COMPILER GATE: %s", " | ".join(patterns) or "disabled")

    _clean_outputs(results_dir, work_dir)
    pins_data = pk.pins_json(pins, cli_info, resolved)
    pk.write_pins_json(results_dir / "pins.json", pins_data)

    wrapper_env = os.environ.get(rf.ENV_WRAPPER_SRC)
    settings = extraction.ExtractionSettings(
        codeql=codeql,
        bazel=bazel,
        workspace=workspace,
        work_dir=work_dir,
        db_dir=db_dir,
        targets=targets,
        configs=args.configs,
        bazel_options=args.bazel_options,
        platform=args.platform,
        replay_jobs=replay_jobs,
        exclude_globs=args.exclude_glob,
        expect_patterns=patterns,
        require_root=require_root,
        expect_gcc_major=gcc_major,
        max_failed_command_pct=args.max_failed_command_pct,
        force_rebuild=args.force_rebuild,
        verbose=args.verbose,
        bcc_sh=rf.resolve_env_path(rf.ENV_BCC_SH),
        bcc_env=rf.bcc_environment(),
        wrapper_source=rf.resolve_env_path(rf.ENV_WRAPPER_SRC) if wrapper_env else None,
    )

    logging.info("> Initialize CodeQL database")
    extraction.init_database(settings)
    compilers_json = results_dir / "compilers.json"
    outcome = extraction.run_extraction(settings, compilers_json)

    logging.info("> Check database extraction")
    db_report = check_db.analyze_database(
        db_dir,
        max_failed_command_pct=args.max_failed_command_pct,
        max_commands_with_issues_pct=args.max_commands_with_issues_pct,
        max_unique_parse_errors=args.max_unique_parse_errors,
    )
    db_text = check_db.format_report(db_report)
    (results_dir / "extraction-summary.txt").write_text(db_text, encoding="utf-8")
    print(db_text, end="")
    if not db_report.passed:
        raise ScaError(
            "extraction quality thresholds failed: " + "; ".join(db_report.failures)
        )

    logging.info("> Finalize database")
    analysis.finalize_database(codeql, db_dir, threads, ram_mb)
    compiler_check.run_post_gate(
        outcome.gate,
        codeql=codeql,
        database=db_dir,
        diagnostics_dir=diagnostics_dir,
        work_dir=work_dir,
        execroot=outcome.execroot,
        tolerance_pct=args.max_failed_command_pct,
        threads=threads,
        compilers_json=compilers_json,
    )

    logging.info("> Run queries")
    analysis.run_queries(
        codeql, db_dir, refs, additional, threads, ram_mb, args.verbose
    )

    logging.info("> Write CSV/SARIF reports")
    analysis.generate_reports(
        codeql,
        db_dir,
        by_report,
        results_dir,
        additional_packs=additional,
        threads=threads,
        source_root=workspace,
        verbose=args.verbose,
    )
    combined = results_dir / "code-scanning.sarif"
    analysis.generate_combined_sarif(
        codeql,
        db_dir,
        refs,
        combined,
        category=category,
        additional_packs=additional,
        threads=threads,
        source_root=workspace,
        verbose=args.verbose,
    )

    pruned = analysis.prune_external_results(
        combined, source_root=workspace, execroot=outcome.execroot
    )
    if pruned["results"] or pruned["locations"]:
        logging.info(
            "> Removed %d result(s) and %d location(s) in code outside the workspace "
            "from %s (external repositories, toolchain and system headers)",
            pruned["results"],
            pruned["locations"],
            combined.name,
        )
    sarif_report = check_sarif.analyze(check_sarif.load_sarif(combined))
    (results_dir / "sarif-summary.txt").write_text(sarif_report.text, encoding="utf-8")
    print(sarif_report.text, end="")
    per_report_counts = {
        n: summary.load_counts(results_dir / f"{n}.sarif") for n in selected
    }
    combined_counts = summary.load_counts(combined)
    (results_dir / "summary.md").write_text(
        summary.build_summary(
            per_report=per_report_counts,
            combined=combined_counts,
            compilers=outcome.gate.to_json(),
            extraction=db_report.to_dict(),
            pins=pins_data,
            dropped_external=pruned["results"],
        ),
        encoding="utf-8",
    )
    if sarif_report.uri_violations:
        raise ScaError(
            "SARIF result locations are not workspace-relative; GitHub Code Scanning "
            "would drop or misplace these alerts (see sarif-summary.txt)"
        )
    if sarif_report.quality_failed:
        logging.warning(
            "SARIF quality checks reported problems (see sarif-summary.txt)"
        )

    logging.info("> Done. Results are stored in: %s", results_dir)
    if _fail_on_findings(args.fail_on, combined_counts):
        logging.error("Findings at or above --fail-on=%s were reported", args.fail_on)
        return EXIT_FINDINGS
    return 0


def main(argv: Sequence[str] | None = None) -> int:
    args = parse_args(argv)
    setup_logging(args.verbose)
    try:
        return run(args)
    except ScaError as exc:
        logging.error("%s", exc)
        return exc.exit_code


if __name__ == "__main__":
    raise SystemExit(main())
