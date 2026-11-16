# *******************************************************************************
# Copyright (c) 2026 Contributors to the Eclipse Foundation
#
# See the NOTICE file(s) distributed with this work for additional
# information regarding copyright ownership.
#
# This program and the accompanying materials are made available under the
# terms of the Apache License Version 2.0 which is available at
# https://www.apache.org/licenses/LICENSE-2.0
#
# SPDX-License-Identifier: Apache-2.0
# *******************************************************************************
"""Pinned versions of everything the CodeQL tooling downloads or depends on.

Single source of truth for the constants used by docs and tests. Starlark loaded
by MODULE.bazel is not possible, so the repository rules in `MODULE.bazel` carry
the same URLs and checksums inline. When bumping a pin, update all of:

  * this file,
  * the `http_archive`/`http_file` blocks in `MODULE.bazel`,
  * `config/packs.toml` and `config/reports.toml`,
  * the pinned versions table in `README.md`,
  * `queries/*/qlpack.yml` (+ `codeql-pack.lock.yml`),

`codeql/tests` fails when they disagree.
"""

CODEQL_CLI_VERSION = "2.21.4"
# 2.21.4 has no C/C++-only bundle, so the full linux64 bundle is pinned.
CODEQL_BUNDLE_URL = "https://github.com/github/codeql-action/releases/download/codeql-bundle-v2.21.4/codeql-bundle-linux64.tar.zst"
CODEQL_BUNDLE_SHA256 = "6da9f3df228d4e162af877569240a627c1af22f6adbd26352b22357fb8bd7545"

CODEQL_CPP_QUERIES_VERSION = "1.4.1"
CODEQL_CPP_ALL_VERSION = "5.0.0"
CODING_STANDARDS_VERSION = "2.62.0"

BCC_VERSION = "0.22.4"
BCC_URL = "https://github.com/kiron1/bazel-compile-commands/releases/download/bazel-compile-commands-v0.22.4/bazel-compile-commands_0.22.4-linux_amd64.zip"
BCC_SHA256 = "bb162adaefb282a9e4f0f399fe6223d1d2fe3740518148c1ca8f6f81db4003f6"

BCC_JAMMY_VERSION = "0.21.0"
BCC_JAMMY_URL = "https://github.com/kiron1/bazel-compile-commands/releases/download/v0.21.0/bazel-compile-commands_0.21.0-jammy_amd64.deb"
BCC_JAMMY_SHA256 = "e57cc545ea9ab6dfc494eb2c5913210099e8e31e947517fd0545844d06949b47"

CODE_COMPLEXITY_PACK_VERSION = "1.0.0"
DIAGNOSTICS_PACK_VERSION = "1.0.0"
