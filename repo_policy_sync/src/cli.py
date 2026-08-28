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

"""Command-line entry point for SCORE Repository Policy Sync."""

from __future__ import annotations

import argparse
import sys
from collections.abc import Sequence
from pathlib import Path

from repo_cache import default_cache_directory

from .config import load_config
from .errors import PolicyError, RepoPolicySyncError
from .github import GitHubCli
from .policy import (
    BUNDLED_POLICY_DIRECTORY,
    DEFAULT_POLICY_DIRECTORY,
    discover_policy_paths,
    load_policies,
    resolve_policy_names,
)
from .reporting import render_json, render_markdown, render_table
from .runner import DEFAULT_POLICY_WORKERS, DEFAULT_SYNC_WORKERS, run_policies


def create_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="score-repo-policy-sync",
        description="Evaluate and remediate repository policies across a GitHub organization.",
        epilog=(
            "All policy options except command-specific output options may also be "
            "set in the TOML configuration. "
            "Explicit command-line values override configuration values."
        ),
    )
    commands = parser.add_subparsers(dest="command", required=True, metavar="COMMAND")
    plan = commands.add_parser(
        "plan", help="Evaluate policies without changing repositories."
    )
    _add_common_arguments(plan, reports=True)
    apply = commands.add_parser(
        "apply", help="Apply policies and manage pull requests."
    )
    _add_common_arguments(apply, reports=True)
    apply.add_argument(
        "--recreate",
        action=argparse.BooleanOptionalAction,
        default=None,
        help="Recreate one existing policy-owned pull request from the current default branch.",
    )
    apply.add_argument(
        "--allow-dirty-pr",
        action=argparse.BooleanOptionalAction,
        default=None,
        help=(
            "After the automatic formatting-fix retry, create a draft pull request "
            "when pre-commit still fails and comment with the failure."
        ),
    )
    return parser


def _add_common_arguments(
    parser: argparse.ArgumentParser, *, reports: bool = False
) -> None:
    typical = parser.add_argument_group("Typical")
    rare = parser.add_argument_group("Rare")
    debugging = parser.add_argument_group("Debugging only")
    typical.add_argument(
        "--org",
        help="GitHub organization name (also available in the TOML configuration).",
    )
    typical.add_argument(
        "--policy",
        action="append",
        metavar="NAME",
        help="Select one local or bundled policy by name. Repeat to select policies; defaults to all available policies.",
    )
    typical.add_argument(
        "--repo",
        action="append",
        default=None,
        help="Exact repository name to include. Repeat to include more repositories.",
    )
    rare.add_argument(
        "--config",
        type=Path,
        help="TOML configuration file (default: score-repo-policy-sync.toml if present).",
    )
    if reports:
        rare.add_argument(
            "--json-output",
            type=Path,
            metavar="PATH",
            help="Also write the JSON report to PATH.",
        )
        rare.add_argument(
            "--markdown-output",
            type=Path,
            metavar="PATH",
            help="Also write the Markdown report to PATH.",
        )
    rare.add_argument(
        "--policy-dir",
        dest="policy_dir",
        type=Path,
        action="append",
        help="Local policy directory; repeat to combine directories (default: ./policies if present).",
    )
    rare.add_argument(
        "--exclude-policy",
        action="append",
        metavar="NAME",
        help="Exclude one local or bundled policy by name. Repeat to exclude policies.",
    )
    rare.add_argument(
        "--quiet",
        action=argparse.BooleanOptionalAction,
        default=None,
        help="Suppress progress messages on standard error.",
    )
    debugging.add_argument(
        "--cache-dir",
        type=Path,
        default=None,
        help="Persistent directory for disposable repository checkouts.",
    )
    debugging.add_argument(
        "--sync-workers",
        type=int,
        default=None,
        help=(
            "Number of repository checkouts to synchronize concurrently "
            f"(default: {DEFAULT_SYNC_WORKERS}, the available CPU count)."
        ),
    )
    debugging.add_argument(
        "--policy-workers",
        type=int,
        default=None,
        help=(
            "Number of repositories to evaluate or apply per policy concurrently "
            f"(default: {DEFAULT_POLICY_WORKERS}, the available CPU count)."
        ),
    )


