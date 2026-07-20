# *******************************************************************************
# Copyright (c) 2024 Contributors to the Eclipse Foundation
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
load("@aspect_rules_py//py:defs.bzl", "py_library")

# `templates.ini` is inside srcs. 
# This is because if it would be in the `data` attr we would need to find the actual path of it with a lot of effort
# So this is a good workaround that does not change the workings of the lib.
py_library(
    name = "cr_checker_lib",
    srcs = [
        "cr_checker.py",
        "templates.ini",
    ],
    visibility = ["//visibility:public"],
)
