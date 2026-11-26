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

Review changes and prepare GitHub release drafts for independently versioned
components in a monorepo. Monorepo Release uses the path rules in
`.github/labeler.yml` to select the commits for both the release overview and
the release notes.

## Requirements

- Git and Python 3.12 or newer.
- A repository with at least one component defined in a committed
  `.github/labeler.yml`. [Configuration reference](docs/reference.md#component-configuration)
- An authenticated GitHub CLI (`gh`) with write access to the target repository
  for creating drafts.

## Install

Run this from the tools repository to install the command with `uv`.

```sh
uv tool install ./monorepo_release
```

When working in the tools repository, you can also run the workspace copy
with `uv run --locked monorepo-release status`.

## Review pending changes

Run `status` from the checkout you want to inspect.

```sh
monorepo-release status
```

The report lists each component’s last release tag and its pending commits.
In a GitHub Actions check, set `fetch-depth: 0` on the checkout step to make
tags and commit history available. The report also appears in the job summary.

## Prepare a release draft

Confirm the target repository with `gh repo view`. In a fork checkout, use
`gh repo set-default OWNER/REPO` to select the repository for the draft.
Then run `draft` with the component name.

```sh
monorepo-release draft repo-policy-sync
```

The command reads the target repository’s default branch and prints the link
to the draft. Open the draft in GitHub to review or edit the notes and publish
the release.

See the [command and configuration reference](docs/reference.md) for tag
selection, version bumps, and draft behavior.
