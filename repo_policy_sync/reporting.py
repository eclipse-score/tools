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

"""Render stable human- and machine-readable policy run reports."""

from __future__ import annotations

import json
import shutil
import unicodedata
from collections import Counter

from .models import Change
from .runner import RepositoryOutcome, RunReport, RunSummary


def render_table(report: RunReport) -> str:
    """Render a concise table suitable for interactive terminal use."""

    rows = [
        (
            outcome.policy_id,
            outcome.repository,
            _status_label(outcome.status),
            _actions_label(outcome),
        )
        for outcome in report.outcomes
    ]
    if not rows:
        rows.append(("—", "—", "—", "No policy evaluations."))

    lines = [
        "📋 Policy evaluations",
        _render_box_table(
            ("Policy", "Repository", "Status", "Actions"),
            rows,
            column_limits=(24, 24, 32, 48),
        ),
    ]
    lines.extend(_summary_lines(report))
    return "\n".join(lines)


def render_json(report: RunReport) -> str:
    """Render the complete report as a versioned JSON document."""

    summary = report.summary
    return json.dumps(
        {
            "schema_version": 2,
            "summary": {
                "repositories": summary.repositories,
                "synchronized": summary.synchronized,
                "sync_failures": summary.sync_failures,
                "skipped": summary.skipped,
                "evaluations": summary.evaluations,
                "compliant": summary.compliant,
                "drifted": summary.drifted,
                "not_applicable": summary.not_applicable,
                "evaluation_failures": summary.evaluation_failures,
                "pull_requests_created": summary.pull_requests_created,
                "pull_requests_updated": summary.pull_requests_updated,
                "pull_requests_open": summary.pull_requests_open,
                "pull_requests_recreated": summary.pull_requests_recreated,
                "pull_requests_closed": summary.pull_requests_closed,
                "duration_seconds": summary.duration_seconds,
            },
            "outcomes": [
                {
                    "policy_id": outcome.policy_id,
                    "repository": outcome.repository,
                    "applicable": outcome.when,
                    "status": outcome.status,
                    "changes": [_change_to_json(change) for change in outcome.changes],
                    "pull_request_url": outcome.pull_request_url,
                    "policy_pr_status": outcome.policy_pr_status,
                    "warnings": list(outcome.warnings),
                    "error": outcome.error,
                }
                for outcome in report.outcomes
            ],
        },
        indent=2,
        sort_keys=True,
    )


def render_markdown(report: RunReport) -> str:
    """Render a compact repository-by-policy matrix for Markdown consumers."""

    compliance_counts = Counter(
        _markdown_compliance_status(outcome) for outcome in report.outcomes
    )
    pull_request_counts = Counter(
        outcome.policy_pr_status or "not checked" for outcome in report.outcomes
    )
    lines = [
        "# Repository policy compliance",
        "",
        "## Summary",
        "",
        f"`{report.summary.repositories}` repositories · "
        f"`{report.summary.evaluations}` evaluations · "
        f"`{report.summary.duration_seconds:.1f}s`",
        "",
        f"- ✅ Compliant: `{compliance_counts['yes']}`",
        f"- ❌ Changes needed: `{compliance_counts['no']}`",
        f"- N/A Not applicable: `{compliance_counts['not applicable']}`",
        f"- ⏭️ Not evaluated: `{compliance_counts['not evaluated']}`",
        f"- ⚠️ Errors: `{compliance_counts['error']}`",
        "",
        "- Pull requests: "
        f"`🔄 {pull_request_counts['open']} open` · "
        f"`🔗 {pull_request_counts['merged']} merged` · "
        f"`✅ {pull_request_counts['closed']} closed` · "
        f"`— {pull_request_counts['none']} none` · "
        f"`? {pull_request_counts['not checked']} not checked`",
        "",
        "## Compliance matrix",
        "",
    ]
    lines.extend(_markdown_matrix(report.outcomes))
    lines.extend(
        [
            "",
            "Legend: ✅ compliant · ❌ changes needed · N/A not applicable · "
            "⏭️ not evaluated · ⚠️ error · GitHub badge `Open`/`Merged`/`Closed` = PR state",
        ]
    )
    lines.extend(_markdown_details_section(report.outcomes))
    return "\n".join(lines)


def _change_to_json(change: Change) -> dict[str, str | None]:
    return {
        "path": str(change.path),
        "description": change.description,
        "rationale": change.rationale,
    }


