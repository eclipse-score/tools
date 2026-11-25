<!-- ----------------------------------------------------------------------------
  Copyright (c) 2026 Contributors to the Eclipse Foundation

  See the NOTICE file(s) distributed with this work for additional
  information regarding copyright ownership.

  This program and the accompanying materials are made available under the
  terms of the Apache License Version 2.0 which is available at
  https://www.apache.org/licenses/LICENSE-2.0

  SPDX-License-Identifier: Apache-2.0
----------------------------------------------------------------------------- -->

# CodeQL analysis

`codeql_analysis` defines a `bazel run` target that analyses C/C++ code with
CodeQL and the [CodeQL Coding Standards](https://github.com/github/codeql-coding-standards)
(MISRA C++:2023 by default).

- **Traced build**: the database is created by tracing a real `bazel build` of
  the given targets. CodeQL sees every compiler and linker invocation, so rules
  across translation units work. Caches are bypassed for this build, so every
  action runs.
- **Pinned and offline**: CodeQL bundle, coding-standards sources and the
  pre-compiled query pack are Bazel repositories pinned by sha256.
- **Coding standards workflow**: deviations from every `coding-standards.yml`
  in the workspace are applied, guideline recategorizations of the configured
  file are applied to the SARIF, and the compliance reports of the coding
  standards (`analysis_report`) are written next to it.

## Usage

```starlark
# MODULE.bazel
bazel_dep(name = "score_tools", version = "<release>")
```

```starlark
# BUILD
load("@score_tools//codeql:defs.bzl", "codeql_analysis")

codeql_analysis(
    name = "codeql",
    targets = ["//score/..."],
    # Bazel configs of the traced build, e.g. one selecting a toolchain whose
    # compiler can be traced (dynamically linked).
    build_configs = ["codeql"],
    coding_standards_config = "quality/coding-standards.yml",
)
```

```bash
bazel run //:codeql -- --output-dir=/tmp/codeql --output-prefix=codeql-linux
# Another platform, e.g. QNX
bazel run //:codeql -- --build-config=qnx --output-prefix=codeql-qnx
```

Arguments after `--` are appended to the ones of the macro: `--target`
(several patterns per flag allowed, exclusions as `--target=-//pkg/...`),
`--build-config` and `--bazel-arg` add to the configured lists, `--output-dir`
(default: `bazel info output_path`), `--output-prefix`, `--database` /
`--database-path` (keep the database instead of using a temporary directory),
`--phase=create-database|analyze-database` (only one half, on the kept
`--database`), `--query-spec` (analyse one query from the coding-standards
sources instead of the pack suite, e.g. to debug a rule) and `--bazel` (Bazel
executable of the traced build, e.g. a wrapper that adds startup options).

| Attribute | Meaning |
| --- | --- |
| `targets` | Bazel target patterns to build and analyse. |
| `build_configs` | `--config` values of the traced build. |
| `bazel_args` | Further flags of the traced build. |
| `exclude_testonly` | Build only C/C++ (`cc_*`) targets that are not `testonly` (tests, mocks, test helpers). |
| `keep_going` | Analyse what was built even if some targets fail to build. |
| `pack` | Pre-compiled coding-standards pack, currently `misra-cpp`. |
| `suite` | Suite inside the pack, default `codeql-suites/misra-cpp-default.qls`. |
| `coding_standards_config` | Workspace-relative `coding-standards.yml` whose guideline recategorizations are applied to the SARIF. |
| `codescanning_config` | CodeQL code scanning config (query filters). |

The compiler of the traced build must be traceable by CodeQL. Statically
linked compilers are not; use a config that selects a dynamically linked
toolchain for the analysis.

## Updating the pins

The CodeQL bundle, `CODEQL_CODING_STANDARDS_VERSION` and the pack sha256 in
`MODULE.bazel` must be updated together: each coding-standards release names
the CodeQL CLI version it supports. The Python dependencies of the coding
standards scripts are locked in `third_party/requirements_lock.txt`
(`bazel run //codeql/third_party:pip_requirements.update`).
