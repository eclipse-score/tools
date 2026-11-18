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
load("@aspect_rules_py//py:defs.bzl", "py_binary", "py_library")

py_library(
    name = "coverage_report",
    srcs = [
        "__init__.py",
    ] + glob(["src/*.py"]),
    visibility = ["//visibility:public"],
)

py_binary(
    name = "score-coverage-report",
    main = "__main__.py",
    srcs = [
        "__main__.py",
        ":coverage_report",
    ],
    visibility = ["//visibility:public"],
)
