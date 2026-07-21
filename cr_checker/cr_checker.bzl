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

"""Defines Bazel rules for running copyright checks and fixes."""

load("@aspect_rules_py//py:defs.bzl", "py_binary")

def copyright_checker(
        name,
        visibility,
        template = None,
        exclusion = None,
        extensions = [],
        debug = False,
        fix = False,
        target_compatible_with = None):
    """
    Defines a custom build rule for checking and optionally fixing files for compliance
    with specific requirements, such as copyright headers.

    Args:
        name (str): The name of the rule, used as an identifier in the build system.
        visibility (list): A list defining the visibility of the rule, specifying which
                           targets can use this rule.
        template (str, optional): Path to the template resource used for validation.
                                  Defaults to "//tools/cr_checker/resources:templates".
        exclusion (str, optional): Path to a text file listing files to be excluded from the copyright check.
                                   File format: one path per line, relative to the repository root.
        extensions (list, optional): A list of file extensions to filter the source files.
                                     Defaults to an empty list, meaning all files are checked.
        debug (bool, optional): Whether to enable debug mode, providing additional logs.
                                Defaults to False.
        fix (bool, optional): Whether to apply fixes to files instead of just reporting issues.
                                         Defaults to False.

    Returns:
        None: This function defines a rule for a build system and does not return a value.
    """
    t_names = [
        "{}.check".format(name),
        "{}.fix".format(name),
    ]
    args = []
    if template:
        args.append(
            "-t $(location {})".format(template),
        )
    if len(extensions):
        args.append("-e {exts}".format(
            exts = " ".join([exts for exts in extensions]),
        ))

    if exclusion:
        args.append("--exclusion-file $(location {})".format(exclusion))

    if debug:
        args.append("-v")

    data = []
    if template:
        data.append(template)
    if exclusion:
        data.append(exclusion)
    for t_name in t_names:
        if t_name == "{}.fix".format(name):
            args.insert(0, "--fix")


        py_binary(
            name = t_name,
            main = "cr_checker.py",
            srcs = [
                "@score_tools//cr_checker/tool:cr_checker_lib",
            ],
            args = args,
            data = data,
            visibility = visibility,
            target_compatible_with = target_compatible_with,
        )

    native.alias(
        name = "copyright-check",
        actual = ":" + name + ".check",
        visibility = visibility,
        target_compatible_with = target_compatible_with,
        tags = [
            "cli_help=Check for license headers:\n" +
            "bazel run //:copyright-check",
        ],
    )

    native.alias(
        name = "copyright-fix",
        actual = ":" + name + ".fix",
        visibility = visibility,
        tags = [
            "cli_help=Fix license headers:\n" +
            "bazel run //:copyright-fix",
        ],
    )
