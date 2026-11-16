<!-- ----------------------------------------------------------------------------
  Copyright (c) 2026 Contributors to the Eclipse Foundation

  See the NOTICE file(s) distributed with this work for additional
  information regarding copyright ownership.

  This program and the accompanying materials are made available under the
  terms of the Apache License Version 2.0 which is available at
  https://www.apache.org/licenses/LICENSE-2.0

  SPDX-License-Identifier: Apache-2.0
----------------------------------------------------------------------------- -->

# SCORE Tools

This repository contains small, reusable tools and libraries for SCORE
projects. Choose a component below to find its setup and usage instructions.

| Component | What it does |
| --- | --- |
| [Repository Cache](repo_cache/README.md) | Maintains local checkouts of repositories in a GitHub organization for tools that operate across many repositories. |
| [Repository Policy Sync](repo_policy_sync/README.md) | Evaluates repository policies across a GitHub organization and can open reviewable pull requests to apply them. |
| [Rust Dependency Audit](rust_dependency_audit/README.md) | Audits Rust dependencies across repositories and cross-references them against `score-crates` as GitHub Pages. |
| [SCORE pytest](score_pytest/README.md) | Provides a Bazel rule for pytest and a plugin that adds structured metadata to JUnit reports. |
| [Copyright Checker](cr_checker/README.md) | Checks copyright headers and supports pre-commit and Bazel integrations. |

## Contributing

See [CONTRIBUTING.md](CONTRIBUTING.md) for the repository's purpose, scope,
and guidance on proposing a new component.
