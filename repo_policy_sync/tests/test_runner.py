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

from __future__ import annotations

import shutil
import threading
from pathlib import Path

import pytest

from repo_cache import RepoCacheError, SyncOutcome, SyncReport
from repo_policy_sync.src import runner
from repo_policy_sync.src.errors import RepoPolicySyncError
from repo_policy_sync.src.github import (
    CommitResult,
    PolicyPullRequestStatus,
    PullRequest,
    _pull_request_body,
)
from repo_policy_sync.src.models import (
    BazelCondition,
    EnsureLine,
    FileExistsCondition,
    RemoveFile,
    Evaluation,
    Policy,
    Repository,
)
from repo_policy_sync.src.runner import _run_repository, run_policies


class FakeRepositoryClient:
    def __init__(self, source: Path, repositories: tuple[Repository, ...]) -> None:
        self.source = source
        self.repositories = repositories
        self.cloned: list[str] = []

    def find_open_pull_request(self, **_: object) -> None:
        return None

    def switch_to_policy_branch(self, **_: object) -> None:
        raise AssertionError("plan mode must not create a branch")

    def commit_and_push(self, **_: object) -> None:
        raise AssertionError("plan mode must not push")

    def create_pull_request(self, **_: object) -> None:
        raise AssertionError("plan mode must not create a pull request")


def _install_fake_sync(
    monkeypatch: pytest.MonkeyPatch,
    client: FakeRepositoryClient,
    *,
    failures: dict[str, str] | None = None,
) -> None:
    """Replace runner.sync_org/restore_synced_default_branch with in-memory fakes.

    Mirrors repo_cache.sync_org's archived/repos filtering closely enough for
    runner-level tests, without shelling out to git/gh.
    """

    failures = failures or {}

    def fake_sync_org(
        *,
        org: str,
        cache_dir: Path,
        repos=(),
        include_archived: bool = False,
        workers: int = 1,
        progress=None,
    ) -> SyncReport:
        report_progress = progress or (lambda _: None)
        active = tuple(
            repository
            for repository in client.repositories
            if include_archived or not repository.archived
        )
        requested = set(repos)
        available = {repository.name for repository in active}
        missing = sorted(requested - available)
        if missing:
            raise RepoCacheError(
                f"repository filter not found in organization: {', '.join(missing)}"
            )
        selected = tuple(
            repository
            for repository in active
            if not requested or repository.name in requested
        )
        with_branches = tuple(
            repository
            for repository in selected
            if repository.default_branch is not None
        )
        if with_branches:
            report_progress(
                f"Synchronizing {len(with_branches)} checkout(s) "
                f"with {workers} worker(s)..."
            )
        outcomes: dict[str, SyncOutcome] = {}
        for repository in selected:
            checkout = cache_dir / org / repository.name
            if repository.default_branch is None:
                outcomes[repository.name] = SyncOutcome(repository, checkout)
                continue
            client.cloned.append(f"{org}/{repository.name}")
            error = failures.get(repository.name)
            if error is not None:
                outcomes[repository.name] = SyncOutcome(repository, checkout, error)
                continue
            shutil.copytree(client.source, checkout)
            outcomes[repository.name] = SyncOutcome(repository, checkout, None)
        ordered = tuple(outcomes[repository.name] for repository in selected)
        return SyncReport(org=org, cache_dir=cache_dir, outcomes=ordered)

    monkeypatch.setattr(runner, "sync_org", fake_sync_org)
    monkeypatch.setattr(runner, "restore_synced_default_branch", lambda **_: None)


class RoundTripClient:
    def __init__(self) -> None:
        self.pull_request: PullRequest | None = None
        self.branch_snapshot: dict[Path, bytes] | None = None
        self.commit_calls = 0
        self.create_calls = 0

    def find_open_pull_request(self, **_: object) -> PullRequest | None:
        return self.pull_request

    def switch_to_policy_branch(
        self, *, checkout: Path, exists_remotely: bool, **_: object
    ) -> None:
        if exists_remotely:
            assert self.branch_snapshot is not None
            _restore_snapshot(checkout, self.branch_snapshot)

    def verify_policy_branch_head(self, **_: object) -> None:
        pass

    def commit_and_push(self, *, checkout: Path, **_: object) -> CommitResult:
        self.commit_calls += 1
        self.branch_snapshot = _snapshot(checkout)
        return CommitResult("b" * 40)

    def create_pull_request(
        self,
        *,
        repository: str,
        branch: str,
        policy: Policy,
        changes: tuple,
        head_oid: str,
        **_: object,
    ) -> PullRequest:
        self.create_calls += 1
        self.pull_request = PullRequest(
            number=1,
            url=f"https://github.example/{repository}/pull/1",
            expected_head_oid=head_oid,
            branch=branch,
            body=_pull_request_body(policy, changes, head_oid=head_oid),
            mergeable="MERGEABLE",
        )
        return self.pull_request


