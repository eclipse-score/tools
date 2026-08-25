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

`COMMAND` is one of `plan`, `apply`, or `collect-samples`. Policy options may
also be set in the TOML configuration. Explicit
command-line values override values from the configuration. The organization
may therefore be supplied either with `--org` or in TOML. Report output paths
are CLI-only.

## Commands

| Command | Description |
| --- | --- |
| `plan` | Evaluate policies without changing repositories. Exit status `1` indicates drift. |
| `apply` | Apply policies and create or update policy-owned pull requests. |
| `collect-samples` | Collect policy-matching repository files into a local sample directory without changing repositories. Requires `--output DIRECTORY`. |

## Typical

| Option | Description |
| --- | --- |
| `--org NAME` | GitHub organization to scan. May be set in TOML. |
| `--policy NAME` | Select a local policy by directory name from the selected local policy directories. Repeat to select more than one. Defaults to all local policies. Bundled SCORE policies are included separately by default. |
| `--repo NAME` | Restrict the run to an exact repository name. Repeat to select more than one. |
| *(stdout)* | Always prints the terminal policy-evaluation table. |

## Rare

| Option | Description |
| --- | --- |
| `--config PATH` | TOML configuration file. Defaults to `score-repo-policy-sync.toml` in the current working directory when present. This and the report output path options are CLI-only. |
| `--json-output PATH` | Also write the versioned JSON report to `PATH`. |
| `--markdown-output PATH` | Also write the Markdown report to `PATH`. |
| `--policy-dir PATH` | Local policy directory. Repeat to combine directories. Defaults to `./policies` in the current working directory when present. |
| `--exclude-bundled-policy NAME` | Exclude one bundled SCORE policy. Repeat to exclude more than one. All other bundled policies are included by default. |
| `--recreate` | On `apply`, rebuild one existing policy-owned pull request from its repository's current default branch. Requires exactly one `--repo` and exactly one `--policy`. |
| `--allow-dirty-pr`, `--no-allow-dirty-pr` | After the automatic formatting-fix retry, commit and push changes even if pre-commit still fails; create or keep the pull request as a draft and add a comment with the failure. |
| `--quiet`, `--no-quiet` | Suppress progress messages on standard error. The report remains on standard output. |

## Debugging only

| Option | Description |
| --- | --- |
| `--cache-dir PATH` | Directory for disposable checkouts. Defaults to the XDG cache directory. |
| `--sync-workers N` | Number of concurrent checkout refreshes. Defaults to the available CPU count, with a minimum of `1`. |
| `--policy-workers N` | Number of repositories evaluated or applied concurrently for each policy. Defaults to the available CPU count, with a minimum of `1`. Set to `1` to process policies serially. |
| `--output DIRECTORY` | On `collect-samples`, empty output directory for collected files and `inventory.json`. |

Archived repositories are excluded from every run. Selecting an archived
repository with `--repo` fails validation instead of silently ignoring it.

## Exit status

| Status | Meaning |
| --- | --- |
| `0` | The plan found no required changes, or apply mode completed without errors. |
| `1` | Plan mode found one or more required changes. |
| `2` | Invalid input or an authentication, GitHub, Git, or policy execution error occurred. |

## JSON report

`--json-output PATH` writes one JSON document to `PATH`. The top-level
`schema_version` currently has value `2`. The `summary` object separately
reports repository synchronization, policy evaluation, pull-request activity
(including automatic closures), and elapsed duration; each `outcomes` element
includes the policy, repository, applicability result, status, planned or
applied changes, pull request URL, policy pull-request status, warnings, and
error. `--markdown-output PATH`
writes the compact Markdown matrix to `PATH`. JSON and Markdown output paths
can be supplied together so all reports are generated from one run.

`collect-samples --output DIRECTORY` writes the matching repository files below
`DIRECTORY/<policy>/<repository>/before/` and an `inventory.json` describing
the collected cases. The directory must be empty or not yet exist.

See the [configuration reference](configuration.md) for the TOML format.
