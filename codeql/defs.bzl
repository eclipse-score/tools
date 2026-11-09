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
"""Public Bazel API of the CodeQL static analysis tooling.

Usage in a consumer repository (`tools/codeql/BUILD`):

    load("@score_tools//codeql:defs.bzl", "codeql_sca")

    codeql_sca(
        name = "sca_linux_qm",
        targets = ["//score/...", "-//score/mock/..."],
        configs = ["x86_64-linux"],
        safety_level = "QM",
    )

and run it with `bazel run //tools/codeql:sca_linux_qm`.
"""

load("@rules_python//python:defs.bzl", "py_binary")

# (environment variable, label) pairs. The value of every variable is the
# runfiles-relative path (`$(rlocationpath ...)`) of the label; the Python tool
# resolves it with `python.runfiles`.
_RUNFILE_ENV = {
    "SCORE_CODEQL_BCC": Label("@bazel_compile_commands//:bin"),
    "SCORE_CODEQL_BCC_BAZEL_WRAPPER": Label("//codeql:scripts/bcc_bazel_wrapper.sh"),
    "SCORE_CODEQL_BCC_DEB": Label("@bazel_compile_commands_jammy_deb//file"),
    "SCORE_CODEQL_BCC_SH": Label("//codeql:scripts/bcc.sh"),
    "SCORE_CODEQL_CLI": Label("@codeql_bundle//:codeql_cli"),
    "SCORE_CODEQL_COMPLEXITY_QLPACK": Label("//codeql:queries/code-complexity/qlpack.yml"),
    "SCORE_CODEQL_DIAGNOSTICS_QLPACK": Label("//codeql:queries/diagnostics/qlpack.yml"),
    "SCORE_CODEQL_PACKS": Label("//codeql:config/packs.toml"),
    "SCORE_CODEQL_REPORTS": Label("//codeql:config/reports.toml"),
    "SCORE_CODEQL_WRAPPER_SRC": Label("//codeql:wrapper/codeql_cc_wrapper.c"),
}

# Whole local packs (the `qlpack.yml` labels above only cover one file each).
_PACK_DATA = [
    Label("//codeql:code_complexity_pack"),
    Label("//codeql:diagnostics_pack"),
]

_THRESHOLD_FLAGS = {
    "max_commands_with_issues_pct": "--max-commands-with-issues-pct",
    "max_failed_command_pct": "--max-failed-command-pct",
    "max_unique_parse_errors": "--max-unique-parse-errors",
}

def _sh_quote(value):
    """Quotes a value for Bazel's Bourne-shell tokenization of `args`."""
    return "'" + value.replace("$", "$$").replace("'", "'\\''") + "'"

def _flag(flag, value):
    return "--{}={}".format(flag, _sh_quote(str(value)))

def _location_flag(flag, label):
    # Not quoted: the `$(rlocationpath)` expansion must stay visible to Bazel.
    return "--{}=$(rlocationpath {})".format(flag, label)

def _as_list(value):
    if value == None:
        return []
    if type(value) == "string":
        return [value]
    return list(value)

def codeql_tool_binary(
        name,
        main,
        args = [],
        extra_data = [],
        visibility = None):
    """Declares a runnable `score_codeql` tool with the pinned runfiles wired up.

    This is the building block of `//codeql:sca`, `//codeql:list_rules` and
    `codeql_sca`; consumers normally call `codeql_sca` instead.

    Args:
        name: Name of the binary.
        main: Label of the Python entry point inside `score_codeql/`.
        args: Arguments passed to the tool before any command line arguments.
        extra_data: Additional runfiles (labels).
        visibility: Visibility of the binary.
    """
    env = {
        var: "$(rlocationpath {})".format(label)
        for var, label in _RUNFILE_ENV.items()
    }
    py_binary(
        name = name,
        srcs = [main],
        main = main,
        args = args,
        data = _RUNFILE_ENV.values() + _PACK_DATA + extra_data,
        env = env,
        tags = ["local"],
        target_compatible_with = ["@platforms//os:linux"],
        visibility = visibility,
        deps = [Label("//codeql:score_codeql")],
    )

def codeql_sca_binary(
        name,
        args = [],
        extra_data = [],
        visibility = None):
    """Declares a runnable `sca` binary with the pinned tool runfiles wired up.

    This is the building block of `//codeql:sca` and `codeql_sca`; consumers
    normally call `codeql_sca` instead.

    Args:
        name: Name of the binary.
        args: Arguments passed to the tool before any command line arguments.
        extra_data: Additional runfiles (labels).
        visibility: Visibility of the binary.
    """
    codeql_tool_binary(
        name = name,
        main = Label("//codeql:score_codeql/__main__.py"),
        args = args,
        extra_data = extra_data,
        visibility = visibility,
    )

