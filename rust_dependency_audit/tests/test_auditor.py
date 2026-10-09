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

import pytest
from rust_dependency_audit.src.cargo_parser import (
    CargoTomlPackage,
    CargoLockPackage,
    DeclaredDependency,
)
from rust_dependency_audit.src.score_crates import (
    ScoreCratesReference,
    ScoreCrateSpec,
)
from rust_dependency_audit.src.auditor import (
    RepositoryAuditData,
    CrateStatus,
    audit_repository,
    audit_organization,
)


@pytest.fixture
def sample_score_crates():
    crates = {
        "clap": ScoreCrateSpec(package="clap", version="4.5.4"),
        "tokio": ScoreCrateSpec(package="tokio", version="1.47.1"),
        "log": ScoreCrateSpec(package="log", version="0.4.27"),
    }
    aliases = {"clap": "@crate_index//:clap"}
    return ScoreCratesReference(crates=crates, aliases=aliases)


def test_audit_repository_managed_and_unmanaged(sample_score_crates):
    pkg1 = CargoTomlPackage(
        name="my_crate",
        version="0.1.0",
        path="crates/my_crate/Cargo.toml",
        dependencies=[
            DeclaredDependency(name="clap", version_req="4.5.4"),
            DeclaredDependency(name="tokio", version_req="1.30.0"),  # mismatch
            DeclaredDependency(
                name="custom_external", version_req="0.9.0"
            ),  # unmanaged
            DeclaredDependency(name="local_helper", is_path=True),  # internal path dep
        ],
    )

    lock1 = CargoLockPackage(
        name="clap",
        version="4.5.4",
    )
    lock2 = CargoLockPackage(
        name="tokio",
        version="1.30.0",
    )
    lock3 = CargoLockPackage(
        name="custom_external",
        version="0.9.0",
    )

    repo_data = RepositoryAuditData(
        name="eclipse-score/my_repo",
        is_archived=False,
        cargo_tomls=[pkg1],
        cargo_locks=[lock1, lock2, lock3],
    )

    result = audit_repository(repo_data, sample_score_crates)

    assert result.repo_name == "eclipse-score/my_repo"
    assert len(result.crates) == 3  # local_helper excluded

    crates_by_name = {c.crate_name: c for c in result.crates}

    assert crates_by_name["clap"].status == CrateStatus.MANAGED
    assert crates_by_name["clap"].score_crates_version == "4.5.4"

    assert crates_by_name["tokio"].status == CrateStatus.VERSION_MISMATCH
    assert crates_by_name["tokio"].requested_version == "1.30.0"
    assert crates_by_name["tokio"].score_crates_version == "1.47.1"

    assert crates_by_name["custom_external"].status == CrateStatus.UNMANAGED
    assert crates_by_name["custom_external"].score_crates_version is None


def test_audit_organization_aggregation(sample_score_crates):
    repo1 = RepositoryAuditData(
        name="eclipse-score/repo1",
        cargo_tomls=[
            CargoTomlPackage(
                name="c1",
                path="Cargo.toml",
                dependencies=[DeclaredDependency(name="clap", version_req="4.5.4")],
            )
        ],
    )
    repo2 = RepositoryAuditData(
        name="eclipse-score/repo2",
        cargo_tomls=[
            CargoTomlPackage(
                name="c2",
                path="Cargo.toml",
                dependencies=[
                    DeclaredDependency(name="clap", version_req="4.5.4"),
                    DeclaredDependency(name="serde_unknown", version_req="1.0.0"),
                ],
            )
        ],
    )

    report = audit_organization([repo1, repo2], sample_score_crates)

    assert report.total_repositories == 2
    assert report.rust_repositories_count == 2
    assert report.total_distinct_crates == 2
    assert report.managed_crates_count == 1
    assert report.unmanaged_crates_count == 1
    assert "clap" in report.crate_usage_summary
    assert len(report.crate_usage_summary["clap"].used_in_repos) == 2


