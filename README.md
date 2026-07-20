<!--
*******************************************************************************
Copyright (c) 2026 Contributors to the Eclipse Foundation

See the NOTICE file(s) distributed with this work for additional
information regarding copyright ownership.

This program and the accompanying materials are made available under the
terms of the Apache License Version 2.0 which is available at
https://www.apache.org/licenses/LICENSE-2.0

SPDX-License-Identifier: Apache-2.0
*******************************************************************************
-->

# Tools

## Why this repository exists

The utilities/tools in this repository used to live inside [tooling](https://github.com/eclipse-score/tooling). That
repo works well for its main purpose, but it also pulls in a large and
growing dependency graph because it bundles many unrelated concerns together.

That created a real cost for consumers: if you only needed one small,
self-contained utility, building or depending on it meant pulling in the
entire dependency graph of `tooling`, even though 99% of it was
irrelevant to what you actually wanted.

This repository exists to fix that. It pulls a set of small, self-contained
utilities out of `tooling` into their own home, so that depending on
a utility costs you only what that utility actually needs — not the
dependency footprint of an unrelated, much larger codebase.

## Scope

This repo is intentionally narrow. To stay useful, it needs to *stay*
narrow — the whole point of splitting it out was to avoid recreating the
same problem in a new location.

**In scope:**
- Small, self-contained, general-purpose utilities with minimal external
  dependencies.
- Code with no coupling to `tooling`.
- Utilities that are (or are likely to be) reused across multiple,
  otherwise-unrelated projects.

**Out of scope:**
- Anything tied to a specific product, service, or domain — that belongs in
  the repo that owns that domain.
- Utilities that only make sense in the context of `tooling`.
- Anything that would pull in a large or heavyweight dependency for the
  benefit of a single utility. If a proposed addition needs a big
  dependency, prefer keeping it in whichever repo already depends on that
  library, or give it its own repo instead of adding it here.
- Grab-bag/"misc" code with no clear justification for living here. When in
  doubt, ask whether this utility would want to be depended on by something
  that doesn't want any of the other dependencies this repo would then
  bring in. If the answer isn't clearly yes, it doesn't belong here.

## Adding something new

Before adding a new utility here, check:
1. Is it genuinely general-purpose, not tied to one product's domain logic?
2. Does it avoid pulling in dependencies that most other things in this
   repo don't already need?
3. Would splitting it into its own small repo/target actually serve
   consumers better than adding it here?

If any of these gives you pause, raise it for discussion before merging.
