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

"""Subprocess boundary for pre-authenticated `gh` and `git` commands."""

from __future__ import annotations

import subprocess
from pathlib import Path

from .errors import CommandError


def run_command(command: list[str], *, cwd: Path | None = None) -> str:
    """Run a `gh`/`git` command, raising CommandError with a redacted message on failure."""

    try:
        result = subprocess.run(
            command,
            check=True,
            capture_output=True,
            text=True,
            cwd=cwd,
        )
    except FileNotFoundError as exc:
        raise CommandError(f"required command is unavailable: {command[0]}") from exc
    except subprocess.CalledProcessError as exc:
        detail = exc.stderr.strip() or exc.stdout.strip() or "command failed"
        raise CommandError(f"{' '.join(command[:3])}: {detail}") from exc
    return result.stdout
