<!-- ----------------------------------------------------------------------------
  Copyright (c) 2026 Contributors to the Eclipse Foundation

  See the NOTICE file(s) distributed with this work for additional
  information regarding copyright ownership.

  This program and the accompanying materials are made available under the
  terms of the Apache License Version 2.0 which is available at
  https://www.apache.org/licenses/LICENSE-2.0

  SPDX-License-Identifier: Apache-2.0
----------------------------------------------------------------------------- -->

# Monorepo Release reference

## Module configuration

Each label in `.github/labeler.yml` defines a release module. Give each
module one `changed-files` rule with `any-glob-to-any-file`. Use a single
path or a list of paths relative to the repository root.

```yaml
bazel.yml:
  - changed-files:
      - any-glob-to-any-file:
          - .github/workflows/bazel.yml
          - ".github/actions/_setup-*/**"
```

The supported patterns are exact file paths, `*` within one path segment,
and `**` as a complete path segment for zero or more directory levels.
Patterns include dotfiles and dot directories. A commit that matches several
modules is included in each one.

Tags use `<module>/vMAJOR.MINOR.PATCH`. A final dot suffix of one to three
characters is dropped from the module name. The example above uses `bazel`
as the argument to `draft` and `bazel/v1.2.3` as a release tag. Module names
must be unique after normalization and valid as Git tag prefixes. Duplicate
YAML keys are configuration errors.

## Commands

### `monorepo-release status`

Reads the configuration committed at local `HEAD` and reports the latest
module release tag, comparison base, and pending commits for each module.
Uncommitted and untracked changes produce a warning and are excluded from the
report.

Both commands run `git fetch --tags` in the current checkout and retrieve
full history when the checkout is shallow. A failed fetch aborts either
command. For `status`, Git selects the remote and updates its remote-tracking
refs. A full checkout without a remote uses its local tags.

| Status | Meaning |
| --- | --- |
| `Unreleased` | The module has no reachable comparison tag. |
| `New` | The module has no release tag of its own, and its paths match files at `HEAD` but none at the repository comparison tag. |
| `Changed` | The module has relevant commits after the comparison tag. |
| `Unchanged` | The comparison tag covers every relevant commit. |

A module introduced only by maintenance commits can appear as `New`. A draft
requires at least one relevant commit.

In GitHub Actions, `status` appends a Markdown report when
`GITHUB_STEP_SUMMARY` and `GITHUB_REPOSITORY` are set.

### `monorepo-release draft MODULE [--repo [HOST/]OWNER/REPO] [--dry-run]`

Reads the configuration and commit history from the default branch of the
target repository and fetches its module and repository tags. By default,
the target is the repository selected by `gh repo view`. The command fetches
the target branch and tags into the current checkout. It uses the fetched
branch commit and all reachable tags available locally, including local-only
tags. The draft targets the exact commit SHA used for the notes.

- If a draft already exists for the module, the command prints its link and
  preserves the maintainer’s edits.
- If the module has no relevant changes, the command reports that there is
  nothing to draft.
- With `--dry-run`, the command previews the repository, proposed tag, target
  commit, comparison base, and release notes without creating a draft. A later
  invocation calculates the proposal again from the then-current default
  branch and tags.
- Otherwise, the command creates a GitHub draft and prints its link. A
  maintainer publishes the release in GitHub.

## Release selection and versioning

Both commands select the comparison base in this order. A tag must be
reachable from the target commit, and versions are compared numerically.

1. The highest stable tag for the module, such as `bazel/v1.2.3`.
2. The highest stable repository tag in the format `vMAJOR.MINOR.PATCH`,
   such as `v0.0.5`.
3. The full matching history when neither tag exists.

The first module release uses `0.0.1` when there are relevant changes.
The repository tag sets the start of the comparison. Each module uses
its own version sequence.

The comparison is designed for squash-merged pull requests. It includes
the resulting commits and skips merge commits.

For existing releases, the largest applicable bump among the selected commits
determines the next version.

| Commit subject | Release effect |
| --- | --- |
| Conventional Commit header with `!`, such as `chore(deps)!: remove support` | Major bump |
| `feat:` or a scoped feature header | Minor bump |
| `chore:`, `test:`, or `ci:`, including scoped forms | Excluded as maintenance |
| Other matching commits, including fixes and performance changes | Patch bump |

The release notes list the selected commit subjects. A trailing PR reference
such as `(#123)` links to the pull request.

## Exit status

| Code | Meaning |
| --- | --- |
| `0` | The command completed. This includes an existing draft or no changes to draft. |
| `1` | A configuration, Git, GitHub, or other runtime error occurred. |
| `2` | The command-line arguments are invalid. |
