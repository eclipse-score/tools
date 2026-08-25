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
| `docs-as-code-legacy-configuration-removal` | Remove obsolete `score_docs_as_code` documentation configuration and legacy ignore entries. | One-time cleanup |
| `minimal-bazel-module-declaration` | Keep `MODULE.bazel` limited to the repository-owned module name by removing version metadata. | One-time cleanup |
| `minimum-bazel-version` | Upgrade repositories to at least Bazel `8.6.0` and regenerate the lockfile when required. | Baseline maintenance |
| `score-bazel-dependency-alignment` | Align SCORE platform, documentation, base-library, and process dependencies, including the `score_process` rename. | Coordinated upgrade |
| `score-devcontainer-dockerfile-migration` | Convert an image-based SCORE devcontainer to a Dockerfile-based configuration. | One-time migration |
| `score-devcontainer-standardization` | Add the direct SCORE devcontainer dependency, standard `run-tool` launcher, and supported launcher paths. | One-time integration |
| `score-devcontainer-version-alignment` | Keep the devcontainer image version and direct Bazel dependency version synchronized. | Recurring maintenance |
| `score-docs-workflow-alignment` | Align shared SCORE documentation build and publish workflows while preserving safe repository-specific content. | Workflow maintenance |

The policy definitions and their executable before/after cases are the
authoritative detail. The [policy format reference](../docs/reference/policy-format.md)
covers the schema and operation semantics; the [run-a-policy how-to](../docs/how-to/run-a-policy.md)
covers execution.

## Devcontainer rollout order

The bundled SCORE devcontainer policies deliberately cover distinct lifecycle
stages:

1. `score-devcontainer-dockerfile-migration` converts an image-based
   `devcontainer.json` so Dependabot can update the development image.
2. `score-devcontainer-standardization` adds the direct Bazel dependency and
   standard SCORE tool launcher after a Dockerfile exists.
3. `score-devcontainer-version-alignment` handles later image or module version
   changes as recurring maintenance.

The migration and standardization policies are one-time operations. The
version policy is independent because it is the policy expected to create a
pull request again when a maintainer updates only one of the two version
declarations. The standardization policy may become applicable again if its
managed `run-tool` source asset changes.

Keep these concerns in separate policies. A single policy run evaluates its
changes before applying them, so newly created files are not visible to later
operations in that same evaluation.
