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

CODEQL_CLI_VERSION = "2.27.4"
# The C/C++-only bundle exists since 2.27.1 (165 MB instead of 550 MB). It ships
# cpp-queries, cpp-all and their libraries, which is all this tooling uses.
# GitHub publishes no .checksum.txt for the per-language assets, so the sha256
# was computed from the release asset itself (Bazel still verifies every
# download against it).
CODEQL_BUNDLE_URL = "https://github.com/github/codeql-action/releases/download/codeql-bundle-v2.27.4/codeql-bundle-cpp-linux64.tar.zst"
CODEQL_BUNDLE_SHA256 = "eea5a76ed6778892278a387c4362fef8d03e5a9c3795f5d5022d496fccfd5388"

CODEQL_CPP_QUERIES_VERSION = "1.10.0"
CODEQL_CPP_ALL_VERSION = "12.4.0"
# Coding standards 2.63.0 (2026-11-16) is built against cpp-all 6.1.4 and ships
# those libraries inside its packs (`.codeql/libraries/`), so it does not
# require the CLI's cpp-all version to match. Its release notes still name CLI
# 2.23.9, but that is only `buildMetadata.cliVersion` (the version the packs
# were built with): the packs are published with OCI descriptors
# (`config.digest`), which older CLIs cannot parse (2.23.9 fails with
# `UnrecognizedPropertyException ... "digest"`), so downloading them needs
# CLI >= 2.26.3.
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