def main(argv: Sequence[str] | None = None) -> int:
    parser = create_parser()
    args = parser.parse_args(argv)
    try:
        config = load_config(args.config)
        org = args.org if args.org is not None else config.org
        if not org:
            parser.error("--org is required unless it is set in the TOML configuration")
        policy_names = tuple(
            args.policy if args.policy is not None else (config.policies or ())
        )
        repository_names = tuple(
            args.repo if args.repo is not None else (config.repositories or ())
        )
        applying = args.command == "apply"
        recreate = args.recreate if applying and args.recreate is not None else False
        allow_dirty_pr = (
            args.allow_dirty_pr
            if applying and args.allow_dirty_pr is not None
            else False
        )
        if not applying and (config.recreate or config.allow_dirty_pr):
            parser.error(
                "configuration options recreate and allow_dirty_pr require the apply command"
            )
        if applying:
            recreate = (
                args.recreate
                if args.recreate is not None
                else (config.recreate or False)
            )
            allow_dirty_pr = (
                args.allow_dirty_pr
                if args.allow_dirty_pr is not None
                else (config.allow_dirty_pr or False)
            )
        quiet = args.quiet if args.quiet is not None else (config.quiet or False)
        cache_directory = (
            args.cache_dir
            if args.cache_dir is not None
            else (config.cache_directory or default_cache_directory())
        )
        sync_workers = (
            args.sync_workers
            if args.sync_workers is not None
            else (config.sync_workers or DEFAULT_SYNC_WORKERS)
        )
        policy_workers = (
            args.policy_workers
            if args.policy_workers is not None
            else (config.policy_workers or DEFAULT_POLICY_WORKERS)
        )
        excluded_policy_names = tuple(
            args.exclude_policy
            if args.exclude_policy is not None
            else config.exclude_policies
        )
        if recreate:
            if len(repository_names) != 1:
                parser.error("--recreate requires exactly one --repo")
            if len(policy_names) != 1:
                parser.error("--recreate requires exactly one --policy")
        if not quiet:
            print("Loading policies...", file=sys.stderr, flush=True)
        policy_directories = (
            tuple(dict.fromkeys(args.policy_dir))
            if args.policy_dir is not None
            else config.policy_directories
        )
        local_policy_directories = (
            policy_directories
            if policy_directories is not None
            else (
                (DEFAULT_POLICY_DIRECTORY,) if DEFAULT_POLICY_DIRECTORY.is_dir() else ()
            )
        )
        selection_directories = local_policy_directories + (BUNDLED_POLICY_DIRECTORY,)
        if recreate:
            policy_paths = _resolve_recreate_policy_paths(
                policy_names,
                policy_directories,
            )
        else:
            if policy_names:
                policy_paths = resolve_policy_names(
                    policy_names,
                    selection_directories,
                )
            else:
                local_policy_paths = tuple(
                    path
                    for policy_directory in local_policy_directories
                    for path in discover_policy_paths(policy_directory)
                )
                bundled_policy_paths = discover_policy_paths(BUNDLED_POLICY_DIRECTORY)
                local_policy_path_keys = {path.resolve() for path in local_policy_paths}
                bundled_policy_paths = tuple(
                    path
                    for path in bundled_policy_paths
                    if path.resolve() not in local_policy_path_keys
                )
                policy_paths = local_policy_paths + bundled_policy_paths
            if excluded_policy_names:
                excluded_policy_paths = {
                    path.resolve()
                    for path in resolve_policy_names(
                        excluded_policy_names,
                        selection_directories,
                    )
                }
                policy_paths = tuple(
                    path
                    for path in policy_paths
                    if path.resolve() not in excluded_policy_paths
                )
        policies = load_policies(policy_paths)
        report = run_policies(
            client=GitHubCli(),
            org=org,
            policies=policies,
            repository_names=repository_names,
            checkout_cache_directory=cache_directory,
            apply=applying,
            recreate=recreate,
            allow_dirty_pr=allow_dirty_pr,
            sync_workers=sync_workers,
            policy_workers=policy_workers,
            include_pull_request_status=(
                args.json_output is not None or args.markdown_output is not None
            ),
            progress=_discard_progress if quiet else _write_progress,
        )
    except RepoPolicySyncError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2

    json_output = render_json(report) if args.json_output is not None else None
    markdown_output = (
        render_markdown(report) if args.markdown_output is not None else None
    )
    output = render_table(report)

    try:
        if args.json_output is not None:
            _write_report(args.json_output, json_output)
        if args.markdown_output is not None:
            _write_report(args.markdown_output, markdown_output)
    except RepoPolicySyncError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2

    print(output)
    if report.summary.sync_failures or report.summary.evaluation_failures:
        return 2
    return 1 if report.summary.drifted and not applying else 0


def _write_progress(message: str) -> None:
    print(message, file=sys.stderr, flush=True)


def _discard_progress(_: str) -> None:
    pass


def _resolve_recreate_policy_paths(
    policy_names: tuple[str, ...],
    policy_directories: tuple[Path, ...] | None,
) -> tuple[Path, ...]:
    """Resolve the one policy targeted by recreate, including bundled policies."""

    configured_directories = (
        tuple(dict.fromkeys(policy_directories))
        if policy_directories is not None
        else ((DEFAULT_POLICY_DIRECTORY,) if DEFAULT_POLICY_DIRECTORY.is_dir() else ())
    )
    # Recreate is deliberately restricted to one policy. Resolve its expected
    # path directly so an unrelated malformed policy cannot block the request.
    candidates = tuple(
        directory / name / "policy.yml"
        for directory in configured_directories
        for name in policy_names
        if (directory / name / "policy.yml").is_file()
    )
    if candidates:
        if len(candidates) > 1:
            raise PolicyError(f"policy ID is not unique: {policy_names[0]}")
        return candidates

    bundled_path = BUNDLED_POLICY_DIRECTORY / policy_names[0] / "policy.yml"
    if bundled_path.is_file():
        return (bundled_path,)
    raise PolicyError(f"unknown policy name(s): {', '.join(policy_names)}")


def _write_report(path: Path, output: str | None) -> None:
    if output is None:  # pragma: no cover - guarded by the callers above
        raise RepoPolicySyncError(f"no report was rendered for output path {path}")
    try:
        path.write_text(output + "\n", encoding="utf-8")
    except OSError as exc:
        raise RepoPolicySyncError(f"could not write report {path}: {exc}") from exc


if __name__ == "__main__":  # pragma: no cover - exercised by the console script
    raise SystemExit(main())
