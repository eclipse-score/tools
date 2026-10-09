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

"""Command-line interface for the Rust Dependency Audit tool."""

from __future__ import annotations

import argparse
from pathlib import Path
import sys

from .auditor import RepositoryAuditData, audit_organization
from .cargo_parser import parse_cargo_lock, parse_cargo_toml
from .github_client import GitHubClient
from .reporter import (
    generate_html_report,
    generate_json_report,
    generate_markdown_report,
)
from .score_crates import build_score_crates_reference


def _collect_local_repo_data(repo_path: Path) -> RepositoryAuditData:
    """Collects Cargo files from a local repository directory."""
    cargo_tomls = []
    cargo_locks = []

    for path in repo_path.rglob("*"):
        if ".git" in path.parts:
            continue
        rel_path = str(path.relative_to(repo_path))
        if path.name == "Cargo.toml" and path.is_file():
            try:
                cargo_tomls.append(
                    parse_cargo_toml(path.read_text(encoding="utf-8"), path=rel_path)
                )
            except Exception as e:
                print(f"[!] Warning: Failed to parse {path}: {e}", file=sys.stderr)
        elif path.name == "Cargo.lock" and path.is_file():
            try:
                cargo_locks.extend(
                    parse_cargo_lock(path.read_text(encoding="utf-8"), path=rel_path)
                )
            except Exception as e:
                print(f"[!] Warning: Failed to parse {path}: {e}", file=sys.stderr)

    return RepositoryAuditData(
        name=repo_path.name,
        cargo_tomls=cargo_tomls,
        cargo_locks=cargo_locks,
    )


