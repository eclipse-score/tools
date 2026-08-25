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