def codeql_sca(
        name,
        targets,
        configs = [],
        bazel_options = [],
        platform = "linux",
        safety_level = None,
        reports = [],
        reports_file = None,
        extra_packs = [],
        extra_pack_data = [],
        exclude_globs = [],
        auto_exclude_dirs = None,
        extraction_thresholds = {},
        expected_compilers = None,
        visibility = None):
    """Defines a runnable CodeQL static analysis (SCA) target for a repository.

    `bazel run <name>` extracts the C/C++ code of `targets` with the production
    compiler, runs the selected CodeQL query suites and writes SARIF, CSV and
    summary files below `_codeql_sca/results` of the workspace.

    The arguments are baked into the binary as defaults. Anything passed after
    `--` on the command line takes precedence: scalar options (`--platform`,
    `--safety-level`, thresholds, ...) use the last value, repeatable options
    (`--config`, `--bazel-option`, `--report`, `--exclude-glob`, ...) are
    appended to the baked ones, and positional Bazel target patterns replace the
    `targets` of the macro entirely. Example:

        bazel run //tools/codeql:sca_linux_qm -- //score/time/...
        bazel run //tools/codeql:sca_linux_qm -- --safety-level=ASIL

    Args:
        name: Name of the runnable target.
        targets: Required list of Bazel target patterns. Negative patterns
            (`-//pkg/...`) exclude targets and their sources from the analysis.
        configs: Bazel `--config` values used for the build and for the
            compile-commands generation, e.g. `["x86_64-linux"]`.
        bazel_options: Extra Bazel build flags, e.g. `["--noenable_bzlmod"]`.
        platform: Extraction platform, `"linux"` or `"qnx"`.
        safety_level: Report preset from `config/reports.toml`: `"QM"`,
            `"ASIL"` or `None` (use `reports` or the default report).
        reports: Explicit report names (merged with the preset).
        reports_file: Optional label of a consumer `reports.toml` (or a list of
            labels) that adds to or overrides the default reports and presets.
            It is passed as an `$(rlocationpath)`; the tool resolves it through
            the runfiles when the value is not an existing file path.
        extra_packs: Labels of the `qlpack.yml` file of consumer-local CodeQL
            packs. Each label is passed as an `$(rlocationpath)`; the tool uses
            the parent directory of the resolved file as an additional pack
            directory.
        extra_pack_data: Labels (typically filegroups) with all files of the
            packs listed in `extra_packs`, so they are available as runfiles.
        exclude_globs: Extra glob patterns removed from the compile commands.
        auto_exclude_dirs: Directory names whose targets are excluded
            automatically. `None` keeps the tool default
            (`test`, `tests`, `utests`, `mock`, `mocks`); `[]` disables it.
        extraction_thresholds: Dict overriding the extraction quality gates.
            Supported keys: `max_failed_command_pct`,
            `max_commands_with_issues_pct`, `max_unique_parse_errors`.
        expected_compilers: Regular expressions matched against the basename of
            every extracted compiler. `None` (or empty) keeps the platform
            default (GCC on Linux, `qcc`/`q++` on QNX); `[""]` disables the check.
        visibility: Visibility of the target.
    """
    if not targets:
        fail("codeql_sca({}): `targets` must not be empty".format(name))
    if platform not in ("linux", "qnx"):
        fail("codeql_sca({}): platform must be 'linux' or 'qnx', got '{}'".format(name, platform))
    if safety_level not in (None, "QM", "ASIL"):
        fail("codeql_sca({}): safety_level must be 'QM', 'ASIL' or None, got '{}'".format(name, safety_level))
    for key in extraction_thresholds:
        if key not in _THRESHOLD_FLAGS:
            fail("codeql_sca({}): unknown extraction threshold '{}', supported: {}".format(
                name,
                key,
                ", ".join(sorted(_THRESHOLD_FLAGS)),
            ))

    reports_files = _as_list(reports_file)
    extra_packs = _as_list(extra_packs)

    args = [_flag("platform", platform)]
    if safety_level:
        args.append(_flag("safety-level", safety_level))
    args += [_flag("config", c) for c in configs]
    args += [_flag("bazel-option", o) for o in bazel_options]
    args += [_flag("report", r) for r in reports]
    args += [_location_flag("reports-file", label) for label in reports_files]
    args += [_location_flag("additional-pack-qlpack", label) for label in extra_packs]
    args += [_flag("exclude-glob", g) for g in exclude_globs]
    if auto_exclude_dirs != None:
        # An explicit empty list disables the automatic exclusion.
        if auto_exclude_dirs:
            args += [_flag("auto-exclude-dir", d) for d in auto_exclude_dirs]
        else:
            args.append("--no-auto-exclude")
    args += [_flag("expect-compiler", c) for c in _as_list(expected_compilers)]
    for key in sorted(extraction_thresholds):
        args.append(_flag(_THRESHOLD_FLAGS[key][2:], extraction_thresholds[key]))
    args += [_flag("default-target", t) for t in targets]

    codeql_sca_binary(
        name = name,
        args = args,
        extra_data = reports_files + extra_packs + _as_list(extra_pack_data),
        visibility = visibility,
    )
