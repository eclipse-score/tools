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

from rust_dependency_audit.src.cargo_parser import (
    parse_cargo_toml,
    parse_cargo_lock,
)

SAMPLE_CARGO_TOML = """
[package]
name = "score_log"
version = "0.2.0"
edition = "2021"

[dependencies]
log = "0.4.27"
tokio = { version = "1.47.1", features = ["sync", "rt"] }
serde = { workspace = true }
internal_crate = { path = "../internal" }
external_git = { git = "https://github.com/foo/bar.git", rev = "12345" }

[dev-dependencies]
mockall = "0.14.0"

[build-dependencies]
cc = { version = "1.2.34" }
"""

SAMPLE_WORKSPACE_CARGO_TOML = """
[workspace]
members = [
    "crates/a",
    "crates/b",
]

[workspace.dependencies]
serde = "1.0.228"
anyhow = { version = "1.0.99", features = ["std"] }
"""

SAMPLE_CARGO_LOCK = """
version = 4

[[package]]
name = "log"
version = "0.4.27"
source = "registry+https://github.com/rust-lang/crates.io-index"
dependencies = []

[[package]]
name = "tokio"
version = "1.47.1"
source = "registry+https://github.com/rust-lang/crates.io-index"
dependencies = [
 "bytes",
 "pin-project-lite",
]

[[package]]
name = "score_log"
version = "0.2.0"
dependencies = [
 "log",
 "tokio",
]
"""


def test_parse_cargo_toml_standard_package():
    pkg = parse_cargo_toml(SAMPLE_CARGO_TOML, path="score/log/Cargo.toml")
    assert pkg.name == "score_log"
    assert pkg.version == "0.2.0"
    assert pkg.path == "score/log/Cargo.toml"
    assert len(pkg.dependencies) == 7

    deps_by_name = {d.name: d for d in pkg.dependencies}

    assert "log" in deps_by_name
    assert deps_by_name["log"].version_req == "0.4.27"
    assert deps_by_name["log"].dep_type == "normal"
    assert not deps_by_name["log"].is_workspace

    assert "tokio" in deps_by_name
    assert deps_by_name["tokio"].version_req == "1.47.1"
    assert deps_by_name["tokio"].features == ["sync", "rt"]

    assert "serde" in deps_by_name
    assert deps_by_name["serde"].is_workspace is True

    assert "internal_crate" in deps_by_name
    assert deps_by_name["internal_crate"].is_path is True

    assert "external_git" in deps_by_name
    assert deps_by_name["external_git"].is_git is True
    assert deps_by_name["external_git"].git_url == "https://github.com/foo/bar.git"
    assert deps_by_name["external_git"].git_rev == "12345"

    assert "mockall" in deps_by_name
    assert deps_by_name["mockall"].dep_type == "dev"

    assert "cc" in deps_by_name
    assert deps_by_name["cc"].dep_type == "build"


def test_parse_cargo_toml_renamed_package():
    content = """
[package]
name = "renamer"
version = "0.1.0"

[dependencies]
logging = { package = "log", version = "0.4.27" }
"""
    pkg = parse_cargo_toml(content, path="Cargo.toml")
    assert len(pkg.dependencies) == 1
    dep = pkg.dependencies[0]
    assert dep.name == "log"
    assert dep.alias == "logging"
    assert dep.version_req == "0.4.27"


def test_parse_cargo_toml_workspace_root():
    pkg = parse_cargo_toml(SAMPLE_WORKSPACE_CARGO_TOML, path="Cargo.toml")
    assert pkg.name == "workspace_root"
    assert pkg.is_workspace_root is True
    assert len(pkg.dependencies) == 2

    deps_by_name = {d.name: d for d in pkg.dependencies}
    assert deps_by_name["serde"].version_req == "1.0.228"
    assert deps_by_name["anyhow"].version_req == "1.0.99"
    assert deps_by_name["anyhow"].features == ["std"]


def test_parse_cargo_lock():
    lock_pkgs = parse_cargo_lock(SAMPLE_CARGO_LOCK, path="Cargo.lock")
    assert len(lock_pkgs) == 3

    lock_map = {p.name: p for p in lock_pkgs}
    assert "log" in lock_map
    assert lock_map["log"].version == "0.4.27"
    assert lock_map["log"].source is not None

    assert "tokio" in lock_map
    assert lock_map["tokio"].version == "1.47.1"
    assert "bytes" in lock_map["tokio"].dependencies

    assert "score_log" in lock_map
    assert lock_map["score_log"].version == "0.2.0"
    assert lock_map["score_log"].source is None
