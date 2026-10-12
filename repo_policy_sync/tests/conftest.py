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

from pathlib import Path

import pytest

from repo_policy_sync.src.policy import BUNDLED_POLICY_DIRECTORY


@pytest.fixture
def fake_repo(fs) -> Path:
    """Return an isolated fake checkout with access to bundled policies."""

    fs.add_real_directory(BUNDLED_POLICY_DIRECTORY)
    root = Path("/repo")
    root.mkdir()
    return root
