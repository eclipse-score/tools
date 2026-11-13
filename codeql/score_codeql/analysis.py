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

"""Finalize the database, evaluate queries once and interpret results per report."""

from __future__ import annotations

import concurrent.futures
import json
import logging
import os
import re
from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import Any
from urllib.parse import unquote

from score_codeql import csv_header, sarif_suppressions
from score_codeql.config import PackRef
from score_codeql.errors import ScaError
from score_codeql.process import run_cmd

MAX_CODEQL_THREADS = 16
MAX_RAM_MB = 12288
MAX_REPORT_WORKERS = 4
_SARIF_FLAGS = (
    "--format=sarif-latest",
    "--no-sarif-minify",
    "--sarif-group-rules-by-pack",
    "--sarif-include-query-help=always",
    "--sublanguage-file-coverage",
)


def default_threads(requested: int | None = None) -> int:
    """Requested thread count, or ``min(cpu, 16)``."""
    if requested and requested > 0:
        return requested
    return min(os.cpu_count() or 1, MAX_CODEQL_THREADS)


def default_ram_mb(
    requested: int | None = None, meminfo: Path = Path("/proc/meminfo")
) -> int:
    """Requested RAM budget, or 75 % of ``MemAvailable`` capped at 12288 MB."""
    if requested and requested > 0:
        return requested
    try:
        for line in meminfo.read_text(encoding="utf-8").splitlines():
            if line.startswith("MemAvailable:"):
                available_mb = int(line.split()[1]) // 1024
                return max(1024, min(MAX_RAM_MB, available_mb * 3 // 4))
    except (OSError, ValueError, IndexError):
        pass
    return MAX_RAM_MB


def finalize_database(codeql: Path, database: Path, threads: int, ram_mb: int) -> None:
    run_cmd(
        [
            str(codeql),
            "database",
            "finalize",
            "-v",
            "-j",
            str(threads),
            f"--ram={ram_mb}",
            str(database),
        ]
    )


def run_queries(
    codeql: Path,
    database: Path,
    refs: Sequence[PackRef],
    additional_packs: str,
    threads: int,
    ram_mb: int,
    verbose: bool = False,
) -> None:
    """Evaluate the union of all selected queries in a single pass."""
    run_cmd(
        [
            str(codeql),
            "database",
            "run-queries",
            *(["--verbose"] if verbose else []),
            str(database),
            "--xterm-progress=no",
            "-j",
            str(threads),
            f"--ram={ram_mb}",
            f"--additional-packs={additional_packs}",
            *(str(r) for r in refs),
        ]
    )


def interpret(
    codeql: Path,
    database: Path,
    refs: Sequence[PackRef],
    *,
    fmt: str,
    output: Path,
    additional_packs: str,
    threads: int,
    verbose: bool = False,
    category: str | None = None,
) -> None:
    """One ``interpret-results`` call; *fmt* is ``sarif`` or ``csv``."""
    if fmt == "sarif":
        format_flags = list(_SARIF_FLAGS)
        if category:
            format_flags.append(f"--sarif-category={category}")
    elif fmt == "csv":
        format_flags = ["--format=csv"]
    else:
        raise ValueError(f"unknown format {fmt!r}")
    run_cmd(
        [
            str(codeql),
            "database",
            "interpret-results",
            *(["--verbose"] if verbose else []),
            str(database),
            *format_flags,
            f"--output={output}",
            "-j",
            str(threads),
            f"--additional-packs={additional_packs}",
            *(str(r) for r in refs),
        ]
    )


def _post_process_sarif(sarif: Path, source_root: Path) -> None:
    if sarif_suppressions.annotate_sarif(sarif, source_root) != 0:
        raise ScaError(f"failed to annotate suppressions in {sarif}")


def generate_report(
    codeql: Path,
    database: Path,
    name: str,
    refs: Sequence[PackRef],
    results_dir: Path,
    *,
    additional_packs: str,
    threads: int,
    source_root: Path,
    verbose: bool = False,
) -> tuple[Path, Path]:
    """SARIF + CSV for one report with suppression and CSV header post-processing."""
    sarif = results_dir / f"{name}.sarif"
    csv_file = results_dir / f"{name}.csv"
    logging.info("Generate %s (SARIF)...", name)
    interpret(
        codeql,
        database,
        refs,
        fmt="sarif",
        output=sarif,
        additional_packs=additional_packs,
        threads=threads,
        verbose=verbose,
    )
    logging.info("Generate %s (CSV)...", name)
    interpret(
        codeql,
        database,
        refs,
        fmt="csv",
        output=csv_file,
        additional_packs=additional_packs,
        threads=threads,
        verbose=verbose,
    )
    if csv_header.ensure_csv_header(csv_file) != 0:
        raise ScaError(f"failed to fix the CSV header of {csv_file}")
    _post_process_sarif(sarif, source_root)
    return sarif, csv_file


def generate_reports(
    codeql: Path,
    database: Path,
    by_report: Mapping[str, Sequence[PackRef]],
    results_dir: Path,
    *,
    additional_packs: str,
    threads: int,
    source_root: Path,
    verbose: bool = False,
) -> None:
    """Generate all per-report outputs; threads are divided across parallel workers."""
    workers = max(1, min(len(by_report), MAX_REPORT_WORKERS))
    per_worker = max(1, threads // workers)
    if workers == 1:
        for name, refs in by_report.items():
            generate_report(
                codeql,
                database,
                name,
                refs,
                results_dir,
                additional_packs=additional_packs,
                threads=per_worker,
                source_root=source_root,
                verbose=verbose,
            )
        return
    with concurrent.futures.ThreadPoolExecutor(max_workers=workers) as executor:
        futures = {
            executor.submit(
                generate_report,
                codeql,
                database,
                name,
                refs,
                results_dir,
                additional_packs=additional_packs,
                threads=per_worker,
                source_root=source_root,
                verbose=verbose,
            ): name
            for name, refs in by_report.items()
        }
        for future in concurrent.futures.as_completed(futures):
            try:
                future.result()
            except ScaError as exc:
                raise ScaError(
                    f"report generation failed for {futures[future]!r}: {exc}",
                    exc.exit_code,
                ) from exc


def generate_combined_sarif(
    codeql: Path,
    database: Path,
    refs: Sequence[PackRef],
    output: Path,
    *,
    category: str,
    additional_packs: str,
    threads: int,
    source_root: Path,
    verbose: bool = False,
) -> None:
    """One SARIF run over the union of all refs: the only file uploaded to Code Scanning."""
    logging.info("Generate combined SARIF (%s)...", output.name)
    interpret(
        codeql,
        database,
        refs,
        fmt="sarif",
        output=output,
        additional_packs=additional_packs,
        threads=threads,
        verbose=verbose,
        category=category,
    )
    _post_process_sarif(output, source_root)


_SCHEME_RE = re.compile(r"^[A-Za-z][A-Za-z0-9+.-]*:")


def _under(path: Path, root: Path) -> bool:
    try:
        path.resolve().relative_to(root.resolve())
    except (OSError, ValueError):
        return False
    return True


def outside_workspace(
    uri: str, *, source_root: Path, execroot: Path | None = None
) -> bool:
    """True if *uri* refers to code that is not part of the analysed workspace.

    GitHub Code Scanning can only display alerts for files of the repository.
    Results in third-party code — Bazel external repositories, the hermetic
    toolchain and its sysroot, or system headers — are therefore not uploaded.

    Everything that belongs to the workspace is *not* classified as outside,
    including badly expressed paths such as ``bazel-out/...`` or an absolute
    execroot path; ``check_sarif`` reports those as errors instead of silently
    dropping them.
    """
    if not uri:
        return False
    path = unquote(uri)
    if _SCHEME_RE.match(path):
        if not path.startswith("file:"):
            return False
        path = path[len("file:") :].lstrip("/")
        path = "/" + path
    if not path.startswith("/") and not re.match(r"^[A-Za-z]:[\\/]", path):
        # Workspace-relative: only `external/...` never exists in the repository.
        return path.replace("\\", "/").split("/", 1)[0] == "external"
    absolute = Path(path)
    if _under(absolute, source_root):
        return False
    if execroot is not None and _under(absolute, execroot / "external"):
        return True
    if execroot is not None and _under(absolute, execroot):
        return False

    # Outside both the workspace and the execroot. Existing files are the
    # hermetic toolchain, its sysroot or system headers (third-party code); a
    # path that does not exist cannot be analysed at all and is reported by
    # ``check_sarif`` instead of being dropped silently.
    return absolute.exists()


def _filter_locations(result: dict[str, Any], keep) -> tuple[dict[str, Any], int]:
    """Drop the locations of *result* for which *keep* is False."""
    dropped = 0
    for key in ("locations", "relatedLocations"):
        locations = result.get(key)
        if not isinstance(locations, list):
            continue
        kept = []
        for location in locations:
            uri = (
                location.get("physicalLocation", {})
                .get("artifactLocation", {})
                .get("uri")
            )
            if uri is not None and not keep(uri):
                dropped += 1
                continue
            kept.append(location)
        if kept:
            result[key] = kept
        else:
            result.pop(key, None)
    return result, dropped


def prune_external_results(
    sarif: Path, *, source_root: Path, execroot: Path | None = None
) -> dict[str, int]:
    """Remove results and locations in third-party code from *sarif*.

    The file is rewritten in place. Returns the number of dropped ``results``
    and ``locations`` (primary and related), so the caller can report them in
    the summary.
    """
    try:
        with sarif.open("r", encoding="utf-8") as fh:
            document = json.load(fh)
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ScaError(f"cannot read SARIF {sarif}: {exc}") from exc

    def keep(uri: str) -> bool:
        return not outside_workspace(uri, source_root=source_root, execroot=execroot)

    counts = {"results": 0, "locations": 0}
    for run in document.get("runs", []) or []:
        kept_results = []
        for result in run.get("results", []) or []:
            had_location = bool(result.get("locations"))
            result, dropped = _filter_locations(result, keep)
            counts["locations"] += dropped
            if had_location and not result.get("locations"):
                counts["results"] += 1
                continue
            kept_results.append(result)
        run["results"] = kept_results

    with sarif.open("w", encoding="utf-8") as fh:
        json.dump(document, fh, indent=2)
        fh.write("\n")
    return counts
