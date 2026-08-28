<!-- ----------------------------------------------------------------------------
  Copyright (c) 2026 Contributors to the Eclipse Foundation

  See the NOTICE file(s) distributed with this work for additional
  information regarding copyright ownership.

  This program and the accompanying materials are made available under the
  terms of the Apache License Version 2.0 which is available at
  https://www.apache.org/licenses/LICENSE-2.0

  SPDX-License-Identifier: Apache-2.0
----------------------------------------------------------------------------- -->

# SCORE Repository Policy Sync

SCORE Repository Policy Sync continuously evaluates declarative repository
policies across a GitHub organization. It checks each repository's current
default branch and, when requested, opens or updates one reviewable pull
request per policy and repository.

The default mode is safe to use in CI: it only reports drift and makes no
remote changes. Apply mode is deliberately explicit and preserves policy PR
ownership so repeated runs update the same proposal rather than creating
duplicates. When an existing policy PR is already correct but conflicts with
its target branch, apply mode automatically rebuilds it from the current
default branch; body-only changes update the PR text without rebuilding its
branch.

## Quick start

Requirements: Python 3.12+, [uv](https://docs.astral.sh/uv), Git, and an
authenticated [GitHub CLI](https://cli.github.com/).

```bash
uv sync
gh auth login

# Plan with local policies from ./policies, when present, and the bundled SCORE
# policies without changing remote repositories.
uv run score-repo-policy-sync plan --org eclipse-score

# Add another local policy directory when needed.
uv run score-repo-policy-sync plan --org eclipse-score \
  --policy-dir shared-policies

# Apply: create or update policy-owned pull requests.
uv run score-repo-policy-sync apply --org eclipse-score
```

Pre-commit is run again when the first run applies formatting fixes. If the
second run is clean, those fixes are included in the normal pull request. If
pre-commit still fails but the changes should remain reviewable, opt in to a
draft pull request. The failure is added as a PR comment:

```bash
uv run score-repo-policy-sync apply --org eclipse-score --allow-dirty-pr
```

Restrict a run with repeatable `--policy NAME` and `--repo NAME` flags. The
`--policy` option is an exact allowlist across local and bundled policies: only
the named policies run, regardless of where they are defined. Without
`--policy`, all local policies and bundled SCORE policies are included; policies
can then be excluded with `--exclude-policy NAME`. Use repeated
`--policy-dir PATH` options to add local policy directories:

```bash
uv run score-repo-policy-sync plan \
  --org eclipse-score \
  --repo reference_integration \
  --policy-dir repo_policy_sync/policies \
  --policy minimum-bazel-version

uv run score-repo-policy-sync plan \
  --org etas \
  --repo reference_integration
```

To exclude a policy for a repository or rollout:

```bash
uv run score-repo-policy-sync plan \
  --org etas \
  --exclude-policy minimum-bazel-version
```

Policy options can be kept in the optional `score-repo-policy-sync.toml` file.
Explicit CLI values override the file; see the
[configuration reference](docs/reference/configuration.md).

The CLI always prints a compact table to standard output. Pass
`--json-output PATH` and/or `--markdown-output PATH` to write additional
versioned JSON and Markdown reports during the same policy run. Markdown is
suited for pull requests, issues, and wikis. Its cells use `✅` for compliant,
`❌` for required changes, `N/A` for policies that do not apply, and
`⚠️`/`⏭️` for errors or skipped evaluations. Open, merged, and automatically
closed policy pull requests are shown as linked GitHub-logo badges in the
affected cells; change and error details are kept in a collapsible section.
Plan mode exits `1`
when policy drift is found, `0` when no policy drift is found, and `2` for
input or execution errors. Apply mode exits `0` after successful remediation.

## Operational model

Repository checkouts are synchronized by the
[`repo_cache`](../repo_cache/README.md) component and cached under
`$XDG_CACHE_HOME/repo-cache/<owner>/<repository>` or
`~/.cache/repo-cache/<owner>/<repository>`. The generic cache can be shared
with other repository tools. Checkouts are disposable: each run refreshes the
selected repositories to their current default branches before evaluation.
Archived repositories are excluded. Use `--cache-dir PATH` in CI to choose a
workspace-local cache and `--sync-workers N` to control concurrent checkout
synchronization. Policy evaluation and apply work, including policy follow-up
commands such as Bazel, run across independent repositories with
`--policy-workers N`.

To rebuild a policy PR from the current default branch, use the guarded
recreate operation with exactly one repository and policy:

```bash
uv run score-repo-policy-sync apply --org eclipse-score --repo reference_integration \
  --policy minimum-bazel-version --recreate
```

## Documentation

The [documentation index](docs/README.md) is organized using the four Diataxis
quadrants:

- **Tutorials:** [create your first policy](docs/tutorials/first-policy.md).
- **How-to guides:** [run a policy](docs/how-to/run-a-policy.md).
- **Reference:** [CLI](docs/reference/cli.md) and
  [policy format](docs/reference/policy-format.md) plus the
  [bundled policy overview](policies/README.md).
- **Explanation:** [architecture](docs/explanation/architecture.md),
  [execution model](docs/explanation/execution-model.md), and
  [pull request safety](docs/explanation/pull-request-safety.md).

## First-version interface

The supported executable is `score-repo-policy-sync`. Policy IDs are the
directory names containing each `policy.yml`, and policy-owned branches use
the `repo-policy-sync/<policy-id>` naming scheme. The first version does not
provide command aliases or historical policy-ID compatibility; update callers
to the supported command and current policy IDs before rollout.

## First-version change summary

The first version provides declarative bundled and local policies, fixture-
tested idempotent operations, safe plan mode, explicit apply mode, and
policy-owned pull requests with terminal, JSON, and Markdown reports. It
supports the documented GitHub organization workflow, bounded checkout and
policy concurrency, and recovery from stale or conflicting policy branches.

Compatibility notes:

- Callers must use `score-repo-policy-sync`, `--policy-dir`, the current policy
  IDs, and `repo-policy-sync/<policy-id>` branches. Legacy command aliases,
  `--policy-directory`, and historical policy IDs are not supported.
- Renaming a policy changes its branch and pull-request identity. Existing
  policy branches or pull requests must be handled before adopting the new ID.
- The first version intentionally does not provide dynamic operation plugins,
  persistent result storage, generalized retries/rate-limit handling, or
  non-GitHub providers.
