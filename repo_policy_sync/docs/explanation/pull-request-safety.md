<!-- ----------------------------------------------------------------------------
  Copyright (c) 2026 Contributors to the Eclipse Foundation

  See the NOTICE file(s) distributed with this work for additional
  information regarding copyright ownership.

  This program and the accompanying materials are made available under the
  terms of the Apache License Version 2.0 which is available at
  https://www.apache.org/licenses/LICENSE-2.0

  SPDX-License-Identifier: Apache-2.0
----------------------------------------------------------------------------- -->

# Pull request safety

Each changed repository receives at most one open pull request for a policy.
The policy directory name is the policy ID and maps to the deterministic branch
`repo-policy-sync/<normalized-policy-id>`, so subsequent runs update the same pull
request instead of creating duplicates.

PR discovery uses the `repo-policy-sync` label and then matches the policy
marker in the body. A policy can list former IDs in `legacy_names`, allowing
historical branch names and the former `repo-sync` marker format to remain
visible during a policy migration. New pull requests always use the current
policy ID and branch scheme.

## Ownership and safety

The label identifies the tool and the PR body contains an invisible marker for
the policy. The body may also contain a marker for the branch head created by
Repository Policy Sync. Before reusing or closing a PR in apply mode, the tool
verifies that its remote head still matches the stored value. A missing head
marker or a mismatch fails the run without updating, closing, or otherwise
taking over the pull request. This protects a policy branch that someone has
changed manually. Plan mode can report a matching historical PR even when its
old body has no head marker.

If the refreshed default branch is compliant while an owned PR remains open,
apply mode closes that PR after verifying the same branch-head marker used for
other owned-branch changes. Plan mode does not close it. A changed or missing
branch-head marker prevents closure and leaves the PR open for human review.

## Generated content

The runtime template is [pull_request.md](../../templates/pull_request.md). It
contains the policy identity and description, the non-compliant files that
triggered the pull request, any satisfied applicability condition, and the
concrete changed files. A change can include one operation-level rationale; it
is rendered as a nested bullet below that change. The template also states that
the pull request is generated and must be reviewed before merging.

Repository Policy Sync applies the `automation` and `repo-policy-sync` labels
after PR creation. Before creating a PR, it creates either label when it is
absent from the repository; existing labels are not modified. On later runs,
an owned PR whose branch already contains the policy changes is left alone when
its generated body is current; a changed template or explanation updates only
the PR text. If GitHub reports that this unchanged policy branch conflicts with
the target branch, the tool rebuilds it from the freshly synchronized default
branch and reapplies the policy. If applying a policy fails, the tool records
the error and closes that PR only after the same branch-head verification
succeeds.
