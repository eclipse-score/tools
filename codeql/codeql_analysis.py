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
"""CodeQL C/C++ analysis of a traced Bazel build against a coding-standards pack.

The database is created by tracing a real `bazel build` (compiler and linker
invocations), so no compile commands have to be replayed. The queries come from
a pre-compiled codeql-coding-standards release pack; deviations and guideline
recategorizations use the coding standards' own `coding-standards.yml` mechanism.
Run through the `codeql_analysis` macro (see defs.bzl).
"""

from __future__ import annotations

import argparse
import datetime
import json
import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path
from typing import Any

import yaml

# Forces the traced build to execute every action, a cache hit would not be traced.
CACHE_BYPASS_FLAGS = [
    "--nouse_action_cache",
    "--noremote_accept_cached",
    "--noremote_upload_local_results",
    "--disk_cache=",
    "--platform_suffix=codeql",
]


def real_path(path: str | Path) -> Path:
    """Absolute path with symlinks resolved.

    Bazel runfiles are a symlink farm, and CodeQL's pack and data-extension
    lookups do not follow symlinks.
    """
    return Path(os.path.realpath(os.path.abspath(path)))


def pack_root(qlpack: str | Path, suite: str) -> Path:
    """Root of the pre-compiled query pack, checked to contain `suite`."""
    root = real_path(qlpack).parent
    if not (root / suite).is_file():
        raise RuntimeError(f"Query suite '{suite}' is missing in the pack at {root}")
    return root


def coding_standards_root(common_qlpack: str | Path) -> Path:
    """Root of the coding-standards sources, given their `cpp/common/src/qlpack.yml`."""
    return real_path(common_qlpack).parents[3]


def read_pack_identity(root: Path) -> tuple[str, str]:
    """Return the (name, version) declared in the pack's qlpack.yml."""
    name = version = None
    for line in (root / "qlpack.yml").read_text(encoding="utf-8").splitlines():
        key, _, value = line.strip().partition(":")
        if key == "name" and name is None:
            name = value.strip().strip("'\"")
        elif key == "version" and version is None:
            version = value.strip().strip("'\"")
    if not name or not version:
        raise RuntimeError(
            f"Could not read pack name/version from {root / 'qlpack.yml'}"
        )
    return name, version


def merged_environment(
    base: dict[str, str], tracer_env: dict[str, str]
) -> dict[str, str]:
    """`base` with the CodeQL tracer variables prepended (e.g. LD_PRELOAD)."""
    env = dict(base)
    for key, value in tracer_env.items():
        env[key] = f"{value}:{env[key]}" if key in env else value
    return env


def bazel_build_command(
    bazel: str,
    targets: list[str],
    build_configs: list[str],
    bazel_args: list[str],
    tracer_env: dict[str, str],
    database: Path,
    seed: str,
) -> list[str]:
    """The traced `bazel build`; every tracer variable is forwarded into the actions."""
    return [
        bazel,
        "build",
        *CACHE_BYPASS_FLAGS,
        "--stamp",
        f"--action_env=CODEQL_SEED_FORCE_RECOMPILE={seed}",
        f"--sandbox_writable_path={database}",
        *(f"--config={config}" for config in build_configs),
        *bazel_args,
        # Remote workers have neither the tracer nor the database.
        "--remote_executor=",
        *(f"--action_env={key}" for key in sorted(tracer_env)),
        "--",
        *targets,
    ]


def production_query(targets: list[str]) -> str:
    """Query for the C/C++ rule targets of `targets` (negative patterns honoured) that are not testonly."""
    positive = " + ".join(t for t in targets if not t.startswith("-"))
    negative = "".join(f" - {t[1:]}" for t in targets if t.startswith("-"))
    selected = f'kind("^cc_", ({positive}){negative})'
    return f"{selected} except attr(testonly, 1, {selected})"


def production_targets(bazel: str, workspace: Path, targets: list[str]) -> list[str]:
    result = subprocess.run(
        [bazel, "query", production_query(targets), "--output=label"],
        cwd=workspace,
        capture_output=True,
        text=True,
        check=True,
    )
    labels = result.stdout.split()
    if not labels:
        raise RuntimeError(f"No non-testonly targets in {targets}")
    return labels