def _markdown_compliance_status(outcome: RepositoryOutcome) -> str:
    if outcome.status in {"compliant", "pull-request-closed"}:
        return "yes"
    if outcome.status == "not-applicable":
        return "not applicable"
    if outcome.status in {"skipped", "sync-error"}:
        return "not evaluated"
    if outcome.status == "error":
        return "error"
    return "no"


def _markdown_matrix(outcomes: tuple[RepositoryOutcome, ...]) -> list[str]:
    """Render one row per repository and one column per policy."""

    repositories = tuple(dict.fromkeys(outcome.repository for outcome in outcomes))
    policies = tuple(dict.fromkeys(outcome.policy_id for outcome in outcomes))
    if not repositories or not policies:
        return ["_No repository/policy evaluations._"]

    by_pair = {(outcome.repository, outcome.policy_id): outcome for outcome in outcomes}
    lines = [
        "| Repository | "
        + " | ".join(_markdown_cell(policy) for policy in policies)
        + " |",
        "| --- | " + " | ".join("---" for _ in policies) + " |",
    ]
    for repository in repositories:
        cells = [
            _markdown_matrix_cell(by_pair.get((repository, policy)))
            for policy in policies
        ]
        lines.append("| " + " | ".join((_markdown_cell(repository), *cells)) + " |")
    return lines


def _markdown_matrix_cell(outcome: RepositoryOutcome | None) -> str:
    if outcome is None:
        return "N/A"

    compliance = _markdown_compliance_status(outcome)
    if compliance == "yes":
        status = "✅"
    elif compliance == "no":
        status = "❌"
    elif compliance == "not applicable":
        status = "N/A"
    elif compliance == "not evaluated":
        status = "⏭️"
    else:
        status = "⚠️"

    parts = [status]
    if outcome.policy_pr_status in {"open", "merged", "closed"}:
        pr_label = {
            "open": "🔄 open PR",
            "merged": "🔗 merged PR",
            "closed": "✅ closed PR",
        }[outcome.policy_pr_status]
        if outcome.pull_request_url:
            parts.append(
                _markdown_pr_badge(outcome.policy_pr_status, outcome.pull_request_url)
            )
        else:
            parts.append(pr_label)
    return _markdown_cell(" ".join(parts))


def _markdown_pr_badge(status: str, url: str) -> str:
    """Render a GitHub-logo status badge linking to the policy pull request."""

    label, color = {
        "open": ("Open", "2ea043"),
        "merged": ("Merged", "8250df"),
        "closed": ("Closed", "6e7781"),
    }[status]
    badge_url = (
        f"https://img.shields.io/badge/-{label}-{color}"
        "?style=flat&logo=github&logoColor=white"
    )
    return f"[![{label} PR]({badge_url})]({_markdown_url(url)})"


def _markdown_details_section(outcomes: tuple[RepositoryOutcome, ...]) -> list[str]:
    detailed = tuple(
        outcome for outcome in outcomes if _markdown_details(outcome) != "—"
    )
    if not detailed:
        return []

    lines = [
        "",
        "<details>",
        f"<summary>Details ({len(detailed)})</summary>",
        "",
    ]
    for outcome in detailed:
        lines.append(
            f"- `{_markdown_cell(outcome.repository)}` / "
            f"`{_markdown_cell(outcome.policy_id)}`: "
            f"{_markdown_cell(_markdown_details(outcome))}"
        )
    lines.extend(["", "</details>"])
    return lines


def _markdown_details(outcome: RepositoryOutcome) -> str:
    if outcome.error:
        return outcome.error
    if outcome.status == "skipped":
        return "No default branch; evaluation skipped."
    details = _format_changes(outcome.changes)
    if outcome.warnings:
        details = "; ".join(part for part in (details, *outcome.warnings) if part)
    return details or "—"


def _markdown_url(value: str) -> str:
    return value.replace("(", "%28").replace(")", "%29").replace("\n", "")


def _markdown_cell(value: str) -> str:
    return value.replace("|", "\\|").replace("\n", " ")


