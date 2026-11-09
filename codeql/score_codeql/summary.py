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

"""Markdown summary (``summary.md``) of an analysis run, e.g. for ``$GITHUB_STEP_SUMMARY``."""

from __future__ import annotations

import argparse
import json
import logging
from collections import Counter
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from score_codeql.errors import ScaError
from score_codeql.logging_utils import setup_logging

SEVERITIES = ("error", "warning", "note")
TOP_N = 10


@dataclass
class SarifCounts:
    """Result statistics of one SARIF file."""

    by_severity: Counter[str] = field(default_factory=Counter)
    suppressed: int = 0
    by_rule: Counter[str] = field(default_factory=Counter)
    by_file: Counter[str] = field(default_factory=Counter)

    @property
    def total(self) -> int:
        return sum(self.by_severity.values())


def _rule_levels(run: Mapping[str, Any]) -> dict[str, str]:
    tool = run.get("tool", {})
    levels: dict[str, str] = {}
    for source in [tool.get("driver", {}), *tool.get("extensions", [])]:
        for rule in source.get("rules", []):
            level = rule.get("defaultConfiguration", {}).get("level")
            if level and rule.get("id"):
                levels[rule["id"]] = level
    return levels


def count_results(sarif: Mapping[str, Any]) -> SarifCounts:
    """Count results per severity, rule and file; suppressed results are separate."""
    counts = SarifCounts()
    for run in sarif.get("runs", []):
        levels = _rule_levels(run)
        for result in run.get("results", []):
            if result.get("suppressions"):
                counts.suppressed += 1
                continue
            rule_id = (
                result.get("ruleId") or result.get("rule", {}).get("id") or "<unknown>"
            )
            level = result.get("level") or levels.get(rule_id) or "warning"
            counts.by_severity[level if level in SEVERITIES else "note"] += 1
            counts.by_rule[rule_id] += 1
            locations = result.get("locations") or [{}]
            uri = (
                locations[0]
                .get("physicalLocation", {})
                .get("artifactLocation", {})
                .get("uri")
            )
            if uri:
                counts.by_file[uri] += 1
    return counts


def load_counts(path: Path) -> SarifCounts:
    try:
        with path.open("r", encoding="utf-8") as fh:
            return count_results(json.load(fh))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ScaError(f"cannot read SARIF {path}: {exc}") from exc


def _esc(text: object) -> str:
    return str(text).replace("|", "\\|").replace("\n", " ")


def _table(header: Sequence[str], rows: Sequence[Sequence[object]]) -> list[str]:
    lines = [
        "| " + " | ".join(header) + " |",
        "|" + "|".join("---" for _ in header) + "|",
    ]
    lines += ["| " + " | ".join(_esc(c) for c in row) + " |" for row in rows]
    return lines


def compiler_section(compilers: Mapping[str, Any] | None) -> list[str]:
    """The "Compiler" table of ``compilers.json``."""
    lines = ["## Compiler", ""]
    if not compilers:
        return [*lines, "_No compiler information available._", ""]
    rows = []
    for item in compilers.get("compilers", []):
        wrapper = {True: "via wrapper", False: "direct", None: "n/a"}[
            item.get("via_wrapper")
        ]
        extracted = item.get("extracted")
        rows.append(
            (
                item.get("basename", "?"),
                item.get("version") or "unknown",
                item.get("linkage", "unknown"),
                wrapper,
                f"{item.get('commands', 0)} commands",
                "n/a" if extracted is None else f"{extracted} extracted",
            )
        )
    lines += _table(
        ["Compiler", "Version", "Linkage", "Replay", "Commands", "Extracted"], rows
    )
    lines.append("")
    status = compilers.get("status", "unknown")
    lines.append(f"Compiler gate: **{status}**")
    for error in compilers.get("errors", []):
        lines.append(f"- {_esc(error)}")
    lines.append("")
    return lines


def extraction_section(stats: Mapping[str, Any] | None) -> list[str]:
    lines = ["## Extraction", ""]
    if not stats:
        return [*lines, "_No extraction statistics available._", ""]
    lines += _table(
        ["Metric", "Value"],
        [
            ("Command logs analyzed", stats.get("total_logs", 0)),
            (
                "Commands failed",
                f"{stats.get('failed_logs', 0)} ({stats.get('failed_pct', 0.0):.1f}%)",
            ),
            (
                "Commands with issues",
                f"{stats.get('logs_with_errors', 0)} ({stats.get('issues_pct', 0.0):.1f}%)",
            ),
            ("Unique parse errors", stats.get("unique_parse", 0)),
            ("Status", "PASS" if stats.get("passed", True) else "FAIL"),
        ],
    )
    lines.append("")
    return lines