def create_database(args: argparse.Namespace, workspace: Path, database: Path) -> None:
    targets = (
        production_targets(args.bazel, workspace, args.target)
        if args.exclude_testonly
        else args.target
    )
    subprocess.run(
        [
            args.codeql,
            "database",
            "init",
            "--overwrite",
            "--begin-tracing",
            "--language=cpp",
            f"--codescanning-config={args.codescanning_config}",
            f"--source-root={workspace}",
            "--",
            database,
        ],
        check=True,
    )
    tracer_env = json.loads(
        (database / "temp/tracingEnvironment/start-tracing.json").read_text(
            encoding="utf-8"
        )
    )
    env = merged_environment(dict(os.environ), tracer_env)

    # Converts every coding-standards.y(a)ml (deviations) and indexes it into the database.
    subprocess.run(
        [args.process_config, f"--working-dir={workspace}"],
        env=env,
        cwd=workspace,
        check=True,
    )

    seed = datetime.datetime.now().strftime("%Y%m%d%H%M%S%f")
    command = bazel_build_command(
        args.bazel,
        targets,
        args.build_config,
        args.bazel_arg,
        tracer_env,
        database,
        seed,
    )
    if args.keep_going:
        command.insert(2, "--keep_going")
    if args.exclude_testonly:
        # Query results are explicit labels; wildcards would skip incompatible targets silently.
        command.insert(2, "--skip_incompatible_explicit_targets")
    build = subprocess.run(command, env=env, cwd=workspace)
    if build.returncode:
        if not args.keep_going:
            raise subprocess.CalledProcessError(build.returncode, command)
        print(
            f"WARNING: traced build exited with {build.returncode}; analysing what was built",
            file=sys.stderr,
        )

    subprocess.run(
        [args.codeql, "database", "finalize", "-j=0", "--", database], check=True
    )


def read_yaml(path: Path) -> dict[str, Any]:
    data: object = yaml.safe_load(path.read_text(encoding="utf-8"))
    return data if isinstance(data, dict) else {}


def query_suite(
    name: str, version: str, suite: str, codescanning_config: Path, output: Path
) -> Path | None:
    """Suite of the pack with the config's query filters, which an explicit suite would bypass."""
    filters: object = read_yaml(codescanning_config).get("query-filters")
    if not isinstance(filters, list) or not filters:
        return None
    instructions = [{"import": suite, "from": name, "version": version}, *filters]
    output.write_text(yaml.safe_dump(instructions, sort_keys=False), encoding="utf-8")
    return output


def analyze_database(
    args: argparse.Namespace, workspace: Path, database: Path, output_dir: Path
) -> Path:
    root = pack_root(args.pack, args.suite)
    if args.query_spec:
        # A single query straight from the sources, e.g. to debug one rule.
        query = args.query_spec
        packs = (
            f"--additional-packs={coding_standards_root(args.coding_standards_sources)}"
        )
    else:
        name, version = read_pack_identity(root)
        suite = query_suite(
            name,
            version,
            args.suite,
            Path(args.codescanning_config),
            output_dir / f"{args.output_prefix}.qls",
        )
        query = str(suite) if suite else f"{name}@{version}:{args.suite}"
        packs = f"--search-path={root}"
    sarif = output_dir / f"{args.output_prefix}.sarif"
    subprocess.run(
        [
            args.codeql,
            "database",
            "analyze",
            "-j=0",
            database,
            query,
            packs,
            "--format=sarifv2.1.0",
            f"--output={sarif}",
        ],
        check=True,
    )
    if args.coding_standards_config:
        recategorize_sarif(args, workspace / args.coding_standards_config, sarif)
    normalize_sarif_rule_order(sarif)
    write_analysis_report(args, root, database, sarif, output_dir)
    return sarif


def recategorize_sarif(args: argparse.Namespace, config: Path, sarif: Path) -> None:
    """Apply the guideline recategorizations of `config` to the SARIF in place."""
    if not config.is_file():
        raise RuntimeError(f"Coding standards config not found: {config}")
    # The recategorization script fails on configs that only contain deviations.
    if not read_yaml(config).get("guideline-recategorizations"):
        return
    recategorized = sarif.with_name(sarif.name + ".recategorized")
    subprocess.run(
        [
            args.recategorize,
            "--coding-standards-schema-file",
            real_path(args.coding_standards_schema),
            "--sarif-schema-file",
            real_path(args.sarif_schema),
            config,
            sarif,
            recategorized,
        ],
        check=True,
    )
    os.replace(recategorized, sarif)


