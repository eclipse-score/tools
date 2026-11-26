<!-- ----------------------------------------------------------------------------
  Copyright (c) 2026 Contributors to the Eclipse Foundation

  See the NOTICE file(s) distributed with this work for additional
  information regarding copyright ownership.

  This program and the accompanying materials are made available under the
  terms of the Apache License Version 2.0 which is available at
  https://www.apache.org/licenses/LICENSE-2.0

  SPDX-License-Identifier: Apache-2.0
----------------------------------------------------------------------------- -->

# Monorepo Release design

## Component configuration and release history

Monorepo Release requires `.github/labeler.yml` with at least one component. It
reads the same path rules that GitHub's PR labeler uses. It supports exact paths
and `*` and `**` globs under `changed-files` and `any-glob-to-any-file`. A commit
that touches paths in multiple components belongs to each of them. Component
tags use `<component>/vMAJOR.MINOR.PATCH`.
A label ending in a dot suffix of one to three characters drops that suffix
from the tag prefix. For example, `bazel.yml` uses `bazel/vMAJOR.MINOR.PATCH`.

`status` evaluates the current checkout at `HEAD`. `draft` evaluates the
GitHub repository's default branch at a specific commit SHA. For each component
they evaluate, both commands compare that commit with the component's highest
reachable stable tag. Each component release tag has a matching GitHub release.

## Version proposal and release notes

`status` and `draft` use the same Git commit selection. Both skip maintenance
commits with `chore`, `test`, or `ci` prefixes, including scoped forms such as
`chore(deps):`. Commits marked as breaking in the subject remain relevant.

The largest applicable change determines the version bump. A breaking change
bumps the major version, `feat` bumps the minor version, and `fix` or `perf`
bumps the patch version. Other relevant commits default to a patch bump. A
first release starts at `0.1.0` and includes the matching history through the
target commit.

The release notes list the selected commits. Each entry uses the commit
subject and links to the commit. A PR reference in the subject also links to
that PR.

## Draft creation

`draft` checks GitHub for an open draft for the selected component. If one
exists, it prints the draft's link and preserves the maintainer's edits. If the
last release already covers all relevant changes, `draft` reports that there is
nothing to draft.

Monorepo Release passes the component tag, target commit SHA, and rendered notes
to `gh release create --draft`. Later commits on the default branch are
available for the next release. When a maintainer publishes the draft in
GitHub, GitHub creates the tag if it does not already exist.
[GitHub CLI](https://cli.github.com/manual/gh_release_create)
· [Draft tag behavior](https://github.com/googleapis/release-please/blob/main/docs/manifest-releaser.md)
