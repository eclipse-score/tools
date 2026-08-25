<!-- ----------------------------------------------------------------------------
  Copyright (c) 2026 Contributors to the Eclipse Foundation

  See the NOTICE file(s) distributed with this work for additional
  information regarding copyright ownership.

  This program and the accompanying materials are made available under the
  terms of the Apache License Version 2.0 which is available at
  https://www.apache.org/licenses/LICENSE-2.0

  SPDX-License-Identifier: Apache-2.0
----------------------------------------------------------------------------- -->

# Alternatives and rationale

This guide explains why Repository Policy Sync exists alongside other fleet
maintenance approaches.

SCORE Repository Policy Sync exists to manage organization-wide repository
policies: desired states that are declarative, repeatable, reviewable, and safe
to reconcile again. It is not intended to be the only way to edit many
repositories.

## Decision criteria

A suitable policy tool must support more than cloning repositories and applying
a text change. In particular, it needs to:

- select repositories efficiently while making the final decision from current
  checkout content;
- express an idempotent desired state and explain why a change is needed;
- test a policy against small before/after repository fixtures;
- create one identifiable pull request per policy and repository; and
- update that pull request predictably on later runs without taking over a
  user-owned branch.

## all-repos

[all-repos](https://github.com/asottile/all-repos) is a mature tool for cloning
a configured set of repositories and applying sweeping changes. Its distributed
`grep` and `sed` commands, custom autofixers, repository discovery, and
parallel execution make it a strong choice for one-off or imperative fleet
maintenance.

It is not the foundation for Repository Policy Sync because its generic
clone/autofix/push model does not provide this project's policy contract out of
the box:

- YAML policies with checked-in executable examples;
- live checkout-based compliance evaluation across all selected repositories;
- per-policy applicability conditions and operation rationales; or
- stable policy ownership of branch names and pull-request bodies.

Building those features as an all-repos autofixer and custom push integration
would retain its configuration and authentication model while duplicating the
core behavior of Repository Policy Sync. That increases the number of control
planes without reducing the policy-specific code.

Use all-repos when a maintainer needs an ad-hoc, imperative sweep. Use
Repository Policy Sync when the change is an enduring organization policy that
should be stored, tested, and rerun as a policy.

## GitHub Actions and scripts

GitHub Actions is appropriate for repository-local enforcement, such as a
format or validation check that runs on every pull request. It is less suitable
for centrally discovering, evaluating, and remediating a changing set of
repositories. A standalone script has the opposite trade-off: it is quick for
a one-off migration but does not naturally preserve policy identity, fixtures,
or pull-request lifecycle.

Ansible's
[`ansible.builtin.blockinfile`](https://docs.ansible.com/projects/ansible/latest/collections/ansible/builtin/blockinfile_module.html)
is a useful declarative option when a known file on a known target needs one
managed, marker-delimited text block. It is idempotent and supports check and
diff modes, but is deliberately a file-editing primitive rather than a
repository-policy system. Using it for this problem would still require custom
inventory or repository discovery, checkout evaluation, fixture testing, and
pull-request lifecycle management around the playbook.

The current design keeps these roles separate:

- repository-local automation remains in each repository or its GitHub Actions;
- ad-hoc fleet edits can use all-repos or a focused script; and
- Ansible can manage a known, marker-delimited block in a known file; and
- durable cross-repository policy remediation belongs in Repository Policy Sync.
