<!-- ----------------------------------------------------------------------------
  Copyright (c) 2026 Contributors to the Eclipse Foundation

  See the NOTICE file(s) distributed with this work for additional
  information regarding copyright ownership.

  This program and the accompanying materials are made available under the
  terms of the Apache License Version 2.0 which is available at
  https://www.apache.org/licenses/LICENSE-2.0

  SPDX-License-Identifier: Apache-2.0
----------------------------------------------------------------------------- -->

# Tutorial: create your first policy

This tutorial creates, tests, and plans a small policy that ensures generated
build output is ignored. It assumes you have completed the installation steps
in the [project README](../../README.md).

Bundled policies are small, versioned specifications with executable examples.
Create a directory under `repo_policy_sync/policies/` and give it a stable,
lowercase directory name:

```text
policies/
  ensure-build-directory-ignored/
    policy.yml
    missing-ignore-rule/
      before/
      after/
```

Start with a minimal definition:

```yaml
title: "chore: ignore generated build directory"
description: Keep generated build output out of version control.

ensure:
  - type: ensure_line
    path: .gitignore
    line: _build
    rationale: Build output is generated locally.
```

The policy directory name (`ensure-build-directory-ignored`) is its permanent
ID and determines the policy-owned branch and pull-request marker. Keep it
stable after rollout; renaming a policy is a first-version breaking change.

Add the smallest representative input tree under `before/`, then the exact
expected tree under `after/`. The fixture test applies every bundled policy,
compares its output with `after/`, and confirms a second application makes no
changes:

```bash
uv run pytest -q repo_policy_sync/tests/test_policy_fixtures.py
```

Run the complete policy-sync suite before rolling out a policy:

```bash
uv run pytest -q repo_policy_sync/tests
```

Use plan mode against one known repository before enabling apply mode for an
organization:

```bash
uv run score-repo-policy-sync plan \
  --org eclipse-score \
  --repo example-repository \
  --policy ensure-build-directory-ignored
```
