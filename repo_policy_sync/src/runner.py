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

"""Organization-level orchestration for plan and apply runs."""

from __future__ import annotations

import sys
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass
from pathlib import Path
from time import monotonic
from typing import Callable, Protocol

from repo_cache import (
    DEFAULT_SYNC_WORKERS,
    RepoCacheError,
    restore_synced_default_branch,
    sync_org,
)

from .engine import apply_policy, evaluate_policy
from .errors import RepoPolicySyncError, redact_sensitive_text
from .github import (
    CommitResult,
    PolicyPullRequestStatus,
    TOOL_SLUG,
    _pull_request_body,
    policy_branches,
)
from .models import Change, Policy, Repository

DEFAULT_POLICY_WORKERS = DEFAULT_SYNC_WORKERS


class RepositoryClient(Protocol):
    """The small gh/Git boundary required by the organization workflow."""

    def find_open_pull_request(
        self,
        *,
        repository: str,
        branches: tuple[str, ...],
        policy_id: str,
        legacy_policy_ids: tuple[str, ...] = (),
    ) -> object | None: ...

    def find_policy_pull_request_status(
        self,
        *,
        repository: str,
        branches: tuple[str, ...],
        policy_id: str,
        legacy_policy_ids: tuple[str, ...] = (),
    ) -> PolicyPullRequestStatus: ...

    def switch_to_policy_branch(
        self, *, checkout: Path, branch: str, exists_remotely: bool
    ) -> None: ...

    def recreate_policy_branch(self, *, checkout: Path, branch: str) -> None: ...

    def verify_policy_branch_head(
        self, *, checkout: Path, branch: str, expected_head_oid: str
    ) -> None: ...

    def commit_and_push(
        self,
        *,
        checkout: Path,
        branch: str,
        policy: Policy,
        changes: tuple[Change, ...],
        allow_dirty_pr: bool = False,
    ) -> CommitResult: ...

    def has_changes(self, *, checkout: Path, changes: tuple[Change, ...]) -> bool: ...

    def commit_and_force_push(
        self,
        *,
        checkout: Path,
        branch: str,
        expected_head_oid: str,
        policy: Policy,
        changes: tuple[Change, ...],
        allow_dirty_pr: bool = False,
    ) -> CommitResult: ...

    def create_pull_request(
        self,
        *,
        repository: str,
        base: str,
        branch: str,
        policy: Policy,
        changes: tuple[Change, ...],
        head_oid: str,
        draft: bool = False,
    ) -> object: ...

    def update_pull_request(
        self,
        *,
        repository: str,
        pull_request: object,
        policy: Policy,
        changes: tuple[Change, ...],
        head_oid: str,
        failure: str | None = None,
    ) -> None: ...

    def close_pull_request(self, *, repository: str, pull_request: object) -> None: ...

    def mark_pull_request_draft(
        self, *, repository: str, pull_request: object
    ) -> None: ...

    def comment_on_pull_request(
        self, *, repository: str, pull_request: object, failure: str
    ) -> None: ...


@dataclass(frozen=True)
class RunSummary:
    repositories: int
    synchronized: int
    sync_failures: int
    skipped: int
    evaluations: int
    compliant: int
    drifted: int
    not_applicable: int
    evaluation_failures: int
    pull_requests_created: int
    pull_requests_updated: int
    pull_requests_open: int
    pull_requests_recreated: int
    pull_requests_closed: int = 0
    duration_seconds: float = 0.0


@dataclass(frozen=True)
class RunReport:
    """Complete result of evaluating the selected policy/repository pairs."""

    summary: RunSummary
    outcomes: tuple[RepositoryOutcome, ...]
    policies: tuple[Policy, ...] = ()


@dataclass(frozen=True)
class RepositoryOutcome:
    repository: str
    policy_id: str
    when: str
    status: str
    changes: tuple[Change, ...] = ()
    pull_request_url: str | None = None
    warnings: tuple[str, ...] = ()
    error: str | None = None
    policy_pr_status: str | None = None
    pull_request_closed_by_run: bool = False


