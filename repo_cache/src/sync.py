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

"""Synchronize local, disposable checkouts for every repository in an organization."""

from __future__ import annotations

import os
from collections.abc import Callable, Sequence
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass
from fnmatch import fnmatchcase
from pathlib import Path

from .checkout import sync_default_branch
from .errors import EmptyRepositoryError, RepoCacheError, redact_sensitive_text
from .github import ensure_authenticated, list_repositories
from .models import Repository

DEFAULT_SYNC_WORKERS: int = max(1, os.cpu_count() or 1)
_THREAD_NAME_PREFIX = "repo-cache-sync"


@dataclass(frozen=True)
class SyncOutcome:
    """The result of attempting to sync one repository's default branch."""

    repository: Repository
    checkout: Path
    error: str | None = None
    empty: bool = False


@dataclass(frozen=True)
class SyncReport:
    """The outcome of synchronizing a selection of an organization's repositories."""

    org: str
    cache_dir: Path
    outcomes: tuple[SyncOutcome, ...]

    @property
    def failures(self) -> tuple[SyncOutcome, ...]:
        return tuple(outcome for outcome in self.outcomes if outcome.error)

    @property
    def empty_repositories(self) -> tuple[SyncOutcome, ...]:
        return tuple(outcome for outcome in self.outcomes if outcome.empty)


def sync_org(
    *,
    org: str,
    cache_dir: Path,
    repos: Sequence[str] = (),
    include_archived: bool = False,
    workers: int = DEFAULT_SYNC_WORKERS,
    progress: Callable[[str], None] | None = None,
) -> SyncReport:
    """List an organization's repositories and sync each into `cache_dir/org/<name>`.

    Raises RepoCacheError for an authentication failure or an unknown `repos`
    name or pattern. Repository names without glob metacharacters are matched
    exactly. Names containing ``*``, ``?``, or ``[`` use case-sensitive
    :func:`fnmatch.fnmatchcase` matching against active repositories. Per-
    repository sync failures are captured in `SyncOutcome.error` rather than
    raised, so one broken repository does not abort the rest.
    Empty repositories are reported in `SyncReport.empty_repositories` instead
    of being treated as failures.
    """

    if workers < 1:
        raise RepoCacheError("sync worker count must be at least 1")

    report_progress = progress or (lambda _: None)

    report_progress("Checking gh authentication...")
    ensure_authenticated()

    repositories = list_repositories(org=org)
    active_repositories = tuple(
        repository
        for repository in repositories
        if include_archived or not repository.archived
    )

    available = {repository.name for repository in active_repositories}
    exact_names = {name for name in repos if not _has_glob_pattern(name)}
    patterns = tuple(name for name in repos if _has_glob_pattern(name))
    missing = sorted(exact_names - available)
    unmatched_patterns = tuple(
        sorted(
            {
                pattern
                for pattern in patterns
                if not any(fnmatchcase(name, pattern) for name in available)
            }
        )
    )
    if missing or unmatched_patterns:
        diagnostics = []
        if missing:
            diagnostics.append(
                f"repository filter not found in organization: {', '.join(missing)}"
            )
        if unmatched_patterns:
            diagnostics.append(
                "repository pattern matched no active repositories: "
                + ", ".join(unmatched_patterns)
            )
        raise RepoCacheError("; ".join(diagnostics))

    report_progress(f"Found {len(active_repositories)} active repositories.")
    report_progress(f"Using checkout cache at {cache_dir}.")

    selected_repositories = tuple(
        repository
        for repository in active_repositories
        if not repos
        or repository.name in exact_names
        or any(fnmatchcase(repository.name, pattern) for pattern in patterns)
    )
    repositories_with_branches = tuple(
        repository
        for repository in selected_repositories
        if repository.default_branch is not None
    )

    outcomes: dict[str, SyncOutcome] = {
        repository.name: SyncOutcome(
            repository, cache_dir / org / repository.name, empty=True
        )
        for repository in selected_repositories
        if repository.default_branch is None
    }

    if repositories_with_branches:
        report_progress(
            f"Synchronizing {len(repositories_with_branches)} checkout(s) "
            f"with {workers} worker(s)..."
        )
        with ThreadPoolExecutor(
            max_workers=workers, thread_name_prefix=_THREAD_NAME_PREFIX
        ) as executor:
            futures = {
                executor.submit(
                    sync_default_branch,
                    repository=f"{org}/{repository.name}",
                    branch=repository.default_branch,
                    destination=cache_dir / org / repository.name,
                ): repository
                for repository in repositories_with_branches
            }
            for index, future in enumerate(as_completed(futures), start=1):
                repository = futures[future]
                checkout = cache_dir / org / repository.name
                try:
                    future.result()
                except EmptyRepositoryError:
                    outcomes[repository.name] = SyncOutcome(
                        repository, checkout, empty=True
                    )
                    status = "empty"
                except (RepoCacheError, OSError) as exc:
                    error = redact_sensitive_text(str(exc) or exc.__class__.__name__)
                    outcomes[repository.name] = SyncOutcome(repository, checkout, error)
                    status = "failed"
                else:
                    outcomes[repository.name] = SyncOutcome(repository, checkout, None)
                    status = "done"
                report_progress(
                    f"  [{index}/{len(repositories_with_branches)}] {repository.name}: {status}"
                )

    ordered_outcomes = tuple(
        outcomes[repository.name] for repository in selected_repositories
    )
    return SyncReport(org=org, cache_dir=cache_dir, outcomes=ordered_outcomes)


def _has_glob_pattern(value: str) -> bool:
    """Return whether ``value`` uses one of the supported fnmatch metacharacters."""

    return any(character in value for character in "*?[")