def prepare_offline_codeql_home(libraries: Path, home: Path) -> None:
    """Per-run $HOME so `analysis_report` resolves locked pack dependencies offline.

    Its queries pin dependencies that CodeQL only finds in the user pack cache or
    in --additional-packs; the pack already vendors them under `libraries`.
    """
    config = home / ".config" / "codeql" / "config"
    config.parent.mkdir(parents=True, exist_ok=True)
    config.write_text(f"--additional-packs {libraries}\n", encoding="utf-8")


def write_analysis_report(
    args: argparse.Namespace, root: Path, database: Path, sarif: Path, output_dir: Path
) -> None:
    """Compliance reports of the coding standards; a failure does not fail the analysis."""
    reports = output_dir / "analysis_reports" / args.output_prefix
    shutil.rmtree(reports, ignore_errors=True)
    home = output_dir / "codeql_home"
    prepare_offline_codeql_home(root / ".codeql" / "libraries", home)
    env = dict(os.environ)
    env["HOME"] = str(home)
    env["PATH"] = f"{real_path(args.codeql).parent}{os.pathsep}{env.get('PATH', '')}"
    result = subprocess.run(
        [args.analysis_report, database, sarif, reports],
        capture_output=True,
        text=True,
        env=env,
    )
    for stream in (result.stdout, result.stderr):
        if stream.strip():
            print(stream.strip())
    if result.returncode:
        print(
            f"WARNING: analysis_report exited with code {result.returncode}",
            file=sys.stderr,
        )


def normalize_sarif_rule_order(sarif: Path) -> None:
    """Sort rules and artifacts so that reruns produce byte-identical SARIF."""
    document = json.loads(sarif.read_text(encoding="utf-8"))
    for run in document.get("runs", []):
        normalize_sarif_run(run)
    normalized = sarif.with_name(sarif.name + ".normalized")
    normalized.write_text(json.dumps(document, indent=2) + "\n", encoding="utf-8")
    os.replace(normalized, sarif)


def normalize_sarif_run(run: dict[str, Any]) -> None:
    driver = run.get("tool", {}).get("driver", {})
    rules = sorted(
        enumerate(driver.get("rules", [])), key=lambda item: item[1].get("id", "")
    )
    rule_index = {old: new for new, (old, _) in enumerate(rules)}
    driver["rules"] = [rule for _, rule in rules]

    artifacts = sorted(
        enumerate(run.get("artifacts", [])),
        key=lambda item: item[1].get("location", {}).get("uri", ""),
    )
    artifact_index = {old: new for new, (old, _) in enumerate(artifacts)}
    if "artifacts" in run:
        run["artifacts"] = [artifact for _, artifact in artifacts]
    for artifact in run.get("artifacts", []):
        # Artifacts refer to themselves and their parent by index, too.
        location = artifact.get("location", {})
        if "index" in location:
            location["index"] = artifact_index[location["index"]]
        if "parentIndex" in artifact:
            artifact["parentIndex"] = artifact_index[artifact["parentIndex"]]

    for result in run.get("results", []):
        if "ruleIndex" in result:
            result["ruleIndex"] = rule_index[result["ruleIndex"]]
        if "index" in result.get("rule", {}):
            result["rule"]["index"] = rule_index[result["rule"]["index"]]
    for key, value in run.items():
        if key != "artifacts":
            remap_artifact_indices(value, artifact_index)


def remap_artifact_indices(value: object, artifact_index: dict[int, int]) -> None:
    if isinstance(value, dict):
        location = value.get("artifactLocation")
        if isinstance(location, dict) and "index" in location:
            if location.get("uri"):
                del location["index"]
            else:
                location["index"] = artifact_index[location["index"]]
        for child in value.values():
            remap_artifact_indices(child, artifact_index)
    elif isinstance(value, list):
        for child in value:
            remap_artifact_indices(child, artifact_index)


def bazel_output_path(bazel: str, workspace: Path) -> Path:
    result = subprocess.run(
        [bazel, "info", "output_path"],
        cwd=workspace,
        capture_output=True,
        text=True,
        check=True,
    )
    return Path(result.stdout.strip())


