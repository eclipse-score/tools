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

"""The replace_regex operation."""

import re
from pathlib import Path
from typing import Any

from ..errors import PolicyError, RepoPolicySyncError
from ..models import Change, EnsureOperation, ReplaceRegex
from ._validation import (
    expect_keys,
    optional_string,
    required_string,
    safe_relative_path,
)


class ReplaceRegexOperation:
    operation_type = "replace_regex"
    operation_class = ReplaceRegex

    def parse(self, raw: dict[str, Any], source: Path) -> ReplaceRegex:
        expect_keys(
            raw, {"type", "path", "pattern", "replacement", "rationale"}, source
        )
        pattern = required_string(raw, "pattern", source)
        replacement = required_string(raw, "replacement", source)
        try:
            re.compile(pattern).sub(replacement, "")
        except re.error as exc:
            raise PolicyError(
                f"policy {source}: invalid replace_regex pattern or replacement: {exc}"
            ) from exc
        return ReplaceRegex(
            safe_relative_path(required_string(raw, "path", source), source),
            pattern,
            replacement,
            optional_string(raw, "rationale", source),
        )

    def describe_changes(
        self,
        root: Path,
        operation: EnsureOperation,
        *,
        organization: str | None = None,
    ) -> tuple[Change, ...]:
        assert isinstance(operation, ReplaceRegex)
        path = root / operation.path
        _validate_target(path, operation)
        if not path.is_file():
            return ()
        text = path.read_text(encoding="utf-8")
        return (
            (Change(operation.path, "replace matching text", operation.rationale),)
            if re.sub(operation.pattern, operation.replacement, text) != text
            else ()
        )

    def apply(
        self,
        root: Path,
        operation: EnsureOperation,
        *,
        organization: str | None = None,
    ) -> None:
        assert isinstance(operation, ReplaceRegex)
        path = root / operation.path
        _validate_target(path, operation)
        if not path.is_file():
            return
        text = path.read_text(encoding="utf-8")
        replaced = re.sub(operation.pattern, operation.replacement, text)
        if replaced != text:
            path.write_text(replaced, encoding="utf-8")


def _validate_target(path: Path, operation: ReplaceRegex) -> None:
    if path.exists() and not path.is_file():
        raise RepoPolicySyncError(f"{operation.path} must be a file")
