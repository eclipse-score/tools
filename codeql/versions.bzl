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

CODEQL_CLI_VERSION = "2.23.9"
# There is no C/C++-only bundle before 2.27.1, so the full linux64 bundle is
# used (about 550 MB, downloaded once and cached by Bazel). It ships
# cpp-queries, cpp-all and their libraries, which is all this tooling uses. The
# sha256 is the one published in the release's `.checksum.txt`.
CODEQL_BUNDLE_URL = "https://github.com/github/codeql-action/releases/download/codeql-bundle-v2.23.9/codeql-bundle-linux64.tar.zst"
CODEQL_BUNDLE_SHA256 = "551d66e0298a4498050417cd1324e07ac2a39d58dad73e4641cfdf03677cb245"

CODEQL_CPP_QUERIES_VERSION = "1.5.8"
CODEQL_CPP_ALL_VERSION = "6.1.4"
# Coding standards 2.63.0 (2026-11-16) is supported with CLI 2.23.9 and is built
# against cpp-all 6.1.4 - exactly the library of the 2.23.9 bundle - and ships
# those libraries inside its packs (`.codeql/libraries/`). The registry copy is
# published with OCI descriptors (`config.digest`), which 2.23.9 cannot parse
# (`UnrecognizedPropertyException ... "digest"`); only CLI >= 2.26.3 can
# download it. The packs are therefore installed from the release asset
# (`fetch_packs`, and the automatic fallback in `packs.ensure_packs`) instead of
# from the registry.
CODING_STANDARDS_VERSION = "2.63.0"
# Release asset with the same packs as the registry copy (byte-identical), used
# by `bazel run //codeql:fetch_packs` to build a pack cache without the registry.
# The checksum is the one published in the release's `checksums.txt`.
CODING_STANDARDS_PACKS_URL = "https://github.com/github/codeql-coding-standards/releases/download/v2.63.0/coding-standards-codeql-packs.zip"
CODING_STANDARDS_PACKS_SHA256 = "897f8bfb5a13683ad611fcdd07e4cd2cd96ab3973253c439ea6fc8882b32c8cc"

BCC_VERSION = "0.22.4"
BCC_URL = "https://github.com/kiron1/bazel-compile-commands/releases/download/bazel-compile-commands-v0.22.4/bazel-compile-commands_0.22.4-linux_amd64.zip"
BCC_SHA256 = "bb162adaefb282a9e4f0f399fe6223d1d2fe3740518148c1ca8f6f81db4003f6"

BCC_JAMMY_VERSION = "0.21.0"
BCC_JAMMY_URL = "https://github.com/kiron1/bazel-compile-commands/releases/download/v0.21.0/bazel-compile-commands_0.21.0-jammy_amd64.deb"
BCC_JAMMY_SHA256 = "e57cc545ea9ab6dfc494eb2c5913210099e8e31e947517fd0545844d06949b47"

CODE_COMPLEXITY_PACK_VERSION = "1.0.0"
DIAGNOSTICS_PACK_VERSION = "1.0.0"
