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

"""Auditor engine cross-referencing repository crates with score-crates."""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum

from .cargo_parser import CargoLockPackage, CargoTomlPackage
from .score_crates import ScoreCrateSpec, ScoreCratesReference


class CrateStatus(str, Enum):
    """Classification of crate dependency against score-crates."""

    MANAGED = "MANAGED"
    VERSION_MISMATCH = "VERSION_MISMATCH"
    UNMANAGED = "UNMANAGED"


@dataclass
class AuditedCrate:
    """Represents an audited crate used within a repository."""

    crate_name: str
    status: CrateStatus
    requested_version: str | None = None
    resolved_version: str | None = None
    score_crates_version: str | None = None
    features: list[str] = field(default_factory=list)
    dep_types: list[str] = field(default_factory=list)
    manifest_paths: list[str] = field(default_factory=list)


@dataclass
class RepositoryAuditData:
    """Input data for a repository collected from GitHub."""

    name: str
    is_archived: bool = False
    cargo_tomls: list[CargoTomlPackage] = field(default_factory=list)
    cargo_locks: list[CargoLockPackage] = field(default_factory=list)


@dataclass
class AuditedRepository:
    """Result of auditing a single repository."""

    repo_name: str
    is_archived: bool = False
    project_paths: list[str] = field(default_factory=list)
    crates: list[AuditedCrate] = field(default_factory=list)
    managed_crates_count: int = 0
    mismatch_crates_count: int = 0
    unmanaged_crates_count: int = 0


@dataclass
class CrateUsage:
    """Summary of usage for a single crate across the organization."""

    crate_name: str
    status: CrateStatus
    score_crates_version: str | None = None
    used_in_repos: list[str] = field(default_factory=list)
    versions_seen: set[str] = field(default_factory=set)


@dataclass
class OrganizationAuditReport:
    """Consolidated organization-wide audit report."""

    organization: str = ""
    reference_repo: str = "eclipse-score/score-crates"
    total_repositories: int = 0
    rust_repositories_count: int = 0
    total_distinct_crates: int = 0
    managed_crates_count: int = 0
    mismatch_crates_count: int = 0
    unmanaged_crates_count: int = 0
    repositories: list[AuditedRepository] = field(default_factory=list)
    crate_usage_summary: dict[str, CrateUsage] = field(default_factory=dict)


def _parse_requirement(req: str) -> tuple[str, str]:
    """Parses a version requirement string into (operator, version)."""
    s = req.strip()
    for op in (">=", "<=", "!=", "=", "~", "^", ">", "<"):
        if s.startswith(op):
            return op, s[len(op) :].strip()
    # In Cargo, a requirement without an operator is a caret requirement
    return "^", s


def _requirements_match(req_ver: str | None, ref_ver: str | None) -> bool:
    """Compares a declared requirement with score-crates reference requirement."""
    if not req_ver or not ref_ver:
        return True

    op_req, clean_req = _parse_requirement(req_ver)
    op_ref, clean_ref = _parse_requirement(ref_ver)

    return op_req == op_ref and clean_req == clean_ref


def _resolved_matches_spec(res_ver: str | None, spec_ver: str | None) -> bool:
    """Compares a resolved lockfile version with score-crates reference version."""
    if not res_ver or not spec_ver:
        return True

    _, clean_spec = _parse_requirement(spec_ver)
    return res_ver.strip() == clean_spec


def _normalize_git_url(url: str | None) -> str | None:
    """Normalizes a git repository URL for comparison."""
    if not url:
        return None
    u = url.strip().rstrip("/")
    if u.endswith(".git"):
        u = u[:-4]
    return u.lower()


