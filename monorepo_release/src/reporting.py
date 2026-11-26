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

"""Format module release reports and notes."""

import re
from urllib.parse import quote

from .releases import Commit, ModuleReport

PR_REFERENCE = re.compile(r"\(#([1-9][0-9]*)\)$")


def markdown_text(value: str) -> str:
    return re.sub(r"([\\`*_[\]<>!~|@])", r"\\\1", value)


def commit_list(
    commits: list[Commit], repository_url: str, *, link_commits: bool
) -> str:
    lines: list[str] = []
    for commit in commits:
        subject = markdown_text(commit.subject)
        if match := PR_REFERENCE.search(commit.subject):
            number = match[1]
            subject = markdown_text(commit.subject[: match.start()])
            subject += f"([#{number}]({repository_url}/pull/{number}))"
        if link_commits:
            subject = (
                f"[{commit.sha[:7]}]({repository_url}/commit/{commit.sha}) {subject}"
            )
        lines.append(f"- {subject}")
    return "\n".join(lines) + "\n"


def status_table(reports: list[ModuleReport]) -> list[tuple[str, str, str, str]]:
    return [("Module", "Latest release", "Comparison base", "Status")] + [
        (
            report.module,
            report.previous_tag or "—",
            report.comparison_tag or "—",
            report.status,
        )
        for report in reports
    ]


def status_summary(
    reports: list[ModuleReport],
    repository_url: str,
    head: str,
    warning: str,
) -> str:
    lines = [
        "## Monorepo release",
        "",
        f"Commits through [{head[:7]}]({repository_url}/commit/{head}).",
        "",
        "| Module | Latest release | Comparison base | Status |",
        "| --- | --- | --- | --- |",
    ]
    for report in reports:
        release = "—"
        if tag := report.previous_tag:
            release = f"[{markdown_text(tag)}]({repository_url}/releases/tag/{quote(tag, safe='')})"
        comparison = markdown_text(report.comparison_tag or "—")
        lines.append(
            f"| {markdown_text(report.module)} | {release} | {comparison} | {report.status} |"
        )
    if warning:
        lines.extend(["", warning])
    summary = "\n".join(lines) + "\n"
    for report in reports:
        if not report.commits:
            continue
        section = f"\n### {markdown_text(report.module)}\n\n" + commit_list(
            report.commits, repository_url, link_commits=True
        )
        # Keep the Actions summary below its 1 MiB limit. The step log has the full report.
        if len((summary + section).encode()) > 900_000:
            return summary + "\nAdditional commits are available in the step log.\n"
        summary += section
    return summary
