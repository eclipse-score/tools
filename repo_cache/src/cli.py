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

"""Command-line entry point for SCORE Repository Cache."""

from __future__ import annotations

import argparse
import json
import sys
from collections.abc import Sequence
from pathlib import Path

from .cache import default_cache_directory
from .errors import RepoCacheError
from .github import list_repositories
from .sync import DEFAULT_SYNC_WORKERS, sync_org


def create_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="score-repo-cache",
        description=(
            "Maintain a local, disposable Git checkout of every repository "
            "in a GitHub organization."
        ),
    )
    commands = parser.add_subparsers(dest="command", required=True, metavar="COMMAND")

    list_command = commands.add_parser(
        "list", help="List repositories in an organization."
    )
    list_command.add_argument("--org", required=True, help="GitHub organization name.")
    list_command.add_argument(
        "--include-archived", action="store_true", help="Include archived repositories."
    )
    list_command.add_argument(
        "--json",
        action="store_true",
        help="Print machine-readable JSON instead of a table.",
    )

    sync_command = commands.add_parser(
        "sync", help="Synchronize cached checkouts for an organization."
    )
    sync_command.add_argument("--org", required=True, help="GitHub organization name.")
    sync_command.add_argument(
        "--repo",
        action="append",
        default=None,
        metavar="NAME",
        help="Exact repository name to include. Repeat to include more repositories.",
    )
    sync_command.add_argument(
        "--cache-dir",
        type=Path,
        default=None,
        help="Persistent directory for disposable repository checkouts "
        f"(default: {default_cache_directory()}).",
    )
    sync_command.add_argument(
        "--include-archived", action="store_true", help="Include archived repositories."
    )
    sync_command.add_argument(
        "--workers",
        type=int,
        default=None,
        help=f"Number of checkouts to synchronize concurrently (default: {DEFAULT_SYNC_WORKERS}).",
    )
    sync_command.add_argument(
        "--quiet",
        action="store_true",
        help="Suppress progress messages on standard error.",
    )
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    parser = create_parser()
    args = parser.parse_args(argv)
    try:
        if args.command == "list":
            return _run_list(args)
        return _run_sync(args)
    except RepoCacheError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2


def _run_list(args: argparse.Namespace) -> int:
    repositories = list_repositories(org=args.org)
    if not args.include_archived:
        repositories = tuple(
            repository for repository in repositories if not repository.archived
        )
    if args.json:
        print(
            json.dumps(
                [
                    {
                        "name": repository.name,
                        "default_branch": repository.default_branch,
                        "archived": repository.archived,
                    }
                    for repository in repositories
                ],
                indent=2,
            )
        )
    else:
        for repository in repositories:
            marker = " (archived)" if repository.archived else ""
            print(f"{repository.name}\t{repository.default_branch or '-'}{marker}")
    return 0


def _run_sync(args: argparse.Namespace) -> int:
    cache_dir = args.cache_dir or default_cache_directory()
    workers = args.workers or DEFAULT_SYNC_WORKERS
    report = sync_org(
        org=args.org,
        cache_dir=cache_dir,
        repos=tuple(args.repo or ()),
        include_archived=args.include_archived,
        workers=workers,
        progress=None if args.quiet else _write_progress,
    )
    for outcome in report.failures:
        print(f"error: {outcome.repository.name}: {outcome.error}", file=sys.stderr)
    synced = len(report.outcomes) - len(report.failures)
    print(
        f"Synchronized {synced}/{len(report.outcomes)} checkout(s) at {cache_dir / args.org}"
    )
    return 2 if report.failures else 0


def _write_progress(message: str) -> None:
    print(message, file=sys.stderr, flush=True)


if __name__ == "__main__":  # pragma: no cover - exercised via the console script
    raise SystemExit(main())
