<!-- ----------------------------------------------------------------------------
  Copyright (c) 2026 Contributors to the Eclipse Foundation

  See the NOTICE file(s) distributed with this work for additional
  information regarding copyright ownership.

  This program and the accompanying materials are made available under the
  terms of the Apache License Version 2.0 which is available at
  https://www.apache.org/licenses/LICENSE-2.0

  SPDX-License-Identifier: Apache-2.0
----------------------------------------------------------------------------- -->

# Architecture and design

SCORE Repository Policy Sync separates policy semantics from command-line and
GitHub concerns.
Most behavior can therefore be tested against ordinary temporary directories,
without network access, Git, or `gh`.

```text
cli.py
  ├─ reporting.py              table and versioned JSON renderers
  └─ runner.py                 organization-level execution and report assembly
       ├─ policy.py             YAML → validated Policy objects
       ├─ operations/           built-in operation registry and implementations
       ├─ engine.py             evaluate and apply policies to a checkout
       └─ github.py             gh and Git process adapter
```

`models.py` contains the immutable values exchanged across these layers.
`errors.py` defines expected user-facing errors. `cli.py` is the composition
root: it creates the concrete GitHub client, selects a renderer, and translates
failures into CLI exit codes.

## Responsibilities

### Policy loading and operations

`policy.py` validates policy metadata and conditions. It delegates each
`ensure[].type` to the explicit registry in `operations/`. An operation owns
its YAML validation, compliance check, remediation description, and application.

The registry is intentionally built in. It makes supported operations visible
and testable without runtime discovery, third-party code loading, or an
extension ABI. Add a new operation by implementing it in `operations/` and
registering it there.

### Policy engine

`engine.py` evaluates a policy against one local repository directory. It
checks conditions, gathers the changes that would be made, and applies those
same idempotent operations when requested. It has no GitHub, Git, or
argument-parsing dependency.

### Orchestration and infrastructure

`runner.py` discovers repositories, refreshes the selected cached checkouts
in parallel, then coordinates one policy across independent repository
checkouts in parallel through a small `RepositoryClient` protocol. It returns
a structured `RunReport` rather than formatting output. `reporting.py` renders
that report as a terminal table or a versioned JSON document. `github.py`
implements the protocol with the pre-authenticated `gh` CLI and Git.

## Invariants

- A refreshed repository checkout is always the source of truth for
  applicability and compliance.
- Policy operations use repository-relative paths and are idempotent.
- Each policy maps to one deterministic branch per repository.
- A branch is treated as tool-owned only when its open pull request contains
  the policy marker.
- An apply run updates the owned pull request’s title and body from the current
  policy before pushing any required commit.

## Deliberate boundaries

The current scope does not provide dynamic plugins, retries, rate-limit
handling, or policy result storage. Checkout synchronization and per-policy
repository processing are parallel; each repository checkout remains isolated.
The explicit operation registry is the extension point until a concrete
requirement justifies a more dynamic model.