def _git_matches_spec(
    git_urls: set[str], git_revs: set[str], spec: ScoreCrateSpec
) -> bool:
    """Checks whether declared git dependency parameters match score-crates git spec."""
    if spec.git:
        if not git_urls:
            return False
        norm_spec_git = _normalize_git_url(spec.git)
        for u in git_urls:
            if _normalize_git_url(u) != norm_spec_git:
                return False
        if spec.rev:
            if not git_revs:
                return False
            for r in git_revs:
                if r.strip() != spec.rev.strip():
                    return False
        return True
    else:
        # spec is not git-based, repository dependency should not be git-based
        if git_urls:
            return False
        return True


def audit_repository(
    repo_data: RepositoryAuditData, score_crates_ref: ScoreCratesReference
) -> AuditedRepository:
    """Audits a single repository against score-crates."""
    project_paths: list[str] = []
    internal_crate_names: set[str] = set()

    for toml in repo_data.cargo_tomls:
        if toml.path and toml.path not in project_paths:
            project_paths.append(toml.path)
        if toml.name and toml.name not in ("workspace_root", "unknown"):
            internal_crate_names.add(toml.name)

    # Build lockfile lookup: crate_name -> set of resolved versions
    lock_versions: dict[str, set[str]] = {}
    for lock in repo_data.cargo_locks:
        if lock.name not in lock_versions:
            lock_versions[lock.name] = set()
        if lock.version:
            lock_versions[lock.name].add(lock.version)

    # Map crate_name -> collected info across all manifests
    collected_crates: dict[str, dict] = {}

    for toml in repo_data.cargo_tomls:
        for dep in toml.dependencies:
            # Exclude internal path dependencies
            if dep.is_path or dep.name in internal_crate_names:
                continue

            crate_name = dep.name
            if crate_name not in collected_crates:
                collected_crates[crate_name] = {
                    "version_reqs": set(),
                    "git_urls": set(),
                    "git_revs": set(),
                    "features": set(),
                    "dep_types": set(),
                    "manifests": set(),
                }

            entry = collected_crates[crate_name]
            if dep.version_req:
                entry["version_reqs"].add(dep.version_req)
            if dep.git_url:
                entry["git_urls"].add(dep.git_url)
            if dep.git_rev:
                entry["git_revs"].add(dep.git_rev)
            entry["features"].update(dep.features)
            entry["dep_types"].add(dep.dep_type)
            if toml.path:
                entry["manifests"].add(toml.path)

    # If there are crates in lockfile not in toml (e.g. lockfile-only scan)
    if not collected_crates and repo_data.cargo_locks:
        for lock in repo_data.cargo_locks:
            if lock.name in internal_crate_names:
                continue
            if lock.name not in collected_crates:
                collected_crates[lock.name] = {
                    "version_reqs": set(),
                    "git_urls": set(),
                    "git_revs": set(),
                    "features": set(),
                    "dep_types": {"locked"},
                    "manifests": {"Cargo.lock"},
                }
            if lock.version:
                collected_crates[lock.name]["version_reqs"].add(lock.version)

    audited_crates: list[AuditedCrate] = []
    managed_count = 0
    mismatch_count = 0
    unmanaged_count = 0

    for crate_name, info in sorted(collected_crates.items()):
        resolved_versions = sorted(lock_versions.get(crate_name, set()))
        requested_versions = sorted(info["version_reqs"])

        resolved_ver_str = ", ".join(resolved_versions) if resolved_versions else None
        requested_ver_str = (
            ", ".join(requested_versions) if requested_versions else resolved_ver_str
        )

        spec = score_crates_ref.get_spec(crate_name)
        score_ver = spec.version if spec else None

        if not score_crates_ref.contains_crate(crate_name) or spec is None:
            status = CrateStatus.UNMANAGED
            unmanaged_count += 1
        else:
            # Check git specs
            git_ok = _git_matches_spec(info["git_urls"], info["git_revs"], spec)

            # Check declared version requirements
            req_ok = True
            if spec.version:
                if not requested_versions and not resolved_versions:
                    req_ok = False
                for r in requested_versions:
                    if not _requirements_match(r, spec.version):
                        req_ok = False
                        break

            # Check resolved lockfile versions against spec
            res_ok = True
            if spec.version and resolved_versions:
                for rv in resolved_versions:
                    if not _resolved_matches_spec(rv, spec.version):
                        res_ok = False
                        break

            if not git_ok or not req_ok or not res_ok:
                status = CrateStatus.VERSION_MISMATCH
                mismatch_count += 1
            else:
                status = CrateStatus.MANAGED
                managed_count += 1

        score_display_ver = score_ver
        if not score_display_ver and spec and spec.git:
            score_display_ver = f"git:{spec.rev[:8]}" if spec.rev else f"git:{spec.git}"

        audited_crates.append(
            AuditedCrate(
                crate_name=crate_name,
                status=status,
                requested_version=requested_ver_str,
                resolved_version=resolved_ver_str,
                score_crates_version=score_display_ver,
                features=sorted(info["features"]),
                dep_types=sorted(info["dep_types"]),
                manifest_paths=sorted(info["manifests"]),
            )
        )

    return AuditedRepository(
        repo_name=repo_data.name,
        is_archived=repo_data.is_archived,
        project_paths=project_paths,
        crates=audited_crates,
        managed_crates_count=managed_count,
        mismatch_crates_count=mismatch_count,
        unmanaged_crates_count=unmanaged_count,
    )


