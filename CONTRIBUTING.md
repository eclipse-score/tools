<!-- ----------------------------------------------------------------------------
  Copyright (c) 2026 Contributors to the Eclipse Foundation

  See the NOTICE file(s) distributed with this work for additional
  information regarding copyright ownership.

  This program and the accompanying materials are made available under the
  terms of the Apache License Version 2.0 which is available at
  https://www.apache.org/licenses/LICENSE-2.0

  SPDX-License-Identifier: Apache-2.0
----------------------------------------------------------------------------- -->

# Contributing

## Why this repository exists

The utilities in this repository used to live inside
[tooling](https://github.com/eclipse-score/tooling). That repository serves
its main purpose well, but it also brings a large and growing dependency graph
because it bundles many unrelated concerns together.

For consumers who need only one small, self-contained utility, building or
depending on it meant pulling in the dependency graph of `tooling`, even when
most of it was irrelevant. This repository gives those utilities a separate
home so consumers can depend on what they need without taking on unrelated
dependencies.

## Repository scope

This repository is intentionally narrow. Its components should remain small,
self-contained, and useful across otherwise unrelated projects.

**In scope:**

- General-purpose utilities with minimal external dependencies.
- Code with no coupling to `tooling`.
- Utilities that are, or are likely to be, reused across multiple projects.

**Out of scope:**

- Code tied to a specific product, service, or domain; that belongs in the
  repository that owns that domain.
- Utilities that only make sense in the context of `tooling`.
- Additions that bring in a large dependency for the benefit of a single
  utility. If a proposed addition needs a heavyweight dependency, consider
  keeping it in a repository that already uses that library or giving it a
  separate repository.
- Grab-bag code without a clear reason to live here. Consider whether a
  consumer would want this utility without also taking on the dependencies of
  the other components in this repository.

## Proposing a new component

Before adding a utility, consider:

1. Is it general-purpose rather than tied to one product's domain logic?
2. Does it avoid adding dependencies that most other components here do not
   already need?
3. Would consumers be better served by putting it in its own small
   repository or target?

If any answer is unclear, raise the proposal for discussion before merging.