def run_policies(
    *,
    client: RepositoryClient,
    org: str,
    policies: tuple[Policy, ...],
    repository_names: tuple[str, ...],
    checkout_cache_directory: Path,
    apply: bool,
    recreate: bool = False,
    allow_dirty_pr: bool = False,
    sync_workers: int = DEFAULT_SYNC_WORKERS,
    policy_workers: int = DEFAULT_POLICY_WORKERS,
    progress: Callable[[str], None] | None = None,
    include_pull_request_status: bool = False,
) -> RunReport:
    """Synchronize repositories, then process each policy across repositories in parallel.

    The returned report is independent of presentation so callers can render a
    terminal table, machine-readable JSON, or their own integration output.
    """

    if policy_workers < 1:
        raise RepoPolicySyncError("policy worker count must be at least 1")
    if recreate and not apply:
        raise RepoPolicySyncError("--recreate requires apply mode")
    started = monotonic()
    report_progress = progress or _write_progress
    try:
        sync_report = sync_org(
            org=org,
            cache_dir=checkout_cache_directory,
            repos=repository_names,
            workers=sync_workers,
            progress=report_progress,
        )
    except RepoCacheError as exc:
        raise RepoPolicySyncError(str(exc)) from exc

    selected_repositories = tuple(
        outcome.repository for outcome in sync_report.outcomes
    )
    sync_failures = {
        outcome.repository.name: outcome.error for outcome in sync_report.failures
    }

    synchronized = len(selected_repositories) - len(sync_failures)
    skipped = sum(
        repository.default_branch is None for repository in selected_repositories
    )
    synchronized -= skipped
    evaluations = compliant = drifted = not_applicable = evaluation_failures = 0
    pull_requests_created = pull_requests_updated = pull_requests_open = (
        pull_requests_recreated
    ) = pull_requests_closed = 0
    outcomes: list[RepositoryOutcome] = []
    for policy in policies:
        policy_outcomes = _run_policy_across_repositories(
            client=client,
            org=org,
            policy=policy,
            repositories=selected_repositories,
            checkout_cache_directory=checkout_cache_directory,
            apply=apply,
            recreate=recreate,
            allow_dirty_pr=allow_dirty_pr,
            sync_failures=sync_failures,
            workers=policy_workers,
            progress=report_progress,
            include_pull_request_status=include_pull_request_status,
        )
        outcomes.extend(policy_outcomes)
        for outcome in policy_outcomes:
            if outcome.status in {"skipped", "sync-error"}:
                continue
            evaluations += 1
            if outcome.status == "error":
                evaluation_failures += 1
                continue
            if outcome.status in {"compliant", "pull-request-closed"}:
                compliant += 1
            elif outcome.status == "not-applicable":
                not_applicable += 1
            elif outcome.status in {
                "changes-required",
                "pull-request-created",
                "pull-request-updated",
                "pull-request-open",
                "pull-request-recreated",
                "pull-request-recreated-no-changes",
            }:
                drifted += 1
            if outcome.status == "pull-request-created":
                pull_requests_created += 1
            elif outcome.status == "pull-request-updated":
                pull_requests_updated += 1
            elif outcome.status == "pull-request-open":
                pull_requests_open += 1
            elif outcome.status in {
                "pull-request-recreated",
                "pull-request-recreated-no-changes",
            }:
                pull_requests_recreated += 1
            if outcome.pull_request_closed_by_run:
                pull_requests_closed += 1
    return RunReport(
        summary=RunSummary(
            repositories=len(selected_repositories),
            synchronized=synchronized,
            sync_failures=len(sync_failures),
            skipped=skipped,
            evaluations=evaluations,
            compliant=compliant,
            drifted=drifted,
            not_applicable=not_applicable,
            evaluation_failures=evaluation_failures,
            pull_requests_created=pull_requests_created,
            pull_requests_updated=pull_requests_updated,
            pull_requests_open=pull_requests_open,
            pull_requests_recreated=pull_requests_recreated,
            pull_requests_closed=pull_requests_closed,
            duration_seconds=monotonic() - started,
        ),
        outcomes=tuple(outcomes),
        policies=policies,
    )


