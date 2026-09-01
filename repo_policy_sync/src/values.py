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

"""Parsing and resolving policy-scoped derived values."""

from __future__ import annotations

import re
from pathlib import Path

from .errors import PolicyError, RepoPolicySyncError
from .models import DockerfileImageVersionSource, ValueBinding
from .operations._validation import (
    expect_keys,
    required_string,
    safe_relative_path,
    validate_repository_path,
)

_NUMERIC_VERSION = re.compile(r"(0|[1-9][0-9]*)\.(0|[1-9][0-9]*)\.(0|[1-9][0-9]*)\Z")
_VALUE_NAME = re.compile(r"[a-z][a-z0-9_]*\Z")


def parse_value_bindings(raw: object, source: Path) -> tuple[ValueBinding, ...]:
    """Parse the optional policy-local value source mapping."""

    if not isinstance(raw, dict):
        raise PolicyError(f"policy {source}: values must be a mapping")
    bindings: list[ValueBinding] = []
    for name, value in raw.items():
        if not isinstance(name, str) or _VALUE_NAME.fullmatch(name) is None:
            raise PolicyError(f"policy {source}: value names must use lower_snake_case")
        if not isinstance(value, dict):
            raise PolicyError(f"policy {source}: values.{name} must be a mapping")
        expect_keys(value, {"type", "dockerfile", "image"}, source)
        value_type = required_string(value, "type", source)
        if value_type != "dockerfile_image_version":
            raise PolicyError(f"policy {source}: unsupported value type {value_type!r}")
        # The source stays attached to the policy. A value reference can
        # therefore never accidentally read a similarly named value from
        # another policy.
        bindings.append(
            ValueBinding(
                name,
                DockerfileImageVersionSource(
                    safe_relative_path(
                        required_string(value, "dockerfile", source), source
                    ),
                    required_string(value, "image", source),
                ),
            )
        )
    return tuple(bindings)


def resolve_values(root: Path, bindings: tuple[ValueBinding, ...]) -> dict[str, str]:
    """Resolve all policy-local value bindings against a repository checkout."""

    return {
        binding.name: _resolve_dockerfile_image_version(root, binding.source)
        for binding in bindings
    }


def value_source_exists(root: Path, binding: ValueBinding) -> bool:
    """Return whether a value source can provide a valid value.

    This predicate is deliberately separate from resolution: a policy can use
    it as an applicability condition without turning a repository that lacks
    the source file into an evaluation error.
    """

    source = binding.source
    path = root / source.dockerfile
    validate_repository_path(root, path)
    if not path.is_file():
        return False
    text = path.read_text(encoding="utf-8")
    tags = _dockerfile_image_tags(text, source)
    if len(tags) != 1:
        return False
    return _is_version_tag(tags[0])


def _resolve_dockerfile_image_version(
    root: Path, source: DockerfileImageVersionSource
) -> str:
    path = root / source.dockerfile
    validate_repository_path(root, path)
    if not path.is_file():
        raise RepoPolicySyncError(f"{source.dockerfile} must exist")
    text = path.read_text(encoding="utf-8")
    tags = _dockerfile_image_tags(text, source)
    if len(tags) != 1:
        raise RepoPolicySyncError(
            f"{source.dockerfile} must contain exactly one FROM "
            f"{source.image}:... instruction"
        )
    tag = tags[0]
    if not _is_version_tag(tag):
        raise RepoPolicySyncError(
            f"{source.dockerfile} must use {source.image}:vX.Y.Z, found {tag!r}"
        )
    return tag[1:]


def _dockerfile_image_tags(
    text: str, source: DockerfileImageVersionSource
) -> list[str]:
    """Extract tags for the configured image without interpreting them yet."""

    return [
        match.group("tag")
        for match in re.finditer(
            rf"(?m)^\s*FROM\s+{re.escape(source.image)}:(?P<tag>[^\s#]+)[^\r\n]*\r?$",
            text,
        )
    ]


def _is_version_tag(tag: str) -> bool:
    return tag.startswith("v") and _parse_version(tag[1:]) is not None


def _parse_version(value: str) -> tuple[int, int, int] | None:
    match = _NUMERIC_VERSION.fullmatch(value)
    return tuple(int(component) for component in match.groups()) if match else None