def _snapshot(root: Path) -> dict[Path, bytes]:
    return {
        path.relative_to(root): path.read_bytes()
        for path in root.rglob("*")
        if path.is_file()
    }


def _restore_snapshot(root: Path, snapshot: dict[Path, bytes]) -> None:
    for child in root.iterdir():
        if child.is_dir() and not child.is_symlink():
            shutil.rmtree(child)
        else:
            child.unlink()
    for relative, contents in snapshot.items():
        path = root / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(contents)


class PolicyStatusClient(FakeRepositoryClient):
    def __init__(
        self,
        source: Path,
        repositories: tuple[Repository, ...],
        status: PolicyPullRequestStatus,
    ) -> None:
        super().__init__(source, repositories)
        self.status = status

    def find_policy_pull_request_status(self, **_: object) -> PolicyPullRequestStatus:
        return self.status


class CompliantRunClient(FakeRepositoryClient):
    def __init__(self, source: Path, repositories: tuple[Repository, ...]) -> None:
        super().__init__(source, repositories)
        self.pull_request = PullRequest(
            1,
            "https://github.example/eclipse-score/candidate/pull/1",
            expected_head_oid="a" * 40,
            branch="repo-policy-sync/example",
        )
        self.closed = False

    def find_open_pull_request(self, **_: object) -> PullRequest:
        return self.pull_request

    def verify_policy_branch_head(self, **_: object) -> None:
        pass

    def close_pull_request(self, **_: object) -> None:
        self.closed = True


def test_runner_clones_all_repositories(
    tmp_path: Path, capsys, monkeypatch: pytest.MonkeyPatch
) -> None:
    repository = tmp_path / "repository"
    repository.mkdir()
    (repository / "MODULE.bazel").write_text('bazel_dep(name = "score_docs_as_code")\n')
    policy = Policy(
        id="example",
        title="Example",
        description=None,
        bazel_condition=BazelCondition(("score_docs_as_code",)),
        ensure=(EnsureLine(Path(".gitignore"), "_build", ()),),
    )
    repositories = (
        Repository("candidate", "main"),
        Repository("excluded", "main"),
    )
    client = FakeRepositoryClient(repository, repositories)
    _install_fake_sync(monkeypatch, client)

    report = run_policies(
        client=client,
        org="eclipse-score",
        policies=(policy,),
        repository_names=(),
        checkout_cache_directory=tmp_path / "cache",
        apply=False,
    )

    assert client.cloned == ["eclipse-score/candidate", "eclipse-score/excluded"]
    assert report.summary.repositories == 2
    assert report.summary.evaluations == 2
    assert report.summary.drifted == 2
    assert report.policies == (policy,)
    captured = capsys.readouterr()
    assert captured.out == ""
    assert "Synchronizing 2 checkout(s)" in captured.err
    assert [outcome.repository for outcome in report.outcomes] == [
        "candidate",
        "excluded",
    ]
    assert all(outcome.status == "changes-required" for outcome in report.outcomes)


def test_plan_apply_and_repeat_reuses_the_owned_policy_branch(tmp_path: Path) -> None:
    default = tmp_path / "default"
    default.mkdir()
    checkout = tmp_path / "checkout"
    shutil.copytree(default, checkout)
    policy = Policy(
        id="example",
        title="Example",
        description=None,
        bazel_condition=None,
        ensure=(EnsureLine(Path("required.txt"), "yes", ()),),
    )
    client = RoundTripClient()

    plan = _run_repository(
        client=client,
        org="eclipse-score",
        repository="candidate",
        default_branch="main",
        policy=policy,
        checkout=checkout,
        apply=False,
    )

    assert plan.status == "changes-required"
    assert client.commit_calls == 0
    assert client.create_calls == 0
    assert not (checkout / "required.txt").exists()

    first_apply = _run_repository(
        client=client,
        org="eclipse-score",
        repository="candidate",
        default_branch="main",
        policy=policy,
        checkout=checkout,
        apply=True,
    )

    assert first_apply.status == "pull-request-created"
    assert client.commit_calls == 1
    assert client.create_calls == 1

    shutil.rmtree(checkout)
    shutil.copytree(default, checkout)
    second_apply = _run_repository(
        client=client,
        org="eclipse-score",
        repository="candidate",
        default_branch="main",
        policy=policy,
        checkout=checkout,
        apply=True,
    )

    assert second_apply.status == "pull-request-open"
    assert client.commit_calls == 1
    assert client.create_calls == 1


