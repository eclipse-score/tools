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

"""Summarise a CodeQL SARIF file and validate it for Code Scanning.

Prints the frequency of every rule id, tool notifications and database-quality
metrics. Two kinds of checks produce a failing exit status:

* **Result location URIs** must be workspace-relative. Absolute paths,
  ``..`` escapes, ``bazel-*`` output trees and ``external/`` paths are dropped or
  misplaced by GitHub Code Scanning, so they are rejected here.
* **Quality metrics** (compilations without errors, syntax errors, compiler
  errors) fail unless ``--no-fail`` is given.

Missing or invalid SARIF input always fails.
"""

from __future__ import annotations

import argparse
import json
import logging
import re
from collections import Counter
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any
from urllib.parse import unquote

from score_codeql.errors import ScaError
from score_codeql.logging_utils import setup_logging

_SCHEME_RE = re.compile(r"^[A-Za-z][A-Za-z0-9+.-]*:")
_MAX_REPORTED_URI_VIOLATIONS = 20


@dataclass
class SarifReport:
    """Result of analysing a SARIF file."""

    lines: list[str] = field(default_factory=list)
    quality_failed: bool = False
    uri_violations: list[str] = field(default_factory=list)

    @property
    def text(self) -> str:
        return "\n".join(self.lines) + ("\n" if self.lines else "")


def load_sarif(path: Path) -> list[dict[str, Any]]:
    """Return the ``runs`` array of *path*; raises ``ScaError`` if unusable."""
    try:
        with path.open("r", encoding="utf-8") as fh:
            sarif = json.load(fh)
    except FileNotFoundError as exc:
        raise ScaError(f"SARIF file not found: {path}") from exc
    except (OSError, UnicodeDecodeError) as exc:
        raise ScaError(f"cannot read {path}: {exc}") from exc
    except json.JSONDecodeError as exc:
        raise ScaError(f"invalid JSON in {path}: {exc}") from exc
    if not isinstance(sarif, dict):
        raise ScaError(
            f"expected a JSON object at the root of {path}, got {type(sarif).__name__}"
        )
    runs = sarif.get("runs", [])
    if not isinstance(runs, list):
        raise ScaError(
            f"expected 'runs' to be a JSON array in {path}, got {type(runs).__name__}"
        )
    if not runs:
        raise ScaError(f"no runs found in SARIF file: {path}")
    return runs


def uri_problem(uri: str) -> str | None:
    """Why *uri* is not a workspace-relative path, or ``None`` if it is."""
    if not uri:
        return "empty URI"
    if _SCHEME_RE.match(uri):
        return "absolute URI with scheme"
    path = unquote(uri)
    if path.startswith("/") or re.match(r"^[A-Za-z]:[\\/]", path):
        return "absolute path"
    parts = path.replace("\\", "/").split("/")
    if ".." in parts:
        return "path escapes the workspace ('..')"
    if parts[0].startswith("bazel-"):
        return "Bazel output tree path"
    if parts[0] == "external":
        return "external repository path"
    return None


def _result_uris(result: dict[str, Any]) -> list[str]:
    uris: list[str] = []
    for key in ("locations", "relatedLocations"):
        for location in result.get(key, []) or []:
            uri = (
                location.get("physicalLocation", {})
                .get("artifactLocation", {})
                .get("uri")
            )
            if uri is not None:
                uris.append(uri)
    return uris


def check_result_uris(runs: list[dict[str, Any]]) -> list[str]:
    """Describe every distinct non-workspace-relative result location URI."""
    problems: dict[str, str] = {}
    for run in runs:
        for result in run.get("results", []):
            for uri in _result_uris(result):
                reason = uri_problem(uri)
                if reason and uri not in problems:
                    problems[uri] = (
                        f"{uri} ({reason}; rule {result.get('ruleId', '?')})"
                    )
    return sorted(problems.values())


def get_metric(
    runs: list[dict[str, Any]], rule_id: str, metric_name: str
) -> float | None:
    """Value of a named metric from ``properties.metricResults``, or ``None``."""
    for run in runs:
        for metric in run.get("properties", {}).get("metricResults", []):
            if (
                metric.get("ruleId") == rule_id
                and metric.get("message", {}).get("text", "") == metric_name
            ):
                return metric.get("value")
    return None


def get_compiler_error_details(runs: list[dict[str, Any]]) -> list[str]:
    errors = []
    for run in runs:
        for metric in run.get("properties", {}).get("metricResults", []):
            if metric.get("ruleId") == "cpp/telemetry/compiler-errors":
                msg = metric.get("message", {}).get("text", "")
                if msg:
                    flat = " ".join(msg.split())
                    tail = flat[-70:]
                    if len(flat) > 70:
                        tail = f"...{tail}"
                    errors.append(f"{int(metric.get('value', 1)):4d}x  {tail}")
    return errors


def _rule_sources(run: dict[str, Any]) -> list[dict[str, Any]]:
    tool = run.get("tool", {})
    return [tool.get("driver", {}), *tool.get("extensions", [])]