def _run_policy_across_repositories(
    *,
    client: RepositoryClient,
    org: str,
    policy: Policy,
    repositories: tuple[Repository, ...],
    checkout_cache_directory: Path,
    apply: bool,
    recreate: bool,
    allow_dirty_pr: bool,
    sync_failures: dict[str, str],
    workers: int,
    progress: Callable[[str], None],
    include_pull_request_status: bool,
) -> tuple[RepositoryOutcome, ...]:
    """Evaluate or apply one policy in independent repository checkouts concurrently."""

    progress(
        f"{policy.id}: processing {len(repositories)} repositories with {workers} worker(s)..."
    )
    outcomes: list[RepositoryOutcome | None] = [None] * len(repositories)
    futures = {}
    with ThreadPoolExecutor(
        max_workers=workers, thread_name_prefix=TOOL_SLUG
    ) as executor:
        for index, repository in enumerate(repositories):
            if repository.default_branch is None:
                outcomes[index] = RepositoryOutcome(
                    repository.name, policy.id, "unknown", "skipped"
                )
                continue
            if error := sync_failures.get(repository.name):
                outcomes[index] = RepositoryOutcome(
                    repository.name, policy.id, "unknown", "sync-error", error=error
                )
                continue
            futures[
                executor.submit(
                    _run_policy_in_repository,
                    client=client,
                    org=org,
                    repository=repository,
                    policy=policy,
                    checkout=checkout_cache_directory / org / repository.name,
                    apply=apply,
                    recreate=recreate,
                    allow_dirty_pr=allow_dirty_pr,
                    include_pull_request_status=include_pull_request_status,
                )
            ] = (index, repository)
        for completed, future in enumerate(as_completed(futures), start=1):
            index, repository = futures[future]
            try:
                outcome = future.result()
            except (OSError, UnicodeError, RepoPolicySyncError) as exc:
                outcome = RepositoryOutcome(
                    repository.name,
                    policy.id,
                    "unknown",
                    "error",
                    error=_policy_execution_error(exc),
                )
            outcomes[index] = outcome
            progress(
                f"  [{completed}/{len(futures)}] {repository.name}: {outcome.status}"
            )
    return tuple(outcome for outcome in outcomes if outcome is not None)


def _run_policy_in_repository(
    *,
    client: RepositoryClient,
    org: str,
    repository: Repository,
    policy: Policy,
    checkout: Path,
    apply: bool,
    recreate: bool,
    allow_dirty_pr: bool,
    include_pull_request_status: bool,
) -> RepositoryOutcome:
    restore_synced_default_branch(checkout=checkout)
    if (
        repository.default_branch is None
    ):  # pragma: no cover - filtered before submission
        raise RepoPolicySyncError(f"repository {repository.name} has no default branch")
    return _run_repository(
        client=client,
        org=org,
        repository=repository.name,
        default_branch=repository.default_branch,
        policy=policy,
        checkout=checkout,
        apply=apply,
        recreate=recreate,
        allow_dirty_pr=allow_dirty_pr,
        include_pull_request_status=include_pull_request_status,
    )


