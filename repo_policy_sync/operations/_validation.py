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

"""Shared YAML validation helpers for built-in operations."""

from pathlib import Path
from typing import Any

from ..errors import PolicyError


def expect_keys(value: dict[str, Any], allowed: set[str], source: Path) -> None:
    unexpected = set(value) - allowed
    if unexpected:
        raise PolicyError(
            f"policy {source}: unexpected fields: {', '.join(sorted(unexpected))}"
        )


def safe_relative_path(raw: str, source: Path) -> Path:
    path = Path(raw)
    if path.is_absolute() or ".." in path.parts or raw in {"", "."}:
        raise PolicyError(
            f"policy {source}: path must be a non-empty repository-relative path"
        )
    return path


def required_string(value: dict[str, Any], key: str, source: Path) -> str:
    result = value.get(key)
    if not isinstance(result, str) or not result.strip():
        raise PolicyError(f"policy {source}: {key} must be a non-empty string")
    return result


def optional_string(value: dict[str, Any], key: str, source: Path) -> str | None:
    result = value.get(key)
    if result is None:
        return None
    if not isinstance(result, str) or not result.strip():
        raise PolicyError(f"policy {source}: {key} must be a non-empty string")
    return result


def string_list(value: object, name: str, source: Path) -> tuple[str, ...]:
    if not isinstance(value, list) or not all(
        isinstance(item, str) and item for item in value
    ):
        raise PolicyError(
            f"policy {source}: {name} must be a list of non-empty strings"
        )
    return tuple(value)
