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
from unittest.mock import patch
from rust_dependency_audit.src.github_client import GitHubClient
from rust_dependency_audit.src.cli import main


def test_github_client_token_masking():
    token = "secret_github_token_value_xyz"
    client = GitHubClient(token=token)

    # Ensure repr or str never prints the token
    assert token not in str(client)
    assert token not in repr(client)
    assert "secret" not in repr(client)


def test_cli_local_scan_mode(tmp_path):
    # Setup mock local repo with Cargo.toml
    repo_dir = tmp_path / "test_repo"
    repo_dir.mkdir()
    cargo_toml = repo_dir / "Cargo.toml"
    cargo_toml.write_text("""
[package]
name = "test_pkg"
version = "0.1.0"

[dependencies]
clap = "4.5.4"
""")

    # Non-Rust repository
    non_rust_dir = tmp_path / "cpp_repo"
    non_rust_dir.mkdir()
    (non_rust_dir / "CMakeLists.txt").write_text("cmake_minimum_required(VERSION 3.20)")

    # Mock score-crates reference files
    ref_dir = tmp_path / "score-crates"
    ref_dir.mkdir()
    (ref_dir / "MODULE.bazel").write_text("""
crate.spec(
    package = "clap",
    version = "4.5.4",
)
""")
    (ref_dir / "BUILD").write_text("""
alias(name = "clap", actual = "@crate_index//:clap")
""")

    out_dir = tmp_path / "output"
    custom_md = tmp_path / "custom_summary.md"

    exit_code = main(
        [
            "--local-scan-dir",
            str(tmp_path),
            "--local-reference-dir",
            str(ref_dir),
            "--output-dir",
            str(out_dir),
            "--markdown-output",
            str(custom_md),
            "--org",
            "test-org",
        ]
    )

    assert exit_code == 0
    # Both default files and custom markdown output should exist
    assert (out_dir / "index.html").exists()
    assert (out_dir / "rust_dependency_audit.json").exists()
    assert (out_dir / "rust_dependency_audit.md").exists()
    assert custom_md.exists()

    report_data = json.loads((out_dir / "rust_dependency_audit.json").read_text())
    # Total repositories scanned should count all dirs (test_repo, cpp_repo, score-crates, output)
    # Rust repositories should only count test_repo
    assert report_data["rust_repositories_count"] == 1
    assert report_data["total_repositories"] >= 2

    html_content = (out_dir / "index.html").read_text()
    assert "test_pkg" in html_content or "test_repo" in html_content
    assert "clap" in html_content


def test_cli_empty_reference_fails(tmp_path):
    ref_dir = tmp_path / "empty-score-crates"
    ref_dir.mkdir()
    (ref_dir / "MODULE.bazel").write_text("# Empty module without crates\n")

    exit_code = main(
        [
            "--local-scan-dir",
            str(tmp_path),
            "--local-reference-dir",
            str(ref_dir),
            "--output-dir",
            str(tmp_path / "out"),
        ]
    )
    # Should exit with non-zero error to prevent publishing invalid audit
    assert exit_code != 0


def test_github_client_tree_truncated_fallback():
    client = GitHubClient(token="mock_token")

    def mock_request(endpoint):
        if "recursive=1" in endpoint:
            # First request returns truncated
            return {
                "truncated": True,
                "tree": [{"path": "file1.txt", "type": "blob", "sha": "111"}],
            }
        elif endpoint.endswith("/git/trees/main"):
            # Non-recursive root returns directory
            return {
                "tree": [
                    {"path": "file1.txt", "type": "blob", "sha": "111"},
                    {"path": "subdir", "type": "tree", "sha": "222"},
                ]
            }
        elif endpoint.endswith("/git/trees/222"):
            # Subtree for directory
            return {
                "tree": [
                    {"path": "file2.txt", "type": "blob", "sha": "333"},
                ]
            }
        return {}

    with patch.object(client, "_request", side_effect=mock_request):
        paths = client.get_repo_git_tree("org", "repo", "main")
        assert "file1.txt" in paths
        assert "subdir/file2.txt" in paths


def test_github_client_submodules_resolution():
    client = GitHubClient(token="mock_token")

    gitmodules_content = """
[submodule "third_party/sub"]
    path = third_party/sub
    url = https://github.com/eclipse-score/sub_repo.git
"""

    def mock_request(endpoint):
        if "trees/main" in endpoint:
            return {
                "truncated": False,
                "tree": [
                    {"path": ".gitmodules", "type": "blob", "sha": "111"},
                    {"path": "third_party/sub", "type": "commit", "sha": "sha_sub"},
                ],
            }
        elif "trees/sha_sub" in endpoint:
            return {
                "truncated": False,
                "tree": [
                    {"path": "Cargo.toml", "type": "blob", "sha": "sha_cargo"},
                ],
            }
        return {}

    with patch.object(client, "_request", side_effect=mock_request):
        with patch.object(client, "get_file_content", return_value=gitmodules_content):
            paths = client.get_repo_git_tree("eclipse-score", "main_repo", "main")
            assert ".gitmodules" in paths
            assert "third_party/sub/Cargo.toml" in paths
