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

## Component configuration

Each label in `.github/labeler.yml` defines a release component. Give each
component one `changed-files` rule with `any-glob-to-any-file`. Use a single
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
A commit that matches several components is included in each one.

Tags use `<component>/vMAJOR.MINOR.PATCH`. A final dot suffix of one to three
characters is dropped from the component name. The example above uses `bazel`
as the argument to `draft` and `bazel/v1.2.3` as a release tag. Component names
must be unique after normalization and valid as Git tag prefixes.

## Commands

### `monorepo-release status`

Reads the configuration committed at local `HEAD` and reports the latest
release tag and pending commits for each component. Uncommitted and untracked
changes produce a warning and are excluded from the report.

| Status | Meaning |
| --- | --- |
| `Unreleased` | The component has no reachable stable tag. |
| `Changes need review` | The component has relevant commits after its latest stable tag. |
| `Unchanged` | The latest stable tag covers every relevant commit. |

In GitHub Actions, `status` appends a Markdown report when
`GITHUB_STEP_SUMMARY` and `GITHUB_REPOSITORY` are set.

### `monorepo-release draft COMPONENT`

Reads the configuration, commit history, and component tags from the default
branch of the GitHub repository selected by `gh repo view`. The draft targets
the exact commit SHA used to generate the notes.

- If a draft already exists for the component, the command prints its link and
  preserves the maintainer’s edits.
- If the component has no relevant changes, the command reports that there is
  nothing to draft.
- Otherwise, the command creates a GitHub draft and prints its link. A
  maintainer publishes the release in GitHub.

## Release selection and versioning

Both commands compare with the highest stable component tag by version that
is reachable from the target commit. For a component without a matching tag,
the comparison covers its full matching history and the first draft uses
`0.1.0`.

For existing releases, the largest applicable bump among the selected commits
determines the next version.

| Commit subject | Release effect |
| --- | --- |
| Conventional Commit header with `!`, such as `chore(deps)!: remove support` | Major bump |
| `feat:` or a scoped feature header | Minor bump |
| `chore:`, `test:`, or `ci:`, including scoped forms | Excluded as maintenance |
| Other matching commits, including fixes and performance changes | Patch bump |

The release notes list the selected commit subjects with links to the commits.
A trailing PR reference such as `(#123)` also links to the pull request.

## Exit status

| Code | Meaning |
| --- | --- |
| `0` | The command completed. This includes an existing draft or no changes to draft. |
| `1` | A configuration, Git, GitHub, or other runtime error occurred. |
| `2` | The command-line arguments are invalid. |