def _run_repository(
    *,
    client: RepositoryClient,
    org: str,
    repository: str,
    default_branch: str,
    policy: Policy,
    checkout: Path,
    apply: bool,
    recreate: bool = False,
    allow_dirty_pr: bool = False,
    include_pull_request_status: bool = False,
) -> RepositoryOutcome:
    full_name = f"{org}/{repository}"
    policy_pr_status = (
        _find_policy_pull_request_status(
            client=client,
            repository=full_name,
            policy=policy,
        )
        if include_pull_request_status
        else None
    )
    try:
        evaluation = evaluate_policy(checkout, policy, organization=org)
    except RepoPolicySyncError as exc:
        if apply:
            existing_pr = client.find_open_pull_request(
                repository=full_name,
                branches=policy_branches(policy),
                policy_id=policy.id,
                legacy_policy_ids=policy.legacy_names,
            )
            if existing_pr is not None:
                branch = existing_pr.branch or policy_branches(policy)[0]
                try:
                    client.verify_policy_branch_head(
                        checkout=checkout,
                        branch=branch,
                        expected_head_oid=existing_pr.expected_head_oid,
                    )
                except RepoPolicySyncError:
                    pass
                else:
                    client.update_pull_request(
                        repository=full_name,
                        pull_request=existing_pr,
                        policy=policy,
                        changes=(),
                        head_oid=existing_pr.expected_head_oid,
                        failure=str(exc),
                    )
                    client.close_pull_request(
                        repository=full_name, pull_request=existing_pr
                    )
        raise
    if not evaluation.applies:
        if apply:
            existing_pr = client.find_open_pull_request(
                repository=full_name,
                branches=policy_branches(policy),
                policy_id=policy.id,
                legacy_policy_ids=policy.legacy_names,
            )
            if existing_pr is not None:
                _close_owned_pull_request(
                    client=client,
                    repository=full_name,
                    policy=policy,
                    pull_request=existing_pr,
                    checkout=checkout,
                )
                return RepositoryOutcome(
                    repository,
                    policy.id,
                    "no (live)",
                    "not-applicable",
                    pull_request_url=existing_pr.url,
                    policy_pr_status="closed",
                    pull_request_closed_by_run=True,
                )
        return RepositoryOutcome(
            repository,
            policy.id,
            "no (live)",
            "not-applicable",
            pull_request_url=_policy_pr_url(policy_pr_status),
            policy_pr_status=_policy_pr_label(policy_pr_status),
        )
    if recreate:
        return _recreate_repository(
            client=client,
            organization=org,
            repository=repository,
            full_name=full_name,
            policy=policy,
            checkout=checkout,
            allow_dirty_pr=allow_dirty_pr,
        )
    if not evaluation.changes:
        existing_pr = (
            policy_pr_status.open
            if policy_pr_status is not None
            else (
                client.find_open_pull_request(
                    repository=full_name,
                    branches=policy_branches(policy),
                    policy_id=policy.id,
                    legacy_policy_ids=policy.legacy_names,
                )
                if apply
                else None
            )
        )
        if apply and existing_pr is not None:
            _close_owned_pull_request(
                client=client,
                repository=full_name,
                policy=policy,
                pull_request=existing_pr,
                checkout=checkout,
            )
            return RepositoryOutcome(
                repository,
                policy.id,
                "yes (live)",
                "pull-request-closed",
                pull_request_url=existing_pr.url,
                policy_pr_status="closed",
                pull_request_closed_by_run=True,
            )
        return RepositoryOutcome(
            repository,
            policy.id,
            "yes (live)",
            "compliant",
            pull_request_url=_policy_pr_url(policy_pr_status),
            policy_pr_status=_policy_pr_label(policy_pr_status),
        )
    if not apply:
        return RepositoryOutcome(
            repository,
            policy.id,
            "yes (live)",
            "changes-required",
            changes=evaluation.changes,
            pull_request_url=_policy_pr_url(policy_pr_status),
            policy_pr_status=_policy_pr_label(policy_pr_status),
        )

    branches = policy_branches(policy)
    existing_pr = policy_pr_status.open if policy_pr_status is not None else None
    if existing_pr is None:
        existing_pr = client.find_open_pull_request(
            repository=full_name,
            branches=branches,
            policy_id=policy.id,
            legacy_policy_ids=policy.legacy_names,
        )
    branch = (existing_pr.branch if existing_pr is not None else "") or branches[0]
    if existing_pr is not None and existing_pr.expected_head_oid is None:
        raise RepoPolicySyncError(
            f"refusing to modify policy-owned pull request {existing_pr.url}: "
            "it has no recognized branch-head marker"
        )
    try:
        if existing_pr is not None:
            client.verify_policy_branch_head(
                checkout=checkout,
                branch=branch,
                expected_head_oid=existing_pr.expected_head_oid,
            )
        client.switch_to_policy_branch(
            checkout=checkout, branch=branch, exists_remotely=existing_pr is not None
        )
        applied = apply_policy(checkout, policy, organization=org)
        head_oid = existing_pr.expected_head_oid if existing_pr is not None else ""
        pre_commit_failure = None
        if applied.changes:
            commit_result = client.commit_and_push(
                checkout=checkout,
                branch=branch,
                policy=policy,
                changes=applied.changes,
                allow_dirty_pr=allow_dirty_pr,
            )
            head_oid, pre_commit_failure = _commit_result_parts(commit_result)
    except RepoPolicySyncError as exc:
        if existing_pr is not None:
            try:
                client.verify_policy_branch_head(
                    checkout=checkout,
                    branch=branch,
                    expected_head_oid=existing_pr.expected_head_oid,
                )
            except RepoPolicySyncError:
                pass
            else:
                client.update_pull_request(
                    repository=full_name,
                    pull_request=existing_pr,
                    policy=policy,
                    changes=evaluation.changes,
                    head_oid=existing_pr.expected_head_oid,
                    failure=str(exc),
                )
                client.close_pull_request(
                    repository=full_name, pull_request=existing_pr
                )
        raise
    if not applied.changes:
        if existing_pr is not None:
            if existing_pr.mergeable == "CONFLICTING":
                # The checkout currently contains the unchanged PR branch. Reset
                # it to the freshly synchronized default branch before rebuilding
                # the conflicted PR with the current policy.
                restore_synced_default_branch(checkout=checkout)
                return _recreate_existing_pull_request(
                    client=client,
                    organization=org,
                    repository=repository,
                    full_name=full_name,
                    policy=policy,
                    checkout=checkout,
                    existing_pr=existing_pr,
                    changes=evaluation.changes,
                    allow_dirty_pr=allow_dirty_pr,
                )
            if _pull_request_body_changed(
                existing_pr,
                policy=policy,
                changes=evaluation.changes,
                head_oid=existing_pr.expected_head_oid,
            ):
                client.update_pull_request(
                    repository=full_name,
                    pull_request=existing_pr,
                    policy=policy,
                    changes=evaluation.changes,
                    head_oid=existing_pr.expected_head_oid,
                )
                return RepositoryOutcome(
                    repository,
                    policy.id,
                    "yes (live)",
                    "pull-request-updated",
                    changes=evaluation.changes,
                    pull_request_url=existing_pr.url,
                    policy_pr_status="open",
                )
            return RepositoryOutcome(
                repository,
                policy.id,
                "yes (live)",
                "pull-request-open",
                changes=evaluation.changes,
                pull_request_url=existing_pr.url,
                policy_pr_status="open",
            )
        return RepositoryOutcome(
            repository,
            policy.id,
            "yes (live)",
            "compliant",
            pull_request_url=_policy_pr_url(policy_pr_status),
            policy_pr_status=_policy_pr_label(policy_pr_status),
        )
    if existing_pr is None:
        pull_request = client.create_pull_request(
            repository=full_name,
            base=default_branch,
            branch=branch,
            policy=policy,
            changes=applied.changes,
            head_oid=head_oid,
            draft=pre_commit_failure is not None,
        )
        if pre_commit_failure is not None:
            _comment_dirty_pull_request(
                client=client,
                repository=full_name,
                pull_request=pull_request,
                failure=pre_commit_failure,
            )
        return RepositoryOutcome(
            repository,
            policy.id,
            "yes (live)",
            "pull-request-created",
            changes=applied.changes,
            pull_request_url=pull_request.url,
            warnings=pull_request.warnings,
            policy_pr_status="open",
        )
    client.update_pull_request(
        repository=full_name,
        pull_request=existing_pr,
        policy=policy,
        changes=applied.changes,
        head_oid=head_oid,
    )
    if pre_commit_failure is not None:
        _mark_dirty_pull_request(
            client=client,
            repository=full_name,
            pull_request=existing_pr,
            failure=pre_commit_failure,
        )
    return RepositoryOutcome(
        repository,
        policy.id,
        "yes (live)",
        "pull-request-updated",
        changes=applied.changes,
        pull_request_url=existing_pr.url,
        policy_pr_status="open",
    )


