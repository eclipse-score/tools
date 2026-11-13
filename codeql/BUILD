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

load("@rules_python//python:defs.bzl", "py_library")
load(":defs.bzl", "codeql_sca_binary", "codeql_tool_binary")

package(default_visibility = ["//visibility:public"])

exports_files(
    [
        "config/packs.toml",
        "config/reports.toml",
        "queries/code-complexity/qlpack.yml",
        "queries/diagnostics/qlpack.yml",
        "score_codeql/__main__.py",
        "score_codeql/list_rules.py",
        "scripts/bcc.sh",
        "scripts/bcc_bazel_wrapper.sh",
        "versions.bzl",
        "wrapper/codeql_cc_wrapper.c",
    ],
)

py_library(
    name = "score_codeql",
    srcs = glob(
        ["score_codeql/**/*.py"],
        # Entry points are sources of their py_binary targets instead (otherwise
        # both targets would emit the same .pyc).
        exclude = [
            "score_codeql/__main__.py",
            "score_codeql/list_rules.py",
        ],
        allow_empty = True,
    ),
    imports = ["."],
    deps = ["@rules_python//python/runfiles"],
)

# Generic entry point: `bazel run @score_tools//codeql:sca -- --config=... //pkg/...`.
# Consumers usually define their own target with `codeql_sca` (see defs.bzl).
codeql_sca_binary(name = "sca")

# Rule inventory of the selected reports: one `queries.csv` per pack reference.
# `bazel run //codeql:list_rules -- --safety-level=ASIL --output-dir=/tmp/rules`
codeql_tool_binary(
    name = "list_rules",
    main = "//codeql:score_codeql/list_rules.py",
)

filegroup(
    name = "config",
    srcs = [
        "config/packs.toml",
        "config/reports.toml",
    ],
)

filegroup(
    name = "scripts",
    srcs = [
        "scripts/bcc.sh",
        "scripts/bcc_bazel_wrapper.sh",
    ],
)

filegroup(
    name = "wrapper",
    srcs = ["wrapper/codeql_cc_wrapper.c"],
)

# Local CodeQL packs, passed to the CLI with --additional-packs.
filegroup(
    name = "code_complexity_pack",
    srcs = glob(["queries/code-complexity/**"]),
)

filegroup(
    name = "diagnostics_pack",
    srcs = glob(["queries/diagnostics/**"]),
)
