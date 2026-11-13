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
"""Pinned CodeQL CLI bundle (C/C++ only), exposed to the `//codeql:sca` tool."""

filegroup(
    name = "bundle",
    srcs = glob(["codeql/**"]),
    visibility = ["//visibility:public"],
)

# The CLI locates `tools/`, `cpp/` and `qlpacks/` relative to its own location,
# so the whole bundle travels along as runfiles of the launcher.
filegroup(
    name = "codeql_cli",
    srcs = ["codeql/codeql"],
    data = [":bundle"],
    visibility = ["//visibility:public"],
)
