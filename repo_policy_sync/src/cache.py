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

"""Let commands with temporary homes reuse the configured Bazel caches."""

import os
from pathlib import Path
import shlex


def configure_bazel_cache(home: Path, environment: dict[str, str]) -> None:
    """Make the configured caches available inside a command's temporary home.

    Policy commands and pre-commit hooks get a fresh HOME for each invocation.
    Without explicit cache paths, they would download dependencies into that
    home and lose them when the command finishes.

    REPO_POLICY_SYNC_BAZELRC selects a file containing the shared cache paths.
    Import it from the temporary ~/.bazelrc so Bazel finds it even when called
    by a hook. This also works with Bazel 8, which does not read the BAZELRC
    environment variable. The runner's own ~/.bazelrc is not copied.
    """

    cache_rc = os.environ.get("REPO_POLICY_SYNC_BAZELRC")
    if cache_rc:
        # A relative path refers to the directory where policy sync started,
        # not the target repository where the command will run. Use `import`
        # so Bazel reports a missing file instead of silently running uncached.
        path = Path(cache_rc).resolve()
        (home / ".bazelrc").write_text(f"import {shlex.quote(str(path))}\n")

    bazelisk_home = os.environ.get("BAZELISK_HOME")
    if bazelisk_home:
        # Bazelisk caches the Bazel executable separately from dependencies.
        # Preserve its download directory when replacing HOME.
        environment["BAZELISK_HOME"] = str(Path(bazelisk_home).resolve())
