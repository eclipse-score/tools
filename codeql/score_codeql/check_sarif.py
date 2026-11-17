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
metrics. Three kinds of checks produce a failing exit status:

* **Result location URIs** must be workspace-relative. Absolute paths,
  ``..`` escapes, ``bazel-*`` output trees and ``external/`` paths are dropped or
  misplaced by GitHub Code Scanning, so they are rejected here.
* **An empty ruleset** means no query produced results, so nothing was analysed.
  This always fails, also with ``--no-fail``. ``cpp/telemetry/*`` rules are
  excluded from that count: they only exist to carry metrics and would make a
  metric-only run look like an analysed one.
* **Quality metrics** fail unless ``--no-fail`` is given. The gated metrics are
  the telemetry percentages that are deterministic evidence of a broken
  extraction (compilations, source/header files, ``#include`` directives) plus
  the number of syntax and compiler errors. The ``ErrorExpr``-based percentages
  (functions, expressions, lines of code) are **not** gated: they count every
  ``ErrorExpr`` the extractor synthesised, including documented artefacts such
  as the first argument of an allocator call, so they stay below 100 % for any
  project that uses the standard library. They are still reported as ``INFO``
  lines so a real deficit remains visible. Percentages that are legitimately
  below 100 % for type-resolution reasons (calls with an explicit target,
  expressions and variables with a known type) are reported as ``INFO`` in
  verbose mode only. In a multi-run SARIF the worst run decides for a
  percentage (``min``) and counts are summed, so a healthy run can never mask a
  broken one.

If the SARIF carries no ``cpp/telemetry`` metrics at all (for example because the
selected report contains no ``@kind metric`` queries), the quality checks cannot
be evaluated. That is reported with a visible ``WARN`` line so a run never looks
green because telemetry is missing. A single run without telemetry next to runs
that have it is reported the same way.

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
_TELEMETRY_PREFIX = "cpp/telemetry/"

# Gated: a value below 100 % here means whole translation units or headers were
# not extracted, which no legitimate build produces.
_GATED_PERCENTAGES = (
    ("cpp/telemetry/database-quality", "Percentage of compilations without errors"),
    (
        "cpp/telemetry/database-quality",
        "Percentage of source/header files without errors",
    ),
    (
        "cpp/telemetry/database-quality",
        "Percentage of successfully resolved #include directives",
    ),
)

# Not gated, always reported as INFO: these three percentages count every
# `ErrorExpr` the extractor synthesised, not only the ones that indicate a
# broken compilation. One large artefact is documented by CodeQL itself:
# `Function.hasErrors()` excludes the first argument of an allocator call
# because it is *always* extracted as `ErrorExpr` (placement new through
# `std::allocator_traits::construct`, i.e. every `std::vector::emplace_back`,
# `std::make_shared`, `std::function` construction, ...). The telemetry queries
# do not apply that exclusion, and the remaining nodes carry no source location
# at all (`file://:0:0:0:0`), so no source change can remove them either.
# Gating these at 100 % fails every C++ project that uses the standard library
# without saying anything about the analysed sources. The values are still
# printed so a real regression (a translation unit dropping out) stays visible.
_INFORMATIONAL_PERCENTAGES = (
    ("cpp/telemetry/database-quality", "Percentage of functions without errors"),
    ("cpp/telemetry/database-quality", "Percentage of non-error expressions"),
    ("cpp/telemetry/database-quality", "Percentage of lines of code without errors"),
)

# Not gated, `--verbose` only: indirect calls and template-dependent types
# legitimately keep these below 100 %, and they are noisy by nature.
_VERBOSE_PERCENTAGES = (
    ("cpp/telemetry/database-quality", "Percentage of calls with an explicit target"),
    ("cpp/telemetry/database-quality", "Percentage of expressions with a known type"),
    ("cpp/telemetry/database-quality", "Percentage of variables with a known type"),
)

_GATED_COUNTS = (
    ("cpp/telemetry/extraction-metrics", "syntax errors"),
    ("cpp/telemetry/extraction-metrics", "compiler errors"),
)


def format_percentage(value: float) -> str:
    """Format a percentage with up to four decimals (never hides a deficit)."""
    text = f"{value:.4f}".rstrip("0")
    if text.endswith("."):
        text += "0"
    return f"{text}%"


@dataclass
class QualityResult:
    """Outcome of the SARIF telemetry quality checks."""

    lines: list[str] = field(default_factory=list)
    failed: bool = False
    empty_ruleset: bool = False
    unchecked: list[str] = field(default_factory=list)
    unevaluated_runs: list[int] = field(default_factory=list)


@dataclass
class SarifReport:
    """Result of analysing a SARIF file."""

    lines: list[str] = field(default_factory=list)
    quality_failed: bool = False
    empty_ruleset: bool = False
    unchecked: list[str] = field(default_factory=list)
    unevaluated_runs: list[int] = field(default_factory=list)
    uri_violations: list[str] = field(default_factory=list)

    @property
    def telemetry_missing(self) -> bool:
        """Some gated quality checks could not be evaluated (metric absent)."""
        return bool(self.unchecked or self.unevaluated_runs)

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


def _metric_results(run: dict[str, Any]) -> list[dict[str, Any]]:
    results = run.get("properties", {}).get("metricResults", []) or []
    return results if isinstance(results, list) else []


def get_metric(
    runs: list[dict[str, Any]],
    rule_id: str,
    metric_name: str,
    *,
    combine: str = "min",
) -> float | None:
    """Aggregate a named metric over *runs*, or return ``None`` if absent.

    A multi-run SARIF must not be judged by the first run that happens to carry
    the metric. Percentages use ``combine="min"`` (the worst run decides), counts
    use ``combine="sum"`` (they add up).
    """
    values = [
        float(metric["value"])
        for run in runs
        for metric in _metric_results(run)
        if metric.get("ruleId") == rule_id
        and metric.get("message", {}).get("text", "") == metric_name
        and metric.get("value") is not None
    ]
    if not values:
        return None
    return min(values) if combine == "min" else sum(values)


def has_telemetry(runs: list[dict[str, Any]]) -> bool:
    """Whether the SARIF carries any ``cpp/telemetry`` metric at all."""
    return any(
        str(metric.get("ruleId", "")).startswith(_TELEMETRY_PREFIX)
        for run in runs
        for metric in _metric_results(run)
    )


def non_telemetry_rule_count(runs: list[dict[str, Any]]) -> int:
    """Number of rules that can produce findings (``cpp/telemetry/*`` excluded)."""
    return sum(
        1
        for run in runs
        for source in _rule_sources(run)
        for rule in source.get("rules", [])
        if not str(rule.get("id", "")).startswith(_TELEMETRY_PREFIX)
    )


def get_compiler_error_details(runs: list[dict[str, Any]]) -> list[str]:
    errors = []
    for run in runs:
        for metric in _metric_results(run):
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


def _quality_lines(runs: list[dict[str, Any]], verbose: bool) -> QualityResult:
    result = QualityResult()

    def check(label: str, ok: bool | None, detail: str = "") -> None:
        suffix = f" ({detail})" if detail else ""
        if ok is None:
            if verbose:
                result.lines.append(f"[quality check] SKIP: {label}{suffix}")
        elif ok:
            if verbose:
                result.lines.append(f"[quality check] PASS: {label}{suffix}")
        else:
            result.failed = True
            result.lines.append(f"[quality check] FAIL: {label}{suffix}")

    rule_count = non_telemetry_rule_count(runs)
    label = "Expect number of rules > 0, actual:"
    if rule_count > 0:
        check(label, True, f"{rule_count} rules")
    else:
        result.failed = True
        result.empty_ruleset = True
        result.lines.append(
            f"[quality check] FAIL: {label} (0 rules; no query produced results, "
            "check the selected reports and the resolved packs; cpp/telemetry/* "
            "metric rules do not count)"
        )

    telemetry = has_telemetry(runs)
    runs_without_telemetry = [
        index for index, run in enumerate(runs) if not has_telemetry([run])
    ]

    for rule_id, metric_name in _GATED_PERCENTAGES:
        pct = get_metric(runs, rule_id, metric_name)
        check(
            f"Expect: {metric_name.lower()} == 100%, actual:",
            None if pct is None else pct >= 100.0,
            "metric not found" if pct is None else format_percentage(pct),
        )

    counts: dict[str, float | None] = {}
    for rule_id, metric_name in _GATED_COUNTS:
        counts[metric_name] = get_metric(runs, rule_id, metric_name, combine="sum")
        count = counts[metric_name]
        check(
            f"Expect: {metric_name} == 0, actual:",
            None if count is None else int(count) == 0,
            "metric not found" if count is None else str(int(count)),
        )
        if count is None:
            result.unchecked.append(metric_name)

    compiler = counts["compiler errors"]
    if compiler is not None and int(compiler) > 0:
        result.lines.extend(
            f"[quality check]          {d}" for d in get_compiler_error_details(runs)
        )

    if telemetry:
        for rule_id, metric_name in _INFORMATIONAL_PERCENTAGES:
            pct = get_metric(runs, rule_id, metric_name)
            shown = "metric not found" if pct is None else format_percentage(pct)
            result.lines.append(
                f"[quality check] INFO: {metric_name.lower()}, actual: ({shown})"
            )

    if verbose:
        for rule_id, metric_name in _VERBOSE_PERCENTAGES:
            pct = get_metric(runs, rule_id, metric_name)
            shown = "metric not found" if pct is None else format_percentage(pct)
            result.lines.append(
                f"[quality check] INFO: {metric_name.lower()}, actual: ({shown})"
            )

    if telemetry:
        if runs_without_telemetry:
            result.unevaluated_runs = runs_without_telemetry
            labels = ", ".join(f"run {i}" for i in runs_without_telemetry)
            result.lines.append(
                f"[quality check] WARN: {labels} carry no cpp/telemetry metrics; "
                "their database quality was not evaluated"
            )
        if result.unchecked:
            result.lines.append(
                "[quality check] WARN: metric missing, not evaluated: "
                + ", ".join(result.unchecked)
            )
    else:
        result.unchecked = [name for _id, name in _GATED_PERCENTAGES] + [
            name for _id, name in _GATED_COUNTS
        ]
        result.lines.append(
            "[quality check] WARN: no cpp/telemetry metrics in this SARIF; "
            "database quality checks were not evaluated (do the selected reports "
            "contain @kind metric queries?)"
        )
    return result


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
    quality = _quality_lines(runs, verbose)
    report.quality_failed = quality.failed
    report.empty_ruleset = quality.empty_ruleset
    report.unchecked = list(quality.unchecked)
    report.unevaluated_runs = list(quality.unevaluated_runs)
    report.lines.extend(quality.lines)
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
        help="Return 0 when quality metrics fail (an empty ruleset, URI violations "
        "and invalid input still fail)",
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
    if report.empty_ruleset:
        return 1
    return 1 if report.quality_failed and not args.no_fail else 0


if __name__ == "__main__":
    raise SystemExit(main())
