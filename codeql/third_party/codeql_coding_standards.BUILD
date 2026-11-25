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
load("@rules_python//python:defs.bzl", "py_binary")
load("@score_tools_codeql_pip//:requirements.bzl", "requirement")

exports_files([
    "cpp/common/src/qlpack.yml",
    "schemas/coding-standards-schema-1.0.0.json",
    "schemas/sarif-schema-2.1.0.json",
])

py_binary(
    name = "process_coding_standards_config",
    srcs = ["scripts/configuration/process_coding_standards_config.py"],
    python_version = "3.12",
    visibility = ["//visibility:public"],
    deps = [requirement("pyyaml")],
)

py_binary(
    name = "guideline_recategorize",
    srcs = ["scripts/guideline_recategorization/recategorize.py"],
    data = [
        "schemas/coding-standards-schema-1.0.0.json",
        "schemas/sarif-schema-2.1.0.json",
    ],
    main = "scripts/guideline_recategorization/recategorize.py",
    python_version = "3.12",
    visibility = ["//visibility:public"],
    deps = [
        requirement("jsonpath-ng"),
        requirement("jsonpatch"),
        requirement("jsonpointer"),
        requirement("jsonschema"),
        requirement("pyyaml"),
    ],
)

py_binary(
    name = "analysis_report",
    srcs = [
        "scripts/reports/analysis_report.py",
        "scripts/reports/codeqlvalidation.py",
        "scripts/reports/deviations.py",
        "scripts/reports/diagnostics.py",
        "scripts/reports/error.py",
        "scripts/reports/guideline_recategorizations.py",
        "scripts/reports/utils.py",
        "scripts/shared/codeql.py",
    ],
    data = ["supported_codeql_configs.json"] + glob(["cpp/**"]),
    imports = [
        "scripts/reports",
        "scripts/shared",
    ],
    python_version = "3.12",
    visibility = ["//visibility:public"],
    deps = [requirement("pyyaml")],
)
