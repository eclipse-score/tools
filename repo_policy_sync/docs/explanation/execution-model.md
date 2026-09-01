<!-- ----------------------------------------------------------------------------
  Copyright (c) 2026 Contributors to the Eclipse Foundation

  See the NOTICE file(s) distributed with this work for additional
  information regarding copyright ownership.

  This program and the accompanying materials are made available under the
  terms of the Apache License Version 2.0 which is available at
  https://www.apache.org/licenses/LICENSE-2.0

  SPDX-License-Identifier: Apache-2.0
----------------------------------------------------------------------------- -->

# Execution model and boundaries

Repository Policy Sync evaluates the bundled SCORE catalogue and, when
present, local policies from `./policies` or the directories selected with
`--policy-dir`. Policies can be excluded with `--exclude-policy`. Policy names
selected with `--policy` are resolved across local and bundled policy
directories, and an explicit selection runs only those policies. A policy that needs changes
owns one deterministic branch and one pull request per repository.

## Lifecycle

1. Discover organization repositories and load policies. Policy definitions
   are discovered as `policy.yml` files in the selected policy directory in
   deterministic path order.
2. Refresh each selected repository’s disposable checkout to its current default branch.
3. Evaluate the policy against that checkout. The checkout is the
   authority for applicability and compliance. Conditions are checked first;
   once they match, policy-local values are resolved and passed into the
   operations that reference them.
4. In plan mode, report required changes and make no remote changes.
5. In apply mode, reuse a policy-owned pull request when present, otherwise
   create a policy branch, apply the policy, run the configured pre-commit hooks
   on the policy-changed paths when the target repository has
   `.pre-commit-config.yaml`, commit, push, and open a pull request. If the
   first pre-commit run applies formatting fixes,
   the changes are staged and pre-commit is run once more before publishing.
   The same pre-commit gate runs before rebuilding an existing policy branch.
   Existing policy-owned pull requests receive the current title and body. If
   pre-commit still fails after the retry, no commit, push, or pull request is
   created. With `--allow-dirty-pr`, the changes are committed and pushed
   anyway, and the resulting pull request is draft with a comment containing
   the remaining pre-commit failure.
   If the refreshed default branch is already compliant, apply mode closes an
   existing policy-owned pull request after verifying its branch head; plan
   mode leaves the pull request open.

   Pre-commit runs use a credential-reduced environment and temporary home
   directory. Hooks are still arbitrary repository code, so apply mode requires
   trusted target repositories.

Checkout synchronization and each policy's repository processing run in
parallel. Repositories remain isolated in separate checkouts; use
`--sync-workers` and `--policy-workers` to bound their respective concurrency.

## Supported behavior

- `when.bazel` dependency-presence and version conditions,
  `when.file_exists`, `when.file_contains`, and `when.value_exists` conditions;
- policy-local derived values, including Dockerfile image-version sources,
  which can be passed explicitly into operations;
- the explicitly registered `ensure_line`, `ensure_minimum_version`,
  `remove_file`, `replace_regex`, and `ensure_bazel_dependency` operations;
- fixed `automation` and `repo-policy-sync` labels, policy titles, descriptions, and
  per-operation rationales;
- terminal-table output, a compact repository-by-policy Markdown matrix, and a
  versioned JSON report for automation.

## Exit status

| Status | Meaning |
| --- | --- |
| `0` | Plan or apply mode completed without input, authentication, GitHub, Git, or policy execution errors. Plan mode may still report policy drift. |
| `2` | Input, authentication, GitHub, Git, or execution error. |

## Current boundaries

- Handling a remote policy branch that has no open policy-owned pull request.
- Retries and rate-limit handling.
- Additional conditions and built-in operations.