def _summary_lines(report: RunReport) -> list[str]:
    summary = report.summary
    evaluation_rows = (
        ("Repositories", f"{summary.repositories} selected", ""),
        ("  ✅ synchronized", str(summary.synchronized), ""),
        ("  ⚠ sync failed", str(summary.sync_failures), ""),
        ("  ⏭ skipped (no default branch)", str(summary.skipped), ""),
        ("Policy evaluations", str(summary.evaluations), ""),
        _summary_row("  ✅", summary.compliant, summary.evaluations),
        _summary_row(
            f"  {_changes_required_marker()}", summary.drifted, summary.evaluations
        ),
        _summary_row("  ⚪", summary.not_applicable, summary.evaluations),
        _summary_row("  ⚠ failed", summary.evaluation_failures, summary.evaluations),
    )
    lines = [
        "",
        f"📊 Summary · {summary.duration_seconds:.1f}s",
        _render_box_table(
            ("Area", "Count", "Share"),
            evaluation_rows,
            column_limits=(36, 18, 12),
        ),
    ]
    if _has_pull_request_activity(summary):
        lines.extend(
            [
                "",
                "🔀 Pull requests",
                _render_box_table(
                    ("State", "Count"),
                    (
                        ("🆕 created", str(summary.pull_requests_created)),
                        ("✏ updated", str(summary.pull_requests_updated)),
                        ("🔄 already open", str(summary.pull_requests_open)),
                        ("♻ recreated", str(summary.pull_requests_recreated)),
                        ("✅ closed", str(summary.pull_requests_closed)),
                    ),
                    column_limits=(28, 18),
                ),
            ]
        )
    sync_failures = _unique_sync_failures(report.outcomes)
    lines.extend(_failure_table("⚠ Sync failure causes", sync_failures))
    evaluation_failures = tuple(
        outcome for outcome in report.outcomes if outcome.status == "error"
    )
    lines.extend(
        _failure_table("⚠ Policy evaluation failure causes", evaluation_failures)
    )
    return lines


def _summary_row(label: str, count: int, total: int) -> tuple[str, str, str]:
    percentage = f"{count / total:.1%}" if total else "—"
    return label, str(count), percentage


def _one_line(value: str) -> str:
    return value.replace("\n", " ")


def _has_pull_request_activity(summary: RunSummary) -> bool:
    return any(
        (
            summary.pull_requests_created,
            summary.pull_requests_updated,
            summary.pull_requests_open,
            summary.pull_requests_recreated,
            summary.pull_requests_closed,
        )
    )


def _unique_sync_failures(
    outcomes: tuple[RepositoryOutcome, ...],
) -> tuple[RepositoryOutcome, ...]:
    unique: dict[tuple[str, str], RepositoryOutcome] = {}
    for outcome in outcomes:
        if outcome.status == "sync-error":
            unique.setdefault(
                (outcome.repository, outcome.error or "unknown error"), outcome
            )
    return tuple(unique.values())


def _failure_table(title: str, failures: tuple[RepositoryOutcome, ...]) -> list[str]:
    if not failures:
        return []
    lines = ["", title]
    grouped = Counter(
        _one_line(outcome.error or "unknown error") for outcome in failures
    )
    rows = []
    for error, count in grouped.most_common(5):
        affected = [
            f"{outcome.policy_id}/{outcome.repository}"
            for outcome in failures
            if _one_line(outcome.error or "unknown error") == error
        ]
        examples = ", ".join(affected[:3])
        remaining = f", and {len(affected) - 3} more" if len(affected) > 3 else ""
        rows.append((str(count), error, f"{examples}{remaining}"))
    if len(grouped) > 5:
        rows.append(("—", f"{len(grouped) - 5} more distinct failure cause(s)", ""))
    lines.append(
        _render_box_table(
            ("Count", "Cause", "Affected evaluations"),
            rows,
            column_limits=(10, 52, 44),
        )
    )
    return lines


def _status_label(status: str) -> str:
    return {
        "compliant": "✅",
        "changes-required": _changes_required_marker(),
        "not-applicable": "⚪",
        "sync-error": "⚠ sync failed",
        "error": "⚠ evaluation failed",
        "pull-request-created": "🆕 pull request created",
        "pull-request-updated": "✏ pull request updated",
        "pull-request-open": "🔄 pull request open",
        "pull-request-recreated": "♻ pull request recreated",
        "pull-request-recreated-no-changes": "♻ pull request recreated (no changes)",
        "pull-request-closed": "✅ pull request closed",
    }.get(status, status)


def _changes_required_marker() -> str:
    return "🔴"


def _actions_label(outcome: RepositoryOutcome) -> str:
    if outcome.error:
        return outcome.error
    actions = _format_changes(outcome.changes)
    if outcome.pull_request_url:
        actions = f"{actions}; PR: {outcome.pull_request_url}"
    if outcome.warnings:
        actions = f"{actions}; {'; '.join(outcome.warnings)}"
    return actions or "-"


def _format_changes(changes: tuple[Change, ...]) -> str:
    by_path: dict[str, list[str]] = {}
    for change in changes:
        by_path.setdefault(str(change.path), []).append(change.description)
    return "; ".join(
        f"{path}: {', '.join(descriptions)}" for path, descriptions in by_path.items()
    )


