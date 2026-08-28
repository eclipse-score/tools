<!-- ----------------------------------------------------------------------------
  Copyright (c) 2026 Contributors to the Eclipse Foundation

  See the NOTICE file(s) distributed with this work for additional
  information regarding copyright ownership.

  This program and the accompanying materials are made available under the
  terms of the Apache License Version 2.0 which is available at
  https://www.apache.org/licenses/LICENSE-2.0

  SPDX-License-Identifier: Apache-2.0
----------------------------------------------------------------------------- -->

# Repository Cache

Maintain a local, disposable Git checkout of every repository in a GitHub
organization. Used by other SCORE tools that need to operate across an
entire organization's repositories without re-cloning them on every run.

Talks to GitHub exclusively through the `gh` CLI, and has zero non-stdlib
Python dependencies, so it can be installed standalone in another
Bazel-based or `uv`-based repository:

```bash
uv add repo-cache --git https://github.com/eclipse-score/score_tools --subdirectory repo_cache
```

## CLI

```bash
score-repo-cache list --org eclipse-score
score-repo-cache sync --org eclipse-score --repo score --repo score_tools
```

`sync` clones each selected repository's default branch into
`~/.cache/repo-cache/<org>/<name>` (override with `--cache-dir`), or fetches
and resets an existing checkout back to a clean state if it was already
cloned there.

## Library

```python
from pathlib import Path
from repo_cache import sync_org

report = sync_org(org="eclipse-score", cache_dir=Path.home() / ".cache/repo-cache")
for outcome in report.failures:
    print(outcome.repository.name, outcome.error)
```

## Bazel

Add `@score_tools//repo_cache:repo_cache` to your `py_library`/`py_binary`
`deps` to use it from another Bazel workspace that depends on `score_tools`.