def _find_policy_pull_request_status(
    *, client: RepositoryClient, repository: str, policy: Policy
) -> PolicyPullRequestStatus:
    return client.find_policy_pull_request_status(
        repository=repository,
        branches=policy_branches(policy),
        policy_id=policy.id,
        legacy_policy_ids=policy.legacy_names,
    )


def _policy_execution_error(error: Exception) -> str:
    """Turn local policy I/O failures into concise reportable diagnostics."""

    return redact_sensitive_text(f"policy execution failed: {error}")


def _policy_pr_label(status: PolicyPullRequestStatus | None) -> str | None:
    if status is None:
        return None
    if status.open is not None:
        return "open"
    if status.merged is not None:
        return "merged"
    if status.closed is not None:
        return "closed"
    return "none"


def _policy_pr_url(status: PolicyPullRequestStatus | None) -> str | None:
    if status is None:
        return None
    pull_request = status.open or status.merged or status.closed
    return pull_request.url if pull_request is not None else None


def _close_owned_pull_request(
    *,
    client: RepositoryClient,
    repository: str,
    policy: Policy,
    pull_request: object,
    checkout: Path,
) -> None:
    """Close an owned PR only after confirming its branch is still tool-owned."""

    expected_head_oid = pull_request.expected_head_oid
    if expected_head_oid is None:
        raise RepoPolicySyncError(
            f"refusing to close policy-owned pull request {pull_request.url}: "
            "it has no recognized branch-head marker"
        )
    branch = pull_request.branch or policy_branches(policy)[0]
    client.verify_policy_branch_head(
        checkout=checkout,
        branch=branch,
        expected_head_oid=expected_head_oid,
    )
    client.close_pull_request(repository=repository, pull_request=pull_request)


def _pull_request_body_changed(
    pull_request: object,
    *,
    policy: Policy,
    changes: tuple[Change, ...],
    head_oid: str,
) -> bool:
    """Return whether the generated explanation differs from the PR body."""

    body = getattr(pull_request, "body", None)
    return body != _pull_request_body(policy, changes, head_oid=head_oid)


