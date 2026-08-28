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

from pathlib import Path

import pytest

from repo_cache.src import sync as sync_module
from repo_cache.src.errors import RepoCacheError
from repo_cache.src.models import Repository
from repo_cache.src.sync import SyncOutcome, sync_org


def _stub_listing(monkeypatch, repositories: tuple[Repository, ...]) -> None:
    monkeypatch.setattr(sync_module, "ensure_authenticated", lambda: None)
    monkeypatch.setattr(sync_module, "list_repositories", lambda *, org: repositories)


def test_sync_org_syncs_every_active_repository(monkeypatch, tmp_path: Path) -> None:
    repositories = (Repository("alpha", "main"), Repository("beta", "main"))
    _stub_listing(monkeypatch, repositories)
    synced: list[str] = []
    monkeypatch.setattr(
        sync_module,
        "sync_default_branch",
        lambda *, repository, branch, destination: synced.append(repository),
    )

    report = sync_org(org="acme", cache_dir=tmp_path)

    assert synced == ["acme/alpha", "acme/beta"]
    assert [outcome.repository.name for outcome in report.outcomes] == ["alpha", "beta"]
    assert report.failures == ()


def test_sync_org_excludes_archived_repositories_by_default(
    monkeypatch, tmp_path: Path
) -> None:
    repositories = (
        Repository("alpha", "main"),
        Repository("retired", "main", archived=True),
    )
    _stub_listing(monkeypatch, repositories)
    monkeypatch.setattr(sync_module, "sync_default_branch", lambda **_: None)

    report = sync_org(org="acme", cache_dir=tmp_path)

    assert [outcome.repository.name for outcome in report.outcomes] == ["alpha"]


def test_sync_org_includes_archived_repositories_when_requested(
    monkeypatch, tmp_path: Path
) -> None:
    repositories = (
        Repository("alpha", "main"),
        Repository("retired", "main", archived=True),
    )
    _stub_listing(monkeypatch, repositories)
    monkeypatch.setattr(sync_module, "sync_default_branch", lambda **_: None)

    report = sync_org(org="acme", cache_dir=tmp_path, include_archived=True)

    assert {outcome.repository.name for outcome in report.outcomes} == {
        "alpha",
        "retired",
    }


def test_sync_org_skips_repositories_without_a_default_branch(
    monkeypatch, tmp_path: Path
) -> None:
    repositories = (Repository("empty", None),)
    _stub_listing(monkeypatch, repositories)

    def fail_sync(**_: object) -> None:
        raise AssertionError("must not sync a repository without a default branch")

    monkeypatch.setattr(sync_module, "sync_default_branch", fail_sync)

    report = sync_org(org="acme", cache_dir=tmp_path)

    assert report.outcomes == (
        SyncOutcome(repositories[0], tmp_path / "acme" / "empty", None),
    )


def test_sync_org_records_a_sync_failure_without_aborting_others(
    monkeypatch, tmp_path: Path
) -> None:
    repositories = (Repository("broken", "main"), Repository("fine", "main"))
    _stub_listing(monkeypatch, repositories)

    def fake_sync(*, repository: str, branch: str, destination: Path) -> None:
        if repository.endswith("broken"):
            raise RepoCacheError("checkout failed")

    monkeypatch.setattr(sync_module, "sync_default_branch", fake_sync)

    report = sync_org(org="acme", cache_dir=tmp_path)

    assert len(report.failures) == 1
    assert report.failures[0].repository.name == "broken"
    assert report.failures[0].error == "checkout failed"


def test_sync_org_rejects_an_unknown_repository_filter(
    monkeypatch, tmp_path: Path
) -> None:
    _stub_listing(monkeypatch, (Repository("alpha", "main"),))

    with pytest.raises(
        RepoCacheError, match="repository filter not found in organization: missing"
    ):
        sync_org(org="acme", cache_dir=tmp_path, repos=("missing",))


def test_sync_org_rejects_fewer_than_one_worker(tmp_path: Path) -> None:
    with pytest.raises(RepoCacheError, match="at least 1"):
        sync_org(org="acme", cache_dir=tmp_path, workers=0)


def test_sync_org_reports_progress(monkeypatch, tmp_path: Path) -> None:
    _stub_listing(monkeypatch, (Repository("alpha", "main"),))
    monkeypatch.setattr(sync_module, "sync_default_branch", lambda **_: None)
    messages: list[str] = []

    sync_org(org="acme", cache_dir=tmp_path, progress=messages.append)

    assert any("Synchronizing 1 checkout(s)" in message for message in messages)