def audit_organization(
    repos_data: list[RepositoryAuditData],
    score_crates_ref: ScoreCratesReference,
    reference_repo: str = "eclipse-score/score-crates",
) -> OrganizationAuditReport:
    """Audits multiple repositories and produces an aggregated organization report."""
    audited_repos: list[AuditedRepository] = []
    crate_usage: dict[str, CrateUsage] = {}

    for repo_data in repos_data:
        audited_repo = audit_repository(repo_data, score_crates_ref)
        if audited_repo.crates or audited_repo.project_paths:
            audited_repos.append(audited_repo)

        for c in audited_repo.crates:
            if c.crate_name not in crate_usage:
                crate_usage[c.crate_name] = CrateUsage(
                    crate_name=c.crate_name,
                    status=c.status,
                    score_crates_version=c.score_crates_version,
                    used_in_repos=[audited_repo.repo_name],
                    versions_seen=set(
                        filter(
                            None,
                            [
                                *(
                                    c.requested_version.split(", ")
                                    if c.requested_version
                                    else []
                                ),
                                *(
                                    c.resolved_version.split(", ")
                                    if c.resolved_version
                                    else []
                                ),
                            ],
                        )
                    ),
                )
            else:
                usage = crate_usage[c.crate_name]
                if audited_repo.repo_name not in usage.used_in_repos:
                    usage.used_in_repos.append(audited_repo.repo_name)
                if c.requested_version:
                    for v in c.requested_version.split(", "):
                        usage.versions_seen.add(v)
                if c.resolved_version:
                    for v in c.resolved_version.split(", "):
                        usage.versions_seen.add(v)
                # Escalate status if any mismatch
                if c.status == CrateStatus.VERSION_MISMATCH:
                    usage.status = CrateStatus.VERSION_MISMATCH

    total_managed = sum(
        1 for u in crate_usage.values() if u.status == CrateStatus.MANAGED
    )
    total_mismatch = sum(
        1 for u in crate_usage.values() if u.status == CrateStatus.VERSION_MISMATCH
    )
    total_unmanaged = sum(
        1 for u in crate_usage.values() if u.status == CrateStatus.UNMANAGED
    )

    return OrganizationAuditReport(
        organization="",
        reference_repo=reference_repo,
        total_repositories=len(repos_data),
        rust_repositories_count=len(audited_repos),
        total_distinct_crates=len(crate_usage),
        managed_crates_count=total_managed,
        mismatch_crates_count=total_mismatch,
        unmanaged_crates_count=total_unmanaged,
        repositories=audited_repos,
        crate_usage_summary=crate_usage,
    )