def _commit_result_parts(result: CommitResult) -> tuple[str, str | None]:
    return result.head_oid, result.pre_commit_failure


def _mark_dirty_pull_request(
    *, client: RepositoryClient, repository: str, pull_request: object, failure: str
) -> None:
    client.mark_pull_request_draft(repository=repository, pull_request=pull_request)
    _comment_dirty_pull_request(
        client=client, repository=repository, pull_request=pull_request, failure=failure
    )


def _comment_dirty_pull_request(
    *, client: RepositoryClient, repository: str, pull_request: object, failure: str
) -> None:
    client.comment_on_pull_request(
        repository=repository, pull_request=pull_request, failure=failure
    )


def _recreate_repository(
    *,
    client: RepositoryClient,
    organization: str,
    repository: str,
    full_name: str,
    policy: Policy,
    checkout: Path,
    allow_dirty_pr: bool = False,
) -> RepositoryOutcome:
    """Rebuild an existing policy branch from the freshly synced default branch."""

    branches = policy_branches(policy)
    existing_pr = client.find_open_pull_request(
        repository=full_name,
        branches=branches,
        policy_id=policy.id,
        legacy_policy_ids=policy.legacy_names,
    )
    if existing_pr is None:
        raise RepoPolicySyncError(
            f"cannot recreate {policy.id} for {repository}: no open policy-owned pull request"
        )
    return _recreate_existing_pull_request(
        client=client,
        organization=organization,
        repository=repository,
        full_name=full_name,
        policy=policy,
        checkout=checkout,
        existing_pr=existing_pr,
        allow_dirty_pr=allow_dirty_pr,
    )


def _recreate_existing_pull_request(
    *,
    client: RepositoryClient,
    organization: str,
    repository: str,
    full_name: str,
    policy: Policy,
    checkout: Path,
    existing_pr: object,
    changes: tuple[Change, ...] | None = None,
    allow_dirty_pr: bool = False,
) -> RepositoryOutcome:
    """Rebuild one known policy PR from the freshly synchronized default branch."""

    branches = policy_branches(policy)
    branch = existing_pr.branch or branches[0]
    if existing_pr.expected_head_oid is None:
        raise RepoPolicySyncError(
            f"refusing to recreate policy-owned pull request {existing_pr.url}: "
            "it has no recognized branch-head marker"
        )
    client.verify_policy_branch_head(
        checkout=checkout,
        branch=branch,
        expected_head_oid=existing_pr.expected_head_oid,
    )
    client.recreate_policy_branch(checkout=checkout, branch=branch)
    applied = apply_policy(
        checkout, policy, force_after_apply=True, organization=organization
    )
    if not client.has_changes(checkout=checkout, changes=applied.changes):
        body_changes = applied.changes if changes is None else changes
        if _pull_request_body_changed(
            existing_pr,
            policy=policy,
            changes=body_changes,
            head_oid=existing_pr.expected_head_oid,
        ):
            client.update_pull_request(
                repository=full_name,
                pull_request=existing_pr,
                policy=policy,
                changes=body_changes,
                head_oid=existing_pr.expected_head_oid,
            )
        return RepositoryOutcome(
            repository,
            policy.id,
            "yes (live)",
            "pull-request-recreated-no-changes",
            pull_request_url=existing_pr.url,
            policy_pr_status="open",
        )
    commit_result = client.commit_and_force_push(
        checkout=checkout,
        branch=branch,
        expected_head_oid=existing_pr.expected_head_oid,
        policy=policy,
        changes=applied.changes,
        allow_dirty_pr=allow_dirty_pr,
    )
    head_oid, pre_commit_failure = _commit_result_parts(commit_result)
    client.update_pull_request(
        repository=full_name,
        pull_request=existing_pr,
        policy=policy,
        changes=applied.changes,
        head_oid=head_oid,
    )
    if pre_commit_failure is not None:
        _mark_dirty_pull_request(
            client=client,
            repository=full_name,
            pull_request=existing_pr,
            failure=pre_commit_failure,
        )
    return RepositoryOutcome(
        repository,
        policy.id,
        "yes (live)",
        "pull-request-recreated",
        changes=applied.changes,
        pull_request_url=existing_pr.url,
        policy_pr_status="open",
    )


def _write_progress(message: str) -> None:
    print(message, file=sys.stderr, flush=True)
