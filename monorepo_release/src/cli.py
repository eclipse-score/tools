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

"""Command-line entry point for Monorepo Release."""

import argparse
import os
import subprocess
import sys
from collections.abc import Sequence
from pathlib import Path
from typing import cast
from urllib.parse import quote

from .github import draft_release
from .releases import component_report, git, reachable_tags, read_components
from .reporting import status_summary, status_table


def show_status(root: Path) -> None:
    head = git(root, "rev-parse", "--verify", "HEAD").strip()
    components = read_components(root, head)
    tags = reachable_tags(root, head)
    reports = [
        component_report(root, head, tags, component, paths)
        for component, paths in components.items()
    ]
    rows = status_table(reports)
    component_width = max(len(component) for component, _, _ in rows)
    tag_width = max(len(tag) for _, tag, _ in rows)
    for component, tag, status in rows:
        print(f"{component:<{component_width}}  {tag:<{tag_width}}  {status}")

    print(f"\nCommits through {head[:7]}.")
    warning = ""
    if git(root, "status", "--porcelain"):
        warning = "Warning: uncommitted and untracked changes are excluded from the comparison."
        print(warning)
    for report in reports:
        if report.commits:
            print(f"\n{report.component}")
            for commit in report.commits:
                print(f"  {commit.sha[:7]} {commit.subject}")

    summary = os.environ.get("GITHUB_STEP_SUMMARY")
    repository = os.environ.get("GITHUB_REPOSITORY")
    if summary and repository:
        server = os.environ.get("GITHUB_SERVER_URL", "https://github.com").rstrip("/")
        repository_url = f"{server}/{quote(repository, safe='/')}"
        with Path(summary).open("a", encoding="utf-8") as file:
            _ = file.write(status_summary(reports, repository_url, head, warning))


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="monorepo-release")
    commands = parser.add_subparsers(dest="command", required=True)
    _ = commands.add_parser("status", help="Show unreleased changes by component.")
    draft = commands.add_parser("draft", help="Create a GitHub release draft.")
    _ = draft.add_argument("component", help="Component name used in release tags.")
    args = parser.parse_args(argv)

    try:
        root = Path(git(Path.cwd(), "rev-parse", "--show-toplevel").strip())
        if args.command == "status":
            show_status(root)
        else:
            print(draft_release(root, cast(str, args.component)))
    except subprocess.CalledProcessError as error:
        detail = cast(str, error.stderr).strip()
        print(f"monorepo-release: {detail or error}", file=sys.stderr)
        return 1
    except (OSError, ValueError) as error:
        print(f"monorepo-release: {error}", file=sys.stderr)
        return 1
    return 0