def test_audit_exact_pin_operator_semantics():
    # score-crates exact pin `=0.2.186` for QNX
    score_ref = ScoreCratesReference(
        crates={
            "libc": ScoreCrateSpec(package="libc", version="=0.2.186"),
            "clap": ScoreCrateSpec(package="clap", version="4.5.4"),
        }
    )

    # Repo 1 uses non-exact "0.2.186" (caret) -> should be VERSION_MISMATCH
    repo_caret = RepositoryAuditData(
        name="eclipse-score/repo_caret",
        cargo_tomls=[
            CargoTomlPackage(
                name="pkg",
                path="Cargo.toml",
                dependencies=[DeclaredDependency(name="libc", version_req="0.2.186")],
            )
        ],
    )
    res_caret = audit_repository(repo_caret, score_ref)
    assert res_caret.crates[0].status == CrateStatus.VERSION_MISMATCH

    # Repo 2 uses exact "=0.2.186" -> should be MANAGED
    repo_exact = RepositoryAuditData(
        name="eclipse-score/repo_exact",
        cargo_tomls=[
            CargoTomlPackage(
                name="pkg",
                path="Cargo.toml",
                dependencies=[DeclaredDependency(name="libc", version_req="=0.2.186")],
            )
        ],
        cargo_locks=[CargoLockPackage(name="libc", version="0.2.186")],
    )
    res_exact = audit_repository(repo_exact, score_ref)
    assert res_exact.crates[0].status == CrateStatus.MANAGED

    # Repo 3 uses caret "^4.5.4" against caret "4.5.4" in score-crates -> should be MANAGED
    repo_clap = RepositoryAuditData(
        name="eclipse-score/repo_clap",
        cargo_tomls=[
            CargoTomlPackage(
                name="pkg",
                path="Cargo.toml",
                dependencies=[DeclaredDependency(name="clap", version_req="^4.5.4")],
            )
        ],
        cargo_locks=[CargoLockPackage(name="clap", version="4.5.4")],
    )
    res_clap = audit_repository(repo_clap, score_ref)
    assert res_clap.crates[0].status == CrateStatus.MANAGED


def test_audit_lockfile_multiple_versions_and_mismatch():
    score_ref = ScoreCratesReference(
        crates={"syn": ScoreCrateSpec(package="syn", version="2.0.68")}
    )

    # Cargo.lock has two versions of syn: 1.0.109 and 2.0.68
    repo = RepositoryAuditData(
        name="eclipse-score/repo_multi_lock",
        cargo_tomls=[
            CargoTomlPackage(
                name="pkg",
                path="Cargo.toml",
                dependencies=[DeclaredDependency(name="syn", version_req="2.0.68")],
            )
        ],
        cargo_locks=[
            CargoLockPackage(name="syn", version="1.0.109"),
            CargoLockPackage(name="syn", version="2.0.68"),
        ],
    )

    res = audit_repository(repo, score_ref)
    assert len(res.crates) == 1
    crate = res.crates[0]
    # Because lockfile contains 1.0.109 which doesn't match 2.0.68, it's a mismatch
    assert crate.status == CrateStatus.VERSION_MISMATCH
    assert "1.0.109" in crate.resolved_version
    assert "2.0.68" in crate.resolved_version


def test_audit_multiple_manifests_different_requirements():
    score_ref = ScoreCratesReference(
        crates={"serde": ScoreCrateSpec(package="serde", version="1.0.200")}
    )

    # Two crates in same repo: one requests 1.0.100, one requests 1.0.200
    repo = RepositoryAuditData(
        name="eclipse-score/multi_manifest",
        cargo_tomls=[
            CargoTomlPackage(
                name="crate_a",
                path="crates/a/Cargo.toml",
                dependencies=[DeclaredDependency(name="serde", version_req="1.0.100")],
            ),
            CargoTomlPackage(
                name="crate_b",
                path="crates/b/Cargo.toml",
                dependencies=[DeclaredDependency(name="serde", version_req="1.0.200")],
            ),
        ],
    )

    res = audit_repository(repo, score_ref)
    assert len(res.crates) == 1
    crate = res.crates[0]
    # Both versions are collected and reported
    assert "1.0.100" in crate.requested_version
    assert "1.0.200" in crate.requested_version
    # One version mismatched, so status must be VERSION_MISMATCH
    assert crate.status == CrateStatus.VERSION_MISMATCH


def test_audit_git_dependencies():
    score_ref = ScoreCratesReference(
        crates={
            "iceoryx2-qnx8": ScoreCrateSpec(
                package="iceoryx2-qnx8",
                git="https://github.com/qorix-group/iceoryx2.git",
                rev="9f5622f554de48a7a296e1a5a71200b01e35a502",
            )
        }
    )

    # Matches git repo and rev
    repo_ok = RepositoryAuditData(
        name="eclipse-score/repo_git_ok",
        cargo_tomls=[
            CargoTomlPackage(
                name="pkg",
                path="Cargo.toml",
                dependencies=[
                    DeclaredDependency(
                        name="iceoryx2-qnx8",
                        git_url="https://github.com/qorix-group/iceoryx2",
                        git_rev="9f5622f554de48a7a296e1a5a71200b01e35a502",
                    )
                ],
            )
        ],
    )
    res_ok = audit_repository(repo_ok, score_ref)
    assert res_ok.crates[0].status == CrateStatus.MANAGED

    # Wrong rev
    repo_bad_rev = RepositoryAuditData(
        name="eclipse-score/repo_git_bad_rev",
        cargo_tomls=[
            CargoTomlPackage(
                name="pkg",
                path="Cargo.toml",
                dependencies=[
                    DeclaredDependency(
                        name="iceoryx2-qnx8",
                        git_url="https://github.com/qorix-group/iceoryx2",
                        git_rev="deadbeef12345678",
                    )
                ],
            )
        ],
    )
    res_bad_rev = audit_repository(repo_bad_rev, score_ref)
    assert res_bad_rev.crates[0].status == CrateStatus.VERSION_MISMATCH
