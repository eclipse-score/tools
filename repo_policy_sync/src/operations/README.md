<!-- ----------------------------------------------------------------------------
  Copyright (c) 2026 Contributors to the Eclipse Foundation

  See the NOTICE file(s) distributed with this work for additional
  information regarding copyright ownership.

  This program and the accompanying materials are made available under the
  terms of the Apache License Version 2.0 which is available at
  https://www.apache.org/licenses/LICENSE-2.0

  SPDX-License-Identifier: Apache-2.0
----------------------------------------------------------------------------- -->

# Built-in policy operations

Policy `ensure` entries use the operation types registered in
[`__init__.py`](__init__.py). This directory contains every operation
available to a policy; operation IDs are part of the policy file format and
must be registered before they can be used.

Use the catalogue below to choose an operation. The
[policy format reference](../docs/reference/policy-format.md) is the
authoritative source for the complete schema, validation rules, and examples.

## Operations

| Operation | Use it for | Main behavior | Tests |
| --- | --- | --- | --- |
| `ensure_line` | Keeping one exact line in a text file | Inserts the desired line, removes configured replacements and duplicates, and creates a missing file. | [`test_ensure_line.py`](../tests/operations/test_ensure_line.py) |
| `ensure_minimum_version` | Maintaining a simple version file such as `.bazelversion` | Replaces a lower `major.minor.patch` value; equal or higher versions and missing files are compliant. | [`test_ensure_minimum_version.py`](../tests/operations/test_ensure_minimum_version.py) |
| `remove_file` | Removing an obsolete file | Deletes an existing file; a missing file is compliant and directories are rejected. | [`test_remove_file.py`](../tests/operations/test_remove_file.py) |
| `replace_regex` | Applying a narrow text substitution | Applies Python `re.sub` to a complete UTF-8 file; missing files and non-matching patterns are compliant. | [`test_replace_regex.py`](../tests/operations/test_replace_regex.py) |

All operations accept an optional `rationale`. When a change is needed, the
rationale is included with the generated change description.

## Common rules

- Operation paths are relative to the repository root unless the policy
  format explicitly describes a policy-local source asset.
- Policies are evaluated for applicability first. A policy that does not match
  its `when` conditions makes no change.
- Operations are deterministic and idempotent: a compliant repository can be
  evaluated repeatedly without producing further changes.
- A policy gathers its changes before applying them, so files created by one
  operation are not visible to later operations in that same evaluation.
- Invalid or ambiguous input is rejected during policy loading or evaluation;
  operations do not silently guess at an unsupported file format.

## Related documentation

- [Policy format reference](../docs/reference/policy-format.md) — complete
  operation schemas and semantics.
- [Documentation index](../docs/README.md) — tutorials, how-to guides,
  reference pages, and explanations.
- [Run a policy](../docs/how-to/run-a-policy.md) — plan and apply a policy.
- [Bundled policy overview](../policies/README.md) — policies shipped with the
  repository and their intended lifecycle.
