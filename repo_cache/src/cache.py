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

"""Locations for persistent, disposable repository checkouts."""

from __future__ import annotations

import os
from pathlib import Path


def default_cache_directory() -> Path:
    """Return the standard user cache location without creating it."""

    xdg_cache_home = os.environ.get("XDG_CACHE_HOME")
    cache_home = Path(xdg_cache_home) if xdg_cache_home else Path.home() / ".cache"
    return cache_home / "repo-cache"
