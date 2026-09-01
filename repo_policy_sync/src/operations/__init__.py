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

"""Built-in policy operations and their explicit dispatch registry."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import fields, replace
from pathlib import Path
from typing import Any, Protocol

from ..errors import RepoPolicySyncError
from ..models import Change, EnsureOperation, ValueReference
from .ensure_bazel_dependency import EnsureBazelDependencyOperation
from .ensure_line import EnsureLineOperation
from .ensure_minimum_version import EnsureMinimumVersionOperation
from .remove_file import RemoveFileOperation
from .replace_regex import ReplaceRegexOperation


class OperationHandler(Protocol):
    operation_type: str
    operation_class: type[Any]

    def parse(self, raw: dict[str, Any], source: Path) -> EnsureOperation: ...

    def describe_changes(
        self,
        root: Path,
        operation: EnsureOperation,
        *,
        organization: str | None = None,
    ) -> tuple[Change, ...]: ...

    def apply(
        self,
        root: Path,
        operation: EnsureOperation,
        *,
        organization: str | None = None,
    ) -> None: ...


_HANDLERS: tuple[OperationHandler, ...] = (
    EnsureBazelDependencyOperation(),
    EnsureLineOperation(),
    EnsureMinimumVersionOperation(),
    RemoveFileOperation(),
    ReplaceRegexOperation(),
)
# Operations that work on a repository root are handled explicitly below;
# path-based operations can be dispatched directly to one target file.
_BY_TYPE = {handler.operation_type: handler for handler in _HANDLERS}


def parse_operation(raw: object, source: Path) -> EnsureOperation:
    """Parse one operation using the built-in registry."""

    if not isinstance(raw, dict):
        from ..errors import PolicyError

        raise PolicyError(f"policy {source}: each ensure item must be a mapping")
    operation_type = raw.get("type")
    if not isinstance(operation_type, str) or operation_type not in _BY_TYPE:
        from ..errors import PolicyError

        raise PolicyError(
            f"policy {source}: unsupported ensure type {operation_type!r}"
        )
    return _BY_TYPE[operation_type].parse(raw, source)


def describe_changes(
    root: Path, operation: EnsureOperation, *, organization: str | None = None
) -> tuple[Change, ...]:
    """Describe every path an operation would change."""

    return _handler_for(operation).describe_changes(
        root, operation, organization=organization
    )


def apply(
    root: Path, operation: EnsureOperation, *, organization: str | None = None
) -> None:
    """Apply one operation from a repository root."""

    _handler_for(operation).apply(root, operation, organization=organization)


def resolve_operation(
    operation: EnsureOperation, values: Mapping[str, str]
) -> EnsureOperation:
    """Materialize policy-local references in an operation.

    References are intentionally plain data rather than workflow steps. This
    keeps operation handlers independent of value-source details and lets the
    engine use identical inputs while describing and applying changes.
    """

    replacements: dict[str, str] = {}
    for field in fields(operation):
        value = getattr(operation, field.name)
        if not isinstance(value, ValueReference):
            continue
        try:
            replacements[field.name] = values[value.name]
        except KeyError as exc:
            raise RepoPolicySyncError(
                f"unknown policy value reference: {value.name!r}"
            ) from exc
    return replace(operation, **replacements) if replacements else operation


def _handler_for(operation: EnsureOperation) -> OperationHandler:
    for handler in _HANDLERS:
        if handler.operation_class is type(operation):
            return handler
    raise TypeError(f"no operation handler registered for {type(operation).__name__}")
