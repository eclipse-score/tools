<!-- ----------------------------------------------------------------------------
  Copyright (c) 2026 Contributors to the Eclipse Foundation

  See the NOTICE file(s) distributed with this work for additional
  information regarding copyright ownership.

  This program and the accompanying materials are made available under the
  terms of the Apache License Version 2.0 which is available at
  https://www.apache.org/licenses/LICENSE-2.0

  SPDX-License-Identifier: Apache-2.0
----------------------------------------------------------------------------- -->

# How to run a policy

Use plan mode first. It refreshes disposable local checkouts, reports drift,
and surfaces an actionable matching policy pull request (for example an open
PR while changes are still required, or a merged/closed PR alongside current
compliance), but never changes remote repositories:

```bash
uv run score-repo-policy-sync plan --org eclipse-score
```

Bundled SCORE policies are loaded by default. In addition, policies are loaded
from `./policies` in the current working directory when that directory exists.
The directory layout and policy format are the same for local and bundled
policies:

```bash
uv run score-repo-policy-sync plan \
  --org etas
```

Use `--policy-dir PATH` when local policies are stored elsewhere. Repeat the
option to combine local policy directories.

Exclude a local or bundled policy with `--exclude-policy`:

```bash
uv run score-repo-policy-sync plan \
  --org eclipse-score \
  --repo reference_integration \
  --exclude-policy minimum-bazel-version
```

The exclusion can be repeated. For a persistent setup, use the optional
`score-repo-policy-sync.toml` configuration file:

```toml
[score-repo-policy-sync]
exclude_policies = ["minimum-bazel-version"]
```

See the [configuration reference](../reference/configuration.md) for local
policy directories and command-line overrides.

Limit a rollout to selected policy and repository names with repeatable
`--policy` and `--repo` options. `--policy` selects an exact allowlist across
local and bundled policies; no other policy is run:

```bash
uv run score-repo-policy-sync plan \
  --org eclipse-score \
  --repo reference_integration \
  --policy-dir policies \
  --policy minimum-bazel-version
```

When the plan is reviewed, apply the same selection to create or update the
policy-owned pull requests:

```bash
uv run score-repo-policy-sync apply \
  --org eclipse-score \
  --repo reference_integration \
  --policy-dir policies \
  --policy minimum-bazel-version
```

## Authentication and permissions

Authenticate `gh` before running the command. The token must be able to list
the selected organization repositories, read their default branches and
contents, and read pull requests when Markdown pull-request status is
requested. In GitHub Actions, the plan workflow therefore needs at least:

```yaml
permissions:
  contents: read
  pull-requests: read
```

Apply mode additionally pushes policy branches, edits or creates pull
requests, creates the automation labels when needed, and can comment on a
dirty draft pull request. An approved apply token normally needs:

```yaml
permissions:
  contents: write
  pull-requests: write
  issues: write
```

For private organizations, grant the equivalent organization and repository
read access required by the organization's token policy. Keep apply workflows
manual or otherwise separately protected; the pull-request validation
workflow must use the `plan` command and must not use `apply`.

Apply mode runs the target repository's configured pre-commit hooks on the
policy-changed paths before publishing changes. Treat apply mode as
trusted-repository execution:
repository hooks can execute arbitrary code. The runner removes the usual
GitHub token and user configuration environment, disables Git prompts, and
uses a temporary home directory, but this is not a sandbox.

For CI or another programmatic consumer, write the versioned JSON report to a
file while retaining the standard table output:

```bash
uv run score-repo-policy-sync plan \
  --org eclipse-score \
  --json-output policy-report.json
```

Add `--markdown-output report.md` to generate the Markdown report in the same
run.

On a normal apply run, an existing policy PR whose branch already contains the
desired changes is not rebuilt unnecessarily. A stale generated body is updated
in place. If that unchanged branch has a merge conflict with the repository's
current default branch, the tool automatically recreates it from that default
branch and reapplies the policy.

When the refreshed default branch is already compliant, apply mode closes an
existing policy-owned PR after verifying that its branch head still matches the
tool's ownership marker. Plan mode leaves the PR open. A changed or missing
marker stops the run without closing the PR so it can be reviewed manually.

Use `apply --recreate` only to rebuild one existing policy pull request from
the current default branch. It requires exactly one `--repo` and one
`--policy`; see the [CLI reference](../reference/cli.md) for all constraints.

## Recovering from failures

Exit status `2` means that authentication, checkout, GitHub, Git, or policy
execution failed. The terminal report and JSON/Markdown details identify the
affected repository and policy; fix that repository or credential issue and
rerun the same selection. A failed checkout or evaluation does not prevent
other selected repositories from being reported.

Surfaced runtime errors and automation-failure text redact common GitHub token,
bearer, password, secret, and URL-credential forms. Output from policy
`after_apply` commands is captured instead of being printed directly. Keep
credentials out of policy descriptions, rationales, and command arguments as
an additional precaution.

If an existing policy branch has changed outside the tool, Repository Policy
Sync refuses to update it. Review the branch and pull request manually before
rerunning. If a generated pull request is in conflict, a normal apply rerun
rebuilds it from the current default branch; `apply --recreate` is available
for the explicitly guarded one-repository, one-policy case.