def _render_box_table(
    headers: tuple[str, ...],
    rows: tuple[tuple[str, ...], ...] | list[tuple[str, ...]],
    *,
    column_limits: tuple[int, ...],
) -> str:
    """Render a Unicode box table with wrapped cells and aligned columns."""

    if len(headers) != len(column_limits):
        raise ValueError("headers and column_limits must have the same length")
    column_count = len(headers)
    normalized_rows = [tuple(_one_line(value) for value in row) for row in rows]
    if any(len(row) != column_count for row in normalized_rows):
        raise ValueError("every row must have one value per header")

    terminal_width = shutil.get_terminal_size(fallback=(120, 24)).columns
    available_width = max(1, terminal_width - (3 * column_count + 1))
    minimum_widths = tuple(
        min(limit, max(8, _display_width(header)))
        for header, limit in zip(headers, column_limits, strict=True)
    )
    widths = [
        min(
            limit,
            max(
                _display_width(header),
                *(
                    max(
                        (_display_width(line) for line in value.splitlines()), default=0
                    )
                    for value in (row[index] for row in normalized_rows)
                ),
            ),
        )
        for index, (header, limit) in enumerate(
            zip(headers, column_limits, strict=True)
        )
    ]
    while sum(widths) > available_width:
        shrinkable = [
            index for index, width in enumerate(widths) if width > minimum_widths[index]
        ]
        if not shrinkable:
            break
        index = max(shrinkable, key=lambda item: widths[item] - minimum_widths[item])
        widths[index] -= 1

    # ``widths`` describes the text area. The two spaces added around every
    # cell in _wrap_row are part of the rendered table as well.
    border_widths = [width + 2 for width in widths]
    top = "┌" + "┬".join("─" * width for width in border_widths) + "┐"
    separator = "├" + "┼".join("─" * width for width in border_widths) + "┤"
    bottom = "└" + "┴".join("─" * width for width in border_widths) + "┘"
    rendered_rows = [_wrap_row(headers, widths)]
    rendered_rows.extend(_wrap_row(row, widths) for row in normalized_rows)
    lines = [top]
    for row_index, row_lines in enumerate(rendered_rows):
        if row_index:
            lines.append(separator)
        lines.extend(row_lines)
    lines.append(bottom)
    return "\n".join(lines)


def _wrap_row(
    values: tuple[str, ...], widths: list[int] | tuple[int, ...]
) -> list[str]:
    wrapped = [
        _wrap_cell(value, width) for value, width in zip(values, widths, strict=True)
    ]
    lines = []
    for line_index in (
        range(max(len(lines) for lines in wrapped)) if wrapped else range(0)
    ):
        cells = [
            _pad_display_width(
                cell_lines[line_index] if line_index < len(cell_lines) else "", width
            )
            for cell_lines, width in zip(wrapped, widths, strict=True)
        ]
        lines.append("│ " + " │ ".join(cells) + " │")
    return lines


def _wrap_cell(value: str, width: int) -> list[str]:
    """Wrap a cell without exceeding its display width."""

    if width < 1:
        return [""]
    lines: list[str] = []
    for raw_line in value.splitlines() or [""]:
        remaining = raw_line.strip()
        if not remaining:
            lines.append("")
            continue
        while _display_width(remaining) > width:
            split_at = _last_space_within(remaining, width)
            if split_at <= 0:
                split_at = _fit_prefix(remaining, width)
            lines.append(remaining[:split_at].rstrip())
            remaining = remaining[split_at:].lstrip()
        lines.append(remaining)
    return lines or [""]


def _last_space_within(value: str, width: int) -> int:
    position = 0
    last_space = -1
    for index, character in enumerate(value):
        character_width = _display_width(character)
        if position + character_width > width:
            break
        position += character_width
        if character.isspace():
            last_space = index
    return last_space


def _fit_prefix(value: str, width: int) -> int:
    position = 0
    for index, character in enumerate(value):
        character_width = _display_width(character)
        if position + character_width > width:
            return max(1, index)
        position += character_width
    return len(value)


def _pad_display_width(value: str, width: int) -> str:
    return value + " " * max(0, width - _display_width(value))


def _display_width(value: str) -> int:
    """Return a terminal-oriented width for a Unicode string."""

    width = 0
    for index, character in enumerate(value):
        if unicodedata.combining(character) or unicodedata.category(character) in {
            "Cf",
            "Mn",
        }:
            continue
        if unicodedata.east_asian_width(character) in {"W", "F"}:
            width += 2
        else:
            width += 1
    return width
