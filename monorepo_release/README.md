<!-- ----------------------------------------------------------------------------
  Copyright (c) 2026 Contributors to the Eclipse Foundation

  See the NOTICE file(s) distributed with this work for additional
  information regarding copyright ownership.

  This program and the accompanying materials are made available under the
  terms of the Apache License Version 2.0 which is available at
  https://www.apache.org/licenses/LICENSE-2.0

  SPDX-License-Identifier: Apache-2.0
----------------------------------------------------------------------------- -->

# Monorepo Release

Monorepo Release shows what has changed since the last release of each component
and prepares a GitHub release draft for a selected component. It uses the
component path rules in `.github/labeler.yml` to select the commits for both the
release overview and the release notes. Each component has its own version and
release tag.

## Usage

From the tools checkout, run the CLI with `uv run --locked monorepo-release`.
To use the CLI in another checkout, install it with
`uv tool install ./monorepo_release` from the tools repository.

Run these commands from a Git checkout with at least one component in
`.github/labeler.yml`. Creating a draft requires an authenticated GitHub CLI
with write access to the repository.

```sh
monorepo-release status
monorepo-release draft repo-policy-sync
```

In this repository, `repo-policy-sync` includes `repo_policy_sync/**` and its
GitHub Actions workflow. Changes to either path appear in the notes for a
release such as `repo-policy-sync/v1.2.3`. [Path rules](../.github/labeler.yml)

`draft` prints the draft's GitHub link. Open the draft to review or edit its
notes and publish it when ready. Run `status` locally or in a pull request check
to review pending changes. For a PR check, use `fetch-depth: 0` so Git can find
each component's tags and commit history. `draft` uses the repository selected
by `gh repo view`, which can be set with `gh repo set-default` in a fork checkout.

See the [design notes](DESIGN.md) for path matching, versioning, and how drafts
are tied to a commit.