def parse_args(argv: list[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    tools = parser.add_argument_group(
        "tool locations (set by the codeql_analysis macro)"
    )
    tools.add_argument("--codeql", required=True, help="CodeQL CLI launcher")
    tools.add_argument(
        "--pack", required=True, help="qlpack.yml of the pre-compiled query pack"
    )
    tools.add_argument(
        "--process-config", required=True, help="process_coding_standards_config binary"
    )
    tools.add_argument(
        "--recategorize", required=True, help="guideline_recategorize binary"
    )
    tools.add_argument(
        "--analysis-report", required=True, help="analysis_report binary"
    )
    tools.add_argument("--coding-standards-schema", required=True)
    tools.add_argument("--sarif-schema", required=True)
    tools.add_argument(
        "--coding-standards-sources",
        required=True,
        help="cpp/common/src/qlpack.yml of the coding-standards sources",
    )
    parser.add_argument(
        "--codescanning-config",
        required=True,
        help="CodeQL code scanning config (query filters)",
    )
    parser.add_argument(
        "--suite",
        required=True,
        help="Suite inside the pack, e.g. codeql-suites/misra-cpp-default.qls",
    )
    parser.add_argument(
        "--coding-standards-config",
        help="Workspace-relative coding-standards.yml whose guideline recategorizations are applied to the SARIF",
    )
    parser.add_argument(
        "--target",
        action="extend",
        nargs="+",
        default=[],
        help="Bazel target patterns to build (repeatable, several per flag; "
        "exclusions as --target=-//pkg/...)",
    )
    parser.add_argument(
        "--build-config",
        action="append",
        default=[],
        help="Bazel --config of the traced build",
    )
    parser.add_argument(
        "--keep-going",
        action="store_true",
        help="Build with --keep_going and analyse what was built even if targets fail",
    )
    parser.add_argument(
        "--exclude-testonly",
        action="store_true",
        help="Build only rule targets that are not testonly (tests, mocks, test helpers)",
    )
    parser.add_argument(
        "--bazel-arg",
        action="append",
        default=[],
        help="Extra flag for the traced build",
    )
    parser.add_argument(
        "--bazel",
        default="bazel",
        help="Bazel executable of the traced build (e.g. a wrapper adding startup options)",
    )
    parser.add_argument(
        "--output-dir",
        help="Directory for SARIF and reports (default: bazel info output_path)",
    )
    parser.add_argument(
        "--output-prefix", default="codeql", help="SARIF file name without extension"
    )
    parser.add_argument(
        "--database",
        "--database-path",
        dest="database",
        help="Keep the CodeQL database in this directory instead of a temporary one",
    )
    parser.add_argument(
        "--phase",
        choices=["create-database", "analyze-database", "all"],
        default="all",
        help="Only create or only analyse the --database (default: both)",
    )
    parser.add_argument(
        "--query-spec",
        help="Analyse this query from the coding-standards sources instead of the pack suite",
    )
    args = parser.parse_args(argv)
    if args.phase != "all" and not args.database:
        parser.error(f"--phase={args.phase} needs --database")
    if args.phase != "analyze-database" and not args.target:
        parser.error("at least one --target is required")
    args.codeql = str(real_path(args.codeql))
    # Helper binaries stay inside this runfiles tree, where they find their own runfiles.
    for name in ("process_config", "recategorize", "analysis_report"):
        setattr(args, name, os.path.abspath(getattr(args, name)))
    args.codescanning_config = str(real_path(args.codescanning_config))
    return args


def main(argv: list[str] | None = None) -> int:
    args = parse_args(sys.argv[1:] if argv is None else argv)
    workspace = Path(os.environ.get("BUILD_WORKSPACE_DIRECTORY") or Path.cwd())
    cwd = Path(os.environ.get("BUILD_WORKING_DIRECTORY") or Path.cwd())
    output_dir = (
        cwd / args.output_dir
        if args.output_dir
        else bazel_output_path(args.bazel, workspace)
    )
    output_dir.mkdir(parents=True, exist_ok=True)

    if args.database:
        database = cwd / args.database
        database.parent.mkdir(parents=True, exist_ok=True)
        if args.phase != "analyze-database":
            create_database(args, workspace, database)
        if args.phase == "create-database":
            print(f"CodeQL database written to {database}")
            return 0
        sarif = analyze_database(args, workspace, database, output_dir)
    else:
        with tempfile.TemporaryDirectory(prefix="codeql_db_") as tmp:
            database = Path(tmp) / "db"
            create_database(args, workspace, database)
            sarif = analyze_database(args, workspace, database, output_dir)
    print(f"SARIF written to {sarif}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
