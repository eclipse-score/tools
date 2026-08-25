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

# Bundled policies are enabled by default; list only intentional exclusions.
exclude_bundled_policies = [
  "score-devcontainer-dockerfile-migration",
]

recreate = false
allow_dirty_pr = false
quiet = false
cache_dir = ".cache/repo-policy-sync"
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
| `exclude_bundled_policies` | repeated `--exclude-bundled-policy` |
| `recreate` | `--recreate` / `--no-recreate` |
| `allow_dirty_pr` | `--allow-dirty-pr` / `--no-allow-dirty-pr` |
| `quiet` | `--quiet` / `--no-quiet` |
| `cache_dir` | `--cache-dir` |
| `sync_workers` | `--sync-workers` |
| `policy_workers` | `--policy-workers` |

`policy_dirs` is optional. If it is omitted, `./policies` is used when that
directory exists. Setting it to `[]` disables local policy directories.

`exclude_bundled_policies` accepts bundled policy directory names. When an option is present on the command line, its value replaces the
corresponding TOML value, including list-valued options. Unknown policy names
and unknown TOML fields are errors.