def results_frequency(runs: list[dict[str, Any]]) -> list[tuple[str, int]]:
    counter: Counter[str] = Counter()
    for run in runs:
        for result in run.get("results", []):
            counter[result.get("ruleId", "<unknown>")] += 1
    return sorted(counter.items(), key=lambda kv: (-kv[1], kv[0]))


def _quality_lines(runs: list[dict[str, Any]], verbose: bool) -> tuple[list[str], bool]:
    lines: list[str] = []
    failed = False

    def check(label: str, ok: bool | None, detail: str = "") -> None:
        nonlocal failed
        suffix = f" ({detail})" if detail else ""
        if ok is None:
            if verbose:
                lines.append(f"[quality check] SKIP: {label}{suffix}")
        elif ok:
            if verbose:
                lines.append(f"[quality check] PASS: {label}{suffix}")
        else:
            failed = True
            lines.append(f"[quality check] FAIL: {label}{suffix}")

    rule_count = sum(
        len(source.get("rules", [])) for run in runs for source in _rule_sources(run)
    )
    check("Expect number of rules > 0, actual:", rule_count > 0, f"{rule_count} rules")

    pct = get_metric(
        runs,
        "cpp/telemetry/database-quality",
        "Percentage of compilations without errors",
    )
    label = "Expect: compilations without errors == 100%, actual:"
    check(
        label,
        None if pct is None else pct >= 100.0,
        "metric not found" if pct is None else f"{pct:.1f}%",
    )

    syntax = get_metric(runs, "cpp/telemetry/extraction-metrics", "syntax errors")
    label = "Expect: syntax errors == 0, actual:"
    check(
        label,
        None if syntax is None else int(syntax) == 0,
        "metric not found" if syntax is None else str(int(syntax)),
    )

    compiler = get_metric(runs, "cpp/telemetry/extraction-metrics", "compiler errors")
    label = "Expect: compiler errors == 0, actual:"
    check(
        label,
        None if compiler is None else int(compiler) == 0,
        "metric not found" if compiler is None else str(int(compiler)),
    )
    if compiler is not None and int(compiler) > 0:
        lines.extend(
            f"[quality check]          {d}" for d in get_compiler_error_details(runs)
        )
    return lines, failed


def rules_table(runs: list[dict[str, Any]]) -> list[str]:
    rules = []
    for run in runs:
        for source in _rule_sources(run):
            for rule in source.get("rules", []):
                rules.append(
                    (
                        rule.get("id", ""),
                        rule.get("defaultConfiguration", {}).get("level", ""),
                        rule.get("shortDescription", {}).get("text", ""),
                    )
                )
    rules.sort(key=lambda r: r[0])
    return [
        f"[ruleset] {rule_id:<55} {('[' + level + ']') if level else '':<9} {desc}"
        for rule_id, level, desc in rules
    ]


def notification_lines(runs: list[dict[str, Any]]) -> list[str]:
    lines = []
    for run in runs:
        for invocation in run.get("invocations", []):
            for notif in invocation.get("toolExecutionNotifications", []):
                if notif.get("level", "none") not in ("warning", "error"):
                    continue
                msg = notif.get("message", {}).get("text", "").split("\n")[0]
                rule_id = notif.get("descriptor", {}).get("id", "")
                lines.append(f"[toolExecutionNotification] {rule_id}: {msg}")
    return lines


def analyze(runs: list[dict[str, Any]], verbose: bool = False) -> SarifReport:
    """Build the full text report for *runs*."""
    report = SarifReport()
    prefix = "[results] " if verbose else ""
    for rule_id, count in results_frequency(runs):
        report.lines.append(f"{prefix}{count:5d}  {rule_id}")
    quality, report.quality_failed = _quality_lines(runs, verbose)
    report.lines.extend(quality)
    if verbose:
        report.lines.extend(rules_table(runs))
    report.lines.extend(notification_lines(runs))
    report.uri_violations = check_result_uris(runs)
    shown = report.uri_violations[:_MAX_REPORTED_URI_VIOLATIONS]
    for violation in shown:
        report.lines.append(f"[uri check] FAIL: {violation}")
    hidden = len(report.uri_violations) - len(shown)
    if hidden > 0:
        report.lines.append(f"[uri check] ... and {hidden} more")
    return report


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Summarize CodeQL SARIF analysis results."
    )
    parser.add_argument("sarif_file", nargs="?", type=Path, default=Path("cpp.sarif"))
    parser.add_argument(
        "--no-fail",
        action="store_true",
        help="Return 0 when quality metrics fail (URI violations and invalid input still fail)",
    )
    parser.add_argument(
        "--no-uri-check",
        action="store_true",
        help="Do not require workspace-relative result location URIs",
    )
    parser.add_argument("-v", "--verbose", action="store_true")
    args = parser.parse_args(argv)
    setup_logging(args.verbose, quiet_default=True)
    try:
        report = analyze(load_sarif(args.sarif_file), args.verbose)
    except ScaError as exc:
        logging.error("%s", exc)
        return exc.exit_code
    print(report.text, end="")
    if report.uri_violations and not args.no_uri_check:
        return 1
    return 1 if report.quality_failed and not args.no_fail else 0


if __name__ == "__main__":
    raise SystemExit(main())