def main(argv: list[str] | None = None) -> int:
    """CLI main entry point."""
    parser = argparse.ArgumentParser(
        prog="score-rust-dependency-audit",
        description="Audit Rust dependencies across repositories and cross-reference with score-crates.",
    )
    parser.add_argument(
        "--org",
        default="eclipse-score",
        help="GitHub organization to audit (default: eclipse-score)",
    )
    parser.add_argument(
        "--reference-repo",
        default="eclipse-score/score-crates",
        help="Central score-crates repository (default: eclipse-score/score-crates)",
    )
    parser.add_argument(
        "--output-dir",
        default="site",
        help="Directory to save generated reports (default: site)",
    )
    parser.add_argument(
        "--include-archived",
        action="store_true",
        help="Include archived repositories in the audit",
    )
    parser.add_argument(
        "--markdown-output",
        help="Custom path to write Markdown report",
    )
    parser.add_argument(
        "--json-output",
        help="Custom path to write JSON report",
    )
    parser.add_argument(
        "--html-output",
        help="Custom path to write HTML report",
    )
    parser.add_argument(
        "--local-scan-dir",
        help="Scan local directory of repositories instead of querying GitHub API",
    )
    parser.add_argument(
        "--local-reference-dir",
        help="Directory containing local score-crates MODULE.bazel and BUILD files",
    )

    args = parser.parse_args(argv)

    # 1. Build score-crates reference
    print(f"[*] Loading central reference: {args.reference_repo}...")
    if args.local_reference_dir:
        ref_p = Path(args.local_reference_dir)
        mod_bazel = (
            (ref_p / "MODULE.bazel").read_text(encoding="utf-8")
            if (ref_p / "MODULE.bazel").exists()
            else ""
        )
        build_bazel = (
            (ref_p / "BUILD").read_text(encoding="utf-8")
            if (ref_p / "BUILD").exists()
            else ""
        )
        score_crates_ref = build_score_crates_reference(mod_bazel, build_bazel)
    else:
        client = GitHubClient()
        owner, repo = args.reference_repo.split("/", 1)
        mod_bazel = client.get_file_content(owner, repo, "MODULE.bazel") or ""
        build_bazel = client.get_file_content(owner, repo, "BUILD") or ""
        score_crates_ref = build_score_crates_reference(mod_bazel, build_bazel)

    ref_label = args.reference_repo or (
        str(args.local_reference_dir)
        if args.local_reference_dir
        else "eclipse-score/score-crates"
    )
    if not score_crates_ref.crates and not score_crates_ref.aliases:
        print(
            f"[!] Error: No crates found in reference '{ref_label}'. "
            "Audit aborted to prevent false organization-wide results.",
            file=sys.stderr,
        )
        return 1

    print(
        f"    - Discovered {len(score_crates_ref.crates)} approved crates in score-crates."
    )

    # 2. Collect repository data
    repos_data: list[RepositoryAuditData] = []

    if args.local_scan_dir:
        base_p = Path(args.local_scan_dir)
        print(f"[*] Scanning local repositories in {base_p}...")
        for child in sorted(base_p.iterdir()):
            if child.is_dir() and not child.name.startswith("."):
                repos_data.append(_collect_local_repo_data(child))
    else:
        client = GitHubClient()
        print(f"[*] Fetching repository list for organization: {args.org}...")
        gh_repos = client.get_organization_repositories(
            args.org, include_archived=args.include_archived
        )
        print(f"    - Found {len(gh_repos)} repositories. Inspecting trees...")

        for r in gh_repos:
            repo_name = r["name"]
            default_branch = r.get("default_branch", "main")
            is_archived = r.get("archived", False)

            # Query tree to find Cargo.toml and Cargo.lock files
            tree_paths = client.get_repo_git_tree(
                args.org, repo_name, branch=default_branch
            )
            cargo_toml_paths = [p for p in tree_paths if p.endswith("Cargo.toml")]
            cargo_lock_paths = [p for p in tree_paths if p.endswith("Cargo.lock")]

            if not cargo_toml_paths and not cargo_lock_paths:
                repos_data.append(
                    RepositoryAuditData(
                        name=f"{args.org}/{repo_name}",
                        is_archived=is_archived,
                    )
                )
                continue

            print(
                f"    - [{repo_name}] Found {len(cargo_toml_paths)} Cargo.toml, {len(cargo_lock_paths)} Cargo.lock"
            )

            tomls = []
            for tp in cargo_toml_paths:
                content = client.get_file_content(
                    args.org, repo_name, tp, branch=default_branch
                )
                if content:
                    try:
                        tomls.append(parse_cargo_toml(content, path=tp))
                    except Exception as e:
                        print(
                            f"      [!] Warning: Failed parsing {repo_name}:{tp}: {e}",
                            file=sys.stderr,
                        )

            locks = []
            for lp in cargo_lock_paths:
                content = client.get_file_content(
                    args.org, repo_name, lp, branch=default_branch
                )
                if content:
                    try:
                        locks.extend(parse_cargo_lock(content, path=lp))
                    except Exception as e:
                        print(
                            f"      [!] Warning: Failed parsing {repo_name}:{lp}: {e}",
                            file=sys.stderr,
                        )

            repos_data.append(
                RepositoryAuditData(
                    name=f"{args.org}/{repo_name}",
                    is_archived=is_archived,
                    cargo_tomls=tomls,
                    cargo_locks=locks,
                )
            )

    # 3. Perform audit
    print(f"[*] Auditing {len(repos_data)} repositories...")
    report = audit_organization(repos_data, score_crates_ref, reference_repo=ref_label)
    report.organization = args.org

    # 4. Generate outputs
    out_dir = Path(args.output_dir)
    out_dir.mkdir(parents=True, exist_ok=True)

    default_html_file = out_dir / "index.html"
    default_json_file = out_dir / "rust_dependency_audit.json"
    default_md_file = out_dir / "rust_dependency_audit.md"

    html_file = Path(args.html_output) if args.html_output else default_html_file
    json_file = Path(args.json_output) if args.json_output else default_json_file
    md_file = Path(args.markdown_output) if args.markdown_output else default_md_file

    html_content = generate_html_report(report)
    json_content = generate_json_report(report)
    md_content = generate_markdown_report(report)

    # Always write outputs to default directory so full bundle is retained
    default_html_file.write_text(html_content, encoding="utf-8")
    default_json_file.write_text(json_content, encoding="utf-8")
    default_md_file.write_text(md_content, encoding="utf-8")

    # If custom paths were specified, also write there
    if html_file != default_html_file:
        html_file.parent.mkdir(parents=True, exist_ok=True)
        html_file.write_text(html_content, encoding="utf-8")
    if json_file != default_json_file:
        json_file.parent.mkdir(parents=True, exist_ok=True)
        json_file.write_text(json_content, encoding="utf-8")
    if md_file != default_md_file:
        md_file.parent.mkdir(parents=True, exist_ok=True)
        md_file.write_text(md_content, encoding="utf-8")

    print("\n" + "=" * 60)
    print("🎯 Rust Dependency Audit Complete!")
    print(f"Total Repositories Scanned : {report.total_repositories}")
    print(f"Rust Repositories Found    : {report.rust_repositories_count}")
    print(f"Total Distinct Crates      : {report.total_distinct_crates}")
    print(f"Managed in {ref_label.split('/')[-1]}    : {report.managed_crates_count}")
    print(f"Version Mismatches         : {report.mismatch_crates_count}")
    print(f"Unmanaged Crates           : {report.unmanaged_crates_count}")
    print("-" * 60)
    print(f"HTML (GitHub Pages) Report : {default_html_file}")
    print(f"JSON Report                : {default_json_file}")
    print(f"Markdown Report            : {default_md_file}")
    if md_file != default_md_file:
        print(f"Custom Markdown Output     : {md_file}")
    print("=" * 60 + "\n")

    return 0


if __name__ == "__main__":
    sys.exit(main())
