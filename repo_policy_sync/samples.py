# *******************************************************************************
# Copyright (c) 2026 Contributors to the Eclipse Foundation
#
# See the NOTICE file(s) distributed with this work for additional
# information regarding copyright ownership.
#
# This program and the accompanying materials are made available under the
# terms of the Apache License 2.0 which is available at
# https://www.apache.org/licenses/LICENSE-2.0
#
# SPDX-License-Identifier: Apache-2.0
# *******************************************************************************

"""Read-only collection of representative policy input samples."""

from __future__ import annotations

import json
import shutil
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Callable

from .engine import matches_policy_conditions, policy_sample_paths
from .errors import RepoPolicySyncError, redact_sensitive_text
from .models import Policy, Repository
from .runner import (
    DEFAULT_SYNC_WORKERS,
    RepositoryClient,
    _select_repositories,
    _sync_repositories,
    _validate_requested_repositories,
)


@dataclass(frozen=True)
class SampleFile:
    """One repository file copied into a collected sample case."""

    path: Path
    size: int


@dataclass(frozen=True)
class SampleCase:
    """The files selected for one repository and policy pair."""

    policy_id: str
    repository: str
    files: tuple[SampleFile, ...]


@dataclass(frozen=True)
class SampleCollectionReport:
    """Collected cases and checkout failures."""

    output: Path
    cases: tuple[SampleCase, ...]
    sync_failures: tuple[tuple[str, str], ...] = ()


def collect_samples(
    *,
    client: RepositoryClient,
    org: str,
    policies: tuple[Policy, ...],
    repository_names: tuple[str, ...],
    checkout_cache_directory: Path,
    output_directory: Path,
    sync_workers: int = DEFAULT_SYNC_WORKERS,
    progress: Callable[[str], None] | None = None,
) -> SampleCollectionReport:
    """Collect policy-matching workflow files without changing repositories."""

    if sync_workers < 1:
        raise RepoPolicySyncError("sync worker count must be at least 1")
    _prepare_output_directory(output_directory)
    report_progress = progress or _write_progress
    report_progress("Checking gh authentication...")
    client.ensure_authenticated()
    repositories = tuple(
        repository
        for repository in client.list_repositories(org=org)
        if not repository.archived
    )
    _validate_requested_repositories(repositories, repository_names)
    selected = _select_repositories(repositories, repository_names)
    report_progress(f"Found {len(selected)} active repositories.")
    report_progress(f"Using checkout cache at {checkout_cache_directory}.")
    sync_failures = _sync_repositories(
        client=client,
        org=org,
        repositories=selected,
        checkout_cache_directory=checkout_cache_directory,
        workers=sync_workers,
        progress=report_progress,
    )

    cases: list[SampleCase] = []
    for policy in policies:
        for repository in selected:
            if repository.default_branch is None or repository.name in sync_failures:
                continue
            checkout = checkout_cache_directory / org / repository.name
            try:
                if not matches_policy_conditions(checkout, policy):
                    continue
                paths = policy_sample_paths(checkout, policy)
                case = _copy_case(
                    output_directory,
                    policy,
                    repository,
                    checkout,
                    paths,
                )
            except (OSError, UnicodeError, RepoPolicySyncError) as exc:
                raise RepoPolicySyncError(
                    f"could not collect {policy.id} from {repository.name}: "
                    f"{redact_sensitive_text(str(exc))}"
                ) from exc
            cases.append(case)
            report_progress(
                f"  {policy.id}/{repository.name}: {len(case.files)} file(s)"
            )

    report = SampleCollectionReport(
        output=output_directory,
        cases=tuple(cases),
        sync_failures=tuple(sorted(sync_failures.items())),
    )
    _write_inventory(report)
    return report


def _prepare_output_directory(output: Path) -> None:
    if output.exists():
        if not output.is_dir():
            raise RepoPolicySyncError(f"sample output is not a directory: {output}")
        if any(output.iterdir()):
            raise RepoPolicySyncError(f"sample output directory is not empty: {output}")
    else:
        output.mkdir(parents=True)


def _copy_case(
    output: Path,
    policy: Policy,
    repository: Repository,
    checkout: Path,
    paths: tuple[Path, ...],
) -> SampleCase:
    case_root = output / policy.id / repository.name / "before"
    files: list[SampleFile] = []
    for path in paths:
        relative = path.relative_to(checkout)
        destination = case_root / relative
        destination.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(path, destination)
        files.append(SampleFile(relative, path.stat().st_size))
    return SampleCase(policy.id, repository.name, tuple(files))


def _write_inventory(report: SampleCollectionReport) -> None:
    inventory = {
        "schema_version": 1,
        "cases": [
            {
                "policy_id": case.policy_id,
                "repository": case.repository,
                "files": [
                    {"path": str(sample.path), "size": sample.size}
                    for sample in case.files
                ],
            }
            for case in report.cases
        ],
        "sync_failures": [
            {"repository": repository, "error": error}
            for repository, error in report.sync_failures
        ],
    }
    (report.output / "inventory.json").write_text(
        json.dumps(inventory, indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )


def render_sample_collection(report: SampleCollectionReport) -> str:
    """Render a concise summary for the command line."""

    lines = [
        "📦 Workflow samples",
        f"Collected {len(report.cases)} case(s) in {report.output}.",
    ]
    if report.sync_failures:
        lines.append(f"Checkout failures: {len(report.sync_failures)}.")
    return "\n".join(lines)


def _write_progress(message: str) -> None:
    print(message, file=sys.stderr, flush=True)
