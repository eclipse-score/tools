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

"""Small helpers for parsing and comparing bzlmod versions."""

from __future__ import annotations

import re

from .models import BazelDependencyCondition

BazelVersion = tuple[int, int, int]

_VERSION = re.compile(r"(0|[1-9][0-9]*)\.(0|[1-9][0-9]*)\.(0|[1-9][0-9]*)\Z")
_CONDITION = re.compile(
    r"\A\s*([A-Za-z0-9_][A-Za-z0-9_.-]*)\s*(==|!=|<=|>=|<|>)\s*"
    r"((?:0|[1-9][0-9]*)\.(?:0|[1-9][0-9]*)\.(?:0|[1-9][0-9]*))\s*\Z"
)


def parse_bazel_version(value: str) -> BazelVersion | None:
    """Parse a strict major.minor.patch version."""

    match = _VERSION.fullmatch(value)
    return tuple(int(component) for component in match.groups()) if match else None


def starlark_call_ranges(text: str, function_name: str) -> tuple[tuple[int, int], ...]:
    """Return body ranges for calls outside comments and strings."""

    # Masking non-code text preserves the original offsets, so callers can
    # inspect the original source and still apply precise replacements.
    masked = _mask_starlark(text)
    pattern = re.compile(rf"(?<![A-Za-z0-9_]){re.escape(function_name)}\s*\(")
    ranges: list[tuple[int, int]] = []
    for match in pattern.finditer(masked):
        opening = masked.find("(", match.start(), match.end())
        depth = 0
        # Count nested parentheses instead of stopping at the first closing
        # one; Starlark call arguments can themselves contain function calls.
        for index in range(opening, len(masked)):
            if masked[index] == "(":
                depth += 1
            elif masked[index] == ")":
                depth -= 1
                if depth == 0:
                    ranges.append((opening + 1, index))
                    break
    return tuple(ranges)


def _mask_starlark(text: str) -> str:
    # Comments and strings can contain text that looks like a real call. Replace
    # them with spaces while retaining newlines and character positions for the
    # offset calculations in starlark_call_ranges.
    masked = list(text)
    index = 0
    while index < len(text):
        if text[index] == "#":
            while index < len(text) and text[index] not in "\r\n":
                masked[index] = " "
                index += 1
            continue
        if text[index] not in "'\"":
            index += 1
            continue
        quote = text[index]
        delimiter = quote * 3 if text.startswith(quote * 3, index) else quote
        for offset in range(len(delimiter)):
            masked[index + offset] = " "
        index += len(delimiter)
        escaped = False
        while index < len(text):
            character = text[index]
            if character not in "\r\n":
                masked[index] = " "
            if escaped:
                escaped = False
            elif character == "\\":
                escaped = True
            elif text.startswith(delimiter, index):
                for offset in range(len(delimiter)):
                    masked[index + offset] = " "
                index += len(delimiter)
                break
            index += 1
    return "".join(masked)


def parse_bazel_dependency_condition(value: str) -> BazelDependencyCondition | None:
    """Parse ``module OP major.minor.patch`` condition syntax."""

    match = _CONDITION.fullmatch(value)
    if match is None:
        return None
    version = parse_bazel_version(match.group(3))
    assert version is not None
    return BazelDependencyCondition(match.group(1), match.group(2), version)


def matches_bazel_dependency_condition(
    actual: BazelVersion, condition: BazelDependencyCondition
) -> bool:
    """Compare one parsed dependency version with a policy condition."""

    if condition.operator == "==":
        return actual == condition.version
    if condition.operator == "!=":
        return actual != condition.version
    if condition.operator == "<":
        return actual < condition.version
    if condition.operator == "<=":
        return actual <= condition.version
    if condition.operator == ">":
        return actual > condition.version
    if condition.operator == ">=":
        return actual >= condition.version
    raise ValueError(f"unsupported Bazel version operator: {condition.operator}")