def test_runner_adds_policy_pull_request_status_for_markdown_reports(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    source = tmp_path / "repository"
    source.mkdir()
    policy = Policy(
        "example", "Example", None, None, (EnsureLine(Path("required.txt"), "yes", ()),)
    )
    client = PolicyStatusClient(
        source,
        (Repository("candidate", "main"),),
        PolicyPullRequestStatus(
            merged=PullRequest(
                7,
                "https://github.example/owner/candidate/pull/7",
                merged_at="2026-01-01",
            )
        ),
    )
    _install_fake_sync(monkeypatch, client)

    report = run_policies(
        client=client,
        org="eclipse-score",
        policies=(policy,),
        repository_names=(),
        checkout_cache_directory=tmp_path / "cache",
        apply=False,
        sync_workers=1,
        policy_workers=1,
        include_pull_request_status=True,
    )

    assert report.outcomes[0].policy_pr_status == "merged"
    assert report.outcomes[0].pull_request_url.endswith("/7")


def test_runner_reports_a_pre_existing_closed_pull_request_for_plan(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Plan surfaces a policy PR someone closed without merging, not just none."""
    source = tmp_path / "repository"
    source.mkdir()
    policy = Policy(
        "example", "Example", None, None, (EnsureLine(Path("required.txt"), "yes", ()),)
    )
    client = PolicyStatusClient(
        source,
        (Repository("candidate", "main"),),
        PolicyPullRequestStatus(
            closed=PullRequest(
                8,
                "https://github.example/owner/candidate/pull/8",
                closed_at="2026-01-01",
            )
        ),
    )
    _install_fake_sync(monkeypatch, client)

    report = run_policies(
        client=client,
        org="eclipse-score",
        policies=(policy,),
        repository_names=(),
        checkout_cache_directory=tmp_path / "cache",
        apply=False,
        sync_workers=1,
        policy_workers=1,
        include_pull_request_status=True,
    )

    assert report.outcomes[0].policy_pr_status == "closed"
    assert report.outcomes[0].pull_request_url.endswith("/8")
    assert report.summary.pull_requests_closed == 0


def test_runner_does_not_count_a_pre_existing_closed_pr_as_closed_by_plan(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Plan reporting a historical closed PR must not claim this run closed one."""
    source = tmp_path / "repository"
    source.mkdir()
    policy = Policy(
        "example",
        "Example",
        None,
        None,
        (),
        file_exists_condition=FileExistsCondition(Path("does-not-exist")),
    )
    client = PolicyStatusClient(
        source,
        (Repository("candidate", "main"),),
        PolicyPullRequestStatus(
            closed=PullRequest(
                8,
                "https://github.example/owner/candidate/pull/8",
                closed_at="2026-01-01",
            )
        ),
    )
    _install_fake_sync(monkeypatch, client)

    report = run_policies(
        client=client,
        org="eclipse-score",
        policies=(policy,),
        repository_names=(),
        checkout_cache_directory=tmp_path / "cache",
        apply=False,
        sync_workers=1,
        policy_workers=1,
        include_pull_request_status=True,
    )

    assert report.outcomes[0].status == "not-applicable"
    assert report.outcomes[0].policy_pr_status == "closed"
    assert report.summary.pull_requests_closed == 0


def test_runner_reports_pull_request_status_when_policy_is_not_applicable(
    tmp_path: Path,
) -> None:
    """Plan reports an existing PR even when the live policy no longer applies."""
    policy = Policy(
        "example",
        "Example",
        None,
        None,
        (),
        file_exists_condition=FileExistsCondition(Path("does-not-exist")),
    )
    client = PolicyStatusClient(
        tmp_path,
        (Repository("repo", "main"),),
        PolicyPullRequestStatus(
            open=PullRequest(1, "https://github.example/owner/repo/pull/1")
        ),
    )

    outcome = _run_repository(
        client=client,
        org="eclipse-score",
        repository="repo",
        default_branch="main",
        policy=policy,
        checkout=tmp_path,
        apply=False,
        include_pull_request_status=True,
    )

    assert outcome.status == "not-applicable"
    assert outcome.policy_pr_status == "open"
    assert outcome.pull_request_url == "https://github.example/owner/repo/pull/1"


def test_runner_counts_closed_pull_requests_as_compliant(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    source = tmp_path / "repository"
    source.mkdir()
    (source / "required.txt").write_text("yes\n")
    policy = Policy(
        "example", "Example", None, None, (EnsureLine(Path("required.txt"), "yes", ()),)
    )
    client = CompliantRunClient(source, (Repository("candidate", "main"),))
    _install_fake_sync(monkeypatch, client)

    report = run_policies(
        client=client,
        org="eclipse-score",
        policies=(policy,),
        repository_names=(),
        checkout_cache_directory=tmp_path / "cache",
        apply=True,
        sync_workers=1,
        policy_workers=1,
    )

    assert report.summary.compliant == 1
    assert report.summary.drifted == 0
    assert report.summary.pull_requests_closed == 1
    assert report.outcomes[0].status == "pull-request-closed"
    assert client.closed


def test_runner_syncs_a_repository_once_for_multiple_policies(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    repository = tmp_path / "repository"
    repository.mkdir()
    first = Policy(
        id="first",
        title="First",
        description=None,
        bazel_condition=None,
        ensure=(EnsureLine(Path("first.txt"), "first", ()),),
    )
    second = Policy(
        id="second",
        title="Second",
        description=None,
        bazel_condition=None,
        ensure=(EnsureLine(Path("second.txt"), "second", ()),),
    )
    client = FakeRepositoryClient(repository, (Repository("candidate", "main"),))
    _install_fake_sync(monkeypatch, client)

    report = run_policies(
        client=client,
        org="eclipse-score",
        policies=(first, second),
        repository_names=(),
        checkout_cache_directory=tmp_path / "cache",
        apply=False,
        sync_workers=2,
    )

    assert client.cloned == ["eclipse-score/candidate"]
    assert report.summary.repositories == 1
    assert report.summary.evaluations == 2
    assert report.summary.drifted == 2


def test_runner_excludes_archived_repositories(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    source = tmp_path / "repository"
    source.mkdir()
    policy = Policy(
        id="example",
        title="Example",
        description=None,
        bazel_condition=None,
        ensure=(EnsureLine(Path(".gitignore"), "_build", ()),),
    )
    client = FakeRepositoryClient(
        source,
        (
            Repository("active", "main"),
            Repository("archived", "main", archived=True),
            Repository("without-default", None),
        ),
    )
    _install_fake_sync(monkeypatch, client)

    report = run_policies(
        client=client,
        org="eclipse-score",
        policies=(policy,),
        repository_names=(),
        checkout_cache_directory=tmp_path / "cache",
        apply=False,
        sync_workers=1,
        policy_workers=1,
    )

    assert client.cloned == ["eclipse-score/active"]
    assert report.summary.repositories == 2
    assert report.summary.skipped == 1
    assert [(outcome.repository, outcome.status) for outcome in report.outcomes] == [
        ("active", "changes-required"),
        ("without-default", "skipped"),
    ]


def test_runner_propagates_authentication_failures(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    source = tmp_path / "repository"
    source.mkdir()
    client = FakeRepositoryClient(source, (Repository("active", "main"),))

    def fake_sync_org(**_: object) -> SyncReport:
        raise RepoCacheError("authentication failed")

    monkeypatch.setattr(runner, "sync_org", fake_sync_org)

    with pytest.raises(RepoPolicySyncError, match="authentication failed"):
        run_policies(
            client=client,
            org="eclipse-score",
            policies=(),
            repository_names=(),
            checkout_cache_directory=tmp_path / "cache",
            apply=False,
        )


def test_runner_counts_a_sync_failure_once_per_repository(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    source = tmp_path / "repository"
    source.mkdir()
    policies = (
        Policy(
            "first", "First", None, None, (EnsureLine(Path("first.txt"), "first", ()),)
        ),
        Policy(
            "second",
            "Second",
            None,
            None,
            (EnsureLine(Path("second.txt"), "second", ()),),
        ),
    )
    client = FakeRepositoryClient(source, (Repository("candidate", "main"),))
    _install_fake_sync(monkeypatch, client, failures={"candidate": "checkout failed"})

    report = run_policies(
        client=client,
        org="eclipse-score",
        policies=policies,
        repository_names=(),
        checkout_cache_directory=tmp_path / "cache",
        apply=False,
        sync_workers=1,
        policy_workers=1,
    )

    assert report.summary.repositories == 1
    assert report.summary.synchronized == 0
    assert report.summary.sync_failures == 1
    assert report.summary.evaluations == 0
    assert [outcome.status for outcome in report.outcomes] == [
        "sync-error",
        "sync-error",
    ]


def test_runner_reports_checkout_os_errors_without_a_traceback(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    source = tmp_path / "repository"
    source.mkdir()
    policy = Policy(
        "example",
        "Example",
        None,
        None,
        (EnsureLine(Path("required.txt"), "yes", ()),),
    )
    client = FakeRepositoryClient(source, (Repository("candidate", "main"),))
    _install_fake_sync(
        monkeypatch,
        client,
        failures={"candidate": "checkout cache is not accessible"},
    )

    report = run_policies(
        client=client,
        org="eclipse-score",
        policies=(policy,),
        repository_names=(),
        checkout_cache_directory=tmp_path / "cache",
        apply=False,
        sync_workers=1,
        policy_workers=1,
    )

    assert report.summary.sync_failures == 1
    assert report.outcomes[0].status == "sync-error"
    assert report.outcomes[0].error == "checkout cache is not accessible"


def test_runner_reports_policy_file_io_failures_without_aborting_other_evaluations(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    source = tmp_path / "repository"
    source.mkdir()
    policy = Policy(
        "example", "Example", None, None, (EnsureLine(Path("required.txt"), "yes", ()),)
    )
    client = FakeRepositoryClient(
        source,
        (Repository("first", "main"), Repository("second", "main")),
    )
    _install_fake_sync(monkeypatch, client)
    calls = 0

    def evaluate(*_: object, **__: object):
        nonlocal calls
        calls += 1
        if calls == 1:
            raise UnicodeDecodeError("utf-8", b"\xff", 0, 1, "invalid start byte")
        return Evaluation(applies=True, changes=())

    monkeypatch.setattr(runner, "evaluate_policy", evaluate)

    report = run_policies(
        client=client,
        org="eclipse-score",
        policies=(policy,),
        repository_names=(),
        checkout_cache_directory=tmp_path / "cache",
        apply=False,
        sync_workers=1,
        policy_workers=1,
    )

    assert report.summary.evaluation_failures == 1
    assert report.summary.compliant == 1
    assert [outcome.status for outcome in report.outcomes] == ["error", "compliant"]
    assert (
        report.outcomes[0].error
        == "policy execution failed: 'utf-8' codec can't decode byte 0xff in position 0: invalid start byte"
    )


def test_runner_redacts_raw_policy_execution_errors(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    source = tmp_path / "repository"
    source.mkdir()
    policy = Policy(
        "example", "Example", None, None, (EnsureLine(Path("required.txt"), "yes", ()),)
    )
    client = FakeRepositoryClient(source, (Repository("candidate", "main"),))
    _install_fake_sync(monkeypatch, client)

    def evaluate(*_: object, **__: object):
        raise UnicodeError("authorization: Bearer ghp_secret_value_12345")

    monkeypatch.setattr(runner, "evaluate_policy", evaluate)

    report = run_policies(
        client=client,
        org="eclipse-score",
        policies=(policy,),
        repository_names=(),
        checkout_cache_directory=tmp_path / "cache",
        apply=False,
        sync_workers=1,
        policy_workers=1,
    )

    assert report.outcomes[0].error is not None
    assert "ghp_secret_value_12345" not in report.outcomes[0].error
    assert "[REDACTED]" in report.outcomes[0].error


def test_runner_processes_one_policy_across_repositories_in_parallel(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    source = tmp_path / "repository"
    source.mkdir()
    policy = Policy(
        id="example",
        title="Example",
        description=None,
        bazel_condition=None,
        ensure=(EnsureLine(Path(".gitignore"), "_build", ()),),
    )
    client = FakeRepositoryClient(
        source,
        (Repository("first", "main"), Repository("second", "main")),
    )
    _install_fake_sync(monkeypatch, client)
    barrier = threading.Barrier(2)

    def run_in_parallel(**kwargs: object) -> runner.RepositoryOutcome:
        barrier.wait(timeout=2)
        return runner.RepositoryOutcome(
            repository=str(kwargs["repository"]),
            policy_id=policy.id,
            when="yes (live)",
            status="compliant",
        )

    monkeypatch.setattr(runner, "_run_repository", run_in_parallel)

    report = run_policies(
        client=client,
        org="eclipse-score",
        policies=(policy,),
        repository_names=(),
        checkout_cache_directory=tmp_path / "cache",
        apply=False,
        sync_workers=2,
        policy_workers=2,
    )

    assert report.summary.compliant == 2


def test_existing_pull_request_is_closed_with_failure_details(tmp_path: Path) -> None:
    checkout = tmp_path / "repository"
    (checkout / "obsolete").mkdir(parents=True)
    policy = Policy(
        id="example",
        title="Example",
        description=None,
        bazel_condition=None,
        ensure=(RemoveFile(Path("obsolete")),),
    )
    client = ExistingPullRequestFailureClient()

    with pytest.raises(RepoPolicySyncError, match="refusing to remove directory"):
        _run_repository(
            client=client,
            org="eclipse-score",
            repository="candidate",
            default_branch="main",
            policy=policy,
            checkout=checkout,
            apply=True,
        )

    assert client.failure is not None
    assert "refusing to remove directory" in client.failure
    assert client.closed


def test_pre_commit_failure_can_create_a_dirty_draft_pull_request(
    tmp_path: Path,
) -> None:
    checkout = tmp_path / "repository"
    checkout.mkdir()
    policy = Policy(
        "example", "Example", None, None, (EnsureLine(Path("required.txt"), "yes", ()),)
    )
    client = DirtyPullRequestClient()

    outcome = _run_repository(
        client=client,
        org="eclipse-score",
        repository="candidate",
        default_branch="main",
        policy=policy,
        checkout=checkout,
        apply=True,
        allow_dirty_pr=True,
    )

    assert outcome.status == "pull-request-created"
    assert client.created_draft
    assert not client.marked_draft
    assert client.failure == "pre-commit found issues"


class DirtyPullRequestClient:
    def __init__(self) -> None:
        self.created_draft = False
        self.marked_draft = False
        self.failure: str | None = None
        self.pull_request = PullRequest(
            1, "https://github.example/eclipse-score/candidate/pull/1"
        )

    def find_open_pull_request(self, **_: object) -> None:
        return None

    def switch_to_policy_branch(self, **_: object) -> None:
        pass

    def commit_and_push(self, **_: object) -> CommitResult:
        return CommitResult("b" * 40, "pre-commit found issues")

    def create_pull_request(self, *, draft: bool = False, **_: object) -> PullRequest:
        self.created_draft = draft
        return self.pull_request

    def mark_pull_request_draft(self, **_: object) -> None:
        self.marked_draft = True

    def comment_on_pull_request(self, *, failure: str, **_: object) -> None:
        self.failure = failure


class ExistingPullRequestFailureClient:
    pull_request = PullRequest(
        1, "https://github.example/eclipse-score/candidate/pull/1", "a" * 40
    )

    def __init__(self) -> None:
        self.failure: str | None = None
        self.closed = False

    def find_open_pull_request(self, **_: object) -> PullRequest:
        return self.pull_request

    def switch_to_policy_branch(self, **_: object) -> None:
        pass

    def verify_policy_branch_head(self, **_: object) -> None:
        pass

    def commit_and_push(self, **_: object) -> str:
        raise AssertionError("a failed policy must not be committed")

    def update_pull_request(self, *, failure: str | None = None, **_: object) -> None:
        self.failure = failure

    def close_pull_request(self, **_: object) -> None:
        self.closed = True


class CompliantPullRequestClient:
    def __init__(self, *, verification_failure: RepoPolicySyncError | None = None):
        self.pull_request = PullRequest(
            1,
            "https://github.example/eclipse-score/candidate/pull/1",
            expected_head_oid="a" * 40,
            branch="repo-policy-sync/example",
        )
        self.verification_failure = verification_failure
        self.verified = False
        self.closed = False

    def find_open_pull_request(self, **_: object) -> PullRequest:
        return self.pull_request

    def verify_policy_branch_head(self, **_: object) -> None:
        self.verified = True
        if self.verification_failure is not None:
            raise self.verification_failure

    def close_pull_request(self, **_: object) -> None:
        self.closed = True


def test_apply_closes_owned_pull_request_after_default_branch_compliance(
    tmp_path: Path,
) -> None:
    checkout = tmp_path / "repository"
    checkout.mkdir()
    (checkout / "required.txt").write_text("yes\n")
    policy = Policy(
        "example",
        "Example",
        None,
        None,
        (EnsureLine(Path("required.txt"), "yes", ()),),
    )
    client = CompliantPullRequestClient()

    outcome = _run_repository(
        client=client,
        org="eclipse-score",
        repository="candidate",
        default_branch="main",
        policy=policy,
        checkout=checkout,
        apply=True,
    )

    assert outcome.status == "pull-request-closed"
    assert outcome.policy_pr_status == "closed"
    assert outcome.pull_request_url == client.pull_request.url
    assert client.verified
    assert client.closed


def test_apply_closes_owned_pull_request_when_policy_is_not_applicable(
    tmp_path: Path,
) -> None:
    checkout = tmp_path / "repository"
    checkout.mkdir()
    policy = Policy(
        "example",
        "Example",
        None,
        BazelCondition(("missing_dependency",)),
        (),
    )
    client = CompliantPullRequestClient()

    outcome = _run_repository(
        client=client,
        org="eclipse-score",
        repository="candidate",
        default_branch="main",
        policy=policy,
        checkout=checkout,
        apply=True,
    )

    assert outcome.when == "no (live)"
    assert outcome.status == "not-applicable"
    assert outcome.policy_pr_status == "closed"
    assert outcome.pull_request_url == client.pull_request.url
    assert client.verified
    assert client.closed


def test_compliant_pull_request_is_not_closed_when_branch_head_changed(
    tmp_path: Path,
) -> None:
    checkout = tmp_path / "repository"
    checkout.mkdir()
    (checkout / "required.txt").write_text("yes\n")
    policy = Policy(
        "example",
        "Example",
        None,
        None,
        (EnsureLine(Path("required.txt"), "yes", ()),),
    )
    client = CompliantPullRequestClient(
        verification_failure=RepoPolicySyncError("branch changed")
    )

    with pytest.raises(RepoPolicySyncError, match="branch changed"):
        _run_repository(
            client=client,
            org="eclipse-score",
            repository="candidate",
            default_branch="main",
            policy=policy,
            checkout=checkout,
            apply=True,
        )

    assert client.verified
    assert not client.closed


def test_recreate_rebuilds_the_existing_policy_branch(tmp_path: Path) -> None:
    checkout = tmp_path / "repository"
    checkout.mkdir()
    (checkout / ".bazelversion").write_text("8.5.0\n")
    policy = Policy(
        id="example",
        title="Example",
        description=None,
        bazel_condition=None,
        ensure=(EnsureLine(Path(".bazelversion"), "8.6.0", ()),),
    )
    client = RecreateClient(has_changes=True)

    outcome = _run_repository(
        client=client,
        org="eclipse-score",
        repository="candidate",
        default_branch="main",
        policy=policy,
        checkout=checkout,
        apply=True,
        recreate=True,
    )

    assert outcome.status == "pull-request-recreated"
    assert client.recreated_branch
    assert client.force_pushed
    assert client.updated


def test_recreate_does_not_push_without_a_diff(tmp_path: Path) -> None:
    checkout = tmp_path / "repository"
    checkout.mkdir()
    (checkout / ".bazelversion").write_text("8.5.0\n")
    policy = Policy(
        "example",
        "Example",
        None,
        None,
        (EnsureLine(Path(".bazelversion"), "8.6.0", ()),),
    )
    client = RecreateClient(has_changes=False)

    outcome = _run_repository(
        client=client,
        org="eclipse-score",
        repository="candidate",
        default_branch="main",
        policy=policy,
        checkout=checkout,
        apply=True,
        recreate=True,
    )

    assert outcome.status == "pull-request-recreated-no-changes"
    assert not client.force_pushed
    assert client.updated


def test_existing_compliant_pull_request_updates_only_stale_body(
    tmp_path: Path,
) -> None:
    checkout = tmp_path / "repository"
    checkout.mkdir()
    (checkout / ".bazelversion").write_text("8.5.0\n")
    policy = Policy(
        "example",
        "Example",
        None,
        None,
        (EnsureLine(Path(".bazelversion"), "8.6.0", ()),),
    )
    client = ImplicitRecreateClient(mergeable="MERGEABLE", body="stale body")

    outcome = _run_repository(
        client=client,
        org="eclipse-score",
        repository="candidate",
        default_branch="main",
        policy=policy,
        checkout=checkout,
        apply=True,
    )

    assert outcome.status == "pull-request-updated"
    assert client.updated
    assert not client.recreated_branch
    assert not client.force_pushed


def test_existing_compliant_conflicted_pull_request_is_recreated(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    checkout = tmp_path / "repository"
    checkout.mkdir()
    (checkout / ".bazelversion").write_text("8.5.0\n")
    policy = Policy(
        "example",
        "Example",
        None,
        None,
        (EnsureLine(Path(".bazelversion"), "8.6.0", ()),),
    )
    client = ImplicitRecreateClient(mergeable="CONFLICTING", body="stale body")
    monkeypatch.setattr(
        runner,
        "restore_synced_default_branch",
        lambda *, checkout: (checkout / ".bazelversion").write_text("8.5.0\n"),
    )

    outcome = _run_repository(
        client=client,
        org="eclipse-score",
        repository="candidate",
        default_branch="main",
        policy=policy,
        checkout=checkout,
        apply=True,
    )

    assert outcome.status == "pull-request-recreated"
    assert client.recreated_branch
    assert client.force_pushed
    assert client.updated


def test_existing_compliant_pull_request_is_left_alone_with_current_body(
    tmp_path: Path,
) -> None:
    checkout = tmp_path / "repository"
    checkout.mkdir()
    (checkout / ".bazelversion").write_text("8.5.0\n")
    policy = Policy(
        "example",
        "Example",
        None,
        None,
        (EnsureLine(Path(".bazelversion"), "8.6.0", ()),),
    )
    change = runner.evaluate_policy(checkout, policy).changes
    client = ImplicitRecreateClient(
        mergeable="MERGEABLE",
        body=_pull_request_body(policy, change, head_oid="a" * 40),
    )

    outcome = _run_repository(
        client=client,
        org="eclipse-score",
        repository="candidate",
        default_branch="main",
        policy=policy,
        checkout=checkout,
        apply=True,
    )

    assert outcome.status == "pull-request-open"
    assert not client.updated
    assert not client.recreated_branch


class RecreateClient:
    pull_request = PullRequest(
        1, "https://github.example/eclipse-score/candidate/pull/1", "a" * 40
    )

    def __init__(self, *, has_changes: bool) -> None:
        self._has_changes = has_changes
        self.recreated_branch = False
        self.force_pushed = False
        self.updated = False

    def find_open_pull_request(self, **_: object) -> PullRequest:
        return self.pull_request

    def verify_policy_branch_head(self, **_: object) -> None:
        pass

    def recreate_policy_branch(self, **_: object) -> None:
        self.recreated_branch = True

    def has_changes(self, **_: object) -> bool:
        return self._has_changes

    def commit_and_force_push(self, **_: object) -> CommitResult:
        self.force_pushed = True
        return CommitResult("b" * 40)

    def update_pull_request(self, **_: object) -> None:
        self.updated = True


class ImplicitRecreateClient:
    def __init__(self, *, mergeable: str, body: str) -> None:
        self.pull_request = PullRequest(
            1,
            "https://github.example/eclipse-score/candidate/pull/1",
            "a" * 40,
            body=body,
            mergeable=mergeable,
        )
        self.recreated_branch = False
        self.force_pushed = False
        self.updated = False

    def find_open_pull_request(self, **_: object) -> PullRequest:
        return self.pull_request

    def verify_policy_branch_head(self, **_: object) -> None:
        pass

    def switch_to_policy_branch(self, *, checkout: Path, **_: object) -> None:
        (checkout / ".bazelversion").write_text("8.6.0\n")

    def recreate_policy_branch(self, **_: object) -> None:
        self.recreated_branch = True

    def has_changes(self, **_: object) -> bool:
        return self.recreated_branch

    def commit_and_force_push(self, **_: object) -> CommitResult:
        self.force_pushed = True
        return CommitResult("b" * 40)

    def update_pull_request(self, **_: object) -> None:
        self.updated = True
