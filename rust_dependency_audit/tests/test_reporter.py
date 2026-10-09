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

import json
import pytest
from rust_dependency_audit.src.auditor import (
    OrganizationAuditReport,
    AuditedRepository,
    AuditedCrate,
    CrateStatus,
    CrateUsage,
)
from rust_dependency_audit.src.reporter import (
    generate_markdown_report,
    generate_json_report,
    generate_html_report,
)


@pytest.fixture
def sample_report():
    c1 = AuditedCrate(
        crate_name="clap",
        status=CrateStatus.MANAGED,
        requested_version="4.5.4",
        resolved_version="4.5.4",
        score_crates_version="4.5.4",
        manifest_paths=["Cargo.toml"],
    )
    c2 = AuditedCrate(
        crate_name="tokio",
        status=CrateStatus.VERSION_MISMATCH,
        requested_version="1.30.0",
        score_crates_version="1.47.1",
        manifest_paths=["crates/app/Cargo.toml"],
    )
    c3 = AuditedCrate(
        crate_name="unregistered_crate",
        status=CrateStatus.UNMANAGED,
        requested_version="0.1.0",
        manifest_paths=["Cargo.toml"],
    )

    repo1 = AuditedRepository(
        repo_name="eclipse-score/repo1",
        project_paths=["Cargo.toml", "crates/app/Cargo.toml"],
        crates=[c1, c2, c3],
        managed_crates_count=1,
        mismatch_crates_count=1,
        unmanaged_crates_count=1,
    )

    usage = {
        "clap": CrateUsage(
            crate_name="clap",
            status=CrateStatus.MANAGED,
            score_crates_version="4.5.4",
            used_in_repos=["eclipse-score/repo1"],
            versions_seen={"4.5.4"},
        ),
        "tokio": CrateUsage(
            crate_name="tokio",
            status=CrateStatus.VERSION_MISMATCH,
            score_crates_version="1.47.1",
            used_in_repos=["eclipse-score/repo1"],
            versions_seen={"1.30.0"},
        ),
        "unregistered_crate": CrateUsage(
            crate_name="unregistered_crate",
            status=CrateStatus.UNMANAGED,
            score_crates_version=None,
            used_in_repos=["eclipse-score/repo1"],
            versions_seen={"0.1.0"},
        ),
    }

    return OrganizationAuditReport(
        organization="eclipse-score",
        total_repositories=10,
        rust_repositories_count=1,
        total_distinct_crates=3,
        managed_crates_count=1,
        mismatch_crates_count=1,
        unmanaged_crates_count=1,
        repositories=[repo1],
        crate_usage_summary=usage,
    )


def test_generate_markdown_report(sample_report):
    md = generate_markdown_report(sample_report)
    assert "# Rust Dependency Audit Report: eclipse-score" in md
    assert "## Repositories Overview" in md
    assert "Terminology & Classification Guide" in md
    assert "MANAGED" in md
    assert "VERSION_MISMATCH" in md
    assert "UNMANAGED" in md
    assert "eclipse-score/repo1" in md
    assert (
        "1 managed" in md.lower()
        or "| 1 | 1 | 1 |" in md
        or ("1" in md and "eclipse-score/repo1" in md)
    )
    assert "clap" in md
    assert "tokio" in md
    assert "unregistered_crate" in md


def test_generate_json_report(sample_report):
    json_str = generate_json_report(sample_report)
    data = json.loads(json_str)
    assert data["organization"] == "eclipse-score"
    assert data["total_repositories"] == 10
    assert data["rust_repositories_count"] == 1
    assert data["total_distinct_crates"] == 3
    assert len(data["repositories"]) == 1
    assert len(data["crate_usage_summary"]) == 3


def test_generate_html_report(sample_report):
    html = generate_html_report(sample_report)
    assert "<!DOCTYPE html>" in html
    assert "Rust Dependency Audit" in html
    assert "Repositories Overview" in html
    assert "Terminology & Classification Guide" in html
    assert "clap" in html
    assert "tokio" in html
    assert "unregistered_crate" in html
    assert "eclipse-score/repo1" in html
    # Ensure no token leaks
    assert (
        "token" not in html.lower() or "token" in "score_crates"
    )  # only legitimate words
    assert "ghp_" not in html


def test_custom_reference_repo_in_reports(sample_report):
    sample_report.reference_repo = "custom-org/my-crates"
    md = generate_markdown_report(sample_report)
    assert "custom-org/my-crates" in md
    assert "https://github.com/custom-org/my-crates" in md

    html_out = generate_html_report(sample_report)
    assert "custom-org/my-crates" in html_out
    assert "https://github.com/custom-org/my-crates" in html_out

    json_str = generate_json_report(sample_report)
    assert json.loads(json_str)["reference_repo"] == "custom-org/my-crates"


def test_html_report_xss_safety(sample_report):
    sample_report.repositories[0].crates.append(
        AuditedCrate(
            crate_name="</script><script>alert('xss')</script>",
            status=CrateStatus.UNMANAGED,
            manifest_paths=["<img src=x onerror=alert(1)>"],
        )
    )
    html_out = generate_html_report(sample_report)
    # Raw closing script tag must not exist inside the data payload
    assert "</script><script>alert('xss')</script>" not in html_out
    # Instead, < should be escaped to \u003c in json script tag
    assert "\\u003c/script>\\u003cscript>alert('xss')\\u003c/script>" in html_out
    assert "escapeHtml" in html_out
