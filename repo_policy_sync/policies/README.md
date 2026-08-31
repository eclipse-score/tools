<!-- ----------------------------------------------------------------------------
  Copyright (c) 2026 Contributors to the Eclipse Foundation

  See the NOTICE file(s) distributed with this work for additional
  information regarding copyright ownership.

  This program and the accompanying materials are made available under the
  terms of the Apache License Version 2.0 which is available at
  https://www.apache.org/licenses/LICENSE-2.0

  SPDX-License-Identifier: Apache-2.0
----------------------------------------------------------------------------- -->

# Bundled policy overview

This directory contains the bundled repository policies. Each policy lives in
its own directory and is defined by a `policy.yml` file. The directory name is
the policy ID used by the CLI, policy-owned branches, and pull-request markers.

The policies are evaluated independently. A policy that does not match its
`when` conditions is not applicable and makes no change; a matching policy
reports or applies only the changes described by its own `ensure` operations.

For tutorials, how-to guides, interface reference, and design explanations,
use the [documentation index](../docs/README.md).

## Policies

| Policy | Responsibility | Typical lifecycle |
| --- | --- | --- |
| `docs-as-code-gitignore` | Update `score_docs_as_code` Git ignore entries and remove legacy configuration files. | One-time cleanup |
| `minimal-bazel-module-declaration` | Keep `MODULE.bazel` limited to the repository-owned module name by removing version metadata. | One-time cleanup |
| `minimum-bazel-version` | Upgrade repositories to at least Bazel `8.6.0` and regenerate the lockfile when required. | Baseline maintenance |
| `score-devcontainer-dependency-alignment` | Declare the direct SCORE devcontainer Bazel dependency using the Dockerfile image version. | One-time integration |

The policy definitions and their executable before/after cases are the
authoritative detail. The [policy format reference](../docs/reference/policy-format.md)
covers the schema and operation semantics; the [run-a-policy how-to](../docs/how-to/run-a-policy.md)
covers execution.
