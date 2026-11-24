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
"""Macro for a runnable CodeQL C/C++ analysis of a traced Bazel build."""

load("@rules_python//python:defs.bzl", "py_binary")

_CODEQL = Label("@codeql_bundle//:cli")
_PACKS = {
    "misra-cpp": (Label("@codeql_misra_cpp_pack//:pack"), Label("@codeql_misra_cpp_pack//:pack/qlpack.yml")),
}
_PROCESS_CONFIG = Label("@codeql_coding_standards//:process_coding_standards_config")
_RECATEGORIZE = Label("@codeql_coding_standards//:guideline_recategorize")
_ANALYSIS_REPORT = Label("@codeql_coding_standards//:analysis_report")
_CODING_STANDARDS_SCHEMA = Label("@codeql_coding_standards//:schemas/coding-standards-schema-1.0.0.json")
_SARIF_SCHEMA = Label("@codeql_coding_standards//:schemas/sarif-schema-2.1.0.json")
_SCRIPT = Label("//codeql:codeql_analysis.py")
_DEFAULT_CODESCANNING_CONFIG = Label("//codeql:config.yaml")
_LINUX = Label("@platforms//os:linux")

def codeql_analysis(
        name,
        targets,
        build_configs = [],
        bazel_args = [],
        exclude_testonly = False,
        keep_going = False,
        pack = "misra-cpp",
        suite = "codeql-suites/misra-cpp-default.qls",
        coding_standards_config = None,
        codescanning_config = None,
        visibility = None,
        **kwargs):
    """Defines `bazel run`-able CodeQL analysis of `targets`.

    The run traces `bazel build <targets>` in the calling workspace (no caches,
    so every compile and link action is seen by CodeQL), analyses the database
    with a pre-compiled codeql-coding-standards pack and writes
    `<output-prefix>.sarif` plus the coding standards' compliance reports.

    Args:
        name: Name of the runnable target.
        targets: Bazel target patterns to build and analyse.
        build_configs: Bazel `--config` values of the traced build, e.g. a config
            selecting a toolchain whose compiler CodeQL can trace.
        bazel_args: Further flags for the traced build.
        exclude_testonly: Build only the C/C++ (`cc_*`) targets of `targets` that are not
            `testonly` (tests, mocks, test helpers), resolved with `bazel query`.
        keep_going: Build with `--keep_going` and analyse what was built even
            if some targets fail; the database integrity report shows the result.
        pack: Pre-compiled coding-standards pack; currently only "misra-cpp".
        suite: Query suite inside the pack.
        coding_standards_config: Workspace-relative path of the
            `coding-standards.yml` whose guideline recategorizations are applied
            to the SARIF. Deviations are picked up from every
            `coding-standards.yml` in the workspace regardless.
        codescanning_config: Label of a CodeQL code scanning config (query
            filters); defaults to excluding `exclude-from-incremental` queries.
        visibility: Visibility of the target.
        **kwargs: Passed to the underlying py_binary.

    Command line arguments of `bazel run` are appended, e.g. `--output-dir`,
    `--output-prefix`, `--database` or further `--target`/`--build-config`.
    """
    if pack not in _PACKS:
        fail("Unsupported pack '{}', supported: {}".format(pack, ", ".join(_PACKS)))
    pack_files, qlpack = _PACKS[pack]
    codescanning_config = codescanning_config or _DEFAULT_CODESCANNING_CONFIG

    # $(rootpath) is relative to the working directory of `bazel run` (the runfiles tree).
    args = [
        "--codeql=$(rootpath {})".format(_CODEQL),
        "--pack=$(rootpath {})".format(qlpack),
        "--process-config=$(rootpath {})".format(_PROCESS_CONFIG),
        "--recategorize=$(rootpath {})".format(_RECATEGORIZE),
        "--analysis-report=$(rootpath {})".format(_ANALYSIS_REPORT),
        "--coding-standards-schema=$(rootpath {})".format(_CODING_STANDARDS_SCHEMA),
        "--sarif-schema=$(rootpath {})".format(_SARIF_SCHEMA),
        "--codescanning-config=$(rootpath {})".format(codescanning_config),
        "--suite={}".format(suite),
    ]
    if coding_standards_config:
        args.append("--coding-standards-config={}".format(coding_standards_config))
    if exclude_testonly:
        args.append("--exclude-testonly")
    if keep_going:
        args.append("--keep-going")
    args += ["--target={}".format(target) for target in targets]
    args += ["--build-config={}".format(config) for config in build_configs]
    args += ["--bazel-arg={}".format(arg) for arg in bazel_args]

    py_binary(
        name = name,
        srcs = [_SCRIPT],
        main = _SCRIPT,
        python_version = "3.12",
        args = args,
        data = [
            _CODEQL,
            pack_files,
            qlpack,
            _PROCESS_CONFIG,
            _RECATEGORIZE,
            _ANALYSIS_REPORT,
            _CODING_STANDARDS_SCHEMA,
            _SARIF_SCHEMA,
            codescanning_config,
        ],
        tags = ["local", "manual"],
        target_compatible_with = [_LINUX],
        visibility = visibility,
        **kwargs
    )