def findings_section(
    per_report: Mapping[str, SarifCounts],
    combined: SarifCounts | None,
    dropped_external: int = 0,
) -> list[str]:
    lines = ["## Findings", ""]
    rows = [
        (name, *(c.by_severity[s] for s in SEVERITIES), c.suppressed, c.total)
        for name, c in per_report.items()
    ]
    if combined is not None:
        rows.append(
            (
                "**combined (Code Scanning)**",
                *(combined.by_severity[s] for s in SEVERITIES),
                combined.suppressed,
                combined.total,
            )
        )
    lines += _table(["Report", "Error", "Warning", "Note", "Suppressed", "Total"], rows)
    lines.append("")
    if dropped_external:
        lines += [
            f"`{dropped_external}` result(s) in code outside the workspace (Bazel "
            "external repositories, toolchain and system headers) are not uploaded to "
            "Code Scanning; they remain in the per-report SARIF/CSV files.",
            "",
        ]
    focus = combined if combined is not None else next(iter(per_report.values()), None)
    if focus is not None and focus.total:
        lines += ["### Top rules", ""]
        lines += _table(["Rule", "Count"], focus.by_rule.most_common(TOP_N))
        lines += ["", "### Top files", ""]
        lines += _table(["File", "Count"], focus.by_file.most_common(TOP_N))
        lines.append("")
    return lines


def pins_section(pins: Mapping[str, Any] | None) -> list[str]:
    lines = ["## Pinned versions", ""]
    if not pins:
        return [*lines, "_No pin information available._", ""]
    rows = [
        ("CodeQL CLI", pins.get("codeql_cli_actual") or pins.get("codeql_cli", "?"))
    ]
    resolved = pins.get("resolved")
    if resolved:
        rows += [(item["pack"], item.get("origin", "")) for item in resolved]
    else:
        rows += [
            (f"{n}@{v}", "")
            for n, v in {**pins.get("packs", {}), **pins.get("local_packs", {})}.items()
        ]
    lines += _table(["Component", "Version / origin"], rows)
    lines.append("")
    return lines


def build_summary(
    *,
    per_report: Mapping[str, SarifCounts],
    combined: SarifCounts | None,
    compilers: Mapping[str, Any] | None,
    extraction: Mapping[str, Any] | None,
    pins: Mapping[str, Any] | None,
    dropped_external: int = 0,
    title: str = "CodeQL static analysis",
) -> str:
    """Compose the markdown document; the compiler table comes first."""
    lines = [f"# {title}", ""]
    lines += compiler_section(compilers)
    lines += extraction_section(extraction)
    lines += findings_section(per_report, combined, dropped_external)
    lines += pins_section(pins)
    return "\n".join(lines).rstrip() + "\n"


def _load_json(path: Path | None) -> Any:
    if path is None or not path.is_file():
        return None
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        logging.warning("Cannot read %s: %s", path, exc)
        return None


def summarize_directory(
    results_dir: Path,
    *,
    report_names: Sequence[str],
    combined_name: str = "code-scanning.sarif",
    compilers: Path | None = None,
    pins: Path | None = None,
    extraction: Mapping[str, Any] | None = None,
    dropped_external: int = 0,
) -> str:
    """Build the summary from the files in *results_dir*."""
    per_report = {
        name: load_counts(results_dir / f"{name}.sarif")
        for name in report_names
        if (results_dir / f"{name}.sarif").is_file()
    }
    combined_path = results_dir / combined_name
    combined = load_counts(combined_path) if combined_path.is_file() else None
    return build_summary(
        per_report=per_report,
        combined=combined,
        compilers=_load_json(compilers),
        extraction=extraction,
        pins=_load_json(pins),
        dropped_external=dropped_external,
    )


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Write summary.md for a results directory."
    )
    parser.add_argument("results_dir", type=Path)
    parser.add_argument(
        "--output", type=Path, default=None, help="default: <results_dir>/summary.md"
    )
    parser.add_argument("--compilers", type=Path, default=None, help="compilers.json")
    parser.add_argument("--pins", type=Path, default=None, help="pins.json")
    args = parser.parse_args(argv)
    setup_logging()
    names = sorted(
        p.stem
        for p in args.results_dir.glob("*.sarif")
        if p.name != "code-scanning.sarif"
    )
    try:
        text = summarize_directory(
            args.results_dir,
            report_names=names,
            compilers=args.compilers or args.results_dir / "compilers.json",
            pins=args.pins or args.results_dir / "pins.json",
        )
    except ScaError as exc:
        logging.error("%s", exc)
        return exc.exit_code
    (args.output or args.results_dir / "summary.md").write_text(text, encoding="utf-8")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
