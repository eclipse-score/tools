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
modules in a monorepo. Monorepo Release uses the path rules in
`.github/labeler.yml` to select the commits for both the release overview and
the release notes.

## Requirements

- Git and `uv`. The tool requires Python 3.12 or newer.
- A repository with at least one module defined in a committed
  `.github/labeler.yml`. [Configuration reference](docs/reference.md#module-configuration)
- An authenticated GitHub CLI (`gh`) with write access to the target repository
  for creating drafts.

## Review pending changes

Run `status` from the checkout you want to inspect. `uvx` runs the tool
directly from its Git repository.

```sh
uvx --from 'git+https://github.com/eclipse-score/tools.git#subdirectory=monorepo_release' \
  monorepo-release status
```

When developing the tool, run the workspace copy with
`uv run --locked monorepo-release status`.

The report lists each module’s last release tag, comparison base, and
pending commits. Until a module has its own release tag, the latest
reachable stable repository tag serves as the comparison base.
In a GitHub Actions check, set `fetch-depth: 0` on the checkout step to make
tags and commit history available. The report also appears in the job summary.

## Prepare a release draft

Preview the proposed tag, target commit, comparison base, and release notes.
Each module starts at `v0.0.1`. Use `--repo` to select the target
explicitly, for example from a fork checkout.

```sh
uvx --from 'git+https://github.com/eclipse-score/tools.git#subdirectory=monorepo_release' \
  monorepo-release draft repo-policy-sync --repo eclipse-score/tools --dry-run
```

Run the command without `--dry-run` to create the draft. Open the printed
link in GitHub to review or edit the notes and publish the release.

See the [command and configuration reference](docs/reference.md) for tag
selection, version bumps, and draft behavior.
