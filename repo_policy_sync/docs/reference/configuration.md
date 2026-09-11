<!-- ----------------------------------------------------------------------------
  Copyright (c) 2026 Contributors to the Eclipse Foundation

  See the NOTICE file(s) distributed with this work for additional
  information regarding copyright ownership.

  This program and the accompanying materials are made available under the
  terms of the Apache License Version 2.0 which is available at
  https://www.apache.org/licenses/LICENSE-2.0

  SPDX-License-Identifier: Apache-2.0
----------------------------------------------------------------------------- -->

# Configuration reference

The optional `score-repo-policy-sync.toml` file in the current working
directory configures the policy command. Use `--config PATH` to select another
file. If `--config` is not supplied and the default file is absent, the command
continues with its defaults. `--config`, `--json-output`, and
`--markdown-output` are CLI-only.

```toml
[score-repo-policy-sync]

org = "eclipse-score"
policies = ["my-local-policy"]
repos = ["reference_integration"]

# Relative paths are resolved relative to this TOML file.
policy_dirs = ["policies", "shared-policies"]

# Local and bundled policies are enabled by default; list only intentional exclusions.
exclude_policies = [
  "minimum-bazel-version",
]

recreate = false
allow_dirty_pr = false
quiet = false
cache_dir = ".cache/repo-policy-sync"
pull_request_template = "templates/repo-policy-sync-pull-request.md"
sync_workers = 4
policy_workers = 4
```

The TOML keys map to the corresponding CLI options as follows:

| TOML key | CLI option |
| --- | --- |
| `org` | `--org` |
| `policies` | repeated `--policy` |
| `repos` | repeated `--repo` |
| `policy_dirs` | repeated `--policy-dir` |
| `exclude_policies` | repeated `--exclude-policy` |
| `recreate` | `--recreate` / `--no-recreate` |
| `allow_dirty_pr` | `--allow-dirty-pr` / `--no-allow-dirty-pr` |
| `quiet` | `--quiet` / `--no-quiet` |
| `cache_dir` | `--cache-dir` |
| `pull_request_template` | `--pull-request-template` |
| `sync_workers` | `--sync-workers` |
| `policy_workers` | `--policy-workers` |

`policy_dirs` is optional. If it is omitted, `./policies` is used when that
directory exists. Setting it to `[]` disables local policy directories.

`exclude_policies` accepts local or bundled policy directory names. When an option is present on the command line, its value replaces the
corresponding TOML value, including list-valued options. Unknown policy names
and unknown TOML fields are errors.

`pull_request_template` selects the body template for newly created and
updated policy pull requests. Relative TOML paths are resolved relative to the
configuration file. The command-line override resolves relative paths from the
current working directory. If omitted, the packaged
`repo_policy_sync/templates/pull_request.md` template is used.

Templates must contain each supported placeholder exactly by name; whitespace
inside the braces is allowed:

| Placeholder | Rendered value |
| --- | --- |
| `{{ policy_id }}` | Policy identifier. |
| `{{ policy_description }}` | Policy description, or the default description. |
| `{{ policy_trigger }}` | Why the repository matches the policy. |
| `{{ changes }}` | Markdown list of changed files and rationales. |
| `{{ failure_section }}` | Automation failure details, or empty text. |
| `{{ policy_marker }}` | Ownership marker used to identify the policy PR. |
| `{{ policy_head_marker }}` | Branch-head marker used by PR safety checks. |

Missing or unknown placeholders are configuration errors. The ownership and
head markers are mandatory because policy pull-request discovery and safety
checks depend on them.

Values in `repos` use the same repository selection rules as `--repo`: values
without `*`, `?`, or `[` are exact names, while values containing those
characters use case-sensitive Python `fnmatch` semantics. For example,
`repos = ["score*"]` selects every active repository whose name starts with
`score`. A pattern that matches no repository eligible for synchronization is
diagnosed separately from an exact repository name that is not present. When
`--recreate` is used, the expanded selection must contain exactly one
repository before checkout.
