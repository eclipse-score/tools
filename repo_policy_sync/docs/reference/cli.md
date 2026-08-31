<!-- ----------------------------------------------------------------------------
  Copyright (c) 2026 Contributors to the Eclipse Foundation

  See the NOTICE file(s) distributed with this work for additional
  information regarding copyright ownership.

  This program and the accompanying materials are made available under the
  terms of the Apache License Version 2.0 which is available at
  https://www.apache.org/licenses/LICENSE-2.0

  SPDX-License-Identifier: Apache-2.0
----------------------------------------------------------------------------- -->

# CLI reference

```text
score-repo-policy-sync COMMAND [OPTIONS]
```

`COMMAND` is one of `plan` or `apply`. Policy options may also be set in the
TOML configuration. Explicit
command-line values override values from the configuration. The organization
may therefore be supplied either with `--org` or in TOML. Report output paths
are CLI-only.

## Commands

| Command | Description |
| --- | --- |
| `plan` | Evaluate policies and report matching policy pull requests without changing repositories. Drift is reported in the output and does not affect the exit status. |
| `apply` | Apply policies and create or update policy-owned pull requests. |

## Typical

| Option | Description |
| --- | --- |
| `--org NAME` | GitHub organization to scan. May be set in TOML. |
| `--policy NAME` | Select a policy by directory name from local or bundled policies. Repeat to select more than one; when present, only the selected policies run. Defaults to all local and bundled policies. |
| `--repo NAME` | Restrict the run to an exact repository name. Repeat to select more than one. |
| *(stdout)* | Always prints the terminal policy-evaluation table; its status column shows policy pull-request state and number instead of the plain compliance status when that state is actionable. |

## Rare

| Option | Description |
| --- | --- |
| `--config PATH` | TOML configuration file. Defaults to `score-repo-policy-sync.toml` in the current working directory when present. This and the report output path options are CLI-only. |
| `--json-output PATH` | Also write the versioned JSON report to `PATH`. |
| `--markdown-output PATH` | Also write the Markdown report to `PATH`. |
| `--policy-dir PATH` | Local policy directory. Repeat to combine directories. Defaults to `./policies` in the current working directory when present. |
| `--exclude-policy NAME` | Exclude one local or bundled policy. Applied after any explicit `--policy` selection; repeat to exclude more than one. |
| `--recreate` | On `apply`, rebuild one existing policy-owned pull request from its repository's current default branch. Requires exactly one `--repo` and exactly one `--policy`. |
| `--allow-dirty-pr`, `--no-allow-dirty-pr` | After the automatic formatting-fix retry, commit and push changes even if pre-commit still fails; create or keep the pull request as a draft and add a comment with the failure. |
| `--quiet`, `--no-quiet` | Suppress progress messages on standard error. The report remains on standard output. |

## Debugging only

| Option | Description |
| --- | --- |
| `--cache-dir PATH` | Directory for disposable checkouts. Defaults to the XDG cache directory. |
| `--sync-workers N` | Number of concurrent checkout refreshes. Defaults to the available CPU count, with a minimum of `1`. |
| `--policy-workers N` | Number of repositories evaluated or applied concurrently for each policy. Defaults to the available CPU count, with a minimum of `1`. Set to `1` to process policies serially. |

Archived repositories are excluded from every run. Selecting an archived
repository with `--repo` fails validation instead of silently ignoring it.

## Exit status

| Status | Meaning |
| --- | --- |
| `0` | Plan or apply mode completed without input, authentication, GitHub, Git, or policy execution errors. Plan mode may still report policy drift. |
| `2` | Invalid input or an authentication, GitHub, Git, or policy execution error occurred. |

## JSON report

`--json-output PATH` writes one JSON document to `PATH`. The top-level
`schema_version` currently has value `2`. The `summary` object separately
reports repository synchronization, policy evaluation, pull-request activity
(including automatic closures), and elapsed duration. The `policies` array
contains the selected policy metadata (`id`, `title`, `description`, and
`legacy_names`). Each `outcomes` element references its policy by `policy_id`
and includes the repository, applicability result, status, planned or applied
changes, pull request URL, policy pull-request status, warnings, and error.
`--markdown-output PATH`
writes the compact Markdown matrix to `PATH`. JSON and Markdown output paths
can be supplied together so all reports are generated from one run.

See the [configuration reference](configuration.md) for the TOML format.
