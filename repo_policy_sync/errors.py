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

"""Errors raised for invalid policy and runtime input."""

from __future__ import annotations

import os
import re


_GITHUB_TOKEN = re.compile(
    r"(?<![A-Za-z0-9_])(?:github_pat_[A-Za-z0-9_]{10,}|gh[pousr]_[A-Za-z0-9_]{10,})"
    r"(?![A-Za-z0-9_])"
)
_BEARER_CREDENTIAL = re.compile(
    r"(?i)(\b(?:authorization\s*:\s*)?(?:bearer|token)\s+)([^\s,;]+)"
)
_NAMED_CREDENTIAL = re.compile(
    r"(?i)(\b(?:api[-_]?key|password|passwd|secret|token)\s*[:=]\s*)([^\s,;]+)"
)
_OPTION_CREDENTIAL = re.compile(
    r"(?i)(--?(?:api[-_]?key|password|passwd|secret|token)\s+)([^\s,;]+)"
)
_URL_CREDENTIAL = re.compile(r"(?i)(https?://[^/\s:@]+):([^@\s]+)@")


def redact_sensitive_text(value: str) -> str:
    """Remove common credential forms before text reaches a user or PR."""

    for variable in ("GH_TOKEN", "GITHUB_TOKEN"):
        secret = os.environ.get(variable)
        if secret and len(secret) >= 8:
            value = value.replace(secret, "[REDACTED]")
    value = _GITHUB_TOKEN.sub("[REDACTED]", value)
    value = _BEARER_CREDENTIAL.sub(r"\1[REDACTED]", value)
    value = _NAMED_CREDENTIAL.sub(r"\1[REDACTED]", value)
    value = _OPTION_CREDENTIAL.sub(r"\1[REDACTED]", value)
    return _URL_CREDENTIAL.sub(r"\1[REDACTED]@", value)


class RepoPolicySyncError(RuntimeError):
    """Base error presented to Repository Policy Sync users without a traceback."""

    def __init__(self, message: object) -> None:
        super().__init__(redact_sensitive_text(str(message)))


class PolicyError(RepoPolicySyncError):
    """A policy file does not conform to the supported schema."""


class CommandError(RepoPolicySyncError):
    """An external gh or Git command failed."""
