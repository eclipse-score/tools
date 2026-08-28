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

"""Domain model for a repository listed in a GitHub organization."""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class Repository:
    """A repository as reported by the GitHub API."""

    name: str
    default_branch: str | None
    archived: bool = False
